#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -x .venv/bin/python ]]; then
  echo "Python environment is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

mkdir -p tmp
.venv/bin/python -m uvicorn backend.app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  >tmp/backend.stdout.log \
  2>tmp/backend.stderr.log &
backend_pid=$!

cleanup() {
  kill "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Backend started. Logs: tmp/backend.stdout.log and tmp/backend.stderr.log"
echo "Open http://127.0.0.1:5173 after the frontend starts."
pnpm --dir frontend dev
