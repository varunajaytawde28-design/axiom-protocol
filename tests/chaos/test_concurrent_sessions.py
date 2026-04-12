"""Chaos test: Concurrent MCP Sessions.

Tests that multiple MCP sessions for different projects
don't interfere with each other.
"""

from __future__ import annotations

import json
import threading

import pytest

from vt_protocol.mcp.server import (
    _sessions,
    check_before_coding,
    get_project_decisions,
    report_decision,
)

pytestmark = pytest.mark.chaos


@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    yield
    _sessions.clear()


class TestConcurrentSessions:
    """Multiple projects using MCP tools simultaneously."""

    def test_separate_sessions_per_project(self):
        """Each project gets its own session."""
        check_before_coding("a.py", project="project-A")
        check_before_coding("b.py", project="project-B")
        assert len(_sessions) == 2

        # Each session tracks its own project
        projects = {s.project for s in _sessions.values()}
        assert projects == {"project-A", "project-B"}

    def test_session_reuse_within_project(self):
        """Same project reuses session across tool calls."""
        check_before_coding("a.py", project="project-A")
        get_project_decisions(project="project-A")
        report_decision(title="Test", content="C", project="project-A")

        project_a_sessions = [s for s in _sessions.values() if s.project == "project-A"]
        assert len(project_a_sessions) == 1
        assert project_a_sessions[0].context_injections == 2

    def test_many_projects(self):
        """50 different projects each get separate sessions."""
        for i in range(50):
            check_before_coding("file.py", project=f"project-{i}")
        assert len(_sessions) == 50

    def test_threaded_session_creation(self):
        """Sessions created from multiple threads don't crash."""
        errors = []

        def create_session(project: str):
            try:
                check_before_coding("file.py", project=project)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=create_session, args=(f"thread-{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(_sessions) == 10

    def test_report_tracks_per_session(self):
        """Each project gets its own session with correct project field."""
        check_before_coding("a.py", project="A")
        check_before_coding("b.py", project="B")

        report_decision(title="A-decision", content="C", project="A")
        report_decision(title="B-decision", content="C", project="B")

        a_sessions = [s for s in _sessions.values() if s.project == "A"]
        b_sessions = [s for s in _sessions.values() if s.project == "B"]

        assert len(a_sessions) == 1
        assert len(b_sessions) == 1
        assert a_sessions[0].project == "A"
        assert b_sessions[0].project == "B"
