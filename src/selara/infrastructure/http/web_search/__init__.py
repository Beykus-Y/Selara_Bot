"""Web search infrastructure: provider-backed search and page reading."""
from selara.infrastructure.http.web_search.client import WebSearchClient, build_web_search_client
from selara.infrastructure.http.web_search.models import (
    PageContent,
    SearchResultItem,
    WebSearchError,
)
from selara.infrastructure.http.web_search.providers import DuckDuckGoProvider, SearchProvider, SearxngProvider

__all__ = [
    "DuckDuckGoProvider",
    "PageContent",
    "SearchProvider",
    "SearchResultItem",
    "SearxngProvider",
    "WebSearchClient",
    "WebSearchError",
    "build_web_search_client",
]
