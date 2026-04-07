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

SET_SIZE = 50   # questions per shared set / per session


# ── Subscription gate ─────────────────────────────────────────────────────────

def _ensure_trial_or_raise(user_id: str, exam_slug: str) -> None:
    """
    Auto-create a 7-day trial subscription for the user's first (and only) exam.
    Raises 403 if trial already used for a different exam or no paid sub exists.
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
        # Check if they have an active sub for a DIFFERENT exam to give a clearer message
        other = fetchone(
            "SELECT exam_slug FROM user_subscriptions "
            "WHERE user_id = %s AND exam_slug != %s AND status IN ('active', 'trial') "
            "AND expires_at > NOW() LIMIT 1",
            (user_id, exam_slug),
        )
        if other:
            raise HTTPException(
                status_code=403,
                detail=f"ENROLLED_ELSEWHERE|{other['exam_slug']}",
            )
        raise HTTPException(
            status_code=403,
            detail="No active subscription for this exam. Your free trial has been used.",
        )


def _check_subscription(user_id: str, exam_slug: str) -> None:
    """Verify active sub exists; auto-create trial for first-timers."""
    if settings.bypass_subscription:
        return
    sub = fetchone(
        "SELECT id, expires_at FROM user_subscriptions "
        "WHERE user_id = %s AND exam_slug = %s AND status IN ('active', 'trial') "
        "ORDER BY expires_at DESC LIMIT 1",
        (user_id, exam_slug),
    )
    if sub:
        expires_at = sub["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            execute(
                "UPDATE user_subscriptions SET status = 'expired' "
                "WHERE user_id = %s AND exam_slug = %s AND status IN ('active', 'trial')",
                (user_id, exam_slug),
            )
            _ensure_trial_or_raise(user_id, exam_slug)
    else:
        _ensure_trial_or_raise(user_id, exam_slug)


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


# ── Practice session management ───────────────────────────────────────────────

def _find_set_for_new_session(exam_slug: str, questions_seen: list) -> int:
    """
    Determine which set_number a new practice session should be assigned to.

    Priority:
    1. The lowest set that is still being built (< SET_SIZE questions) — user
       draws existing questions and their answers generate new ones into this set.
    2. The lowest full set where the user still has unseen questions.
    3. A brand-new set (max + 1) if the user has exhausted everything.
    """
    rows = fetchall(
        "SELECT set_number, COUNT(*) AS cnt FROM questions "
        "WHERE exam_slug = %s AND is_active = TRUE "
        "GROUP BY set_number ORDER BY set_number",
        (exam_slug,),
    )
    if not rows:
        return 1  # first ever question will be generated into set 1

    seen_set = set(questions_seen)
    for row in rows:
        sn, cnt = row["set_number"], row["cnt"]
        if cnt < SET_SIZE:
            return sn  # still building — join this set
        # Full set: check if user has unseen questions here
        ids = fetchall(
            "SELECT id FROM questions WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE",
            (exam_slug, sn),
        )
        if any(r["id"] not in seen_set for r in ids):
            return sn

    return rows[-1]["set_number"] + 1  # start a new set


def _get_or_create_practice_session(user_id: str, exam_slug: str, questions_seen: list) -> dict:
    """
    Return the user's current incomplete practice session, creating one if needed.
    One session = one set of up to SET_SIZE questions.
    """
    session = fetchone(
        "SELECT id, set_number, questions_answered, is_complete FROM practice_sessions "
        "WHERE user_id = %s AND exam_slug = %s AND is_complete = FALSE "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, exam_slug),
    )
    if session:
        return dict(session)

    set_number = _find_set_for_new_session(exam_slug, questions_seen)
    session_id = str(uuid.uuid4())
    execute(
        "INSERT INTO practice_sessions (id, user_id, exam_slug, set_number) "
        "VALUES (%s, %s, %s, %s)",
        (session_id, user_id, exam_slug, set_number),
    )
    return {"id": session_id, "set_number": set_number, "questions_answered": 0, "is_complete": False}


def _mark_session_complete(session_id: str) -> None:
    execute(
        "UPDATE practice_sessions SET is_complete = TRUE, last_active_at = NOW() WHERE id = %s",
        (session_id,),
    )


def _increment_session(session_id: str) -> int:
    """Increment questions_answered; return new count."""
    row = execute(
        "UPDATE practice_sessions "
        "SET questions_answered = questions_answered + 1, last_active_at = NOW() "
        "WHERE id = %s "
        "RETURNING questions_answered",
        (session_id,),
    )
    return row["questions_answered"] if row else 0


# ── Question pool fetch (DB + Redis) ──────────────────────────────────────────

def _fetch_question_pool(exam_slug: str, domain: str, questions_seen: list, set_number: int) -> list[dict]:
    """
    Return unseen candidate questions for the given domain + set.
    Shared Redis cache per exam+domain+set; per-user filtering applied after retrieval.
    """
    pool = get_cached_pool(exam_slug, f"{domain}:set{set_number}")
    if pool is None:
        pool = fetchall(
            "SELECT id, exam_slug, domain, topic, stem, options, difficulty FROM questions "
            "WHERE exam_slug = %s AND domain = %s AND set_number = %s AND is_active = TRUE LIMIT 20",
            (exam_slug, domain, set_number),
        )
        if pool:
            cache_question_pool(exam_slug, f"{domain}:set{set_number}", pool)

    seen_set = set(questions_seen)
    return [q for q in (pool or []) if q["id"] not in seen_set]


# ── Background prefetch ───────────────────────────────────────────────────────

def _prefetch_question_for_user(user_id: str, exam_slug: str) -> None:
    """
    Best-effort: pick the likely-next question and cache it in Redis.
    Uses the user's current practice session to respect set boundaries.
    """
    try:
        progress = fetchone(
            "SELECT domain_scores, questions_seen FROM user_progress "
            "WHERE user_id = %s AND exam_slug = %s",
            (user_id, exam_slug),
        )
        domain_scores = progress["domain_scores"] if progress else {}
        questions_seen = progress["questions_seen"] if progress else []

        session = fetchone(
            "SELECT set_number FROM practice_sessions "
            "WHERE user_id = %s AND exam_slug = %s AND is_complete = FALSE "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, exam_slug),
        )
        if not session:
            return

        set_number = session["set_number"]
        domain = _select_domain(exam_slug, domain_scores)
        pool = _fetch_question_pool(exam_slug, domain, questions_seen, set_number)

        if pool:
            q = random.choice(pool)
            q["options"] = _normalize_options(q.get("options") or [])
            set_prefetch(user_id, exam_slug, q)
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/question")
async def get_question(
    body: QuestionRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)
    _check_subscription(user_id, body.exam_slug)

    # Load user progress
    progress = fetchone(
        "SELECT domain_scores, questions_seen FROM user_progress "
        "WHERE user_id = %s AND exam_slug = %s",
        (user_id, body.exam_slug),
    )
    domain_scores = progress["domain_scores"] if progress else {}
    questions_seen = progress["questions_seen"] if progress else []

    # Get or create practice session (determines which set we're in)
    session = _get_or_create_practice_session(user_id, body.exam_slug, questions_seen)
    set_number = session["set_number"]
    questions_answered = session["questions_answered"]

    # Session already hit SET_SIZE — tell the frontend
    if questions_answered >= SET_SIZE:
        _mark_session_complete(session["id"])
        raise HTTPException(status_code=200, detail="SESSION_COMPLETE")

    def _with_meta(q: dict) -> dict:
        q["options"] = _normalize_options(q.get("options") or [])
        q["session_progress"] = {
            "answered": questions_answered,
            "total": SET_SIZE,
            "set_number": set_number,
        }
        return q

    # 1. Redis prefetch — only valid if it belongs to the current set
    prefetched = pop_prefetch(user_id, body.exam_slug)
    if prefetched and prefetched.get("set_number") == set_number:
        return _with_meta(prefetched)
    elif prefetched:
        # Stale prefetch from a different set — discard silently
        pass

    # 2. Pool cache → DB
    domain = _select_domain(body.exam_slug, domain_scores)
    pool = _fetch_question_pool(body.exam_slug, domain, questions_seen, set_number)

    # If no unseen questions in chosen domain, try any domain in this set
    if not pool:
        all_unseen = fetchall(
            "SELECT id, exam_slug, domain, topic, stem, options, difficulty FROM questions "
            "WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE "
            "AND id != ALL(%s) LIMIT 20" if questions_seen else
            "SELECT id, exam_slug, domain, topic, stem, options, difficulty FROM questions "
            "WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE LIMIT 20",
            (body.exam_slug, set_number, questions_seen) if questions_seen else (body.exam_slug, set_number),
        )
        pool = all_unseen or []

    if pool:
        q = random.choice(pool)
        return _with_meta(dict(q))

    # 3. Set not full yet → generate from AI and add to current set
    set_count = fetchone(
        "SELECT COUNT(*) AS cnt FROM questions WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE",
        (body.exam_slug, set_number),
    )
    if set_count and set_count["cnt"] >= SET_SIZE:
        # Set is full and user has seen everything — session should be done
        _mark_session_complete(session["id"])
        raise HTTPException(status_code=200, detail="SESSION_COMPLETE")

    generated = ai_generate_question(body.exam_slug, domain)
    q = execute(
        "INSERT INTO questions "
        "(id, exam_slug, domain, stem, options, correct_answer, explanation, difficulty, set_number) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, exam_slug, domain, stem, options, difficulty, set_number",
        (
            str(uuid.uuid4()), body.exam_slug, domain,
            generated["stem"], json.dumps(generated["options"]),
            generated["correct_answer"], generated["explanation"], "medium",
            set_number,
        ),
    )
    return _with_meta(dict(q))


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
