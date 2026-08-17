# Dataset cache

This directory is a local runtime cache and is intentionally excluded from Git.

With `EXPERIMENT_DATASET_SOURCE=online`, the backend downloads supported datasets
into this directory when they are first needed. With `local`, place the datasets
here before starting an experiment. See `docs/runbook.md` for the expected layouts.
