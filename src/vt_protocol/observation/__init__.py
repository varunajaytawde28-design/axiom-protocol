"""Observation engine — SDK patching, causal detection, span capture.

Modules:
- tainted_str: TaintedStr for causal metadata propagation
- patch: wrapt-based monkey-patching for Anthropic + OpenAI
- signals: Seven golden signals detection
- secrets: Secret detection and redaction
- cache: File-hash change detection
- analyzers: Tree-sitter + regex code analysis
- models: Span, CausalEdge, AgentInfo data models
"""
