"""Custom exceptions for VT Protocol."""


class VTProtocolError(Exception):
    """Base exception for all VT Protocol errors."""


class SessionNotInitializedError(VTProtocolError):
    """Raised when an MCP tool is called before get_project_context."""


class DecisionNotFoundError(VTProtocolError):
    """Raised when a referenced decision does not exist."""


class ContradictionDetectionError(VTProtocolError):
    """Raised when contradiction detection fails."""


class StoreConnectionError(VTProtocolError):
    """Raised when the database connection fails."""


class MerkleVerificationError(VTProtocolError):
    """Raised when a Merkle tree proof fails verification."""


class GovernanceConfigError(VTProtocolError):
    """Raised when governance.yaml is missing or malformed."""


class LLMProviderError(VTProtocolError):
    """Raised when an LLM provider fails or is misconfigured."""


class AgentAccessDeniedError(VTProtocolError):
    """Raised when an agent attempts an action outside its allowed scope."""
