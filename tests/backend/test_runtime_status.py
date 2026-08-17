import json
import os

from backend.app.runtime_status import read_json_retry, write_json_atomic


def test_write_json_atomic_retries_transient_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "runtime_status.json"
    path.write_text(json.dumps({"state": "old"}), encoding="utf-8")
    real_replace = os.replace
    calls = []

    def flaky_replace(source, destination):
        calls.append((source, destination))
        if len(calls) < 3:
            raise PermissionError("simulated Windows sharing violation")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr("backend.app.runtime_status.time.sleep", lambda *_: None)

    write_json_atomic(path, {"state": "running", "pid": 42})

    assert len(calls) == 3
    assert read_json_retry(path) == {"state": "running", "pid": 42}
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_json_atomic_uses_distinct_temporary_files(tmp_path, monkeypatch):
    path = tmp_path / "runtime_status.json"
    real_replace = os.replace
    sources = []

    def capture_replace(source, destination):
        sources.append(source)
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", capture_replace)

    write_json_atomic(path, {"sequence": 1})
    write_json_atomic(path, {"sequence": 2})

    assert sources[0] != sources[1]
    assert read_json_retry(path) == {"sequence": 2}
    assert list(tmp_path.glob("*.tmp")) == []
