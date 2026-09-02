from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from selara.application.daily_summary.participants import ChatMemberInfo
from selara.application.daily_summary.pipeline import _format_final_post, run_daily_summary_pipeline
from selara.application.daily_summary.schemas import MergedTheme, MergedThemeList, SegmentTopicCard, SegmentTopicCardList
from selara.domain.entities import ActivityWindowStats, ArchivedMessageView
from selara.infrastructure.llm.client import LlmClientError

_WINDOW_FROM = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)
_WINDOW_TO = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)


def _msg(message_id: int, user_id: int, minute: int, text: str, reply_to: int | None = None) -> ArchivedMessageView:
    return ArchivedMessageView(
        telegram_message_id=message_id,
        user_id=user_id,
        sent_at=_WINDOW_FROM + timedelta(minutes=minute),
        text=text,
        transcript=None,
        reply_to_telegram_message_id=reply_to,
    )


@dataclass
class _FakeRepo:
    messages: list[ArchivedMessageView]
    members: list[ChatMemberInfo]

    async def list_archived_messages_in_window(self, *, chat_id, window_from, window_to):
        return self.messages

    async def get_daily_summary_member_info(self, *, chat_id, user_ids):
        return [m for m in self.members if m.user_id in user_ids]

    async def get_activity_stats_in_window(self, *, chat_id, window_from, window_to):
        return ActivityWindowStats(message_count=len(self.messages), participant_count=2, reply_count=1)

    async def get_message_context(self, **kwargs):
        return []

    async def get_reply_thread(self, **kwargs):
        return []

    async def search_messages(self, **kwargs):
        return []


@dataclass
class _FakeLlmClient:
    structured_responses: list
    chat_simple_response: str = "Итоги дня: обсудили сериал и VPN."
    tool_response_content: str | None = None
    last_usage: tuple = (100, 20)
    last_model: str = "gpt-4o-mini"
    _structured_index: int = field(default=0, init=False)
    chat_with_tools_calls: int = field(default=0, init=False)

    async def chat_structured(self, messages, *, response_model, max_tokens=None):
        item = self.structured_responses[self._structured_index]
        self._structured_index += 1
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_with_tools(self, messages, tools, *, max_tokens=None):
        self.chat_with_tools_calls += 1
        content = self.tool_response_content if self.tool_response_content is not None else "[]"
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    async def chat_simple(self, messages, *, max_tokens=None):
        return self.chat_simple_response


def _members() -> list[ChatMemberInfo]:
    return [
        ChatMemberInfo(user_id=1, is_active_member=True, display_name="Вася"),
        ChatMemberInfo(user_id=2, is_active_member=True, display_name="Петя"),
    ]


@pytest.mark.asyncio
async def test_pipeline_empty_window_returns_quiet_day_fallback() -> None:
    repo = _FakeRepo(messages=[], members=[])
    llm_client = _FakeLlmClient(structured_responses=[])

    result = await run_daily_summary_pipeline(
        llm_client=llm_client,
        repo=repo,
        chat_id=-100,
        chat_title="Test",
        summary_run_id=1,
        window_from=_WINDOW_FROM,
        window_to=_WINDOW_TO,
        style="neutral",
        persona_enabled=True,
    )

    assert "тихо" in result.generated_text
    assert result.topics_json == {"themes": []}
    assert result.pipeline_cost_usd == 0.0
    assert result.stage_usages == []


@pytest.mark.asyncio
async def test_pipeline_happy_path_produces_writer_text_and_cost() -> None:
    messages = [
        _msg(1, 1, 0, "Кто смотрел новый сезон?"),
        _msg(2, 2, 1, "Да, вчера досмотрел", reply_to=1),
    ]
    repo = _FakeRepo(messages=messages, members=_members())

    segment_card_list = SegmentTopicCardList(
        topics=[
            SegmentTopicCard(
                title="Сериал",
                start_message_id=1,
                end_message_id=2,
                participant_display_names=["Вася", "Петя"],
                blurb="Обсудили новый сезон сериала.",
            )
        ]
    )
    merged = MergedThemeList(
        themes=[MergedTheme(title="Сериал", source_card_indexes=[0], blurb="Итог по сериалу.", importance=4)]
    )
    llm_client = _FakeLlmClient(structured_responses=[segment_card_list, merged])

    result = await run_daily_summary_pipeline(
        llm_client=llm_client,
        repo=repo,
        chat_id=-100,
        chat_title="Test Chat",
        summary_run_id=42,
        window_from=_WINDOW_FROM,
        window_to=_WINDOW_TO,
        style="lively",
        persona_enabled=True,
        glossary_terms=[("DPI", "система глубокого анализа пакетов")],
    )

    assert result.generated_text.startswith("<b>Итоги дня: обсудили сериал и VPN.</b>")
    assert "бета" in result.generated_text.lower()
    assert "<i>" in result.generated_text  # disclaimer is italicized
    assert result.topics_json["themes"][0]["title"] == "Сериал"
    assert result.topics_json["themes"][0]["episode_count"] == 1
    # segment_topics + merge + analyst (1 tool-loop round, no tool calls) + writer
    stages = [u.stage for u in result.stage_usages]
    assert stages == ["segment_topics", "merge", "analyst", "writer"]
    assert result.pipeline_cost_usd > 0


@pytest.mark.asyncio
async def test_pipeline_falls_back_to_quiet_message_when_all_segments_fail() -> None:
    messages = [_msg(1, 1, 0, "привет")]
    repo = _FakeRepo(messages=messages, members=_members())
    llm_client = _FakeLlmClient(structured_responses=[LlmClientError("boom")])

    result = await run_daily_summary_pipeline(
        llm_client=llm_client,
        repo=repo,
        chat_id=-100,
        chat_title="Test",
        summary_run_id=1,
        window_from=_WINDOW_FROM,
        window_to=_WINDOW_TO,
        style="neutral",
        persona_enabled=True,
    )

    assert "не получилось" in result.generated_text.lower()
    assert result.topics_json == {"themes": []}


@pytest.mark.asyncio
async def test_pipeline_merge_failure_falls_back_to_one_theme_per_card() -> None:
    messages = [_msg(1, 1, 0, "тема раз"), _msg(2, 2, 5, "тема два")]
    repo = _FakeRepo(messages=messages, members=_members())

    segment_cards = SegmentTopicCardList(
        topics=[
            SegmentTopicCard(title="A", start_message_id=1, end_message_id=1, blurb="a"),
            SegmentTopicCard(title="B", start_message_id=2, end_message_id=2, blurb="b"),
        ]
    )
    llm_client = _FakeLlmClient(structured_responses=[segment_cards, LlmClientError("merge down")])

    result = await run_daily_summary_pipeline(
        llm_client=llm_client,
        repo=repo,
        chat_id=-100,
        chat_title="Test",
        summary_run_id=1,
        window_from=_WINDOW_FROM,
        window_to=_WINDOW_TO,
        style="neutral",
        persona_enabled=True,
    )

    titles = {theme["title"] for theme in result.topics_json["themes"]}
    assert titles == {"A", "B"}
    assert result.diagnostics.merge_fallback_used is True
    assert result.diagnostics.segment_failures == 0
    assert result.diagnostics.cards_before_merge_count == 2


@pytest.mark.asyncio
async def test_pipeline_diagnostics_report_full_happy_path_counts() -> None:
    messages = [
        _msg(1, 1, 0, "Кто смотрел новый сезон?"),
        _msg(2, 2, 1, "Да, вчера досмотрел", reply_to=1),
    ]
    repo = _FakeRepo(messages=messages, members=_members())

    segment_card_list = SegmentTopicCardList(
        topics=[
            SegmentTopicCard(
                title="Сериал", start_message_id=1, end_message_id=2,
                participant_display_names=["Вася", "Петя"], blurb="Обсудили новый сезон сериала.",
            )
        ]
    )
    merged = MergedThemeList(
        themes=[MergedTheme(title="Сериал", source_card_indexes=[0], blurb="Итог по сериалу.", importance=4)]
    )
    llm_client = _FakeLlmClient(structured_responses=[segment_card_list, merged])

    result = await run_daily_summary_pipeline(
        llm_client=llm_client, repo=repo, chat_id=-100, chat_title="Test Chat", summary_run_id=42,
        window_from=_WINDOW_FROM, window_to=_WINDOW_TO, style="lively", persona_enabled=True,
    )

    diag = result.diagnostics
    assert diag.message_count == 2
    assert diag.segments_processed == 1
    assert diag.segments_total_before_truncation == 1
    assert diag.cards_before_merge_count == 1
    assert diag.segment_failures == 0
    assert diag.merge_fallback_used is False
    assert diag.themes_after_merge_count == 1
    assert diag.final_themes_count == 1
    assert diag.analyst_tool_rounds == 1
    assert diag.analyst_tool_calls == 0
    # the fake analyst returns "[]" (no tool calls, empty refinement) -> kept as fallback
    assert diag.analyst_fallback_used is True


@pytest.mark.asyncio
async def test_pipeline_diagnostics_counts_segment_failures() -> None:
    messages = [_msg(1, 1, 0, "привет"), _msg(2, 2, 60, "ещё одно сообщение")]
    repo = _FakeRepo(messages=messages, members=_members())

    ok_card = SegmentTopicCardList(topics=[SegmentTopicCard(title="X", start_message_id=2, end_message_id=2, blurb="x")])
    merged = MergedThemeList(themes=[MergedTheme(title="X", source_card_indexes=[0], blurb="x", importance=2)])
    # first segment (msg 1) fails, second segment (msg 2, separated by the 60min gap) succeeds
    llm_client = _FakeLlmClient(structured_responses=[LlmClientError("boom"), ok_card, merged])

    result = await run_daily_summary_pipeline(
        llm_client=llm_client, repo=repo, chat_id=-100, chat_title="Test", summary_run_id=1,
        window_from=_WINDOW_FROM, window_to=_WINDOW_TO, style="neutral", persona_enabled=True,
    )

    assert result.diagnostics.segments_processed == 2
    assert result.diagnostics.segment_failures == 1
    assert result.diagnostics.cards_before_merge_count == 1


def test_format_final_post_bolds_post_title_and_each_theme_title() -> None:
    raw = (
        "title: Итоги дня: чат разрывался между гачей и алгеброй\n"
        "theme1_title: Сяо в ударе\n"
        "theme1_text: Сегодня Сяо много жаловался на учёбу, но его поддержали Альбедо и Панталоне.\n"
        "theme2_title: Гача-страдания\n"
        "theme2_text: Участники делились результатами тапов и разочарованиями."
    )

    result = _format_final_post(raw)

    assert result.startswith("<b>Итоги дня: чат разрывался между гачей и алгеброй</b>\n\n")
    assert "<b>Сяо в ударе</b>\nСегодня Сяо много жаловался" in result
    assert "<b>Гача-страдания</b>\nУчастники делились результатами" in result
    assert "<i>" in result and "бета" in result.lower()


def test_format_final_post_preserves_multiline_theme_text() -> None:
    # a theme's text can itself span several lines/sentences with embedded
    # newlines -- lines that don't match a known key must keep accumulating
    # under the last key seen, not get dropped or mistaken for a new field.
    raw = (
        "title: Итоги дня\n"
        "theme1_title: Длинная тема\n"
        "theme1_text: Первое предложение.\n"
        "Второе предложение той же темы.\n"
        "theme2_title: Вторая тема\n"
        "theme2_text: Просто текст."
    )

    result = _format_final_post(raw)

    assert "Первое предложение.\nВторое предложение той же темы." in result
    assert "<b>Вторая тема</b>\nПросто текст." in result


def test_format_final_post_escapes_html_special_characters_from_the_model() -> None:
    # the writer is told never to emit HTML, but its content is still untrusted --
    # a literal "<" or "&" in the model's own output must not break parse_mode=HTML
    # or be interpreted as a tag.
    raw = (
        "title: Заголовок с <тегом> & амперсандом\n"
        "theme1_title: Тема\n"
        "theme1_text: Обсуждали C++ и A<B."
    )

    result = _format_final_post(raw)

    assert "<тегом>" not in result
    assert "&lt;тегом&gt;" in result
    assert "&amp;" in result
    assert "A&lt;B" in result


def test_format_final_post_falls_back_to_free_text_when_model_ignores_the_format() -> None:
    # if the model doesn't use any recognizable key at all, don't lose the
    # content -- degrade to "first line is the title, rest is one paragraph"
    result = _format_final_post("Просто свободный текст без ключей.\n\nВторая строка.")

    assert result.startswith("<b>Просто свободный текст без ключей.</b>")
    assert "Вторая строка." in result


def test_format_final_post_handles_single_line_output_with_no_body() -> None:
    result = _format_final_post("Просто одна строка без темы.")

    assert result.startswith("<b>Просто одна строка без темы.</b>")
    # no stray empty body paragraph between the title and the disclaimer
    assert "<b>Просто одна строка без темы.</b>\n\n<i>" in result
