import pytest
from django.core.exceptions import ValidationError
from work.scope_aggregation import (
    consensus_addition_count,
    default_consensus_policy,
    validate_consensus_policy,
)


def test_task_10_9_default_consensus_policy_adds_one_and_stops_at_five() -> None:
    policy = validate_consensus_policy(default_consensus_policy())

    assert consensus_addition_count(policy=policy, exposure_count=2, state="needs_more") == 0
    assert consensus_addition_count(policy=policy, exposure_count=3, state="needs_more") == 1
    assert consensus_addition_count(policy=policy, exposure_count=4, state="needs_more") == 1
    assert consensus_addition_count(policy=policy, exposure_count=5, state="needs_more") == 0
    assert consensus_addition_count(policy=policy, exposure_count=3, state="resolved_oos") == 0
    assert consensus_addition_count(policy=policy, exposure_count=5, state="unresolved") == 0


@pytest.mark.parametrize(
    "change",
    [
        {"addition_step": 0},
        {"k_initial": 0},
        {"k_initial": 6},
        {"k_max": 2},
        {"version": "future-policy"},
    ],
)
def test_task_10_9_rejects_invalid_consensus_policy(
    change: dict[str, object],
) -> None:
    payload = {
        "addition_step": 1,
        "k_initial": 3,
        "k_max": 5,
        "version": "consensus-policy-v1",
        **change,
    }
    with pytest.raises(ValidationError) as rejected:
        validate_consensus_policy(payload)
    assert rejected.value.code == "consensus_policy_invalid"
