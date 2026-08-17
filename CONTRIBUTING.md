# Contributing

## Development setup

Run `scripts/setup.ps1` on Windows or `scripts/setup.sh` on Linux/macOS. The
default generated `.env` uses mock providers and does not require API keys or a
GPU.

Before submitting a change, run:

```text
python -m pytest tests/backend -q
node --test frontend/tests/ui-contract.test.mjs
pnpm --dir frontend run build
```

Do not commit `.env`, API keys, uploaded papers, generated reports, experiment
artifacts, model checkpoints, or dataset caches.
