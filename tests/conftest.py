"""Shared test fixtures for VT Protocol."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture
def sample_decision() -> Decision:
    return Decision(
        title="Use PostgreSQL for primary datastore",
        content=(
            "We chose PostgreSQL over SQLite because the decision graph requires "
            "concurrent reads and writes from multiple MCP sessions. PostgreSQL's "
            "MVCC handles this natively. Junction table queries for shared-dimension "
            "ranking are well-optimized in Postgres."
        ),
        rationale="Concurrent access, junction table performance, production-ready",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=[Dimension.DATABASE],
        alternatives=["SQLite", "FalkorDB", "Kuzu (archived)"],
        constraints=["Must support async via asyncpg"],
        made_by="claude-code",
        project="vt-protocol",
        source_type=SourceType.AGENT,
    )


@pytest.fixture
def sample_decision_b() -> Decision:
    return Decision(
        title="Use SQLite for local decision storage",
        content=(
            "SQLite with WAL mode provides sufficient performance for single-user "
            "local development. No server process needed."
        ),
        rationale="Simplicity, zero config, embedded",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE],
        alternatives=["PostgreSQL"],
        made_by="developer",
        project="vt-protocol",
        source_type=SourceType.MANUAL,
    )
