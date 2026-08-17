from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


CORE_INPUTS = [-1000.0, -10.0, -1.0, -0.0, 0.0, 1.0, 10.0, 1000.0]
ROBUSTNESS_INPUTS = [-1.0, -1e-12, -1e-15, -0.0, 0.0, 1e-15, 1e-12, 1.0]


def evaluate(inputs: list[float]) -> tuple[float, list[dict]]:
    sys.path.insert(0, str(Path.cwd()))
    from micrograd.engine import Value

    diagnostics: list[dict] = []
    passed = 0
    for raw in inputs:
        try:
            value = Value(raw)
            output = value.relu()
            output.backward()
            expected = 0.0 if raw <= 0.0 else raw
            expected_gradient = 0.0 if raw <= 0.0 else 1.0
            value_error = abs(output.data - expected)
            gradient_error = abs(value.grad - expected_gradient)
            finite = math.isfinite(output.data) and math.isfinite(value.grad)
            canonical_zero = output.data != 0.0 or math.copysign(1.0, output.data) == 1.0
            accepted = (
                finite
                and canonical_zero
                and value_error <= 1e-12
                and gradient_error <= 1e-12
            )
            error = ""
        except Exception as exc:
            accepted = False
            value_error = None
            gradient_error = None
            error = f"{type(exc).__name__}: {exc}"
        passed += int(accepted)
        diagnostics.append(
            {
                "input": raw,
                "accepted": accepted,
                "value_error": value_error,
                "gradient_error": gradient_error,
                "canonical_positive_zero": canonical_zero if not error else False,
                "error": error,
            }
        )
    return passed / len(inputs), diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--protocol-fingerprint", required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--profile", choices=("core", "robustness"), default="core")
    args = parser.parse_args()
    inputs = CORE_INPUTS if args.profile == "core" else ROBUSTNESS_INPUTS
    score, diagnostics = evaluate(inputs)
    Path(args.output).write_text(
        json.dumps(
            {
                "protocol_fingerprint": args.protocol_fingerprint,
                "metrics": {"relu_conformance_score": score},
                "seeds": args.seeds,
                "evaluation_profile": args.profile,
                "inputs": inputs,
                "diagnostics": diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
