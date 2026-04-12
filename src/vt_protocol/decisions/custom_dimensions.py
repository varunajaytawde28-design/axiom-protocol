"""Custom dimension taxonomies — project-specific dimensions.

Extends the 12 core dimensions with project-specific dimensions
defined in governance.yaml. Supports detection patterns for
auto-tagging decisions.

From SPEC Phase 3: "Custom dimension taxonomies — teams define
project-specific dimensions with detection patterns."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from vt_protocol.decisions.models import Dimension

logger = logging.getLogger(__name__)


@dataclass
class DetectionPattern:
    """A pattern for auto-detecting a custom dimension."""

    type: str = "glob"  # glob, regex, keyword
    pattern: str = ""
    weight: float = 1.0

    def matches(self, text: str) -> bool:
        """Check if the pattern matches the given text."""
        if self.type == "keyword":
            return self.pattern.lower() in text.lower()
        elif self.type == "regex":
            try:
                return bool(re.search(self.pattern, text, re.IGNORECASE))
            except re.error:
                return False
        elif self.type == "glob":
            # Simple glob: * matches anything
            escaped = re.escape(self.pattern).replace(r"\*", ".*")
            try:
                return bool(re.search(escaped, text, re.IGNORECASE))
            except re.error:
                return False
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "pattern": self.pattern,
            "weight": self.weight,
        }


@dataclass
class CustomDimension:
    """A project-specific dimension definition."""

    name: str
    display_name: str = ""
    description: str = ""
    detection_patterns: list[DetectionPattern] = field(default_factory=list)
    parent_dimension: str | None = None  # Optional: extends a core dimension

    @property
    def effective_display_name(self) -> str:
        return self.display_name or self.name.replace("_", " ").title()

    def matches_content(self, content: str) -> float:
        """Score how well content matches this dimension's patterns.

        Returns a weighted score (0.0 = no match, higher = better match).
        """
        total = 0.0
        for pattern in self.detection_patterns:
            if pattern.matches(content):
                total += pattern.weight
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.effective_display_name,
            "description": self.description,
            "detection_patterns": [p.to_dict() for p in self.detection_patterns],
            "parent_dimension": self.parent_dimension,
        }


@dataclass
class DimensionTaxonomy:
    """Complete dimension taxonomy: core + custom dimensions."""

    custom_dimensions: list[CustomDimension] = field(default_factory=list)

    @property
    def core_dimensions(self) -> list[str]:
        """List of built-in core dimensions."""
        return [d.value for d in Dimension]

    @property
    def all_dimension_names(self) -> list[str]:
        """All dimension names (core + custom)."""
        return self.core_dimensions + [cd.name for cd in self.custom_dimensions]

    def get_custom(self, name: str) -> CustomDimension | None:
        """Look up a custom dimension by name."""
        for cd in self.custom_dimensions:
            if cd.name == name:
                return cd
        return None

    def is_valid_dimension(self, name: str) -> bool:
        """Check if a dimension name is valid (core or custom)."""
        return name in self.all_dimension_names

    def add_custom(self, dimension: CustomDimension) -> None:
        """Add a custom dimension to the taxonomy."""
        if self.get_custom(dimension.name) is not None:
            raise ValueError(f"Custom dimension '{dimension.name}' already exists")
        self.custom_dimensions.append(dimension)

    def remove_custom(self, name: str) -> bool:
        """Remove a custom dimension. Returns True if found."""
        for i, cd in enumerate(self.custom_dimensions):
            if cd.name == name:
                self.custom_dimensions.pop(i)
                return True
        return False

    def auto_detect(self, content: str) -> list[tuple[str, float]]:
        """Auto-detect dimensions from content.

        Returns list of (dimension_name, score) sorted by score descending.
        """
        results: list[tuple[str, float]] = []
        for cd in self.custom_dimensions:
            score = cd.matches_content(content)
            if score > 0:
                results.append((cd.name, score))
        return sorted(results, key=lambda x: x[1], reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_dimensions": self.core_dimensions,
            "custom_dimensions": [cd.to_dict() for cd in self.custom_dimensions],
            "total": len(self.all_dimension_names),
        }


def load_taxonomy_from_dict(data: dict[str, Any]) -> DimensionTaxonomy:
    """Load a taxonomy from a governance.yaml dimensions section."""
    taxonomy = DimensionTaxonomy()
    custom_data = data.get("custom_dimensions", data.get("dimensions", []))

    if isinstance(custom_data, list):
        for item in custom_data:
            if isinstance(item, dict):
                patterns = []
                for p in item.get("detection_patterns", item.get("patterns", [])):
                    if isinstance(p, dict):
                        patterns.append(DetectionPattern(
                            type=p.get("type", "keyword"),
                            pattern=p.get("pattern", ""),
                            weight=p.get("weight", 1.0),
                        ))
                    elif isinstance(p, str):
                        patterns.append(DetectionPattern(type="keyword", pattern=p))

                taxonomy.add_custom(CustomDimension(
                    name=item.get("name", ""),
                    display_name=item.get("display_name", ""),
                    description=item.get("description", ""),
                    detection_patterns=patterns,
                    parent_dimension=item.get("parent_dimension"),
                ))

    return taxonomy
