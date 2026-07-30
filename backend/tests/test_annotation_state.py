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
)

PAIR_A = "00000000-0000-4000-8000-000000000001"
PAIR_B = "00000000-0000-4000-8000-000000000002"
TOP_A = "00000000-0000-4000-8000-000000000003"
BOTTOM_A = "00000000-0000-4000-8000-000000000004"
TOP_B = "00000000-0000-4000-8000-000000000005"
BOTTOM_B = "00000000-0000-4000-8000-000000000006"
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
        "seam_anchor_pair_id": PAIR_A,
    }


def test_pap_ann_sc_003_top_and_bottom_coordinates_round_trip_without_averaging() -> None:
    random = Random(20260731)

    for index in range(1, 65):
        top_u = random.random()
        bottom_u = (top_u + 0.0001) % 1
        pair_id = str(UUID(int=index))
        top_id = str(UUID(int=1000 + index * 2))
        bottom_id = str(UUID(int=1001 + index * 2))
        state = {
            "pairs": [
                point_pair(
                    pair_id=pair_id,
                    order_index=0,
                    top_id=top_id,
                    bottom_id=bottom_id,
                    top_u=top_u,
                    bottom_u=bottom_u,
                )
            ],
            "seam_anchor_pair_id": pair_id,
        }

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
    state = {
        "pairs": [
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
        ],
        "seam_anchor_pair_id": PAIR_A,
    }

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
        "seam_anchor_pair_id": PAIR_A,
        "pairs": list(reversed(deepcopy(state["pairs"]))),
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
