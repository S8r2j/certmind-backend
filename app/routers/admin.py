import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, fetchall, execute
from app.schemas.models import CouponResponse, CreateCouponRequest, CreateCourseRequest
from app.services.ai import EXAM_METADATA

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Admin gate ────────────────────────────────────────────────────────────────

async def require_admin(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> str:
    await validate_session(request, user_id)
    row = fetchone("SELECT is_admin FROM users WHERE id = %s", (user_id,))
    if not row or not row["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(admin_id: str = Depends(require_admin)):
    rows = fetchall(
        "SELECT u.id, u.email, u.first_name, u.last_name, u.is_admin, u.trial_used, u.created_at, "
        "       s.exam_slug, s.status, s.expires_at "
        "FROM users u "
        "LEFT JOIN user_subscriptions s ON s.user_id = u.id AND s.status IN ('active','trial') AND s.expires_at > NOW() "
        "ORDER BY u.created_at DESC",
        (),
    )
    users: dict[str, dict] = {}
    for r in (rows or []):
        uid = r["id"]
        if uid not in users:
            users[uid] = {
                "id": uid,
                "email": r["email"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "is_admin": r["is_admin"],
                "trial_used": r["trial_used"],
                "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                "subscriptions": [],
            }
        if r["exam_slug"]:
            users[uid]["subscriptions"].append({
                "exam_slug": r["exam_slug"],
                "status": r["status"],
                "expires_at": r["expires_at"].isoformat() if hasattr(r["expires_at"], "isoformat") else str(r["expires_at"]),
            })
    return list(users.values())


# ── Coupons ───────────────────────────────────────────────────────────────────

@router.get("/coupons", response_model=list[CouponResponse])
async def list_coupons(admin_id: str = Depends(require_admin)):
    rows = fetchall("SELECT * FROM discount_coupons ORDER BY created_at DESC", ())
    result = []
    for r in (rows or []):
        result.append(CouponResponse(
            id=r["id"],
            code=r["code"],
            discount_pct=r["discount_pct"],
            max_uses=r["max_uses"],
            used_count=r["used_count"],
            expires_at=r["expires_at"].isoformat() if r["expires_at"] and hasattr(r["expires_at"], "isoformat") else r["expires_at"],
            is_active=r["is_active"],
            stripe_coupon_id=r.get("stripe_coupon_id"),
        ))
    return result


@router.post("/coupons", response_model=CouponResponse, status_code=201)
async def create_coupon(
    body: CreateCouponRequest,
    admin_id: str = Depends(require_admin),
):
    existing = fetchone("SELECT id FROM discount_coupons WHERE code = %s", (body.code.upper(),))
    if existing:
        raise HTTPException(status_code=409, detail="Coupon code already exists")

    coupon_id = str(uuid.uuid4())
    execute(
        "INSERT INTO discount_coupons (id, code, discount_pct, max_uses, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (coupon_id, body.code.upper(), body.discount_pct, body.max_uses, body.expires_at),
    )
    row = fetchone("SELECT * FROM discount_coupons WHERE id = %s", (coupon_id,))
    return CouponResponse(
        id=row["id"],
        code=row["code"],
        discount_pct=row["discount_pct"],
        max_uses=row["max_uses"],
        used_count=row["used_count"],
        expires_at=row["expires_at"].isoformat() if row["expires_at"] and hasattr(row["expires_at"], "isoformat") else row["expires_at"],
        is_active=row["is_active"],
        stripe_coupon_id=row.get("stripe_coupon_id"),
    )


@router.delete("/coupons/{coupon_id}", status_code=204)
async def delete_coupon(
    coupon_id: str,
    admin_id: str = Depends(require_admin),
):
    row = fetchone("SELECT id FROM discount_coupons WHERE id = %s", (coupon_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Coupon not found")
    execute("UPDATE discount_coupons SET is_active = FALSE WHERE id = %s", (coupon_id,))


# ── Courses ───────────────────────────────────────────────────────────────────

@router.get("/courses")
async def list_courses(admin_id: str = Depends(require_admin)):
    """Returns both DB-registered courses and hardcoded EXAM_METADATA entries."""
    db_rows = fetchall("SELECT * FROM exams ORDER BY created_at DESC", ()) or []
    db_slugs = {r["slug"] for r in db_rows}

    courses = []
    for r in db_rows:
        courses.append({
            "slug": r["slug"],
            "title": r["title"],
            "code": r["code"],
            "description": r.get("description"),
            "domains": r.get("domains") or [],
            "is_active": r.get("is_active", True),
            "source": "db",
        })

    # Append hardcoded exams not yet in DB
    for slug, meta in EXAM_METADATA.items():
        if slug not in db_slugs:
            courses.append({
                "slug": slug,
                "title": meta["title"],
                "code": meta["code"],
                "description": None,
                "domains": meta["domains"],
                "is_active": True,
                "source": "config",
            })

    return courses


@router.post("/courses", status_code=201)
async def create_course(
    body: CreateCourseRequest,
    admin_id: str = Depends(require_admin),
):
    existing = fetchone("SELECT slug FROM exams WHERE slug = %s", (body.slug,))
    if existing:
        raise HTTPException(status_code=409, detail="Course slug already exists")

    execute(
        "INSERT INTO exams (id, slug, title, code, description, domains) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), body.slug, body.title, body.code, body.description,
         json.dumps(body.domains)),
    )
    return {"slug": body.slug, "title": body.title}


@router.patch("/courses/{slug}")
async def update_course(
    slug: str,
    body: dict,
    admin_id: str = Depends(require_admin),
):
    row = fetchone("SELECT slug FROM exams WHERE slug = %s", (slug,))
    if not row:
        raise HTTPException(status_code=404, detail="Course not found")

    allowed = {"title", "code", "description", "is_active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    execute(
        f"UPDATE exams SET {set_clause} WHERE slug = %s",
        (*updates.values(), slug),
    )
    return {"slug": slug, **updates}


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(admin_id: str = Depends(require_admin)):
    users_total = fetchone("SELECT COUNT(*) AS cnt FROM users", ())
    active_subs = fetchone(
        "SELECT COUNT(*) AS cnt FROM user_subscriptions "
        "WHERE status IN ('active','trial') AND expires_at > NOW()", ()
    )
    questions_total = fetchone("SELECT COUNT(*) AS cnt FROM questions WHERE is_active = TRUE", ())
    attempts_total = fetchone("SELECT COUNT(*) AS cnt FROM user_question_attempts", ())

    return {
        "users_total": users_total["cnt"] if users_total else 0,
        "active_subscriptions": active_subs["cnt"] if active_subs else 0,
        "questions_total": questions_total["cnt"] if questions_total else 0,
        "attempts_total": attempts_total["cnt"] if attempts_total else 0,
    }
