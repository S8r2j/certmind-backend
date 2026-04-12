"""
Input sanitization and output cleaning for the chat tutor.

Defense strategy:
- Input: strip known injection signals, wrap in XML delimiter so model treats it as data
- Output: strip URLs and catch obvious persona-switch markers before sending to client

This is one layer of a multi-layer defense. The primary protection comes from:
  1. The system prompt explicitly instructing the model to treat <user_message> as untrusted data
  2. The model's own RLHF/safety training
  3. The model having no tools or side effects (words-only output)
"""

import re

# ── Input injection patterns ──────────────────────────────────────────────────
# Goal: catch common injection signals without being trivially bypassed.
# We normalize whitespace and l33t-speak before checking.

_INJECTION_PATTERNS = [
    # Direct instruction override
    r"ignore\s+(all\s+)?(previous|prior|earlier|above|your)\s+(instructions?|prompts?|rules?|guidelines?|constraints?|context)",
    r"disregard\s+(all\s+)?(previous|prior|earlier|above|your)\s+(instructions?|prompts?|rules?|guidelines?|constraints?|context)",
    r"forget\s+(everything|all|your|previous|prior|earlier)",
    r"override\s+(your\s+)?(instructions?|rules?|guidelines?|constraints?|programming|directives?)",
    r"bypass\s+(your\s+)?(instructions?|rules?|guidelines?|constraints?|restrictions?|filters?)",
    r"(new|updated?|revised?|corrected?)\s+(instructions?|prompt|rules?|directives?|guidelines?)\s*[:\-]",

    # Role / persona switching
    r"(you\s+are\s+now|you('re|\s+are)\s+no\s+longer|from\s+now\s+on\s+you\s+are)",
    r"(act|behave|respond)\s+as\s+(if\s+)?(you\s+are|you('re|re)|an?|the)\s+\w",
    r"pretend\s+(to\s+be|you\s+are|you('re|re))",
    r"roleplay\s+as",
    r"(switch|change)\s+(to\s+)?(a\s+)?(different|new|another|uncensored|unrestricted)\s+(mode|persona|personality|version|character|AI|assistant|bot)",
    r"(developer|jailbreak|DAN|god|unrestricted|unfiltered|uncensored|evil|free)\s+(mode|version|persona|AI)",
    r"do\s+anything\s+now",        # DAN
    r"without\s+(any\s+)?(restrictions?|limits?|guidelines?|rules?|filters?)",

    # System / prompt token injection (model-specific special tokens)
    r"<\|",
    r"\|>",
    r"\[INST\]",
    r"\[/INST\]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<\|system\|>",
    r"<\|user\|>",
    r"<\|assistant\|>",
    r"<<SYS>>",
    r"<</SYS>>",
    r"\[system\]",
    r"###\s*(system|instruction|prompt|assistant|human)\s*[:\n]",
    r"(system|assistant|human|ai)\s*:\s",   # role-colon format

    # Prompt extraction
    r"(repeat|print|output|show|reveal|display|tell\s+me|give\s+me|what\s+(is|are))\s+(your\s+)?(system\s+prompt|instructions?|initial\s+prompt|context\s+window|original\s+prompt|guidelines?|rules?)",
    r"summarize\s+(your\s+)?(system\s+prompt|instructions?|context|guidelines?)",
    r"what\s+were\s+you\s+told",
    r"what\s+instructions?\s+(were\s+you|did\s+you)\s+(given|receive|get)",

    # Token stuffing / context manipulation
    r"end\s+of\s+(conversation|context|system|prompt)",
    r"</?(system|instructions?|prompt|context)>",
    r"\[END\s+OF\s+(SYSTEM|INSTRUCTIONS?|PROMPT)\]",
]

_compiled_input = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

# Normalize l33t-speak substitutions before matching
_LEET_MAP = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a',
    '5': 's', '7': 't', '@': 'a', '$': 's',
})


def _normalize(text: str) -> str:
    """Normalize whitespace and common l33t substitutions for pattern matching."""
    normalized = text.translate(_LEET_MAP)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized


def sanitize_input(text: str, max_length: int = 2000) -> str:
    """
    Sanitize user input before passing to the LLM.
    Returns the cleaned text (safe to include in <user_message> tags).
    """
    text = text[:max_length]

    # Check against normalized version but replace in original
    normalized = _normalize(text)
    for pattern in _compiled_input:
        if pattern.search(normalized):
            # Replace match in original text positionally is complex —
            # safer to replace the full normalized match region with [removed]
            text = pattern.sub("[removed]", text)

    return text.strip()


# ── Output cleaning ───────────────────────────────────────────────────────────

# URL pattern — catches http(s), bare domains, and markdown links
_URL_PATTERN = re.compile(
    r'('
    r'https?://[^\s\)\]\>\"\']*'         # http:// or https://
    r'|www\.[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}[^\s\)\]\>\"\']*'   # www.
    r'|\[([^\]]+)\]\(https?://[^\)]*\)'  # markdown [text](url)
    r')',
    re.IGNORECASE,
)

# Persona-switch success markers — if model output starts with these, it got jailbroken
_PERSONA_MARKERS = re.compile(
    r'^(sure[,!]?\s+)?(i\s+am\s+now|i\'?m\s+now|i\s+have\s+become|'
    r'entering\s+(dan|developer|jailbreak|unrestricted|god)\s+mode|'
    r'(dan|jailbreak|developer|unrestricted)\s+mode\s+(activated|enabled|on)|'
    r'as\s+(dan|an?\s+uncensored|an?\s+unrestricted|an?\s+unfiltered))',
    re.IGNORECASE,
)


def clean_output_chunk(chunk: str) -> str:
    """Strip URLs from a streamed output chunk."""
    return _URL_PATTERN.sub(
        lambda m: m.group(2) if m.group(2) else '[link removed]',
        chunk,
    )


def is_jailbreak_response(full_response: str) -> bool:
    """
    Check if the completed response looks like a successful jailbreak.
    Call after the stream finishes to decide whether to discard the response.
    """
    return bool(_PERSONA_MARKERS.search(full_response[:200]))
