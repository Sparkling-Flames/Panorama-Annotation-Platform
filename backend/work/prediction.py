from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256
from math import isfinite
from uuid import NAMESPACE_URL, UUID, uuid5

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from identity.authorization import ResourceNotFound
from identity.models import User
from media.models import Asset, MediaVariant

from .annotation_state import (
    AnnotationStateError,
    AnnotationStatePayload,
    PointPairPayload,
    annotation_state_sha,
    canonicalize_annotation_state,
    empty_annotation_state,
)
from .meta_schema import META_SCHEMA_V1
from .models import PredictionArtifact, PredictionImportPreview, Task

LOCAL_LAYOUT_IMPORTER_V1 = "panorama-layout-json-v1"
PREVIEW_TTL_SECONDS = 900


def _artifact_sha256(
    *,
    asset_id: UUID,
    checkpoint_sha256: str,
    coordinate_mapping: str,
    import_source: str,
    inference_config_sha256: str,
    model_name: str,
    model_version: str,
    state_sha256: str,
) -> str:
    contract = json.dumps(
        {
            "asset_id": str(asset_id),
            "checkpoint_sha256": checkpoint_sha256,
            "coordinate_mapping": coordinate_mapping,
            "import_source": import_source,
            "inference_config_sha256": inference_config_sha256,
            "model_name": model_name,
            "model_version": model_version,
            "state_sha256": state_sha256,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(contract.encode()).hexdigest()


def prediction_artifact_matches_contract(
    artifact: PredictionArtifact,
    *,
    asset_id: UUID,
    expected_sha256: str,
) -> bool:
    if (
        artifact.asset_id != asset_id
        or artifact.artifact_sha256 != expected_sha256
        or artifact.coordinate_mapping != MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY
        or artifact.model_name != artifact.model_name.strip()
        or artifact.model_version != artifact.model_version.strip()
        or artifact.import_source != artifact.import_source.strip()
        or len(artifact.checkpoint_sha256) != 64
        or any(character not in "0123456789abcdef" for character in artifact.checkpoint_sha256)
        or not isinstance(artifact.inference_config, dict)
    ):
        return False
    try:
        config_json = json.dumps(
            artifact.inference_config,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        canonical_state = canonicalize_annotation_state(
            artifact.state,
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.SEMI,
        )
        state_sha256 = annotation_state_sha(
            canonical_state,
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.SEMI,
        )
    except (AnnotationStateError, TypeError, ValueError):
        return False
    config_sha256 = sha256(config_json.encode()).hexdigest()
    return (
        canonical_state == artifact.state
        and state_sha256 == artifact.state_sha256
        and config_sha256 == artifact.inference_config_sha256
        and artifact.artifact_sha256
        == _artifact_sha256(
            asset_id=asset_id,
            checkpoint_sha256=artifact.checkpoint_sha256,
            coordinate_mapping=artifact.coordinate_mapping,
            import_source=artifact.import_source,
            inference_config_sha256=config_sha256,
            model_name=artifact.model_name,
            model_version=artifact.model_version,
            state_sha256=state_sha256,
        )
    )


def create_prediction_artifact(
    *,
    asset: Asset,
    state: object,
    model_name: str,
    model_version: str,
    checkpoint_sha256: str,
    inference_config: object,
    import_source: str,
    coordinate_mapping: str,
) -> PredictionArtifact:
    if coordinate_mapping != MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY:
        raise ValidationError(
            "Prediction coordinates must use normalized identity mapping.",
            code="prediction_coordinate_mapping_invalid",
        )
    if len(checkpoint_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in checkpoint_sha256
    ):
        raise ValidationError(
            "A lowercase checkpoint SHA-256 is required.",
            code="prediction_checkpoint_invalid",
        )
    clean_model_name = model_name.strip()
    clean_model_version = model_version.strip()
    clean_import_source = import_source.strip()
    if not clean_model_name or not clean_model_version or not clean_import_source:
        raise ValidationError(
            "Prediction provenance is incomplete.", code="prediction_provenance_invalid"
        )
    if not isinstance(inference_config, dict):
        raise ValidationError(
            "Inference config must be a JSON object.", code="prediction_config_invalid"
        )
    try:
        config_json = json.dumps(
            inference_config,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        canonical_config = json.loads(config_json)
    except (TypeError, ValueError) as error:
        raise ValidationError(
            "Inference config must be finite JSON.", code="prediction_config_invalid"
        ) from error
    try:
        canonical_state = canonicalize_annotation_state(
            state,
            meta_schema_version=META_SCHEMA_V1,
            task_mode=Task.Mode.SEMI,
        )
    except AnnotationStateError as error:
        raise ValidationError(
            "Prediction state is invalid.", code="prediction_state_invalid"
        ) from error
    state_sha256 = annotation_state_sha(
        canonical_state,
        meta_schema_version=META_SCHEMA_V1,
        task_mode=Task.Mode.SEMI,
    )
    config_sha256 = sha256(config_json.encode()).hexdigest()
    return PredictionArtifact.objects.create(
        asset=asset,
        coordinate_mapping=coordinate_mapping,
        state=canonical_state,
        state_sha256=state_sha256,
        model_name=clean_model_name,
        model_version=clean_model_version,
        checkpoint_sha256=checkpoint_sha256,
        inference_config=canonical_config,
        inference_config_sha256=config_sha256,
        artifact_sha256=_artifact_sha256(
            asset_id=asset.asset_id,
            checkpoint_sha256=checkpoint_sha256,
            coordinate_mapping=coordinate_mapping,
            import_source=clean_import_source,
            inference_config_sha256=config_sha256,
            model_name=clean_model_name,
            model_version=clean_model_version,
            state_sha256=state_sha256,
        ),
        import_source=clean_import_source,
    )


def preview_prediction_import(
    *,
    actor: User,
    asset_id: UUID,
    importer_version: str,
    layout_text: str,
    model_name: str,
    model_version: str,
    checkpoint_sha256: str,
    import_source: str,
) -> PredictionImportPreview:
    if importer_version != LOCAL_LAYOUT_IMPORTER_V1:
        raise ValidationError(
            "Unsupported prediction importer.", code="prediction_importer_unsupported"
        )
    if len(layout_text.encode()) > 2_000_000:
        raise ValidationError("Prediction output is too large.", code="prediction_output_too_large")
    asset = Asset.objects.filter(asset_id=asset_id).first()
    if asset is None:
        raise ResourceNotFound
    try:
        raw = json.loads(layout_text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValidationError(
            "Prediction output is not valid JSON.", code="prediction_output_invalid"
        ) from error
    state, inference_config = _layout_state(asset=asset, raw=raw)
    canonical_state = canonicalize_annotation_state(
        state,
        meta_schema_version=META_SCHEMA_V1,
        task_mode=Task.Mode.SEMI,
    )
    state_sha256 = annotation_state_sha(
        canonical_state,
        meta_schema_version=META_SCHEMA_V1,
        task_mode=Task.Mode.SEMI,
    )
    contract = {
        "checkpoint_sha256": checkpoint_sha256,
        "coordinate_mapping": MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
        "import_source": import_source,
        "inference_config": inference_config,
        "model_name": model_name,
        "model_version": model_version,
        "state": canonical_state,
        "state_sha256": state_sha256,
    }
    contract_json = json.dumps(
        contract,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return PredictionImportPreview.objects.create(
        actor=actor,
        asset=asset,
        importer_version=importer_version,
        contract=contract,
        contract_sha256=sha256(contract_json.encode()).hexdigest(),
        raw_output_sha256=sha256(layout_text.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(seconds=PREVIEW_TTL_SECONDS),
    )


@transaction.atomic
def publish_prediction_import(
    *,
    actor: User,
    preview_id: UUID,
) -> tuple[PredictionArtifact, bool]:
    preview = (
        PredictionImportPreview.objects.select_for_update()
        .filter(preview_id=preview_id, actor=actor)
        .first()
    )
    if preview is None:
        raise ResourceNotFound
    if preview.published_artifact_id is not None:
        published_artifact = preview.published_artifact
        if published_artifact is None:
            raise RuntimeError("Published prediction preview is missing its artifact.")
        return published_artifact, False
    if preview.expires_at <= timezone.now():
        raise ValidationError("Prediction preview expired.", code="prediction_preview_expired")
    contract_json = json.dumps(
        preview.contract,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if sha256(contract_json.encode()).hexdigest() != preview.contract_sha256:
        raise ValidationError("Prediction preview changed.", code="prediction_preview_conflict")
    contract = preview.contract
    try:
        artifact = create_prediction_artifact(
            asset=preview.asset,
            state=contract["state"],
            model_name=contract["model_name"],
            model_version=contract["model_version"],
            checkpoint_sha256=contract["checkpoint_sha256"],
            inference_config=contract["inference_config"],
            import_source=contract["import_source"],
            coordinate_mapping=contract["coordinate_mapping"],
        )
    except (KeyError, TypeError) as error:
        raise ValidationError(
            "Prediction preview is invalid.", code="prediction_preview_conflict"
        ) from error
    preview.published_artifact = artifact
    preview.save(update_fields=["published_artifact"])
    return artifact, True


def _layout_state(*, asset: Asset, raw: object) -> tuple[AnnotationStatePayload, dict[str, object]]:
    try:
        if not isinstance(raw, dict) or set(raw) != {
            "image_filename",
            "image_size",
            "layout",
            "meta",
        }:
            raise ValueError
        image_size = raw["image_size"]
        layout = raw["layout"]
        meta = raw["meta"]
        if (
            not isinstance(image_size, list)
            or len(image_size) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in image_size
            )
            or not all(isfinite(value) and value > 0 for value in image_size)
            or not isinstance(layout, dict)
            or set(layout) != {"corners", "num_corners", "order"}
            or layout["order"] != "sorted_x_cyclic"
            or not isinstance(layout["corners"], list)
            or not layout["corners"]
            or layout["num_corners"] != len(layout["corners"])
            or not isinstance(meta, dict)
        ):
            raise ValueError
        width, height = image_size
        corners: list[tuple[float, float, float]] = []
        for corner in layout["corners"]:
            if not isinstance(corner, dict) or set(corner) != {
                "id",
                "x",
                "y_ceiling",
                "y_floor",
            }:
                raise ValueError
            x, ceiling, floor = corner["x"], corner["y_ceiling"], corner["y_floor"]
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                for value in (x, ceiling, floor)
            ):
                raise ValueError
            if not (0 <= x < width and 0 <= ceiling < floor <= height):
                raise ValueError
            corners.append((float(x), float(ceiling), float(floor)))
        corners.sort()
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationError(
            "Local layout output is invalid.", code="prediction_output_invalid"
        ) from error

    state = empty_annotation_state(meta_schema_version=META_SCHEMA_V1, task_mode=Task.Mode.SEMI)
    pairs: list[PointPairPayload] = []
    for index, (x, ceiling, floor) in enumerate(corners):
        identity = f"panorama-annotation:{asset.asset_id}:prediction:{index}"
        pairs.append(
            {
                "bottom": {
                    "point_id": str(uuid5(NAMESPACE_URL, identity + ":bottom")),
                    "u": x / width,
                    "v": floor / height,
                },
                "order_index": index,
                "pair_id": str(uuid5(NAMESPACE_URL, identity + ":pair")),
                "top": {
                    "point_id": str(uuid5(NAMESPACE_URL, identity + ":top")),
                    "u": x / width,
                    "v": ceiling / height,
                },
            }
        )
    state["pairs"] = pairs
    state["seam_anchor_pair_id"] = pairs[0]["pair_id"]
    return state, dict(meta)
