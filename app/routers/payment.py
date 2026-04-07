import stripe
import uuid
from fastapi import APIRouter, Request, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, execute
from app.core.config import settings
from app.schemas.models import CheckoutRequest

router = APIRouter(prefix="/payment", tags=["payment"])
stripe.api_key = settings.stripe_secret_key


@router.post("/create-checkout")
async def create_checkout(
    body: CheckoutRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    existing = fetchone(
        "SELECT exam_slug, expires_at FROM user_subscriptions "
        "WHERE user_id = %s AND status = 'active' ORDER BY expires_at DESC LIMIT 1",
        (user_id,),
    )
    if existing:
        expires = existing["expires_at"]
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > datetime.now(timezone.utc):
            raise HTTPException(
                status_code=409,
                detail=f"ACTIVE_SUBSCRIPTION_EXISTS|{existing['exam_slug']}|{expires.isoformat()}",
            )

    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_exam_price_id, "quantity": 1}],
        success_url=f"{settings.frontend_url}/payment/success",
        cancel_url=f"{settings.frontend_url}/payment/cancel",
        metadata={"user_id": user_id, "exam_slug": body.exam_slug},
    )

    expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    execute(
        "INSERT INTO user_subscriptions (id, user_id, exam_slug, stripe_session_id, status, expires_at) "
        "VALUES (%s, %s, %s, %s, 'pending', %s)",
        (str(uuid.uuid4()), user_id, body.exam_slug, session.id, expires_at),
    )
    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"]
        if s.get("payment_status") == "paid":
            user_id = s["metadata"]["user_id"]
            exam_slug = s["metadata"]["exam_slug"]
            expires_at = datetime.now(timezone.utc) + timedelta(days=14)
            execute(
                "UPDATE user_subscriptions SET status = 'active', expires_at = %s, "
                "stripe_payment_intent_id = %s WHERE stripe_session_id = %s",
                (expires_at, s.get("payment_intent", ""), s["id"]),
            )
            # Also ensure row exists (idempotency)
            execute(
                "INSERT INTO user_subscriptions (id, user_id, exam_slug, stripe_session_id, status, expires_at) "
                "VALUES (%s, %s, %s, %s, 'active', %s) ON CONFLICT (stripe_session_id) DO NOTHING",
                (str(uuid.uuid4()), user_id, exam_slug, s["id"], expires_at),
            )

    return {"ok": True}
