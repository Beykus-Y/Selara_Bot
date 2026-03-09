from __future__ import annotations

import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from selara.core.config import Settings
from selara.domain.entities import AchievementDefinition, AchievementScope

_VALID_SCOPES = {"chat", "global"}
_VALID_RARITIES = {"common", "uncommon", "rare", "epic", "legendary"}


class AchievementCatalogService:
    def __init__(self, definitions: Iterable[AchievementDefinition]) -> None:
        ordered = sorted(definitions, key=lambda item: (item.sort_order, item.id))
        self._definitions = tuple(ordered)
        self._by_id = {item.id: item for item in ordered}

    @classmethod
    def load(cls, path: Path) -> "AchievementCatalogService":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Achievements catalog must be a JSON array.")

        seen_ids: set[str] = set()
        definitions: list[AchievementDefinition] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"Achievement at index {index} must be an object.")

            achievement_id = str(item.get("id", "")).strip()
            if not achievement_id:
                raise ValueError(f"Achievement at index {index} has empty id.")
            if achievement_id in seen_ids:
                raise ValueError(f"Duplicate achievement id: {achievement_id}")
            seen_ids.add(achievement_id)

            scope = str(item.get("scope", "")).strip()
            if scope not in _VALID_SCOPES:
                raise ValueError(f"Achievement {achievement_id} has invalid scope: {scope}")

            rarity = str(item.get("rarity", "")).strip()
            if rarity not in _VALID_RARITIES:
                raise ValueError(f"Achievement {achievement_id} has invalid rarity: {rarity}")

            condition = item.get("condition")
            if not isinstance(condition, dict):
                raise ValueError(f"Achievement {achievement_id} must define condition object.")
            condition_type = str(condition.get("type", "")).strip()
            if not condition_type:
                raise ValueError(f"Achievement {achievement_id} has empty condition.type.")

            tags = item.get("tags") or []
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ValueError(f"Achievement {achievement_id} has invalid tags.")

            definitions.append(
                AchievementDefinition(
                    id=achievement_id,
                    scope=scope,
                    title=str(item.get("title", "")).strip(),
                    description=str(item.get("description", "")).strip(),
                    hidden=bool(item.get("hidden", False)),
                    rarity=rarity,
                    icon=str(item.get("icon", "")).strip(),
                    sort_order=int(item.get("sort_order", 0)),
                    enabled=bool(item.get("enabled", True)),
                    condition_type=condition_type,
                    condition_payload={key: value for key, value in condition.items() if key != "type"},
                    tags=tuple(str(tag).strip() for tag in tags if str(tag).strip()),
                )
            )

        return cls(definitions)

    def get(self, achievement_id: str) -> AchievementDefinition | None:
        return self._by_id.get(achievement_id)

    def list_all(self) -> tuple[AchievementDefinition, ...]:
        return self._definitions

    def list_by_scope(self, scope: AchievementScope, *, enabled_only: bool = False) -> tuple[AchievementDefinition, ...]:
        items = [item for item in self._definitions if item.scope == scope]
        if enabled_only:
            items = [item for item in items if item.enabled]
        return tuple(items)


@lru_cache(maxsize=1)
def get_achievement_catalog(path_str: str) -> AchievementCatalogService:
    return AchievementCatalogService.load(Path(path_str))


def get_achievement_catalog_from_settings(settings: Settings) -> AchievementCatalogService:
    return get_achievement_catalog(str(settings.resolved_achievements_catalog_path))
