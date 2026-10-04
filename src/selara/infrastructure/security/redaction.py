"""Redact credentials and session material from admin-visible diagnostics."""

from __future__ import annotations

import re

_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization\s*:\s*basic\s+)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(cookie\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(set-cookie\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(DATABASE_URL\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+@"), r"\1[REDACTED]@"),
    (
        re.compile(
            r"(?i)\b([\w.-]*(?:bot[_-]?token|api[_-]?key|password|secret|session(?:[_-]?(?:token|id|secret))?|(?:access|refresh)[_-]?token|private[_-]?key|jwt))\s*[:=]\s*([^\s,;]+)"
        ),
        r"\1=[REDACTED]",
    ),
)


def redact_sensitive_text(value: str) -> str:
    result = value
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
