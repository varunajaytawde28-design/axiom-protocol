#!/usr/bin/env bash
# VT Protocol governance hook
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
_find_smm_dir() {
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.smm" ]; then
            echo "$dir/.smm"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}

_log_trace() {
    local result="$1" reason="$2"
    local smm_dir
    smm_dir=$(_find_smm_dir 2>/dev/null) || return 0
    local traces_dir="$smm_dir/traces"
    mkdir -p "$traces_dir"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local file_val="${FILE_PATH:-}"
    jq -n -c \
        --arg ts "$ts" \
        --arg action "$TOOL_NAME" \
        --arg file "$file_val" \
        --arg result "$result" \
        --arg reason "$reason" \
        '{timestamp: $ts, type: "hook", action: $action, file: $file, result: $result, reason: $reason, agent: "claude-code"}' \
        >> "$traces_dir/events.jsonl" 2>/dev/null || true
}

# --- STATE MACHINE: Check contradiction lock FIRST ---
SMM_DIR=$(_find_smm_dir 2>/dev/null) || true
LOCK_FILE="${SMM_DIR}/contradiction.lock"

if [ -n "$SMM_DIR" ] && [ -f "$LOCK_FILE" ]; then
    # Lock exists — we are in CONTRADICTION_DETECTED state

    # Block Bash commands that attempt self-resolution
    if [ "$TOOL_NAME" = "Bash" ]; then
        BASH_CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
        if printf '%s' "$BASH_CMD" | grep -qE 'vt (check --resolve|resolve)'; then
            _log_trace "block" "Agent attempted self-resolve while contradiction.lock active"
            LOCK_MSG=$(jq -r '.message // "Resolve via dashboard or vt check --resolve in a separate terminal"' "$LOCK_FILE" 2>/dev/null || echo "Resolve via dashboard or terminal")
            jq -n --arg reason "BLOCKED: Agent cannot self-resolve contradictions. $LOCK_MSG" '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    permissionDecision: "deny",
                    permissionDecisionReason: ("VT Protocol: " + $reason)
                }
            }'
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
            jq -n --arg reason "BLOCKED: Unresolved contradiction. Human must resolve via dashboard or 'vt check --resolve' before coding can continue. $LOCK_MSG" '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    permissionDecision: "deny",
                    permissionDecisionReason: ("VT Protocol: " + $reason)
                }
            }'
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
    jq -n --arg reason "$REASON" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: ("VT Protocol: " + $reason)
        }
    }'
    exit 0
fi

_log_trace "pass" ""
exit 0
