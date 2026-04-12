"""Compliance test: Audit Completeness.

Verifies that the audit system captures all required events
for EU AI Act Article 12 compliance:
  - Who made the decision (actor)
  - What context was given (payload)
  - When it happened (timestamp)
  - What session it belongs to (session_id)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = pytest.mark.compliance


class TestAuditEntryCompleteness:
    """Every AuditEntry has all EU AI Act fields."""

    def test_required_fields_present(self):
        """All required audit fields are populated."""
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="claude-code",
            session_id="sess-001",
            project="test-project",
            payload={"decision_id": "d1", "title": "Test"},
        )
        assert entry.actor == "claude-code"
        assert entry.session_id == "sess-001"
        assert entry.timestamp is not None
        assert entry.event_type == AuditEventType.DECISION_ADDED
        assert entry.project == "test-project"

    def test_entry_hash_computed(self):
        """Entry hash is auto-computed on creation."""
        entry = AuditEntry(
            event_type=AuditEventType.SESSION_STARTED,
            actor="test",
        )
        assert entry.entry_hash != ""
        assert len(entry.entry_hash) == 64  # SHA-256 hex

    def test_entry_hash_verifies(self):
        """Entry hash matches recomputation."""
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="test-actor",
            payload={"key": "value"},
        )
        assert entry.verify() is True

    def test_tampered_entry_fails_verify(self):
        """Modifying entry content causes hash verification to fail."""
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="original-actor",
        )
        original_hash = entry.entry_hash

        # Tamper with the actor
        entry.actor = "tampered-actor"
        assert entry.entry_hash == original_hash  # hash field unchanged
        assert entry.verify() is False  # but recomputation differs


class TestAuditEventTypes:
    """All audit event types are usable."""

    @pytest.mark.parametrize("event_type", list(AuditEventType))
    def test_all_event_types(self, event_type):
        """Every AuditEventType can be stored in Merkle tree."""
        tree = MerkleTree(":memory:")
        entry = AuditEntry(
            event_type=event_type,
            actor="test",
            payload={"event": event_type.value},
        )
        idx = tree.append(entry)
        assert idx >= 0

        retrieved = tree.get_entry(idx)
        assert retrieved is not None
        assert retrieved.event_type == event_type
        tree.close()


class TestAuditTimeline:
    """Audit entries preserve chronological order."""

    def test_entries_ordered(self):
        """Entries come back in insertion order."""
        tree = MerkleTree(":memory:")
        for i in range(10):
            entry = AuditEntry(
                event_type=AuditEventType.DECISION_ADDED,
                actor=f"actor-{i}",
                payload={"index": i},
            )
            tree.append(entry)

        entries = tree.get_entries(limit=10)
        assert len(entries) == 10
        for i, e in enumerate(entries):
            assert e.payload["index"] == i
        tree.close()

    def test_timestamps_monotonic(self):
        """Entry timestamps are monotonically increasing."""
        tree = MerkleTree(":memory:")
        for i in range(5):
            tree.append(AuditEntry(
                event_type=AuditEventType.CONTEXT_INJECTION,
                actor="test",
            ))

        entries = tree.get_entries(limit=5)
        for i in range(1, len(entries)):
            assert entries[i].timestamp >= entries[i - 1].timestamp
        tree.close()


class TestAuditWithSessions:
    """Audit entries track MCP sessions."""

    def test_session_events(self):
        """Session start and end are captured."""
        tree = MerkleTree(":memory:")
        session_id = "sess-abc123"

        tree.append(AuditEntry(
            event_type=AuditEventType.SESSION_STARTED,
            actor="claude-code",
            session_id=session_id,
            payload={"project": "test"},
        ))
        tree.append(AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="claude-code",
            session_id=session_id,
            payload={"decision_id": "d1"},
        ))
        tree.append(AuditEntry(
            event_type=AuditEventType.SESSION_COMPLETED,
            actor="claude-code",
            session_id=session_id,
            payload={"decisions_count": 1},
        ))

        entries = tree.get_entries()
        assert len(entries) == 3
        assert all(e.session_id == session_id for e in entries)
        assert entries[0].event_type == AuditEventType.SESSION_STARTED
        assert entries[2].event_type == AuditEventType.SESSION_COMPLETED
        tree.close()

    def test_decision_added_payload(self):
        """DECISION_ADDED entries carry decision metadata."""
        tree = MerkleTree(":memory:")
        tree.append(AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="claude-code",
            session_id="sess-001",
            project="test",
            payload={
                "decision_id": "d1",
                "title": "Use PostgreSQL",
                "dimensions": ["database"],
                "source_type": "agent",
                "confidence": 0.85,
            },
        ))

        entry = tree.get_entry(0)
        assert entry.payload["decision_id"] == "d1"
        assert entry.payload["title"] == "Use PostgreSQL"
        assert entry.payload["confidence"] == 0.85
        tree.close()

    def test_contradiction_detected_payload(self):
        """CONTRADICTION_DETECTED entries carry both decision IDs."""
        tree = MerkleTree(":memory:")
        tree.append(AuditEntry(
            event_type=AuditEventType.CONTRADICTION_DETECTED,
            actor="contradiction-detector",
            payload={
                "decision_a_id": "d1",
                "decision_b_id": "d2",
                "verdict": "contradiction",
                "confidence": 0.92,
            },
        ))

        entry = tree.get_entry(0)
        assert entry.payload["decision_a_id"] == "d1"
        assert entry.payload["verdict"] == "contradiction"
        tree.close()


class TestAuditExport:
    """Audit data can be exported for compliance review."""

    def test_bulk_export(self):
        """All entries can be exported at once."""
        tree = MerkleTree(":memory:")
        for i in range(100):
            tree.append(AuditEntry(
                event_type=AuditEventType.DECISION_ADDED,
                actor="test",
                payload={"i": i},
            ))

        entries = tree.get_entries(limit=10000)
        assert len(entries) == 100
        tree.close()

    def test_pagination(self):
        """Entries can be paginated."""
        tree = MerkleTree(":memory:")
        for i in range(50):
            tree.append(AuditEntry(
                event_type=AuditEventType.DECISION_ADDED,
                actor="test",
                payload={"i": i},
            ))

        page1 = tree.get_entries(limit=20, offset=0)
        page2 = tree.get_entries(limit=20, offset=20)
        page3 = tree.get_entries(limit=20, offset=40)

        assert len(page1) == 20
        assert len(page2) == 20
        assert len(page3) == 10
        tree.close()
