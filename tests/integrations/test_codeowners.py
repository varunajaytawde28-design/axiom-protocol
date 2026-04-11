"""Tests for CODEOWNERS parsing and file-to-owner mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.integrations.codeowners import (
    CodeownersFile,
    CodeownersRule,
    assign_contradiction_owners,
    load_codeowners,
    parse_codeowners,
)


SAMPLE_CODEOWNERS = """\
# Default owners for everything
*       @global-owner1 @global-owner2

# Frontend team owns everything in src/frontend/
src/frontend/**    @frontend-team

# Backend team owns database and API
src/db/**          @backend-team @db-admin
src/api/**         @backend-team

# Security team owns auth
src/auth/**        @security-team @backend-team

# Specific file owners
*.py               @python-team
docker-compose.yml @devops-team
"""


class TestParseCodeowners:
    def test_basic_parse(self) -> None:
        result = parse_codeowners(SAMPLE_CODEOWNERS)
        assert isinstance(result, CodeownersFile)
        assert len(result.rules) == 7  # 7 non-comment, non-blank lines

    def test_empty_file(self) -> None:
        result = parse_codeowners("")
        assert result.rules == []

    def test_comments_only(self) -> None:
        result = parse_codeowners("# comment 1\n# comment 2\n")
        assert result.rules == []

    def test_blank_lines_skipped(self) -> None:
        result = parse_codeowners("* @owner\n\n\n*.py @pyowner\n")
        assert len(result.rules) == 2

    def test_rule_structure(self) -> None:
        result = parse_codeowners("src/** @teamA @teamB\n")
        assert len(result.rules) == 1
        assert result.rules[0].pattern == "src/**"
        assert result.rules[0].owners == ["@teamA", "@teamB"]
        assert result.rules[0].line_number == 1

    def test_single_line_without_owner(self) -> None:
        result = parse_codeowners("*.py\n")
        assert result.rules == []  # pattern with no owner is ignored

    def test_email_owners(self) -> None:
        result = parse_codeowners("*.go team@example.com\n")
        assert result.rules[0].owners == ["team@example.com"]


class TestOwnersFor:
    @pytest.fixture()
    def codeowners(self) -> CodeownersFile:
        return parse_codeowners(SAMPLE_CODEOWNERS)

    def test_global_fallback(self, codeowners: CodeownersFile) -> None:
        # README.md matches * (global) and no more specific rule
        # But it also matches *.py? No, README.md isn't .py
        # Actually, last-match-wins: no py match, just the * rule
        owners = codeowners.owners_for("README.md")
        assert "@global-owner1" in owners

    def test_frontend_match(self, codeowners: CodeownersFile) -> None:
        owners = codeowners.owners_for("src/frontend/components/App.tsx")
        assert "@frontend-team" in owners

    def test_db_match(self, codeowners: CodeownersFile) -> None:
        owners = codeowners.owners_for("src/db/models.py")
        # Last match wins: *.py comes after src/db/** in the file
        assert "@python-team" in owners

    def test_auth_match(self, codeowners: CodeownersFile) -> None:
        owners = codeowners.owners_for("src/auth/jwt.py")
        # Last match: *.py
        assert "@python-team" in owners

    def test_specific_file(self, codeowners: CodeownersFile) -> None:
        owners = codeowners.owners_for("docker-compose.yml")
        assert "@devops-team" in owners

    def test_python_file(self, codeowners: CodeownersFile) -> None:
        owners = codeowners.owners_for("utils/helpers.py")
        assert "@python-team" in owners

    def test_no_match_returns_empty(self) -> None:
        # No global wildcard rule
        co = parse_codeowners("src/** @team\n")
        owners = co.owners_for("README.md")
        assert owners == []


class TestOwnersForFiles:
    def test_batch_lookup(self) -> None:
        co = parse_codeowners("*.py @pyteam\n*.ts @tsteam\n")
        result = co.owners_for_files(["main.py", "app.ts", "README.md"])
        assert result["main.py"] == ["@pyteam"]
        assert result["app.ts"] == ["@tsteam"]
        assert result["README.md"] == []


class TestAllOwners:
    def test_collects_all(self) -> None:
        co = parse_codeowners(SAMPLE_CODEOWNERS)
        owners = co.all_owners()
        assert "@frontend-team" in owners
        assert "@backend-team" in owners
        assert "@security-team" in owners
        assert "@devops-team" in owners


class TestRulesForOwner:
    def test_finds_rules(self) -> None:
        co = parse_codeowners(SAMPLE_CODEOWNERS)
        rules = co.rules_for_owner("@backend-team")
        assert len(rules) >= 2  # src/db/** and src/api/** and src/auth/**


class TestLastMatchWins:
    def test_later_rule_overrides(self) -> None:
        content = "* @default\nsrc/** @team-a\nsrc/special/** @team-b\n"
        co = parse_codeowners(content)
        # src/special/file.py should match team-b (last match)
        assert co.owners_for("src/special/file.py") == ["@team-b"]
        # src/other/file.py matches team-a
        assert co.owners_for("src/other/file.py") == ["@team-a"]


class TestAssignContradictionOwners:
    def test_assigns_from_files(self) -> None:
        co = parse_codeowners("src/db/** @db-team\nsrc/api/** @api-team\n")
        owners = assign_contradiction_owners(
            co,
            ["database"],
            affected_files=["src/db/models.py", "src/api/routes.py"],
        )
        assert "@db-team" in owners
        assert "@api-team" in owners

    def test_no_files_returns_empty(self) -> None:
        co = parse_codeowners("* @owner\n")
        owners = assign_contradiction_owners(co, ["database"])
        assert owners == []

    def test_deduplicated(self) -> None:
        co = parse_codeowners("*.py @team\n")
        owners = assign_contradiction_owners(
            co,
            ["database"],
            affected_files=["a.py", "b.py", "c.py"],
        )
        assert owners == ["@team"]  # Not repeated


class TestLoadCodeowners:
    def test_loads_from_root(self, tmp_path: Path) -> None:
        (tmp_path / "CODEOWNERS").write_text("* @owner\n")
        result = load_codeowners(tmp_path)
        assert result is not None
        assert len(result.rules) == 1

    def test_loads_from_github_dir(self, tmp_path: Path) -> None:
        gh_dir = tmp_path / ".github"
        gh_dir.mkdir()
        (gh_dir / "CODEOWNERS").write_text("*.py @pyteam\n")
        result = load_codeowners(tmp_path)
        assert result is not None
        assert len(result.rules) == 1

    def test_loads_from_docs(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "CODEOWNERS").write_text("docs/** @docs-team\n")
        result = load_codeowners(tmp_path)
        assert result is not None

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        result = load_codeowners(tmp_path)
        assert result is None

    def test_root_takes_priority_over_github(self, tmp_path: Path) -> None:
        (tmp_path / "CODEOWNERS").write_text("* @root-owner\n")
        gh_dir = tmp_path / ".github"
        gh_dir.mkdir()
        (gh_dir / "CODEOWNERS").write_text("* @github-owner\n")
        result = load_codeowners(tmp_path)
        assert result is not None
        assert result.rules[0].owners == ["@root-owner"]


class TestPatternMatching:
    def test_directory_slash_pattern(self) -> None:
        co = parse_codeowners("src/ @owner\n")
        assert co.owners_for("src/file.py") == ["@owner"]
        assert co.owners_for("other/file.py") == []

    def test_star_extension(self) -> None:
        co = parse_codeowners("*.js @js-team\n")
        assert co.owners_for("app.js") == ["@js-team"]
        assert co.owners_for("src/deep/nested/file.js") == ["@js-team"]
        assert co.owners_for("file.py") == []

    def test_double_star_pattern(self) -> None:
        co = parse_codeowners("src/** @team\n")
        assert co.owners_for("src/a.py") == ["@team"]
        assert co.owners_for("src/deep/nested/file.py") == ["@team"]
        assert co.owners_for("other/a.py") == []

    def test_exact_file(self) -> None:
        co = parse_codeowners("Makefile @devops\n")
        assert co.owners_for("Makefile") == ["@devops"]
        # Without leading /, pattern matches basename at any depth (GitHub behavior)
        assert co.owners_for("src/Makefile") == ["@devops"]

    def test_rooted_pattern(self) -> None:
        co = parse_codeowners("/Makefile @devops\n")
        assert co.owners_for("Makefile") == ["@devops"]
        assert co.owners_for("src/Makefile") == []
