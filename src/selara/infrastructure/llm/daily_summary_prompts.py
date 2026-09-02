"""Prompt loading for the daily summary pipeline.

Deliberate departure from the rest of the codebase's convention: every other
feature's prompts are Python string constants in `infrastructure/llm/prompts.py`.
Per Ilya's explicit request, the daily summary pipeline's prompts live as `.md`
files instead, under `infrastructure/llm/prompts/daily_summary/`, so they can be
tuned without touching code. This module is the only place that reads them, using
the same mtime+size cache-invalidation technique as
`presentation/interesting_facts.py`'s `InterestingFactCatalog` (reload only when the
file actually changed on disk).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).with_name("prompts") / "daily_summary"
_MISSING_FILE_SIGNATURE = (-1, -1)


class _CachedPromptFile:
    def __init__(self, filename: str, *, base_dir: Path | None = None) -> None:
        self._path = (base_dir or _PROMPTS_DIR) / filename
        self._signature: tuple[int, int] | None = None
        self._content: str = ""

    def get(self) -> str:
        try:
            stat = self._path.stat()
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            signature = _MISSING_FILE_SIGNATURE

        if signature == self._signature:
            return self._content

        if signature == _MISSING_FILE_SIGNATURE:
            logger.warning("Daily summary prompt file missing: %s", self._path)
            self._signature = signature
            self._content = ""
            return self._content

        try:
            content = self._path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to reload daily summary prompt file %s", self._path)
            self._signature = signature
            return self._content

        self._signature = signature
        self._content = content
        return self._content


_SEGMENTER_FILE = _CachedPromptFile("segmenter.md")
_MERGE_FILE = _CachedPromptFile("merge.md")
_ANALYST_FILE = _CachedPromptFile("analyst.md")
_WRITER_FILE = _CachedPromptFile("writer.md")


STYLE_INSTRUCTIONS: dict[str, str] = {
    "neutral": (
        "Стиль: нейтральный. Пиши спокойно и по делу, без шуток и оценок — просто "
        "изложи, что происходило."
    ),
    "lively": (
        "Стиль: живой. Пиши легко и с интересом, можно немного эмоций и лёгкого юмора, "
        "но не переходи в сарказм и не подкалывай участников."
    ),
    "snarky": (
        "Стиль: с подколами. Можно подшутить над ситуацией и мягко поиронизировать над "
        "происходящим в чате — но не переходи на личности и не будь злым, подколка "
        "должна быть смешной, а не обидной."
    ),
}
_DEFAULT_STYLE = "neutral"


def load_segmenter_prompt() -> str:
    return _SEGMENTER_FILE.get()


def load_merge_prompt() -> str:
    return _MERGE_FILE.get()


def load_analyst_prompt(*, chat_title: str, window_from_ru: str, window_to_ru: str) -> str:
    template = _ANALYST_FILE.get()
    return template.format(
        chat_title=chat_title,
        window_from_ru=window_from_ru,
        window_to_ru=window_to_ru,
    )


def load_writer_prompt(*, style: str) -> str:
    template = _WRITER_FILE.get()
    style_instructions = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS[_DEFAULT_STYLE])
    return template.format(style_instructions=style_instructions)
