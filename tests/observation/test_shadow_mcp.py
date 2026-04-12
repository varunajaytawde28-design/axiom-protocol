"""Tests for shadow MCP discovery."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from vt_protocol.observation.shadow_mcp import (
    MCP_CONFIG_PATHS,
    MCPServerInfo,
    ShadowMCPReport,
    discover_mcp_configs,
    generate_governance_recommendations,
    parse_mcp_config,
    scan_for_shadow_mcps,
)


def _create_mcp_config(root: Path, rel_path: str, config: dict) -> Path:
    full_path = root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(json.dumps(config))
    return full_path


# ---------------------------------------------------------------------------
# MCPServerInfo
# ---------------------------------------------------------------------------


class TestMCPServerInfo:
    def test_defaults(self):
        s = MCPServerInfo()
        assert s.transport == "stdio"
        assert not s.monitored

    def test_to_dict(self):
        s = MCPServerInfo(name="test-server", command="node", monitored=True)
        d = s.to_dict()
        assert d["name"] == "test-server"
        assert d["monitored"] is True


# ---------------------------------------------------------------------------
# ShadowMCPReport
# ---------------------------------------------------------------------------


class TestShadowMCPReport:
    def test_empty_report(self):
        r = ShadowMCPReport()
        assert r.coverage_rate == 1.0
        assert not r.has_shadows

    def test_with_shadows(self):
        r = ShadowMCPReport(
            total_servers=3,
            monitored_servers=1,
            unmonitored_servers=2,
            shadow_servers=[MCPServerInfo(name="s1"), MCPServerInfo(name="s2")],
        )
        assert r.has_shadows
        assert r.coverage_rate == pytest.approx(1 / 3)

    def test_to_dict(self):
        r = ShadowMCPReport(total_servers=2, monitored_servers=1, unmonitored_servers=1)
        d = r.to_dict()
        assert d["has_shadows"] is True
        assert d["coverage_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# discover_mcp_configs
# ---------------------------------------------------------------------------


class TestDiscoverMCPConfigs:
    def test_no_configs(self, tmp_path: Path):
        found = discover_mcp_configs(tmp_path)
        assert len(found) == 0

    def test_find_mcp_json(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".mcp.json", {"mcpServers": {}})
        found = discover_mcp_configs(tmp_path)
        assert len(found) == 1

    def test_find_vscode_config(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".vscode/mcp.json", {"mcpServers": {}})
        found = discover_mcp_configs(tmp_path)
        assert len(found) == 1

    def test_find_cursor_config(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".cursor/mcp.json", {"mcpServers": {}})
        found = discover_mcp_configs(tmp_path)
        assert len(found) == 1

    def test_find_multiple(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".mcp.json", {"mcpServers": {}})
        _create_mcp_config(tmp_path, ".vscode/mcp.json", {"mcpServers": {}})
        found = discover_mcp_configs(tmp_path)
        assert len(found) == 2


# ---------------------------------------------------------------------------
# parse_mcp_config
# ---------------------------------------------------------------------------


class TestParseMCPConfig:
    def test_parse_mcpservers(self, tmp_path: Path):
        config_path = _create_mcp_config(tmp_path, ".mcp.json", {
            "mcpServers": {
                "my-server": {
                    "command": "node",
                    "args": ["index.js"],
                    "transport": "stdio",
                },
            },
        })
        servers = parse_mcp_config(config_path)
        assert len(servers) == 1
        assert servers[0].name == "my-server"
        assert servers[0].command == "node"

    def test_parse_servers_key(self, tmp_path: Path):
        config_path = _create_mcp_config(tmp_path, ".mcp.json", {
            "servers": {
                "alt-server": {"command": "python"},
            },
        })
        servers = parse_mcp_config(config_path)
        assert len(servers) == 1
        assert servers[0].name == "alt-server"

    def test_parse_list_format(self, tmp_path: Path):
        config_path = _create_mcp_config(tmp_path, ".mcp.json", {
            "servers": [
                {"name": "server-1", "command": "node"},
                {"name": "server-2", "command": "python"},
            ],
        })
        servers = parse_mcp_config(config_path)
        assert len(servers) == 2

    def test_parse_invalid_json(self, tmp_path: Path):
        config_path = tmp_path / "bad.json"
        config_path.write_text("not json")
        servers = parse_mcp_config(config_path)
        assert len(servers) == 0

    def test_parse_empty_config(self, tmp_path: Path):
        config_path = _create_mcp_config(tmp_path, ".mcp.json", {})
        servers = parse_mcp_config(config_path)
        assert len(servers) == 0


# ---------------------------------------------------------------------------
# scan_for_shadow_mcps
# ---------------------------------------------------------------------------


class TestScanForShadowMCPs:
    def test_no_configs(self, tmp_path: Path):
        report = scan_for_shadow_mcps(tmp_path)
        assert report.total_servers == 0
        assert not report.has_shadows

    def test_all_monitored(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".mcp.json", {
            "mcpServers": {"my-server": {"command": "node"}},
        })
        report = scan_for_shadow_mcps(
            tmp_path, known_monitored=["my-server"],
        )
        assert report.total_servers == 1
        assert report.monitored_servers == 1
        assert not report.has_shadows

    def test_shadow_detected(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".mcp.json", {
            "mcpServers": {
                "known": {"command": "node"},
                "shadow": {"command": "python"},
            },
        })
        report = scan_for_shadow_mcps(
            tmp_path, known_monitored=["known"],
        )
        assert report.has_shadows
        assert report.unmonitored_servers == 1
        assert report.shadow_servers[0].name == "shadow"

    def test_governance_coverage(self, tmp_path: Path):
        _create_mcp_config(tmp_path, ".mcp.json", {
            "mcpServers": {"my-server": {"command": "node"}},
        })
        report = scan_for_shadow_mcps(
            tmp_path,
            known_monitored=["my-server"],
            governance_covered=["my-server"],
        )
        assert report.all_servers[0].governance_coverage is True


# ---------------------------------------------------------------------------
# generate_governance_recommendations
# ---------------------------------------------------------------------------


class TestGenerateRecommendations:
    def test_no_shadows_no_recommendations(self):
        report = ShadowMCPReport()
        recs = generate_governance_recommendations(report)
        assert len(recs) == 0

    def test_recommendations_for_shadows(self):
        report = ShadowMCPReport(
            unmonitored_servers=2,
            shadow_servers=[
                MCPServerInfo(name="s1", config_path=".mcp.json"),
                MCPServerInfo(name="s2", config_path=".vscode/mcp.json"),
            ],
        )
        recs = generate_governance_recommendations(report)
        assert len(recs) >= 3  # 2 per-server + 1 summary
        assert any("s1" in r for r in recs)
        assert any("s2" in r for r in recs)
