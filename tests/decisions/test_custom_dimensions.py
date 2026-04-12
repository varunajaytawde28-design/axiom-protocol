"""Tests for custom dimension taxonomies."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.custom_dimensions import (
    CustomDimension,
    DetectionPattern,
    DimensionTaxonomy,
    load_taxonomy_from_dict,
)
from vt_protocol.decisions.models import Dimension


# ---------------------------------------------------------------------------
# DetectionPattern
# ---------------------------------------------------------------------------


class TestDetectionPattern:
    def test_keyword_match(self) -> None:
        p = DetectionPattern(type="keyword", pattern="redis")
        assert p.matches("Use Redis for caching") is True
        assert p.matches("Use PostgreSQL") is False

    def test_keyword_case_insensitive(self) -> None:
        p = DetectionPattern(type="keyword", pattern="redis")
        assert p.matches("REDIS cluster") is True

    def test_regex_match(self) -> None:
        p = DetectionPattern(type="regex", pattern=r"v\d+\.\d+")
        assert p.matches("Upgrade to v2.0") is True
        assert p.matches("No version") is False

    def test_regex_invalid_pattern(self) -> None:
        p = DetectionPattern(type="regex", pattern="[invalid")
        assert p.matches("test") is False

    def test_glob_match(self) -> None:
        p = DetectionPattern(type="glob", pattern="**/models/**")
        assert p.matches("src/models/user.py") is True
        assert p.matches("src/utils/helper.py") is False

    def test_unknown_type(self) -> None:
        p = DetectionPattern(type="unknown", pattern="test")
        assert p.matches("test") is False

    def test_to_dict(self) -> None:
        p = DetectionPattern(type="keyword", pattern="redis", weight=2.0)
        d = p.to_dict()
        assert d["type"] == "keyword"
        assert d["weight"] == 2.0


# ---------------------------------------------------------------------------
# CustomDimension
# ---------------------------------------------------------------------------


class TestCustomDimension:
    def test_effective_display_name(self) -> None:
        cd = CustomDimension(name="payment_gateway")
        assert cd.effective_display_name == "Payment Gateway"

    def test_custom_display_name(self) -> None:
        cd = CustomDimension(name="pg", display_name="Payment Gateway")
        assert cd.effective_display_name == "Payment Gateway"

    def test_matches_content_keyword(self) -> None:
        cd = CustomDimension(
            name="payments",
            detection_patterns=[
                DetectionPattern(type="keyword", pattern="stripe", weight=1.0),
                DetectionPattern(type="keyword", pattern="payment", weight=0.5),
            ],
        )
        assert cd.matches_content("Use Stripe for payments") == 1.5
        assert cd.matches_content("Use Redis") == 0.0

    def test_matches_content_partial(self) -> None:
        cd = CustomDimension(
            name="ml",
            detection_patterns=[
                DetectionPattern(type="keyword", pattern="tensorflow"),
                DetectionPattern(type="keyword", pattern="pytorch"),
            ],
        )
        assert cd.matches_content("Use tensorflow") == 1.0

    def test_to_dict(self) -> None:
        cd = CustomDimension(
            name="payments",
            description="Payment processing",
            parent_dimension="security",
        )
        d = cd.to_dict()
        assert d["name"] == "payments"
        assert d["parent_dimension"] == "security"


# ---------------------------------------------------------------------------
# DimensionTaxonomy
# ---------------------------------------------------------------------------


class TestDimensionTaxonomy:
    def test_core_dimensions(self) -> None:
        t = DimensionTaxonomy()
        assert "database" in t.core_dimensions
        assert "auth" in t.core_dimensions
        assert len(t.core_dimensions) == 12

    def test_all_dimension_names_with_custom(self) -> None:
        t = DimensionTaxonomy(custom_dimensions=[
            CustomDimension(name="payments"),
        ])
        names = t.all_dimension_names
        assert "database" in names
        assert "payments" in names

    def test_is_valid_core(self) -> None:
        t = DimensionTaxonomy()
        assert t.is_valid_dimension("database") is True
        assert t.is_valid_dimension("imaginary") is False

    def test_is_valid_custom(self) -> None:
        t = DimensionTaxonomy(custom_dimensions=[
            CustomDimension(name="payments"),
        ])
        assert t.is_valid_dimension("payments") is True

    def test_add_custom(self) -> None:
        t = DimensionTaxonomy()
        t.add_custom(CustomDimension(name="payments"))
        assert t.get_custom("payments") is not None

    def test_add_duplicate_raises(self) -> None:
        t = DimensionTaxonomy()
        t.add_custom(CustomDimension(name="payments"))
        with pytest.raises(ValueError, match="already exists"):
            t.add_custom(CustomDimension(name="payments"))

    def test_remove_custom(self) -> None:
        t = DimensionTaxonomy()
        t.add_custom(CustomDimension(name="payments"))
        assert t.remove_custom("payments") is True
        assert t.get_custom("payments") is None

    def test_remove_unknown(self) -> None:
        t = DimensionTaxonomy()
        assert t.remove_custom("nonexistent") is False

    def test_auto_detect(self) -> None:
        t = DimensionTaxonomy(custom_dimensions=[
            CustomDimension(
                name="payments",
                detection_patterns=[
                    DetectionPattern(type="keyword", pattern="stripe", weight=2.0),
                ],
            ),
            CustomDimension(
                name="analytics",
                detection_patterns=[
                    DetectionPattern(type="keyword", pattern="analytics", weight=1.0),
                ],
            ),
        ])
        results = t.auto_detect("Integrate Stripe for payment analytics")
        assert len(results) == 2
        # Stripe has higher weight, should be first
        assert results[0][0] == "payments"
        assert results[0][1] == 2.0

    def test_auto_detect_no_match(self) -> None:
        t = DimensionTaxonomy(custom_dimensions=[
            CustomDimension(
                name="payments",
                detection_patterns=[DetectionPattern(type="keyword", pattern="stripe")],
            ),
        ])
        results = t.auto_detect("Use PostgreSQL for data storage")
        assert results == []

    def test_to_dict(self) -> None:
        t = DimensionTaxonomy(custom_dimensions=[
            CustomDimension(name="payments"),
        ])
        d = t.to_dict()
        assert d["total"] == 13  # 12 core + 1 custom


# ---------------------------------------------------------------------------
# load_taxonomy_from_dict
# ---------------------------------------------------------------------------


class TestLoadTaxonomyFromDict:
    def test_load_from_list(self) -> None:
        data = {
            "custom_dimensions": [
                {
                    "name": "payments",
                    "display_name": "Payment Gateway",
                    "detection_patterns": [
                        {"type": "keyword", "pattern": "stripe"},
                        {"type": "keyword", "pattern": "payment", "weight": 0.5},
                    ],
                },
            ],
        }
        t = load_taxonomy_from_dict(data)
        assert t.get_custom("payments") is not None
        cd = t.get_custom("payments")
        assert cd.display_name == "Payment Gateway"
        assert len(cd.detection_patterns) == 2

    def test_load_string_patterns(self) -> None:
        data = {
            "dimensions": [
                {
                    "name": "ml",
                    "patterns": ["tensorflow", "pytorch"],
                },
            ],
        }
        t = load_taxonomy_from_dict(data)
        cd = t.get_custom("ml")
        assert cd is not None
        assert len(cd.detection_patterns) == 2
        assert cd.detection_patterns[0].type == "keyword"

    def test_load_with_parent(self) -> None:
        data = {
            "custom_dimensions": [
                {
                    "name": "payment_security",
                    "parent_dimension": "security",
                    "detection_patterns": [],
                },
            ],
        }
        t = load_taxonomy_from_dict(data)
        cd = t.get_custom("payment_security")
        assert cd.parent_dimension == "security"

    def test_load_empty(self) -> None:
        t = load_taxonomy_from_dict({})
        assert len(t.custom_dimensions) == 0
