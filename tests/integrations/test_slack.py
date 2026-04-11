"""Tests for Slack integration."""

from __future__ import annotations

import json

import pytest

from vt_protocol.integrations.slack import (
    SlackConfig,
    SlackMessage,
    build_contradiction_message,
    build_resolution_message,
)


# ---------------------------------------------------------------------------
# SlackConfig
# ---------------------------------------------------------------------------


class TestSlackConfig:
    def test_not_configured_by_default(self) -> None:
        c = SlackConfig()
        assert c.is_configured is False

    def test_configured_with_webhook(self) -> None:
        c = SlackConfig(webhook_url="https://hooks.slack.com/xxx")
        assert c.is_configured is True

    def test_configured_with_token(self) -> None:
        c = SlackConfig(bot_token="xoxb-xxx")
        assert c.is_configured is True

    def test_defaults(self) -> None:
        c = SlackConfig()
        assert c.default_channel == "#architecture-decisions"
        assert c.notify_on_contradiction is True
        assert c.notify_on_resolution is True


# ---------------------------------------------------------------------------
# SlackMessage
# ---------------------------------------------------------------------------


class TestSlackMessage:
    def test_to_payload_minimal(self) -> None:
        m = SlackMessage(text="Hello")
        p = m.to_payload()
        assert p["text"] == "Hello"
        assert "channel" not in p

    def test_to_payload_with_channel(self) -> None:
        m = SlackMessage(channel="#test", text="Hello")
        p = m.to_payload()
        assert p["channel"] == "#test"

    def test_to_payload_with_blocks(self) -> None:
        m = SlackMessage(
            text="Fallback",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}],
        )
        p = m.to_payload()
        assert len(p["blocks"]) == 1

    def test_payload_is_json_serializable(self) -> None:
        m = SlackMessage(text="Test", blocks=[{"type": "divider"}])
        json.dumps(m.to_payload())  # Should not raise


# ---------------------------------------------------------------------------
# build_contradiction_message
# ---------------------------------------------------------------------------


class TestBuildContradictionMessage:
    def test_basic_message(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc123",
            decision_a_title="Use PostgreSQL",
            decision_b_title="Use MongoDB",
            verdict="contradiction",
            confidence=0.85,
            reasoning="They use different database paradigms",
        )
        assert "contradiction" in msg.text.lower()
        assert "PostgreSQL" in msg.text
        assert "MongoDB" in msg.text
        assert len(msg.blocks) > 0

    def test_has_header(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
        )
        header = next(b for b in msg.blocks if b["type"] == "header")
        assert "Contradiction" in header["text"]["text"]

    def test_has_action_buttons(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
        )
        actions = next(b for b in msg.blocks if b["type"] == "actions")
        assert len(actions["elements"]) == 3  # View, Accept Exception, Dismiss

    def test_includes_evidence(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
            evidence_a="Uses PostgreSQL for all data",
            evidence_b="Uses MongoDB for all data",
        )
        # Should have evidence section
        sections = [b for b in msg.blocks if b["type"] == "section"]
        assert len(sections) >= 3  # decisions, reasoning, evidence

    def test_includes_dimensions(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
            dimensions=["database", "caching"],
        )
        context = next(b for b in msg.blocks if b["type"] == "context")
        context_text = json.dumps(context)
        assert "database" in context_text

    def test_includes_owners(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
            owners=["@alice", "@bob"],
        )
        context = next(b for b in msg.blocks if b["type"] == "context")
        context_text = json.dumps(context)
        assert "@alice" in context_text

    def test_tension_uses_yellow_emoji(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            verdict="tension",
            confidence=0.6,
            reasoning="Tension",
        )
        assert "🟡" in msg.text

    def test_dashboard_url_in_button(self) -> None:
        msg = build_contradiction_message(
            contradiction_id="abc123",
            decision_a_title="A",
            decision_b_title="B",
            verdict="contradiction",
            confidence=0.9,
            reasoning="Conflict",
            dashboard_url="https://vt.example.com",
        )
        actions = next(b for b in msg.blocks if b["type"] == "actions")
        view_btn = actions["elements"][0]
        assert "vt.example.com" in view_btn["url"]
        assert "abc123" in view_btn["url"]


# ---------------------------------------------------------------------------
# build_resolution_message
# ---------------------------------------------------------------------------


class TestBuildResolutionMessage:
    def test_basic_resolution(self) -> None:
        msg = build_resolution_message(
            contradiction_id="abc",
            decision_a_title="Use PostgreSQL",
            decision_b_title="Use MongoDB",
            resolution_action="pick_a",
            resolved_by="alice",
        )
        assert "resolved" in msg.text.lower()
        assert "alice" in msg.text
        assert len(msg.blocks) > 0

    def test_action_display(self) -> None:
        msg = build_resolution_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            resolution_action="accept_exception",
            resolved_by="bob",
        )
        assert "Accept Exception" in msg.text

    def test_payload_serializable(self) -> None:
        msg = build_resolution_message(
            contradiction_id="abc",
            decision_a_title="A",
            decision_b_title="B",
            resolution_action="dismiss",
            resolved_by="system",
        )
        json.dumps(msg.to_payload())  # Should not raise
