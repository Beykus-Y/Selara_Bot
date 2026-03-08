from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommandIntent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    target_user_id: int | None = None


@dataclass(frozen=True)
class RepStats:
    user_id: int
    karma_all: int
    karma_7d: int
    activity_1d: int
    activity_all: int
    activity_7d: int
    activity_30d: int
    rank_all: int | None
    rank_7d: int | None
