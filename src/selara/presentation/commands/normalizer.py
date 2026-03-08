import re

_WHITESPACE_RE = re.compile(r"\s+")
_SUFFIX_PUNCT_RE = re.compile(r"[?!.,]+$")


def normalize_text_command(value: str) -> str:
    normalized = value.lower().strip()
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = _SUFFIX_PUNCT_RE.sub("", normalized)
    return normalized.strip()
