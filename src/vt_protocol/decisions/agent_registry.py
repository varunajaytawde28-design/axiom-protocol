"""Agent registry — register AI agents with type, version, capabilities, permissions.

Every AI agent must be registered before it can make architectural decisions.
The registry tracks what each agent is allowed to do and validates
MCP permission requests.

From SPEC Phase 3: "Agent registry — every AI agent registered with type,
version, capabilities, permissions, owner."
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
    """Types of AI agents."""

    CODING = "coding"
    REVIEW = "review"
    SCAN = "scan"
    ORCHESTRATOR = "orchestrator"
    CUSTOM = "custom"


class Permission(str, Enum):
    """Agent permissions for architectural operations."""

    READ_DECISIONS = "read_decisions"
    WRITE_DECISIONS = "write_decisions"
    READ_CONTRADICTIONS = "read_contradictions"
    RESOLVE_CONTRADICTIONS = "resolve_contradictions"
    MODIFY_CODE = "modify_code"
    MODIFY_CONFIG = "modify_config"
    MODIFY_DEPS = "modify_deps"
    EXECUTE_TESTS = "execute_tests"
    DEPLOY = "deploy"


# Default permissions by agent type
DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    AgentType.CODING.value: [
        Permission.READ_DECISIONS.value,
        Permission.WRITE_DECISIONS.value,
        Permission.MODIFY_CODE.value,
        Permission.EXECUTE_TESTS.value,
    ],
    AgentType.REVIEW.value: [
        Permission.READ_DECISIONS.value,
        Permission.READ_CONTRADICTIONS.value,
    ],
    AgentType.SCAN.value: [
        Permission.READ_DECISIONS.value,
        Permission.WRITE_DECISIONS.value,
    ],
    AgentType.ORCHESTRATOR.value: [
        Permission.READ_DECISIONS.value,
        Permission.WRITE_DECISIONS.value,
        Permission.READ_CONTRADICTIONS.value,
        Permission.RESOLVE_CONTRADICTIONS.value,
    ],
    AgentType.CUSTOM.value: [
        Permission.READ_DECISIONS.value,
    ],
}


@dataclass
class AgentRegistration:
    """A registered AI agent."""

    agent_id: str = field(default_factory=lambda: uuid4().hex[:16])
    name: str = ""
    agent_type: str = AgentType.CUSTOM.value
    version: str = "0.0.0"
    capabilities: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    owner: str = ""
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_permission(self, permission: str) -> bool:
        """Check if the agent has a specific permission."""
        return permission in self.permissions

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "version": self.version,
            "capabilities": self.capabilities,
            "permissions": self.permissions,
            "owner": self.owner,
            "registered_at": self.registered_at.isoformat(),
            "active": self.active,
            "metadata": self.metadata,
        }


@dataclass
class PermissionCheck:
    """Result of a permission validation."""

    agent_id: str
    permission: str
    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "permission": self.permission,
            "allowed": self.allowed,
            "reason": self.reason,
        }


class AgentRegistry:
    """Registry of AI agents with permission management."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentRegistration] = {}

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    @property
    def active_agents(self) -> list[AgentRegistration]:
        return [a for a in self._agents.values() if a.active]

    def register(
        self,
        name: str,
        agent_type: str = AgentType.CUSTOM.value,
        *,
        version: str = "0.0.0",
        capabilities: list[str] | None = None,
        permissions: list[str] | None = None,
        owner: str = "",
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRegistration:
        """Register a new agent.

        If permissions not specified, uses defaults for the agent type.
        """
        effective_perms = permissions
        if effective_perms is None:
            effective_perms = list(DEFAULT_PERMISSIONS.get(agent_type, DEFAULT_PERMISSIONS[AgentType.CUSTOM.value]))

        agent = AgentRegistration(
            agent_id=agent_id or uuid4().hex[:16],
            name=name,
            agent_type=agent_type,
            version=version,
            capabilities=capabilities or [],
            permissions=effective_perms,
            owner=owner,
            metadata=metadata or {},
        )
        self._agents[agent.agent_id] = agent
        logger.info("Registered agent %s (%s) type=%s", agent.name, agent.agent_id, agent.agent_type)
        return agent

    def get(self, agent_id: str) -> AgentRegistration | None:
        """Look up an agent by ID."""
        return self._agents.get(agent_id)

    def deactivate(self, agent_id: str) -> bool:
        """Deactivate an agent. Returns True if found."""
        agent = self._agents.get(agent_id)
        if agent:
            agent.active = False
            return True
        return False

    def check_permission(
        self,
        agent_id: str,
        permission: str,
    ) -> PermissionCheck:
        """Validate whether an agent has a specific permission."""
        agent = self._agents.get(agent_id)
        if agent is None:
            return PermissionCheck(
                agent_id=agent_id,
                permission=permission,
                allowed=False,
                reason="Agent not registered",
            )
        if not agent.active:
            return PermissionCheck(
                agent_id=agent_id,
                permission=permission,
                allowed=False,
                reason="Agent is deactivated",
            )
        allowed = agent.has_permission(permission)
        return PermissionCheck(
            agent_id=agent_id,
            permission=permission,
            allowed=allowed,
            reason="" if allowed else f"Permission '{permission}' not granted",
        )

    def update_permissions(
        self,
        agent_id: str,
        permissions: list[str],
    ) -> bool:
        """Update an agent's permissions. Returns True if found."""
        agent = self._agents.get(agent_id)
        if agent:
            agent.permissions = permissions
            return True
        return False

    def list_agents(
        self,
        *,
        agent_type: str | None = None,
        active_only: bool = True,
    ) -> list[AgentRegistration]:
        """List registered agents with optional filters."""
        agents = list(self._agents.values())
        if active_only:
            agents = [a for a in agents if a.active]
        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]
        return agents

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.agent_count,
            "active": len(self.active_agents),
            "agents": [a.to_dict() for a in self._agents.values()],
        }
