from fastapi import APIRouter, Request, Depends, Query
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, fetchall
from app.schemas.models import ProgressResponse, AttemptsResponse, AttemptItem

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("/{exam_slug}", response_model=ProgressResponse)
async def get_progress(
    exam_slug: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    row = fetchone(
        "SELECT domain_scores, total_answered, total_correct, "
        "streak_days, time_committed_seconds "
        "FROM user_progress WHERE user_id = %s AND exam_slug = %s",
        (user_id, exam_slug),
    )
    if not row:
        return ProgressResponse(
            exam_slug=exam_slug,
            total_answered=0,
            total_correct=0,
            domain_scores={},
            streak_days=0,
            time_committed_seconds=0,
        )

    return ProgressResponse(
        exam_slug=exam_slug,
        total_answered=row["total_answered"],
        total_correct=row["total_correct"],
        domain_scores=row["domain_scores"] or {},
        streak_days=row["streak_days"] or 0,
        time_committed_seconds=row["time_committed_seconds"] or 0,
    )


@router.get("/{exam_slug}/attempts", response_model=AttemptsResponse)
async def get_attempts(
    exam_slug: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    filter: str = Query("all", pattern="^(all|correct|incorrect)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    await validate_session(request, user_id)

    where = "a.user_id = %s AND a.exam_slug = %s"
    params: list = [user_id, exam_slug]

    if filter == "correct":
        where += " AND a.is_correct = TRUE"
    elif filter == "incorrect":
        where += " AND a.is_correct = FALSE"

    total_row = fetchone(
        f"SELECT COUNT(*) AS cnt FROM user_question_attempts a WHERE {where}",
        tuple(params),
    )
    total = total_row["cnt"] if total_row else 0

    offset = (page - 1) * page_size
    rows = fetchall(
        f"""
        SELECT
            a.id, a.question_id, a.user_answer, a.is_correct,
            a.attempted_at,
            q.stem, q.options, q.correct_answer, q.option_explanations
        FROM user_question_attempts a
        JOIN questions q ON q.id = a.question_id
        WHERE {where}
        ORDER BY a.attempted_at DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params) + (page_size, offset),
    )

    attempts = []
    for r in (rows or []):
        attempts.append(AttemptItem(
            id=r["id"],
            question_id=r["question_id"],
            stem=r["stem"],
            options=r["options"] if isinstance(r["options"], list) else [],
            correct_answer=r["correct_answer"],
            user_answer=r["user_answer"],
            option_explanations=r["option_explanations"] or {},
            is_correct=r["is_correct"],
            attempted_at=r["attempted_at"].isoformat() if hasattr(r["attempted_at"], "isoformat") else str(r["attempted_at"]),
        ))

    return AttemptsResponse(
        attempts=attempts,
        total=total,
        page=page,
        page_size=page_size,
    )
