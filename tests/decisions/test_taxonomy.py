"""Tests for the 42-dimension taxonomy and auto-detection scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vt_protocol.decisions.models import Dimension
from vt_protocol.decisions.taxonomy import (
    FACETS,
    TAXONOMY,
    DimensionMatch,
    SubDimension,
    get_subdimension,
    get_subdimensions_for,
    scan_project,
    scan_to_core_dimensions,
)


class TestTaxonomyStructure:
    def test_dimension_count(self) -> None:
        # SPEC targets ~42; we have 46 covering all 12 core dimensions
        assert len(TAXONOMY) >= 42

    def test_all_ids_unique(self) -> None:
        ids = [sd.id for sd in TAXONOMY]
        assert len(ids) == len(set(ids))

    def test_all_have_labels(self) -> None:
        for sd in TAXONOMY:
            assert sd.label, f"{sd.id} has no label"

    def test_all_map_to_core_dimension(self) -> None:
        for sd in TAXONOMY:
            assert isinstance(sd.core_dimension, Dimension), f"{sd.id} has bad core_dimension"

    def test_all_have_facet(self) -> None:
        valid_facets = set(FACETS.keys())
        for sd in TAXONOMY:
            assert sd.facet in valid_facets, f"{sd.id} has unknown facet '{sd.facet}'"

    def test_seven_facets(self) -> None:
        assert len(FACETS) == 7

    def test_every_core_dimension_has_subdimensions(self) -> None:
        for dim in Dimension:
            subs = get_subdimensions_for(dim)
            assert len(subs) > 0, f"No sub-dimensions for {dim.value}"


class TestLookup:
    def test_get_subdimension_found(self) -> None:
        sd = get_subdimension("database.relational")
        assert sd is not None
        assert sd.core_dimension == Dimension.DATABASE

    def test_get_subdimension_not_found(self) -> None:
        assert get_subdimension("nonexistent.dim") is None

    def test_get_subdimensions_for_database(self) -> None:
        subs = get_subdimensions_for(Dimension.DATABASE)
        ids = {sd.id for sd in subs}
        assert "database.relational" in ids
        assert "database.nosql" in ids
        assert "database.orm" in ids


class TestAutoDetection:
    def test_detect_python_packages(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "fastapi>=0.100\npsycopg>=3.1\npytest>=8.0\nredis>=5.0\n"
        )
        (tmp_path / ".git").mkdir()  # project root marker
        matches = scan_project(tmp_path)
        dims = {m.sub_dimension.id for m in matches}
        assert "api.rest" in dims  # fastapi
        assert "database.relational" in dims  # psycopg
        assert "quality.unit_testing" in dims  # pytest

    def test_detect_node_packages(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"express": "^4.18", "prisma": "^5.0"},
            "devDependencies": {"jest": "^29", "typescript": "^5"},
        }))
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        dims = {m.sub_dimension.id for m in matches}
        assert "api.rest" in dims  # express
        assert "database.orm" in dims  # prisma
        assert "quality.unit_testing" in dims  # jest

    def test_detect_file_patterns(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        dims = {m.sub_dimension.id for m in matches}
        assert "infra.container" in dims

    def test_detect_directory_patterns(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "migrations").mkdir()
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        dims = {m.sub_dimension.id for m in matches}
        assert "quality.unit_testing" in dims
        assert "database.migration" in dims

    def test_detect_config_files(self, tmp_path: Path) -> None:
        (tmp_path / ".eslintrc").write_text("{}")
        (tmp_path / ".env").write_text("SECRET=x")
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        dims = {m.sub_dimension.id for m in matches}
        assert "quality.linting" in dims
        assert "security.secrets" in dims

    def test_detect_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\ndependencies = ["celery>=5", "structlog>=24"]\n'
        )
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        dims = {m.sub_dimension.id for m in matches}
        assert "comm.queue" in dims  # celery
        assert "obs.logging" in dims  # structlog

    def test_confidence_scales_with_evidence(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("pytest\nfactory-boy\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        testing_matches = [m for m in matches if m.sub_dimension.id == "quality.unit_testing"]
        assert len(testing_matches) == 1
        # pytest package + tests/ directory = 2 evidence items → higher confidence
        assert testing_matches[0].confidence > 0.5

    def test_empty_project(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        matches = scan_project(tmp_path)
        assert matches == []

    def test_scan_to_core_dimensions(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi\npsycopg\n")
        (tmp_path / ".git").mkdir()
        dims = scan_to_core_dimensions(tmp_path)
        assert Dimension.API_STYLE in dims
        assert Dimension.DATABASE in dims
        # Deduplicated
        assert len(dims) == len(set(dims))
