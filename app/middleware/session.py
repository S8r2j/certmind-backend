from fastapi import Request, HTTPException
from app.services.database import fetchone, execute


async def validate_session(request: Request, user_id: str) -> None:
    session_token = request.headers.get("X-Session-Token")
    if not session_token:
        raise HTTPException(status_code=401, detail="SESSION_INVALIDATED")

    row = fetchone(
        "SELECT is_active FROM user_sessions WHERE session_token = %s AND user_id = %s",
        (session_token, user_id),
    )
    if not row or not row["is_active"]:
        raise HTTPException(status_code=401, detail="SESSION_INVALIDATED")

    execute(
        "UPDATE user_sessions SET last_seen_at = NOW() WHERE session_token = %s",
        (session_token,),
    )
