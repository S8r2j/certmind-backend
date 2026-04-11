"""
AI provider orchestration.

AI_MODEL in .env controls which provider is used:
  claude-*       → Anthropic
  gemini-*       → Google Gemini (free tier: 1500 req/day)
  llama-* / mixtral-* / qwen-* / deepseek-* → Groq (free tier)

Default: gemini-2.0-flash   (free, no credit card needed)
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from app.core.config import settings

# ── Exam metadata (shared across providers) ─────────────────────────────────
EXAM_METADATA: dict = {
    "aws-cloud-practitioner": {
        "title": "AWS Cloud Practitioner",
        "code": "CLF-C02",
        "domains": [
            {"name": "Cloud Concepts", "weight": 0.24},
            {"name": "Security and Compliance", "weight": 0.30},
            {"name": "Cloud Technology and Services", "weight": 0.34},
            {"name": "Billing, Pricing, and Support", "weight": 0.12},
        ],
    },
    "aws-ai-practitioner": {
        "title": "AWS AI Practitioner",
        "code": "AIF-C01",
        "domains": [
            {"name": "Fundamentals of AI and ML", "weight": 0.20},
            {"name": "Fundamentals of Generative AI", "weight": 0.24},
            {"name": "Applications of Foundation Models", "weight": 0.28},
            {"name": "Guidelines for Responsible AI", "weight": 0.14},
            {"name": "Security, Compliance, and Governance for AI Solutions", "weight": 0.14},
        ],
    },
    "aws-solutions-architect": {
        "title": "AWS Solutions Architect Associate",
        "code": "SAA-C03",
        "domains": [
            {"name": "Design Secure Architectures", "weight": 0.30},
            {"name": "Design Resilient Architectures", "weight": 0.26},
            {"name": "Design High-Performing Architectures", "weight": 0.24},
            {"name": "Design Cost-Optimized Architectures", "weight": 0.20},
        ],
    },
}


def _parse_json(text: str) -> dict:
    """Strip markdown code fences then parse JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first line (```json or ```) and last line (```)
        inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    # Sometimes models prefix with a sentence before the JSON; find the first {
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    return json.loads(text)


def _model() -> str:
    return settings.ai_model


def _provider() -> str:
    m = _model()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    # llama / mixtral / qwen / deepseek / etc.
    return "groq"


# ── Streaming chat ───────────────────────────────────────────────────────────

async def stream_chat(
    system: str,
    messages: list[dict],
) -> AsyncIterator[tuple[str, int, int]]:
    """
    Yields (text_chunk, input_tokens, output_tokens).
    input/output tokens are only set on the final yielded tuple (others are 0, 0).
    """
    provider = _provider()

    if provider == "anthropic":
        async for item in _anthropic_stream(system, messages):
            yield item
    elif provider == "google":
        async for item in _google_stream(system, messages):
            yield item
    else:
        async for item in _groq_stream(system, messages):
            yield item


async def _anthropic_stream(system, messages):
    client = _get_anthropic_client()
    with client.messages.stream(
        model=_model(),
        max_tokens=1024,
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield (text, 0, 0)
        final = stream.get_final_message()
        yield ("", final.usage.input_tokens, final.usage.output_tokens)


async def _google_stream(system, messages):
    import google.generativeai as genai
    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(
        model_name=_model(),
        system_instruction=system,
    )
    # Convert OpenAI-style messages to Gemini history format
    history = []
    for m in messages[:-1]:
        history.append({
            "role": "user" if m["role"] == "user" else "model",
            "parts": [m["content"]],
        })
    last_msg = messages[-1]["content"] if messages else ""
    chat = model.start_chat(history=history)
    response = chat.send_message(last_msg, stream=True)
    for chunk in response:
        if chunk.text:
            yield (chunk.text, 0, 0)
    # Gemini doesn't expose token counts on streamed chunks easily
    yield ("", 0, 0)


_groq_client = None
_anthropic_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import AsyncGroq
        _groq_client = AsyncGroq(api_key=settings.groq_api_key)
    return _groq_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic as _anthropic
        _anthropic_client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


async def _groq_stream(system, messages):
    client = _get_groq_client()
    all_msgs = [{"role": "system", "content": system}] + messages
    stream = await client.chat.completions.create(
        model=_model(),
        messages=all_msgs,
        max_tokens=1024,
        stream=True,
    )
    async for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        if text:
            yield (text, 0, 0)
    yield ("", 0, 0)


# ── One-shot question generation ─────────────────────────────────────────────

def _normalize_options(options: list) -> list:
    """Convert [{A: text}, ...] → [{key: A, text: text}, ...] if needed."""
    if not options:
        return options
    if isinstance(options[0], dict) and "key" in options[0]:
        return options  # already correct format
    normalized = []
    for opt in options:
        for k, v in opt.items():
            normalized.append({"key": k, "text": v})
    return normalized


def enrich_question(stem: str, correct_answer: str, exam_slug: str, domain: str = "") -> dict:
    """
    Given a stem and the correct answer letter (A/B/C/D), generates:
    - option texts for all 4 keys (correct + 3 distractors)
    - explanation
    - option_explanations

    Synchronous — call via asyncio.to_thread when used inside async SSE generators.
    Returns same shape as generate_question: {options, explanation, option_explanations}
    """
    meta = EXAM_METADATA.get(exam_slug, {})
    exam_title = meta.get("title", exam_slug)
    domain_hint = f" This question is from the domain: {domain}." if domain else ""
    other_keys = [k for k in ["A", "B", "C", "D"] if k != correct_answer]

    prompt = (
        f"You are an expert exam question writer for {exam_title}.{domain_hint}\n\n"
        f"The following MCQ stem has a known correct answer that maps to option {correct_answer}:\n\n"
        f'STEM: "{stem}"\n\n'
        f"Your tasks:\n"
        f"1. Write the option TEXT for the correct answer ({correct_answer}) — it must directly and correctly answer the stem.\n"
        f"2. Write 3 plausible but INCORRECT distractor options for keys {', '.join(other_keys)}. "
        f"Distractors must be believable and related to the topic, but clearly wrong.\n"
        f"3. Write a 2-3 sentence explanation for why option {correct_answer} is correct.\n"
        f"4. Write a 1-2 sentence per-option explanation for all 4 options.\n\n"
        f"Respond ONLY with valid JSON, no markdown, no text outside the JSON.\n"
        f'Format: {{"options": [{{"key": "A", "text": "..."}}, {{"key": "B", "text": "..."}}, '
        f'{{"key": "C", "text": "..."}}, {{"key": "D", "text": "..."}}], '
        f'"explanation": "...", '
        f'"option_explanations": {{"A": "Correct — because ..." or "Incorrect — because ...", '
        f'"B": "...", "C": "...", "D": "..."}}}}'
    )

    provider = _provider()
    if provider == "anthropic":
        result = _anthropic_generate(prompt)
    elif provider == "google":
        result = _google_generate(prompt)
    else:
        result = _groq_generate(prompt)

    result["options"] = _normalize_options(result.get("options", []))
    if "option_explanations" not in result or not isinstance(result["option_explanations"], dict):
        result["option_explanations"] = {}

    # Guard: the correct_answer key must be present in returned options
    keys_present = {o["key"] for o in result.get("options", [])}
    if correct_answer not in keys_present:
        raise ValueError(f"AI did not return option for correct_answer key '{correct_answer}'")

    return result


def _get_exam_meta(exam_slug: str) -> dict:
    """Return exam metadata dict; falls back gracefully for custom exams."""
    return EXAM_METADATA.get(exam_slug, {"title": exam_slug, "code": "", "domains": []})


def generate_question(exam_slug: str, domain: str) -> dict:
    """Synchronous call — returns parsed JSON dict with normalized options and option_explanations."""
    meta = _get_exam_meta(exam_slug)
    domain_list = ", ".join(d["name"] for d in meta["domains"]) if meta["domains"] else domain
    title_str = f"{meta['title']} ({meta['code']})" if meta.get("code") else meta["title"]
    prompt = (
        f"You are an expert certification exam question writer for {title_str}.\n"
        f"Domains: {domain_list}\n"
        f'Generate ONE single-answer MCQ for domain: "{domain}", difficulty: medium.\n'
        "Scenario-based stem, 4 options A-D, exactly one correct answer. "
        "Also explain why each option is correct or incorrect in 1-2 sentences.\n"
        "Respond ONLY in valid JSON, no markdown, no explanation outside the JSON.\n"
        'Format: {"stem": "...", '
        '"options": [{"key": "A", "text": "..."}, {"key": "B", "text": "..."}, {"key": "C", "text": "..."}, {"key": "D", "text": "..."}], '
        '"correct_answer": "A", '
        '"explanation": "...", '
        '"option_explanations": {"A": "Correct — because ...", "B": "Incorrect — because ...", "C": "Incorrect — because ...", "D": "Incorrect — because ..."}}'
    )
    provider = _provider()
    if provider == "anthropic":
        result = _anthropic_generate(prompt)
    elif provider == "google":
        result = _google_generate(prompt)
    else:
        result = _groq_generate(prompt)
    result["options"] = _normalize_options(result.get("options", []))
    if "option_explanations" not in result or not isinstance(result["option_explanations"], dict):
        result["option_explanations"] = {}
    result["question_type"] = "single"
    return result


def generate_multi_question(exam_slug: str, domain: str) -> dict:
    """Generate a multi-select question where 2 or 3 options are correct.

    Returns same shape as generate_question but correct_answer is comma-separated
    e.g. "A,C" or "A,B,D", and question_type="multi".
    """
    meta = _get_exam_meta(exam_slug)
    domain_list = ", ".join(d["name"] for d in meta["domains"]) if meta["domains"] else domain
    title_str = f"{meta['title']} ({meta['code']})" if meta.get("code") else meta["title"]
    prompt = (
        f"You are an expert certification exam question writer for {title_str}.\n"
        f"Domains: {domain_list}\n"
        f'Generate ONE multi-select question for domain: "{domain}", difficulty: medium.\n'
        "IMPORTANT: The question must require the user to SELECT EITHER 2 OR 3 correct answers (you decide which). "
        "The stem MUST include phrasing like '(Select TWO)' or '(Select THREE)' at the end.\n"
        "Scenario-based stem, 4 options A-D. 2 or 3 options are correct, the rest are plausible distractors.\n"
        "correct_answer must be a comma-separated string of the correct keys, e.g. 'A,C' or 'A,B,D'.\n"
        "Also explain why each option is correct or incorrect in 1-2 sentences.\n"
        "Respond ONLY in valid JSON, no markdown, no explanation outside the JSON.\n"
        'Format: {"stem": "... (Select TWO)", '
        '"options": [{"key": "A", "text": "..."}, {"key": "B", "text": "..."}, {"key": "C", "text": "..."}, {"key": "D", "text": "..."}], '
        '"correct_answer": "A,C", '
        '"explanation": "...", '
        '"option_explanations": {"A": "Correct — because ...", "B": "Incorrect — because ...", "C": "Correct — because ...", "D": "Incorrect — because ..."}}'
    )
    provider = _provider()
    if provider == "anthropic":
        result = _anthropic_generate(prompt)
    elif provider == "google":
        result = _google_generate(prompt)
    else:
        result = _groq_generate(prompt)
    result["options"] = _normalize_options(result.get("options", []))
    if "option_explanations" not in result or not isinstance(result["option_explanations"], dict):
        result["option_explanations"] = {}
    result["question_type"] = "multi"
    # Normalize correct_answer: sort and uppercase
    ca = result.get("correct_answer", "")
    parts = sorted(k.strip().upper() for k in ca.split(",") if k.strip())
    result["correct_answer"] = ",".join(parts)
    return result


def generate_fill_question(exam_slug: str, domain: str) -> dict:
    """Generate a fill-in-the-blank question with 4 options, one correct.

    The stem contains exactly '[BLANK]'. correct_answer is a single key.
    question_type="fill".
    """
    meta = _get_exam_meta(exam_slug)
    domain_list = ", ".join(d["name"] for d in meta["domains"]) if meta["domains"] else domain
    title_str = f"{meta['title']} ({meta['code']})" if meta.get("code") else meta["title"]
    prompt = (
        f"You are an expert certification exam question writer for {title_str}.\n"
        f"Domains: {domain_list}\n"
        f'Generate ONE fill-in-the-blank question for domain: "{domain}", difficulty: medium.\n'
        "The stem must be a sentence with exactly one '[BLANK]' placeholder where the correct answer fits. "
        "Example: 'AWS [BLANK] is used to manage encryption keys for data at rest and in transit.'\n"
        "Provide 4 options A-D. Exactly ONE option correctly fills the blank. The other 3 are plausible but wrong.\n"
        "Also explain why each option is correct or incorrect in 1-2 sentences.\n"
        "Respond ONLY in valid JSON, no markdown, no explanation outside the JSON.\n"
        'Format: {"stem": "AWS [BLANK] is used to ...", '
        '"options": [{"key": "A", "text": "..."}, {"key": "B", "text": "..."}, {"key": "C", "text": "..."}, {"key": "D", "text": "..."}], '
        '"correct_answer": "B", '
        '"explanation": "...", '
        '"option_explanations": {"A": "Incorrect — because ...", "B": "Correct — because ...", "C": "Incorrect — because ...", "D": "Incorrect — because ..."}}'
    )
    provider = _provider()
    if provider == "anthropic":
        result = _anthropic_generate(prompt)
    elif provider == "google":
        result = _google_generate(prompt)
    else:
        result = _groq_generate(prompt)
    result["options"] = _normalize_options(result.get("options", []))
    if "option_explanations" not in result or not isinstance(result["option_explanations"], dict):
        result["option_explanations"] = {}
    result["question_type"] = "fill"
    return result


def _anthropic_generate(prompt: str) -> dict:
    import anthropic as _anthropic
    client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=_model(),
        max_tokens=1536,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(msg.content[0].text)


def _google_generate(prompt: str) -> dict:
    import google.generativeai as genai
    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(_model())
    response = model.generate_content(prompt)
    return _parse_json(response.text)


def _groq_generate(prompt: str) -> dict:
    from groq import Groq
    client = Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1536,
    )
    return _parse_json(resp.choices[0].message.content or "")
