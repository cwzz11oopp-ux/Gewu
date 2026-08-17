from __future__ import annotations

import argparse
import json
from pathlib import Path

from model import predict


DATA = [(-2.0, 0), (-1.0, 0), (0.2, 1), (1.0, 1), (2.0, 1)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--protocol-fingerprint", required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    args = parser.parse_args()
    correct = sum(predict(value) == target for value, target in DATA)
    payload = {
        "protocol_fingerprint": args.protocol_fingerprint,
        "metrics": {"accuracy": correct / len(DATA)},
        "seeds": args.seeds,
    }
    Path(args.output).write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
