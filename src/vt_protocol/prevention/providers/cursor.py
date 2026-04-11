"""Cursor rules generator — .cursor/rules/*.mdc files.

Generates Cursor IDE rule files with MDC frontmatter (globs + alwaysApply).

From SPEC: "Auto-generate .cursor/rules/*.mdc — YAML frontmatter with
glob-matched activation."
"""

from __future__ import annotations

from pathlib import Path

from vt_protocol.prevention.priority import ScoredDecision


def generate_cursor_rules(scored: list[ScoredDecision], output_dir: Path) -> list[Path]:
    """Generate .cursor/rules/*.mdc files.

    - "always" tier decisions get alwaysApply: true
    - "auto" tier decisions get globs for file-pattern activation

    Returns list of generated file paths.
    """
    relevant = [sd for sd in scored if sd.tier in ("always", "auto")]
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for sd in relevant:
        d = sd.decision
        slug = d.title.lower().replace(" ", "-")[:50]
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        filename = f"{slug}.mdc"
        filepath = output_dir / filename

        always_apply = sd.tier == "always"
        globs_yaml = ""
        if sd.globs and not always_apply:
            globs_yaml = "globs:\n" + "\n".join(f'  - "{g}"' for g in sd.globs[:5]) + "\n"

        content = d.content.strip()
        if len(content) > 300:
            content = content[:297] + "..."

        text = (
            f"---\n"
            f"description: \"{d.title}\"\n"
            f"{globs_yaml}"
            f"alwaysApply: {'true' if always_apply else 'false'}\n"
            f"---\n\n"
            f"# {d.title}\n\n"
            f"{content}\n"
        )
        filepath.write_text(text)
        paths.append(filepath)

    return paths
