"""Slack integration — notifications to code owners, inline triage actions.

Sends contradiction notifications to appropriate Slack channels/users
based on CODEOWNERS mapping. Supports:
  - Channel notifications for new contradictions
  - DM to affected code owners
  - Inline triage actions (via Slack Block Kit buttons)
  - Webhook-based (no bot token required for basic use)

Message format uses Slack Block Kit for rich formatting:
  - Section block with contradiction summary
  - Context block with confidence, dimensions, evidence
  - Actions block with resolution buttons
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SlackConfig:
    """Configuration for Slack integration."""

    webhook_url: str = ""
    bot_token: str = ""  # For direct API calls (optional)
    default_channel: str = "#architecture-decisions"
    notify_on_contradiction: bool = True
    notify_on_resolution: bool = True
    include_evidence: bool = True
    mention_owners: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url or self.bot_token)


@dataclass
class SlackMessage:
    """A structured Slack message built from Block Kit blocks."""

    channel: str = ""
    text: str = ""  # Fallback text for notifications
    blocks: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": self.text}
        if self.channel:
            payload["channel"] = self.channel
        if self.blocks:
            payload["blocks"] = self.blocks
        return payload


def build_contradiction_message(
    contradiction_id: str,
    decision_a_title: str,
    decision_b_title: str,
    verdict: str,
    confidence: float,
    reasoning: str,
    *,
    evidence_a: str = "",
    evidence_b: str = "",
    dimensions: list[str] | None = None,
    owners: list[str] | None = None,
    dashboard_url: str = "",
) -> SlackMessage:
    """Build a Slack Block Kit message for a new contradiction.

    Returns a SlackMessage with rich formatting:
    - Header with verdict badge
    - Decision comparison
    - Confidence and dimension context
    - Action buttons for triage
    """
    verdict_emoji = "🔴" if verdict == "contradiction" else "🟡"
    confidence_pct = f"{confidence:.0%}"

    fallback = (
        f"{verdict_emoji} {verdict.upper()}: "
        f'"{decision_a_title}" vs "{decision_b_title}" '
        f"(confidence: {confidence_pct})"
    )

    blocks: list[dict[str, Any]] = []

    # Header
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{verdict_emoji} Architecture {verdict.title()} Detected",
        },
    })

    # Decision comparison
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Decision A:*\n{decision_a_title}"},
            {"type": "mrkdwn", "text": f"*Decision B:*\n{decision_b_title}"},
        ],
    })

    # Reasoning
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Reasoning:*\n{reasoning[:500]}",
        },
    })

    # Evidence (optional)
    if evidence_a and evidence_b:
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Evidence A:*\n_{evidence_a[:200]}_"},
                {"type": "mrkdwn", "text": f"*Evidence B:*\n_{evidence_b[:200]}_"},
            ],
        })

    # Context: confidence + dimensions + owners
    context_elements: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*Confidence:* {confidence_pct}"},
    ]
    if dimensions:
        context_elements.append(
            {"type": "mrkdwn", "text": f"*Dimensions:* {', '.join(dimensions)}"}
        )
    if owners:
        mention_str = ", ".join(owners)
        context_elements.append(
            {"type": "mrkdwn", "text": f"*Owners:* {mention_str}"}
        )

    blocks.append({"type": "context", "elements": context_elements})

    # Divider
    blocks.append({"type": "divider"})

    # Action buttons
    actions: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View in Dashboard"},
            "url": f"{dashboard_url}/blast-radius/{contradiction_id}" if dashboard_url else "#",
            "style": "primary",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Accept Exception"},
            "action_id": f"accept_exception_{contradiction_id}",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Dismiss"},
            "action_id": f"dismiss_{contradiction_id}",
            "style": "danger",
        },
    ]
    blocks.append({"type": "actions", "elements": actions})

    return SlackMessage(text=fallback, blocks=blocks)


def build_resolution_message(
    contradiction_id: str,
    decision_a_title: str,
    decision_b_title: str,
    resolution_action: str,
    resolved_by: str,
) -> SlackMessage:
    """Build a Slack message for a resolved contradiction."""
    action_display = resolution_action.replace("_", " ").title()
    text = (
        f"✅ Contradiction resolved: "
        f'"{decision_a_title}" vs "{decision_b_title}" '
        f"— {action_display} by {resolved_by}"
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"✅ *Contradiction Resolved*\n"
                    f"_{decision_a_title}_ vs _{decision_b_title}_\n"
                    f"Action: *{action_display}* by {resolved_by}"
                ),
            },
        },
    ]

    return SlackMessage(text=text, blocks=blocks)


async def send_webhook(
    webhook_url: str,
    message: SlackMessage,
) -> bool:
    """Send a message via Slack incoming webhook.

    Returns True if the webhook call succeeded, False otherwise.
    Uses httpx for async HTTP.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed, cannot send Slack webhook")
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=message.to_payload(),
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
            if response.status_code == 200:
                logger.info("Slack webhook sent successfully")
                return True
            logger.warning(
                "Slack webhook failed: %d %s",
                response.status_code,
                response.text[:200],
            )
            return False
    except Exception:
        logger.exception("Failed to send Slack webhook")
        return False


def send_webhook_sync(
    webhook_url: str,
    message: SlackMessage,
) -> bool:
    """Synchronous version of send_webhook for CLI use."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed, cannot send Slack webhook")
        return False

    try:
        response = httpx.post(
            webhook_url,
            json=message.to_payload(),
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        return response.status_code == 200
    except Exception:
        logger.exception("Failed to send Slack webhook")
        return False
