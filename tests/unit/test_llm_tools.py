import json
from unittest.mock import AsyncMock, MagicMock
import pytest

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.llm.tools import ToolCall, execute_tool


@pytest.fixture
def chat_snapshot():
    return ChatSnapshot(telegram_chat_id=-100123, chat_type="supergroup", title="Test Chat")


@pytest.fixture
def actor_snapshot():
    return UserSnapshot(
        telegram_user_id=111,
        username="actor",
        first_name="Actor",
        last_name="Last",
        is_bot=False,
    )


@pytest.fixture
def target_user():
    return UserSnapshot(
        telegram_user_id=222,
        username="target_user",
        first_name="Target",
        last_name="User",
        is_bot=False,
        chat_display_name="Target User",
    )


@pytest.fixture
def activity_repo(target_user):
    repo = AsyncMock()
    repo.find_chat_user_by_username = AsyncMock(return_value=target_user)
    repo.grant_rest = AsyncMock()
    repo.revoke_rest = AsyncMock(return_value=MagicMock())
    repo.get_moderation_state = AsyncMock(return_value=None)
    repo.get_active_rest_state = AsyncMock(return_value=None)
    repo.get_bot_role = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def llm_repo():
    repo = AsyncMock()
    action_mock = MagicMock()
    action_mock.id = 999
    repo.add_admin_action = AsyncMock(return_value=action_mock)
    return repo


@pytest.mark.asyncio
async def test_get_user_info_success(chat_snapshot, actor_snapshot, activity_repo, llm_repo):
    call = ToolCall(name="get_user_info", arguments={"target": "@target_user"}, call_id="1")
    ctx = {
        "chat_snapshot": chat_snapshot,
        "actor_snapshot": actor_snapshot,
        "activity_repo": activity_repo,
        "llm_repo": llm_repo,
    }

    result = await execute_tool(call, **ctx)
    assert result.success is True
    data = json.loads(result.result_text)
    assert data["user_id"] == 222
    assert data["bot_role"] == "participant"


@pytest.mark.asyncio
async def test_grant_rest_success(chat_snapshot, actor_snapshot, activity_repo, llm_repo):
    call = ToolCall(
        name="grant_rest",
        arguments={"target": "@target_user", "duration_days": 14, "reason": "vacation"},
        call_id="2",
    )
    ctx = {
        "chat_snapshot": chat_snapshot,
        "actor_snapshot": actor_snapshot,
        "activity_repo": activity_repo,
        "llm_repo": llm_repo,
    }

    result = await execute_tool(call, **ctx)
    assert result.success is True
    data = json.loads(result.result_text)
    assert data["ok"] is True
    assert data["duration_days"] == 14
    activity_repo.grant_rest.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_rest_success(chat_snapshot, actor_snapshot, activity_repo, llm_repo):
    # This test is for the new tool which we will implement next.
    call = ToolCall(name="revoke_rest", arguments={"target": "@target_user"}, call_id="3")
    ctx = {
        "chat_snapshot": chat_snapshot,
        "actor_snapshot": actor_snapshot,
        "activity_repo": activity_repo,
        "llm_repo": llm_repo,
    }

    result = await execute_tool(call, **ctx)
    assert result.success is True
    data = json.loads(result.result_text)
    assert data["ok"] is True
    activity_repo.revoke_rest.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_bot_docs_success(chat_snapshot, actor_snapshot, activity_repo, llm_repo, tmp_path, monkeypatch):
    # This test is for list_bot_docs
    call = ToolCall(name="list_bot_docs", arguments={}, call_id="4")
    
    # We mock the folder location where bot_docs are read from
    # E.g., if we read from c:/Selara/docs/bot_docs, we will patch it or use mocked values.
    # Let's create a temporary docs directory to read from
    doc_dir = tmp_path / "bot_docs"
    doc_dir.mkdir()
    (doc_dir / "test_doc.md").write_text("# Test Title\nThis is a test doc.")
    (doc_dir / "another_doc.md").write_text("No header here.")

    # We will patch the path in tools module during execution
    import selara.infrastructure.llm.tools as tools
    monkeypatch.setattr(tools, "_BOT_DOCS_DIR", str(doc_dir))

    ctx = {
        "chat_snapshot": chat_snapshot,
        "actor_snapshot": actor_snapshot,
        "activity_repo": activity_repo,
        "llm_repo": llm_repo,
    }

    result = await execute_tool(call, **ctx)
    assert result.success is True
    data = json.loads(result.result_text)
    assert "docs" in data
    # "test_doc.md" should have title "Test Title"
    docs_dict = {d["filename"]: d["title"] for d in data["docs"]}
    assert docs_dict["test_doc.md"] == "Test Title"
    assert docs_dict["another_doc.md"] == "another_doc.md"


@pytest.mark.asyncio
async def test_read_bot_doc_success(chat_snapshot, actor_snapshot, activity_repo, llm_repo, tmp_path, monkeypatch):
    # This test is for read_bot_doc
    call = ToolCall(name="read_bot_doc", arguments={"doc_name": "test_doc.md"}, call_id="5")
    
    doc_dir = tmp_path / "bot_docs"
    doc_dir.mkdir()
    content = "# Test Title\nThis is a test doc content."
    (doc_dir / "test_doc.md").write_text(content)

    import selara.infrastructure.llm.tools as tools
    monkeypatch.setattr(tools, "_BOT_DOCS_DIR", str(doc_dir))

    ctx = {
        "chat_snapshot": chat_snapshot,
        "actor_snapshot": actor_snapshot,
        "activity_repo": activity_repo,
        "llm_repo": llm_repo,
    }

    result = await execute_tool(call, **ctx)
    assert result.success is True
    data = json.loads(result.result_text)
    assert data["content"] == content


@pytest.mark.asyncio
async def test_read_bot_doc_path_traversal(chat_snapshot, actor_snapshot, activity_repo, llm_repo, tmp_path, monkeypatch):
    call = ToolCall(name="read_bot_doc", arguments={"doc_name": "../secret.txt"}, call_id="6")
    
    doc_dir = tmp_path / "bot_docs"
    doc_dir.mkdir()

    import selara.infrastructure.llm.tools as tools
    monkeypatch.setattr(tools, "_BOT_DOCS_DIR", str(doc_dir))

    ctx = {
        "chat_snapshot": chat_snapshot,
        "actor_snapshot": actor_snapshot,
        "activity_repo": activity_repo,
        "llm_repo": llm_repo,
    }

    result = await execute_tool(call, **ctx)
    assert result.success is False
    data = json.loads(result.result_text)
    assert "error" in data
