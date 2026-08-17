from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a Qwen JSONL diagnostic trace without exposing prompts."
    )
    parser.add_argument("trace", type=Path)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    events = [
        json.loads(line)
        for line in args.trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.run_id:
        events = [event for event in events if event.get("run_id") == args.run_id]

    finished = [event for event in events if event.get("event") == "attempt_finished"]
    print(f"events={len(events)} finished_attempts={len(finished)}")
    print("outcomes=" + json.dumps(Counter(
        event.get("outcome", "unknown") for event in finished
    ), ensure_ascii=False))
    for event in finished:
        print(json.dumps({
            key: event.get(key)
            for key in (
                "timestamp",
                "task",
                "route",
                "model",
                "attempt",
                "outcome",
                "duration_seconds",
                "http_status",
                "request_characters",
                "instructions_characters",
                "evidence_count",
                "candidate_count",
                "registry_count",
                "request_sha256",
                "request_id",
                "response_excerpt",
            )
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
