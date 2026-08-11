from __future__ import annotations

from copy import deepcopy

import pytest
from django.core.exceptions import ValidationError
from work.consensus_aggregation import aggregate_canonical_components
from work.scope_aggregation import default_consensus_policy, validate_consensus_policy


def point(u: float, v: float) -> dict[str, float]:
    return {"u": u, "v": v}


def pair(index: int, u: float, *, id_suffix: str) -> dict[str, object]:
    return {
        "bottom": {"point_id": f"bottom-{id_suffix}-{index}", **point(u, 0.8)},
        "order_index": index,
        "pair_id": f"pair-{id_suffix}-{index}",
        "top": {"point_id": f"top-{id_suffix}-{index}", **point(u, 0.2)},
    }


def portal(
    u: float,
    *,
    id_suffix: str,
    evidence_status: str = "direct_visible",
    kind: str = "door",
) -> dict[str, object]:
    return {
        "evidence_status": evidence_status,
        "geometry": {
            "bottom_left": point(u, 0.75),
            "bottom_right": point((u + 0.05) % 1, 0.75),
            "top_left": point(u, 0.25),
            "top_right": point((u + 0.05) % 1, 0.25),
        },
        "host_edge_ref": None,
        "kind": kind,
        "portal_id": f"portal-{id_suffix}",
    }


def item(
    revision_id: str,
    pair_us: list[float],
    *,
    portals: list[dict[str, object]] | None = None,
    submitted_at: str | None = None,
) -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "state": {
            "geometry_attempt_status": "partial",
            "pairs": [pair(index, u, id_suffix=revision_id) for index, u in enumerate(pair_us)],
            "portals": portals or [],
        },
        "submitted_at": submitted_at or f"2026-01-01T00:00:0{revision_id[-1]}+00:00",
    }


def test_task_10_7_policy_v2_freezes_similarity_but_keeps_k_max_configurable() -> None:
    policy = validate_consensus_policy(default_consensus_policy())

    assert policy == {
        "addition_step": 1,
        "geometry_max_axis_delta": 0.02,
        "k_initial": 3,
        "k_max": 5,
        "minimum_cluster_margin": 2,
        "minimum_primary_support": 3,
        "portal_max_axis_delta": 0.02,
        "similarity_version": "canonical-2d-consensus-v1",
        "version": "consensus-policy-v2",
    }
    changed = validate_consensus_policy({**policy, "k_max": 7})
    assert changed["k_max"] == 7
    assert (
        validate_consensus_policy(
            {
                "addition_step": 1,
                "k_initial": 3,
                "k_max": 5,
                "version": "consensus-policy-v1",
            }
        )["version"]
        == "consensus-policy-v1"
    )

    with pytest.raises(ValidationError) as unavailable:
        aggregate_canonical_components(
            inputs=[item("revision-1", [0.1])],
            policy=validate_consensus_policy(
                {
                    "addition_step": 1,
                    "k_initial": 3,
                    "k_max": 5,
                    "version": "consensus-policy-v1",
                }
            ),
        )
    assert unavailable.value.code == "consensus_similarity_unavailable"


def test_pap_acr_sc_008_geometry_matches_across_seam_rotation_and_direction() -> None:
    inputs = [
        item("revision-1", [0.99, 0.25, 0.55]),
        item("revision-2", [0.251, 0.551, 0.001]),
        item("revision-3", [0.549, 0.249, 0.989]),
    ]

    result = aggregate_canonical_components(
        inputs=inputs,
        policy=validate_consensus_policy(default_consensus_policy()),
    )["geometry"]

    assert result == {
        "cluster_revision_ids": ["revision-1", "revision-2", "revision-3"],
        "margin": 3,
        "medoid_revision_id": "revision-1",
        "primary_support": 3,
        "secondary_support": 0,
        "state": "resolved",
    }
    assert inputs[0]["state"]["pairs"][0]["top"]["u"] == 0.99


def test_pap_acr_sc_033_five_inputs_with_three_two_modes_are_unresolved() -> None:
    # PAP-ACR-SC-007 is the same k_max multimodal boundary at scenario level.
    inputs = [
        item("revision-1", [0.1, 0.4, 0.7]),
        item("revision-2", [0.105, 0.405, 0.705]),
        item("revision-3", [0.11, 0.41, 0.71]),
        item("revision-4", [0.2, 0.5, 0.8]),
        item("revision-5", [0.205, 0.505, 0.805]),
    ]

    result = aggregate_canonical_components(
        inputs=inputs,
        policy=validate_consensus_policy(default_consensus_policy()),
    )["geometry"]

    assert result["state"] == "unresolved"
    assert result["primary_support"] == 3
    assert result["secondary_support"] == 2
    assert result["margin"] == 1
    assert result["medoid_revision_id"] is None


def test_pap_acr_sc_034_portal_uses_an_independent_real_medoid() -> None:
    inputs = [
        item(
            "revision-1",
            [0.1, 0.4, 0.7],
            portals=[portal(0.21, id_suffix="1")],
            submitted_at="2026-01-01T00:00:01+00:00",
        ),
        item(
            "revision-2",
            [0.11, 0.41, 0.71],
            portals=[portal(0.2, id_suffix="2", evidence_status="inferred")],
            submitted_at="2026-01-01T00:00:02+00:00",
        ),
        item(
            "revision-3",
            [0.12, 0.42, 0.72],
            portals=[portal(0.214, id_suffix="3", evidence_status="ambiguous")],
            submitted_at="2026-01-01T00:00:03+00:00",
        ),
    ]
    original = deepcopy(inputs)

    results = aggregate_canonical_components(
        inputs=inputs,
        policy=validate_consensus_policy(default_consensus_policy()),
    )

    assert results["geometry"]["medoid_revision_id"] == "revision-2"
    assert results["portal"]["medoid_revision_id"] == "revision-1"
    assert results["portal"]["state"] == "resolved"
    assert inputs == original


def test_task_10_7_complete_link_rejects_similarity_chains() -> None:
    inputs = [
        item("revision-1", [0.1]),
        item("revision-2", [0.119]),
        item("revision-3", [0.138]),
    ]

    result = aggregate_canonical_components(
        inputs=inputs,
        policy=validate_consensus_policy(default_consensus_policy()),
    )["geometry"]

    assert result["state"] == "needs_more"
    assert result["primary_support"] == 2
    assert result["secondary_support"] == 2
    assert result["margin"] == 0


def test_task_10_10_shared_bias_is_consensus_not_ground_truth() -> None:
    inputs = [
        item("revision-1", [0.7, 0.8, 0.9]),
        item("revision-2", [0.705, 0.805, 0.905]),
        item("revision-3", [0.71, 0.81, 0.91]),
    ]

    result = aggregate_canonical_components(
        inputs=inputs,
        policy=validate_consensus_policy(default_consensus_policy()),
    )["geometry"]

    assert result["state"] == "resolved"
    assert result["medoid_revision_id"] == "revision-2"
    assert set(result) == {
        "cluster_revision_ids",
        "margin",
        "medoid_revision_id",
        "primary_support",
        "secondary_support",
        "state",
    }
    assert all(item["state"]["geometry_attempt_status"] == "partial" for item in inputs)
