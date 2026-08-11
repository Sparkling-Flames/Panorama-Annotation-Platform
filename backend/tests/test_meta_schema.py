from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from work.annotation_state import (
    AnnotationStateError,
    annotation_state_sha,
    canonical_annotation_json,
    canonicalize_annotation_state,
    validate_annotation_submission,
)
from work.meta_schema import (
    META_COPY_V1,
    META_SCHEMA_V1,
    POC_META_COPY_VERSION,
    POC_META_SCHEMA_VERSION,
    meta_contract_payload,
)
from work.models import Task

V1_GOLDEN = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "annotation_state_v1_golden.json").read_text(
        encoding="utf-8"
    )
)


def legacy_complete_state() -> dict[str, object]:
    pair_id = "00000000-0000-4000-8000-000000000001"
    return {
        "geometry_attempt_reason_text": "",
        "geometry_attempt_status": "best_effort_complete",
        "pairs": [
            {
                "bottom": {
                    "point_id": "00000000-0000-4000-8000-000000000003",
                    "u": 0.12,
                    "v": 0.88,
                },
                "order_index": 0,
                "pair_id": pair_id,
                "top": {
                    "point_id": "00000000-0000-4000-8000-000000000002",
                    "u": 0.1,
                    "v": 0.08,
                },
            }
        ],
        "portals": [],
        "seam_anchor_pair_id": pair_id,
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": "annotatable",
    }


def test_task_5_5_v1_contract_has_frozen_codes_and_bilingual_copy() -> None:
    legacy = meta_contract_payload(
        schema_version=POC_META_SCHEMA_VERSION,
        copy_version=POC_META_COPY_VERSION,
        task_mode=Task.Mode.MANUAL,
    )
    manual = meta_contract_payload(
        schema_version=META_SCHEMA_V1,
        copy_version=META_COPY_V1,
        task_mode=Task.Mode.MANUAL,
    )
    semi = meta_contract_payload(
        schema_version=META_SCHEMA_V1,
        copy_version=META_COPY_V1,
        task_mode=Task.Mode.SEMI,
    )

    assert manual["schema_version"] == META_SCHEMA_V1
    assert manual["copy_version"] == META_COPY_V1
    assert [option["code"] for option in manual["difficulty_options"]] == [
        "trivial",
        "occlusion",
        "low_texture",
        "seam",
        "reflection",
        "low_quality",
    ]
    assert "model_issue_options" not in manual
    assert legacy["scope_options"][2]["label"] == {
        "en": "Representation out of scope",
        "zh-CN": "表示范围外",
    }
    assert manual["scope_options"][2]["label"] == {
        "en": "Representation not applicable",
        "zh-CN": "当前表示不适用",
    }
    assert [option["code"] for option in semi["model_issue_options"]] == [
        "acceptable",
        "overextend_adjacent",
        "underextend",
        "over_parsing",
        "corner_drift",
        "corner_duplicate",
        "topology_failure",
        "fail",
    ]
    for field in ("scope_options", "scope_reason_options", "difficulty_options"):
        for option in manual[field]:
            assert set(option["label"]) == {"en", "zh-CN"}
            assert all(option["label"].values())


def test_pap_ann_sc_010_026_v1_metadata_is_mode_scoped_and_mutually_exclusive() -> None:
    state = {**legacy_complete_state(), "difficulty": ["reflection", "occlusion"]}
    canonical = canonicalize_annotation_state(
        state,
        meta_schema_version=META_SCHEMA_V1,
        task_mode=Task.Mode.MANUAL,
    )
    assert canonical["difficulty"] == ["occlusion", "reflection"]

    with pytest.raises(AnnotationStateError) as manual_issue:
        canonicalize_annotation_state(
            {**state, "model_issue": ["acceptable"]},
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.MANUAL,
        )
    assert (manual_issue.value.code, manual_issue.value.field) == (
        "annotation_model_issue_forbidden",
        "model_issue",
    )

    for field, values, code in (
        ("difficulty", ["trivial", "occlusion"], "annotation_difficulty_invalid"),
        ("model_issue", ["acceptable", "corner_drift"], "annotation_model_issue_invalid"),
    ):
        invalid = {**state, "model_issue": ["corner_drift"], field: values}
        with pytest.raises(AnnotationStateError) as error:
            canonicalize_annotation_state(
                invalid,
                meta_schema_version=META_SCHEMA_V1,
                task_mode=Task.Mode.SEMI,
            )
        assert (error.value.code, error.value.field) == (code, field)


def test_v1_submission_requires_metadata_without_reinterpreting_legacy_state() -> None:
    legacy = legacy_complete_state()
    assert canonicalize_annotation_state(
        legacy,
        meta_schema_version=POC_META_SCHEMA_VERSION,
        task_mode=Task.Mode.MANUAL,
    ) == canonicalize_annotation_state(legacy)
    assert validate_annotation_submission(
        legacy,
        meta_schema_version=POC_META_SCHEMA_VERSION,
        task_mode=Task.Mode.SEMI,
    ) == canonicalize_annotation_state(legacy)

    with pytest.raises(AnnotationStateError) as missing_v1:
        canonicalize_annotation_state(
            legacy,
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.MANUAL,
        )
    assert (missing_v1.value.code, missing_v1.value.field) == (
        "annotation_field_missing",
        "difficulty",
    )

    manual = {**deepcopy(legacy), "difficulty": []}
    with pytest.raises(AnnotationStateError) as missing_difficulty:
        validate_annotation_submission(
            manual,
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.MANUAL,
        )
    assert (missing_difficulty.value.code, missing_difficulty.value.field) == (
        "annotation_submission_incomplete",
        "difficulty",
    )

    semi = {**deepcopy(manual), "difficulty": ["trivial"], "model_issue": []}
    with pytest.raises(AnnotationStateError) as missing_model_issue:
        validate_annotation_submission(
            semi,
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.SEMI,
        )
    assert (missing_model_issue.value.code, missing_model_issue.value.field) == (
        "annotation_submission_incomplete",
        "model_issue",
    )


def test_v1_metadata_hash_matches_the_shared_browser_golden_vector() -> None:
    assert (
        canonical_annotation_json(
            V1_GOLDEN["state"],
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.SEMI,
        ).decode()
        == V1_GOLDEN["canonical_json"]
    )
    assert (
        annotation_state_sha(
            V1_GOLDEN["state"],
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.SEMI,
        )
        == V1_GOLDEN["state_sha"]
    )
