import asyncio
import csv
import io
import json
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, fetchall, execute
from app.schemas.models import CouponResponse, CreateCouponRequest, CreateCourseRequest, ExtendTrialRequest
from app.services.ai import EXAM_METADATA, enrich_question
from app.services.platform_settings import get_all_settings, set_setting

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


# ── Extend trial ─────────────────────────────────────────────────────────────

@router.post("/users/{user_id}/extend-trial")
async def extend_trial(
    user_id: str,
    body: ExtendTrialRequest,
    admin_id: str = Depends(require_admin),
):
    """Extend (or create) a trial subscription for a user."""
    row = fetchone(
        "SELECT id, expires_at FROM user_subscriptions "
        "WHERE user_id = %s AND status = 'trial' ORDER BY expires_at DESC LIMIT 1",
        (user_id,),
    )
    now = datetime.now(timezone.utc)
    if row:
        current = row["expires_at"]
        if isinstance(current, str):
            current = datetime.fromisoformat(current.replace("Z", "+00:00"))
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        base = max(current, now)
        new_expires = base + timedelta(days=body.days)
        execute(
            "UPDATE user_subscriptions SET expires_at = %s, status = 'trial' WHERE id = %s",
            (new_expires, row["id"]),
        )
    else:
        if not body.exam_slug:
            raise HTTPException(status_code=400, detail="exam_slug is required when no trial exists")
        new_expires = now + timedelta(days=body.days)
        execute(
            "INSERT INTO user_subscriptions (id, user_id, exam_slug, status, expires_at) "
            "VALUES (%s, %s, %s, 'trial', %s)",
            (str(uuid.uuid4()), user_id, body.exam_slug, new_expires),
        )
        execute("UPDATE users SET trial_used = TRUE WHERE id = %s", (user_id,))
    return {"ok": True, "new_expires_at": new_expires.isoformat()}


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


# ── Platform Settings ────────────────────────────────────────────────────────

@router.get("/settings")
async def list_settings(admin_id: str = Depends(require_admin)):
    """Return all platform settings."""
    return get_all_settings()


@router.put("/settings/{key}")
async def update_setting(
    key: str,
    body: dict,
    admin_id: str = Depends(require_admin),
):
    """Update a single platform setting by key."""
    value = body.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="'value' field is required")
    set_setting(key, str(value))
    return {"key": key, "value": str(value)}


# ── CSV Import ────────────────────────────────────────────────────────────────

_REQUIRED_COLS = {"stem", "correct_answer"}
_FULL_COLS = {"stem", "option_a", "option_b", "option_c", "option_d", "correct_answer"}
_VALID_ANSWERS = {"A", "B", "C", "D"}

_FULL_TEMPLATE_ROW = {
    "stem": "Which AWS service provides managed relational database hosting?",
    "option_a": "Amazon DynamoDB",
    "option_b": "Amazon RDS",
    "option_c": "Amazon Redshift",
    "option_d": "Amazon ElastiCache",
    "correct_answer": "B",
    "explanation": "Amazon RDS manages relational databases like MySQL, PostgreSQL, etc.",
    "option_explanation_a": "Incorrect — DynamoDB is a NoSQL key-value store.",
    "option_explanation_b": "Correct — RDS provides managed relational database engines.",
    "option_explanation_c": "Incorrect — Redshift is a data warehouse service.",
    "option_explanation_d": "Incorrect — ElastiCache is an in-memory caching service.",
    "domain": "Cloud Technology and Services",
    "difficulty": "easy",
}
_MINIMAL_TEMPLATE_ROW = {
    "stem": "What does S3 stand for in AWS?",
    "correct_answer": "A",
    "domain": "Cloud Technology and Services",
}


def _validate_csv(content: bytes) -> tuple[list[dict], str | None]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], "File is not valid UTF-8. Save your CSV as UTF-8 and try again."

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], "CSV appears to be empty — no header row found."

    cols = {c.strip().lower() for c in reader.fieldnames}
    missing = _REQUIRED_COLS - cols
    if missing:
        return [], f"CSV is missing required column(s): {', '.join(sorted(missing))}"

    rows = []
    for i, row in enumerate(reader, start=2):  # row 1 is header
        stem = (row.get("stem") or "").strip()
        answer = (row.get("correct_answer") or "").strip().upper()
        if not stem:
            return [], f"Row {i}: 'stem' is empty."
        if answer not in _VALID_ANSWERS:
            return [], f"Row {i}: 'correct_answer' must be A, B, C or D — got '{answer}'."
        rows.append({k.strip().lower(): (v or "").strip() for k, v in row.items()})

    if not rows:
        return [], "CSV has a header but no data rows."

    return rows, None


def _find_import_set_number(exam_slug: str) -> int:
    """Return the set_number to use for imported questions (first set with < 50 questions)."""
    row = fetchone(
        "SELECT set_number, COUNT(*) AS cnt FROM questions "
        "WHERE exam_slug = %s AND is_active = TRUE "
        "GROUP BY set_number ORDER BY set_number DESC LIMIT 1",
        (exam_slug,),
    )
    if not row:
        return 1
    if row["cnt"] < 50:
        return row["set_number"]
    return row["set_number"] + 1


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_import(exam_slug: str, rows: list[dict]):
    total = len(rows)
    inserted = skipped = errors = 0

    yield _sse({"step": "validating", "message": f"Validation passed — {total} row(s) ready.", "current": 0, "total": total, "inserted": 0, "skipped": 0, "errors": 0})

    set_number = await asyncio.to_thread(_find_import_set_number, exam_slug)

    for i, row in enumerate(rows, start=1):
        stem = row["stem"]
        correct_answer = row["correct_answer"].upper()
        domain = row.get("domain", "")
        difficulty = row.get("difficulty", "medium") or "medium"

        # Duplicate check
        dup = await asyncio.to_thread(
            fetchone,
            "SELECT id FROM questions WHERE exam_slug = %s AND LOWER(stem) = LOWER(%s)",
            (exam_slug, stem),
        )
        if dup:
            skipped += 1
            yield _sse({"step": "processing", "message": f"Row {i}: skipped (duplicate stem).", "current": i, "total": total, "row_index": i, "mode": "skip", "inserted": inserted, "skipped": skipped, "errors": errors})
            continue

        # Detect mode
        is_full = all(row.get(f"option_{k.lower()}") for k in ["a", "b", "c", "d"])
        mode = "full" if is_full else "minimal"

        if mode == "full":
            options = [
                {"key": "A", "text": row["option_a"]},
                {"key": "B", "text": row["option_b"]},
                {"key": "C", "text": row["option_c"]},
                {"key": "D", "text": row["option_d"]},
            ]
            explanation = row.get("explanation", "")
            option_explanations = {
                "A": row.get("option_explanation_a", ""),
                "B": row.get("option_explanation_b", ""),
                "C": row.get("option_explanation_c", ""),
                "D": row.get("option_explanation_d", ""),
            }
            yield _sse({"step": "processing", "message": f"Row {i}: inserting (full mode).", "current": i, "total": total, "row_index": i, "mode": mode, "inserted": inserted, "skipped": skipped, "errors": errors})
        else:
            yield _sse({"step": "processing", "message": f"Row {i}: enriching with AI (minimal mode)…", "current": i, "total": total, "row_index": i, "mode": mode, "inserted": inserted, "skipped": skipped, "errors": errors})
            try:
                enriched = await asyncio.to_thread(enrich_question, stem, correct_answer, exam_slug, domain)
                options = enriched["options"]
                explanation = enriched.get("explanation", "")
                option_explanations = enriched.get("option_explanations", {})
            except Exception as exc:
                errors += 1
                yield _sse({"step": "processing", "message": f"Row {i}: AI enrichment failed — {exc}.", "current": i, "total": total, "row_index": i, "mode": mode, "inserted": inserted, "skipped": skipped, "errors": errors})
                continue

        try:
            await asyncio.to_thread(
                execute,
                "INSERT INTO questions (id, exam_slug, domain, stem, options, correct_answer, "
                "explanation, option_explanations, difficulty, set_number) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid4()), exam_slug, domain, stem,
                    json.dumps(options), correct_answer,
                    explanation, json.dumps(option_explanations),
                    difficulty, set_number,
                ),
            )
            inserted += 1
            yield _sse({"step": "processing", "message": f"Row {i}: inserted successfully.", "current": i, "total": total, "row_index": i, "mode": mode, "inserted": inserted, "skipped": skipped, "errors": errors})
        except Exception as exc:
            errors += 1
            yield _sse({"step": "processing", "message": f"Row {i}: DB insert failed — {exc}.", "current": i, "total": total, "row_index": i, "mode": mode, "inserted": inserted, "skipped": skipped, "errors": errors})

    yield _sse({"step": "done", "message": "Import complete.", "current": total, "total": total, "inserted": inserted, "skipped": skipped, "errors": errors})


@router.post("/import-questions")
async def import_questions(
    exam_slug: str = Form(...),
    file: UploadFile = File(...),
    admin_id: str = Depends(require_admin),
):
    content = await file.read()
    rows, error = _validate_csv(content)
    if error:
        async def _error_stream():
            yield _sse({"step": "error", "message": error, "current": 0, "total": -1, "inserted": 0, "skipped": 0, "errors": 1})
        return StreamingResponse(_error_stream(), media_type="text/event-stream")

    return StreamingResponse(_stream_import(exam_slug, rows), media_type="text/event-stream")


@router.get("/import-template/{exam_slug}")
async def import_template(
    exam_slug: str,
    admin_id: str = Depends(require_admin),
):
    full_headers = [
        "stem", "option_a", "option_b", "option_c", "option_d",
        "correct_answer", "explanation",
        "option_explanation_a", "option_explanation_b",
        "option_explanation_c", "option_explanation_d",
        "domain", "difficulty",
    ]
    minimal_headers = ["stem", "correct_answer", "domain"]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=full_headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerow(_FULL_TEMPLATE_ROW)

    # Minimal row — fill missing full columns with empty string
    minimal_row = {h: "" for h in full_headers}
    minimal_row.update(_MINIMAL_TEMPLATE_ROW)
    writer.writerow(minimal_row)

    csv_bytes = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=import-template-{exam_slug}.csv"},
    )
