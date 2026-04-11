import json
import random
import uuid
from datetime import datetime, timezone, timedelta, date
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, fetchall, execute
from app.services.ai import (
    generate_question as ai_generate_question,
    generate_multi_question as ai_generate_multi,
    generate_fill_question as ai_generate_fill,
    EXAM_METADATA, _normalize_options,
)
from app.services.redis_client import (
    cache_question_pool, get_cached_pool,
    set_prefetch, pop_prefetch,
)
from app.schemas.models import QuestionRequest, AnswerRequest
from app.core.config import settings
from app.services.platform_settings import get_int

router = APIRouter(prefix="/practice", tags=["practice"])


def _set_size() -> int:
    return get_int("session_set_size", 50)

def _trial_days() -> int:
    return get_int("trial_days", 3)

def _trial_question_limit() -> int:
    return get_int("trial_question_limit", 25)


# ── Subscription gate ─────────────────────────────────────────────────────────


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ensure_trial_or_raise(user_id: str, exam_slug: str, client_ip: str) -> None:
    user = fetchone("SELECT trial_used FROM users WHERE id = %s", (user_id,))

    if not user or user["trial_used"]:
        # Trial already used for this account — check if they're enrolled elsewhere (during trial)
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
        raise HTTPException(status_code=403, detail="TRIAL_USED|NO_SUBSCRIPTION")

    # IP check: has this IP already been used for a trial on any account?
    ip_row = fetchone("SELECT ip FROM trial_ips WHERE ip = %s", (client_ip,))
    if ip_row:
        raise HTTPException(status_code=403, detail="TRIAL_IP_USED")

    # Grant trial for this exam on this account
    trial_expires = datetime.now(timezone.utc) + timedelta(days=_trial_days())
    execute(
        "INSERT INTO user_subscriptions (id, user_id, exam_slug, status, expires_at) "
        "VALUES (%s, %s, %s, 'trial', %s)",
        (str(uuid.uuid4()), user_id, exam_slug, trial_expires),
    )
    execute("UPDATE users SET trial_used = TRUE WHERE id = %s", (user_id,))
    execute(
        "INSERT INTO trial_ips (ip) VALUES (%s) ON CONFLICT DO NOTHING",
        (client_ip,),
    )


def _check_subscription(user_id: str, exam_slug: str, client_ip: str) -> None:
    if settings.bypass_subscription:
        return
    sub = fetchone(
        "SELECT id, expires_at, status FROM user_subscriptions "
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
            _ensure_trial_or_raise(user_id, exam_slug, client_ip)
            return
        # Enforce question cap during trial
        if sub["status"] == "trial":
            answered = fetchone(
                "SELECT COUNT(*) AS cnt FROM user_question_attempts "
                "WHERE user_id = %s AND exam_slug = %s",
                (user_id, exam_slug),
            )
            if answered and answered["cnt"] >= _trial_question_limit():
                raise HTTPException(status_code=403, detail="TRIAL_LIMIT_REACHED")
    else:
        _ensure_trial_or_raise(user_id, exam_slug, client_ip)


# ── Domain selection ──────────────────────────────────────────────────────────

def _select_domain(exam_slug: str, domain_scores: dict) -> str:
    meta = EXAM_METADATA.get(exam_slug)
    if not meta:
        row = fetchone(
            "SELECT domains FROM exams WHERE slug = %s AND is_active = TRUE",
            (exam_slug,),
        )
        if not row or not row["domains"]:
            raise HTTPException(status_code=404, detail="Exam not found")
        raw = row["domains"]
        domains = raw if isinstance(raw, list) else json.loads(raw)
        meta = {"domains": domains}
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
    rows = fetchall(
        "SELECT set_number, COUNT(*) AS cnt FROM questions "
        "WHERE exam_slug = %s AND is_active = TRUE "
        "GROUP BY set_number ORDER BY set_number",
        (exam_slug,),
    )
    if not rows:
        return 1

    seen_set = set(questions_seen)
    for row in rows:
        sn, cnt = row["set_number"], row["cnt"]
        if cnt < _set_size():
            return sn
        ids = fetchall(
            "SELECT id FROM questions WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE",
            (exam_slug, sn),
        )
        if any(r["id"] not in seen_set for r in ids):
            return sn

    return rows[-1]["set_number"] + 1


def _get_or_create_practice_session(user_id: str, exam_slug: str, questions_seen: list) -> dict:
    session = fetchone(
        "SELECT id, set_number, questions_answered, is_complete, multi_served, fill_served "
        "FROM practice_sessions "
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
    return {
        "id": session_id, "set_number": set_number,
        "questions_answered": 0, "is_complete": False,
        "multi_served": 0, "fill_served": 0,
    }


def _mark_session_complete(session_id: str) -> None:
    execute(
        "UPDATE practice_sessions SET is_complete = TRUE, last_active_at = NOW() WHERE id = %s",
        (session_id,),
    )


def _increment_session(session_id: str, time_spent: int = 0) -> int:
    row = execute(
        "UPDATE practice_sessions "
        "SET questions_answered = questions_answered + 1, "
        "    time_spent_seconds = time_spent_seconds + %s, "
        "    last_active_at = NOW() "
        "WHERE id = %s "
        "RETURNING questions_answered",
        (time_spent, session_id),
    )
    return row["questions_answered"] if row else 0


# ── Answer scoring ────────────────────────────────────────────────────────────

def _check_answer(submitted: str, correct: str, qtype: str) -> bool:
    if qtype == "multi":
        return set(submitted.upper().split(",")) == set(correct.upper().split(","))
    return submitted.strip().upper() == correct.strip().upper()


# ── Streak logic ──────────────────────────────────────────────────────────────

def _compute_streak(current_streak: int, last_streak_date) -> int:
    today = datetime.now(timezone.utc).date()
    if last_streak_date is None:
        return 1
    if isinstance(last_streak_date, str):
        last_streak_date = date.fromisoformat(last_streak_date)
    if last_streak_date == today:
        return current_streak  # already answered today
    if (today - last_streak_date).days == 1:
        return current_streak + 1  # consecutive day
    return 1  # gap — reset


# ── Question pool fetch (DB + Redis) ──────────────────────────────────────────

def _fetch_question_pool(exam_slug: str, domain: str, questions_seen: list, set_number: int) -> list[dict]:
    pool = get_cached_pool(exam_slug, f"{domain}:set{set_number}")
    if pool is None:
        pool = fetchall(
            "SELECT id, exam_slug, domain, topic, stem, options, difficulty, option_explanations, question_type "
            "FROM questions "
            "WHERE exam_slug = %s AND domain = %s AND set_number = %s AND question_type = 'single' AND is_active = TRUE LIMIT 20",
            (exam_slug, domain, set_number),
        )
        if pool:
            cache_question_pool(exam_slug, f"{domain}:set{set_number}", pool)

    seen_set = set(questions_seen)
    return [q for q in (pool or []) if q["id"] not in seen_set]


# ── Background prefetch ───────────────────────────────────────────────────────

def _prefetch_question_for_user(user_id: str, exam_slug: str) -> None:
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

@router.get("/session-status")
async def get_session_status(
    exam_slug: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)
    session = fetchone(
        "SELECT id, set_number, questions_answered, is_complete FROM practice_sessions "
        "WHERE user_id = %s AND exam_slug = %s AND is_complete = FALSE "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, exam_slug),
    )
    if not session or session["questions_answered"] == 0:
        return {"active": False}
    return {
        "active": True,
        "questions_answered": session["questions_answered"],
        "set_size": _set_size(),
        "set_number": session["set_number"],
    }


@router.post("/question")
async def get_question(
    body: QuestionRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)
    _check_subscription(user_id, body.exam_slug, _get_client_ip(request))

    progress = fetchone(
        "SELECT domain_scores, questions_seen FROM user_progress "
        "WHERE user_id = %s AND exam_slug = %s",
        (user_id, body.exam_slug),
    )
    domain_scores = progress["domain_scores"] if progress else {}
    questions_seen = progress["questions_seen"] if progress else []

    session = _get_or_create_practice_session(user_id, body.exam_slug, questions_seen)
    set_number = session["set_number"]
    questions_answered = session["questions_answered"]
    multi_served = session.get("multi_served", 0)
    fill_served = session.get("fill_served", 0)

    MULTI_QUOTA = 5
    FILL_QUOTA = 2

    # Tab-level lock: reject if another tab claimed this session in the last 30s
    if body.tab_id:
        lock_row = fetchone(
            "SELECT active_tab_id, last_active_at FROM practice_sessions WHERE id = %s",
            (session["id"],),
        )
        if lock_row:
            existing_tab = lock_row["active_tab_id"]
            last_active = lock_row["last_active_at"]
            if existing_tab and existing_tab != body.tab_id and last_active:
                if isinstance(last_active, str):
                    from datetime import datetime as _dt
                    last_active = _dt.fromisoformat(last_active.replace("Z", "+00:00"))
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - last_active).total_seconds()
                if age < 30:
                    raise HTTPException(status_code=403, detail="SESSION_ACTIVE_ELSEWHERE")
        # Claim the session for this tab
        execute(
            "UPDATE practice_sessions SET active_tab_id = %s WHERE id = %s",
            (body.tab_id, session["id"]),
        )

    if questions_answered >= _set_size():
        _mark_session_complete(session["id"])
        raise HTTPException(status_code=200, detail="SESSION_COMPLETE")

    def _with_meta(q: dict) -> dict:
        q["options"] = _normalize_options(q.get("options") or [])
        if not q.get("option_explanations"):
            q["option_explanations"] = {}
        q.setdefault("question_type", "single")
        # For multi-select: expose how many answers the user must select
        if q["question_type"] == "multi":
            # Fetch correct_answer count from DB (we don't want to expose the actual answer)
            ca_row = fetchone("SELECT correct_answer FROM questions WHERE id = %s", (q["id"],))
            if ca_row:
                parts = [p for p in ca_row["correct_answer"].split(",") if p.strip()]
                q["correct_answers_count"] = len(parts)
        q["session_progress"] = {
            "answered": questions_answered,
            "total": _set_size(),
            "set_number": set_number,
        }
        return q

    # 1. Redis prefetch (single-type only — skip if we need multi/fill next)
    needs_multi = multi_served < MULTI_QUOTA and questions_answered < _set_size() - FILL_QUOTA
    needs_fill = fill_served < FILL_QUOTA

    if not needs_multi and not needs_fill:
        prefetched = pop_prefetch(user_id, body.exam_slug)
        if prefetched and prefetched.get("set_number") == set_number:
            return _with_meta(prefetched)

    domain = _select_domain(body.exam_slug, domain_scores)

    # 2. Float pool for multi/fill — queried exam-wide, no set_number restriction
    if needs_multi or needs_fill:
        qtype = "multi" if needs_multi else "fill"
        counter_col = "multi_served" if needs_multi else "fill_served"
        float_pool = fetchall(
            "SELECT id, exam_slug, domain, topic, stem, options, difficulty, option_explanations, question_type "
            "FROM questions "
            "WHERE exam_slug = %s AND question_type = %s AND is_active = TRUE "
            + ("AND id != ALL(%s) LIMIT 10" if questions_seen else "LIMIT 10"),
            (body.exam_slug, qtype, questions_seen) if questions_seen else (body.exam_slug, qtype),
        )
        if float_pool:
            execute(
                f"UPDATE practice_sessions SET {counter_col} = {counter_col} + 1 WHERE id = %s",
                (session["id"],),
            )
            return _with_meta(dict(random.choice(float_pool)))
        else:
            # Generate one with AI
            generated = ai_generate_multi(body.exam_slug, domain) if qtype == "multi" else ai_generate_fill(body.exam_slug, domain)
            q_row = execute(
                "INSERT INTO questions "
                "(id, exam_slug, domain, stem, options, correct_answer, explanation, option_explanations, difficulty, set_number, question_type) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, exam_slug, domain, stem, options, difficulty, option_explanations, set_number, question_type",
                (
                    str(uuid.uuid4()), body.exam_slug, domain,
                    generated["stem"], json.dumps(generated["options"]),
                    generated["correct_answer"], generated["explanation"],
                    json.dumps(generated.get("option_explanations", {})),
                    "medium", 0, qtype,
                ),
            )
            execute(
                f"UPDATE practice_sessions SET {counter_col} = {counter_col} + 1 WHERE id = %s",
                (session["id"],),
            )
            return _with_meta(dict(q_row))

    # 3. Pool cache → DB (single-type questions)
    pool = _fetch_question_pool(body.exam_slug, domain, questions_seen, set_number)

    if not pool:
        all_unseen = fetchall(
            "SELECT id, exam_slug, domain, topic, stem, options, difficulty, option_explanations, question_type "
            "FROM questions "
            "WHERE exam_slug = %s AND set_number = %s AND question_type = 'single' AND is_active = TRUE "
            + ("AND id != ALL(%s) LIMIT 20" if questions_seen else "LIMIT 20"),
            (body.exam_slug, set_number, questions_seen) if questions_seen else (body.exam_slug, set_number),
        )
        pool = all_unseen or []

    if pool:
        q = random.choice(pool)
        return _with_meta(dict(q))

    # 4. Generate from AI (single)
    set_count = fetchone(
        "SELECT COUNT(*) AS cnt FROM questions WHERE exam_slug = %s AND set_number = %s AND is_active = TRUE",
        (body.exam_slug, set_number),
    )
    if set_count and set_count["cnt"] >= _set_size():
        _mark_session_complete(session["id"])
        raise HTTPException(status_code=200, detail="SESSION_COMPLETE")

    generated = ai_generate_question(body.exam_slug, domain)
    q = execute(
        "INSERT INTO questions "
        "(id, exam_slug, domain, stem, options, correct_answer, explanation, option_explanations, difficulty, set_number, question_type) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, exam_slug, domain, stem, options, difficulty, option_explanations, set_number, question_type",
        (
            str(uuid.uuid4()), body.exam_slug, domain,
            generated["stem"], json.dumps(generated["options"]),
            generated["correct_answer"], generated["explanation"],
            json.dumps(generated.get("option_explanations", {})),
            "medium", set_number, "single",
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
        "SELECT correct_answer, explanation, domain, option_explanations, question_type FROM questions WHERE id = %s",
        (body.question_id,),
    )
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    qtype = q.get("question_type") or "single"
    correct = _check_answer(body.answer, q["correct_answer"], qtype)
    domain = q["domain"]
    option_explanations = q.get("option_explanations") or {}
    time_spent = body.time_spent_seconds or 0

    today = datetime.now(timezone.utc).date()

    # Update user progress
    progress = fetchone(
        "SELECT id, domain_scores, questions_seen, total_answered, total_correct, "
        "streak_days, last_streak_date, time_committed_seconds "
        "FROM user_progress WHERE user_id = %s AND exam_slug = %s",
        (user_id, body.exam_slug),
    )
    if progress:
        domain_scores = progress["domain_scores"] or {}
        questions_seen = progress["questions_seen"] or []
        domain_scores.setdefault(domain, {"correct": 0, "total": 0})
        domain_scores[domain]["total"] += 1
        if correct:
            domain_scores[domain]["correct"] += 1
        if body.question_id not in questions_seen:
            questions_seen.append(body.question_id)

        new_streak = _compute_streak(
            progress.get("streak_days", 0),
            progress.get("last_streak_date"),
        )

        execute(
            "UPDATE user_progress SET "
            "domain_scores = %s, questions_seen = %s, "
            "total_answered = total_answered + 1, total_correct = total_correct + %s, "
            "streak_days = %s, last_streak_date = %s, "
            "time_committed_seconds = time_committed_seconds + %s "
            "WHERE id = %s",
            (
                json.dumps(domain_scores), questions_seen,
                1 if correct else 0,
                new_streak, today,
                time_spent,
                progress["id"],
            ),
        )
    else:
        domain_scores = {domain: {"correct": 1 if correct else 0, "total": 1}}
        new_streak = 1
        execute(
            "INSERT INTO user_progress "
            "(id, user_id, exam_slug, domain_scores, questions_seen, total_answered, total_correct, "
            "streak_days, last_streak_date, time_committed_seconds) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()), user_id, body.exam_slug,
                json.dumps(domain_scores), [body.question_id],
                1, 1 if correct else 0,
                1, today, time_spent,
            ),
        )

    # Record attempt
    execute(
        "INSERT INTO user_question_attempts (id, user_id, exam_slug, question_id, user_answer, is_correct) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), user_id, body.exam_slug, body.question_id, body.answer, correct),
    )

    # Advance practice session
    session = fetchone(
        "SELECT id, questions_answered FROM practice_sessions "
        "WHERE user_id = %s AND exam_slug = %s AND is_complete = FALSE "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, body.exam_slug),
    )
    session_complete = False
    if session:
        new_count = _increment_session(session["id"], time_spent)
        if new_count >= _set_size():
            _mark_session_complete(session["id"])
            session_complete = True

    if not session_complete:
        background_tasks.add_task(_prefetch_question_for_user, user_id, body.exam_slug)

    return {
        "correct": correct,
        "correct_answer": q["correct_answer"],
        "explanation": q["explanation"],
        "option_explanations": option_explanations,
        "domain_scores": domain_scores,
        "streak_days": new_streak,
        "session_complete": session_complete,
        "question_type": qtype,
    }
