"""42-dimension architectural taxonomy with auto-detection rules.

SPEC Phase 1: 30+ dimensions auto-detectable from code WITHOUT LLM call.
Scans package.json, requirements.txt, docker-compose, config files, and
directory structure.

Developer-facing config exposes 7 top-level facets only. The full 42
dimensions are internal — used for junction-table routing of contradiction
checks (shared-dimension-count × recency-multiplier).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from vt_protocol.decisions.models import Dimension

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Facets — 7 top-level categories (developer-facing)
# ---------------------------------------------------------------------------

FACETS: dict[str, list[Dimension]] = {
    "data": [Dimension.DATABASE],
    "api": [Dimension.API_STYLE, Dimension.MESSAGING],
    "infrastructure": [Dimension.DEPLOYMENT, Dimension.LOGGING],
    "security": [Dimension.AUTH, Dimension.SECURITY],
    "quality": [Dimension.TESTING, Dimension.ERROR_HANDLING],
    "architecture": [Dimension.STATE_MANAGEMENT, Dimension.CACHING, Dimension.CONCURRENCY],
    "frontend": [],  # No core dimension yet — maps to nearest
}


# ---------------------------------------------------------------------------
# SubDimension — one of 42 detailed dimensions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubDimension:
    """A single detailed dimension in the 42-dimension taxonomy."""

    id: str
    label: str
    core_dimension: Dimension
    facet: str
    python_packages: tuple[str, ...] = ()
    node_packages: tuple[str, ...] = ()
    file_patterns: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()
    directory_patterns: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Full 42-dimension taxonomy
# ---------------------------------------------------------------------------

TAXONOMY: tuple[SubDimension, ...] = (
    # ── DATA facet (6) ─────────────────────────────────────────────────
    SubDimension(
        "database.relational", "Relational Database", Dimension.DATABASE, "data",
        python_packages=("psycopg", "psycopg2", "asyncpg", "pymysql", "mysqlclient",
                         "sqlite3", "aiosqlite"),
        node_packages=("pg", "mysql2", "better-sqlite3", "knex", "sequelize", "prisma"),
        config_files=("alembic.ini",),
    ),
    SubDimension(
        "database.nosql", "NoSQL Database", Dimension.DATABASE, "data",
        python_packages=("pymongo", "motor", "redis", "cassandra-driver"),
        node_packages=("mongodb", "mongoose", "redis", "ioredis", "dynamodb"),
    ),
    SubDimension(
        "database.orm", "ORM / Data Access", Dimension.DATABASE, "data",
        python_packages=("sqlalchemy", "django", "tortoise-orm", "peewee", "sqlmodel"),
        node_packages=("prisma", "typeorm", "sequelize", "drizzle-orm", "knex"),
    ),
    SubDimension(
        "database.migration", "Schema Migration", Dimension.DATABASE, "data",
        python_packages=("alembic", "django"),
        node_packages=("knex", "prisma", "typeorm"),
        file_patterns=("migrations/", "alembic/"),
        config_files=("alembic.ini",),
        directory_patterns=("migrations",),
    ),
    SubDimension(
        "database.search", "Search Engine", Dimension.DATABASE, "data",
        python_packages=("elasticsearch", "opensearchpy", "meilisearch", "whoosh"),
        node_packages=("@elastic/elasticsearch", "meilisearch", "algolia"),
    ),
    SubDimension(
        "database.graph", "Graph Database", Dimension.DATABASE, "data",
        python_packages=("neo4j", "py2neo", "falkordb", "kuzu"),
        node_packages=("neo4j-driver",),
    ),
    # ── API facet (6) ──────────────────────────────────────────────────
    SubDimension(
        "api.rest", "REST API", Dimension.API_STYLE, "api",
        python_packages=("fastapi", "flask", "django-rest-framework", "starlette",
                         "falcon", "bottle", "sanic"),
        node_packages=("express", "fastify", "koa", "hapi", "nest"),
    ),
    SubDimension(
        "api.graphql", "GraphQL", Dimension.API_STYLE, "api",
        python_packages=("strawberry-graphql", "graphene", "ariadne"),
        node_packages=("apollo-server", "graphql", "type-graphql", "@graphql-tools/schema"),
        file_patterns=("*.graphql", "schema.graphql"),
    ),
    SubDimension(
        "api.grpc", "gRPC", Dimension.API_STYLE, "api",
        python_packages=("grpcio", "grpcio-tools", "betterproto"),
        node_packages=("@grpc/grpc-js", "grpc"),
        file_patterns=("*.proto",),
    ),
    SubDimension(
        "api.websocket", "WebSocket", Dimension.API_STYLE, "api",
        python_packages=("websockets", "channels", "socketio"),
        node_packages=("ws", "socket.io", "@socketio/server"),
    ),
    SubDimension(
        "api.serialization", "Data Serialization", Dimension.API_STYLE, "api",
        python_packages=("marshmallow", "pydantic", "cattrs", "msgpack", "protobuf",
                         "avro"),
        node_packages=("zod", "joi", "yup", "ajv", "io-ts"),
    ),
    SubDimension(
        "api.versioning", "API Versioning", Dimension.API_STYLE, "api",
        file_patterns=("v1/", "v2/", "api/v1/", "api/v2/"),
        directory_patterns=("v1", "v2"),
    ),
    # ── INFRASTRUCTURE facet (7) ───────────────────────────────────────
    SubDimension(
        "infra.container", "Container", Dimension.DEPLOYMENT, "infrastructure",
        file_patterns=("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                       ".dockerignore"),
        config_files=("Dockerfile", "docker-compose.yml", "docker-compose.yaml"),
    ),
    SubDimension(
        "infra.orchestration", "Orchestration", Dimension.DEPLOYMENT, "infrastructure",
        file_patterns=("k8s/", "kubernetes/", "helm/"),
        config_files=("Chart.yaml", "kustomization.yaml"),
        directory_patterns=("k8s", "kubernetes", "helm", "charts"),
    ),
    SubDimension(
        "infra.ci_cd", "CI/CD", Dimension.DEPLOYMENT, "infrastructure",
        file_patterns=(".github/workflows/", ".gitlab-ci.yml", "Jenkinsfile",
                       ".circleci/"),
        config_files=(".github/workflows", ".gitlab-ci.yml", "Jenkinsfile",
                      ".circleci/config.yml"),
        directory_patterns=(".github", ".circleci"),
    ),
    SubDimension(
        "infra.cloud", "Cloud Provider", Dimension.DEPLOYMENT, "infrastructure",
        python_packages=("boto3", "google-cloud-core", "azure-core"),
        node_packages=("aws-sdk", "@aws-sdk/client-s3", "@google-cloud/storage",
                       "@azure/core-rest-pipeline"),
        config_files=("serverless.yml", "terraform.tf"),
        file_patterns=("*.tf", "serverless.yml"),
    ),
    SubDimension(
        "infra.cdn", "CDN / Static Assets", Dimension.DEPLOYMENT, "infrastructure",
        node_packages=("@cloudflare/workers-types",),
        config_files=("vercel.json", "netlify.toml", "wrangler.toml"),
    ),
    SubDimension(
        "infra.serverless", "Serverless", Dimension.DEPLOYMENT, "infrastructure",
        python_packages=("chalice", "zappa", "mangum"),
        node_packages=("serverless", "@vercel/nft", "aws-lambda"),
        config_files=("serverless.yml", "sam.yaml", "template.yaml"),
    ),
    SubDimension(
        "infra.iac", "Infrastructure as Code", Dimension.DEPLOYMENT, "infrastructure",
        python_packages=("pulumi",),
        node_packages=("@pulumi/pulumi", "cdktf"),
        file_patterns=("*.tf", "Pulumi.yaml"),
        config_files=("Pulumi.yaml", "cdktf.json"),
    ),
    # ── COMMUNICATION facet (4) ────────────────────────────────────────
    SubDimension(
        "comm.queue", "Task Queue", Dimension.MESSAGING, "api",
        python_packages=("celery", "rq", "dramatiq", "huey"),
        node_packages=("bull", "bullmq", "bee-queue"),
    ),
    SubDimension(
        "comm.pubsub", "Pub/Sub & Streaming", Dimension.MESSAGING, "api",
        python_packages=("kafka-python", "confluent-kafka", "nats-py", "aio-pika"),
        node_packages=("kafkajs", "nats", "amqplib"),
    ),
    SubDimension(
        "comm.events", "Event Sourcing", Dimension.MESSAGING, "api",
        python_packages=("eventsourcing",),
        directory_patterns=("events", "event_handlers"),
    ),
    SubDimension(
        "comm.scheduling", "Job Scheduling", Dimension.MESSAGING, "api",
        python_packages=("apscheduler", "celery", "schedule"),
        node_packages=("node-cron", "agenda", "bree"),
        config_files=("crontab",),
    ),
    # ── SECURITY facet (6) ─────────────────────────────────────────────
    SubDimension(
        "security.authn", "Authentication", Dimension.AUTH, "security",
        python_packages=("python-jose", "pyjwt", "authlib", "python-oauth2",
                         "django-allauth", "passlib"),
        node_packages=("passport", "jsonwebtoken", "next-auth", "@auth/core",
                       "lucia"),
    ),
    SubDimension(
        "security.authz", "Authorization", Dimension.AUTH, "security",
        python_packages=("casbin", "django-guardian", "django-rules"),
        node_packages=("casl", "@casbin/node-casbin"),
    ),
    SubDimension(
        "security.encryption", "Encryption", Dimension.SECURITY, "security",
        python_packages=("cryptography", "pynacl", "bcrypt"),
        node_packages=("bcrypt", "crypto-js", "tweetnacl"),
    ),
    SubDimension(
        "security.secrets", "Secrets Management", Dimension.SECURITY, "security",
        python_packages=("hvac", "python-dotenv"),
        node_packages=("dotenv", "@hashicorp/vault"),
        config_files=(".env", ".env.example", ".env.local"),
        file_patterns=(".env*",),
    ),
    SubDimension(
        "security.cors", "CORS", Dimension.SECURITY, "security",
        python_packages=("django-cors-headers", "flask-cors"),
        node_packages=("cors",),
    ),
    SubDimension(
        "security.rate_limiting", "Rate Limiting", Dimension.SECURITY, "security",
        python_packages=("slowapi", "django-ratelimit", "limits"),
        node_packages=("express-rate-limit", "rate-limiter-flexible"),
    ),
    # ── OBSERVABILITY facet (5) ────────────────────────────────────────
    SubDimension(
        "obs.logging", "Structured Logging", Dimension.LOGGING, "infrastructure",
        python_packages=("structlog", "loguru", "python-json-logger"),
        node_packages=("winston", "pino", "bunyan"),
    ),
    SubDimension(
        "obs.monitoring", "Monitoring", Dimension.LOGGING, "infrastructure",
        python_packages=("prometheus-client", "datadog", "sentry-sdk"),
        node_packages=("prom-client", "dd-trace", "@sentry/node"),
        config_files=("prometheus.yml", "datadog.yaml"),
    ),
    SubDimension(
        "obs.tracing", "Distributed Tracing", Dimension.LOGGING, "infrastructure",
        python_packages=("opentelemetry-api", "opentelemetry-sdk", "jaeger-client"),
        node_packages=("@opentelemetry/api", "@opentelemetry/sdk-trace-base"),
    ),
    SubDimension(
        "obs.metrics", "Metrics", Dimension.LOGGING, "infrastructure",
        python_packages=("prometheus-client", "statsd"),
        node_packages=("prom-client", "hot-shots"),
    ),
    SubDimension(
        "obs.alerting", "Alerting", Dimension.LOGGING, "infrastructure",
        config_files=("alertmanager.yml", "pagerduty.yml"),
    ),
    # ── QUALITY facet (6) ──────────────────────────────────────────────
    SubDimension(
        "quality.unit_testing", "Unit Testing", Dimension.TESTING, "quality",
        python_packages=("pytest", "unittest", "nose2"),
        node_packages=("jest", "vitest", "mocha", "ava"),
        directory_patterns=("tests", "test", "__tests__", "spec"),
    ),
    SubDimension(
        "quality.integration_testing", "Integration Testing", Dimension.TESTING, "quality",
        python_packages=("pytest-docker", "testcontainers", "factory-boy"),
        node_packages=("testcontainers", "supertest"),
    ),
    SubDimension(
        "quality.e2e_testing", "End-to-End Testing", Dimension.TESTING, "quality",
        python_packages=("playwright", "selenium"),
        node_packages=("playwright", "@playwright/test", "cypress", "puppeteer"),
        directory_patterns=("e2e", "cypress"),
    ),
    SubDimension(
        "quality.error_handling", "Error Handling", Dimension.ERROR_HANDLING, "quality",
        python_packages=("tenacity", "retry", "backoff"),
        node_packages=("retry", "p-retry"),
    ),
    SubDimension(
        "quality.validation", "Input Validation", Dimension.ERROR_HANDLING, "quality",
        python_packages=("pydantic", "marshmallow", "cerberus", "voluptuous"),
        node_packages=("zod", "joi", "yup", "class-validator"),
    ),
    SubDimension(
        "quality.linting", "Linting & Formatting", Dimension.TESTING, "quality",
        config_files=(".eslintrc", ".eslintrc.js", ".eslintrc.json", ".prettierrc",
                      ".prettierrc.json", "ruff.toml", ".flake8", "setup.cfg",
                      ".pylintrc", "biome.json"),
    ),
    # ── ARCHITECTURE facet (6) ─────────────────────────────────────────
    SubDimension(
        "arch.state", "State Management", Dimension.STATE_MANAGEMENT, "architecture",
        node_packages=("redux", "@reduxjs/toolkit", "zustand", "recoil", "jotai",
                       "mobx", "vuex", "pinia"),
    ),
    SubDimension(
        "arch.caching", "Caching", Dimension.CACHING, "architecture",
        python_packages=("redis", "cachetools", "diskcache", "aiocache",
                         "django-redis"),
        node_packages=("redis", "ioredis", "node-cache", "lru-cache"),
        config_files=("redis.conf",),
    ),
    SubDimension(
        "arch.concurrency", "Concurrency", Dimension.CONCURRENCY, "architecture",
        python_packages=("asyncio", "aiohttp", "trio", "anyio", "multiprocessing"),
        node_packages=("workerpool", "piscina", "threads.js"),
    ),
    SubDimension(
        "arch.di", "Dependency Injection", Dimension.STATE_MANAGEMENT, "architecture",
        python_packages=("dependency-injector", "injector", "python-inject"),
        node_packages=("tsyringe", "inversify", "awilix"),
    ),
    SubDimension(
        "arch.bundling", "Build / Bundling", Dimension.DEPLOYMENT, "architecture",
        node_packages=("webpack", "vite", "esbuild", "rollup", "parcel", "turbopack"),
        config_files=("webpack.config.js", "vite.config.ts", "vite.config.js",
                      "rollup.config.js", "tsconfig.json"),
    ),
    SubDimension(
        "arch.package_mgmt", "Package Management", Dimension.DEPLOYMENT, "architecture",
        config_files=("package.json", "pyproject.toml", "requirements.txt",
                      "Pipfile", "poetry.lock", "pnpm-lock.yaml", "yarn.lock",
                      "Cargo.toml", "go.mod"),
    ),
    # ── INTEGRATION facet (detected via imports + packages) ───────────
    SubDimension(
        "integration.llm", "LLM Provider Integration", Dimension.API_STYLE, "api",
        python_packages=("anthropic", "openai", "cohere", "replicate", "litellm",
                         "langchain", "llama-index", "google-generativeai", "together"),
    ),
    SubDimension(
        "database.sqlite", "SQLite Database", Dimension.DATABASE, "data",
        python_packages=("sqlite3", "aiosqlite"),
    ),
    SubDimension(
        "api.http_server", "HTTP Server (stdlib)", Dimension.API_STYLE, "api",
        python_packages=("http.server", "socketserver", "wsgiref"),
    ),
    SubDimension(
        "arch.threading", "Threading / Concurrency Primitives", Dimension.CONCURRENCY, "architecture",
        python_packages=("threading", "queue", "concurrent.futures", "multiprocessing"),
    ),
    SubDimension(
        "arch.monkey_patching", "Monkey Patching / AOP", Dimension.STATE_MANAGEMENT, "architecture",
        python_packages=("wrapt", "aspectlib", "monkeypatch"),
    ),
    SubDimension(
        "data.similarity", "Similarity Detection", Dimension.DATABASE, "data",
        python_packages=("datasketch", "faiss-cpu", "faiss", "annoy", "hnswlib"),
    ),
    SubDimension(
        "data.ml_embeddings", "ML Embeddings", Dimension.DATABASE, "data",
        python_packages=("sentence-transformers", "transformers", "torch",
                         "tensorflow", "scikit-learn"),
    ),
)

_DIMENSION_COUNT = len(TAXONOMY)  # 46 detailed dimensions across 7 facets

# Lookup maps
_BY_ID: dict[str, SubDimension] = {sd.id: sd for sd in TAXONOMY}
_BY_CORE: dict[Dimension, list[SubDimension]] = {}
for _sd in TAXONOMY:
    _BY_CORE.setdefault(_sd.core_dimension, []).append(_sd)


def get_subdimension(dim_id: str) -> SubDimension | None:
    """Look up a sub-dimension by its ID (e.g. 'database.relational')."""
    return _BY_ID.get(dim_id)


def get_subdimensions_for(core: Dimension) -> list[SubDimension]:
    """All sub-dimensions under a core dimension."""
    return _BY_CORE.get(core, [])


# ---------------------------------------------------------------------------
# Auto-detection result
# ---------------------------------------------------------------------------


@dataclass
class DimensionMatch:
    """A detected dimension with evidence of why it was matched."""

    sub_dimension: SubDimension
    core_dimension: Dimension
    confidence: float  # 0.0–1.0
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Project scanner — 30+ dimensions without any LLM call
# ---------------------------------------------------------------------------


def scan_project(root: Path) -> list[DimensionMatch]:
    """Scan a project directory and return detected dimensions.

    Checks:
    1. Python packages (requirements.txt, pyproject.toml [project].dependencies
       AND [project.optional-dependencies])
    2. Node packages (package.json dependencies + devDependencies)
    3. File existence (Dockerfile, *.proto, etc.)
    4. Directory existence (migrations/, tests/, k8s/, etc.)
    5. Config files (.eslintrc, alembic.ini, etc.)
    6. Python import statements in .py files (stdlib + third-party)
    """
    root = root.resolve()
    py_packages = _scan_python_packages(root)
    node_packages = _scan_node_packages(root)
    existing_files = _scan_files(root)
    existing_dirs = _scan_directories(root)
    py_imports = _scan_python_imports(root)

    matches: list[DimensionMatch] = []

    for sd in TAXONOMY:
        evidence: list[str] = []

        # Python packages (from requirements.txt / pyproject.toml)
        for pkg in sd.python_packages:
            normalized = pkg.replace("-", "_").lower()
            for found in py_packages:
                if found.replace("-", "_").lower() == normalized:
                    evidence.append(f"python:{found}")

        # Python imports (from .py file scanning)
        for pkg in sd.python_packages:
            normalized = pkg.replace("-", "_").lower()
            for found in py_imports:
                if found.replace("-", "_").lower() == normalized:
                    # Avoid duplicate if already found via package files
                    tag = f"import:{found}"
                    if tag not in evidence and f"python:{found}" not in evidence:
                        evidence.append(tag)

        # Node packages
        for pkg in sd.node_packages:
            if pkg in node_packages:
                evidence.append(f"node:{pkg}")

        # File patterns
        for pattern in sd.file_patterns:
            if pattern.endswith("/"):
                # Directory-like file pattern
                dir_name = pattern.rstrip("/")
                if dir_name in existing_dirs:
                    evidence.append(f"dir:{dir_name}/")
            else:
                for f in existing_files:
                    if _matches_pattern(f, pattern):
                        evidence.append(f"file:{f}")
                        break

        # Config files
        for cfg in sd.config_files:
            if cfg in existing_files or cfg in existing_dirs:
                evidence.append(f"config:{cfg}")

        # Directory patterns
        for dp in sd.directory_patterns:
            if dp in existing_dirs:
                evidence.append(f"dir:{dp}")

        if evidence:
            confidence = min(1.0, 0.4 + 0.15 * len(evidence))
            matches.append(DimensionMatch(
                sub_dimension=sd,
                core_dimension=sd.core_dimension,
                confidence=confidence,
                evidence=evidence,
            ))

    return matches


def scan_to_core_dimensions(root: Path) -> list[Dimension]:
    """Scan a project and return deduplicated core Dimension list."""
    matches = scan_project(root)
    seen: set[Dimension] = set()
    result: list[Dimension] = []
    for m in matches:
        if m.core_dimension not in seen:
            seen.add(m.core_dimension)
            result.append(m.core_dimension)
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_package_name(dep: str) -> str:
    """Extract bare package name from a dependency string like 'wrapt>=1.16.0'."""
    name = dep.split(">=")[0].split("<=")[0].split("==")[0].split("!=")[0]
    name = name.split("<")[0].split(">")[0]
    name = name.split("[")[0].split(";")[0].strip()
    return name


def _scan_python_packages(root: Path) -> set[str]:
    """Extract Python package names from requirements.txt and pyproject.toml."""
    packages: set[str] = set()

    # requirements.txt
    for req_file in ("requirements.txt", "requirements-dev.txt", "requirements/base.txt"):
        path = root / req_file
        if path.is_file():
            try:
                for line in path.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    name = _extract_package_name(line)
                    if name:
                        packages.add(name)
            except OSError:
                pass

    # pyproject.toml [project].dependencies AND [project.optional-dependencies]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            project = data.get("project", {})

            # [project.dependencies]
            for dep in project.get("dependencies", []):
                name = _extract_package_name(dep)
                if name:
                    packages.add(name)

            # [project.optional-dependencies] — all groups
            for group_deps in project.get("optional-dependencies", {}).values():
                for dep in group_deps:
                    name = _extract_package_name(dep)
                    if name:
                        packages.add(name)
        except Exception:
            logger.debug("Failed to parse pyproject.toml", exc_info=True)

    # Pipfile
    pipfile = root / "Pipfile"
    if pipfile.is_file():
        try:
            text = pipfile.read_text()
            in_packages = False
            for line in text.splitlines():
                if line.strip().startswith("[packages]") or line.strip().startswith("[dev-packages]"):
                    in_packages = True
                    continue
                if line.strip().startswith("[") and in_packages:
                    in_packages = False
                if in_packages and "=" in line:
                    name = line.split("=")[0].strip().strip('"')
                    if name:
                        packages.add(name)
        except OSError:
            pass

    return packages


def _scan_node_packages(root: Path) -> set[str]:
    """Extract Node.js package names from package.json."""
    packages: set[str] = set()
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text())
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                packages.update(data.get(section, {}).keys())
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to parse package.json", exc_info=True)
    return packages


def _scan_files(root: Path) -> set[str]:
    """Return set of file names/paths (relative, depth-limited) at root level."""
    files: set[str] = set()
    try:
        for entry in root.iterdir():
            if entry.is_file():
                files.add(entry.name)
            elif entry.is_dir() and not entry.name.startswith("."):
                # One level deep for config files in subdirs
                for sub in entry.iterdir():
                    if sub.is_file():
                        files.add(f"{entry.name}/{sub.name}")
    except OSError:
        pass
    return files


def _scan_directories(root: Path) -> set[str]:
    """Return set of directory names at root level."""
    dirs: set[str] = set()
    try:
        for entry in root.iterdir():
            if entry.is_dir():
                dirs.add(entry.name)
    except OSError:
        pass
    return dirs


def _scan_python_imports(root: Path) -> set[str]:
    """Scan .py files for import statements using regex analyzer.

    Returns a set of module names found in import statements (both top-level
    and dotted paths like 'http.server').
    """
    import re

    _IMPORT_RE = re.compile(
        r"^(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.MULTILINE
    )

    modules: set[str] = set()
    skip_dirs = {
        "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
        ".git", ".tox", ".mypy_cache", ".pytest_cache", ".eggs",
        "site-packages",
    }

    try:
        for py_file in root.rglob("*.py"):
            # Skip non-source directories
            parts = py_file.relative_to(root).parts
            if any(p in skip_dirs for p in parts):
                continue
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for m in _IMPORT_RE.finditer(source):
                if m.group(1):  # from X import Y
                    mod = m.group(1)
                    if not mod.startswith("."):
                        modules.add(mod)
                        # Also add top-level for dotted imports
                        top = mod.split(".")[0]
                        if top:
                            modules.add(top)
                elif m.group(2):  # import X, Y
                    for mod in m.group(2).split(","):
                        mod = mod.strip().split(" as ")[0].strip()
                        if mod and not mod.startswith("."):
                            modules.add(mod)
                            top = mod.split(".")[0]
                            if top:
                                modules.add(top)
    except OSError:
        pass

    return modules


def _matches_pattern(filename: str, pattern: str) -> bool:
    """Simple glob-like matching for file patterns."""
    if pattern.startswith("*"):
        return filename.endswith(pattern[1:])
    return filename == pattern
