from itertools import permutations

import pytest
from django.core.exceptions import ValidationError
from work.scope_aggregation import aggregate_scope_evidence, validate_scope_policy


def policy(*, allowed: list[str] | None = None) -> dict[str, object]:
    return {
        "auto_close_reason_codes": allowed or [],
        "maximum_support": 5,
        "minimum_support": 3,
        "version": "scope-policy-v1",
    }


def evidence(
    worker_id: str,
    *,
    observation: str = "representation_oos",
    reason_code: str = "insufficient_evidence",
    structural_components: list[str] | None = None,
) -> dict[str, object]:
    return {
        "eligible_components": ["evidence", "scope", *(structural_components or [])],
        "revision_id": f"revision-{worker_id}",
        "scope_evidence": {
            "observation": observation,
            "reason_codes": [reason_code],
        },
        "worker_id": worker_id,
    }


def test_task_10_5_scope_policy_requires_an_explicit_schema_reason_whitelist() -> None:
    safe_default = validate_scope_policy(policy())
    allowed = validate_scope_policy(policy(allowed=["insufficient_evidence"]))
    inputs = [evidence(str(index)) for index in range(3)]

    assert aggregate_scope_evidence(policy=safe_default, inputs=inputs)["state"] == "needs_more"
    assert aggregate_scope_evidence(policy=allowed, inputs=inputs) == {
        "reason_code": "insufficient_evidence",
        "state": "resolved_oos",
        "support": 3,
    }
    with pytest.raises(ValidationError) as rejected:
        validate_scope_policy(policy(allowed=["not-a-schema-reason"]))
    assert rejected.value.code == "scope_policy_invalid"


@pytest.mark.parametrize("component", ["geometry", "portal"])
def test_pap_acr_sc_023_structure_conflict_needs_more_then_becomes_unresolved(
    component: str,
) -> None:
    frozen_policy = validate_scope_policy(policy(allowed=["insufficient_evidence"]))
    inputs = [evidence(str(index)) for index in range(3)]
    inputs[0] = evidence("0", structural_components=[component])

    assert aggregate_scope_evidence(policy=frozen_policy, inputs=inputs)["state"] == "needs_more"
    while len(inputs) < 5:
        inputs.append(evidence(str(len(inputs))))
    assert aggregate_scope_evidence(policy=frozen_policy, inputs=inputs)["state"] == "unresolved"


def test_task_10_5_scope_aggregation_is_order_independent_and_never_majority_truth() -> None:
    frozen_policy = validate_scope_policy(policy(allowed=["insufficient_evidence"]))
    inputs = [
        evidence("one"),
        evidence("two"),
        evidence("three", observation="needs_scope_review"),
    ]

    results = {
        aggregate_scope_evidence(policy=frozen_policy, inputs=list(order))["state"]
        for order in permutations(inputs)
    }

    assert results == {"needs_more"}
