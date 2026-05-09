#!/usr/bin/env bash
# AgentOrchestra Claude Code hook bridge.
#
# Invoked by Claude Code for SessionStart, PreToolUse, PostToolUse,
# Stop, SubagentStop hooks.  Reads a JSON payload from stdin and
# POSTs it to the orchestrator's /ingest/hook endpoint with the
# bearer token saved by the GUI/service on first launch.
#
# Two env vars must be set (the installer writes them into Claude
# Code's settings.json so they're available to every hook invocation):
#
#   AGENTORCHESTRA_URL    e.g. http://127.0.0.1:8765
#   AGENTORCHESTRA_TOKEN  the bearer token from the keyring
#
# If either is missing, the hook is a no-op so it can never break a
# Claude Code session.

set -uo pipefail

if [[ -z "${AGENTORCHESTRA_URL:-}" || -z "${AGENTORCHESTRA_TOKEN:-}" ]]; then
    exit 0
fi

# Read stdin (the hook payload Claude Code provides).  cat is fine here
# — payloads are tiny structured JSON, not file contents.
PAYLOAD="$(cat)"

# Best-effort POST.  We never fail the hook on network errors.
curl --silent --max-time 2 \
    --header "authorization: Bearer ${AGENTORCHESTRA_TOKEN}" \
    --header "content-type: application/json" \
    --data "${PAYLOAD}" \
    "${AGENTORCHESTRA_URL}/ingest/hook" \
    > /dev/null 2>&1 || true

exit 0
