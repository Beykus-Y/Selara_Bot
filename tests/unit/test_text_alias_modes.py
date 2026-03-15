from selara.domain.entities import ChatTextAlias
from selara.presentation.commands.catalog import match_builtin_command, resolve_builtin_command_key
from selara.presentation.handlers.text_commands import _apply_alias_mode_to_text


def _alias(
    *,
    alias_text: str,
    command_key: str,
    source_trigger: str,
) -> ChatTextAlias:
    return ChatTextAlias(
        id=1,
        chat_id=1,
        command_key=command_key,
        alias_text_norm=alias_text,
        source_trigger_norm=source_trigger,
        created_by_user_id=1,
        updated_at=None,
    )


def test_catalog_resolves_standard_triggers() -> None:
    assert resolve_builtin_command_key("старт") == "start"
    assert resolve_builtin_command_key("нейминг") == "naming"
    assert resolve_builtin_command_key("Кто Я") == "me"
    assert resolve_builtin_command_key("Кто Ты") == "me"
    assert resolve_builtin_command_key("моя статья") == "article"
    assert resolve_builtin_command_key("статья") == "article"
    assert resolve_builtin_command_key("топ") == "top"
    assert resolve_builtin_command_key("актив") == "active"


def test_catalog_matches_builtin_prefix() -> None:
    match = match_builtin_command("топ 15")
    assert match is not None
    assert match.command_key == "top"
    assert match.matched_trigger_norm == "топ"


def test_catalog_matches_profile_lookup_prefix_with_target() -> None:
    match = match_builtin_command("кто ты @alice")
    assert match is not None
    assert match.command_key == "me"
    assert match.matched_trigger_norm == "кто ты"


def test_catalog_matches_game_prefix_with_tail() -> None:
    match = match_builtin_command("игра мафия")
    assert match is not None
    assert match.command_key == "game"
    assert match.matched_trigger_norm == "игра"


def test_catalog_does_not_match_invalid_active_sentence_prefix() -> None:
    assert match_builtin_command("актив вернулся что ли") is None


def test_catalog_does_not_match_invalid_market_sentence_prefix() -> None:
    assert match_builtin_command("рынок сегодня шумный") is None


def test_alias_mode_both_rewrites_prefix_and_tail() -> None:
    aliases = [_alias(alias_text="+ник", command_key="naming", source_trigger="нейминг")]
    rewritten = _apply_alias_mode_to_text(text="+ник Ivan", mode="both", aliases=aliases)
    assert rewritten == "нейминг Ivan"


def test_alias_mode_both_rewrites_profile_alias_with_target_tail() -> None:
    aliases = [_alias(alias_text="ты кто", command_key="me", source_trigger="кто ты")]
    rewritten = _apply_alias_mode_to_text(text="ты кто @alice", mode="both", aliases=aliases)
    assert rewritten == "кто ты @alice"


def test_alias_mode_both_keeps_profile_tail_when_alias_command_key_is_wrong() -> None:
    aliases = [_alias(alias_text="ты кто", command_key="alive", source_trigger="кто ты")]
    rewritten = _apply_alias_mode_to_text(text="ты кто @alice", mode="both", aliases=aliases)
    assert rewritten == "кто ты @alice"


def test_alias_mode_prefers_longest_alias_match() -> None:
    aliases = [
        _alias(alias_text="мой", command_key="me", source_trigger="кто я"),
        _alias(alias_text="мой ник", command_key="naming", source_trigger="нейминг"),
    ]
    rewritten = _apply_alias_mode_to_text(text="мой ник Иван", mode="both", aliases=aliases)
    assert rewritten == "нейминг Иван"


def test_alias_mode_standard_only_ignores_custom_aliases() -> None:
    aliases = [_alias(alias_text="+ник", command_key="naming", source_trigger="нейминг")]
    rewritten = _apply_alias_mode_to_text(text="+ник Иван", mode="standard_only", aliases=aliases)
    assert rewritten == "+ник Иван"


def test_alias_mode_aliases_if_exists_blocks_standard_trigger() -> None:
    aliases = [_alias(alias_text="мой профиль", command_key="me", source_trigger="кто я")]
    rewritten = _apply_alias_mode_to_text(text="кто я", mode="aliases_if_exists", aliases=aliases)
    assert rewritten is None


def test_alias_mode_aliases_if_exists_keeps_other_commands() -> None:
    aliases = [_alias(alias_text="мой профиль", command_key="me", source_trigger="кто я")]
    rewritten = _apply_alias_mode_to_text(text="репутация", mode="aliases_if_exists", aliases=aliases)
    assert rewritten == "репутация"
