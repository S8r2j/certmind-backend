from fastapi import APIRouter, Request, Depends
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone
from app.schemas.models import ProgressResponse

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("/{exam_slug}", response_model=ProgressResponse)
async def get_progress(
    exam_slug: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    row = fetchone(
        "SELECT domain_scores, total_answered, total_correct FROM user_progress "
        "WHERE user_id = %s AND exam_slug = %s",
        (user_id, exam_slug),
    )
    if not row:
        return ProgressResponse(exam_slug=exam_slug, total_answered=0, total_correct=0, domain_scores={})

    return ProgressResponse(
        exam_slug=exam_slug,
        total_answered=row["total_answered"],
        total_correct=row["total_correct"],
        domain_scores=row["domain_scores"] or {},
    )
