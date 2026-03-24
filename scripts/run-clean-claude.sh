#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEAN_HOME="${ROOT_DIR}/.tmp/claude-clean-home"

if [[ -z "${NEW_API_TOKEN:-}" ]]; then
  echo "NEW_API_TOKEN is required" >&2
  exit 1
fi

mkdir -p "${CLEAN_HOME}"
mkdir -p "${CLEAN_HOME}/.claude"

pkill -f '^claude$' 2>/dev/null || true

if [[ $# -gt 0 ]]; then
  cmd=("$@")
else
  cmd=(claude)
fi

exec env -i \
  HOME="${CLEAN_HOME}" \
  PATH="${PATH}" \
  TERM="${TERM:-xterm-256color}" \
  LANG="${LANG:-en_US.UTF-8}" \
  LC_ALL="${LC_ALL:-en_US.UTF-8}" \
  ANTHROPIC_BASE_URL="http://127.0.0.1:4010" \
  ANTHROPIC_API_KEY="${NEW_API_TOKEN}" \
  "${cmd[@]}"
