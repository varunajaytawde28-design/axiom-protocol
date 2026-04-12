"""QA view — Behavioral contract violations.

Extract API contracts from code, compare across services,
detect inconsistencies and violations.

From SPEC Sprint 16: "QA view — behavioral contract violations."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ViolationType(str, Enum):
    """Types of contract violations."""

    METHOD_MISMATCH = "method_mismatch"
    CONTENT_TYPE_MISMATCH = "content_type_mismatch"
    STATUS_CODE_MISMATCH = "status_code_mismatch"
    MISSING_ENDPOINT = "missing_endpoint"
    DUPLICATE_ROUTE = "duplicate_route"
    SCHEMA_MISMATCH = "schema_mismatch"


@dataclass
class APIEndpoint:
    """A single API endpoint extracted from code."""

    method: str = "GET"
    path: str = ""
    response_type: str = "json"
    status_codes: list[int] = field(default_factory=lambda: [200])
    service: str = ""
    source_file: str = ""
    line_number: int = 0
    parameters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "response_type": self.response_type,
            "status_codes": self.status_codes,
            "service": self.service,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "parameters": self.parameters,
        }


@dataclass
class ContractViolation:
    """A detected contract violation between services."""

    violation_type: ViolationType
    severity: str = "warning"  # warning, error, critical
    message: str = ""
    endpoint_a: APIEndpoint | None = None
    endpoint_b: APIEndpoint | None = None
    service_a: str = ""
    service_b: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_type": self.violation_type.value,
            "severity": self.severity,
            "message": self.message,
            "endpoint_a": self.endpoint_a.to_dict() if self.endpoint_a else None,
            "endpoint_b": self.endpoint_b.to_dict() if self.endpoint_b else None,
            "service_a": self.service_a,
            "service_b": self.service_b,
        }


@dataclass
class ServiceContract:
    """All API contracts for a single service."""

    service_name: str = ""
    endpoints: list[APIEndpoint] = field(default_factory=list)

    @property
    def endpoint_count(self) -> int:
        return len(self.endpoints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "endpoint_count": self.endpoint_count,
            "endpoints": [e.to_dict() for e in self.endpoints],
        }


@dataclass
class ContractReport:
    """Full contract analysis report."""

    services: list[ServiceContract] = field(default_factory=list)
    violations: list[ContractViolation] = field(default_factory=list)

    @property
    def total_endpoints(self) -> int:
        return sum(s.endpoint_count for s in self.services)

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def consistency_score(self) -> float:
        """1.0 = fully consistent, 0.0 = every endpoint has a violation."""
        if self.total_endpoints == 0:
            return 1.0
        return max(0.0, 1.0 - self.violation_count / max(self.total_endpoints, 1))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_endpoints": self.total_endpoints,
            "violation_count": self.violation_count,
            "consistency_score": round(self.consistency_score, 3),
            "services": [s.to_dict() for s in self.services],
            "violations": [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Contract extraction from Python source
# ---------------------------------------------------------------------------

# Patterns for FastAPI/Flask route decorators
_FASTAPI_ROUTE_RE = re.compile(
    r"""@(?:app|router)\.(get|post|put|patch|delete|head|options)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

_FLASK_ROUTE_RE = re.compile(
    r"""@(?:app|blueprint|bp)\s*\.route\s*\(\s*["']([^"']+)["']\s*(?:,\s*methods\s*=\s*\[([^\]]*)\])?""",
    re.IGNORECASE,
)

# Pattern for response type hints
_RESPONSE_TYPE_RE = re.compile(
    r"""\)\s*->\s*([\w\[\], |]+)\s*:""",
)


def extract_endpoints_from_source(
    source: str,
    *,
    service_name: str = "",
    source_file: str = "",
) -> list[APIEndpoint]:
    """Extract API endpoints from Python source code.

    Recognizes FastAPI and Flask route decorators.
    """
    endpoints: list[APIEndpoint] = []
    lines = source.split("\n")

    for i, line in enumerate(lines, 1):
        # FastAPI-style: @app.get("/path")
        match = _FASTAPI_ROUTE_RE.search(line)
        if match:
            method = match.group(1).upper()
            path = match.group(2)
            response_type = _detect_response_type(lines, i - 1)
            status_codes = _detect_status_codes(lines, i - 1)
            params = _extract_path_params(path)
            endpoints.append(APIEndpoint(
                method=method,
                path=path,
                response_type=response_type,
                status_codes=status_codes,
                service=service_name,
                source_file=source_file,
                line_number=i,
                parameters=params,
            ))
            continue

        # Flask-style: @app.route("/path", methods=["GET", "POST"])
        match = _FLASK_ROUTE_RE.search(line)
        if match:
            path = match.group(1)
            methods_str = match.group(2) or '"GET"'
            methods = re.findall(r'"(\w+)"', methods_str)
            if not methods:
                methods = ["GET"]
            params = _extract_path_params(path)
            for method in methods:
                endpoints.append(APIEndpoint(
                    method=method.upper(),
                    path=path,
                    response_type="json",
                    service=service_name,
                    source_file=source_file,
                    line_number=i,
                    parameters=params,
                ))

    return endpoints


def _detect_response_type(lines: list[str], decorator_idx: int) -> str:
    """Look at the function signature for response type hints."""
    # Check the next few lines for the function definition
    for offset in range(1, 5):
        idx = decorator_idx + offset
        if idx >= len(lines):
            break
        line = lines[idx]
        type_match = _RESPONSE_TYPE_RE.search(line)
        if type_match:
            type_str = type_match.group(1).strip()
            if "HTML" in type_str:
                return "html"
            if "FileResponse" in type_str or "StreamingResponse" in type_str:
                return "binary"
            if "str" == type_str:
                return "text"
            return "json"
    return "json"


def _detect_status_codes(lines: list[str], decorator_idx: int) -> list[int]:
    """Scan function body for status code references."""
    codes: list[int] = [200]
    for offset in range(1, 30):
        idx = decorator_idx + offset
        if idx >= len(lines):
            break
        line = lines[idx]
        # Look for status_code= or HTTPException(NNN
        for m in re.finditer(r"status_code\s*=\s*(\d{3})", line):
            code = int(m.group(1))
            if code not in codes:
                codes.append(code)
        for m in re.finditer(r"HTTPException\s*\(\s*(\d{3})", line):
            code = int(m.group(1))
            if code not in codes:
                codes.append(code)
        # Stop at next function/class def
        if re.match(r"^(?:def |class |@)", line.lstrip()) and offset > 2:
            break
    return sorted(codes)


def _extract_path_params(path: str) -> list[str]:
    """Extract path parameters like {id} or <id>."""
    params: list[str] = []
    for m in re.finditer(r"\{(\w+)\}", path):
        params.append(m.group(1))
    for m in re.finditer(r"<(?:\w+:)?(\w+)>", path):
        params.append(m.group(1))
    return params


# ---------------------------------------------------------------------------
# Contract comparison and violation detection
# ---------------------------------------------------------------------------


def find_violations(contracts: list[ServiceContract]) -> list[ContractViolation]:
    """Compare contracts across services and find violations."""
    violations: list[ContractViolation] = []

    # Build path → endpoints map
    path_map: dict[str, list[APIEndpoint]] = {}
    for contract in contracts:
        for ep in contract.endpoints:
            key = _normalize_path(ep.path)
            path_map.setdefault(key, []).append(ep)

    # Check for duplicate routes within a service
    for contract in contracts:
        seen: dict[str, APIEndpoint] = {}
        for ep in contract.endpoints:
            route_key = f"{ep.method}:{_normalize_path(ep.path)}"
            if route_key in seen:
                violations.append(ContractViolation(
                    violation_type=ViolationType.DUPLICATE_ROUTE,
                    severity="warning",
                    message=f"Duplicate route {ep.method} {ep.path} in {contract.service_name}",
                    endpoint_a=seen[route_key],
                    endpoint_b=ep,
                    service_a=contract.service_name,
                    service_b=contract.service_name,
                ))
            else:
                seen[route_key] = ep

    # Cross-service comparisons
    for norm_path, endpoints in path_map.items():
        if len(endpoints) < 2:
            continue

        # Check for content type mismatches on same path
        for i, ep_a in enumerate(endpoints):
            for ep_b in endpoints[i + 1:]:
                if ep_a.service == ep_b.service:
                    continue
                if ep_a.method == ep_b.method and ep_a.response_type != ep_b.response_type:
                    violations.append(ContractViolation(
                        violation_type=ViolationType.CONTENT_TYPE_MISMATCH,
                        severity="error",
                        message=(
                            f"{ep_a.method} {ep_a.path}: {ep_a.service} returns "
                            f"{ep_a.response_type} but {ep_b.service} expects "
                            f"{ep_b.response_type}"
                        ),
                        endpoint_a=ep_a,
                        endpoint_b=ep_b,
                        service_a=ep_a.service,
                        service_b=ep_b.service,
                    ))

    return violations


def _normalize_path(path: str) -> str:
    """Normalize path for comparison — replace params with placeholders."""
    normalized = re.sub(r"\{[^}]+\}", "{param}", path)
    normalized = re.sub(r"<[^>]+>", "{param}", normalized)
    return normalized.rstrip("/") or "/"


def analyze_contracts(
    sources: list[tuple[str, str, str]],
) -> ContractReport:
    """Analyze API contracts from multiple source files.

    Args:
        sources: List of (source_code, service_name, file_path) tuples.
    """
    services: list[ServiceContract] = []

    for source, service_name, file_path in sources:
        endpoints = extract_endpoints_from_source(
            source, service_name=service_name, source_file=file_path,
        )
        if endpoints:
            services.append(ServiceContract(
                service_name=service_name,
                endpoints=endpoints,
            ))

    violations = find_violations(services)

    return ContractReport(services=services, violations=violations)
