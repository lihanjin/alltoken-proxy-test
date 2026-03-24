#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEW_API_DIR="/Users/leo/Library/CloudStorage/SynologyDrive-leo/Documents/code/new-api"
CLIPROXY_DIR="/Users/leo/code/CLIProxyAPIPlus"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
PROFILE="claude-code/cliproxy/local"
CLIENT_STAGE_LOG="/tmp/tapchain-client-newapi.log"
CPA_STAGE_LOG="/tmp/tapchain-newapi-cliproxy.log"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd lsof

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "missing virtualenv python: ${PYTHON_BIN}" >&2
  echo "run: cd ${ROOT_DIR} && python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

echo "starting new-api..."
docker compose -f "${NEW_API_DIR}/docker-compose.dev.yml" up -d backend web >/dev/null

echo "starting cliproxy..."
docker compose -f "${CLIPROXY_DIR}/docker-compose.yml" up -d >/dev/null

echo "restarting tapchain stages for ${PROFILE}..."
pkill -f "tapchain run --config .*${PROFILE}" 2>/dev/null || true
pkill -f "tapchain serve --stage client-newapi" 2>/dev/null || true
pkill -f "tapchain serve --stage newapi-cliproxy" 2>/dev/null || true
sleep 1

zsh -lc "'${PYTHON_BIN}' -m tapchain serve \
  --stage client-newapi \
  --listen 127.0.0.1:4010 \
  --upstream http://127.0.0.1:3000 \
  --log-dir '${ROOT_DIR}/logs' \
  > '${CLIENT_STAGE_LOG}' 2>&1 < /dev/null &!"

zsh -lc "'${PYTHON_BIN}' -m tapchain serve \
  --stage newapi-cliproxy \
  --listen 0.0.0.0:8317 \
  --upstream http://127.0.0.1:9317 \
  --log-dir '${ROOT_DIR}/logs' \
  > '${CPA_STAGE_LOG}' 2>&1 < /dev/null &!"

sleep 2

echo
echo "status:"
curl -fsS http://127.0.0.1:3000/api/status >/dev/null && echo "  new-api:   ok (http://127.0.0.1:3000)"
curl -fsS -o /dev/null http://127.0.0.1:9317/management.html && echo "  cliproxy:  ok (http://127.0.0.1:9317)"
lsof -nP -iTCP:8317 -sTCP:LISTEN >/dev/null && echo "  tapchain:  ok (http://127.0.0.1:8317)"
lsof -nP -iTCP:4010 -sTCP:LISTEN >/dev/null && echo "  client in: ok (http://127.0.0.1:4010)"

echo
echo "new api channel upstream should be: http://host.docker.internal:8317"
echo "claude-code entry should be:        http://127.0.0.1:4010"
echo "tapchain logs:"
echo "  ${CLIENT_STAGE_LOG}"
echo "  ${CPA_STAGE_LOG}"
