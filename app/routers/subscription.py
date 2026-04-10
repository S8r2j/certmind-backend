from fastapi import APIRouter, Request, Depends
from datetime import datetime, timezone
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, execute
from app.schemas.models import SubscriptionResponse
from app.core.config import settings
from app.services.platform_settings import get_int as _get_int

router = APIRouter(prefix="/subscription", tags=["subscription"])


@router.get("/status", response_model=SubscriptionResponse)
async def get_subscription_status(
    request: Request,
    exam_slug: str = "",
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    if settings.bypass_subscription:
        return SubscriptionResponse(active=True, exam_slug=exam_slug or "all", days_remaining=999)

    if exam_slug:
        row = fetchone(
            "SELECT exam_slug, expires_at, status FROM user_subscriptions "
            "WHERE user_id = %s AND exam_slug = %s AND status IN ('active', 'trial') "
            "ORDER BY expires_at DESC LIMIT 1",
            (user_id, exam_slug),
        )
    else:
        row = fetchone(
            "SELECT exam_slug, expires_at, status FROM user_subscriptions "
            "WHERE user_id = %s AND status IN ('active', 'trial') ORDER BY expires_at DESC LIMIT 1",
            (user_id,),
        )

    if not row:
        return SubscriptionResponse(active=False)

    expires_at = row["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if expires_at <= now:
        execute(
            "UPDATE user_subscriptions SET status = 'expired' WHERE user_id = %s AND exam_slug = %s "
            "AND status IN ('active', 'trial')",
            (user_id, row["exam_slug"]),
        )
        return SubscriptionResponse(active=False)

    days_remaining = max(0, (expires_at - now).days)
    is_trial = row["status"] == "trial"
    return SubscriptionResponse(
        active=True,
        exam_slug=row["exam_slug"],
        expires_at=expires_at.isoformat(),
        days_remaining=days_remaining,
        is_trial=is_trial,
        trial_question_limit=_get_int("trial_question_limit", 25) if is_trial else None,
    )
