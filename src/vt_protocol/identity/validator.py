"""Agent identity validator.

Validate agent identity claims, check capabilities against trust levels,
and enforce identity policies.

From SPEC Sprint 24: "Open agent identity specification — validator."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from vt_protocol.identity.spec import (
    AgentIdentity,
    AgentType,
    CapabilityType,
    TrustLevel,
    TRUST_CAPABILITIES,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """A single validation error."""

    field: str = ""
    message: str = ""
    severity: str = "error"  # "error", "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class IdentityValidationResult:
    """Result of validating an agent identity."""

    valid: bool = True
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    capabilities_checked: int = 0
    trust_level_appropriate: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "capabilities_checked": self.capabilities_checked,
            "trust_level_appropriate": self.trust_level_appropriate,
        }


@dataclass
class CapabilityCheckResult:
    """Result of checking if an agent can perform an operation."""

    allowed: bool = False
    agent_id: str = ""
    capability: str = ""
    trust_level: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "trust_level": self.trust_level,
            "reason": self.reason,
        }


class IdentityValidator:
    """Validate agent identities and authorize operations."""

    def __init__(self) -> None:
        self._policy: dict[str, Any] = {}

    def set_policy(self, policy: dict[str, Any]) -> None:
        """Set identity validation policy."""
        self._policy = policy

    def validate_identity(self, identity: AgentIdentity) -> IdentityValidationResult:
        """Validate an agent identity for completeness and consistency."""
        result = IdentityValidationResult()

        # Required fields
        if not identity.agent_id:
            result.errors.append(ValidationError(
                field="agent_id", message="Agent ID is required",
            ))
            result.valid = False

        if not identity.name:
            result.errors.append(ValidationError(
                field="name", message="Agent name is required",
            ))
            result.valid = False

        # Version format
        if identity.version:
            parts = identity.version.split(".")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                result.warnings.append(ValidationError(
                    field="version",
                    message=f"Version '{identity.version}' is not semver",
                    severity="warning",
                ))

        # Trust level vs capabilities consistency
        allowed_by_trust = TRUST_CAPABILITIES.get(identity.trust_level, set())
        for cap_str in identity.capabilities:
            result.capabilities_checked += 1
            try:
                cap = CapabilityType(cap_str)
                if cap not in allowed_by_trust:
                    # Custom capability exceeds trust level
                    result.warnings.append(ValidationError(
                        field="capabilities",
                        message=(
                            f"Capability '{cap_str}' exceeds trust level "
                            f"'{identity.trust_level.value}'"
                        ),
                        severity="warning",
                    ))
            except ValueError:
                # Non-standard capability
                result.warnings.append(ValidationError(
                    field="capabilities",
                    message=f"Non-standard capability: '{cap_str}'",
                    severity="warning",
                ))

        # Organization required for federated agents
        if identity.public_key and not identity.organization:
            result.errors.append(ValidationError(
                field="organization",
                message="Organization is required for federated agents with public keys",
            ))
            result.valid = False

        # Policy-based checks
        if self._policy:
            self._check_policy(identity, result)

        return result

    def check_capability(
        self, identity: AgentIdentity, capability: str,
    ) -> CapabilityCheckResult:
        """Check if an agent is authorized for a specific capability."""
        result = CapabilityCheckResult(
            agent_id=identity.agent_id,
            capability=capability,
            trust_level=identity.trust_level.value,
        )

        if identity.has_capability(capability):
            result.allowed = True
            result.reason = f"Capability '{capability}' is allowed at trust level '{identity.trust_level.value}'"
        else:
            result.allowed = False
            result.reason = (
                f"Capability '{capability}' is not allowed at trust level "
                f"'{identity.trust_level.value}'"
            )

        return result

    def check_operation(
        self,
        identity: AgentIdentity,
        operation: str,
        *,
        target: str = "",
    ) -> CapabilityCheckResult:
        """Check if an agent can perform a specific operation.

        Maps operations to capabilities.
        """
        op_capability_map: dict[str, str] = {
            "read": CapabilityType.READ_DECISIONS.value,
            "write": CapabilityType.WRITE_DECISIONS.value,
            "detect": CapabilityType.DETECT_CONTRADICTIONS.value,
            "resolve": CapabilityType.RESOLVE_CONTRADICTIONS.value,
            "configure": CapabilityType.MODIFY_GOVERNANCE.value,
            "execute": CapabilityType.EXECUTE_CODE.value,
            "external": CapabilityType.ACCESS_EXTERNAL.value,
            "manage": CapabilityType.MANAGE_AGENTS.value,
        }

        capability = op_capability_map.get(operation, operation)
        return self.check_capability(identity, capability)

    def _check_policy(
        self, identity: AgentIdentity, result: IdentityValidationResult,
    ) -> None:
        """Apply custom policy checks."""
        # Minimum trust level policy
        min_trust = self._policy.get("min_trust_level")
        if min_trust:
            trust_order = [t.value for t in TrustLevel]
            current_idx = trust_order.index(identity.trust_level.value)
            required_idx = trust_order.index(min_trust) if min_trust in trust_order else 0
            if current_idx < required_idx:
                result.errors.append(ValidationError(
                    field="trust_level",
                    message=f"Trust level '{identity.trust_level.value}' below policy minimum '{min_trust}'",
                ))
                result.valid = False
                result.trust_level_appropriate = False

        # Required organization policy
        required_org = self._policy.get("required_organization")
        if required_org and identity.organization != required_org:
            result.errors.append(ValidationError(
                field="organization",
                message=f"Organization '{identity.organization}' does not match required '{required_org}'",
            ))
            result.valid = False

        # Blocked agent types
        blocked_types = self._policy.get("blocked_agent_types", [])
        if identity.agent_type.value in blocked_types:
            result.errors.append(ValidationError(
                field="agent_type",
                message=f"Agent type '{identity.agent_type.value}' is blocked by policy",
            ))
            result.valid = False
