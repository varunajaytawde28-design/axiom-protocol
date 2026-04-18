#!/usr/bin/env bash
# VT Protocol governance hook
# VT Protocol — Claude Code PostToolUse hook
#
# Runs after Write/Edit succeeds. Re-scans for new assumption patterns,
# then checks for contradictions. If found, vt check saves them to
# .smm/contradictions/, and this hook logs + warns Claude Code.

set -euo pipefail

DEBUG_LOG="/tmp/vt-post-write-debug.log"

_debug() {
    printf '[%s] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >> "$DEBUG_LOG" 2>/dev/null || true
}

_debug "=== Hook triggered ==="
_debug "PWD=$PWD"

INPUT=$(cat)
_debug "Raw input (truncated): $(printf '%s' "$INPUT" | head -c 500)"

# Claude Code sends {"tool_name":"Write"} at top level in PostToolUse hooks
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // .tool.name // empty' 2>/dev/null) || true
_debug "Parsed TOOL_NAME=$TOOL_NAME"

case "$TOOL_NAME" in
    Write|Edit)
        _debug "Matched tool: $TOOL_NAME — proceeding"
        ;;
    *)
        _debug "Tool '$TOOL_NAME' not matched — exiting early"
        exit 0
        ;;
esac

# Find .smm/ directory for trace logging and contradiction storage
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
    jq -n -c \
        --arg ts "$ts" \
        --arg action "$TOOL_NAME" \
        --arg result "$result" \
        --arg reason "$reason" \
        '{timestamp: $ts, type: "hook", action: ("post:" + $action), file: "", result: $result, reason: $reason, agent: "claude-code"}' \
        >> "$traces_dir/events.jsonl" 2>/dev/null || true
    _debug "Trace logged: result=$result reason=$reason"
}

if ! command -v vt >/dev/null 2>&1; then
    _debug "ERROR: 'vt' command not found in PATH"
    _debug "PATH=$PATH"
    exit 0
fi
_debug "'vt' found at $(command -v vt)"

# --- Step 1: vt infer (SYNCHRONOUS) ---
# Creates new decision files in .smm/decisions/ for any newly detected patterns.
# Note: vt infer has no --quiet flag — only --path is available.
_debug "Running: vt infer"
INFER_OUTPUT=$(vt infer 2>&1) || true
INFER_EXIT=$?
_debug "vt infer exit=$INFER_EXIT output: $INFER_OUTPUT"

# --- Step 2: vt check (SYNCHRONOUS, runs AFTER infer) ---
# Detects contradictions across decisions. Saves them to .smm/contradictions/.
# Use plain text output and grep for FAIL — simpler and fewer failure modes
# than JSON parsing. The --json-output flag is also available but jq parsing
# adds a dependency that can silently fail.
_debug "Running: vt check"
CHECK_OUTPUT=$(vt check 2>&1) || true
_debug "vt check output: $CHECK_OUTPUT"

# Check for FAIL in plain-text output (vt check prints "**Result: FAIL**" when violations exist)
FOUND_FAIL=""
if printf '%s' "$CHECK_OUTPUT" | grep -q "FAIL"; then
    FOUND_FAIL="yes"
fi
_debug "FAIL detected: ${FOUND_FAIL:-no}"

if [ -n "$FOUND_FAIL" ]; then
    _debug "Contradictions found! (vt check already saved to .smm/contradictions/)"

    # Extract actionable count from check output if possible
    ACTIONABLE_COUNT=$(printf '%s' "$CHECK_OUTPUT" | grep -oE '[0-9]+ actionable' | grep -oE '[0-9]+' || echo "some")
    _log_trace "contradiction" "${ACTIONABLE_COUNT} contradiction(s) detected after write"

    # --- STATE MACHINE: Create contradiction.lock ---
    # This transitions the system to CONTRADICTION_DETECTED state.
    # PreToolUse hook will block ALL writes until a human resolves via
    # dashboard or 'vt check --resolve' in a separate terminal.
    SMM_DIR=$(_find_smm_dir 2>/dev/null) || true
    if [ -n "$SMM_DIR" ]; then
        LOCK_FILE="$SMM_DIR/contradiction.lock"
        # Extract first contradiction ID from .smm/contradictions/ if possible
        CONTRADICTION_ID=""
        CONTRADICTION_DESC=""
        for cfile in "$SMM_DIR"/contradictions/contradiction-*.json; do
            [ -f "$cfile" ] || continue
            C_STATUS=$(jq -r '.status // ""' "$cfile" 2>/dev/null) || true
            if [ "$C_STATUS" = "unresolved" ]; then
                CONTRADICTION_ID=$(jq -r '.id // ""' "$cfile" 2>/dev/null) || true
                C_TITLE_A=$(jq -r '.decision_a_title // ""' "$cfile" 2>/dev/null) || true
                C_TITLE_B=$(jq -r '.decision_b_title // ""' "$cfile" 2>/dev/null) || true
                CONTRADICTION_DESC="${C_TITLE_A} vs ${C_TITLE_B}"
                break
            fi
        done
        TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        jq -n \
            --arg cid "$CONTRADICTION_ID" \
            --arg ts "$TS" \
            --arg desc "$CONTRADICTION_DESC" \
            --arg msg "Resolve via dashboard or 'vt check --resolve' in a separate terminal" \
            '{contradiction_id: $cid, detected_at: $ts, description: $desc, message: $msg}' \
            > "$LOCK_FILE" 2>/dev/null || true
        _debug "Created contradiction.lock: id=$CONTRADICTION_ID desc=$CONTRADICTION_DESC"
    fi

    # Build message — vt check already saved the files, so just report
    MSG="VT PROTOCOL: ${ACTIONABLE_COUNT} actionable contradiction(s) detected after write! Run: vt check --resolve or open dashboard"

    _debug "Returning block decision to Claude Code"
    jq -n --arg msg "$MSG" '{
        decision: "block",
        reason: $msg,
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: $msg
        }
    }'
    exit 0
fi

_log_trace "pass" ""
_debug "No contradictions — hook completed successfully"
exit 0
