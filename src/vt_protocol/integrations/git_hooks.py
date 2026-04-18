"""Git hooks integration — pre-commit and post-commit hooks.

Pre-commit: run contradiction check, block on critical contradictions.
Post-commit: append to Merkle tree audit log, tag with decision metadata.

From SPEC T4: "Pre-commit: smm check runs automatically. Post-commit:
auto-tag commit with decision metadata via git trailers."
"""

from __future__ import annotations

import logging
import stat
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Hook marker so we can detect our hooks and avoid duplicates
_HOOK_MARKER = "# VT Protocol governance hook"

_PRE_COMMIT_HOOK = f"""\
#!/bin/sh
{_HOOK_MARKER}
# Runs `vt check` before each commit.
# Exit code 1 blocks the commit if critical contradictions exist.

if command -v vt >/dev/null 2>&1; then
    vt check --exit-code
    STATUS=$?
    if [ $STATUS -ne 0 ]; then
        echo ""
        echo "VT Protocol: commit blocked due to governance violations."
        echo "Run 'vt check' for details, or use --no-verify to skip."
        exit 1
    fi
fi
"""

_POST_COMMIT_HOOK = f"""\
#!/bin/sh
{_HOOK_MARKER}
# Appends commit metadata to the Merkle tree audit log.
# Then re-scans for new patterns and checks for contradictions.
# Runs in the background — never blocks the commit.

if command -v vt >/dev/null 2>&1; then
    HASH=$(git rev-parse HEAD 2>/dev/null)
    MSG=$(git log -1 --pretty=%s 2>/dev/null)
    AUTHOR=$(git log -1 --pretty=%an 2>/dev/null)
    # Fire and forget — audit log append is best-effort
    vt audit-commit --hash "$HASH" --message "$MSG" --author "$AUTHOR" &

    # Re-scan for new patterns from this commit
    vt infer 2>/dev/null &

    # Check for contradictions and warn prominently
    (
        sleep 2  # wait for infer to settle
        CHECK_OUT=$(vt check 2>/dev/null)
        if printf '%s' "$CHECK_OUT" | grep -q "FAIL"; then
            echo ""
            echo "⚠️  VT PROTOCOL: contradictions detected!"
            echo "   Run 'vt check --resolve' to resolve, or open dashboard."
        fi
    ) &
fi
"""


def install_hooks(project_root: Path) -> list[str]:
    """Install VT Protocol git hooks into .git/hooks/.

    Installs pre-commit and post-commit hooks. If a hook already exists
    and doesn't contain our marker, we append. If it already has our
    marker, we skip (idempotent).

    Returns list of hook names installed or updated.
    """
    git_hooks_dir = project_root / ".git" / "hooks"
    if not git_hooks_dir.is_dir():
        logger.warning("No .git/hooks/ directory found at %s", project_root)
        return []

    installed: list[str] = []

    for name, content in [("pre-commit", _PRE_COMMIT_HOOK), ("post-commit", _POST_COMMIT_HOOK)]:
        hook_path = git_hooks_dir / name
        installed_or_updated = _install_single_hook(hook_path, content)
        if installed_or_updated:
            installed.append(name)

    return installed


def uninstall_hooks(project_root: Path) -> list[str]:
    """Remove VT Protocol hooks from .git/hooks/.

    If the hook file contains only our content, removes the file.
    If it contains other content too, removes only our section.

    Returns list of hook names removed.
    """
    git_hooks_dir = project_root / ".git" / "hooks"
    if not git_hooks_dir.is_dir():
        return []

    removed: list[str] = []

    for name in ("pre-commit", "post-commit"):
        hook_path = git_hooks_dir / name
        if not hook_path.exists():
            continue

        content = hook_path.read_text()
        if _HOOK_MARKER not in content:
            continue

        # Check if the file is entirely ours
        lines = content.split("\n")
        our_lines = []
        other_lines = []
        in_our_section = False
        for line in lines:
            if _HOOK_MARKER in line:
                in_our_section = True
            if in_our_section:
                our_lines.append(line)
            else:
                other_lines.append(line)

        if not any(l.strip() for l in other_lines if not l.startswith("#!")):
            # Entirely ours — remove
            hook_path.unlink()
        else:
            # Has other content — remove only our section
            hook_path.write_text("\n".join(other_lines))
            _make_executable(hook_path)

        removed.append(name)

    return removed


def is_installed(project_root: Path) -> dict[str, bool]:
    """Check which VT Protocol hooks are installed."""
    git_hooks_dir = project_root / ".git" / "hooks"
    result = {"pre-commit": False, "post-commit": False}
    if not git_hooks_dir.is_dir():
        return result

    for name in result:
        hook_path = git_hooks_dir / name
        if hook_path.exists():
            content = hook_path.read_text()
            result[name] = _HOOK_MARKER in content

    return result


def create_mcp_json(project_root: Path) -> Path:
    """Create .mcp.json for Claude Code / Cursor MCP auto-discovery.

    Points to the VT Protocol MCP server so agents get governance
    tools automatically when they open the project.
    """
    import json

    mcp_config = {
        "mcpServers": {
            "vt-protocol": {
                "command": "vt",
                "args": ["serve", "--stdio"],
            }
        }
    }

    path = project_root / ".mcp.json"
    path.write_text(json.dumps(mcp_config, indent=2) + "\n")
    return path


def _install_single_hook(hook_path: Path, content: str) -> bool:
    """Install or append a single hook. Returns True if written."""
    if hook_path.exists():
        existing = hook_path.read_text()
        if _HOOK_MARKER in existing:
            return False  # Already installed
        # Append to existing hook
        hook_path.write_text(existing.rstrip() + "\n\n" + content)
    else:
        hook_path.write_text(content)

    _make_executable(hook_path)
    return True


def install_claude_code_hook(project_root: Path) -> bool:
    """Install the Claude Code PreToolUse hook for VT Protocol governance.

    Creates .claude/hooks/vt-validate.sh and .claude/settings.json.
    The hook intercepts Write/Edit tool calls and validates them against
    active decisions before execution.

    Returns True if installed, False if already present.
    """
    import json

    hooks_dir = project_root / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hooks_dir / "vt-validate.sh"
    settings_path = project_root / ".claude" / "settings.json"

    # Only skip if BOTH the pre-validate AND post-write hooks are present.
    # A previous install may have only created vt-validate.sh (Bug 3 fix).
    post_write_check = hooks_dir / "vt-post-write.sh"
    if (
        hook_path.exists()
        and _HOOK_MARKER in hook_path.read_text()
        and post_write_check.exists()
        and _HOOK_MARKER in post_write_check.read_text()
    ):
        return False

    hook_content = f"""\
#!/usr/bin/env bash
{_HOOK_MARKER}
# VT Protocol — Claude Code PreToolUse hook
#
# STATE MACHINE ENFORCEMENT:
# 1. If .smm/contradiction.lock exists, BLOCK all Write/Edit operations
#    and block Bash commands containing "vt check --resolve" or "vt resolve"
#    (agent must not self-resolve — only humans can resolve)
# 2. Otherwise, validate Write/Edit against active decisions via vt validate-change

set -euo pipefail

INPUT=$(cat)

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

# Find .smm/ directory for trace logging and lock file checks
_find_smm_dir() {{
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.smm" ]; then
            echo "$dir/.smm"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}}

_log_trace() {{
    local result="$1" reason="$2"
    local smm_dir
    smm_dir=$(_find_smm_dir 2>/dev/null) || return 0
    local traces_dir="$smm_dir/traces"
    mkdir -p "$traces_dir"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local file_val="${{FILE_PATH:-}}"
    jq -n -c \\
        --arg ts "$ts" \\
        --arg action "$TOOL_NAME" \\
        --arg file "$file_val" \\
        --arg result "$result" \\
        --arg reason "$reason" \\
        '{{timestamp: $ts, type: "hook", action: $action, file: $file, result: $result, reason: $reason, agent: "claude-code"}}' \\
        >> "$traces_dir/events.jsonl" 2>/dev/null || true
}}

# --- STATE MACHINE: Check contradiction lock FIRST ---
SMM_DIR=$(_find_smm_dir 2>/dev/null) || true
LOCK_FILE="${{SMM_DIR}}/contradiction.lock"

if [ -n "$SMM_DIR" ] && [ -f "$LOCK_FILE" ]; then
    # Lock exists — we are in CONTRADICTION_DETECTED state

    # Block Bash commands that attempt self-resolution
    if [ "$TOOL_NAME" = "Bash" ]; then
        BASH_CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
        if printf '%s' "$BASH_CMD" | grep -qE 'vt (check --resolve|resolve)'; then
            _log_trace "block" "Agent attempted self-resolve while contradiction.lock active"
            LOCK_MSG=$(jq -r '.message // "Resolve via dashboard or vt check --resolve in a separate terminal"' "$LOCK_FILE" 2>/dev/null || echo "Resolve via dashboard or terminal")
            jq -n --arg reason "BLOCKED: Agent cannot self-resolve contradictions. $LOCK_MSG" '{{
                hookSpecificOutput: {{
                    hookEventName: "PreToolUse",
                    permissionDecision: "deny",
                    permissionDecisionReason: ("VT Protocol: " + $reason)
                }}
            }}'
            exit 0
        fi
        # Allow other Bash commands (git, npm, etc.)
        exit 0
    fi

    # Block Write/Edit while locked
    case "$TOOL_NAME" in
        Write|Edit)
            LOCK_MSG=$(jq -r '.message // "Resolve via dashboard or vt check --resolve in a separate terminal"' "$LOCK_FILE" 2>/dev/null || echo "Resolve via dashboard or terminal")
            _log_trace "block" "Unresolved contradiction — writes blocked"
            jq -n --arg reason "BLOCKED: Unresolved contradiction. Human must resolve via dashboard or 'vt check --resolve' before coding can continue. $LOCK_MSG" '{{
                hookSpecificOutput: {{
                    hookEventName: "PreToolUse",
                    permissionDecision: "deny",
                    permissionDecisionReason: ("VT Protocol: " + $reason)
                }}
            }}'
            exit 0
            ;;
    esac

    # Other tools pass through
    exit 0
fi

# --- No lock — normal validation flow ---

case "$TOOL_NAME" in
    Write|Edit) ;;
    *) exit 0 ;;
esac

FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

if [ "$TOOL_NAME" = "Write" ]; then
    CONTENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.content // empty')
elif [ "$TOOL_NAME" = "Edit" ]; then
    CONTENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.new_string // empty')
fi

if [ -z "$CONTENT" ]; then
    exit 0
fi

if ! command -v vt >/dev/null 2>&1; then
    _log_trace "pass" "vt not installed"
    exit 0
fi

RESULT=$(printf '%s' "$CONTENT" | vt validate-change --file-path "$FILE_PATH" 2>/dev/null) || true

STATUS=$(printf '%s' "$RESULT" | jq -r '.status // "pass"')

if [ "$STATUS" = "fail" ]; then
    REASON=$(printf '%s' "$RESULT" | jq -r '.reason // "Governance violation detected"')
    _log_trace "block" "$REASON"
    jq -n --arg reason "$REASON" '{{
        hookSpecificOutput: {{
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: ("VT Protocol: " + $reason)
        }}
    }}'
    exit 0
fi

_log_trace "pass" ""
exit 0
"""
    hook_path.write_text(hook_content)
    _make_executable(hook_path)

    # PostToolUse hook — runs vt infer + check after Write/Edit succeeds
    post_hook_path = hooks_dir / "vt-post-write.sh"
    post_hook_content = f"""\
#!/usr/bin/env bash
{_HOOK_MARKER}
# VT Protocol — Claude Code PostToolUse hook
#
# Runs after Write/Edit succeeds. Re-scans for new assumption patterns,
# then checks for contradictions. If found, saves to .smm/contradictions/,
# logs to events.jsonl, and returns a warning to Claude Code.

set -euo pipefail

INPUT=$(cat)

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

case "$TOOL_NAME" in
    Write|Edit) ;;
    *) exit 0 ;;
esac

# Find .smm/ directory for trace logging and contradiction storage
_find_smm_dir() {{
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.smm" ]; then
            echo "$dir/.smm"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}}

_log_trace() {{
    local result="$1" reason="$2"
    local smm_dir
    smm_dir=$(_find_smm_dir 2>/dev/null) || return 0
    local traces_dir="$smm_dir/traces"
    mkdir -p "$traces_dir"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    jq -n -c \\
        --arg ts "$ts" \\
        --arg action "$TOOL_NAME" \\
        --arg result "$result" \\
        --arg reason "$reason" \\
        '{{timestamp: $ts, type: "hook", action: ("post:" + $action), file: "", result: $result, reason: $reason, agent: "claude-code"}}' \\
        >> "$traces_dir/events.jsonl" 2>/dev/null || true
}}

if ! command -v vt >/dev/null 2>&1; then
    exit 0
fi

DEBUG_LOG="/tmp/vt-post-write-debug.log"
_debug() {{
    printf '[%s] %s\\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >> "$DEBUG_LOG" 2>/dev/null || true
}}

_debug "=== PostToolUse hook triggered ==="
_debug "PWD=$PWD TOOL=$TOOL_NAME"

# Re-scan for new patterns (vt infer has no --quiet flag, only --path)
_debug "Running: vt infer"
INFER_OUT=$(vt infer 2>&1) || true
_debug "vt infer output: $INFER_OUT"

# Check for contradictions using plain text + grep (simpler than JSON parsing)
_debug "Running: vt check"
CHECK_OUT=$(vt check 2>&1) || true
_debug "vt check output: $CHECK_OUT"

FOUND_FAIL=""
if printf '%s' "$CHECK_OUT" | grep -q "FAIL"; then
    FOUND_FAIL="yes"
fi
_debug "FAIL detected: ${{FOUND_FAIL:-no}}"

if [ -n "$FOUND_FAIL" ]; then
    ACTIONABLE_COUNT=$(printf '%s' "$CHECK_OUT" | grep -oE '[0-9]+ actionable' | grep -oE '[0-9]+' || echo "some")

    _log_trace "contradiction" "$ACTIONABLE_COUNT contradiction(s) detected after write"

    # --- STATE MACHINE: Create contradiction.lock ---
    # This transitions the system to CONTRADICTION_DETECTED state.
    # PreToolUse hook will block ALL writes until a human resolves via
    # dashboard or 'vt check --resolve' in a separate terminal.
    SMM_DIR=$(_find_smm_dir 2>/dev/null) || true
    if [ -n "$SMM_DIR" ]; then
        LOCK_FILE="$SMM_DIR/contradiction.lock"
        CONTRADICTION_ID=""
        CONTRADICTION_DESC=""
        for cfile in "$SMM_DIR"/contradictions/contradiction-*.json; do
            [ -f "$cfile" ] || continue
            C_STATUS=$(jq -r '.status // ""' "$cfile" 2>/dev/null) || true
            if [ "$C_STATUS" = "unresolved" ]; then
                CONTRADICTION_ID=$(jq -r '.id // ""' "$cfile" 2>/dev/null) || true
                C_TITLE_A=$(jq -r '.decision_a_title // ""' "$cfile" 2>/dev/null) || true
                C_TITLE_B=$(jq -r '.decision_b_title // ""' "$cfile" 2>/dev/null) || true
                CONTRADICTION_DESC="${{C_TITLE_A}} vs ${{C_TITLE_B}}"
                break
            fi
        done
        TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        jq -n \\
            --arg cid "$CONTRADICTION_ID" \\
            --arg ts "$TS" \\
            --arg desc "$CONTRADICTION_DESC" \\
            --arg msg "Resolve via dashboard or 'vt check --resolve' in a separate terminal" \\
            '{{contradiction_id: $cid, detected_at: $ts, description: $desc, message: $msg}}' \\
            > "$LOCK_FILE" 2>/dev/null || true
        _debug "Created contradiction.lock: id=$CONTRADICTION_ID desc=$CONTRADICTION_DESC"
    fi

    MSG="VT PROTOCOL: $ACTIONABLE_COUNT actionable contradiction(s) detected after write! Run: vt check --resolve or open dashboard"
    _debug "Returning block: $MSG"

    jq -n --arg msg "$MSG" '{{
        decision: "block",
        reason: $msg,
        hookSpecificOutput: {{
            hookEventName: "PostToolUse",
            additionalContext: $msg
        }}
    }}'
    exit 0
fi

_log_trace "pass" ""
exit 0
"""
    post_hook_path.write_text(post_hook_content)
    _make_executable(post_hook_path)

    # Write or merge .claude/settings.json
    hook_config = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit|Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/vt-validate.sh",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/vt-post-write.sh",
                            "timeout": 120,
                        }
                    ],
                }
            ],
        }
    }

    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
            # Merge hooks into existing config
            if "hooks" not in existing:
                existing["hooks"] = hook_config["hooks"]
            else:
                for event in ("PreToolUse", "PostToolUse"):
                    if event not in existing["hooks"]:
                        existing["hooks"][event] = hook_config["hooks"][event]
                    else:
                        matchers = [h.get("matcher") for h in existing["hooks"][event]]
                        if "Write|Edit" not in matchers:
                            existing["hooks"][event].extend(hook_config["hooks"][event])
            settings_path.write_text(json.dumps(existing, indent=2) + "\n")
        except (json.JSONDecodeError, KeyError):
            settings_path.write_text(json.dumps(hook_config, indent=2) + "\n")
    else:
        settings_path.write_text(json.dumps(hook_config, indent=2) + "\n")

    return True


def _make_executable(path: Path) -> None:
    """chmod +x a file."""
    current = path.stat().st_mode
    path.chmod(current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
