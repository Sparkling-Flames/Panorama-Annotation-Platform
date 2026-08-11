from __future__ import annotations

import json
from copy import deepcopy
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone
from identity.models import DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.catalog import MediaCatalogCandidate
from media.models import Asset, MediaVariant
from work.annotation_state import AnnotationStatePayload, empty_annotation_state
from work.batch_exports import process_next_batch_export, request_batch_export
from work.meta_schema import META_SCHEMA_V1
from work.models import (
    Assignment,
    BlockReport,
    CurrentDraft,
    PredictionArtifact,
    Task,
)
from work.prediction import create_prediction_artifact
from work.services import assign_task, create_task_draft, create_work_batch, publish_task

pytestmark = pytest.mark.django_db


def media_contract(suffix: str) -> tuple[Asset, MediaVariant]:
    asset = Asset.objects.create(source_key=f"prediction/{suffix}")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key=f"cos://prediction/{suffix}.jpg",
        object_version="v1",
        content_sha256="a" * 64,
        content_length=100,
        content_crc64ecma="11",
        width=20,
        height=10,
        format=MediaVariant.Format.JPEG,
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    return asset, variant


def prediction_state() -> AnnotationStatePayload:
    state = empty_annotation_state(meta_schema_version=META_SCHEMA_V1, task_mode=Task.Mode.SEMI)
    state["pairs"] = [
        {
            "bottom": {
                "point_id": "00000000-0000-4000-8000-000000000003",
                "u": 0.2,
                "v": 0.9,
            },
            "order_index": 0,
            "pair_id": "00000000-0000-4000-8000-000000000001",
            "top": {
                "point_id": "00000000-0000-4000-8000-000000000002",
                "u": 0.2,
                "v": 0.1,
            },
        }
    ]
    state["seam_anchor_pair_id"] = "00000000-0000-4000-8000-000000000001"
    return state


def artifact(asset: Asset, *, checkpoint: str = "b" * 64) -> PredictionArtifact:
    return create_prediction_artifact(
        asset=asset,
        state=prediction_state(),
        model_name="local-panorama-model",
        model_version="local-v1",
        checkpoint_sha256=checkpoint,
        inference_config={"flip": False, "threshold": 0.5},
        import_source="local-admin-upload",
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )


def test_pap_pas_sc_003_prediction_artifact_freezes_canonical_model_contract() -> None:
    asset, _variant = media_contract("contract")

    frozen = artifact(asset)

    assert frozen.state_sha256
    assert len(frozen.state_sha256) == 64
    assert len(frozen.inference_config_sha256) == 64
    assert len(frozen.artifact_sha256) == 64
    assert frozen.state["pairs"][0]["pair_id"] == "00000000-0000-4000-8000-000000000001"
    assert frozen.model_name == "local-panorama-model"
    assert frozen.model_version == "local-v1"
    assert frozen.checkpoint_sha256 == "b" * 64
    assert frozen.inference_config == {"flip": False, "threshold": 0.5}
    assert frozen.import_source == "local-admin-upload"


def test_prediction_artifact_rejects_invalid_state_and_coordinate_mapping() -> None:
    asset, _variant = media_contract("invalid")
    invalid = prediction_state()
    invalid["pairs"][0]["top"]["u"] = 1.0

    with pytest.raises(ValidationError) as bad_state:
        create_prediction_artifact(
            asset=asset,
            state=invalid,
            model_name="local-panorama-model",
            model_version="local-v1",
            checkpoint_sha256="b" * 64,
            inference_config={},
            import_source="local-admin-upload",
            coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
        )
    assert bad_state.value.code == "prediction_state_invalid"

    with pytest.raises(ValidationError) as bad_mapping:
        create_prediction_artifact(
            asset=asset,
            state=prediction_state(),
            model_name="local-panorama-model",
            model_version="local-v1",
            checkpoint_sha256="b" * 64,
            inference_config={},
            import_source="local-admin-upload",
            coordinate_mapping="resized_crop",
        )
    assert bad_mapping.value.code == "prediction_coordinate_mapping_invalid"


def test_pap_pas_sc_002_semi_task_pins_matching_artifact_id_and_hash() -> None:
    asset, variant = media_contract("semi")
    frozen = artifact(asset)

    task = create_task_draft(
        asset=asset,
        media_variants=(variant,),
        mode=Task.Mode.SEMI,
        prediction_artifact=frozen,
    )
    task = publish_task(task_id=task.task_id)

    assert task.prediction_artifact_id == frozen.artifact_id
    assert task.prediction_artifact_sha256 == frozen.artifact_sha256

    other_asset, other_variant = media_contract("other")
    with pytest.raises(ValidationError) as mismatch:
        create_task_draft(
            asset=other_asset,
            media_variants=(other_variant,),
            mode=Task.Mode.SEMI,
            prediction_artifact=frozen,
        )
    assert mismatch.value.code == "prediction_asset_mismatch"


def test_batch_export_traces_only_the_semi_task_model_contract() -> None:
    asset, variant = media_contract("version-export")
    frozen = artifact(asset)
    task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.SEMI,
            prediction_artifact=frozen,
        ).task_id
    )
    batch = create_work_batch(name="Version export")
    worker = User.objects.create_user(
        username="version-export-worker",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    assign_task(batch=batch, task=task, worker=worker)

    request_batch_export(batch_id=batch.batch_id)
    snapshot = process_next_batch_export(worker_id="version-export-job")

    assert snapshot is not None
    exported_task = snapshot.export_manifest["tasks"][0]
    assert exported_task["active_time_rule_version"] == "active-time-v1"
    assert exported_task["meta_schema_version"] == task.meta_schema_version
    assert exported_task["meta_copy_version"] == task.meta_copy_version
    assert exported_task["prediction_artifact"] == {
        "artifact_id": str(frozen.artifact_id),
        "artifact_sha256": frozen.artifact_sha256,
        "checkpoint_sha256": frozen.checkpoint_sha256,
        "inference_config_sha256": frozen.inference_config_sha256,
        "model_name": frozen.model_name,
        "model_version": frozen.model_version,
    }
    assert "geometry_engine_version" not in exported_task
    assert "assist_version" not in exported_task


def test_pap_pas_sc_003_frozen_prediction_and_published_binding_reject_rewrite() -> None:
    asset, variant = media_contract("immutable")
    frozen = artifact(asset)
    task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.SEMI,
            prediction_artifact=frozen,
        ).task_id
    )
    changed_state = deepcopy(frozen.state)
    changed_state["pairs"][0]["top"]["u"] = 0.3

    with pytest.raises(DatabaseError), transaction.atomic():
        PredictionArtifact.objects.filter(pk=frozen.pk).update(state=changed_state)
    with pytest.raises(DatabaseError), transaction.atomic():
        PredictionArtifact.objects.filter(pk=frozen.pk).delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(prediction_artifact_sha256="f" * 64)


def test_pap_pas_sc_004_new_checkpoint_creates_new_artifact_and_task() -> None:
    asset, variant = media_contract("checkpoint")
    first_artifact = artifact(asset, checkpoint="b" * 64)
    first_task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.SEMI,
            prediction_artifact=first_artifact,
        ).task_id
    )

    second_artifact = artifact(asset, checkpoint="c" * 64)
    second_task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.SEMI,
            prediction_artifact=second_artifact,
        ).task_id
    )

    assert second_artifact.artifact_id != first_artifact.artifact_id
    assert second_artifact.artifact_sha256 != first_artifact.artifact_sha256
    assert second_task.task_id != first_task.task_id
    assert first_task.prediction_artifact_id == first_artifact.artifact_id
    assert second_task.prediction_artifact_id == second_artifact.artifact_id


def test_pap_pas_sc_002_pap_pas_sc_006_frozen_semi_initialization_needs_no_model_runtime() -> None:
    asset, variant = media_contract("mode-isolation")
    frozen = artifact(asset)
    manual = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.MANUAL,
        ).task_id
    )
    semi = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.SEMI,
            prediction_artifact=frozen,
        ).task_id
    )
    assigned_worker = User.objects.create_user(
        username="prediction-worker",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    batch = create_work_batch(name="Prediction isolation")
    manual_assignment = assign_task(batch=batch, task=manual, worker=assigned_worker)
    semi_assignment = assign_task(batch=batch, task=semi, worker=assigned_worker)
    tab_id = str(uuid4())
    DataNoticeAcceptance.objects.create(
        worker=assigned_worker, notice_version=CURRENT_DATA_NOTICE_VERSION
    )
    client = Client()
    client.force_login(assigned_worker)
    acquired = client.post(
        "/api/workspace/acquire",
        data=json.dumps(
            {
                "client_instance_id": str(uuid4()),
                "tab_id": tab_id,
                "takeover": False,
            }
        ),
        content_type="application/json",
    )
    assert acquired.status_code == 201

    def open_and_read(assignment_id: object) -> dict[str, object]:
        opened = client.post(
            f"/api/worker/assignments/{assignment_id}/open",
            data=json.dumps({"tab_id": tab_id}),
            content_type="application/json",
        )
        assert opened.status_code == 200, opened.json()
        response = client.get(
            f"/api/worker/assignments/{assignment_id}/draft",
            {"tab_id": tab_id},
        )
        assert response.status_code == 200, response.json()
        return cast(dict[str, object], response.json())

    manual_draft = open_and_read(manual_assignment.assignment_id)
    semi_draft = open_and_read(semi_assignment.assignment_id)
    manual_again = open_and_read(manual_assignment.assignment_id)
    manual_state = cast(dict[str, object], manual_draft["state"])
    semi_state = cast(dict[str, object], semi_draft["state"])
    manual_again_state = cast(dict[str, object], manual_again["state"])

    assert manual_state["pairs"] == []
    assert "model_issue" not in manual_state
    assert all(
        forbidden not in json.dumps(manual_draft).lower()
        for forbidden in ("artifact", "checkpoint", "inference_config", "model_issue")
    )
    assert semi_state["pairs"] == frozen.state["pairs"]
    assert semi_state["model_issue"] == []
    assert manual_again_state == manual_state


def test_pap_pas_sc_005_admin_previews_local_layout_then_freezes_the_same_artifact() -> None:
    asset, variant = media_contract("admin-import")
    admin = User.objects.create_user(username="prediction-admin", role=User.Role.ADMIN)
    other_admin = User.objects.create_user(username="other-prediction-admin", role=User.Role.ADMIN)
    client = Client()
    client.force_login(admin)
    layout_text = json.dumps(
        {
            "image_filename": "admin-import.jpg",
            "image_size": [20, 10],
            "layout": {
                "corners": [
                    {"id": 1, "x": 12, "y_ceiling": 2, "y_floor": 8},
                    {"id": 0, "x": 4, "y_ceiling": 1, "y_floor": 9},
                ],
                "num_corners": 2,
                "order": "sorted_x_cyclic",
            },
            "meta": {"config": "local-model.yaml"},
        },
        separators=(",", ":"),
    )
    candidate = MediaCatalogCandidate(
        source_key=variant.source_key,
        version_id=variant.object_version,
        content_sha256=variant.content_sha256,
        content_length=variant.content_length,
        crc64ecma=variant.content_crc64ecma,
        width=variant.width,
        height=variant.height,
        format=variant.format,
        preview_url="https://private.cos.test/admin-import.jpg?versionId=v1",
    )

    with patch("work.views.get_cos_catalog") as catalog:
        catalog.return_value.get_candidate.return_value = candidate
        previewed = client.post(
            "/api/admin/predictions/preview",
            data=json.dumps(
                {
                    "asset_id": str(asset.asset_id),
                    "checkpoint_sha256": "b" * 64,
                    "import_source": "local-admin-upload",
                    "importer_version": "panorama-layout-json-v1",
                    "layout_text": layout_text,
                    "model_name": "local-panorama-model",
                    "model_version": "local-v1",
                }
            ),
            content_type="application/json",
        )

    assert previewed.status_code == 201, previewed.json()
    preview = previewed.json()
    assert preview["state"]["pairs"][0]["top"] == {
        "point_id": preview["state"]["pairs"][0]["top"]["point_id"],
        "u": 0.2,
        "v": 0.1,
    }
    assert preview["state"]["pairs"][1]["bottom"]["u"] == 0.6
    assert preview["preview_media"]["url"] == candidate.preview_url
    assert preview["raw_output_sha256"]
    assert "layout_text" not in preview

    other_client = Client()
    other_client.force_login(other_admin)
    hidden = other_client.post(
        f"/api/admin/predictions/{preview['preview_id']}/publish",
        data="{}",
        content_type="application/json",
    )
    assert hidden.status_code == 404

    published = client.post(
        f"/api/admin/predictions/{preview['preview_id']}/publish",
        data="{}",
        content_type="application/json",
    )
    replayed = client.post(
        f"/api/admin/predictions/{preview['preview_id']}/publish",
        data="{}",
        content_type="application/json",
    )
    assert published.status_code == 201, published.json()
    assert replayed.status_code == 200
    assert replayed.json() == published.json()
    frozen = PredictionArtifact.objects.get(artifact_id=published.json()["artifact_id"])
    assert frozen.state == preview["state"]
    assert frozen.inference_config == {"config": "local-model.yaml"}
    assert frozen.asset_id == asset.asset_id


def test_pap_pas_sc_011_prediction_import_rejects_unregistered_scripts() -> None:
    asset, _variant = media_contract("bad-import")
    admin = User.objects.create_user(username="bad-prediction-admin", role=User.Role.ADMIN)
    client = Client()
    client.force_login(admin)
    base = {
        "asset_id": str(asset.asset_id),
        "checkpoint_sha256": "b" * 64,
        "import_source": "local-admin-upload",
        "layout_text": "{}",
        "model_name": "local-panorama-model",
        "model_version": "local-v1",
    }

    unregistered = client.post(
        "/api/admin/predictions/preview",
        data=json.dumps({**base, "importer_version": "custom-python-v1"}),
        content_type="application/json",
    )
    script = client.post(
        "/api/admin/predictions/preview",
        data=json.dumps(
            {
                **base,
                "importer_version": "panorama-layout-json-v1",
                "script": "print('do not run')",
            }
        ),
        content_type="application/json",
    )

    assert unregistered.status_code == 400
    assert unregistered.json() == {"error": {"code": "prediction_importer_unsupported"}}
    assert script.status_code == 400
    assert script.json() == {"error": {"code": "invalid_prediction_import"}}


@pytest.mark.parametrize("failure", ["hash", "mapping"])
def test_pap_pas_sc_007_invalid_semi_prediction_blocks_the_real_assignment(failure: str) -> None:
    asset, variant = media_contract(f"blocked-{failure}")
    artifact_id = uuid4()
    artifact_sha256 = "f" * 64
    PredictionArtifact.objects.bulk_create(
        [
            PredictionArtifact(
                artifact_id=artifact_id,
                asset=asset,
                coordinate_mapping=(
                    "resized_crop"
                    if failure == "mapping"
                    else MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY
                ),
                state=prediction_state(),
                state_sha256="0" * 64,
                model_name="local-panorama-model",
                model_version="local-v1",
                checkpoint_sha256="b" * 64,
                inference_config={},
                inference_config_sha256="0" * 64,
                artifact_sha256=artifact_sha256,
                import_source="corruption-test",
            )
        ]
    )
    corrupt_artifact = PredictionArtifact.objects.get(artifact_id=artifact_id)
    task = create_task_draft(
        asset=asset,
        media_variants=(variant,),
        mode=Task.Mode.SEMI,
        prediction_artifact=corrupt_artifact,
    )
    with pytest.raises(ValidationError) as rejected:
        publish_task(task_id=task.task_id)
    assert rejected.value.code == "semi_prediction_not_ready"
    task.status = Task.Status.PUBLISHED
    task.published_at = timezone.now()
    task.save(update_fields=["published_at", "status"])
    worker = User.objects.create_user(
        username=f"blocked-{failure}-worker",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    assignment = assign_task(
        batch=create_work_batch(name=f"Blocked {failure}"),
        task=task,
        worker=worker,
    )
    tab_id = str(uuid4())
    DataNoticeAcceptance.objects.create(worker=worker, notice_version=CURRENT_DATA_NOTICE_VERSION)
    client = Client()
    client.force_login(worker)
    acquired = client.post(
        "/api/workspace/acquire",
        data=json.dumps({"client_instance_id": str(uuid4()), "tab_id": tab_id, "takeover": False}),
        content_type="application/json",
    )
    assert acquired.status_code == 201
    opened = client.post(
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        data=json.dumps({"tab_id": tab_id}),
        content_type="application/json",
    )
    assert opened.status_code == 200

    response = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft",
        {"tab_id": tab_id},
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "semi_prediction_unavailable"}}
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.BLOCKED
    report = BlockReport.objects.get(assignment=assignment)
    assert report.reason_code == BlockReport.ReasonCode.TECHNICAL_FAILURE
    assert report.reason_text == "semi_prediction_unavailable"
    assert not CurrentDraft.objects.filter(draft_cycle__assignment=assignment).exists()
