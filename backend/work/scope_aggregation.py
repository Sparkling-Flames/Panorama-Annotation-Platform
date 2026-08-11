from __future__ import annotations

from typing import TypedDict

from django.core.exceptions import ValidationError

from .meta_schema import SCOPE_REASON_CODES

SCOPE_POLICY_V1 = "scope-policy-v1"
CONSENSUS_POLICY_V1 = "consensus-policy-v1"
CONSENSUS_POLICY_V2 = "consensus-policy-v2"
CONSENSUS_SIMILARITY_V1 = "canonical-2d-consensus-v1"


class ScopePolicy(TypedDict):
    auto_close_reason_codes: list[str]
    maximum_support: int
    minimum_support: int
    version: str


class ScopeAggregationResult(TypedDict):
    reason_code: str | None
    state: str
    support: int


class ConsensusPolicy(TypedDict, total=False):
    addition_step: int
    geometry_max_axis_delta: float
    k_initial: int
    k_max: int
    minimum_cluster_margin: int
    minimum_primary_support: int
    portal_max_axis_delta: float
    similarity_version: str
    version: str


def default_consensus_policy() -> ConsensusPolicy:
    return {
        "addition_step": 1,
        "geometry_max_axis_delta": 0.02,
        "k_initial": 3,
        "k_max": 5,
        "minimum_cluster_margin": 2,
        "minimum_primary_support": 3,
        "portal_max_axis_delta": 0.02,
        "similarity_version": CONSENSUS_SIMILARITY_V1,
        "version": CONSENSUS_POLICY_V2,
    }


def validate_consensus_policy(payload: object) -> ConsensusPolicy:
    if not isinstance(payload, dict):
        raise ValidationError("Invalid consensus policy.", code="consensus_policy_invalid")
    version = payload.get("version")
    expected_fields = {"addition_step", "k_initial", "k_max", "version"}
    if version == CONSENSUS_POLICY_V2:
        expected_fields |= {
            "geometry_max_axis_delta",
            "minimum_cluster_margin",
            "minimum_primary_support",
            "portal_max_axis_delta",
            "similarity_version",
        }
    if set(payload) != expected_fields or version not in {
        CONSENSUS_POLICY_V1,
        CONSENSUS_POLICY_V2,
    }:
        raise ValidationError("Invalid consensus policy.", code="consensus_policy_invalid")
    step = payload["addition_step"]
    initial = payload["k_initial"]
    maximum = payload["k_max"]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (step, initial, maximum)
        )
        or step < 1
        or initial < 1
        or maximum < initial
    ):
        raise ValidationError("Invalid consensus policy.", code="consensus_policy_invalid")
    common: ConsensusPolicy = {
        "addition_step": step,
        "k_initial": initial,
        "k_max": maximum,
        "version": version,
    }
    if version == CONSENSUS_POLICY_V1:
        return common
    primary_support = payload["minimum_primary_support"]
    margin = payload["minimum_cluster_margin"]
    geometry_delta = payload["geometry_max_axis_delta"]
    portal_delta = payload["portal_max_axis_delta"]
    if (
        payload["similarity_version"] != CONSENSUS_SIMILARITY_V1
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (primary_support, margin)
        )
        or primary_support < 1
        or primary_support > maximum
        or margin < 1
        or margin > maximum
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
            or value > 1
            for value in (geometry_delta, portal_delta)
        )
    ):
        raise ValidationError("Invalid consensus policy.", code="consensus_policy_invalid")
    return {
        **common,
        "geometry_max_axis_delta": float(geometry_delta),
        "minimum_cluster_margin": margin,
        "minimum_primary_support": primary_support,
        "portal_max_axis_delta": float(portal_delta),
        "similarity_version": CONSENSUS_SIMILARITY_V1,
    }


def consensus_addition_count(*, policy: ConsensusPolicy, exposure_count: int, state: str) -> int:
    if (
        state != "needs_more"
        or exposure_count < policy["k_initial"]
        or exposure_count >= policy["k_max"]
    ):
        return 0
    return min(policy["addition_step"], policy["k_max"] - exposure_count)


def default_scope_policy() -> ScopePolicy:
    return {
        "auto_close_reason_codes": [],
        "maximum_support": 5,
        "minimum_support": 3,
        "version": SCOPE_POLICY_V1,
    }


def validate_scope_policy(payload: object) -> ScopePolicy:
    if not isinstance(payload, dict) or set(payload) != {
        "auto_close_reason_codes",
        "maximum_support",
        "minimum_support",
        "version",
    }:
        raise ValidationError("Invalid scope policy.", code="scope_policy_invalid")
    allowed = payload["auto_close_reason_codes"]
    minimum = payload["minimum_support"]
    maximum = payload["maximum_support"]
    if (
        payload["version"] != SCOPE_POLICY_V1
        or not isinstance(allowed, list)
        or any(not isinstance(code, str) or code not in SCOPE_REASON_CODES for code in allowed)
        or len(allowed) != len(set(allowed))
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or minimum < 1
        or maximum < minimum
    ):
        raise ValidationError("Invalid scope policy.", code="scope_policy_invalid")
    return {
        "auto_close_reason_codes": list(allowed),
        "maximum_support": maximum,
        "minimum_support": minimum,
        "version": SCOPE_POLICY_V1,
    }


def aggregate_scope_evidence(
    *, policy: ScopePolicy, inputs: list[dict[str, object]]
) -> ScopeAggregationResult:
    workers: set[str] = set()
    reasons: list[str] = []
    has_conflict = False
    for item in inputs:
        worker_id = item.get("worker_id")
        components = item.get("eligible_components")
        scope = item.get("scope_evidence")
        if (
            not isinstance(worker_id, str)
            or worker_id in workers
            or not isinstance(components, list)
            or any(not isinstance(component, str) for component in components)
            or not isinstance(scope, dict)
        ):
            raise ValidationError("Invalid scope input.", code="scope_input_invalid")
        workers.add(worker_id)
        observation = scope.get("observation")
        reason_codes = scope.get("reason_codes")
        if (
            observation != "representation_oos"
            or not isinstance(reason_codes, list)
            or len(reason_codes) != 1
            or not isinstance(reason_codes[0], str)
        ):
            has_conflict = True
        else:
            reasons.append(reason_codes[0])
        has_conflict = has_conflict or "geometry" in components or "portal" in components

    reason = reasons[0] if reasons and len(set(reasons)) == 1 else None
    support = len(reasons) if reason is not None else 0
    resolved = (
        not has_conflict
        and len(reasons) == len(inputs)
        and reason in policy["auto_close_reason_codes"]
        and support >= policy["minimum_support"]
    )
    if resolved:
        state = "resolved_oos"
    elif len(inputs) >= policy["maximum_support"]:
        state = "unresolved"
    else:
        state = "needs_more"
    return {"reason_code": reason, "state": state, "support": support}
