#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
LOG_DIR="${ROOT_DIR}/logs-upstream"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "missing virtualenv python: ${PYTHON_BIN}" >&2
  echo "run: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"${PYTHON_BIN}" -m tapchain serve \
  --stage upstream-claude \
  --listen 0.0.0.0:8411 \
  --upstream https://api.anthropic.com \
  --log-dir "${LOG_DIR}" &

"${PYTHON_BIN}" -m tapchain serve \
  --stage upstream-gemini \
  --listen 0.0.0.0:8412 \
  --upstream https://generativelanguage.googleapis.com \
  --log-dir "${LOG_DIR}" &

"${PYTHON_BIN}" -m tapchain serve \
  --stage upstream-codex \
  --listen 0.0.0.0:8413 \
  --upstream https://api.openai.com/v1 \
  --log-dir "${LOG_DIR}" &

echo "upstream taps started"
echo "claude: http://host.docker.internal:8411"
echo "gemini: http://host.docker.internal:8412"
echo "codex:  http://host.docker.internal:8413/v1"
echo "logs:   ${LOG_DIR}"

wait
