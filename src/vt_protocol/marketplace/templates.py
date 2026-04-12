"""Governance rule marketplace — decision template library.

Bundled starter governance configs for common tech stacks.

From SPEC Sprint 22: "Decision template library."
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StackTemplate:
    """A bundled governance config template for a tech stack."""

    name: str = ""
    display_name: str = ""
    description: str = ""
    stack: list[str] = field(default_factory=list)  # e.g. ["nextjs", "prisma", "vercel"]
    dimensions: list[str] = field(default_factory=list)
    governance_config: dict[str, Any] = field(default_factory=dict)
    decision_seeds: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "stack": self.stack,
            "dimensions": self.dimensions,
            "governance_config": self.governance_config,
            "decision_seeds": self.decision_seeds,
        }


@dataclass
class TemplateLibrary:
    """Registry of built-in governance templates."""

    templates: dict[str, StackTemplate] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.templates)

    def register(self, template: StackTemplate) -> None:
        self.templates[template.name] = template

    def get(self, name: str) -> StackTemplate | None:
        return self.templates.get(name)

    def list_templates(self) -> list[str]:
        return sorted(self.templates.keys())

    def search(self, *, stack: str = "", dimension: str = "") -> list[StackTemplate]:
        results: list[StackTemplate] = []
        for t in self.templates.values():
            if stack and stack.lower() not in [s.lower() for s in t.stack]:
                continue
            if dimension and dimension not in t.dimensions:
                continue
            results.append(t)
        return results

    def instantiate(self, name: str, **overrides: Any) -> dict[str, Any] | None:
        """Return a deep copy of the governance config, with overrides applied."""
        t = self.templates.get(name)
        if t is None:
            return None
        config = copy.deepcopy(t.governance_config)
        for key, value in overrides.items():
            config[key] = value
        return config


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------


NEXTJS_PRISMA_VERCEL = StackTemplate(
    name="nextjs-prisma-vercel",
    display_name="Next.js + Prisma + Vercel",
    description="Full-stack TypeScript with Prisma ORM and Vercel deployment.",
    stack=["nextjs", "prisma", "vercel", "typescript"],
    dimensions=["database", "api-style", "deployment", "state-management"],
    governance_config={
        "extends": ["@vt/recommended"],
        "agents": {"claude": True, "cursor": True, "copilot": True},
        "rules": {
            "freeze_on_adopt": True,
            "contradiction_threshold": 0.7,
            "max_new_deps_per_task": 3,
        },
        "stack": {
            "framework": "nextjs",
            "orm": "prisma",
            "deploy": "vercel",
            "language": "typescript",
        },
        "conventions": {
            "api_style": "app-router",
            "data_fetching": "server-components",
            "state": "react-query",
            "auth": "next-auth",
        },
    },
    decision_seeds=[
        {
            "title": "Use Next.js App Router for API routes",
            "dimensions": ["api-style"],
            "rationale": "App Router colocation keeps API logic next to UI components.",
        },
        {
            "title": "Prisma as ORM with migration tracking",
            "dimensions": ["database"],
            "rationale": "Type-safe database access with auto-generated client.",
        },
        {
            "title": "Deploy to Vercel with preview deploys",
            "dimensions": ["deployment"],
            "rationale": "Zero-config deployment with automatic preview URLs per PR.",
        },
    ],
)


DJANGO_POSTGRES_AWS = StackTemplate(
    name="django-postgres-aws",
    display_name="Django + PostgreSQL + AWS",
    description="Python web framework with PostgreSQL and AWS deployment.",
    stack=["django", "postgresql", "aws", "python"],
    dimensions=["database", "api-style", "deployment", "auth", "testing"],
    governance_config={
        "extends": ["@vt/recommended"],
        "agents": {"claude": True, "cursor": True, "copilot": True},
        "rules": {
            "freeze_on_adopt": True,
            "contradiction_threshold": 0.7,
            "max_new_deps_per_task": 3,
        },
        "stack": {
            "framework": "django",
            "database": "postgresql",
            "deploy": "aws",
            "language": "python",
        },
        "conventions": {
            "api_style": "django-rest-framework",
            "auth": "django-allauth",
            "testing": "pytest-django",
            "migrations": "django-migrations",
        },
    },
    decision_seeds=[
        {
            "title": "Use Django REST Framework for API layer",
            "dimensions": ["api-style"],
            "rationale": "DRF provides serializers, viewsets, and auth out of the box.",
        },
        {
            "title": "PostgreSQL with Django migrations",
            "dimensions": ["database"],
            "rationale": "Battle-tested combination with full migration tracking.",
        },
        {
            "title": "Deploy to AWS ECS with RDS",
            "dimensions": ["deployment"],
            "rationale": "Containerized deployment with managed database.",
        },
    ],
)


FASTAPI_SQLALCHEMY_DOCKER = StackTemplate(
    name="fastapi-sqlalchemy-docker",
    display_name="FastAPI + SQLAlchemy + Docker",
    description="High-performance async Python API with SQLAlchemy and Docker.",
    stack=["fastapi", "sqlalchemy", "docker", "python"],
    dimensions=["database", "api-style", "deployment", "concurrency", "testing"],
    governance_config={
        "extends": ["@vt/recommended"],
        "agents": {"claude": True, "cursor": True, "copilot": True},
        "rules": {
            "freeze_on_adopt": True,
            "contradiction_threshold": 0.7,
            "max_new_deps_per_task": 3,
        },
        "stack": {
            "framework": "fastapi",
            "orm": "sqlalchemy",
            "deploy": "docker",
            "language": "python",
        },
        "conventions": {
            "api_style": "openapi-first",
            "async": "asyncio",
            "testing": "pytest-asyncio",
            "migrations": "alembic",
        },
    },
    decision_seeds=[
        {
            "title": "FastAPI with async endpoints",
            "dimensions": ["api-style", "concurrency"],
            "rationale": "Native async/await with automatic OpenAPI docs.",
        },
        {
            "title": "SQLAlchemy 2.0 with Alembic migrations",
            "dimensions": ["database"],
            "rationale": "Modern SQLAlchemy with typed queries and migration tracking.",
        },
        {
            "title": "Docker Compose for local dev, Kubernetes for prod",
            "dimensions": ["deployment"],
            "rationale": "Consistent container-based deployment across environments.",
        },
    ],
)


REACT_TYPESCRIPT_VITE = StackTemplate(
    name="react-typescript-vite",
    display_name="React + TypeScript + Vite",
    description="Modern frontend with React, TypeScript, and Vite build tooling.",
    stack=["react", "typescript", "vite"],
    dimensions=["state-management", "api-style", "testing"],
    governance_config={
        "extends": ["@vt/recommended"],
        "agents": {"claude": True, "cursor": True, "copilot": True},
        "rules": {
            "freeze_on_adopt": True,
            "contradiction_threshold": 0.7,
            "max_new_deps_per_task": 3,
        },
        "stack": {
            "framework": "react",
            "build": "vite",
            "language": "typescript",
        },
        "conventions": {
            "state": "zustand",
            "data_fetching": "tanstack-query",
            "routing": "react-router",
            "testing": "vitest",
            "styling": "tailwind",
        },
    },
    decision_seeds=[
        {
            "title": "Zustand for global state management",
            "dimensions": ["state-management"],
            "rationale": "Minimal boilerplate state management without Redux complexity.",
        },
        {
            "title": "TanStack Query for server state",
            "dimensions": ["api-style", "state-management"],
            "rationale": "Separates server state from client state with caching.",
        },
        {
            "title": "Vitest for unit tests with React Testing Library",
            "dimensions": ["testing"],
            "rationale": "Vite-native testing with excellent DX and fast execution.",
        },
    ],
)


def get_default_library() -> TemplateLibrary:
    """Return a library pre-loaded with all built-in templates."""
    lib = TemplateLibrary()
    for template in [
        NEXTJS_PRISMA_VERCEL,
        DJANGO_POSTGRES_AWS,
        FASTAPI_SQLALCHEMY_DOCKER,
        REACT_TYPESCRIPT_VITE,
    ]:
        lib.register(template)
    return lib
