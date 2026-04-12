"""Tests for file read tracking in cache.py."""

from __future__ import annotations

from pathlib import Path

from vt_protocol.observation.cache import FileReadRecord, FileReadTracker


class TestFileReadTracker:
    def test_record_read(self):
        tracker = FileReadTracker()
        record = tracker.record_read("src/main.py", agent_id="claude")
        assert record.file_path == "src/main.py"
        assert record.agent_id == "claude"
        assert tracker.read_count == 1

    def test_unique_files(self):
        tracker = FileReadTracker()
        tracker.record_read("src/main.py", agent_id="a")
        tracker.record_read("src/main.py", agent_id="a")
        tracker.record_read("src/utils.py", agent_id="a")
        assert tracker.read_count == 3
        assert len(tracker.unique_files) == 2

    def test_summary_by_agent(self):
        tracker = FileReadTracker()
        tracker.record_read("a.py", agent_id="agent-a")
        tracker.record_read("b.py", agent_id="agent-a")
        tracker.record_read("a.py", agent_id="agent-a")
        tracker.record_read("c.py", agent_id="agent-b")

        summary = tracker.summary_by_agent()
        assert summary["agent-a"]["reads"] == 3
        assert summary["agent-a"]["unique_files"] == 2
        assert summary["agent-b"]["reads"] == 1
        assert summary["agent-b"]["unique_files"] == 1

    def test_to_activity_entries(self):
        tracker = FileReadTracker()
        tracker.record_read("src/main.py", agent_id="claude", session_id="s1")
        tracker.record_read("tests/test.py", agent_id="claude", session_id="s1")

        entries = tracker.to_activity_entries()
        assert len(entries) == 2
        assert entries[0]["action_type"] == "file_read"
        assert entries[0]["agent_id"] == "claude"
        assert entries[0]["tool_name"] == "read_file"
        assert "main.py" in entries[0]["summary"]

    def test_save_and_load(self, tmp_path: Path):
        tracker = FileReadTracker()
        tracker.record_read("a.py", agent_id="agent-a")
        tracker.record_read("b.py", agent_id="agent-b")

        save_path = tmp_path / "reads.json"
        tracker.save(save_path)

        tracker2 = FileReadTracker()
        tracker2.load(save_path)
        assert tracker2.read_count == 2
        assert tracker2.reads[0].file_path == "a.py"
        assert tracker2.reads[1].agent_id == "agent-b"

    def test_load_missing_file(self, tmp_path: Path):
        tracker = FileReadTracker()
        tracker.load(tmp_path / "nonexistent.json")
        assert tracker.read_count == 0

    def test_reset(self):
        tracker = FileReadTracker()
        tracker.record_read("a.py")
        tracker.record_read("b.py")
        tracker.reset()
        assert tracker.read_count == 0

    def test_summary_unknown_agent(self):
        tracker = FileReadTracker()
        tracker.record_read("a.py")  # no agent_id
        summary = tracker.summary_by_agent()
        assert "unknown" in summary
