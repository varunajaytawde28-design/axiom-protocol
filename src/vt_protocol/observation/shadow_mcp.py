"""Shadow MCP discovery.

Scan the workspace for MCP server configurations that may be running
without VT Protocol governance coverage. Inventory all MCP servers,
flag unmonitored ones, and generate coverage reports.

From SPEC Sprint 24: "Shadow MCP discovery."
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Known MCP config file locations
MCP_CONFIG_PATHS: list[str] = [
    ".mcp.json",
    ".mcp/config.json",
    ".vscode/mcp.json",
    ".cursor/mcp.json",
    ".claude/mcp.json",
    "mcp.json",
]


@dataclass
class MCPServerInfo:
    """Information about a discovered MCP server."""

    name: str = ""
    config_path: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio", "http", "ws"
    monitored: bool = False
    governance_coverage: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "config_path": self.config_path,
            "command": self.command,
            "args": self.args,
            "transport": self.transport,
            "monitored": self.monitored,
            "governance_coverage": self.governance_coverage,
        }


@dataclass
class ShadowMCPReport:
    """Report of MCP server discovery scan."""

    total_configs_found: int = 0
    total_servers: int = 0
    monitored_servers: int = 0
    unmonitored_servers: int = 0
    shadow_servers: list[MCPServerInfo] = field(default_factory=list)
    all_servers: list[MCPServerInfo] = field(default_factory=list)
    config_files_scanned: list[str] = field(default_factory=list)

    @property
    def coverage_rate(self) -> float:
        if self.total_servers == 0:
            return 1.0
        return self.monitored_servers / self.total_servers

    @property
    def has_shadows(self) -> bool:
        return self.unmonitored_servers > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_configs_found": self.total_configs_found,
            "total_servers": self.total_servers,
            "monitored_servers": self.monitored_servers,
            "unmonitored_servers": self.unmonitored_servers,
            "coverage_rate": round(self.coverage_rate, 4),
            "has_shadows": self.has_shadows,
            "shadow_servers": [s.to_dict() for s in self.shadow_servers],
            "all_servers": [s.to_dict() for s in self.all_servers],
            "config_files_scanned": self.config_files_scanned,
        }


def discover_mcp_configs(root: Path) -> list[Path]:
    """Find all MCP configuration files under a root directory."""
    found: list[Path] = []
    for rel_path in MCP_CONFIG_PATHS:
        full_path = root / rel_path
        if full_path.exists() and full_path.is_file():
            found.append(full_path)
    return found


def parse_mcp_config(config_path: Path) -> list[MCPServerInfo]:
    """Parse an MCP config file and extract server definitions."""
    servers: list[MCPServerInfo] = []

    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse %s: %s", config_path, e)
        return servers

    # Handle various MCP config formats
    server_defs = data.get("mcpServers", data.get("servers", {}))
    if isinstance(server_defs, dict):
        for name, config in server_defs.items():
            server = MCPServerInfo(
                name=name,
                config_path=str(config_path),
                command=config.get("command", ""),
                args=config.get("args", []),
                env=config.get("env", {}),
                transport=config.get("transport", "stdio"),
            )
            servers.append(server)
    elif isinstance(server_defs, list):
        for config in server_defs:
            server = MCPServerInfo(
                name=config.get("name", "unnamed"),
                config_path=str(config_path),
                command=config.get("command", ""),
                args=config.get("args", []),
                transport=config.get("transport", "stdio"),
            )
            servers.append(server)

    return servers


def scan_for_shadow_mcps(
    root: Path,
    *,
    known_monitored: list[str] | None = None,
    governance_covered: list[str] | None = None,
) -> ShadowMCPReport:
    """Scan workspace for MCP servers and identify unmonitored ones.

    Args:
        root: Workspace root directory.
        known_monitored: List of server names known to be monitored by VT Protocol.
        governance_covered: List of server names with governance coverage.
    """
    monitored_set = set(known_monitored or [])
    governed_set = set(governance_covered or [])

    report = ShadowMCPReport()

    # Discover config files
    config_files = discover_mcp_configs(root)
    report.total_configs_found = len(config_files)
    report.config_files_scanned = [str(p) for p in config_files]

    # Parse all servers
    for config_path in config_files:
        servers = parse_mcp_config(config_path)
        for server in servers:
            server.monitored = server.name in monitored_set
            server.governance_coverage = server.name in governed_set
            report.all_servers.append(server)

            if not server.monitored:
                report.shadow_servers.append(server)

    report.total_servers = len(report.all_servers)
    report.monitored_servers = sum(1 for s in report.all_servers if s.monitored)
    report.unmonitored_servers = report.total_servers - report.monitored_servers

    return report


def generate_governance_recommendations(report: ShadowMCPReport) -> list[str]:
    """Generate recommendations for bringing shadow MCPs under governance."""
    recommendations: list[str] = []

    for server in report.shadow_servers:
        recommendations.append(
            f"Add '{server.name}' (from {server.config_path}) to governance config "
            f"— currently running without VT Protocol monitoring."
        )

    if report.unmonitored_servers > 0:
        recommendations.append(
            f"Total: {report.unmonitored_servers} unmonitored MCP server(s) found. "
            f"Add them to governance.yaml agents section."
        )

    return recommendations
