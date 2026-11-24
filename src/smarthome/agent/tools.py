"""Small local tools used by graph nodes. No network, no API key."""

import re

# Intent -> trigger phrases (lowercase). Multi-word phrases are matched on word boundaries.
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "greeting": ("xin chào", "chào", "hello", "hi", "hey"),
    "thanks": ("cảm ơn", "cám ơn", "thanks", "thank you"),
    "help": ("giúp", "trợ giúp", "help"),
}

UNKNOWN_INTENT = "unknown"

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def detect_intent(text: str) -> str:
    """Return the first matching intent for ``text`` or ``"unknown"``."""
    padded = f" {' '.join(tokenize(text))} "
    for intent, phrases in INTENT_KEYWORDS.items():
        if any(f" {phrase} " in padded for phrase in phrases):
            return intent
    return UNKNOWN_INTENT
