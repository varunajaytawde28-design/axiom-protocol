"""Open agent identity specification.

Define a universal agent identity schema — UUID, type, version,
capabilities, trust level. Agents across organizations can identify
themselves consistently for governance and federation.

From SPEC Sprint 24: "Open agent identity specification."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class AgentType(str, Enum):
    """Standard agent type taxonomy."""

    CODING = "coding"
    REVIEW = "review"
    SCAN = "scan"
    ORCHESTRATOR = "orchestrator"
    CUSTOM = "custom"
    HUMAN = "human"


class TrustLevel(str, Enum):
    """Trust levels for agent operations."""

    UNTRUSTED = "untrusted"
    BASIC = "basic"
    VERIFIED = "verified"
    TRUSTED = "trusted"
    PRIVILEGED = "privileged"


class CapabilityType(str, Enum):
    """Standard agent capabilities."""

    READ_DECISIONS = "read_decisions"
    WRITE_DECISIONS = "write_decisions"
    DETECT_CONTRADICTIONS = "detect_contradictions"
    RESOLVE_CONTRADICTIONS = "resolve_contradictions"
    MODIFY_GOVERNANCE = "modify_governance"
    EXECUTE_CODE = "execute_code"
    ACCESS_EXTERNAL = "access_external"
    MANAGE_AGENTS = "manage_agents"


# Trust level → allowed capabilities
TRUST_CAPABILITIES: dict[TrustLevel, set[CapabilityType]] = {
    TrustLevel.UNTRUSTED: {CapabilityType.READ_DECISIONS},
    TrustLevel.BASIC: {
        CapabilityType.READ_DECISIONS,
        CapabilityType.WRITE_DECISIONS,
    },
    TrustLevel.VERIFIED: {
        CapabilityType.READ_DECISIONS,
        CapabilityType.WRITE_DECISIONS,
        CapabilityType.DETECT_CONTRADICTIONS,
    },
    TrustLevel.TRUSTED: {
        CapabilityType.READ_DECISIONS,
        CapabilityType.WRITE_DECISIONS,
        CapabilityType.DETECT_CONTRADICTIONS,
        CapabilityType.RESOLVE_CONTRADICTIONS,
        CapabilityType.EXECUTE_CODE,
    },
    TrustLevel.PRIVILEGED: set(CapabilityType),
}


@dataclass
class AgentIdentity:
    """Universal agent identity."""

    agent_id: str = field(default_factory=lambda: uuid4().hex[:16])
    name: str = ""
    agent_type: AgentType = AgentType.CUSTOM
    version: str = "1.0.0"
    organization: str = ""
    trust_level: TrustLevel = TrustLevel.BASIC
    capabilities: list[str] = field(default_factory=list)
    public_key: str = ""  # for cross-org federation
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def allowed_capabilities(self) -> set[str]:
        """Get capabilities allowed by trust level."""
        base = {c.value for c in TRUST_CAPABILITIES.get(self.trust_level, set())}
        return base | set(self.capabilities)

    def has_capability(self, capability: str) -> bool:
        """Check if agent has a specific capability."""
        return capability in self.allowed_capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "agent_type": self.agent_type.value,
            "version": self.version,
            "organization": self.organization,
            "trust_level": self.trust_level.value,
            "capabilities": sorted(self.allowed_capabilities),
            "public_key": self.public_key[:20] + "..." if len(self.public_key) > 20 else self.public_key,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AgentRegistry:
    """Registry of known agents."""

    agents: dict[str, AgentIdentity] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.agents)

    def register(self, agent: AgentIdentity) -> None:
        self.agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AgentIdentity | None:
        return self.agents.get(agent_id)

    def find_by_type(self, agent_type: AgentType) -> list[AgentIdentity]:
        return [a for a in self.agents.values() if a.agent_type == agent_type]

    def find_by_org(self, organization: str) -> list[AgentIdentity]:
        return [a for a in self.agents.values() if a.organization == organization]

    def find_by_capability(self, capability: str) -> list[AgentIdentity]:
        return [a for a in self.agents.values() if a.has_capability(capability)]

    def remove(self, agent_id: str) -> bool:
        if agent_id in self.agents:
            del self.agents[agent_id]
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "agents": {k: v.to_dict() for k, v in self.agents.items()},
        }
