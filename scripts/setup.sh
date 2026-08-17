#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -r requirements.txt

if ! command -v pnpm >/dev/null 2>&1; then
  if ! command -v corepack >/dev/null 2>&1; then
    echo "pnpm or Corepack is required. Install Node.js 20.19+ or 22.12+ first." >&2
    exit 1
  fi
  corepack enable
  corepack prepare pnpm@11.10.0 --activate
fi

pnpm --dir frontend install --frozen-lockfile

if [[ ! -f .env ]]; then
  cp .env.demo.example .env
  echo "Created .env from the no-key demo configuration."
fi

echo "Setup complete. Run ./scripts/start.sh"
