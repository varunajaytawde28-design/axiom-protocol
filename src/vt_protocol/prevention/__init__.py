"""Prevention layer — auto-generate agent instruction files from decision graph.

Modules:
- priority: Score decisions for tier assignment
- rulesync: Orchestrate file generation across providers
- providers/: Agent-specific generators (Claude, Cursor, AGENTS.md)
"""
