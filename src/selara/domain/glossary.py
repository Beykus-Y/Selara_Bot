"""Deterministic retrieval shared by the admin agent and daily summaries."""
from __future__ import annotations

import re
import json
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

MAX_GLOSSARY_TERMS = 200
MAX_DEFINITION_LENGTH = 2000
MAX_TERM_LENGTH = 256
MAX_ALIASES = 20


def normalize_glossary_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().replace("ё", "е").split())


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize_glossary_text(text)))


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class GlossaryMatch:
    entry: GlossaryEntry
    score: float
    match_type: str


def rank_glossary(entries: list[GlossaryEntry], query: str, *, limit: int = 5) -> list[GlossaryMatch]:
    normalized = normalize_glossary_text(query).strip("?!.,:;«»\"'")
    query_words = _words(normalized)
    if not query_words:
        return []
    matches = []
    for entry in entries:
        best_score, best_type = 0.0, ""
        for index, name in enumerate((entry.term, *entry.aliases)):
            name = normalize_glossary_text(name)
            name_words = _words(name)
            if name == normalized:
                score, kind = (1.0, "exact") if index == 0 else (0.99, "alias")
            elif name_words and name_words <= query_words:
                score, kind = 0.93, "term_in_query"
            elif query_words <= name_words:
                score, kind = 0.88, "term_words"
            else:
                # Word matching never treats 'рест' as a substring of
                # 'арест'. Fuzzy suggestions are not confirmed meanings.
                ratio = SequenceMatcher(None, normalized, name).ratio()
                if len(name) >= 4 and 4 <= len(normalized) <= MAX_TERM_LENGTH and ratio >= 0.74:
                    score, kind = 0.65 + ratio * 0.1, "fuzzy"
                else:
                    continue
            if score > best_score:
                best_score, best_type = score, kind
        overlap = query_words & _words(entry.definition)
        if overlap:
            score = 0.45 + 0.15 * len(overlap) / len(query_words)
            if score > best_score:
                best_score, best_type = score, "definition"
        if best_score:
            matches.append(GlossaryMatch(entry, round(best_score, 3), best_type))
    return sorted(matches, key=lambda match: (-match.score, normalize_glossary_text(match.entry.term)))[:max(1, min(limit, 50))]


def select_glossary_context(
    entries: list[GlossaryEntry], query: str, *, limit: int = 8, max_chars: int = 6000,
) -> list[GlossaryMatch]:
    selected = []
    used = 0
    for match in rank_glossary(entries, query, limit=limit):
        size = len(json.dumps({"term": match.entry.term, "definition": match.entry.definition,
                               "aliases": match.entry.aliases, "match_type": match.match_type}, ensure_ascii=False)) + 2
        if used + size <= max_chars:
            selected.append(match)
            used += size
    return selected
