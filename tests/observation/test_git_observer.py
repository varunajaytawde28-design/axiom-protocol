"""Tests for git operation observer."""

from __future__ import annotations

from vt_protocol.observation.git_observer import GitObserver, GitOperation


class TestGitObserver:
    def test_record_commit(self):
        obs = GitObserver()
        op = obs.record_commit(
            "Fix login bug",
            files_changed=["src/auth.py", "tests/test_auth.py"],
            author="agent",
            branch="main",
            agent_id="claude-backend",
        )
        assert op.operation == "commit"
        assert op.message == "Fix login bug"
        assert len(op.files_changed) == 2
        assert op.severity == "info"
        assert obs.operation_count == 1

    def test_record_push(self):
        obs = GitObserver()
        op = obs.record_push("main", "origin", agent_id="agent-a")
        assert op.operation == "push"
        assert op.severity == "info"
        assert op.branch == "main"
        assert op.remote == "origin"

    def test_record_force_push_critical(self):
        obs = GitObserver()
        op = obs.record_push("main", "origin", force=True, agent_id="agent-a")
        assert op.operation == "force_push"
        assert op.severity == "critical"
        assert obs.has_force_push is True

    def test_no_force_push(self):
        obs = GitObserver()
        obs.record_push("main")
        assert obs.has_force_push is False

    def test_record_branch_create(self):
        obs = GitObserver()
        op = obs.record_branch("feature/new-thing", "create", agent_id="agent")
        assert op.operation == "branch_create"
        assert op.severity == "info"
        assert op.branch == "feature/new-thing"

    def test_record_branch_delete(self):
        obs = GitObserver()
        op = obs.record_branch("old-branch", "delete")
        assert op.operation == "branch_delete"
        assert op.severity == "warning"

    def test_record_tag(self):
        obs = GitObserver()
        op = obs.record_tag("v1.0.0", "create", message="Release 1.0")
        assert op.operation == "tag_create"
        assert op.severity == "info"
        assert op.details["tag"] == "v1.0.0"

    def test_record_tag_delete(self):
        obs = GitObserver()
        op = obs.record_tag("v0.9.0", "delete")
        assert op.operation == "tag_delete"
        assert op.severity == "warning"

    def test_record_generic_operation(self):
        obs = GitObserver()
        op = obs.record_operation(
            "rebase",
            message="Rebase onto main",
            branch="feature/x",
            agent_id="agent",
        )
        assert op.operation == "rebase"
        assert op.severity == "warning"

    def test_record_merge(self):
        obs = GitObserver()
        op = obs.record_operation("merge", message="Merge main into feature", branch="feature/x")
        assert op.operation == "merge"
        assert op.severity == "info"

    def test_record_reset_hard(self):
        obs = GitObserver()
        op = obs.record_operation("reset_hard", message="Reset to HEAD~3")
        assert op.severity == "critical"

    def test_to_activity_entries(self):
        obs = GitObserver()
        obs.record_commit("Fix bug", files_changed=["a.py"], agent_id="agent-a")
        obs.record_push("main", force=True, agent_id="agent-a")

        entries = obs.to_activity_entries()
        assert len(entries) == 2
        assert entries[0]["action_type"] == "git_operation"
        assert entries[0]["severity"] == "info"
        assert entries[1]["severity"] == "critical"
        assert "force_push" in entries[1]["tool_name"]

    def test_to_activity_entry_format(self):
        obs = GitObserver()
        obs.record_commit("test msg", agent_id="a1", session_id="s1", branch="dev")
        entries = obs.to_activity_entries()
        e = entries[0]
        assert e["agent_id"] == "a1"
        assert e["session_id"] == "s1"
        assert e["details"]["branch"] == "dev"
        assert e["details"]["operation"] == "commit"

    def test_to_dict(self):
        obs = GitObserver()
        op = obs.record_commit("msg", author="user")
        d = op.to_dict()
        assert d["operation"] == "commit"
        assert d["author"] == "user"
        assert "operation_id" in d

    def test_reset(self):
        obs = GitObserver()
        obs.record_commit("a")
        obs.record_push("main")
        obs.reset()
        assert obs.operation_count == 0
