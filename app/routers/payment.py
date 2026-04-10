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


def _resolve_coupon(coupon_code: str | None) -> str | None:
    """
    Validate coupon code and return a Stripe coupon ID.
    Creates the Stripe coupon on first use if stripe_coupon_id is not yet set.
    Returns None if coupon_code is absent or invalid.
    """
    if not coupon_code:
        return None

    row = fetchone(
        "SELECT id, discount_pct, max_uses, used_count, expires_at, is_active, stripe_coupon_id "
        "FROM discount_coupons WHERE code = %s",
        (coupon_code.upper(),),
    )
    if not row or not row["is_active"]:
        raise HTTPException(status_code=400, detail="Invalid or inactive coupon code")

    if row["max_uses"] is not None and row["used_count"] >= row["max_uses"]:
        raise HTTPException(status_code=400, detail="Coupon usage limit reached")

    if row["expires_at"]:
        expires = row["expires_at"]
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Coupon has expired")

    # Lazily create the Stripe coupon object on first use
    stripe_coupon_id = row["stripe_coupon_id"]
    if not stripe_coupon_id:
        stripe_coupon = stripe.Coupon.create(
            percent_off=row["discount_pct"],
            duration="once",
            id=f"certmind-{row['id'][:8]}",
        )
        stripe_coupon_id = stripe_coupon.id
        execute(
            "UPDATE discount_coupons SET stripe_coupon_id = %s WHERE id = %s",
            (stripe_coupon_id, row["id"]),
        )

    # Increment used_count
    execute(
        "UPDATE discount_coupons SET used_count = used_count + 1 WHERE id = %s",
        (row["id"],),
    )

    return stripe_coupon_id


@router.post("/create-checkout")
async def create_checkout(
    body: CheckoutRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    stripe_coupon_id = _resolve_coupon(body.coupon_code)

    checkout_params: dict = dict(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_exam_price_id, "quantity": 1}],
        success_url=f"{settings.frontend_url}/payment/success",
        cancel_url=f"{settings.frontend_url}/payment/cancel",
        metadata={"user_id": user_id, "exam_slug": body.exam_slug},
    )
    if stripe_coupon_id:
        checkout_params["discounts"] = [{"coupon": stripe_coupon_id}]

    session = stripe.checkout.Session.create(**checkout_params)

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
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
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)
            execute(
                "UPDATE user_subscriptions SET status = 'active', expires_at = %s, "
                "stripe_payment_intent_id = %s WHERE stripe_session_id = %s",
                (expires_at, s.get("payment_intent", ""), s["id"]),
            )
            # Idempotency guard
            execute(
                "INSERT INTO user_subscriptions (id, user_id, exam_slug, stripe_session_id, status, expires_at) "
                "VALUES (%s, %s, %s, %s, 'active', %s) ON CONFLICT (stripe_session_id) DO NOTHING",
                (str(uuid.uuid4()), user_id, exam_slug, s["id"], expires_at),
            )

    return {"ok": True}
