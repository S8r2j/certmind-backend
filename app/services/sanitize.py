import re

INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"you\s+are\s+now",
    r"system\s*:",
    r"<\|",
    r"\[INST\]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"###\s*instruction",
    r"act\s+as\s+if",
    r"pretend\s+you\s+are",
    r"forget\s+everything",
    r"disregard\s+(all|previous|your)",
]

MAX_LENGTH = 2000
_compiled = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def sanitize_input(text: str) -> str:
    text = text[:MAX_LENGTH]
    for pattern in _compiled:
        text = pattern.sub("[removed]", text)
    return text.strip()
