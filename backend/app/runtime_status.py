from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def read_json_retry(path: Path, *, attempts: int = 5, delay_seconds: float = 0.02) -> dict[str, Any]:
    """Read a small JSON status file while tolerating a concurrent atomic replace."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("runtime status must be a JSON object")
            return value
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds * (attempt + 1))
    assert last_error is not None
    raise last_error


def write_json_atomic(
    path: Path,
    payload: dict[str, Any],
    *,
    attempts: int = 8,
    delay_seconds: float = 0.025,
) -> None:
    """Atomically replace JSON with retry support for transient Windows sharing violations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())

        last_error: OSError | None = None
        for attempt in range(attempts):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(delay_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error
    finally:
        temporary.unlink(missing_ok=True)
