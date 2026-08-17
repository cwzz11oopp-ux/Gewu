from __future__ import annotations

import math

from scripts.run_v2_public_repo_e2e import protocol as core_protocol
from scripts.run_v2_public_repo_hardening import (
    ROBUSTNESS_INPUTS,
    robustness_protocol,
)


def test_robustness_grid_is_finite_bounded_and_predeclared():
    assert ROBUSTNESS_INPUTS == [
        -1.0,
        -1e-12,
        -1e-15,
        -0.0,
        0.0,
        1e-15,
        1e-12,
        1.0,
    ]
    assert all(math.isfinite(value) for value in ROBUSTNESS_INPUTS)
    zeros = [value for value in ROBUSTNESS_INPUTS if value == 0.0]
    assert [math.copysign(1.0, value) for value in zeros] == [-1.0, 1.0]
    assert {abs(value) for value in ROBUSTNESS_INPUTS if value != 0.0} == {
        1e-15,
        1e-12,
        1.0,
    }


def test_robustness_protocol_preserves_controls_and_excludes_unmeasured_domains():
    upstream = "7bc720e951fe422b8f8814aa5aa1b64121d26b4c"
    core = core_protocol(upstream)
    robust = robustness_protocol(core, upstream)

    assert robust.fingerprint() != core.fingerprint()
    assert robust.metrics == core.metrics
    assert robust.preprocessing == core.preprocessing
    assert robust.seed_policy == core.seed_policy
    assert robust.training_controls == core.training_controls
    assert robust.evaluation_protocol["inputs"] == ROBUSTNESS_INPUTS
    assert robust.evaluation_protocol["explicit_exclusions"] == [
        "NaN",
        "infinity",
        "subnormal values",
        "general floating-point behavior",
    ]
