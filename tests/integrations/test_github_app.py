"""Tests for GitHub App PR comments — all HTTP mocked."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from vt_protocol.integrations.github_app import (
    BOT_MARKER,
    ReviewResult,
    create_check_run,
    format_review_comment,
    post_or_update_comment,
)

# Dummy request so httpx.Response.raise_for_status() works
_DUMMY_REQUEST = httpx.Request("GET", "https://api.github.com/test")


def _resp(status: int, *, json_data: object = None) -> httpx.Response:
    """Build an httpx.Response with a request attached (needed for raise_for_status)."""
    r = httpx.Response(status, json=json_data)
    r._request = _DUMMY_REQUEST
    return r


class TestReviewResult:
    def test_pass_no_violations(self) -> None:
        review = ReviewResult()
        assert review.status == "pass"
        assert not review.has_violations

    def test_fail_on_contradictions(self) -> None:
        review = ReviewResult(contradictions=[{"verdict": "contradiction"}])
        assert review.status == "fail"
        assert review.has_violations

    def test_fail_on_excess_deps(self) -> None:
        review = ReviewResult(
            new_dependencies=["a", "b", "c", "d"],
            max_deps_per_task=3,
        )
        assert review.has_violations

    def test_pass_within_dep_limit(self) -> None:
        review = ReviewResult(
            new_dependencies=["a", "b"],
            max_deps_per_task=3,
        )
        assert not review.has_violations


class TestFormatReviewComment:
    def test_contains_bot_marker(self) -> None:
        review = ReviewResult()
        comment = format_review_comment(review)
        assert BOT_MARKER in comment

    def test_pass_status(self) -> None:
        review = ReviewResult()
        comment = format_review_comment(review)
        assert "PASS" in comment

    def test_fail_status(self) -> None:
        review = ReviewResult(contradictions=[{"verdict": "contradiction"}])
        comment = format_review_comment(review)
        assert "FAIL" in comment

    def test_includes_decisions(self) -> None:
        review = ReviewResult(
            decisions_introduced=[{"title": "Use PostgreSQL", "type": "architectural"}],
        )
        comment = format_review_comment(review)
        assert "Use PostgreSQL" in comment
        assert "NEW" in comment

    def test_includes_contradictions(self) -> None:
        review = ReviewResult(
            contradictions=[{
                "verdict": "contradiction",
                "decision_a": "Use PG",
                "decision_b": "Use SQLite",
                "confidence": "0.9",
            }],
        )
        comment = format_review_comment(review)
        assert "CONTRADICTION" in comment
        assert "Use PG" in comment

    def test_includes_dependencies(self) -> None:
        review = ReviewResult(new_dependencies=["redis", "celery"])
        comment = format_review_comment(review)
        assert "redis" in comment
        assert "celery" in comment

    def test_includes_coherence(self) -> None:
        review = ReviewResult(coherence_score=0.87)
        comment = format_review_comment(review)
        assert "87%" in comment

    def test_no_decisions_message(self) -> None:
        review = ReviewResult()
        comment = format_review_comment(review)
        assert "No architectural decisions" in comment


class TestPostOrUpdateComment:
    async def test_creates_new_comment(self) -> None:
        """Test creating a new comment when none exists."""
        with patch("vt_protocol.integrations.github_app.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_client.get.return_value = _resp(200, json_data=[])
            mock_client.post.return_value = _resp(201, json_data={"id": 42, "body": "test"})

            result = await post_or_update_comment(
                "owner", "repo", 1, "body",
                token="test-token",
            )
            assert result["id"] == 42
            mock_client.post.assert_called_once()

    async def test_updates_existing_comment(self) -> None:
        """Test updating when our comment already exists."""
        existing_comment = {"id": 99, "body": f"{BOT_MARKER}\nold body"}

        with patch("vt_protocol.integrations.github_app.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_client.get.return_value = _resp(200, json_data=[existing_comment])
            mock_client.patch.return_value = _resp(200, json_data={"id": 99, "body": "updated"})

            result = await post_or_update_comment(
                "owner", "repo", 1, "new body",
                token="test-token",
            )
            assert result["id"] == 99
            mock_client.patch.assert_called_once()
            mock_client.post.assert_not_called()


class TestCreateCheckRun:
    async def test_success_check(self) -> None:
        review = ReviewResult()
        with patch("vt_protocol.integrations.github_app.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post.return_value = _resp(
                201, json_data={"id": 1, "conclusion": "success"}
            )

            result = await create_check_run(
                "owner", "repo", "abc123", review,
                token="test-token",
            )
            assert result["conclusion"] == "success"

    async def test_failure_check(self) -> None:
        review = ReviewResult(contradictions=[{"verdict": "contradiction"}])
        with patch("vt_protocol.integrations.github_app.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post.return_value = _resp(
                201, json_data={"id": 1, "conclusion": "failure"}
            )

            result = await create_check_run(
                "owner", "repo", "abc123", review,
                token="test-token",
            )
            assert result["conclusion"] == "failure"
            # Verify the request body had "failure"
            call_kwargs = mock_client.post.call_args
            body = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
            assert body["conclusion"] == "failure"
