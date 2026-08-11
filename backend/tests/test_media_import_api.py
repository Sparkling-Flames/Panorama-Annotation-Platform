from __future__ import annotations

import importlib
import json
import secrets
from typing import Any
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from identity.models import AuditEvent

pytestmark = pytest.mark.django_db

HIGH_RESOLUTION_HASH = "a" * 64
COMPRESSED_HASH = "b" * 64
ASSET_SOURCE_KEY = "panoramas/warehouse-001"
HIGH_RESOLUTION_SOURCE_KEY = "incoming/warehouse-001/high.png"
COMPRESSED_SOURCE_KEY = "incoming/warehouse-001/compressed.jpg"


def media_catalog() -> Any:
    return importlib.import_module("media.catalog")


def media_views() -> Any:
    return importlib.import_module("media.views")


def media_workflows() -> Any:
    return importlib.import_module("work.media_workflows")


def set_catalog(monkeypatch: pytest.MonkeyPatch, catalog: Any) -> None:
    monkeypatch.setattr(media_views(), "get_cos_catalog", catalog)
    monkeypatch.setattr(media_workflows(), "get_cos_catalog", catalog)


def create_admin_client(*, username: str = "media-administrator") -> Client:
    user_model = get_user_model()
    administrator = user_model.objects.create_superuser(
        username=username,
        password=secrets.token_urlsafe(18),
    )
    client = Client()
    client.force_login(administrator)
    return client


def catalog_with_warehouse_candidates(
    *,
    high_resolution_hash: str = HIGH_RESOLUTION_HASH,
    compressed_hash: str = COMPRESSED_HASH,
    high_resolution_source_key: str = HIGH_RESOLUTION_SOURCE_KEY,
    compressed_source_key: str = COMPRESSED_SOURCE_KEY,
    high_resolution_width: int = 4096,
    high_resolution_height: int = 2048,
    high_resolution_format: str = "png",
    compressed_width: int = 2048,
    compressed_height: int = 1024,
) -> Any:
    module = media_catalog()
    candidates = (
        module.MediaCatalogCandidate(
            source_key=high_resolution_source_key,
            version_id="cos-version-high-001",
            content_sha256=high_resolution_hash,
            content_length=8_388_608,
            crc64ecma="12345678901234567890",
            width=high_resolution_width,
            height=high_resolution_height,
            format=high_resolution_format,
            preview_url="https://cos.example.test/preview/high.png?signature=redacted",
        ),
        module.MediaCatalogCandidate(
            source_key=compressed_source_key,
            version_id="cos-version-compressed-001",
            content_sha256=compressed_hash,
            content_length=1_048_576,
            crc64ecma="1234567890",
            width=compressed_width,
            height=compressed_height,
            format="jpeg",
            preview_url="https://cos.example.test/preview/compressed.jpg?signature=redacted",
        ),
    )

    class FakeMediaCatalog:
        def list_candidates(self, *, prefix: str, marker: str | None) -> Any:
            assert marker is None
            return module.MediaCatalogPage(
                candidates=tuple(
                    candidate for candidate in candidates if candidate.source_key.startswith(prefix)
                ),
                next_marker=None,
            )

        def get_candidate(self, *, source_key: str) -> Any:
            for candidate in candidates:
                if candidate.source_key == source_key:
                    return candidate
            raise module.MediaCandidateNotFound(source_key)

    return FakeMediaCatalog()


def request_payload(
    *,
    asset_source_key: str = ASSET_SOURCE_KEY,
    create_annotation_round: bool = False,
    high_resolution_source_key: str = HIGH_RESOLUTION_SOURCE_KEY,
    compressed_source_key: str = COMPRESSED_SOURCE_KEY,
) -> bytes:
    return json.dumps(
        {
            "asset_source_key": asset_source_key,
            "create_annotation_round": create_annotation_round,
            "high_resolution_source_key": high_resolution_source_key,
            "compressed_source_key": compressed_source_key,
        }
    ).encode("utf-8")


def publication_request(preview: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "expected_plan_sha256": preview["plan_sha256"],
            "preview_id": preview["preview_id"],
        }
    ).encode("utf-8")


def test_pap_mid_sc_005_sc_006_admin_imports_media_and_creates_explicit_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = catalog_with_warehouse_candidates()
    set_catalog(monkeypatch, lambda: catalog)
    client = create_admin_client()

    browse_response = client.get("/api/admin/media/candidates?prefix=incoming/warehouse-001")

    assert browse_response.status_code == 200
    assert browse_response.json() == {
        "candidates": [
            {
                "content_crc64ecma": "12345678901234567890",
                "content_length": 8_388_608,
                "content_sha256": HIGH_RESOLUTION_HASH,
                "coordinate_mapping": "normalized_identity",
                "format": "png",
                "height": 2048,
                "object_version": "cos-version-high-001",
                "preview_url": "https://cos.example.test/preview/high.png?signature=redacted",
                "source_key": HIGH_RESOLUTION_SOURCE_KEY,
                "width": 4096,
            },
            {
                "content_crc64ecma": "1234567890",
                "content_length": 1_048_576,
                "content_sha256": COMPRESSED_HASH,
                "coordinate_mapping": "normalized_identity",
                "format": "jpeg",
                "height": 1024,
                "object_version": "cos-version-compressed-001",
                "preview_url": "https://cos.example.test/preview/compressed.jpg?signature=redacted",
                "source_key": COMPRESSED_SOURCE_KEY,
                "width": 2048,
            },
        ],
        "next_marker": None,
    }

    preview_response = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(create_annotation_round=True),
        content_type="application/json",
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert UUID(preview_payload["preview_id"]).version == 4
    assert len(preview_payload["plan_sha256"]) == 64
    assert preview_payload["expires_at"]
    assert {
        key: value
        for key, value in preview_payload.items()
        if key not in {"expires_at", "plan_sha256", "preview_id"}
    } == {
        "asset_source_key": ASSET_SOURCE_KEY,
        "asset_will_be_reused": False,
        "create_annotation_round": True,
        "variants": [
            {
                "content_crc64ecma": "12345678901234567890",
                "content_length": 8_388_608,
                "content_sha256": HIGH_RESOLUTION_HASH,
                "coordinate_mapping": "normalized_identity",
                "format": "png",
                "height": 2048,
                "object_version": "cos-version-high-001",
                "preview_url": "https://cos.example.test/preview/high.png?signature=redacted",
                "role": "high_resolution",
                "source_key": HIGH_RESOLUTION_SOURCE_KEY,
                "width": 4096,
            },
            {
                "content_crc64ecma": "1234567890",
                "content_length": 1_048_576,
                "content_sha256": COMPRESSED_HASH,
                "coordinate_mapping": "normalized_identity",
                "format": "jpeg",
                "height": 1024,
                "object_version": "cos-version-compressed-001",
                "preview_url": "https://cos.example.test/preview/compressed.jpg?signature=redacted",
                "role": "compressed",
                "source_key": COMPRESSED_SOURCE_KEY,
                "width": 2048,
            },
        ],
    }
    models = importlib.import_module("media.models")
    assert models.Asset.objects.count() == 0
    assert models.MediaVariant.objects.count() == 0

    first_publish_response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview_payload),
        content_type="application/json",
    )
    assert first_publish_response.status_code == 201
    first_payload = first_publish_response.json()
    assert first_payload["created_asset"] is True
    first_round = first_payload["annotation_round"]
    assert UUID(first_round["task_id"]).version == 4
    assert first_round == {
        "asset_id": first_payload["asset_id"],
        "mode": "manual",
        "previous_task_id": None,
        "status": "published",
        "task_id": first_round["task_id"],
    }
    assert len(first_payload["media_variants"]) == 2
    assert {variant["created"] for variant in first_payload["media_variants"]} == {True}
    assert {
        (variant["object_version"], variant["content_length"], variant["content_crc64ecma"])
        for variant in first_payload["media_variants"]
    } == {
        ("cos-version-high-001", 8_388_608, "12345678901234567890"),
        ("cos-version-compressed-001", 1_048_576, "1234567890"),
    }

    retry_publish_response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview_payload),
        content_type="application/json",
    )
    assert retry_publish_response.status_code == 201
    assert retry_publish_response.json() == first_payload

    duplicate_preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(create_annotation_round=True),
        content_type="application/json",
    ).json()
    second_publish_response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(duplicate_preview),
        content_type="application/json",
    )
    assert second_publish_response.status_code == 200
    second_payload = second_publish_response.json()
    assert second_payload["created_asset"] is False
    assert {variant["created"] for variant in second_payload["media_variants"]} == {False}
    assert second_payload["asset_id"] == first_payload["asset_id"]
    assert UUID(second_payload["annotation_round"]["task_id"]).version == 4
    assert second_payload["annotation_round"]["task_id"] != first_round["task_id"]
    assert second_payload["annotation_round"]["previous_task_id"] == first_round["task_id"]
    assert models.Asset.objects.count() == 1
    assert models.MediaVariant.objects.count() == 2
    work_models = importlib.import_module("work.models")
    assert work_models.Task.objects.filter(asset_id=first_payload["asset_id"]).count() == 2
    publication_audits = list(
        AuditEvent.objects.filter(action="task.published").order_by("created_at")
    )
    assert [event.target_id for event in publication_audits] == [
        first_round["task_id"],
        second_payload["annotation_round"]["task_id"],
    ]
    assert {event.target_type for event in publication_audits} == {"task"}
    assert {event.reason for event in publication_audits} == {"media_import_annotation_round"}


def test_media_and_round_publication_roll_back_together_when_task_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_catalog(monkeypatch, catalog_with_warehouse_candidates)
    workflows = media_workflows()
    create_round = workflows.create_manual_annotation_round

    def reject_round_creation(**_kwargs: object) -> None:
        raise ValidationError("Task creation failed.", code="task_creation_failed")

    monkeypatch.setattr(workflows, "create_manual_annotation_round", reject_round_creation)
    client = create_admin_client()
    preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(create_annotation_round=True),
        content_type="application/json",
    ).json()

    response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "task_creation_failed"}}
    models = importlib.import_module("media.models")
    stored_preview = models.MediaImportPreview.objects.get(preview_id=preview["preview_id"])
    assert stored_preview.published_at is None
    assert stored_preview.published_asset_id is None
    assert models.Asset.objects.count() == 0
    assert models.MediaVariant.objects.count() == 0

    monkeypatch.setattr(workflows, "create_manual_annotation_round", create_round)
    retry = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert retry.status_code == 201
    assert retry.json()["annotation_round"]["status"] == "published"
    stored_preview.refresh_from_db()
    assert stored_preview.published_at is not None
    assert models.Asset.objects.count() == 1
    assert models.MediaVariant.objects.count() == 2
    assert importlib.import_module("work.models").Task.objects.count() == 1


def test_media_publish_accepts_only_a_frozen_preview_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_catalog(monkeypatch, catalog_with_warehouse_candidates)
    client = create_admin_client()
    preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()

    changed_form = json.loads(request_payload())
    changed_form["asset_source_key"] = "panoramas/changed-after-preview"
    response = client.post(
        "/api/admin/media/imports/publish",
        data=json.dumps(changed_form),
        content_type="application/json",
    )

    assert preview["preview_id"]
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "invalid_media_import_publication"}}


@pytest.mark.parametrize(
    ("catalog_options", "error_code"),
    [
        (
            {
                "high_resolution_width": 4000,
                "high_resolution_height": 1500,
                "compressed_width": 2000,
                "compressed_height": 750,
            },
            "media_panorama_aspect_ratio_invalid",
        ),
        ({"high_resolution_format": "webp"}, "media_format_unsupported"),
        ({"compressed_width": 3072}, "media_variant_mapping_incompatible"),
    ],
)
def test_media_import_api_returns_stable_validation_codes(
    monkeypatch: pytest.MonkeyPatch,
    catalog_options: dict[str, Any],
    error_code: str,
) -> None:
    views = media_views()
    monkeypatch.setattr(
        views,
        "get_cos_catalog",
        lambda: catalog_with_warehouse_candidates(**catalog_options),
    )
    client = create_admin_client()

    response = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": error_code}}


def test_pap_mid_sc_004_media_import_api_rejects_a_skybox_face_set() -> None:
    client = create_admin_client()
    payload = json.loads(request_payload())
    payload["skybox_face_source_keys"] = [
        f"incoming/warehouse-001/face-{index}.png" for index in range(6)
    ]

    response = client.post(
        "/api/admin/media/imports/preview",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "media_skybox_not_supported"}}


def test_pap_mid_sc_002_media_publish_rejects_cos_metadata_changed_after_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = catalog_with_warehouse_candidates()
    set_catalog(monkeypatch, lambda: catalog)
    client = create_admin_client()
    preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()
    catalog = catalog_with_warehouse_candidates(high_resolution_hash="c" * 64)

    response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "media_import_integrity_conflict"}}
    models = importlib.import_module("media.models")
    assert models.Asset.objects.count() == 0


def test_media_import_api_maps_registered_cos_integrity_drift_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = media_catalog()

    class IntegrityConflictCatalog:
        def list_candidates(self, *, prefix: str, marker: str | None) -> Any:
            raise module.MediaCandidateIntegrityConflict

        def get_candidate(self, *, source_key: str) -> Any:
            raise module.MediaCandidateIntegrityConflict

    client = create_admin_client()
    set_catalog(monkeypatch, IntegrityConflictCatalog)

    browse_response = client.get("/api/admin/media/candidates")
    preview_response = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    )

    assert browse_response.status_code == 409
    assert preview_response.status_code == 409
    expected_error = {"error": {"code": "media_candidate_integrity_conflict"}}
    assert browse_response.json() == expected_error
    assert preview_response.json() == expected_error

    set_catalog(monkeypatch, catalog_with_warehouse_candidates)
    preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()
    set_catalog(monkeypatch, IntegrityConflictCatalog)

    publish_response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert publish_response.status_code == 409
    assert publish_response.json() == expected_error


def test_media_preview_can_only_be_published_by_its_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_catalog(monkeypatch, catalog_with_warehouse_candidates)
    owner = create_admin_client(username="preview-owner")
    other_admin = create_admin_client(username="other-admin")
    preview = owner.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()

    response = other_admin.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "media_import_preview_not_found"}}


def test_expired_media_preview_cannot_be_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_catalog(monkeypatch, catalog_with_warehouse_candidates)
    client = create_admin_client()
    preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()
    models = importlib.import_module("media.models")
    models.MediaImportPreview.objects.filter(preview_id=preview["preview_id"]).update(
        expires_at="2000-01-01T00:00:00Z"
    )

    response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "media_import_preview_expired"}}


def test_pap_mid_sc_007_cancelled_media_preview_cannot_publish_or_create_an_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_catalog(monkeypatch, catalog_with_warehouse_candidates)
    client = create_admin_client()
    preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()

    cancel_response = client.post(
        "/api/admin/media/imports/cancel",
        data=publication_request(preview),
        content_type="application/json",
    )
    publish_response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(preview),
        content_type="application/json",
    )

    assert cancel_response.status_code == 200
    assert cancel_response.json() == {"cancelled": True, "preview_id": preview["preview_id"]}
    assert publish_response.status_code == 409
    assert publish_response.json() == {"error": {"code": "media_import_preview_cancelled"}}
    models = importlib.import_module("media.models")
    assert models.Asset.objects.count() == 0
    assert models.MediaVariant.objects.count() == 0


@pytest.mark.parametrize(
    ("second_asset_source_key", "second_high_source_key", "second_high_hash", "error_code"),
    [
        (ASSET_SOURCE_KEY, HIGH_RESOLUTION_SOURCE_KEY, "c" * 64, "media_source_key_conflict"),
        (
            "panoramas/warehouse-002",
            HIGH_RESOLUTION_SOURCE_KEY,
            HIGH_RESOLUTION_HASH,
            "media_source_key_conflict",
        ),
    ],
)
def test_media_import_conflicts_have_stable_codes_and_leave_no_partial_data(
    monkeypatch: pytest.MonkeyPatch,
    second_asset_source_key: str,
    second_high_source_key: str,
    second_high_hash: str,
    error_code: str,
) -> None:
    catalog = catalog_with_warehouse_candidates()
    set_catalog(monkeypatch, lambda: catalog)
    client = create_admin_client()
    first_preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()
    assert (
        client.post(
            "/api/admin/media/imports/publish",
            data=publication_request(first_preview),
            content_type="application/json",
        ).status_code
        == 201
    )

    catalog = catalog_with_warehouse_candidates(
        high_resolution_hash=second_high_hash,
        high_resolution_source_key=second_high_source_key,
    )
    second_preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(
            asset_source_key=second_asset_source_key,
            high_resolution_source_key=second_high_source_key,
        ),
        content_type="application/json",
    ).json()
    response = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(second_preview),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": error_code}}
    models = importlib.import_module("media.models")
    assert models.Asset.objects.count() == 1
    assert models.MediaVariant.objects.count() == 2


def test_media_import_api_appends_a_new_same_role_variant_to_an_existing_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = catalog_with_warehouse_candidates()
    set_catalog(monkeypatch, lambda: catalog)
    client = create_admin_client()
    first_preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(),
        content_type="application/json",
    ).json()
    first_publication = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(first_preview),
        content_type="application/json",
    )
    assert first_publication.status_code == 201

    replacement_source_key = "incoming/warehouse-001/high-v2.png"
    catalog = catalog_with_warehouse_candidates(
        high_resolution_hash="c" * 64,
        high_resolution_source_key=replacement_source_key,
    )
    replacement_preview = client.post(
        "/api/admin/media/imports/preview",
        data=request_payload(high_resolution_source_key=replacement_source_key),
        content_type="application/json",
    ).json()
    replacement_publication = client.post(
        "/api/admin/media/imports/publish",
        data=publication_request(replacement_preview),
        content_type="application/json",
    )

    assert replacement_publication.status_code == 200
    assert replacement_publication.json()["created_asset"] is False
    assert replacement_publication.json()["asset_id"] == first_publication.json()["asset_id"]
    models = importlib.import_module("media.models")
    assert models.MediaVariant.objects.filter(role="high_resolution").count() == 2
    assert models.MediaVariant.objects.count() == 3


def test_media_import_api_rejects_non_admin_requests() -> None:
    user_model = get_user_model()
    worker = user_model.objects.create_user(
        username="media-worker",
        password=secrets.token_urlsafe(18),
        role=user_model.Role.WORKER,
        must_change_password=False,
    )
    client = Client()
    client.force_login(worker)

    response = client.get("/api/admin/media/candidates")

    assert response.status_code == 403
    assert response.json() == {"error": {"code": "admin_required"}}
