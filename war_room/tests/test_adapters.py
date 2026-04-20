"""Tests for all War Room adapters."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from war_room.adapters import (
    PaperclipAdapter,
    MulticaAdapter,
    GitHubAdapter,
    ObsidianAdapter,
    OllamaAdapter,
    TelegramAdapter,
)
from war_room.adapters.base import AdapterHealth


# ===================================================================
# PaperclipAdapter
# ===================================================================

class TestPaperclipAdapter:
    @pytest.fixture
    def mock_adapter(self, tmp_path):
        with patch("war_room.adapters.paperclip.DEFAULT_BASE", "http://test-pc:3100"):
            adapter = PaperclipAdapter(base_url="http://test-pc:3100", mock=True)
            adapter._mock_path = tmp_path / ".paperclip_mock.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_health_mock_mode(self, mock_adapter):
        h = await mock_adapter.health()
        assert h.name == "paperclip"
        assert h.reachable is False
        assert "mock" in h.error.lower()

    @pytest.mark.asyncio
    async def test_list_agents_mock(self, mock_adapter):
        agents = await mock_adapter.list_agents()
        ids = {a["id"] for a in agents}
        assert "Dexter" in ids
        assert "David" in ids

    @pytest.mark.asyncio
    async def test_create_issue_mock(self, mock_adapter):
        issue = await mock_adapter.create_issue(
            title="Test issue",
            description="Desc",
            project="NERVIX",
            assignee="Dexter",
        )
        assert issue is not None
        assert issue["title"] == "Test issue"
        assert issue["status"] == "backlog"
        assert issue["id"].startswith("PC-")

    @pytest.mark.asyncio
    async def test_move_issue_mock(self, mock_adapter):
        created = await mock_adapter.create_issue("T", "D", "P", "Dexter")
        ok = await mock_adapter.move_issue(created["id"], "in-progress")
        assert ok is True
        tasks = await mock_adapter.list_tasks("Dexter")
        assert any(t["status"] == "in-progress" for t in tasks)

    @pytest.mark.asyncio
    async def test_sync_to_war_room_mock(self, mock_adapter):
        await mock_adapter.create_issue("T", "D", "P", "Dexter")
        # Direct workload check
        workload = await mock_adapter.list_tasks("Dexter")
        assert len(workload) >= 1
        # Full sync returns agents + tasks
        items = await mock_adapter.sync_to_war_room()
        agents = [i for i in items if i["type"] == "agent"]
        assert len(agents) >= 4

    @pytest.mark.asyncio
    async def test_sync_from_war_room_mock(self, mock_adapter):
        ok = await mock_adapter.sync_from_war_room([
            {"type": "create_issue", "payload": {
                "title": "From WR", "description": "x", "project": "NERVIX", "assignee": "Dexter"
            }},
        ])
        assert ok is True
        tasks = await mock_adapter.list_tasks()
        assert any(t["title"] == "From WR" for t in tasks)

    @pytest.mark.asyncio
    async def test_persistence(self, mock_adapter):
        await mock_adapter.create_issue("Persist", "D", "P", "Dexter")
        mock_adapter._save_mock()
        # New adapter loading same file
        adapter2 = PaperclipAdapter(base_url="http://test-pc:3100", mock=True)
        adapter2._mock_path = mock_adapter._mock_path
        adapter2._load_mock()
        tasks = await adapter2.list_tasks()
        assert any(t["title"] == "Persist" for t in tasks)

    @pytest.mark.asyncio
    async def test_real_health_unreachable(self):
        with patch("war_room.adapters.paperclip.DEFAULT_BASE", "http://127.0.0.1:59999"):
            adapter = PaperclipAdapter(base_url="http://127.0.0.1:59999")
            h = await adapter.health()
        assert h.reachable is False
        assert h.error is not None

    @pytest.mark.asyncio
    async def test_add_comment_mock(self, mock_adapter):
        ok = await mock_adapter.add_comment("PC-1000", "Looks good")
        assert ok is True


# ===================================================================
# MulticaAdapter
# ===================================================================

class TestMulticaAdapter:
    @pytest.fixture
    def local_adapter(self, tmp_path):
        with patch("war_room.adapters.multica.MULTICA_STATE_DIR", tmp_path):
            adapter = MulticaAdapter(base_url="http://127.0.0.1:59998")
            adapter._state_file = tmp_path / "war_room_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_health_unreachable(self, local_adapter):
        h = await local_adapter.health()
        assert h.name == "multica"
        assert h.reachable is False

    @pytest.mark.asyncio
    async def test_list_agents_local_fallback(self, local_adapter):
        local_adapter._local_agents = [
            {"id": "ma-1", "name": "Alpha", "role": "Research", "status": "idle"},
        ]
        agents = await local_adapter.list_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "Alpha"
        assert agents[0]["platform"] == "multica"

    @pytest.mark.asyncio
    async def test_create_task_local(self, local_adapter):
        task = await local_adapter.create_task(
            title="Local task",
            description="Desc",
            assignee="alpha",
        )
        assert task is not None
        assert task["title"] == "Local task"
        assert task["status"] == "open"
        assert task["id"].startswith("MC-")

    @pytest.mark.asyncio
    async def test_list_tasks_local_filtered(self, local_adapter):
        local_adapter._local_tasks = [
            {"id": "MC-0001", "title": "A", "status": "open", "assignee": "alpha"},
            {"id": "MC-0002", "title": "B", "status": "open", "assignee": "beta"},
        ]
        tasks = await local_adapter.list_tasks("alpha")
        assert len(tasks) == 1
        assert tasks[0]["title"] == "A"

    @pytest.mark.asyncio
    async def test_sync_to_war_room_local(self, local_adapter):
        local_adapter._local_agents = [
            {"id": "a1", "name": "Alpha", "role": "Research", "status": "idle"},
        ]
        local_adapter._local_tasks = [
            {"id": "t1", "title": "Task", "status": "open", "assignee": "a1"},
        ]
        items = await local_adapter.sync_to_war_room()
        assert any(i["type"] == "agent" for i in items)
        assert any(i["type"] == "task" for i in items)

    @pytest.mark.asyncio
    async def test_sync_from_war_room_local(self, local_adapter):
        ok = await local_adapter.sync_from_war_room([
            {"type": "create_task", "payload": {"title": "From WR", "description": "x"}},
        ])
        assert ok is True
        assert any(t["title"] == "From WR" for t in local_adapter._local_tasks)

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        with patch("war_room.adapters.multica.MULTICA_STATE_DIR", tmp_path):
            adapter = MulticaAdapter(base_url="http://127.0.0.1:59998")
            adapter._state_file = tmp_path / "war_room_sync.json"
            await adapter.create_task("Persist", "D")
            adapter._save_local_state()

            adapter2 = MulticaAdapter(base_url="http://127.0.0.1:59998")
            adapter2._state_file = tmp_path / "war_room_sync.json"
            adapter2._load_local_state()
            assert any(t["title"] == "Persist" for t in adapter2._local_tasks)

    @pytest.mark.asyncio
    async def test_create_issue_local(self, local_adapter):
        issue = await local_adapter.create_issue(
            title="Issue title",
            description="Issue desc",
            status="backlog",
            priority="high",
        )
        assert issue["status"] == "backlog"
        assert issue["priority"] == "high"

    @pytest.mark.asyncio
    async def test_update_issue_local(self, local_adapter):
        local_adapter._local_issues = [
            {"id": "MC-0001", "title": "A", "status": "open", "assignee": "alpha"},
        ]
        ok = await local_adapter.update_issue("MC-0001", {"status": "done"})
        assert ok is True
        assert local_adapter._local_issues[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_add_comment_local(self, local_adapter):
        ok = await local_adapter.add_comment("MC-0001", "LGTM")
        assert ok is True

    @pytest.mark.asyncio
    async def test_normalize_agent_archived(self, local_adapter):
        raw = {"id": "a1", "name": "Test", "status": "idle", "archived_at": "2024-01-01T00:00:00Z"}
        norm = local_adapter._normalize_agent(raw)
        assert norm["status"] == "archived"

    @pytest.mark.asyncio
    async def test_normalize_task_with_assignee(self, local_adapter):
        raw = {"id": "i1", "title": "T", "status": "backlog", "assignee_type": "agent", "assignee_id": "a1"}
        norm = local_adapter._normalize_task(raw)
        assert norm["assignee"] == "agent:a1"

    @pytest.mark.asyncio
    async def test_sync_from_war_room_move_issue(self, local_adapter):
        local_adapter._local_issues = [
            {"id": "MC-0001", "title": "A", "status": "open", "assignee": "alpha"},
        ]
        ok = await local_adapter.sync_from_war_room([
            {"type": "move_issue", "issue_id": "MC-0001", "status": "done"},
        ])
        assert ok is True
        assert local_adapter._local_issues[0]["status"] == "done"


class TestMulticaAdapterRealMode:
    """Tests for MulticaAdapter interacting with a mocked real server."""

    @pytest.fixture
    def real_adapter(self, tmp_path):
        with patch("war_room.adapters.multica.MULTICA_STATE_DIR", tmp_path):
            adapter = MulticaAdapter(
                base_url="http://mock-multica:8080",
                token="test-token",
                workspace_id="ws-123",
            )
            adapter._state_file = tmp_path / "war_room_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_health_real_ok(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"status": "ok", "version": "0.5.0"}
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            h = await real_adapter.health()
            assert h.reachable is True
            assert h.version == "0.5.0"
            assert h.meta["status"] == "ok"

    @pytest.mark.asyncio
    async def test_list_agents_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = [
                {"id": "ag-1", "name": "ClaudeDev", "status": "active", "runtime_mode": "claude"},
            ]
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            agents = await real_adapter.list_agents()
            assert len(agents) == 1
            assert agents[0]["name"] == "ClaudeDev"
            assert agents[0]["role"] == "claude"
            mock_client.get.assert_awaited_once()
            args, kwargs = mock_client.get.call_args
            assert "/api/agents" in args[0]

    @pytest.mark.asyncio
    async def test_list_tasks_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "issues": [
                    {"id": "iss-1", "title": "Fix bug", "status": "todo", "priority": "high"},
                ],
                "total": 1,
            }
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            tasks = await real_adapter.list_tasks()
            assert len(tasks) == 1
            assert tasks[0]["title"] == "Fix bug"
            assert tasks[0]["priority"] == "high"

    @pytest.mark.asyncio
    async def test_create_issue_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "iss-new", "title": "New issue", "status": "backlog"}
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            created = await real_adapter.create_issue(title="New issue", description="Details")
            assert created["id"] == "iss-new"
            mock_client.post.assert_awaited_once()
            args, kwargs = mock_client.post.call_args
            assert args[0] == "/api/issues"
            assert kwargs["json"]["title"] == "New issue"

    @pytest.mark.asyncio
    async def test_update_issue_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.put = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            ok = await real_adapter.update_issue("iss-1", {"status": "done"})
            assert ok is True
            mock_client.put.assert_awaited_once()
            args, kwargs = mock_client.put.call_args
            assert args[0] == "/api/issues/iss-1"

    @pytest.mark.asyncio
    async def test_add_comment_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            ok = await real_adapter.add_comment("iss-1", "Nice work")
            assert ok is True
            mock_client.post.assert_awaited_once()
            args, kwargs = mock_client.post.call_args
            assert args[0] == "/api/issues/iss-1/comments"
            assert kwargs["json"]["content"] == "Nice work"

    @pytest.mark.asyncio
    async def test_workspace_auto_discovery(self, real_adapter):
        real_adapter.workspace_id = ""
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            ws_resp = MagicMock()
            ws_resp.json.return_value = [{"id": "ws-auto", "name": "Auto Workspace"}]
            ws_resp.raise_for_status = MagicMock()

            agents_resp = MagicMock()
            agents_resp.json.return_value = []
            agents_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.headers = {}
            mock_client.get = AsyncMock(side_effect=[ws_resp, agents_resp])
            real_adapter._client = mock_client

            agents = await real_adapter.list_agents()
            assert real_adapter.workspace_id == "ws-auto"
            assert mock_client.headers.get("X-Workspace-ID") == "ws-auto"

    @pytest.mark.asyncio
    async def test_create_task_alias(self, real_adapter):
        """create_task is an alias for create_issue — ensure it works."""
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "iss-alias", "title": "Alias test"}
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            created = await real_adapter.create_task(title="Alias test")
            assert created["id"] == "iss-alias"


# ===================================================================
# GitHubAdapter
# ===================================================================

class TestGitHubAdapter:
    @pytest.fixture
    def local_adapter(self, tmp_path):
        with patch("war_room.adapters.github.GITHUB_STATE_DIR", tmp_path):
            adapter = GitHubAdapter(repo="", token="", base_url="http://127.0.0.1:59997")
            adapter._state_file = tmp_path / "github_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_health_no_token(self, local_adapter):
        h = await local_adapter.health()
        # api.github.com:443 is reachable, but token is missing
        assert h.name == "github"
        assert "GITHUB_TOKEN not set" in (h.error or "")

    @pytest.mark.asyncio
    async def test_list_agents_local_fallback(self, local_adapter):
        local_adapter._local_agents = [
            {"login": "alice", "type": "User"},
        ]
        agents = await local_adapter.list_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "alice"
        assert agents[0]["platform"] == "github"

    @pytest.mark.asyncio
    async def test_create_issue_local(self, local_adapter):
        issue = await local_adapter.create_issue(
            title="Local GH issue",
            body="Details",
            labels=["bug"],
        )
        assert issue is not None
        assert issue["title"] == "Local GH issue"
        assert issue["state"] == "open"
        assert issue["id"].startswith("GH-")

    @pytest.mark.asyncio
    async def test_list_tasks_local_filtered(self, local_adapter):
        local_adapter._local_issues = [
            {"id": "GH-0001", "number": 1, "title": "A", "state": "open", "assignee": "alice"},
            {"id": "GH-0002", "number": 2, "title": "B", "state": "open", "assignee": "bob"},
        ]
        tasks = await local_adapter.list_tasks("alice")
        assert len(tasks) == 1
        assert tasks[0]["title"] == "A"

    @pytest.mark.asyncio
    async def test_update_issue_local(self, local_adapter):
        local_adapter._local_issues = [
            {"id": "GH-0001", "number": 1, "title": "A", "state": "open"},
        ]
        ok = await local_adapter.update_issue(1, {"state": "closed"})
        assert ok is True
        assert local_adapter._local_issues[0]["state"] == "closed"

    @pytest.mark.asyncio
    async def test_add_comment_local(self, local_adapter):
        ok = await local_adapter.add_comment(1, "LGTM")
        assert ok is True

    @pytest.mark.asyncio
    async def test_sync_from_war_room_create(self, local_adapter):
        ok = await local_adapter.sync_from_war_room([
            {"type": "create_issue", "payload": {"title": "From WR", "body": "x"}},
        ])
        assert ok is True
        assert any(i["title"] == "From WR" for i in local_adapter._local_issues)

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        with patch("war_room.adapters.github.GITHUB_STATE_DIR", tmp_path):
            adapter = GitHubAdapter(repo="", token="", base_url="http://127.0.0.1:59997")
            adapter._state_file = tmp_path / "github_sync.json"
            await adapter.create_issue("Persist", "D")
            adapter._save_local_state()

            adapter2 = GitHubAdapter(repo="", token="", base_url="http://127.0.0.1:59997")
            adapter2._state_file = tmp_path / "github_sync.json"
            adapter2._load_local_state()
            assert any(i["title"] == "Persist" for i in adapter2._local_issues)

    @pytest.mark.asyncio
    async def test_normalize_task_with_dict_labels(self, local_adapter):
        raw = {
            "id": 123,
            "number": 42,
            "title": "T",
            "state": "open",
            "labels": [{"name": "bug"}, {"name": "urgent"}],
            "assignee": {"login": "alice"},
        }
        norm = local_adapter._normalize_task(raw)
        assert norm["labels"] == ["bug", "urgent"]
        assert norm["assignee"] == "alice"


class TestGitHubAdapterRealMode:
    @pytest.fixture
    def real_adapter(self, tmp_path):
        with patch("war_room.adapters.github.GITHUB_STATE_DIR", tmp_path):
            adapter = GitHubAdapter(
                repo="test-owner/test-repo",
                token="ghp-test",
                base_url="http://mock-github",
            )
            adapter._state_file = tmp_path / "github_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_list_agents_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = [
                {"login": "alice", "type": "User", "avatar_url": "https://example.com/a.png"},
            ]
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            agents = await real_adapter.list_agents()
            assert len(agents) == 1
            assert agents[0]["name"] == "alice"
            assert agents[0]["avatar_url"] == "https://example.com/a.png"

    @pytest.mark.asyncio
    async def test_list_tasks_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = [
                {"id": 1, "number": 42, "title": "Fix bug", "state": "open", "labels": [], "assignee": None},
                {"id": 2, "number": 43, "title": "PR title", "state": "open", "labels": [], "assignee": None, "pull_request": {}},
            ]
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            tasks = await real_adapter.list_tasks()
            # PR should be filtered out
            assert len(tasks) == 1
            assert tasks[0]["title"] == "Fix bug"

    @pytest.mark.asyncio
    async def test_create_issue_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": 99, "number": 5, "title": "New"}
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            created = await real_adapter.create_issue(title="New", body="Details", labels=["bug"])
            assert created["number"] == 5
            mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_issue_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            ok = await real_adapter.update_issue(42, {"state": "closed"})
            assert ok is True
            args, kwargs = mock_client.patch.call_args
            assert "issues/42" in args[0]

    @pytest.mark.asyncio
    async def test_add_comment_real(self, real_adapter):
        with patch.object(real_adapter, "_is_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._client = mock_client

            ok = await real_adapter.add_comment(42, "Nice")
            assert ok is True
            args, kwargs = mock_client.post.call_args
            assert "issues/42/comments" in args[0]
            assert kwargs["json"]["body"] == "Nice"


# ===================================================================
# ObsidianAdapter
# ===================================================================

class TestObsidianAdapter:
    @pytest.fixture
    def mirror_adapter(self, tmp_path):
        adapter = ObsidianAdapter(vault_path=tmp_path / "nonexistent_vault")
        adapter._mirror = tmp_path / "mirror"
        yield adapter

    @pytest.mark.asyncio
    async def test_health_offline(self, mirror_adapter):
        h = await mirror_adapter.health()
        assert h.reachable is False
        assert "mirror" in (h.meta or {}).get("mode", "")

    @pytest.mark.asyncio
    async def test_list_agents(self, mirror_adapter):
        agents = await mirror_adapter.list_agents()
        assert len(agents) == 1
        assert agents[0]["id"] == "obsidian"
        assert agents[0]["platform"] == "obsidian"

    @pytest.mark.asyncio
    async def test_create_and_list_note(self, mirror_adapter):
        note = await mirror_adapter.create_note(
            title="Test Note",
            content="Hello world",
            tags=["task", "war-room"],
            frontmatter={"status": "todo"},
        )
        assert note is not None
        assert note["title"] == "Test Note"

        notes = await mirror_adapter.list_notes()
        assert len(notes) == 1
        assert notes[0]["title"] == "Test Note"

    @pytest.mark.asyncio
    async def test_list_tasks(self, mirror_adapter):
        await mirror_adapter.create_note(
            title="Task A",
            content="Do something",
            tags=["task"],
            frontmatter={"status": "todo", "assignee": "dexter"},
        )
        await mirror_adapter.create_note(
            title="Not a task",
            content="Just a note",
            tags=["reference"],
        )
        tasks = await mirror_adapter.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Task A"

    @pytest.mark.asyncio
    async def test_update_note(self, mirror_adapter):
        await mirror_adapter.create_note(title="Updatable", content="v1")
        ok = await mirror_adapter.update_note(title="Updatable", content="v2", frontmatter={"status": "done"})
        assert ok is True
        notes = await mirror_adapter.list_notes()
        assert "v2" in notes[0]["body"]
        assert notes[0]["frontmatter"]["status"] == "done"

    @pytest.mark.asyncio
    async def test_sync_from_war_room(self, mirror_adapter):
        ok = await mirror_adapter.sync_from_war_room([
            {"type": "create_note", "payload": {"title": "From WR", "content": "x", "tags": ["task"]}},
        ])
        assert ok is True
        notes = await mirror_adapter.list_notes()
        assert any(n["title"] == "From WR" for n in notes)

    @pytest.mark.asyncio
    async def test_sync_to_war_room(self, mirror_adapter):
        await mirror_adapter.create_note(
            title="Synced",
            content="Body",
            tags=["task"],
            frontmatter={"status": "in_progress"},
        )
        items = await mirror_adapter.sync_to_war_room()
        assert any(i["type"] == "agent" for i in items)
        assert any(i["type"] == "task" for i in items)


# ===================================================================
# OllamaAdapter (Multi-source models)
# ===================================================================

class TestOllamaAdapter:
    @pytest.fixture
    def offline_adapter(self, tmp_path):
        with patch("war_room.adapters.ollama.MODELS_STATE_DIR", tmp_path):
            adapter = OllamaAdapter(ollama_url="http://127.0.0.1:59996")
            adapter._state_file = tmp_path / "models_sync.json"
            # Ensure OpenRouter is unreachable so we get fallback models
            adapter._openrouter_reachable = AsyncMock(return_value=False)
            yield adapter

    @pytest.mark.asyncio
    async def test_health_offline(self, offline_adapter):
        h = await offline_adapter.health()
        # Ollama is unreachable; OpenRouter mocked as unreachable
        assert h.reachable is False
        assert "No model source reachable" in (h.error or "")

    @pytest.mark.asyncio
    async def test_list_agents_fallback(self, offline_adapter):
        agents = await offline_adapter.list_agents()
        names = {a["name"] for a in agents}
        assert "Llama 3.1" in names
        assert "Claude 3.5 Sonnet" in names  # from fallback
        assert all(a["platform"] == "ollama" for a in agents)

    @pytest.mark.asyncio
    async def test_list_free_models_offline(self, offline_adapter):
        free = await offline_adapter.list_free_models()
        # All fallback models are marked free
        assert len(free) >= 4

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, offline_adapter):
        tasks = await offline_adapter.list_tasks()
        assert tasks == []

    @pytest.mark.asyncio
    async def test_generate_offline(self, offline_adapter):
        result = await offline_adapter.generate(model="llama3.1", prompt="Hello")
        assert result is not None
        assert "offline" in result.lower()

    @pytest.mark.asyncio
    async def test_sync_to_war_room(self, offline_adapter):
        items = await offline_adapter.sync_to_war_room()
        assert all(i["type"] == "agent" for i in items)
        assert len(items) >= 4  # fallback models

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        with patch("war_room.adapters.ollama.MODELS_STATE_DIR", tmp_path):
            adapter = OllamaAdapter(ollama_url="http://127.0.0.1:59996")
            adapter._state_file = tmp_path / "models_sync.json"
            adapter._cached_models = [{"name": "custom", "source": "ollama", "details": {}}]
            adapter._save_local_state()

            adapter2 = OllamaAdapter(ollama_url="http://127.0.0.1:59996")
            adapter2._state_file = tmp_path / "models_sync.json"
            adapter2._load_local_state()
            assert any(m["name"] == "custom" for m in adapter2._cached_models)

    @pytest.mark.asyncio
    async def test_generate_openrouter_no_key(self, offline_adapter):
        result = await offline_adapter.generate(
            model="openrouter/anthropic/claude-3.5-sonnet", prompt="Hello"
        )
        assert "OpenRouter key missing" in result


class TestOllamaAdapterRealMode:
    @pytest.fixture
    def real_adapter(self, tmp_path):
        with patch("war_room.adapters.ollama.MODELS_STATE_DIR", tmp_path):
            adapter = OllamaAdapter(
                ollama_url="http://mock-ollama:11434",
                openrouter_key="sk-or-test",
                openrouter_url="http://mock-openrouter",
            )
            adapter._state_file = tmp_path / "models_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_list_agents_ollama_real(self, real_adapter):
        with patch.object(real_adapter, "_ollama_reachable", return_value=True):
            ollama_resp = MagicMock()
            ollama_resp.json.return_value = {
                "models": [
                    {"name": "llama3.1:latest", "details": {"parameter_size": "8B", "quantization_level": "Q4_0"}},
                ]
            }
            ollama_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=ollama_resp)
            real_adapter._ollama_client = mock_client

            # Make OpenRouter unreachable so only Ollama models come back
            with patch.object(real_adapter, "_openrouter_reachable", return_value=False):
                agents = await real_adapter.list_agents()
                assert len(agents) == 1
                assert agents[0]["name"] == "llama3.1"
                assert agents[0]["source"] == "ollama"
                assert agents[0]["parameter_size"] == "8B"

    @pytest.mark.asyncio
    async def test_list_agents_openrouter_real(self, real_adapter):
        with patch.object(real_adapter, "_ollama_reachable", return_value=False):
            or_resp = MagicMock()
            or_resp.json.return_value = {
                "data": [
                    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "google/gemini-pro", "name": "Gemini Pro", "pricing": {"prompt": "0.001", "completion": "0.002"}},
                ]
            }
            or_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=or_resp)
            real_adapter._openrouter_client = mock_client

            with patch.object(real_adapter, "_openrouter_reachable", return_value=True):
                agents = await real_adapter.list_agents()
                assert len(agents) == 2
                assert agents[0]["source"] == "openrouter"
                assert agents[0]["free"] is True
                assert agents[1]["free"] is False

    @pytest.mark.asyncio
    async def test_list_free_models_openrouter(self, real_adapter):
        with patch.object(real_adapter, "_ollama_reachable", return_value=False):
            or_resp = MagicMock()
            or_resp.json.return_value = {
                "data": [
                    {"id": "free-model", "name": "Free", "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "paid-model", "name": "Paid", "pricing": {"prompt": "0.001", "completion": "0.002"}},
                ]
            }
            or_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=or_resp)
            real_adapter._openrouter_client = mock_client

            with patch.object(real_adapter, "_openrouter_reachable", return_value=True):
                free = await real_adapter.list_free_models()
                assert len(free) == 1
                assert free[0]["id"] == "free-model"

    @pytest.mark.asyncio
    async def test_generate_ollama_real(self, real_adapter):
        with patch.object(real_adapter, "_ollama_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"response": "Hello back"}
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._ollama_client = mock_client

            result = await real_adapter.generate(model="llama3.1", prompt="Hello", system="Be nice")
            assert result == "Hello back"
            args, kwargs = mock_client.post.call_args
            assert args[0] == "/api/generate"
            assert kwargs["json"]["model"] == "llama3.1"
            assert kwargs["json"]["system"] == "Be nice"

    @pytest.mark.asyncio
    async def test_generate_openrouter_real(self, real_adapter):
        or_resp = MagicMock()
        or_resp.json.return_value = {
            "choices": [{"message": {"content": "Cloud response"}}]
        }
        or_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=or_resp)
        real_adapter._openrouter_client = mock_client

        # Mark model as openrouter in cache
        real_adapter._cached_models = [
            {"id": "anthropic/claude-3.5-sonnet", "source": "openrouter"}
        ]

        result = await real_adapter.generate(
            model="anthropic/claude-3.5-sonnet", prompt="Hello", system="Be nice"
        )
        assert result == "Cloud response"
        args, kwargs = mock_client.post.call_args
        assert args[0] == "/chat/completions"
        assert kwargs["json"]["model"] == "anthropic/claude-3.5-sonnet"
        assert kwargs["json"]["messages"][0]["role"] == "system"
        assert kwargs["json"]["messages"][1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_pull_model_real(self, real_adapter):
        with patch.object(real_adapter, "_ollama_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            real_adapter._ollama_client = mock_client

            ok = await real_adapter.pull_model("mistral")
            assert ok is True
            args, kwargs = mock_client.post.call_args
            assert kwargs["json"]["name"] == "mistral"

    @pytest.mark.asyncio
    async def test_pull_openrouter_model_noop(self, real_adapter):
        # OpenRouter models are cloud-hosted; pull should be a no-op
        ok = await real_adapter.pull_model("openrouter/anthropic/claude-3.5-sonnet")
        assert ok is True

    @pytest.mark.asyncio
    async def test_delete_model_real(self, real_adapter):
        with patch.object(real_adapter, "_ollama_reachable", return_value=True):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.delete = AsyncMock(return_value=mock_resp)
            real_adapter._ollama_client = mock_client

            ok = await real_adapter.delete_model("old-model")
            assert ok is True
            args, kwargs = mock_client.delete.call_args
            assert kwargs["json"]["name"] == "old-model"

    @pytest.mark.asyncio
    async def test_delete_openrouter_model_fails(self, real_adapter):
        ok = await real_adapter.delete_model("openrouter/anthropic/claude-3.5-sonnet")
        assert ok is False


# ===================================================================
# TelegramAdapter
# ===================================================================

class TestTelegramAdapter:
    @pytest.fixture
    def local_adapter(self, tmp_path):
        with patch("war_room.adapters.telegram.TELEGRAM_STATE_DIR", tmp_path):
            adapter = TelegramAdapter(token="")
            adapter._state_file = tmp_path / "telegram_sync.json"
            yield adapter

    @pytest.fixture
    def bot_adapter(self, tmp_path):
        with patch("war_room.adapters.telegram.TELEGRAM_STATE_DIR", tmp_path):
            adapter = TelegramAdapter(token="test-bot-token")
            adapter._state_file = tmp_path / "telegram_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_health_no_token(self, local_adapter):
        h = await local_adapter.health()
        assert h.reachable is False
        assert "TELEGRAM_BOT_TOKEN not set" in h.error

    @pytest.mark.asyncio
    async def test_list_agents_offline(self, local_adapter):
        agents = await local_adapter.list_agents()
        assert len(agents) == 1
        assert agents[0]["id"] == "telegram_bot"
        assert agents[0]["platform"] == "telegram"

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, local_adapter):
        tasks = await local_adapter.list_tasks()
        assert tasks == []

    @pytest.mark.asyncio
    async def test_process_command_run(self, bot_adapter):
        cmd, args = bot_adapter.process_command("/run Research AI agents")
        assert cmd == "/run"
        assert args == "Research AI agents"

    @pytest.mark.asyncio
    async def test_process_command_help(self, bot_adapter):
        cmd, args = bot_adapter.process_command("/help")
        assert cmd == "/help"
        assert args == ""

    @pytest.mark.asyncio
    async def test_process_command_plain_text(self, bot_adapter):
        cmd, args = bot_adapter.process_command("Just a task")
        assert cmd == ""
        assert args == "Just a task"

    @pytest.mark.asyncio
    async def test_handle_update_message(self, bot_adapter):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 12345},
                "text": "/run Test task",
            },
        }
        result = await bot_adapter.handle_update(update)
        assert result is not None
        assert result["chat_id"] == 12345
        assert result["command"] == "/run"
        assert result["args"] == "Test task"

    @pytest.mark.asyncio
    async def test_handle_update_edited_message(self, bot_adapter):
        update = {
            "update_id": 2,
            "edited_message": {
                "chat": {"id": 99999},
                "text": "/status",
            },
        }
        result = await bot_adapter.handle_update(update)
        assert result is not None
        assert result["chat_id"] == 99999
        assert result["command"] == "/status"

    @pytest.mark.asyncio
    async def test_handle_update_no_text(self, bot_adapter):
        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 12345},
            },
        }
        result = await bot_adapter.handle_update(update)
        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_local(self, local_adapter):
        result = await local_adapter.send_message(12345, "Hello")
        assert result == {"queued": True}
        assert len(local_adapter._outbox) == 1

    @pytest.mark.asyncio
    async def test_send_link_local(self, local_adapter):
        result = await local_adapter.send_link(12345, "http://example.com", "Click me")
        assert result == {"queued": True}

    @pytest.mark.asyncio
    async def test_sync_from_war_room(self, local_adapter):
        ok = await local_adapter.sync_from_war_room([
            {"type": "send_message", "chat_id": 12345, "text": "Hello"},
        ])
        assert ok is True
        assert len(local_adapter._outbox) == 1

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        with patch("war_room.adapters.telegram.TELEGRAM_STATE_DIR", tmp_path):
            adapter = TelegramAdapter(token="")
            adapter._state_file = tmp_path / "telegram_sync.json"
            await adapter.handle_update({
                "update_id": 1,
                "message": {"chat": {"id": 111}, "text": "/run X"},
            })
            adapter._save_local_state()

            adapter2 = TelegramAdapter(token="")
            adapter2._state_file = tmp_path / "telegram_sync.json"
            adapter2._load_local_state()
            assert len(adapter2._recent_commands) == 1


class TestTelegramAdapterRealMode:
    @pytest.fixture
    def real_adapter(self, tmp_path):
        with patch("war_room.adapters.telegram.TELEGRAM_STATE_DIR", tmp_path):
            adapter = TelegramAdapter(token="real-token")
            adapter._state_file = tmp_path / "telegram_sync.json"
            yield adapter

    @pytest.mark.asyncio
    async def test_health_real(self, real_adapter):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": {"id": 1, "username": "war_room_bot", "first_name": "War Room"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        real_adapter._client = mock_client

        h = await real_adapter.health()
        assert h.reachable is True
        assert h.version == "war_room_bot"
        assert h.meta["bot_name"] == "War Room"

    @pytest.mark.asyncio
    async def test_send_message_real(self, real_adapter):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 42}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        real_adapter._client = mock_client

        result = await real_adapter.send_message(12345, "Hello world")
        assert result["message_id"] == 42
        args, kwargs = mock_client.post.call_args
        assert args[0] == "/sendMessage"
        assert kwargs["json"]["chat_id"] == 12345
        assert kwargs["json"]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_get_updates_real(self, real_adapter):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {"update_id": 1, "message": {"chat": {"id": 123}, "text": "hi"}},
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        real_adapter._client = mock_client

        updates = await real_adapter.get_updates()
        assert len(updates) == 1
        assert updates[0]["update_id"] == 1

    @pytest.mark.asyncio
    async def test_set_webhook_real(self, real_adapter):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        real_adapter._client = mock_client

        ok = await real_adapter.set_webhook("https://example.com/webhook", secret_token="secret")
        assert ok is True
        args, kwargs = mock_client.post.call_args
        assert kwargs["json"]["url"] == "https://example.com/webhook"
        assert kwargs["json"]["secret_token"] == "secret"

    @pytest.mark.asyncio
    async def test_delete_webhook_real(self, real_adapter):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        real_adapter._client = mock_client

        ok = await real_adapter.delete_webhook()
        assert ok is True
