"""Tests for QA view — Behavioral contract violations."""

from __future__ import annotations

import pytest

from vt_protocol.dashboard.contracts import (
    APIEndpoint,
    ContractReport,
    ContractViolation,
    HTTPMethod,
    ServiceContract,
    ViolationType,
    analyze_contracts,
    extract_endpoints_from_source,
    find_violations,
)


class TestAPIEndpoint:
    def test_to_dict(self) -> None:
        ep = APIEndpoint(method="GET", path="/api/users", service="auth")
        d = ep.to_dict()
        assert d["method"] == "GET"
        assert d["path"] == "/api/users"
        assert d["service"] == "auth"

    def test_defaults(self) -> None:
        ep = APIEndpoint()
        assert ep.method == "GET"
        assert ep.response_type == "json"


class TestServiceContract:
    def test_endpoint_count(self) -> None:
        sc = ServiceContract(
            service_name="api",
            endpoints=[APIEndpoint(), APIEndpoint()],
        )
        assert sc.endpoint_count == 2

    def test_empty(self) -> None:
        sc = ServiceContract(service_name="empty")
        assert sc.endpoint_count == 0


class TestContractViolation:
    def test_to_dict(self) -> None:
        v = ContractViolation(
            violation_type=ViolationType.CONTENT_TYPE_MISMATCH,
            severity="error",
            message="test",
        )
        d = v.to_dict()
        assert d["violation_type"] == "content_type_mismatch"
        assert d["severity"] == "error"


class TestContractReport:
    def test_empty(self) -> None:
        report = ContractReport()
        assert report.total_endpoints == 0
        assert report.consistency_score == 1.0

    def test_with_violations(self) -> None:
        report = ContractReport(
            services=[ServiceContract(endpoints=[APIEndpoint()] * 5)],
            violations=[ContractViolation(violation_type=ViolationType.DUPLICATE_ROUTE)],
        )
        assert report.total_endpoints == 5
        assert report.violation_count == 1
        assert report.consistency_score < 1.0


class TestExtractEndpoints:
    def test_fastapi_get(self) -> None:
        source = '''
@app.get("/api/users")
async def list_users() -> dict:
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="api")
        assert len(endpoints) == 1
        assert endpoints[0].method == "GET"
        assert endpoints[0].path == "/api/users"

    def test_fastapi_post(self) -> None:
        source = '''
@app.post("/api/users")
async def create_user() -> dict:
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="api")
        assert len(endpoints) == 1
        assert endpoints[0].method == "POST"

    def test_fastapi_with_path_params(self) -> None:
        source = '''
@app.get("/api/users/{user_id}")
async def get_user(user_id: str) -> dict:
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="api")
        assert "user_id" in endpoints[0].parameters

    def test_multiple_endpoints(self) -> None:
        source = '''
@app.get("/api/users")
async def list_users() -> dict:
    return {}

@app.post("/api/users")
async def create_user() -> dict:
    return {}

@app.delete("/api/users/{id}")
async def delete_user(id: str) -> dict:
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="api")
        assert len(endpoints) == 3

    def test_flask_route(self) -> None:
        source = '''
@app.route("/api/data", methods=["GET", "POST"])
def data():
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="api")
        assert len(endpoints) == 2
        methods = {ep.method for ep in endpoints}
        assert "GET" in methods
        assert "POST" in methods

    def test_detects_status_codes(self) -> None:
        source = '''
@app.post("/api/items")
async def create_item() -> dict:
    if error:
        raise HTTPException(400, "bad")
    raise HTTPException(404, "not found")
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="api")
        assert 400 in endpoints[0].status_codes
        assert 404 in endpoints[0].status_codes

    def test_no_endpoints(self) -> None:
        source = "def regular_function(): pass"
        endpoints = extract_endpoints_from_source(source)
        assert len(endpoints) == 0

    def test_router_prefix(self) -> None:
        source = '''
@router.get("/items")
async def get_items() -> dict:
    return {}
'''
        endpoints = extract_endpoints_from_source(source, service_name="items")
        assert len(endpoints) == 1
        assert endpoints[0].service == "items"


class TestFindViolations:
    def test_no_violations(self) -> None:
        contracts = [
            ServiceContract(
                service_name="api",
                endpoints=[APIEndpoint(method="GET", path="/users", response_type="json")],
            ),
        ]
        violations = find_violations(contracts)
        assert len(violations) == 0

    def test_duplicate_route(self) -> None:
        contracts = [
            ServiceContract(
                service_name="api",
                endpoints=[
                    APIEndpoint(method="GET", path="/users"),
                    APIEndpoint(method="GET", path="/users"),
                ],
            ),
        ]
        violations = find_violations(contracts)
        assert any(v.violation_type == ViolationType.DUPLICATE_ROUTE for v in violations)

    def test_content_type_mismatch(self) -> None:
        contracts = [
            ServiceContract(
                service_name="service_a",
                endpoints=[APIEndpoint(method="POST", path="/payments", response_type="json", service="service_a")],
            ),
            ServiceContract(
                service_name="service_b",
                endpoints=[APIEndpoint(method="POST", path="/payments", response_type="xml", service="service_b")],
            ),
        ]
        violations = find_violations(contracts)
        assert any(v.violation_type == ViolationType.CONTENT_TYPE_MISMATCH for v in violations)

    def test_no_cross_service_same_service(self) -> None:
        """Same path on same service with same type = no cross-service violation."""
        contracts = [
            ServiceContract(
                service_name="api",
                endpoints=[
                    APIEndpoint(method="GET", path="/health", response_type="json", service="api"),
                    APIEndpoint(method="POST", path="/health", response_type="json", service="api"),
                ],
            ),
        ]
        violations = find_violations(contracts)
        content_mismatches = [v for v in violations if v.violation_type == ViolationType.CONTENT_TYPE_MISMATCH]
        assert len(content_mismatches) == 0


class TestAnalyzeContracts:
    def test_full_analysis(self) -> None:
        sources = [
            ('''
@app.get("/api/users")
async def list_users() -> dict:
    return {}
''', "user-service", "user_api.py"),
            ('''
@app.get("/api/orders")
async def list_orders() -> dict:
    return {}
''', "order-service", "order_api.py"),
        ]
        report = analyze_contracts(sources)
        assert report.total_endpoints == 2
        assert len(report.services) == 2

    def test_empty_sources(self) -> None:
        report = analyze_contracts([])
        assert report.total_endpoints == 0
        assert report.consistency_score == 1.0

    def test_sources_with_no_endpoints(self) -> None:
        sources = [("def foo(): pass", "svc", "foo.py")]
        report = analyze_contracts(sources)
        assert report.total_endpoints == 0
