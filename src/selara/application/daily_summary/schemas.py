from __future__ import annotations

from pydantic import BaseModel, Field


class SegmentTopicCard(BaseModel):
    """One topic LLM #1 found within a single segment. No stats -- see stats.py."""

    title: str
    start_message_id: int
    end_message_id: int
    participant_display_names: list[str] = Field(default_factory=list)
    blurb: str


class SegmentTopicCardList(BaseModel):
    topics: list[SegmentTopicCard] = Field(default_factory=list)


class MergedTheme(BaseModel):
    """One theme of the day after LLM #2 merges same-subject cards together."""

    title: str
    source_card_indexes: list[int] = Field(default_factory=list)
    blurb: str
    importance: int = Field(ge=1, le=5)


class MergedThemeList(BaseModel):
    themes: list[MergedTheme] = Field(default_factory=list)
