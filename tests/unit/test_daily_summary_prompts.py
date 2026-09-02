from __future__ import annotations

from selara.infrastructure.llm.daily_summary_prompts import (
    STYLE_INSTRUCTIONS,
    _CachedPromptFile,
    load_analyst_prompt,
    load_merge_prompt,
    load_segmenter_prompt,
    load_writer_prompt,
)


def test_segmenter_prompt_loads_and_forbids_counting_stats() -> None:
    prompt = load_segmenter_prompt()
    assert prompt.strip()
    assert "НЕ считай" in prompt


def test_segmenter_prompt_requires_the_topics_object_envelope() -> None:
    # production bug: a prompt that only says "return a list" without ever naming
    # the wrapping key reliably gets a bare JSON array back from the model, which
    # fails SegmentTopicCardList validation -- the prompt must spell out the exact
    # envelope, not just describe the per-item fields.
    prompt = load_segmenter_prompt()
    assert '{"topics":' in prompt
    assert "НЕ возвращай голый список" in prompt


def test_merge_prompt_requires_the_themes_object_envelope() -> None:
    prompt = load_merge_prompt()
    assert '{"themes":' in prompt
    assert "НЕ возвращай голый список" in prompt


def test_merge_prompt_loads_and_mentions_source_card_indexes() -> None:
    prompt = load_merge_prompt()
    assert "source_card_indexes" in prompt


def test_analyst_prompt_fills_in_placeholders() -> None:
    prompt = load_analyst_prompt(chat_title="Тестовый чат", window_from_ru="02.09 07:00", window_to_ru="03.09 07:00")
    assert "Тестовый чат" in prompt
    assert "02.09 07:00" in prompt
    assert "03.09 07:00" in prompt
    assert "{chat_title}" not in prompt


def test_writer_prompt_uses_requested_style() -> None:
    prompt = load_writer_prompt(style="snarky")
    assert STYLE_INSTRUCTIONS["snarky"] in prompt
    assert STYLE_INSTRUCTIONS["neutral"] not in prompt


def test_writer_prompt_falls_back_to_neutral_for_unknown_style() -> None:
    prompt = load_writer_prompt(style="does-not-exist")
    assert STYLE_INSTRUCTIONS["neutral"] in prompt


def test_writer_prompt_forbids_markdown_and_verbatim_quotes() -> None:
    prompt = load_writer_prompt(style="neutral")
    assert "markdown" in prompt.lower()


def test_writer_prompt_requires_covering_every_theme_not_just_the_top_ones() -> None:
    # production bug: "бери не более 3-6 тем" (an upper bound only, no floor) let
    # the writer pick just 1-2 favorite themes out of 6 it was actually given,
    # silently dropping the rest of a real, successful analysis.
    prompt = load_writer_prompt(style="neutral")
    assert "не более 3-6" not in prompt
    assert "каждую присланную тему" in prompt.lower()


def test_writer_prompt_specifies_the_key_value_output_format() -> None:
    prompt = load_writer_prompt(style="neutral")
    assert "title:" in prompt
    assert "theme1_title:" in prompt
    assert "theme1_text:" in prompt
    assert "цитируй" in prompt.lower()


def test_cached_prompt_file_reloads_on_change(tmp_path) -> None:
    path = tmp_path / "x.md"
    path.write_text("version one", encoding="utf-8")
    cached = _CachedPromptFile("x.md", base_dir=tmp_path)

    assert cached.get() == "version one"

    path.write_text("version two", encoding="utf-8")
    assert cached.get() == "version two"


def test_cached_prompt_file_missing_returns_empty_string(tmp_path) -> None:
    cached = _CachedPromptFile("missing.md", base_dir=tmp_path)
    assert cached.get() == ""
