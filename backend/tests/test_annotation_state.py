from __future__ import annotations

import json
from copy import deepcopy
from math import nextafter
from pathlib import Path
from random import Random
from uuid import UUID

import pytest
from work.annotation_state import (
    AnnotationStateError,
    annotation_state_sha,
    canonical_annotation_json,
    canonicalize_annotation_state,
    normalized_to_pixels,
    validate_annotation_submission,
)

PAIR_A = "00000000-0000-4000-8000-000000000001"
PAIR_B = "00000000-0000-4000-8000-000000000002"
TOP_A = "00000000-0000-4000-8000-000000000003"
BOTTOM_A = "00000000-0000-4000-8000-000000000004"
TOP_B = "00000000-0000-4000-8000-000000000005"
BOTTOM_B = "00000000-0000-4000-8000-000000000006"
PORTAL_A = "00000000-0000-4000-8000-000000000007"
GOLDEN_VECTOR = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "annotation_state_golden.json").read_text(
        encoding="utf-8"
    )
)


def point_pair(
    *,
    pair_id: str,
    order_index: int,
    top_id: str,
    bottom_id: str,
    top_u: float,
    top_v: float = 0.2,
    bottom_u: float,
    bottom_v: float = 0.8,
) -> dict[str, object]:
    return {
        "pair_id": pair_id,
        "order_index": order_index,
        "top": {"point_id": top_id, "u": top_u, "v": top_v},
        "bottom": {"point_id": bottom_id, "u": bottom_u, "v": bottom_v},
    }


def seam_state() -> dict[str, object]:
    return {
        "geometry_attempt_reason_text": "",
        "geometry_attempt_status": "best_effort_complete",
        "pairs": [
            point_pair(
                pair_id=PAIR_A,
                order_index=0,
                top_id=TOP_A,
                bottom_id=BOTTOM_A,
                top_u=0.98,
                bottom_u=0.97,
            ),
            point_pair(
                pair_id=PAIR_B,
                order_index=1,
                top_id=TOP_B,
                bottom_id=BOTTOM_B,
                top_u=0.02,
                bottom_u=0.03,
            ),
        ],
        "portals": [],
        "seam_anchor_pair_id": PAIR_A,
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": "annotatable",
    }


def test_pap_ann_sc_003_top_and_bottom_coordinates_round_trip_without_averaging() -> None:
    random = Random(20260731)

    for index in range(1, 65):
        top_u = random.random()
        bottom_u = (top_u + 0.0001) % 1
        pair_id = str(UUID(int=index))
        top_id = str(UUID(int=1000 + index * 2))
        bottom_id = str(UUID(int=1001 + index * 2))
        state = seam_state()
        state["pairs"] = [
            point_pair(
                pair_id=pair_id,
                order_index=0,
                top_id=top_id,
                bottom_id=bottom_id,
                top_u=top_u,
                bottom_u=bottom_u,
            )
        ]
        state["seam_anchor_pair_id"] = pair_id

        canonical = canonicalize_annotation_state(state)
        pair = canonical["pairs"][0]

        assert pair["pair_id"] == pair_id
        assert pair["top"] == {"point_id": top_id, "u": top_u, "v": 0.2}
        assert pair["bottom"] == {"point_id": bottom_id, "u": bottom_u, "v": 0.8}
        assert pair["top"]["u"] != pair["bottom"]["u"]


def test_pap_ann_sc_005_resolution_switch_changes_only_derived_pixels() -> None:
    state = seam_state()
    state_sha = annotation_state_sha(state)

    low_resolution = normalized_to_pixels(u=0.98, v=0.2, width=2048, height=1024)
    high_resolution = normalized_to_pixels(u=0.98, v=0.2, width=4096, height=2048)

    assert high_resolution == (low_resolution[0] * 2, low_resolution[1] * 2)
    assert annotation_state_sha(state) == state_sha


def test_normalized_coordinate_boundaries_are_preserved() -> None:
    state = seam_state()
    state["pairs"] = [
        point_pair(
            pair_id=PAIR_A,
            order_index=0,
            top_id=TOP_A,
            bottom_id=BOTTOM_A,
            top_u=0.0,
            top_v=0.0,
            bottom_u=nextafter(1.0, 0.0),
            bottom_v=1.0,
        )
    ]

    pair = canonicalize_annotation_state(state)["pairs"][0]

    assert pair["top"]["u"] == pair["top"]["v"] == 0.0
    assert pair["bottom"]["u"] == nextafter(1.0, 0.0)
    assert pair["bottom"]["v"] == 1.0


def test_pap_ann_sc_006_seam_order_uses_order_index_instead_of_numeric_u_sort() -> None:
    state = seam_state()
    state["pairs"] = list(reversed(state["pairs"]))

    canonical = canonicalize_annotation_state(state)

    assert [pair["pair_id"] for pair in canonical["pairs"]] == [PAIR_A, PAIR_B]
    assert [pair["top"]["u"] for pair in canonical["pairs"]] == [0.98, 0.02]
    assert canonical["seam_anchor_pair_id"] == PAIR_A


def test_annotation_state_hash_is_deterministic_and_changes_with_canonical_order() -> None:
    state = seam_state()
    equivalent = {
        "geometry_attempt_reason_text": state["geometry_attempt_reason_text"],
        "geometry_attempt_status": state["geometry_attempt_status"],
        "seam_anchor_pair_id": PAIR_A,
        "pairs": list(reversed(deepcopy(state["pairs"]))),
        "portals": [],
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": "annotatable",
    }
    reordered = deepcopy(state)
    reordered_pairs = reordered["pairs"]
    reordered_pairs[0]["order_index"] = 1
    reordered_pairs[1]["order_index"] = 0
    reordered["seam_anchor_pair_id"] = PAIR_B

    assert canonical_annotation_json(equivalent) == canonical_annotation_json(state)
    assert annotation_state_sha(equivalent) == annotation_state_sha(state)
    assert annotation_state_sha(reordered) != annotation_state_sha(state)
    assert len(annotation_state_sha(state)) == 64


def test_canonical_json_uses_ecmascript_number_format() -> None:
    state = GOLDEN_VECTOR["state"]

    assert canonical_annotation_json(state).decode("utf-8") == GOLDEN_VECTOR["canonical_json"]
    assert annotation_state_sha(state) == GOLDEN_VECTOR["state_sha"]


def assert_state_error(
    state: dict[str, object],
    *,
    code: str,
    field: str,
    pair_id: str | None,
    pair_index: int | None = None,
) -> None:
    with pytest.raises(AnnotationStateError) as rejected:
        canonicalize_annotation_state(state)

    assert rejected.value.code == code
    assert rejected.value.field == field
    assert rejected.value.pair_id == pair_id
    assert rejected.value.pair_index == pair_index


def test_duplicate_pair_and_point_ids_are_located() -> None:
    duplicate_pair = seam_state()
    duplicate_pair["pairs"][1]["pair_id"] = PAIR_A
    assert_state_error(
        duplicate_pair,
        code="annotation_pair_id_duplicate",
        field="pair_id",
        pair_id=PAIR_A,
        pair_index=1,
    )

    duplicate_point = seam_state()
    duplicate_point["pairs"][1]["top"]["point_id"] = TOP_A
    assert_state_error(
        duplicate_point,
        code="annotation_point_id_duplicate",
        field="top.point_id",
        pair_id=PAIR_B,
        pair_index=1,
    )


@pytest.mark.parametrize(
    ("point", "axis", "value", "code", "pair_id"),
    [
        ("top", "u", float("nan"), "annotation_coordinate_non_finite", PAIR_A),
        ("bottom", "v", float("inf"), "annotation_coordinate_non_finite", PAIR_A),
        ("top", "u", 1.0, "annotation_coordinate_out_of_range", PAIR_A),
        ("bottom", "v", -0.001, "annotation_coordinate_out_of_range", PAIR_A),
    ],
)
def test_invalid_coordinates_are_located(
    point: str,
    axis: str,
    value: float,
    code: str,
    pair_id: str,
) -> None:
    state = seam_state()
    state["pairs"][0][point][axis] = value

    assert_state_error(
        state,
        code=code,
        field=f"{point}.{axis}",
        pair_id=pair_id,
        pair_index=0,
    )


def test_discontinuous_order_and_bad_seam_reference_are_located() -> None:
    discontinuous = seam_state()
    discontinuous["pairs"][1]["order_index"] = 3
    assert_state_error(
        discontinuous,
        code="annotation_order_index_invalid",
        field="order_index",
        pair_id=PAIR_B,
        pair_index=1,
    )

    bad_seam = seam_state()
    bad_seam["seam_anchor_pair_id"] = "00000000-0000-4000-8000-000000000099"
    assert_state_error(
        bad_seam,
        code="annotation_seam_anchor_invalid",
        field="seam_anchor_pair_id",
        pair_id=None,
    )


def test_illegal_pair_field_is_rejected_without_becoming_canonical_state() -> None:
    state = seam_state()
    state["pairs"][0]["wall"] = {"derived": True}

    assert_state_error(
        state,
        code="annotation_field_unknown",
        field="wall",
        pair_id=PAIR_A,
        pair_index=0,
    )


def test_pap_ann_sc_017_pap_ann_sc_022_poc_contract_is_canonical_without_derived_closure() -> None:
    state = seam_state()
    state["geometry_attempt_status"] = "partial"
    state["scope_reason_codes"] = ["portal_ambiguous", "insufficient_evidence"]
    state["worker_scope_observation"] = "needs_scope_review"
    state["portals"] = [
        {
            "evidence_status": "ambiguous",
            "geometry": {
                "bottom_left": {"u": 0.98, "v": 0.8},
                "bottom_right": {"u": 0.02, "v": 0.8},
                "top_left": {"u": 0.98, "v": 0.3},
                "top_right": {"u": 0.02, "v": 0.3},
            },
            "host_edge_ref": PAIR_A,
            "kind": "architectural_opening",
            "portal_id": PORTAL_A,
        }
    ]

    canonical = canonicalize_annotation_state(state)

    assert canonical["scope_reason_codes"] == [
        "portal_ambiguous",
        "insufficient_evidence",
    ]
    assert canonical["portals"][0]["portal_id"] == PORTAL_A
    assert canonical["portals"][0]["host_edge_ref"] == PAIR_A
    assert "closure" not in canonical["portals"][0]
    assert "physical" not in canonical["portals"][0]


def test_pap_prv_sc_011_partial_submission_does_not_require_closed_geometry() -> None:
    state = seam_state()
    state["geometry_attempt_status"] = "partial"

    canonical = validate_annotation_submission(state)

    assert canonical["geometry_attempt_status"] == "partial"
    assert canonical["pairs"] == canonicalize_annotation_state(state)["pairs"]


@pytest.mark.parametrize(
    ("state_update", "field"),
    [
        ({"worker_scope_observation": "unsupported"}, "worker_scope_observation"),
        (
            {
                "worker_scope_observation": "annotatable",
                "scope_reason_codes": ["insufficient_evidence"],
            },
            "scope_reason_codes",
        ),
    ],
)
def test_poc_scope_rejects_invalid_combinations(
    state_update: dict[str, object], field: str
) -> None:
    state = seam_state()
    state.update(state_update)

    assert_state_error(
        state,
        code="annotation_scope_invalid",
        field=field,
        pair_id=None,
    )


def test_not_drawable_draft_does_not_require_fake_geometry() -> None:
    state = seam_state()
    state.update(
        {
            "geometry_attempt_reason_text": "",
            "geometry_attempt_status": "not_drawable",
            "pairs": [],
            "portals": [],
            "seam_anchor_pair_id": None,
            "scope_reason_codes": ["insufficient_evidence"],
            "worker_scope_observation": "representation_oos",
        }
    )

    canonical = canonicalize_annotation_state(state)

    assert canonical["pairs"] == []
    assert canonical["seam_anchor_pair_id"] is None
    with pytest.raises(AnnotationStateError) as rejected:
        validate_annotation_submission(state)
    assert rejected.value.field == "geometry_attempt_reason_text"
    state["geometry_attempt_reason_text"] = "Image evidence is insufficient."
    assert validate_annotation_submission(state)["pairs"] == []


@pytest.mark.parametrize(
    "state_update",
    [
        {"worker_scope_observation": "needs_scope_review", "scope_reason_codes": []},
        {
            "worker_scope_observation": "representation_oos",
            "scope_reason_codes": ["other"],
            "scope_reason_text": "   ",
        },
    ],
)
def test_incomplete_scope_can_autosave_but_cannot_create_a_revision(
    state_update: dict[str, object],
) -> None:
    state = seam_state()
    state.update(state_update)

    assert canonicalize_annotation_state(state)
    with pytest.raises(AnnotationStateError) as rejected:
        validate_annotation_submission(state)
    assert rejected.value.code == "annotation_submission_incomplete"


def test_new_draft_keeps_unanswered_poc_fields_explicit_without_fake_defaults() -> None:
    state = seam_state()
    state.update(
        {
            "geometry_attempt_status": None,
            "pairs": [],
            "portals": [],
            "seam_anchor_pair_id": None,
            "scope_reason_codes": [],
            "scope_reason_text": "",
            "worker_scope_observation": None,
        }
    )

    assert canonicalize_annotation_state(state) == state


@pytest.mark.parametrize(
    ("portal_update", "code", "field"),
    [
        ({"kind": "furniture_gap"}, "annotation_portal_invalid", "portals.kind"),
        (
            {"host_edge_ref": "00000000-0000-4000-8000-000000000099"},
            "annotation_portal_host_edge_invalid",
            "portals.host_edge_ref",
        ),
        ({"physical": False}, "annotation_field_unknown", "physical"),
    ],
)
def test_portal_rejects_invalid_or_derived_fields(
    portal_update: dict[str, object], code: str, field: str
) -> None:
    state = seam_state()
    portal = {
        "evidence_status": "direct_visible",
        "geometry": {
            "bottom_left": {"u": 0.2, "v": 0.8},
            "bottom_right": {"u": 0.3, "v": 0.8},
            "top_left": {"u": 0.2, "v": 0.3},
            "top_right": {"u": 0.3, "v": 0.3},
        },
        "host_edge_ref": PAIR_A,
        "kind": "door",
        "portal_id": PORTAL_A,
        **portal_update,
    }
    state["portals"] = [portal]

    assert_state_error(state, code=code, field=field, pair_id=None)


def test_invalid_pair_id_still_locates_the_input_row() -> None:
    state = seam_state()
    state["pairs"][1]["pair_id"] = "not-a-uuid"

    assert_state_error(
        state,
        code="annotation_id_invalid",
        field="pair_id",
        pair_id=None,
        pair_index=1,
    )


@pytest.mark.parametrize("payload", [None, 42, [{}]])
def test_non_object_state_has_a_stable_error(payload: object) -> None:
    with pytest.raises(AnnotationStateError) as rejected:
        canonicalize_annotation_state(payload)

    assert rejected.value.code == "annotation_field_type_invalid"
    assert rejected.value.field == "state"
    assert rejected.value.pair_id is None
    assert rejected.value.pair_index is None


def test_huge_json_integer_coordinate_has_a_stable_error() -> None:
    state = seam_state()
    state["pairs"][0]["top"]["u"] = 10**10000

    assert_state_error(
        state,
        code="annotation_coordinate_non_finite",
        field="top.u",
        pair_id=PAIR_A,
        pair_index=0,
    )
