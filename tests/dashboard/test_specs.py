"""Tests for PM view — Living Specifications."""

from __future__ import annotations

import pytest

from vt_protocol.dashboard.specs import (
    CoverageReport,
    CoverageStatus,
    Requirement,
    RequirementCoverage,
    Specification,
    SpecStore,
    compute_coverage,
    extract_requirements,
)
from vt_protocol.decisions.models import Decision, Dimension, SourceType


def _make_decision(title: str, content: str, **kwargs) -> Decision:
    defaults = dict(
        title=title,
        content=content,
        rationale="test",
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
        dimensions=[Dimension.DATABASE],
    )
    defaults.update(kwargs)
    return Decision(**defaults)


class TestRequirement:
    def test_to_dict(self) -> None:
        r = Requirement(text="The system must support login", section="Auth", index=0)
        d = r.to_dict()
        assert d["text"] == "The system must support login"
        assert d["section"] == "Auth"

    def test_default_id(self) -> None:
        r = Requirement(text="test")
        assert len(r.id) == 12


class TestSpecification:
    def test_empty(self) -> None:
        spec = Specification(title="Empty")
        assert spec.requirement_count == 0

    def test_to_dict(self) -> None:
        spec = Specification(
            title="Test PRD",
            requirements=[Requirement(text="req1"), Requirement(text="req2")],
        )
        d = spec.to_dict()
        assert d["title"] == "Test PRD"
        assert d["requirement_count"] == 2


class TestExtractRequirements:
    def test_numbered_items(self) -> None:
        text = "1. The system must support login\n2. Users should see a dashboard"
        spec = extract_requirements(text, title="Test")
        assert spec.requirement_count == 2

    def test_bullet_items(self) -> None:
        text = "- Must handle 1000 concurrent users\n- Should support CSV export"
        spec = extract_requirements(text, title="Test")
        assert spec.requirement_count == 2

    def test_modal_verbs(self) -> None:
        text = "The API must return JSON responses. The system should log all errors."
        spec = extract_requirements(text, title="Test")
        assert spec.requirement_count >= 1

    def test_markdown_sections(self) -> None:
        text = "## Auth\n1. Login support\n## API\n1. REST endpoints"
        spec = extract_requirements(text, title="PRD")
        sections = {r.section for r in spec.requirements}
        assert "Auth" in sections
        assert "API" in sections

    def test_empty_text(self) -> None:
        spec = extract_requirements("", title="Empty")
        assert spec.requirement_count == 0

    def test_short_items_filtered(self) -> None:
        text = "- AB\n- This is a real requirement"
        spec = extract_requirements(text, title="Test")
        # "AB" is too short (< 5 chars)
        texts = [r.text for r in spec.requirements]
        assert "AB" not in texts

    def test_default_title(self) -> None:
        spec = extract_requirements("- Must do thing")
        assert spec.title == "Untitled Spec"


class TestRequirementCoverage:
    def test_to_dict(self) -> None:
        rc = RequirementCoverage(
            requirement=Requirement(text="test"),
            status=CoverageStatus.IMPLEMENTED,
            similarity_score=0.85,
        )
        d = rc.to_dict()
        assert d["status"] == "implemented"
        assert d["similarity_score"] == 0.85


class TestCoverageReport:
    def test_empty(self) -> None:
        report = CoverageReport()
        assert report.total == 0
        assert report.coverage_percent == 0.0

    def test_counts(self) -> None:
        report = CoverageReport(coverages=[
            RequirementCoverage(requirement=Requirement(text="a"), status=CoverageStatus.IMPLEMENTED),
            RequirementCoverage(requirement=Requirement(text="b"), status=CoverageStatus.PARTIAL),
            RequirementCoverage(requirement=Requirement(text="c"), status=CoverageStatus.NOT_STARTED),
            RequirementCoverage(requirement=Requirement(text="d"), status=CoverageStatus.DIVERGED),
        ])
        assert report.implemented_count == 1
        assert report.partial_count == 1
        assert report.not_started_count == 1
        assert report.diverged_count == 1
        assert report.total == 4

    def test_coverage_percent(self) -> None:
        report = CoverageReport(coverages=[
            RequirementCoverage(requirement=Requirement(text="a"), status=CoverageStatus.IMPLEMENTED),
            RequirementCoverage(requirement=Requirement(text="b"), status=CoverageStatus.IMPLEMENTED),
        ])
        assert report.coverage_percent == 100.0


class TestComputeCoverage:
    def test_no_decisions(self) -> None:
        spec = Specification(requirements=[Requirement(text="Use PostgreSQL for data storage")])
        report = compute_coverage(spec, [])
        assert report.coverages[0].status == CoverageStatus.NOT_STARTED

    def test_matching_decision(self) -> None:
        spec = Specification(requirements=[
            Requirement(text="Use PostgreSQL for data storage"),
        ])
        decisions = [_make_decision("Use PostgreSQL", "PostgreSQL for data storage")]
        report = compute_coverage(spec, decisions)
        assert report.coverages[0].status in (CoverageStatus.IMPLEMENTED, CoverageStatus.PARTIAL)
        assert report.coverages[0].matched_decision_title == "Use PostgreSQL"

    def test_no_match(self) -> None:
        spec = Specification(requirements=[
            Requirement(text="Support real-time video streaming"),
        ])
        decisions = [_make_decision("Use PostgreSQL", "Database choice")]
        report = compute_coverage(spec, decisions)
        assert report.coverages[0].status == CoverageStatus.NOT_STARTED

    def test_custom_similarity_fn(self) -> None:
        spec = Specification(requirements=[Requirement(text="anything")])
        decisions = [_make_decision("Match", "Match")]

        def always_high(a: str, b: str) -> float:
            return 0.95

        report = compute_coverage(spec, decisions, similarity_fn=always_high)
        assert report.coverages[0].status == CoverageStatus.IMPLEMENTED

    def test_multiple_requirements(self) -> None:
        spec = Specification(requirements=[
            Requirement(text="Use PostgreSQL database"),
            Requirement(text="Deploy with Docker containers"),
        ])
        decisions = [
            _make_decision("Use PostgreSQL", "PostgreSQL for all data"),
            _make_decision("Docker Deployment", "Deploy with Docker containers"),
        ]
        report = compute_coverage(spec, decisions)
        assert report.total == 2


class TestSpecStore:
    def test_add_and_get(self) -> None:
        store = SpecStore()
        spec = Specification(title="Test")
        sid = store.add(spec)
        assert store.get(sid) is spec
        assert store.count == 1

    def test_list(self) -> None:
        store = SpecStore()
        store.add(Specification(title="A"))
        store.add(Specification(title="B"))
        assert len(store.list_specs()) == 2

    def test_get_missing(self) -> None:
        store = SpecStore()
        assert store.get("nonexistent") is None
