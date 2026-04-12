"""Tests for governance rule marketplace — decision template library."""

from __future__ import annotations

import pytest

from vt_protocol.marketplace.templates import (
    DJANGO_POSTGRES_AWS,
    FASTAPI_SQLALCHEMY_DOCKER,
    NEXTJS_PRISMA_VERCEL,
    REACT_TYPESCRIPT_VITE,
    StackTemplate,
    TemplateLibrary,
    get_default_library,
)


# ---------------------------------------------------------------------------
# StackTemplate
# ---------------------------------------------------------------------------


class TestStackTemplate:
    def test_defaults(self):
        t = StackTemplate()
        assert t.name == ""
        assert t.stack == []

    def test_to_dict(self):
        t = StackTemplate(name="test", stack=["react"])
        d = t.to_dict()
        assert d["name"] == "test"
        assert d["stack"] == ["react"]


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------


class TestBuiltinTemplates:
    def test_nextjs_prisma_vercel(self):
        t = NEXTJS_PRISMA_VERCEL
        assert t.name == "nextjs-prisma-vercel"
        assert "nextjs" in t.stack
        assert "prisma" in t.stack
        assert "vercel" in t.stack
        assert len(t.decision_seeds) >= 3
        assert t.governance_config["extends"] == ["@vt/recommended"]

    def test_django_postgres_aws(self):
        t = DJANGO_POSTGRES_AWS
        assert t.name == "django-postgres-aws"
        assert "django" in t.stack
        assert "postgresql" in t.stack
        assert "aws" in t.stack
        assert len(t.decision_seeds) >= 3

    def test_fastapi_sqlalchemy_docker(self):
        t = FASTAPI_SQLALCHEMY_DOCKER
        assert t.name == "fastapi-sqlalchemy-docker"
        assert "fastapi" in t.stack
        assert "sqlalchemy" in t.stack
        assert "docker" in t.stack
        assert len(t.decision_seeds) >= 3

    def test_react_typescript_vite(self):
        t = REACT_TYPESCRIPT_VITE
        assert t.name == "react-typescript-vite"
        assert "react" in t.stack
        assert "typescript" in t.stack
        assert "vite" in t.stack
        assert len(t.decision_seeds) >= 3

    def test_all_templates_have_governance_config(self):
        for t in [NEXTJS_PRISMA_VERCEL, DJANGO_POSTGRES_AWS, FASTAPI_SQLALCHEMY_DOCKER, REACT_TYPESCRIPT_VITE]:
            assert "extends" in t.governance_config
            assert "rules" in t.governance_config


# ---------------------------------------------------------------------------
# TemplateLibrary
# ---------------------------------------------------------------------------


class TestTemplateLibrary:
    def test_empty_library(self):
        lib = TemplateLibrary()
        assert lib.count == 0

    def test_register_and_get(self):
        lib = TemplateLibrary()
        t = StackTemplate(name="test")
        lib.register(t)
        assert lib.count == 1
        assert lib.get("test") is not None

    def test_get_not_found(self):
        lib = TemplateLibrary()
        assert lib.get("nonexistent") is None

    def test_list_templates(self):
        lib = TemplateLibrary()
        lib.register(StackTemplate(name="b"))
        lib.register(StackTemplate(name="a"))
        names = lib.list_templates()
        assert names == ["a", "b"]  # sorted

    def test_search_by_stack(self):
        lib = get_default_library()
        results = lib.search(stack="django")
        assert len(results) == 1
        assert results[0].name == "django-postgres-aws"

    def test_search_by_dimension(self):
        lib = get_default_library()
        results = lib.search(dimension="database")
        assert len(results) >= 2

    def test_search_case_insensitive_stack(self):
        lib = get_default_library()
        results = lib.search(stack="Django")
        assert len(results) == 1

    def test_search_no_results(self):
        lib = get_default_library()
        results = lib.search(stack="nonexistent")
        assert len(results) == 0

    def test_instantiate(self):
        lib = get_default_library()
        config = lib.instantiate("nextjs-prisma-vercel")
        assert config is not None
        assert config["extends"] == ["@vt/recommended"]

    def test_instantiate_with_overrides(self):
        lib = get_default_library()
        config = lib.instantiate("nextjs-prisma-vercel", custom_key="custom_value")
        assert config["custom_key"] == "custom_value"

    def test_instantiate_not_found(self):
        lib = get_default_library()
        assert lib.instantiate("nonexistent") is None

    def test_instantiate_deep_copy(self):
        lib = get_default_library()
        config1 = lib.instantiate("nextjs-prisma-vercel")
        config2 = lib.instantiate("nextjs-prisma-vercel")
        config1["extends"].append("@vt/custom")
        assert "@vt/custom" not in config2["extends"]


# ---------------------------------------------------------------------------
# get_default_library
# ---------------------------------------------------------------------------


class TestGetDefaultLibrary:
    def test_has_all_templates(self):
        lib = get_default_library()
        assert lib.count == 4
        assert lib.get("nextjs-prisma-vercel") is not None
        assert lib.get("django-postgres-aws") is not None
        assert lib.get("fastapi-sqlalchemy-docker") is not None
        assert lib.get("react-typescript-vite") is not None
