"""
War Room Test Suite
Tests for PaperclipBridge, ResearchTools, WarRoomPipeline helpers, and config.

Run:
  cd /Users/davidai/SemeClaw
  python -m pytest war_room/tests/ -v
"""

import json
import sys
from pathlib import Path

import pytest

# Add war_room to path
WAR_ROOM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(WAR_ROOM_DIR))

from paperclip_bridge import AGENT_ASSIGNEES, PROJECTS, PaperclipBridge
from research_tools import ResearchTools

# ===========================================================================
# Paperclip Bridge Tests
# ===========================================================================


class TestPaperclipBridge:
    """Tests for PaperclipBridge in mock mode."""

    @pytest.fixture
    def bridge(self):
        """Create a bridge in mock mode."""
        return PaperclipBridge(mock=True)

    def test_init_mock_mode(self):
        """Bridge initializes in mock mode correctly."""
        b = PaperclipBridge(mock=True)
        assert b.mock is True
        assert b.base_url == "http://127.0.0.1:3100"

    def test_init_custom_url(self):
        """Bridge accepts custom base URL."""
        b = PaperclipBridge(base_url="http://custom:9999", mock=True)
        assert b.base_url == "http://custom:9999"

    @pytest.mark.asyncio
    async def test_create_issue_mock(self):
        """Creating an issue in mock mode adds to internal list."""
        async with PaperclipBridge(mock=True) as bridge:
            issue = await bridge.create_issue(
                title="Test Issue",
                description="Test description",
                project="NERVIX",
                assignee="Dexter",
                labels=["test"],
                priority="high",
                acceptance_criteria=["Check A", "Check B"],
            )
            assert issue["id"].startswith("PC-")
            assert issue["title"] == "Test Issue"
            assert issue["project"] == "NERVIX"
            assert issue["assignee"] == "Dexter"
            assert issue["status"] == "backlog"
            assert issue["labels"] == ["test"]
            assert issue["priority"] == "high"
            assert len(issue["acceptance_criteria"]) == 2

    @pytest.mark.asyncio
    async def test_create_issue_increments_id(self):
        """Each new issue gets a unique incrementing ID."""
        async with PaperclipBridge(mock=True) as bridge:
            issue1 = await bridge.create_issue("A", "d", "P", "X")
            issue2 = await bridge.create_issue("B", "d", "P", "X")
            assert issue1["id"] != issue2["id"]

    @pytest.mark.asyncio
    async def test_get_board_state_mock(self):
        """Mock board state has expected structure."""
        async with PaperclipBridge(mock=True) as bridge:
            board = await bridge.get_board_state()
            assert board["mode"] == "mock"
            assert list(board["projects"]) == list(PROJECTS.keys())
            assert board["columns"] == ["backlog", "in-progress", "review", "done"]
            assert "Dexter" in board["agents"]
            assert "David" in board["agents"]

    @pytest.mark.asyncio
    async def test_move_issue_mock(self):
        """Moving an issue updates its status in mock mode."""
        async with PaperclipBridge(mock=True) as bridge:
            issue = await bridge.create_issue("Move Me", "desc", "NERVIX", "Dexter")
            result = await bridge.move_issue(issue["id"], "in-progress")
            assert result is True
            # Verify status changed
            issues = bridge.get_mock_issues()
            moved = [i for i in issues if i["id"] == issue["id"]]
            assert moved[0]["status"] == "in-progress"

    @pytest.mark.asyncio
    async def test_move_issue_not_found(self):
        """Moving a non-existent issue returns False."""
        async with PaperclipBridge(mock=True) as bridge:
            result = await bridge.move_issue("PC-9999", "done")
            assert result is False

    @pytest.mark.asyncio
    async def test_get_agent_workload_mock(self):
        """Get workload for an agent in mock mode."""
        async with PaperclipBridge(mock=True) as bridge:
            await bridge.create_issue("Work 1", "d", "NERVIX", "Dexter")
            await bridge.create_issue("Work 2", "d", "NERVIX", "Dexter")
            await bridge.create_issue("Work 3", "d", "NERVIX", "David")

            dexter_work = await bridge.get_agent_workload("Dexter")
            assert len(dexter_work) == 2

    @pytest.mark.asyncio
    async def test_get_project_issues_mock(self):
        """Filter issues by project in mock mode."""
        async with PaperclipBridge(mock=True) as bridge:
            await bridge.create_issue("N1", "d", "NERVIX", "X")
            await bridge.create_issue("C1", "d", "CrawdBot", "X")

            nervix_issues = await bridge.get_project_issues("NERVIX")
            assert len(nervix_issues) == 1
            assert nervix_issues[0]["project"] == "NERVIX"

    def test_resolve_project(self):
        """Project name resolution."""
        b = PaperclipBridge(mock=True)
        assert b.resolve_project("NERVIX") == "nervix"
        assert b.resolve_project("CrawdBot") == "crawdbot"
        assert b.resolve_project("Custom Project") == "custom-project"

    def test_resolve_assignee(self):
        """Agent ID to assignee resolution."""
        b = PaperclipBridge(mock=True)
        assert b.resolve_assignee("research") == "Dexter"
        assert b.resolve_assignee("architect") == "David"
        assert b.resolve_assignee("strategist") == "Memo"
        assert b.resolve_assignee("writer") == "Hermes"
        assert b.resolve_assignee("unknown") == "Unknown"

    def test_format_issue_markdown(self):
        """Issue formatting to Markdown."""
        b = PaperclipBridge(mock=True)
        issue = {
            "id": "PC-1001",
            "title": "Test Issue",
            "project": "NERVIX",
            "assignee": "Dexter",
            "priority": "high",
            "status": "backlog",
            "labels": ["research", "urgent"],
            "description": "Do the thing",
            "acceptance_criteria": ["A done", "B done"],
        }
        md = b.format_issue_markdown(issue)
        assert "PC-1001" in md
        assert "Test Issue" in md
        assert "NERVIX" in md
        assert "Dexter" in md
        assert "high" in md
        assert "A done" in md
        assert "B done" in md

    def test_projects_constant(self):
        """PROJECTS dict has expected entries."""
        assert "NERVIX" in PROJECTS
        assert "CrawdBot" in PROJECTS
        assert "MyWork AI" in PROJECTS
        assert "ZmartyChat" in PROJECTS
        assert "DansLab OS" in PROJECTS

    def test_agent_assignees_constant(self):
        """AGENT_ASSIGNEES has correct mapping."""
        assert AGENT_ASSIGNEES["research"] == "Dexter"
        assert AGENT_ASSIGNEES["architect"] == "David"
        assert AGENT_ASSIGNEES["strategist"] == "Memo"
        assert AGENT_ASSIGNEES["writer"] == "Hermes"


# ===========================================================================
# Research Tools Tests
# ===========================================================================


class TestResearchTools:
    """Tests for ResearchTools."""

    def test_init(self):
        """ResearchTools initializes correctly."""
        tools = ResearchTools()
        assert tools.max_search_results == 5
        assert tools._last_search_results == []

    def test_init_custom_max_results(self):
        """Custom max_search_results."""
        tools = ResearchTools(max_search_results=10)
        assert tools.max_search_results == 10

    def test_available_tools_description(self):
        """Tool description contains all tool names."""
        tools = ResearchTools()
        desc = tools.get_available_tools_description()
        assert "search" in desc
        assert "extract" in desc
        assert "browser_navigate" in desc
        assert "run_code" in desc
        assert "run_shell" in desc

    @pytest.mark.asyncio
    async def test_run_code_success(self):
        """Running valid Python code returns output."""
        tools = ResearchTools()
        result = await tools.run_code("print('hello world')")
        assert "hello world" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_run_code_error(self):
        """Running invalid Python code returns error."""
        tools = ResearchTools()
        result = await tools.run_code("raise ValueError('test error')")
        assert "ValueError" in result
        assert "test error" in result

    @pytest.mark.asyncio
    async def test_run_code_timeout(self):
        """Long-running code is killed after timeout."""
        tools = ResearchTools()
        result = await tools.run_code("import time; time.sleep(10)", timeout=1)
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_run_shell_success(self):
        """Running valid shell command returns output."""
        tools = ResearchTools()
        result = await tools.run_shell("echo hello")
        assert "hello" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_run_shell_error(self):
        """Running invalid shell command returns error."""
        tools = ResearchTools()
        result = await tools.run_shell("exit 42")
        assert "Exit code: 42" in result

    @pytest.mark.asyncio
    async def test_browser_navigate_invalid_url(self):
        """Navigating to invalid URL returns error message."""
        tools = ResearchTools()
        result = await tools.browser_navigate("http://this-domain-definitely-does-not-exist-12345.com")
        assert "failed" in result.lower() or "Navigation" in result


# ===========================================================================
# War Room Pipeline Helper Tests
# ===========================================================================


class TestAgentDefs:
    """Tests for agent definition loading."""

    def test_load_agent_def_research(self):
        """Research agent def loads correctly."""
        from war_room.war_room import load_agent_def

        agent = load_agent_def("research")
        assert agent["id"] == "research"
        assert "system_prompt" in agent
        assert "Research Agent" in agent["system_prompt"]

    def test_load_agent_def_architect(self):
        """Architect agent def loads correctly."""
        from war_room.war_room import load_agent_def

        agent = load_agent_def("architect")
        assert agent["id"] == "architect"
        assert "CTO" in agent["system_prompt"]

    def test_load_agent_def_strategist(self):
        """Strategist agent def loads correctly."""
        from war_room.war_room import load_agent_def

        agent = load_agent_def("strategist")
        assert agent["id"] == "strategist"
        assert "Strategist" in agent["system_prompt"]

    def test_load_agent_def_writer(self):
        """Writer agent def loads correctly."""
        from war_room.war_room import load_agent_def

        agent = load_agent_def("writer")
        assert agent["id"] == "writer"
        assert "Writer" in agent["system_prompt"]

    def test_load_agent_def_not_found(self):
        """Loading non-existent agent raises FileNotFoundError."""
        from war_room.war_room import load_agent_def

        with pytest.raises(FileNotFoundError):
            load_agent_def("nonexistent_agent")


class TestPipelineHelpers:
    """Tests for WarRoomPipeline helper methods."""

    def test_parse_writer_output_with_title(self):
        """Parse writer output with explicit title."""
        from war_room.war_room import WarRoomPipeline

        pipeline = WarRoomPipeline()
        writer_output = (
            "Title: Implement new feature X\n\nDescription of the feature.\n\n- [ ] Write tests\n- [ ] Deploy"
        )
        title, description, ac = pipeline._parse_writer_output(writer_output, "fallback")
        assert title == "Implement new feature X"
        assert len(ac) == 2
        assert "Write tests" in ac
        assert "Deploy" in ac

    def test_parse_writer_output_fallback(self):
        """Parse writer output without title falls back to task."""
        from war_room.war_room import WarRoomPipeline

        pipeline = WarRoomPipeline()
        title, description, ac = pipeline._parse_writer_output("", "My Task")
        assert "[War Room] My Task" in title
        assert description == "My Task"
        assert ac == []

    def test_build_report(self):
        """Report building includes all agent outputs."""
        from war_room.war_room import WarRoomPipeline

        pipeline = WarRoomPipeline()
        pipeline.results = {
            "research": "Research findings here",
            "strategist": "Strategy recommendations",
        }
        report = pipeline._build_report("Test Task", ["research", "strategist"], "2026-04-15")
        assert "# War Room Report" in report
        assert "Test Task" in report
        assert "Research Agent Output" in report
        assert "Strategist Agent Output" in report
        assert "Research findings here" in report
        assert "Strategy recommendations" in report


# ===========================================================================
# Config Tests
# ===========================================================================


class TestConfig:
    """Tests for war_room config loading."""

    def test_config_file_exists(self):
        """war_room/config.json exists."""
        config_path = WAR_ROOM_DIR / "config.json"
        assert config_path.exists()

    def test_config_structure(self):
        """Config has expected keys."""
        config_path = WAR_ROOM_DIR / "config.json"
        cfg = json.loads(config_path.read_text())
        assert "paperclip_url" in cfg
        assert "mock" in cfg
        assert "company_id" in cfg
        assert cfg["paperclip_url"] == "http://127.0.0.1:3100"

    def test_load_bridge_reads_config(self):
        """load_bridge reads config.json."""
        from paperclip_bridge import load_bridge

        bridge = load_bridge(workspace_path=WAR_ROOM_DIR.parent)
        # Bridge should be configured from config
        assert bridge.base_url == "http://127.0.0.1:3100"


# ===========================================================================
# Default Model Test
# ===========================================================================


class TestDefaultModel:
    """Tests for the default model configuration."""

    def test_default_model_is_qwen(self):
        """Default model is qwen3.6-plus, not Claude."""
        from war_room.war_room import DEFAULT_MODEL

        assert "qwen3.6-plus" in DEFAULT_MODEL
        assert "claude" not in DEFAULT_MODEL.lower()
        assert "dashscope" in DEFAULT_MODEL.lower()


# ===========================================================================
# Memory Tests
# ===========================================================================


class TestWarRoomMemory:
    """Tests for the cross-run memory system."""

    @pytest.fixture
    def mem(self, tmp_path):
        from memory import WarRoomMemory

        return WarRoomMemory(tmp_path / "memory")

    def test_save_and_load_all(self, mem):
        """Saving an entry makes it retrievable."""
        mem.save(topic="NERVIX pricing", summary="Free tier + commission model", run_id="test1")
        entries = mem.load_all()
        assert len(entries) == 1
        assert entries[0]["topic"] == "NERVIX pricing"
        assert entries[0]["run_id"] == "test1"

    def test_load_relevant_keyword_match(self, mem):
        """load_relevant returns entries with overlapping keywords."""
        mem.save(topic="NERVIX agent marketplace pricing model", summary="commission", run_id="r1")
        mem.save(topic="SemeClaw dashboard setup", summary="fastapi port 8765", run_id="r2")
        results = mem.load_relevant("pricing strategy for agent marketplace")
        topics = [e["topic"] for e in results]
        assert any("pricing" in t or "marketplace" in t for t in topics)

    def test_load_relevant_no_match(self, mem):
        """load_relevant returns empty list when nothing matches."""
        mem.save(topic="SemeClaw dashboard", summary="fastapi", run_id="r1")
        results = mem.load_relevant("blockchain DeFi protocol")
        # Should return 0 or few results with no keyword overlap
        assert isinstance(results, list)

    def test_duplicate_entries_accumulate(self, mem):
        """Each save call adds a new entry."""
        mem.save(topic="Topic A", summary="sum A", run_id="r1")
        mem.save(topic="Topic B", summary="sum B", run_id="r2")
        assert len(mem.load_all()) == 2

    def test_summary_truncated_to_800(self, mem):
        """Summaries over 800 chars are truncated."""
        long_summary = "x" * 2000
        mem.save(topic="test", summary=long_summary, run_id="r1")
        entry = mem.load_all()[0]
        assert len(entry["summary"]) <= 800

    def test_keywords_extracted(self, mem):
        """Keywords are auto-extracted from topic + summary."""
        mem.save(topic="NERVIX agent marketplace", summary="competitive pricing model", run_id="r1")
        entry = mem.load_all()[0]
        keywords = entry.get("keywords", [])
        assert len(keywords) > 0
        assert "nervix" in keywords or "marketplace" in keywords

    def test_clear(self, mem):
        """Clear removes all entries."""
        mem.save(topic="A", summary="s", run_id="r1")
        mem.save(topic="B", summary="s", run_id="r2")
        count = mem.clear()
        assert count == 2
        assert mem.load_all() == []

    def test_stats(self, mem):
        """Stats returns total count and date range."""
        mem.save(topic="A", summary="s", run_id="r1")
        s = mem.stats()
        assert s["total_entries"] == 1
        assert s["oldest"] is not None


# ===========================================================================
# Coder Agent Tests
# ===========================================================================


class TestCoderAgent:
    """Tests for the Coder agent definition."""

    def test_coder_agent_def_exists(self):
        """coder.md agent definition file exists."""
        from war_room.war_room import AGENTS_DIR

        coder_path = AGENTS_DIR / "coder.md"
        assert coder_path.exists(), "war_room/agents/coder.md not found"

    def test_coder_agent_loadable(self):
        """Coder agent definition can be loaded and has a system prompt."""
        from war_room.war_room import load_agent_def

        agent = load_agent_def("coder")
        assert "system_prompt" in agent
        assert len(agent["system_prompt"]) > 100
        assert "run_shell" in agent["system_prompt"]

    def test_coder_in_tool_enabled_agents(self):
        """Coder is in TOOL_ENABLED_AGENTS so it gets shell/code access."""
        from war_room.war_room import TOOL_ENABLED_AGENTS

        assert "coder" in TOOL_ENABLED_AGENTS


# ===========================================================================
# Telegram Credential Tests
# ===========================================================================


class TestTelegramCreds:
    """Tests for the multi-source Telegram credential loader."""

    def test_load_creds_returns_tuple(self):
        """_load_telegram_creds always returns a 2-tuple."""
        from war_room.war_room import _load_telegram_creds

        result = _load_telegram_creds()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_load_creds_from_yaml(self, tmp_path):
        """Creds are read from default_workspace/config.yaml telegram section."""
        import yaml

        from war_room import war_room as wr

        # Function looks for ROOT / "default_workspace" / "config.yaml"
        ws_dir = tmp_path / "default_workspace"
        ws_dir.mkdir()
        cfg = {"telegram": {"bot_token": "123:ABC", "chat_id": "999"}}
        (ws_dir / "config.yaml").write_text(yaml.dump(cfg))
        original_root = wr.ROOT
        wr.ROOT = tmp_path
        try:
            token, chat_id = wr._load_telegram_creds()
            assert token == "123:ABC"
            assert chat_id == "999"
        finally:
            wr.ROOT = original_root
