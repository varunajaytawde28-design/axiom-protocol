"""Performance test: Decision Throughput.

Measures time to create, write, and read decisions at scale.
Uses real Decision model and file I/O.
"""

from __future__ import annotations

import json
import time

import pytest

from vt_protocol.decisions.models import Decision, DecisionType, Dimension, SourceType

from tests.helpers.decision_factory import make_decision
from tests.helpers.repo_factory import create_project, write_decision

pytestmark = [pytest.mark.performance, pytest.mark.slow]


class TestDecisionCreation:
    """Benchmark Decision model creation."""

    def test_create_1000_decisions(self):
        """Creating 1000 Decision objects should take < 2 seconds."""
        start = time.perf_counter()
        decisions = []
        for i in range(1000):
            d = make_decision(
                title=f"Decision {i}",
                content=f"Content for decision {i} with enough text for confidence.",
                dimensions=[list(Dimension)[i % 12]],
            )
            decisions.append(d)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"Creating 1000 decisions took {elapsed:.2f}s"
        assert len(decisions) == 1000


class TestDecisionFileIO:
    """Benchmark file write/read of decisions."""

    def test_write_500_decisions(self, tmp_path):
        """Writing 500 decisions to disk should take < 5 seconds."""
        root = create_project(tmp_path)
        decisions = [
            make_decision(
                title=f"Decision {i}",
                dimensions=[list(Dimension)[i % 12]],
            )
            for i in range(500)
        ]

        start = time.perf_counter()
        for i, d in enumerate(decisions):
            write_decision(root, d, filename=f"d-{i:03d}.json")
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"Writing 500 decisions took {elapsed:.2f}s"

    def test_read_500_decisions(self, tmp_path):
        """Reading 500 decisions from disk should take < 5 seconds."""
        root = create_project(tmp_path)
        for i in range(500):
            d = make_decision(title=f"Decision {i}")
            write_decision(root, d, filename=f"d-{i:03d}.json")

        start = time.perf_counter()
        decisions_dir = root / ".smm" / "decisions"
        loaded = []
        for fp in sorted(decisions_dir.glob("*.json")):
            data = json.loads(fp.read_text())
            loaded.append(Decision(**data))
        elapsed = time.perf_counter() - start

        assert len(loaded) == 500
        assert elapsed < 5.0, f"Reading 500 decisions took {elapsed:.2f}s"


class TestCLIThroughput:
    """Benchmark CLI commands at scale."""

    def test_check_100_decisions(self, tmp_path):
        """vt check with 100 decisions should take < 5 seconds."""
        from click.testing import CliRunner
        from vt_protocol.cli.commands import main

        root = create_project(tmp_path)
        for i in range(100):
            d = make_decision(title=f"Decision {i}", dimensions=[list(Dimension)[i % 12]])
            write_decision(root, d, filename=f"d-{i:03d}.json")

        runner = CliRunner()
        start = time.perf_counter()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        elapsed = time.perf_counter() - start

        assert result.exit_code == 0
        assert elapsed < 5.0, f"check with 100 decisions took {elapsed:.2f}s"
