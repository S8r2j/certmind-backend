import json
import random
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, fetchall, execute
from app.services.ai import generate_question as ai_generate_question, EXAM_METADATA, _normalize_options
from app.services.redis_client import (
    cache_question_pool, get_cached_pool,
    set_prefetch, pop_prefetch,
)
from app.schemas.models import QuestionRequest, AnswerRequest
from app.core.config import settings

router = APIRouter(prefix="/practice", tags=["practice"])

SET_SIZE = 50   # max questions per shared set before starting a new one


# ── Domain selection ──────────────────────────────────────────────────────────

def _select_domain(exam_slug: str, domain_scores: dict) -> str:
    meta = EXAM_METADATA.get(exam_slug)
    if not meta:
        raise HTTPException(status_code=404, detail="Exam not found")
    domains = meta["domains"]
    weights = []
    for d in domains:
        score = domain_scores.get(d["name"], {})
        answered = score.get("total", 0)
        correct = score.get("correct", 0)
        accuracy = correct / answered if answered > 0 else 0.5
        weights.append(d["weight"] * (1.0 + (1.0 - accuracy)))
    total = sum(weights)
    weights = [w / total for w in weights]
    return random.choices([d["name"] for d in domains], weights=weights, k=1)[0]


# ── Question set selection ────────────────────────────────────────────────────

def _determine_current_set(exam_slug: str, questions_seen: list) -> int:
    """
    Return the set_number to draw from.

    Rules:
    - Find all distinct set numbers that exist for this exam
    - Prefer the lowest-numbered set that still has unseen questions for this user
    - If a set has < SET_SIZE questions it is still being built → include it
    - If all questions in all sets are seen, return max_set + 1 (new set)
    """
    rows = fetchall(
        "SELECT set_number, COUNT(*) AS cnt FROM questions "
        "WHERE exam_slug = %s AND is_active = TRUE "
        "GROUP BY set_number ORDER BY set_number",
        (exam_slug,),
    )
    if not rows:
        return 1  # no questions yet, AI will generate into set 1

    seen_set = set(questions_seen)
    for row in rows:
        sn = row["set_number"]
        cnt = row["cnt"]
        # If set is still being built (<SET_SIZE), always draw from it
        if cnt < SET_SIZE:
            return sn
        # Check if user has any unseen questions in this set
        set_ids = fetchall(
            "SELECT id FROM questions WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE",
            (exam_slug, sn),
        )
        unseen = [r["id"] for r in set_ids if r["id"] not in seen_set]
        if unseen:
            return sn

    # All sets exhausted — start a new one
    return rows[-1]["set_number"] + 1


# ── Question pool fetch (DB + Redis) ──────────────────────────────────────────

def _fetch_question_pool(exam_slug: str, domain: str, questions_seen: list, set_number: int) -> list[dict]:
    """
    Return up to 20 candidate questions.
    Tries Redis pool cache first; falls back to DB and populates cache.
    The cache is shared across users for the same exam+domain, so filtering
    for `questions_seen` (per-user) is done after retrieval.
    """
    pool = get_cached_pool(exam_slug, domain)
    if pool is None:
        # DB query — fetches up to 20 active questions for this domain+set
        if questions_seen:
            pool = fetchall(
                "SELECT id, exam_slug, domain, topic, stem, options, difficulty FROM questions "
                "WHERE exam_slug = %s AND domain = %s AND set_number = %s "
                "AND is_active = TRUE AND id != ALL(%s) LIMIT 20",
                (exam_slug, domain, set_number, questions_seen),
            )
        else:
            pool = fetchall(
                "SELECT id, exam_slug, domain, topic, stem, options, difficulty FROM questions "
                "WHERE exam_slug = %s AND domain = %s AND set_number = %s "
                "AND is_active = TRUE LIMIT 20",
                (exam_slug, domain, set_number),
            )
        if pool:
            cache_question_pool(exam_slug, domain, pool)
    else:
        # Filter out already-seen questions from the cached pool
        seen_set = set(questions_seen)
        pool = [q for q in pool if q["id"] not in seen_set]

    return pool or []


# ── Background prefetch ───────────────────────────────────────────────────────

def _prefetch_question_for_user(user_id: str, exam_slug: str) -> None:
    """
    Called as a background task after submit_answer.
    Picks the likely-next question and stores it in Redis so get_question
    can serve it instantly without hitting the DB.
    Does NOT fall back to AI generation — prefetch is best-effort.
    """
    try:
        progress = fetchone(
            "SELECT domain_scores, questions_seen FROM user_progress "
            "WHERE user_id = %s AND exam_slug = %s",
            (user_id, exam_slug),
        )
        domain_scores = progress["domain_scores"] if progress else {}
        questions_seen = progress["questions_seen"] if progress else []

        domain = _select_domain(exam_slug, domain_scores)
        set_number = _determine_current_set(exam_slug, questions_seen)
        pool = _fetch_question_pool(exam_slug, domain, questions_seen, set_number)

        if pool:
            q = random.choice(pool)
            q["options"] = _normalize_options(q.get("options") or [])
            set_prefetch(user_id, exam_slug, q)
    except Exception:
        pass  # prefetch failure is silent — get_question will handle it normally


# ── Routes ────────────────────────────────────────────────────────────────────

def _ensure_trial_or_raise(user_id: str, exam_slug: str) -> None:
    """
    Auto-create a 7-day trial subscription for the user's first exam.
    Raises 403 if the trial has already been used for a different exam
    and no paid subscription exists.
    """
    user = fetchone("SELECT trial_used FROM users WHERE id = %s", (user_id,))
    if user and not user["trial_used"]:
        trial_expires = datetime.now(timezone.utc) + timedelta(days=7)
        execute(
            "INSERT INTO user_subscriptions (id, user_id, exam_slug, status, expires_at) "
            "VALUES (%s, %s, %s, 'trial', %s)",
            (str(uuid.uuid4()), user_id, exam_slug, trial_expires),
        )
        execute("UPDATE users SET trial_used = TRUE WHERE id = %s", (user_id,))
    else:
        raise HTTPException(
            status_code=403,
            detail="No active subscription for this exam. Your free trial has been used.",
        )


@router.post("/question")
async def get_question(
    body: QuestionRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    # Gate: require active paid or trial subscription (unless bypass is on)
    if not settings.bypass_subscription:
        sub = fetchone(
            "SELECT id, expires_at FROM user_subscriptions "
            "WHERE user_id = %s AND exam_slug = %s AND status IN ('active', 'trial') "
            "ORDER BY expires_at DESC LIMIT 1",
            (user_id, body.exam_slug),
        )
        if sub:
            expires_at = sub["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                # Expired — mark and fall through to trial check
                execute(
                    "UPDATE user_subscriptions SET status = 'expired' "
                    "WHERE user_id = %s AND exam_slug = %s AND status IN ('active', 'trial')",
                    (user_id, body.exam_slug),
                )
                _ensure_trial_or_raise(user_id, body.exam_slug)
        else:
            _ensure_trial_or_raise(user_id, body.exam_slug)

    # 1. Check Redis prefetch first (zero DB queries on cache hit)
    prefetched = pop_prefetch(user_id, body.exam_slug)
    if prefetched:
        return prefetched

    # 2. Cache miss — load progress and select from DB (with Redis pool caching)
    progress = fetchone(
        "SELECT domain_scores, questions_seen FROM user_progress WHERE user_id = %s AND exam_slug = %s",
        (user_id, body.exam_slug),
    )
    domain_scores = progress["domain_scores"] if progress else {}
    questions_seen = progress["questions_seen"] if progress else []

    domain = _select_domain(body.exam_slug, domain_scores)
    set_number = _determine_current_set(body.exam_slug, questions_seen)
    pool = _fetch_question_pool(body.exam_slug, domain, questions_seen, set_number)

    if pool:
        q = random.choice(pool)
        q["options"] = _normalize_options(q.get("options") or [])
        return q

    # 3. Fallback: generate via configured AI provider, insert into current set
    generated = ai_generate_question(body.exam_slug, domain)
    q = execute(
        "INSERT INTO questions (id, exam_slug, domain, stem, options, correct_answer, explanation, difficulty, set_number) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, exam_slug, domain, stem, options, difficulty",
        (
            str(uuid.uuid4()), body.exam_slug, domain,
            generated["stem"], json.dumps(generated["options"]),
            generated["correct_answer"], generated["explanation"], "medium",
            set_number,
        ),
    )
    if q:
        q["options"] = _normalize_options(q.get("options") or [])
    return q


@router.post("/answer")
async def submit_answer(
    body: AnswerRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    q = fetchone(
        "SELECT correct_answer, explanation, domain FROM questions WHERE id = %s",
        (body.question_id,),
    )
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    correct = body.answer == q["correct_answer"]
    domain = q["domain"]

    progress = fetchone(
        "SELECT id, domain_scores, questions_seen, total_answered, total_correct "
        "FROM user_progress WHERE user_id = %s AND exam_slug = %s",
        (user_id, body.exam_slug),
    )

    if progress:
        domain_scores = progress["domain_scores"] or {}
        questions_seen = progress["questions_seen"] or []
        if domain not in domain_scores:
            domain_scores[domain] = {"correct": 0, "total": 0}
        domain_scores[domain]["total"] += 1
        if correct:
            domain_scores[domain]["correct"] += 1
        if body.question_id not in questions_seen:
            questions_seen.append(body.question_id)
        execute(
            "UPDATE user_progress SET domain_scores = %s, questions_seen = %s, "
            "total_answered = total_answered + 1, total_correct = total_correct + %s "
            "WHERE id = %s",
            (json.dumps(domain_scores), questions_seen, 1 if correct else 0, progress["id"]),
        )
    else:
        domain_scores = {domain: {"correct": 1 if correct else 0, "total": 1}}
        execute(
            "INSERT INTO user_progress (id, user_id, exam_slug, domain_scores, questions_seen, total_answered, total_correct) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()), user_id, body.exam_slug,
                json.dumps(domain_scores), [body.question_id],
                1, 1 if correct else 0,
            ),
        )

    # Fire prefetch in background so the next question is ready before user clicks Next
    background_tasks.add_task(_prefetch_question_for_user, user_id, body.exam_slug)

    return {
        "correct": correct,
        "correct_answer": q["correct_answer"],
        "explanation": q["explanation"],
        "domain_scores": domain_scores,
    }
