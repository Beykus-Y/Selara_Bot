"""Data contracts for the web search client and its providers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResultItem:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class PageContent:
    url: str
    final_url: str
    title: str
    text: str
    truncated: bool
    content_type: str


class WebSearchError(Exception):
    """Operational failure surfaced to the LLM as a corrective tool error."""

    def __init__(self, message: str, *, is_timeout: bool = False, is_operational: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.is_timeout = is_timeout
        self.is_operational = is_operational
