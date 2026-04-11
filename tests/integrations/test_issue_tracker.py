"""Tests for Jira/Linear issue tracker integration."""

from __future__ import annotations

import pytest

from vt_protocol.integrations.issue_tracker import (
    IssueStatus,
    IssueSyncResult,
    JiraAdapter,
    LinearAdapter,
    TrackerConfig,
    TrackerIssue,
    TrackerType,
    build_contradiction_issue,
    get_adapter,
)


# ---------------------------------------------------------------------------
# TrackerConfig
# ---------------------------------------------------------------------------


class TestTrackerConfig:
    def test_not_configured_by_default(self) -> None:
        c = TrackerConfig()
        assert c.is_configured is False

    def test_configured(self) -> None:
        c = TrackerConfig(
            base_url="https://jira.example.com",
            api_token="tok",
            project_key="PROJ",
        )
        assert c.is_configured is True

    def test_missing_token_not_configured(self) -> None:
        c = TrackerConfig(base_url="https://jira.example.com", project_key="PROJ")
        assert c.is_configured is False

    def test_default_labels(self) -> None:
        c = TrackerConfig()
        assert "architecture" in c.labels
        assert "vt-protocol" in c.labels


# ---------------------------------------------------------------------------
# TrackerIssue
# ---------------------------------------------------------------------------


class TestTrackerIssue:
    def test_to_dict(self) -> None:
        issue = TrackerIssue(
            issue_id="123",
            issue_key="PROJ-123",
            contradiction_id="c1",
            title="Test issue",
            status=IssueStatus.OPEN,
            assignee="alice",
        )
        d = issue.to_dict()
        assert d["issue_id"] == "123"
        assert d["issue_key"] == "PROJ-123"
        assert d["status"] == "open"

    def test_default_status(self) -> None:
        issue = TrackerIssue()
        assert issue.status == IssueStatus.OPEN


# ---------------------------------------------------------------------------
# IssueStatus enum
# ---------------------------------------------------------------------------


class TestIssueStatus:
    def test_all_values(self) -> None:
        assert IssueStatus.OPEN.value == "open"
        assert IssueStatus.IN_PROGRESS.value == "in_progress"
        assert IssueStatus.RESOLVED.value == "resolved"
        assert IssueStatus.CLOSED.value == "closed"
        assert IssueStatus.WONT_FIX.value == "wont_fix"


# ---------------------------------------------------------------------------
# JiraAdapter
# ---------------------------------------------------------------------------


class TestJiraAdapter:
    @pytest.fixture()
    def adapter(self) -> JiraAdapter:
        config = TrackerConfig(
            tracker_type=TrackerType.JIRA,
            base_url="https://jira.example.com",
            api_token="test-token",
            project_key="PROJ",
        )
        return JiraAdapter(config)

    def test_create_issue(self, adapter: JiraAdapter) -> None:
        issue = adapter.create_issue(
            "Test Contradiction",
            "Description of the issue",
            assignee="alice",
        )
        assert issue.title == "Test Contradiction"
        assert issue.assignee == "alice"

    def test_create_issue_has_payload(self, adapter: JiraAdapter) -> None:
        issue = adapter.create_issue("Title", "Desc")
        assert "payload" in issue.metadata
        payload = issue.metadata["payload"]
        assert payload["fields"]["project"]["key"] == "PROJ"

    def test_create_issue_with_labels(self, adapter: JiraAdapter) -> None:
        issue = adapter.create_issue(
            "Title", "Desc",
            labels=["custom-label"],
        )
        payload = issue.metadata["payload"]
        assert "custom-label" in payload["fields"]["labels"]

    def test_update_status(self, adapter: JiraAdapter) -> None:
        result = adapter.update_status("123", IssueStatus.RESOLVED)
        assert result is True

    def test_get_status(self, adapter: JiraAdapter) -> None:
        # Stub returns None until actual HTTP is implemented
        result = adapter.get_status("123")
        assert result is None

    def test_get_issue(self, adapter: JiraAdapter) -> None:
        result = adapter.get_issue("123")
        assert result is None


# ---------------------------------------------------------------------------
# LinearAdapter
# ---------------------------------------------------------------------------


class TestLinearAdapter:
    @pytest.fixture()
    def adapter(self) -> LinearAdapter:
        config = TrackerConfig(
            tracker_type=TrackerType.LINEAR,
            base_url="https://api.linear.app",
            api_token="lin_test",
            project_key="TEAM",
        )
        return LinearAdapter(config)

    def test_create_issue(self, adapter: LinearAdapter) -> None:
        issue = adapter.create_issue("Title", "Desc")
        assert issue.title == "Title"
        assert issue.metadata["team_key"] == "TEAM"

    def test_update_status(self, adapter: LinearAdapter) -> None:
        assert adapter.update_status("abc", IssueStatus.CLOSED) is True

    def test_get_status(self, adapter: LinearAdapter) -> None:
        assert adapter.get_status("abc") is None


# ---------------------------------------------------------------------------
# get_adapter factory
# ---------------------------------------------------------------------------


class TestGetAdapter:
    def test_jira(self) -> None:
        config = TrackerConfig(tracker_type=TrackerType.JIRA)
        adapter = get_adapter(config)
        assert isinstance(adapter, JiraAdapter)

    def test_linear(self) -> None:
        config = TrackerConfig(tracker_type=TrackerType.LINEAR)
        adapter = get_adapter(config)
        assert isinstance(adapter, LinearAdapter)

    def test_unsupported(self) -> None:
        config = TrackerConfig(tracker_type=TrackerType.GITHUB)
        with pytest.raises(ValueError, match="Unsupported"):
            get_adapter(config)


# ---------------------------------------------------------------------------
# build_contradiction_issue
# ---------------------------------------------------------------------------


class TestBuildContradictionIssue:
    def test_basic(self) -> None:
        title, desc = build_contradiction_issue(
            contradiction_id="abc123",
            decision_a_title="Use PostgreSQL",
            decision_b_title="Use MongoDB",
            verdict="contradiction",
            confidence=0.85,
            reasoning="Different database paradigms",
        )
        assert "[VT]" in title
        assert "PostgreSQL" in title
        assert "MongoDB" in title
        assert "85%" in desc
        assert "abc123" in desc

    def test_title_truncation(self) -> None:
        title, _ = build_contradiction_issue(
            contradiction_id="abc",
            decision_a_title="A" * 100,
            decision_b_title="B" * 100,
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
        )
        assert len(title) <= 120

    def test_includes_dimensions(self) -> None:
        _, desc = build_contradiction_issue(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
            dimensions=["database", "auth"],
        )
        assert "database" in desc
        assert "auth" in desc

    def test_includes_owners(self) -> None:
        _, desc = build_contradiction_issue(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="tension",
            confidence=0.6,
            reasoning="Tension",
            owners=["@alice"],
        )
        assert "@alice" in desc


# ---------------------------------------------------------------------------
# IssueSyncResult
# ---------------------------------------------------------------------------


class TestIssueSyncResult:
    def test_to_dict(self) -> None:
        r = IssueSyncResult(synced=5, created=3, updated=2, errors=["oops"])
        d = r.to_dict()
        assert d["synced"] == 5
        assert d["created"] == 3
        assert d["errors"] == ["oops"]

    def test_defaults(self) -> None:
        r = IssueSyncResult()
        assert r.synced == 0
        assert r.errors == []
