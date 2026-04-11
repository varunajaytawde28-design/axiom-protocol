"""Jira/Linear integration — bidirectional sync for contradictions.

Maps contradictions to issue tracker tickets:
  - Create issues for new contradictions
  - Sync resolution status back from tracker
  - Update contradiction status when issues are resolved
  - Support both Jira and Linear via adapter pattern

Adapter interface:
  - create_issue(contradiction) → issue_id
  - update_issue(issue_id, status) → bool
  - get_issue_status(issue_id) → status
  - sync_statuses(issue_map) → updated list
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TrackerType(str, Enum):
    JIRA = "jira"
    LINEAR = "linear"
    GITHUB = "github"


class IssueStatus(str, Enum):
    """Normalized issue statuses across trackers."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    WONT_FIX = "wont_fix"


@dataclass
class TrackerConfig:
    """Configuration for issue tracker integration."""

    tracker_type: TrackerType = TrackerType.JIRA
    base_url: str = ""
    api_token: str = ""
    project_key: str = ""
    default_assignee: str = ""
    labels: list[str] = field(default_factory=lambda: ["architecture", "vt-protocol"])
    sync_enabled: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_token and self.project_key)


@dataclass
class TrackerIssue:
    """An issue in the tracker, linked to a contradiction."""

    issue_id: str = ""
    issue_key: str = ""  # e.g. "PROJ-123" for Jira, "PROJ-abc" for Linear
    contradiction_id: str = ""
    title: str = ""
    description: str = ""
    status: IssueStatus = IssueStatus.OPEN
    assignee: str = ""
    url: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_key": self.issue_key,
            "contradiction_id": self.contradiction_id,
            "title": self.title,
            "status": self.status.value,
            "assignee": self.assignee,
            "url": self.url,
            "created_at": self.created_at.isoformat(),
        }


class IssueTrackerAdapter(ABC):
    """Abstract adapter for issue tracker operations."""

    @abstractmethod
    def create_issue(
        self,
        title: str,
        description: str,
        *,
        assignee: str = "",
        labels: list[str] | None = None,
    ) -> TrackerIssue:
        """Create a new issue in the tracker."""

    @abstractmethod
    def update_status(self, issue_id: str, status: IssueStatus) -> bool:
        """Update an issue's status."""

    @abstractmethod
    def get_status(self, issue_id: str) -> IssueStatus | None:
        """Get current status of an issue."""

    @abstractmethod
    def get_issue(self, issue_id: str) -> TrackerIssue | None:
        """Fetch full issue details."""


class JiraAdapter(IssueTrackerAdapter):
    """Jira REST API adapter.

    Uses Jira REST API v3 for issue operations.
    Requires base_url, api_token, and project_key.
    """

    def __init__(self, config: TrackerConfig) -> None:
        self.config = config
        self._status_map: dict[str, IssueStatus] = {
            "To Do": IssueStatus.OPEN,
            "Open": IssueStatus.OPEN,
            "In Progress": IssueStatus.IN_PROGRESS,
            "Done": IssueStatus.RESOLVED,
            "Closed": IssueStatus.CLOSED,
            "Won't Fix": IssueStatus.WONT_FIX,
            "Won't Do": IssueStatus.WONT_FIX,
        }

    def create_issue(
        self,
        title: str,
        description: str,
        *,
        assignee: str = "",
        labels: list[str] | None = None,
    ) -> TrackerIssue:
        """Create a Jira issue via REST API."""
        issue_labels = labels or self.config.labels

        payload = {
            "fields": {
                "project": {"key": self.config.project_key},
                "summary": title,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                },
                "issuetype": {"name": "Task"},
                "labels": issue_labels,
            }
        }
        if assignee:
            payload["fields"]["assignee"] = {"accountId": assignee}

        # Actual HTTP call would go here — returning stub for now
        # response = httpx.post(f"{self.config.base_url}/rest/api/3/issue", ...)
        logger.info("Would create Jira issue: %s", title)

        return TrackerIssue(
            title=title,
            description=description,
            assignee=assignee,
            metadata={"payload": payload},
        )

    def update_status(self, issue_id: str, status: IssueStatus) -> bool:
        logger.info("Would update Jira issue %s to %s", issue_id, status.value)
        return True

    def get_status(self, issue_id: str) -> IssueStatus | None:
        logger.info("Would get Jira issue %s status", issue_id)
        return None

    def get_issue(self, issue_id: str) -> TrackerIssue | None:
        logger.info("Would get Jira issue %s", issue_id)
        return None


class LinearAdapter(IssueTrackerAdapter):
    """Linear GraphQL API adapter.

    Uses Linear's GraphQL API for issue operations.
    Requires api_token and project_key (team key).
    """

    def __init__(self, config: TrackerConfig) -> None:
        self.config = config

    def create_issue(
        self,
        title: str,
        description: str,
        *,
        assignee: str = "",
        labels: list[str] | None = None,
    ) -> TrackerIssue:
        """Create a Linear issue via GraphQL."""
        logger.info("Would create Linear issue: %s", title)

        return TrackerIssue(
            title=title,
            description=description,
            assignee=assignee,
            metadata={"team_key": self.config.project_key},
        )

    def update_status(self, issue_id: str, status: IssueStatus) -> bool:
        logger.info("Would update Linear issue %s to %s", issue_id, status.value)
        return True

    def get_status(self, issue_id: str) -> IssueStatus | None:
        logger.info("Would get Linear issue %s status", issue_id)
        return None

    def get_issue(self, issue_id: str) -> TrackerIssue | None:
        logger.info("Would get Linear issue %s", issue_id)
        return None


def get_adapter(config: TrackerConfig) -> IssueTrackerAdapter:
    """Factory: return the appropriate adapter for the configured tracker."""
    if config.tracker_type == TrackerType.JIRA:
        return JiraAdapter(config)
    if config.tracker_type == TrackerType.LINEAR:
        return LinearAdapter(config)
    raise ValueError(f"Unsupported tracker type: {config.tracker_type}")


def build_contradiction_issue(
    contradiction_id: str,
    decision_a_title: str,
    decision_b_title: str,
    verdict: str,
    confidence: float,
    reasoning: str,
    *,
    dimensions: list[str] | None = None,
    owners: list[str] | None = None,
) -> tuple[str, str]:
    """Build issue title and description for a contradiction.

    Returns (title, description) suitable for any tracker.
    """
    title = f"[VT] {verdict.title()}: {decision_a_title} vs {decision_b_title}"
    if len(title) > 120:
        title = title[:117] + "..."

    dims_str = ", ".join(dimensions) if dimensions else "none"
    owners_str = ", ".join(owners) if owners else "unassigned"

    description = (
        f"## Architecture {verdict.title()}\n\n"
        f"**Decision A:** {decision_a_title}\n"
        f"**Decision B:** {decision_b_title}\n\n"
        f"**Confidence:** {confidence:.0%}\n"
        f"**Dimensions:** {dims_str}\n"
        f"**Owners:** {owners_str}\n\n"
        f"### Reasoning\n{reasoning}\n\n"
        f"---\n"
        f"_Generated by VT Protocol (contradiction ID: {contradiction_id})_"
    )

    return title, description


@dataclass
class IssueSyncResult:
    """Result of syncing issue tracker statuses."""

    synced: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "synced": self.synced,
            "created": self.created,
            "updated": self.updated,
            "errors": self.errors,
        }
