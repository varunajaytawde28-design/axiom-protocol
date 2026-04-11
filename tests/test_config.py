"""Tests for governance.yaml parser and config utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.config import (
    DEFAULT_GOVERNANCE_YAML,
    ensure_smm_structure,
    find_project_root,
    load_governance_config,
    save_governance_config,
)
from vt_protocol.decisions.models import GovernanceConfig
from vt_protocol.exceptions import GovernanceConfigError


class TestFindProjectRoot:
    def test_finds_git_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        root = find_project_root(tmp_path)
        assert root == tmp_path

    def test_finds_smm_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".smm").mkdir()
        root = find_project_root(tmp_path)
        assert root == tmp_path

    def test_finds_parent(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        root = find_project_root(child)
        assert root == tmp_path

    def test_raises_if_not_found(self, tmp_path: Path) -> None:
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        with pytest.raises(FileNotFoundError):
            find_project_root(isolated)


class TestEnsureSmmStructure:
    def test_creates_all_subdirs(self, tmp_path: Path) -> None:
        smm = ensure_smm_structure(tmp_path)
        assert (smm / "decisions").is_dir()
        assert (smm / "cache").is_dir()
        assert (smm / "generated").is_dir()
        assert (smm / "audit").is_dir()

    def test_creates_gitignore(self, tmp_path: Path) -> None:
        smm = ensure_smm_structure(tmp_path)
        gi = smm / ".gitignore"
        assert gi.is_file()
        content = gi.read_text()
        assert "cache/" in content
        assert "audit/" in content

    def test_idempotent(self, tmp_path: Path) -> None:
        ensure_smm_structure(tmp_path)
        ensure_smm_structure(tmp_path)  # Should not raise
        assert (tmp_path / ".smm" / "decisions").is_dir()


class TestLoadGovernanceConfig:
    def test_defaults_when_missing(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        cfg = load_governance_config(tmp_path)
        assert "@vt/recommended" in cfg.extends
        assert cfg.agents["claude"] is True
        assert cfg.rules.freeze_on_adopt is True
        assert cfg.rules.contradiction_threshold == 0.7

    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "extends:\n"
            '  - "@vt/recommended"\n'
            '  - "@vt/security-baseline"\n'
            "agents:\n"
            "  claude: true\n"
            "  cursor: false\n"
            "rules:\n"
            "  freeze-on-adopt: false\n"
            "  contradiction-threshold: 0.5\n"
            "  max-new-deps-per-task: 5\n"
            "decisions:\n"
            '  path: ".smm/decisions/"\n'
        )
        cfg = load_governance_config(tmp_path)
        assert len(cfg.extends) == 2
        assert "@vt/security-baseline" in cfg.extends
        assert cfg.agents["cursor"] is False
        assert cfg.rules.freeze_on_adopt is False
        assert cfg.rules.contradiction_threshold == 0.5
        assert cfg.rules.max_new_deps_per_task == 5

    def test_load_minimal_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text("extends: ['@vt/recommended']\n")
        cfg = load_governance_config(tmp_path)
        assert cfg.extends == ["@vt/recommended"]
        # Defaults filled in
        assert cfg.rules.freeze_on_adopt is True

    def test_empty_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text("")
        cfg = load_governance_config(tmp_path)
        assert isinstance(cfg, GovernanceConfig)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text("{{invalid yaml!!")
        with pytest.raises(GovernanceConfigError):
            load_governance_config(tmp_path)

    def test_non_dict_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text("- just\n- a\n- list\n")
        with pytest.raises(GovernanceConfigError):
            load_governance_config(tmp_path)

    def test_kebab_and_snake_case_both_work(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "rules:\n"
            "  freeze_on_adopt: true\n"
            "  contradiction_threshold: 0.8\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.rules.freeze_on_adopt is True
        assert cfg.rules.contradiction_threshold == 0.8

    def test_extends_as_string(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text('extends: "@vt/recommended"\n')
        cfg = load_governance_config(tmp_path)
        assert cfg.extends == ["@vt/recommended"]


class TestSaveGovernanceConfig:
    def test_save_default(self, tmp_path: Path) -> None:
        path = save_governance_config(tmp_path)
        assert path.is_file()
        content = path.read_text()
        assert "extends:" in content
        assert "freeze-on-adopt" in content

    def test_save_custom_config(self, tmp_path: Path) -> None:
        cfg = GovernanceConfig(
            extends=["@vt/recommended", "@myorg/rules"],
            agents={"claude": True, "cursor": True, "copilot": False},
        )
        save_governance_config(tmp_path, cfg)
        # Round-trip: load it back
        loaded = load_governance_config(tmp_path)
        assert loaded.extends == cfg.extends
        assert loaded.agents["copilot"] is False

    def test_round_trip_preserves_rules(self, tmp_path: Path) -> None:
        from vt_protocol.decisions.models import GovernanceRules

        cfg = GovernanceConfig(
            rules=GovernanceRules(
                freeze_on_adopt=False,
                contradiction_threshold=0.3,
                max_new_deps_per_task=10,
            ),
        )
        save_governance_config(tmp_path, cfg)
        loaded = load_governance_config(tmp_path)
        assert loaded.rules.freeze_on_adopt is False
        assert loaded.rules.contradiction_threshold == 0.3
        assert loaded.rules.max_new_deps_per_task == 10
