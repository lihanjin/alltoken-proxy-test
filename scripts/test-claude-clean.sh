#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${NEW_API_TOKEN:-}" ]]; then
  echo "NEW_API_TOKEN is required" >&2
  echo "usage: NEW_API_TOKEN=sk-... $0" >&2
  exit 1
fi

cd "${ROOT_DIR}"

pkill -f '^claude$' 2>/dev/null || true
pkill -f 'tapchain serve --stage client-newapi' 2>/dev/null || true
pkill -f 'tapchain serve --stage newapi-cliproxy' 2>/dev/null || true

rm -rf "${ROOT_DIR}/logs/raw"/* "${ROOT_DIR}/logs/exports"/* "${ROOT_DIR}/logs/grouped"/* 2>/dev/null || true
: > "${ROOT_DIR}/logs/events.jsonl"

"${ROOT_DIR}/scripts/start-claude-cliproxy-stack.sh"
exec "${ROOT_DIR}/scripts/run-clean-claude.sh"
