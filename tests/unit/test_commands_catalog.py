from selara.presentation.commands.catalog import (
    COMMAND_KEY_DEFAULT_SOURCE_TRIGGER,
    SOCIAL_ACTION_18_PLUS,
    SOCIAL_COMMAND_KEY_TO_ACTION,
    build_social_action_docs,
)


def test_build_social_action_docs_covers_every_social_command_key() -> None:
    docs = build_social_action_docs()
    social_keys = {key for key in COMMAND_KEY_DEFAULT_SOURCE_TRIGGER if key.startswith("social_")}

    assert {doc.command_key for doc in docs} == social_keys
    assert len(docs) == len(social_keys)


def test_build_social_action_docs_18_plus_flag_matches_runtime_gate() -> None:
    for doc in build_social_action_docs():
        action = SOCIAL_COMMAND_KEY_TO_ACTION[doc.command_key]
        assert doc.is_18_plus == (action in SOCIAL_ACTION_18_PLUS)


def test_build_social_action_docs_triggers_are_unique() -> None:
    triggers = [doc.trigger for doc in build_social_action_docs()]
    assert len(triggers) == len(set(triggers))
