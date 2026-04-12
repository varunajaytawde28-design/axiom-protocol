"""Factory functions for creating test decisions and contradictions."""

from __future__ import annotations

from uuid import UUID, uuid4

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

# The 12 core dimensions
ALL_DIMENSIONS = list(Dimension)


def make_decision(
    title: str = "Test Decision",
    content: str = "This is a test decision with sufficient content for confidence calculation.",
    *,
    dimensions: list[Dimension] | None = None,
    decision_type: DecisionType = DecisionType.TECHNICAL,
    source_type: SourceType = SourceType.AGENT,
    made_by: str = "test-agent",
    project: str = "test-project",
    rationale: str = "Test rationale",
    alternatives: list[str] | None = None,
    confidence: float = 0.75,
    decision_id: UUID | None = None,
    supersedes: UUID | None = None,
) -> Decision:
    """Create a Decision with sensible defaults."""
    kwargs = dict(
        title=title,
        content=content,
        dimensions=dimensions or [Dimension.DATABASE],
        decision_type=decision_type,
        source_type=source_type,
        made_by=made_by,
        project=project,
        rationale=rationale,
        alternatives=alternatives or [],
        supersedes=supersedes,
    )
    if decision_id is not None:
        kwargs["id"] = decision_id
    if confidence != 0.75:
        kwargs["confidence"] = confidence
    return Decision(**kwargs)


def make_contradiction(
    decision_a: Decision,
    decision_b: Decision,
    *,
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
    confidence: float = 0.85,
    reasoning: str = "Decisions conflict on shared dimension.",
    is_baseline: bool = False,
) -> Contradiction:
    """Create a Contradiction between two decisions."""
    shared = list(set(decision_a.dimensions) & set(decision_b.dimensions))
    return Contradiction(
        decision_a_id=decision_a.id,
        decision_b_id=decision_b.id,
        decision_a_title=decision_a.title,
        decision_b_title=decision_b.title,
        verdict=verdict,
        reasoning=reasoning,
        evidence_a=decision_a.content[:100],
        evidence_b=decision_b.content[:100],
        shared_dimensions=shared,
        confidence=confidence,
        is_baseline=is_baseline,
    )


def make_week_seven_decisions() -> list[Decision]:
    """Create 20 decisions across all 12 dimensions — the 'week seven wall' scenario.

    Simulates a real project after 7 weeks of development where governance
    reaches critical mass and contradictions start appearing.
    """
    decisions = []
    dim_titles = {
        Dimension.DATABASE: [
            ("Use PostgreSQL for primary store", "PostgreSQL with MVCC for concurrent access."),
            ("Use Redis for session storage", "Redis provides fast session lookups."),
        ],
        Dimension.AUTH: [
            ("JWT for API authentication", "Stateless JWT tokens for API auth."),
            ("OAuth2 for external integrations", "OAuth2 flow for third-party access."),
        ],
        Dimension.CACHING: [
            ("Redis caching layer", "Cache hot queries in Redis with 5min TTL."),
        ],
        Dimension.API_STYLE: [
            ("REST API with OpenAPI spec", "RESTful endpoints with full OpenAPI documentation."),
            ("GraphQL for mobile clients", "GraphQL endpoint for flexible mobile queries."),
        ],
        Dimension.DEPLOYMENT: [
            ("Docker containers on ECS", "Containerized deployment on AWS ECS Fargate."),
        ],
        Dimension.CONCURRENCY: [
            ("Celery for async tasks", "Celery with Redis broker for background jobs."),
            ("asyncio for I/O-bound ops", "Python asyncio for database and API calls."),
        ],
        Dimension.LOGGING: [
            ("Structured JSON logging", "All logs in structured JSON format to stdout."),
        ],
        Dimension.TESTING: [
            ("Pytest with fixtures", "Pytest as test runner with fixture-based setup."),
        ],
        Dimension.ERROR_HANDLING: [
            ("Centralized error handler", "Global exception handler with Sentry integration."),
        ],
        Dimension.STATE_MANAGEMENT: [
            ("Server-side sessions", "Server-side session state in Redis."),
        ],
        Dimension.MESSAGING: [
            ("RabbitMQ for events", "RabbitMQ for inter-service event communication."),
            ("Kafka for audit stream", "Kafka for high-volume audit event streaming."),
        ],
        Dimension.SECURITY: [
            ("OWASP Top 10 compliance", "All endpoints validated against OWASP Top 10."),
            ("TLS 1.3 everywhere", "Enforce TLS 1.3 for all internal and external traffic."),
        ],
    }

    for dim, pairs in dim_titles.items():
        for title, content in pairs:
            decisions.append(make_decision(
                title=title,
                content=content,
                dimensions=[dim],
                rationale=f"Standard practice for {dim.value}",
                project="test-project",
            ))

    return decisions


def make_conflicting_pair() -> tuple[Decision, Decision]:
    """Create a pair of decisions that should be detected as contradictory."""
    d_a = make_decision(
        title="Use SQLite for all storage",
        content="SQLite with WAL mode for all data storage. No external database server needed.",
        dimensions=[Dimension.DATABASE],
        rationale="Simplicity and zero-config deployment",
    )
    d_b = make_decision(
        title="Use PostgreSQL for primary storage",
        content="PostgreSQL for primary data storage. Required for concurrent multi-user access.",
        dimensions=[Dimension.DATABASE],
        rationale="Production-grade concurrent access",
    )
    return d_a, d_b


def make_three_way_collision() -> tuple[Decision, Decision, Decision]:
    """Create 3 decisions that pairwise contradict on API style."""
    d1 = make_decision(
        title="REST-only API",
        content="All APIs must be RESTful. No GraphQL or gRPC allowed.",
        dimensions=[Dimension.API_STYLE],
    )
    d2 = make_decision(
        title="GraphQL for all queries",
        content="Use GraphQL for all data queries. REST only for webhooks.",
        dimensions=[Dimension.API_STYLE],
    )
    d3 = make_decision(
        title="gRPC for service-to-service",
        content="All internal service communication must use gRPC.",
        dimensions=[Dimension.API_STYLE],
    )
    return d1, d2, d3


def make_compatible_pair(index: int = 0) -> tuple[Decision, Decision]:
    """Create a pair of decisions that are compatible (not contradictory)."""
    d_a = make_decision(
        title=f"Use pytest for unit tests (pair {index})",
        content=f"pytest is the standard test runner for this project (pair {index}).",
        dimensions=[Dimension.TESTING],
    )
    d_b = make_decision(
        title=f"Use coverage.py for test coverage (pair {index})",
        content=f"coverage.py tracks test coverage metrics (pair {index}).",
        dimensions=[Dimension.TESTING],
    )
    return d_a, d_b
