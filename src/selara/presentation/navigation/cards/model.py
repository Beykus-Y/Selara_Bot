"""Feature card model. A card adds where a feature works, who may use it and
its limits on top of the CommandSpec it points to; the command syntax and
description stay in command_catalog.py.
"""

from __future__ import annotations

from dataclasses import dataclass

CARD_CONTEXTS: frozenset[str] = frozenset({"private", "group"})
CARD_AUDIENCES: frozenset[str] = frozenset({"all", "members", "admins"})


@dataclass(frozen=True)
class FeatureCard:
    spec_key: str
    contexts: tuple[str, ...]
    audience: str
    limits: tuple[str, ...] = ()
    related_nodes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.contexts:
            raise ValueError(f"{self.spec_key}: at least one context is required")
        unknown_contexts = set(self.contexts) - CARD_CONTEXTS
        if unknown_contexts:
            raise ValueError(f"{self.spec_key}: unknown contexts {sorted(unknown_contexts)}")
        if self.audience not in CARD_AUDIENCES:
            raise ValueError(f"{self.spec_key}: unknown audience {self.audience!r}")
