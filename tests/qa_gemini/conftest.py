"""Shared fixtures for the Gemini QA test suite.

Provisions real MerkleTree, DashboardState, CLI runner, MCP tools,
CalibrationStore, and TrajectoryEvent factories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.cli.commands import main
from vt_protocol.config import DEFAULT_GOVERNANCE_YAML, ensure_smm_structure
from vt_protocol.dashboard.app import DashboardState, reset_state, set_state
from vt_protocol.decisions.calibration import CalibrationStore
from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Contradiction,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.mcp.server import _sessions
from vt_protocol.observation.trajectory import TrajectoryEvent


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def vt_project(tmp_path):
    """A fully initialized VT Protocol project."""
    root = tmp_path / "vt-test-project"
    root.mkdir()
    (root / ".git").mkdir()
    ensure_smm_structure(root)
    (root / "governance.yaml").write_text(DEFAULT_GOVERNANCE_YAML)
    return root


@pytest.fixture
def merkle_tree():
    """In-memory Merkle tree."""
    tree = MerkleTree(":memory:")
    yield tree
    tree.close()


@pytest.fixture
def calibration_store():
    """In-memory calibration store."""
    store = CalibrationStore()
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clear_mcp_sessions():
    _sessions.clear()
    yield
    _sessions.clear()


@pytest.fixture(autouse=True)
def _reset_dashboard():
    yield
    reset_state()


def make_trajectory_event(
    action: str,
    target: str = "",
    **metadata,
) -> TrajectoryEvent:
    """Helper to create TrajectoryEvent instances."""
    return TrajectoryEvent(action=action, target=target, metadata=metadata)
