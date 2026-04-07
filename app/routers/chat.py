import asyncio
import json
import re
import uuid
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from datetime import datetime, timezone
from app.middleware.auth import get_current_user
from app.middleware.session import validate_session
from app.services.database import fetchone, execute
from app.services.ai import stream_chat, EXAM_METADATA
from app.services.sanitize import sanitize_input
from app.schemas.models import ChatRequest
from app.core.config import settings

router = APIRouter(prefix="/chat", tags=["chat"])
MAX_TOKENS_PER_DAY = 50000

# Words that strongly suggest the message is about cloud/AI/certification topics
_EXAM_KEYWORDS = re.compile(
    r"\b(aws|cloud|exam|certif|domain|s3|ec2|iam|vpc|lambda|sagemaker|bedrock|"
    r"ai|ml|model|generat|llm|neural|transform|training|inference|"
    r"security|compliance|governance|architect|resilient|cost|billing|pricing|"
    r"question|quiz|ask me|explain|what is|how does|define|difference|compare|"
    r"study|practice|score|domain|weak|strong|gap|prepare|concept|topic)\b",
    re.IGNORECASE,
)

def _is_off_topic(text: str, has_history: bool) -> bool:
    """
    Return True only if the message is clearly off-topic.
    Short messages (≤10 chars) and replies within an existing conversation
    are always allowed through — they're almost certainly contextual.
    """
    if has_history or len(text.strip()) <= 10:
        return False
    return not _EXAM_KEYWORDS.search(text)


def _check_token_budget(user_id: str, subscription_id: str) -> None:
    if subscription_id == "bypass":
        return
    today = datetime.now(timezone.utc).date()
    row = fetchone(
        "SELECT input_tokens + output_tokens AS total FROM token_usage "
        "WHERE user_id = %s AND subscription_id = %s AND recorded_at = %s",
        (user_id, subscription_id, today),
    )
    if row and row["total"] >= MAX_TOKENS_PER_DAY:
        raise HTTPException(status_code=429, detail="Daily token limit reached. Try again tomorrow.")


def _log_tokens(user_id: str, subscription_id: str, input_tokens: int, output_tokens: int) -> None:
    today = datetime.now(timezone.utc).date()
    execute(
        "INSERT INTO token_usage (id, user_id, subscription_id, input_tokens, output_tokens, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, subscription_id, recorded_at) DO UPDATE "
        "SET input_tokens = token_usage.input_tokens + EXCLUDED.input_tokens, "
        "    output_tokens = token_usage.output_tokens + EXCLUDED.output_tokens",
        (str(uuid.uuid4()), user_id, subscription_id, input_tokens, output_tokens, today),
    )


@router.get("/sessions")
async def list_chat_sessions(
    exam_slug: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """Return all sessions for this user+exam, newest first, with a preview."""
    await validate_session(request, user_id)
    from app.services.database import fetchall
    rows = fetchall(
        "SELECT id, created_at, messages FROM chat_sessions "
        "WHERE user_id = %s AND exam_slug = %s ORDER BY created_at DESC",
        (user_id, exam_slug),
    )
    sessions = []
    for row in rows:
        msgs = row["messages"] or []
        # Preview = first user message truncated to 60 chars
        preview = next(
            (m["content"][:60] + ("…" if len(m["content"]) > 60 else "")
             for m in msgs if m.get("role") == "user"),
            "New conversation",
        )
        sessions.append({
            "id": row["id"],
            "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
            "preview": preview,
            "message_count": len(msgs),
        })
    return {"sessions": sessions}


@router.get("/history")
async def get_chat_history(
    exam_slug: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    session_id: str | None = None,
):
    await validate_session(request, user_id)
    if session_id:
        row = fetchone(
            "SELECT messages FROM chat_sessions WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
    else:
        row = fetchone(
            "SELECT messages FROM chat_sessions "
            "WHERE user_id = %s AND exam_slug = %s ORDER BY created_at DESC LIMIT 1",
            (user_id, exam_slug),
        )
    return {"messages": row["messages"] if row else []}


@router.post("/message")
async def chat_message(
    body: ChatRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    await validate_session(request, user_id)

    # Sanitize first — cheap, no I/O
    sanitized = sanitize_input(body.message)

    meta = EXAM_METADATA.get(body.exam_slug, {})
    exam_title = meta.get("title", body.exam_slug)

    # Fetch subscription and progress in parallel (independent of each other)
    sub, progress = await asyncio.gather(
        asyncio.to_thread(
            fetchone,
            "SELECT id, expires_at FROM user_subscriptions "
            "WHERE user_id = %s AND exam_slug = %s AND status = 'active' LIMIT 1",
            (user_id, body.exam_slug),
        ),
        asyncio.to_thread(
            fetchone,
            "SELECT domain_scores FROM user_progress WHERE user_id = %s AND exam_slug = %s",
            (user_id, body.exam_slug),
        ),
    )

    if not sub and not settings.bypass_subscription:
        raise HTTPException(status_code=403, detail="No active subscription for this exam")

    if sub and not settings.bypass_subscription:
        expires_at = sub["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=403, detail="Subscription expired")

    subscription_id = str(sub["id"]) if sub else "bypass"
    _check_token_budget(user_id, subscription_id)

    # Off-topic check — allow through if continuing an existing session (has context)
    if _is_off_topic(sanitized, has_history=bool(body.session_id)):
        refusal = f"I'm here to help you prepare for {exam_title}. Please ask me an exam-related question."

        async def _refusal_stream():
            yield f"data: {json.dumps({'text': refusal})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_refusal_stream(), media_type="text/event-stream")

    exam_code = meta.get("code", "")
    domain_list = ", ".join(d["name"] for d in meta.get("domains", []))

    weak_context = ""
    if progress and progress["domain_scores"]:
        scores = progress["domain_scores"]
        weakest = min(
            scores.items(),
            key=lambda x: x[1]["correct"] / x[1]["total"] if x[1]["total"] > 0 else 1,
        )
        ds = weakest[1]
        pct = int(100 * ds["correct"] / ds["total"]) if ds["total"] > 0 else 0
        weak_context = f" Student's weakest domain: {weakest[0]} ({pct}%)."

    system_prompt = (
        f"You are CertMind AI, an exam tutor for {exam_title} ({exam_code}). "
        f"Domains: {domain_list}.{weak_context} "
        "Only answer exam-related questions. "
        "NEVER generate practice questions, MCQs, or quizzes — if the user asks for a question or quiz, "
        "respond only with: \"Head to Practice Mode to get adaptive exam questions with answer tracking. "
        "I'm here to explain concepts and analyze your gaps.\" "
        "Use Markdown: **bold** key terms, ### section headers, - bullet lists. "
        "For gap analysis: ### Strengths, ### Focus Areas, ### Action Plan, then a > blockquote with the #1 priority. "
        "Be concise. No filler."
    )

    if body.session_id:
        # Continue a specific existing session
        session_row = fetchone(
            "SELECT id, messages FROM chat_sessions WHERE id = %s AND user_id = %s",
            (body.session_id, user_id),
        )
        if session_row:
            session_id = str(session_row["id"])
            messages = session_row["messages"] or []
        else:
            # session_id not found — treat as new
            session_id = str(uuid.uuid4())
            messages = []
            execute(
                "INSERT INTO chat_sessions (id, user_id, exam_slug, messages) VALUES (%s, %s, %s, %s)",
                (session_id, user_id, body.exam_slug, json.dumps([])),
            )
    else:
        # No session_id → always create a new session
        session_id = str(uuid.uuid4())
        messages = []
        execute(
            "INSERT INTO chat_sessions (id, user_id, exam_slug, messages) VALUES (%s, %s, %s, %s)",
            (session_id, user_id, body.exam_slug, json.dumps([])),
        )

    messages.append({"role": "user", "content": sanitized})
    if len(messages) > 20:
        messages = messages[-20:]

    async def stream_response():
        full_text = ""
        input_tok = 0
        output_tok = 0
        # First event: tell the frontend which session this belongs to
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"
        try:
            async for text, in_tok, out_tok in stream_chat(system_prompt, messages):
                if text:
                    full_text += text
                    yield f"data: {json.dumps({'text': text})}\n\n"
                if in_tok or out_tok:
                    input_tok, output_tok = in_tok, out_tok
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"
            messages.append({"role": "assistant", "content": full_text})
            execute(
                "UPDATE chat_sessions SET messages = %s WHERE id = %s",
                (json.dumps(messages), session_id),
            )
            if (input_tok or output_tok) and subscription_id != "bypass":
                _log_tokens(user_id, subscription_id, input_tok, output_tok)

    return StreamingResponse(stream_response(), media_type="text/event-stream")
