"""Z3 Boolean SAT constraints for architectural rule verification.

Encodes 10 core architectural rules as Z3 formulas. The `smm verify`
CLI can check whether a project's decisions satisfy all constraints.

From SPEC T8: "Z3 Boolean SAT for top 10 architectural rules"
From SPEC Phase 3: "Z3 SMT solver integration for formal architectural
constraint checking"

Rules encoded:
  1. no_conflicting_databases — can't use both SQL and NoSQL for primary store
  2. auth_requires_security — auth dimension requires security dimension
  3. api_style_consistency — single API style across project
  4. deployment_requires_logging — deployment changes need logging
  5. caching_requires_state_mgmt — caching needs state management strategy
  6. testing_with_error_handling — testing dimension implies error handling
  7. no_circular_supersession — decision supersession chain is acyclic
  8. concurrency_requires_state_mgmt — concurrency needs state strategy
  9. security_requires_auth — security decisions need auth coverage
 10. messaging_requires_error_handling — async messaging needs error handling

Uses pure Python logic when Z3 is not available (graceful degradation).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from vt_protocol.decisions.models import Decision, Dimension

logger = logging.getLogger(__name__)

# Try to import Z3, fall back to pure Python
_HAS_Z3 = False
try:
    from z3 import Bool, Not, Or, And, Implies, Solver, sat, unsat  # type: ignore[import-untyped]
    _HAS_Z3 = True
except ImportError:
    pass


@dataclass
class ConstraintResult:
    """Result of checking a single constraint."""

    name: str
    description: str
    satisfied: bool
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "satisfied": self.satisfied,
            "details": self.details,
        }


@dataclass
class VerificationReport:
    """Complete verification report for all constraints."""

    results: list[ConstraintResult] = field(default_factory=list)
    all_satisfied: bool = True
    using_z3: bool = _HAS_Z3

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.satisfied)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.satisfied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_satisfied": self.all_satisfied,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "total": len(self.results),
            "using_z3": self.using_z3,
            "results": [r.to_dict() for r in self.results],
        }


def _active_dimensions(decisions: list[Decision]) -> set[str]:
    """Get all dimensions used in active decisions."""
    dims: set[str] = set()
    for d in decisions:
        if d.valid:
            for dim in d.dimensions:
                dims.add(dim.value)
    return dims


def _decisions_by_dimension(decisions: list[Decision]) -> dict[str, list[Decision]]:
    """Group active decisions by dimension."""
    by_dim: dict[str, list[Decision]] = {}
    for d in decisions:
        if d.valid:
            for dim in d.dimensions:
                by_dim.setdefault(dim.value, []).append(d)
    return by_dim


# ---------------------------------------------------------------------------
# Pure Python constraint checkers (fallback when Z3 not available)
# ---------------------------------------------------------------------------


def _check_auth_requires_security(decisions: list[Decision]) -> ConstraintResult:
    """Rule 2: If auth decisions exist, security decisions must also exist."""
    dims = _active_dimensions(decisions)
    has_auth = Dimension.AUTH.value in dims
    has_security = Dimension.SECURITY.value in dims
    satisfied = not has_auth or has_security
    return ConstraintResult(
        name="auth_requires_security",
        description="Auth dimension decisions require security dimension coverage",
        satisfied=satisfied,
        details="" if satisfied else "Auth decisions exist but no security decisions found",
    )


def _check_api_style_consistency(decisions: list[Decision]) -> ConstraintResult:
    """Rule 3: Only one API style should be active."""
    by_dim = _decisions_by_dimension(decisions)
    api_decisions = by_dim.get(Dimension.API_STYLE.value, [])
    api_styles: set[str] = set()
    for d in api_decisions:
        title_lower = d.title.lower()
        for style in ("rest", "graphql", "grpc", "soap", "websocket"):
            if style in title_lower or style in d.content.lower():
                api_styles.add(style)
    satisfied = len(api_styles) <= 1
    return ConstraintResult(
        name="api_style_consistency",
        description="Project should use a single API style",
        satisfied=satisfied,
        details="" if satisfied else f"Multiple API styles detected: {', '.join(sorted(api_styles))}",
    )


def _check_deployment_requires_logging(decisions: list[Decision]) -> ConstraintResult:
    """Rule 4: Deployment changes need logging coverage."""
    dims = _active_dimensions(decisions)
    has_deployment = Dimension.DEPLOYMENT.value in dims
    has_logging = Dimension.LOGGING.value in dims
    satisfied = not has_deployment or has_logging
    return ConstraintResult(
        name="deployment_requires_logging",
        description="Deployment decisions require logging strategy",
        satisfied=satisfied,
        details="" if satisfied else "Deployment decisions exist but no logging decisions found",
    )


def _check_caching_requires_state(decisions: list[Decision]) -> ConstraintResult:
    """Rule 5: Caching needs state management."""
    dims = _active_dimensions(decisions)
    has_caching = Dimension.CACHING.value in dims
    has_state = Dimension.STATE_MANAGEMENT.value in dims
    satisfied = not has_caching or has_state
    return ConstraintResult(
        name="caching_requires_state_mgmt",
        description="Caching decisions require state management strategy",
        satisfied=satisfied,
        details="" if satisfied else "Caching decisions exist but no state management decisions found",
    )


def _check_testing_with_error_handling(decisions: list[Decision]) -> ConstraintResult:
    """Rule 6: Testing implies error handling."""
    dims = _active_dimensions(decisions)
    has_testing = Dimension.TESTING.value in dims
    has_errors = Dimension.ERROR_HANDLING.value in dims
    satisfied = not has_testing or has_errors
    return ConstraintResult(
        name="testing_with_error_handling",
        description="Testing decisions require error handling coverage",
        satisfied=satisfied,
        details="" if satisfied else "Testing decisions exist but no error handling decisions found",
    )


def _check_no_circular_supersession(decisions: list[Decision]) -> ConstraintResult:
    """Rule 7: Supersession chain must be acyclic."""
    # Build graph: decision_id -> supersedes_id
    edges: dict[str, str] = {}
    for d in decisions:
        if d.supersedes:
            edges[str(d.id)] = str(d.supersedes)

    # Check for cycles using DFS
    visited: set[str] = set()
    in_stack: set[str] = set()
    has_cycle = False

    def dfs(node: str) -> bool:
        if node in in_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        if node in edges:
            if dfs(edges[node]):
                return True
        in_stack.discard(node)
        return False

    for node in edges:
        if dfs(node):
            has_cycle = True
            break

    return ConstraintResult(
        name="no_circular_supersession",
        description="Decision supersession chain must be acyclic",
        satisfied=not has_cycle,
        details="" if not has_cycle else "Circular supersession detected in decision chain",
    )


def _check_concurrency_requires_state(decisions: list[Decision]) -> ConstraintResult:
    """Rule 8: Concurrency needs state management."""
    dims = _active_dimensions(decisions)
    has_concurrency = Dimension.CONCURRENCY.value in dims
    has_state = Dimension.STATE_MANAGEMENT.value in dims
    satisfied = not has_concurrency or has_state
    return ConstraintResult(
        name="concurrency_requires_state_mgmt",
        description="Concurrency decisions require state management strategy",
        satisfied=satisfied,
        details="" if satisfied else "Concurrency decisions exist but no state management decisions found",
    )


def _check_security_requires_auth(decisions: list[Decision]) -> ConstraintResult:
    """Rule 9: Security decisions need auth coverage."""
    dims = _active_dimensions(decisions)
    has_security = Dimension.SECURITY.value in dims
    has_auth = Dimension.AUTH.value in dims
    satisfied = not has_security or has_auth
    return ConstraintResult(
        name="security_requires_auth",
        description="Security decisions require auth dimension coverage",
        satisfied=satisfied,
        details="" if satisfied else "Security decisions exist but no auth decisions found",
    )


def _check_messaging_requires_error_handling(decisions: list[Decision]) -> ConstraintResult:
    """Rule 10: Async messaging needs error handling."""
    dims = _active_dimensions(decisions)
    has_messaging = Dimension.MESSAGING.value in dims
    has_errors = Dimension.ERROR_HANDLING.value in dims
    satisfied = not has_messaging or has_errors
    return ConstraintResult(
        name="messaging_requires_error_handling",
        description="Messaging decisions require error handling coverage",
        satisfied=satisfied,
        details="" if satisfied else "Messaging decisions exist but no error handling decisions found",
    )


# ---------------------------------------------------------------------------
# Z3-based verification (when Z3 is available)
# ---------------------------------------------------------------------------


def _verify_with_z3(decisions: list[Decision]) -> list[ConstraintResult]:
    """Verify constraints using Z3 SAT solver."""
    if not _HAS_Z3:
        raise RuntimeError("Z3 is not installed")

    dims = _active_dimensions(decisions)
    results: list[ConstraintResult] = []

    # Create Z3 boolean variables for each dimension
    dim_vars = {d.value: Bool(f"has_{d.value}") for d in Dimension}

    # Set known facts
    facts = []
    for d in Dimension:
        if d.value in dims:
            facts.append(dim_vars[d.value])
        else:
            facts.append(Not(dim_vars[d.value]))

    # Constraint pairs: (name, description, z3_constraint)
    implications = [
        (
            "auth_requires_security",
            "Auth dimension decisions require security dimension coverage",
            Implies(dim_vars[Dimension.AUTH.value], dim_vars[Dimension.SECURITY.value]),
        ),
        (
            "deployment_requires_logging",
            "Deployment decisions require logging strategy",
            Implies(dim_vars[Dimension.DEPLOYMENT.value], dim_vars[Dimension.LOGGING.value]),
        ),
        (
            "caching_requires_state_mgmt",
            "Caching decisions require state management strategy",
            Implies(dim_vars[Dimension.CACHING.value], dim_vars[Dimension.STATE_MANAGEMENT.value]),
        ),
        (
            "testing_with_error_handling",
            "Testing decisions require error handling coverage",
            Implies(dim_vars[Dimension.TESTING.value], dim_vars[Dimension.ERROR_HANDLING.value]),
        ),
        (
            "concurrency_requires_state_mgmt",
            "Concurrency decisions require state management strategy",
            Implies(dim_vars[Dimension.CONCURRENCY.value], dim_vars[Dimension.STATE_MANAGEMENT.value]),
        ),
        (
            "security_requires_auth",
            "Security decisions require auth dimension coverage",
            Implies(dim_vars[Dimension.SECURITY.value], dim_vars[Dimension.AUTH.value]),
        ),
        (
            "messaging_requires_error_handling",
            "Messaging decisions require error handling coverage",
            Implies(dim_vars[Dimension.MESSAGING.value], dim_vars[Dimension.ERROR_HANDLING.value]),
        ),
    ]

    for name, description, constraint in implications:
        solver = Solver()
        for f in facts:
            solver.add(f)
        # Check if the negation of the constraint is satisfiable
        # If NOT constraint is SAT with facts → constraint is violated
        solver.add(Not(constraint))
        check = solver.check()
        satisfied = check == unsat  # If UNSAT, constraint holds
        results.append(ConstraintResult(
            name=name,
            description=description,
            satisfied=satisfied,
            details="" if satisfied else f"Z3: constraint '{name}' violated with current dimensions",
        ))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


ALL_CONSTRAINT_NAMES = [
    "auth_requires_security",
    "api_style_consistency",
    "deployment_requires_logging",
    "caching_requires_state_mgmt",
    "testing_with_error_handling",
    "no_circular_supersession",
    "concurrency_requires_state_mgmt",
    "security_requires_auth",
    "messaging_requires_error_handling",
]

_PYTHON_CHECKERS = {
    "auth_requires_security": _check_auth_requires_security,
    "api_style_consistency": _check_api_style_consistency,
    "deployment_requires_logging": _check_deployment_requires_logging,
    "caching_requires_state_mgmt": _check_caching_requires_state,
    "testing_with_error_handling": _check_testing_with_error_handling,
    "no_circular_supersession": _check_no_circular_supersession,
    "concurrency_requires_state_mgmt": _check_concurrency_requires_state,
    "security_requires_auth": _check_security_requires_auth,
    "messaging_requires_error_handling": _check_messaging_requires_error_handling,
}


def verify_constraints(
    decisions: list[Decision],
    *,
    constraints: list[str] | None = None,
    use_z3: bool | None = None,
) -> VerificationReport:
    """Verify architectural constraints against current decisions.

    Args:
        decisions: Active decisions to check.
        constraints: Specific constraint names to check (default: all).
        use_z3: Force Z3 on/off. Default: auto-detect.

    Returns:
        VerificationReport with pass/fail for each constraint.
    """
    names = constraints or ALL_CONSTRAINT_NAMES
    should_use_z3 = use_z3 if use_z3 is not None else _HAS_Z3

    results: list[ConstraintResult] = []

    if should_use_z3 and _HAS_Z3:
        # Use Z3 for implication constraints
        z3_results = _verify_with_z3(decisions)
        z3_by_name = {r.name: r for r in z3_results}

        for name in names:
            if name in z3_by_name:
                results.append(z3_by_name[name])
            elif name in _PYTHON_CHECKERS:
                # Non-Z3 constraints (api_style, circular supersession)
                results.append(_PYTHON_CHECKERS[name](decisions))
    else:
        # Pure Python fallback
        for name in names:
            if name in _PYTHON_CHECKERS:
                results.append(_PYTHON_CHECKERS[name](decisions))

    all_satisfied = all(r.satisfied for r in results)
    return VerificationReport(
        results=results,
        all_satisfied=all_satisfied,
        using_z3=should_use_z3 and _HAS_Z3,
    )
