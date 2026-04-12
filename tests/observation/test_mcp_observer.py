"""Tests for MCP Gateway observer."""

from __future__ import annotations

from vt_protocol.observation.mcp_observer import (
    MCPObserver,
    MCPToolCall,
    categorize_tool,
)


class TestCategorizeTool:
    def test_filesystem_tools(self):
        assert categorize_tool("read_file") == "filesystem"
        assert categorize_tool("write_file") == "filesystem"
        assert categorize_tool("list_directory") == "filesystem"
        assert categorize_tool("search_files") == "filesystem"

    def test_shell_tools(self):
        assert categorize_tool("run_command") == "shell"
        assert categorize_tool("bash") == "shell"
        assert categorize_tool("execute_command") == "shell"

    def test_git_tools(self):
        assert categorize_tool("git_commit") == "git"
        assert categorize_tool("git_push") == "git"
        assert categorize_tool("git_branch") == "git"

    def test_external_unknown(self):
        assert categorize_tool("send_email") == "external"
        assert categorize_tool("slack_post") == "external"

    def test_heuristic_filesystem(self):
        assert categorize_tool("read_json_file") == "filesystem"
        assert categorize_tool("file_upload") == "filesystem"

    def test_heuristic_shell(self):
        assert categorize_tool("shell_exec") == "shell"

    def test_heuristic_git(self):
        assert categorize_tool("git_cherry_pick") == "git"


class TestMCPObserver:
    def test_record_basic(self):
        obs = MCPObserver()
        call = obs.record("read_file", {"path": "src/main.py"}, "file contents here")
        assert call.tool_name == "read_file"
        assert call.category == "filesystem"
        assert call.success is True
        assert obs.call_count == 1

    def test_record_with_agent(self):
        obs = MCPObserver()
        call = obs.record(
            "run_command",
            {"command": "ls -la"},
            "total 42",
            agent_id="claude-backend",
            session_id="sess-001",
            duration_ms=150.0,
        )
        assert call.agent_id == "claude-backend"
        assert call.session_id == "sess-001"
        assert call.duration_ms == 150.0
        assert call.category == "shell"

    def test_record_failure(self):
        obs = MCPObserver()
        call = obs.record(
            "write_file",
            {"path": "/root/secret"},
            "",
            success=False,
            error="Permission denied",
        )
        assert call.success is False
        assert call.error == "Permission denied"

    def test_response_preview_truncated(self):
        obs = MCPObserver()
        long_response = "x" * 1000
        call = obs.record("read_file", {}, long_response)
        assert len(call.response_preview) == 500

    def test_calls_by_category(self):
        obs = MCPObserver()
        obs.record("read_file", {})
        obs.record("write_file", {})
        obs.record("run_command", {})
        obs.record("git_push", {})
        counts = obs.calls_by_category()
        assert counts["filesystem"] == 2
        assert counts["shell"] == 1
        assert counts["git"] == 1

    def test_calls_by_agent(self):
        obs = MCPObserver()
        obs.record("read_file", {}, agent_id="agent-a")
        obs.record("read_file", {}, agent_id="agent-a")
        obs.record("run_command", {}, agent_id="agent-b")
        counts = obs.calls_by_agent()
        assert counts["agent-a"] == 2
        assert counts["agent-b"] == 1

    def test_to_activity_entries(self):
        obs = MCPObserver()
        obs.record("read_file", {"path": "main.py"}, "contents", agent_id="test")
        obs.record("run_command", {"command": "ls"}, "output", agent_id="test")

        entries = obs.to_activity_entries()
        assert len(entries) == 2
        assert entries[0]["action_type"] == "mcp_tool"
        assert entries[0]["agent_id"] == "test"
        assert "filesystem" in entries[0]["summary"]
        assert entries[1]["details"]["category"] == "shell"

    def test_to_activity_entry_failure_severity(self):
        obs = MCPObserver()
        obs.record("write_file", {}, "", success=False, error="fail")
        entries = obs.to_activity_entries()
        assert entries[0]["severity"] == "warning"

    def test_reset(self):
        obs = MCPObserver()
        obs.record("read_file", {})
        obs.record("write_file", {})
        assert obs.call_count == 2
        obs.reset()
        assert obs.call_count == 0

    def test_to_dict(self):
        obs = MCPObserver()
        call = obs.record("read_file", {"path": "x"}, "y", server_name="fs-server")
        d = call.to_dict()
        assert d["tool_name"] == "read_file"
        assert d["server_name"] == "fs-server"
        assert "call_id" in d
