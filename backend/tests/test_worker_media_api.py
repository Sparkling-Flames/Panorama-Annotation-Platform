from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from django.test import Client
from identity.models import DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.catalog import MediaCandidateNotFound, MediaCatalogCandidate
from media.models import Asset, MediaObjectRegistration, MediaVariant
from work.models import OperationalIssue, Task
from work.services import (
    assign_task,
    cancel_task,
    create_task_draft,
    create_work_batch,
    publish_task,
)

pytestmark = pytest.mark.django_db


class RecordingCosClient:
    def __init__(self) -> None:
        self.head_calls: list[dict[str, Any]] = []
        self.sign_calls: list[dict[str, Any]] = []

    def head_object(self, **kwargs: Any) -> dict[str, str]:
        self.head_calls.append(kwargs)
        registration = MediaObjectRegistration.objects.get(source_key=kwargs["Key"])
        assert kwargs["VersionId"] == registration.version_id
        return {
            "Content-Length": str(registration.content_length),
            "Content-Type": "image/jpeg" if registration.format == "jpeg" else "image/png",
            "x-cos-hash-crc64ecma": registration.crc64ecma,
            "x-cos-meta-content-sha256": registration.content_sha256,
            "x-cos-meta-height": str(registration.height),
            "x-cos-meta-width": str(registration.width),
            "x-cos-version-id": registration.version_id,
        }

    def get_presigned_url(self, **kwargs: Any) -> str:
        self.sign_calls.append(kwargs)
        return (
            f"https://private.cos.example.test/{kwargs['Key']}"
            f"?versionId={kwargs['Params']['versionId']}&renewal={len(self.sign_calls)}"
        )


def worker(username: str) -> User:
    return User.objects.create_user(
        username=username,
        role=User.Role.WORKER,
        must_change_password=False,
    )


def media_variant(*, asset: Asset, role: str, suffix: str, version: str) -> MediaVariant:
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key=f"incoming/{suffix}/{role}",
        object_version=version,
        content_sha256=("a" if role == MediaVariant.Role.COMPRESSED else "b") * 64,
        content_length=1_024 if role == MediaVariant.Role.COMPRESSED else 4_096,
        content_crc64ecma="1234567890",
        width=2_048 if role == MediaVariant.Role.COMPRESSED else 4_096,
        height=1_024 if role == MediaVariant.Role.COMPRESSED else 2_048,
        format=(
            MediaVariant.Format.JPEG
            if role == MediaVariant.Role.COMPRESSED
            else MediaVariant.Format.PNG
        ),
        role=role,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    MediaObjectRegistration.objects.create(
        source_key=variant.source_key,
        version_id=variant.object_version,
        content_sha256=variant.content_sha256,
        content_length=variant.content_length,
        crc64ecma=variant.content_crc64ecma,
        width=variant.width,
        height=variant.height,
        format=variant.format,
        manifest_sha256="c" * 64,
    )
    return variant


def assignment_with_media(
    *, owner: User, suffix: str = "delivery"
) -> tuple[Any, tuple[MediaVariant, ...]]:
    asset = Asset.objects.create(source_key=f"panoramas/{suffix}")
    variants = (
        media_variant(
            asset=asset,
            role=MediaVariant.Role.HIGH_RESOLUTION,
            suffix=suffix,
            version="cos-high-v7",
        ),
        media_variant(
            asset=asset,
            role=MediaVariant.Role.COMPRESSED,
            suffix=suffix,
            version="cos-compressed-v3",
        ),
    )
    draft = create_task_draft(asset=asset, media_variants=variants, mode=Task.Mode.MANUAL)
    task = publish_task(task_id=draft.task_id)
    assignment = assign_task(
        batch=create_work_batch(name=f"{suffix} batch"),
        task=task,
        worker=owner,
    )
    return assignment, variants


def logged_in(user: User) -> Client:
    DataNoticeAcceptance.objects.get_or_create(
        worker=user,
        notice_version=CURRENT_DATA_NOTICE_VERSION,
    )
    client = Client()
    client.force_login(user)
    return client


def test_worker_cannot_read_assigned_media_before_accepting_current_notice(
    monkeypatch: Any,
) -> None:
    owner = worker("media-notice-gated-owner")
    assignment, _variants = assignment_with_media(owner=owner, suffix="notice-gated")
    cos = RecordingCosClient()
    monkeypatch.setattr("media.catalog.configured_cos_client", lambda: (cos, "private-bucket"))
    client = Client()
    client.force_login(owner)

    response = client.get(f"/api/worker/assignments/{assignment.assignment_id}/media")

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "notice_acceptance_required"}}
    assert cos.head_calls == cos.sign_calls == []


def candidate_for(variant: MediaVariant, *, version: str | None = None) -> MediaCatalogCandidate:
    return MediaCatalogCandidate(
        source_key=variant.source_key,
        version_id=version or variant.object_version,
        content_sha256=variant.content_sha256,
        content_length=variant.content_length,
        crc64ecma=variant.content_crc64ecma,
        width=variant.width,
        height=variant.height,
        format=variant.format,
        preview_url=f"https://private.cos.example.test/{variant.media_variant_id}",
    )


def test_pap_mid_sc_008_owned_assignment_signs_only_fixed_exact_versions(monkeypatch: Any) -> None:
    owner = worker("media-owner")
    assignment, variants = assignment_with_media(owner=owner)
    unbound = media_variant(
        asset=assignment.task.asset,
        role=MediaVariant.Role.COMPRESSED,
        suffix="unbound",
        version="cos-unbound-v1",
    )
    cos = RecordingCosClient()
    monkeypatch.setattr("media.catalog.configured_cos_client", lambda: (cos, "private-bucket"))

    response = logged_in(owner).get(f"/api/worker/assignments/{assignment.assignment_id}/media")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")
    payload = response.json()
    assert payload["assignment_id"] == str(assignment.assignment_id)
    assert [item["role"] for item in payload["variants"]] == [
        MediaVariant.Role.COMPRESSED,
        MediaVariant.Role.HIGH_RESOLUTION,
    ]
    assert {item["media_variant_id"] for item in payload["variants"]} == {
        str(variant.media_variant_id) for variant in variants
    }
    assert str(unbound.media_variant_id) not in str(payload)
    assert "source_key" not in str(payload)
    assert [call["VersionId"] for call in cos.head_calls] == [
        "cos-compressed-v3",
        "cos-high-v7",
    ]
    assert [call["Params"] for call in cos.sign_calls] == [
        {"versionId": "cos-compressed-v3"},
        {"versionId": "cos-high-v7"},
    ]
    assert all(call["Bucket"] == "private-bucket" for call in cos.sign_calls)
    assert all(call["Method"] == "GET" for call in cos.sign_calls)
    assert all(call["Expired"] <= 300 for call in cos.sign_calls)
    assert response.headers["Cache-Control"] == "private, no-store"


def test_pap_mid_sc_008_foreign_and_missing_assignments_are_indistinguishable(
    monkeypatch: Any,
) -> None:
    owner = worker("media-object-owner")
    foreign_worker = worker("media-foreign-worker")
    assignment, _variants = assignment_with_media(owner=owner, suffix="private")
    cos = RecordingCosClient()
    monkeypatch.setattr("media.catalog.configured_cos_client", lambda: (cos, "private-bucket"))
    client = logged_in(foreign_worker)

    foreign = client.get(f"/api/worker/assignments/{assignment.assignment_id}/media")
    missing = client.get(f"/api/worker/assignments/{uuid4()}/media")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"error": {"code": "resource_not_found"}}
    assert cos.head_calls == cos.sign_calls == []


def test_pap_mid_sc_009_reloads_short_lived_urls_without_changing_versions(
    monkeypatch: Any,
) -> None:
    owner = worker("media-renewal-owner")
    assignment, _variants = assignment_with_media(owner=owner, suffix="renewal")
    cos = RecordingCosClient()
    monkeypatch.setattr("media.catalog.configured_cos_client", lambda: (cos, "private-bucket"))
    client = logged_in(owner)

    first = client.get(f"/api/worker/assignments/{assignment.assignment_id}/media")
    second = client.get(f"/api/worker/assignments/{assignment.assignment_id}/media")

    assert first.status_code == second.status_code == 200
    assert [item["url"] for item in first.json()["variants"]] != [
        item["url"] for item in second.json()["variants"]
    ]
    assert [call["Params"] for call in cos.sign_calls] == [
        {"versionId": "cos-compressed-v3"},
        {"versionId": "cos-high-v7"},
        {"versionId": "cos-compressed-v3"},
        {"versionId": "cos-high-v7"},
    ]


def test_pap_mid_sc_011_integrity_failure_keeps_the_equivalent_variant(
    monkeypatch: Any,
) -> None:
    owner = worker("media-partial-owner")
    assignment, variants = assignment_with_media(owner=owner, suffix="partial")
    by_key = {variant.source_key: variant for variant in variants}

    class PartlyMismatchedCatalog:
        def get_candidate(self, *, source_key: str) -> MediaCatalogCandidate:
            variant = by_key[source_key]
            return candidate_for(
                variant,
                version="unexpected-version"
                if variant.role == MediaVariant.Role.HIGH_RESOLUTION
                else None,
            )

    monkeypatch.setattr("work.media_workflows.get_cos_catalog", PartlyMismatchedCatalog)

    response = logged_in(owner).get(f"/api/worker/assignments/{assignment.assignment_id}/media")

    assert response.status_code == 200
    assert [item["role"] for item in response.json()["variants"]] == [MediaVariant.Role.COMPRESSED]
    assert response.json()["unavailable_roles"] == [MediaVariant.Role.HIGH_RESOLUTION]
    assert "unexpected-version" not in str(response.json())


def test_pap_mid_sc_012_all_permanently_missing_variants_are_unavailable(
    monkeypatch: Any,
) -> None:
    owner = worker("media-missing-owner")
    assignment, _variants = assignment_with_media(owner=owner, suffix="missing")

    class MissingCatalog:
        def get_candidate(self, *, source_key: str) -> MediaCatalogCandidate:
            raise MediaCandidateNotFound(source_key)

    monkeypatch.setattr("work.media_workflows.get_cos_catalog", MissingCatalog)

    response = logged_in(owner).get(f"/api/worker/assignments/{assignment.assignment_id}/media")

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "image_unavailable"}}
    assert OperationalIssue.objects.get(assignment=assignment).error_code == "image_unavailable"


def test_terminated_assignment_cannot_renew_media_credentials(monkeypatch: Any) -> None:
    owner = worker("media-terminal-owner")
    assignment, _variants = assignment_with_media(owner=owner, suffix="terminal")
    cancel_task(task_id=assignment.task_id, reason="wrong media")
    cos = RecordingCosClient()
    monkeypatch.setattr("media.catalog.configured_cos_client", lambda: (cos, "private-bucket"))

    response = logged_in(owner).get(f"/api/worker/assignments/{assignment.assignment_id}/media")

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "assignment_media_unavailable"}}
    assert cos.head_calls == cos.sign_calls == []


def test_pap_mid_sc_014_worker_cannot_upload_replacement_media() -> None:
    owner = worker("media-upload-owner")
    assignment, _variants = assignment_with_media(owner=owner, suffix="upload")
    counts_before = (
        Asset.objects.count(),
        MediaVariant.objects.count(),
        assignment.task.allowed_media_variants.count(),
    )

    response = logged_in(owner).post(
        f"/api/worker/assignments/{assignment.assignment_id}/media",
        data=b"not-an-approved-panorama",
        content_type="image/jpeg",
    )

    assignment.task.refresh_from_db()
    assert response.status_code == 405
    assert (
        Asset.objects.count(),
        MediaVariant.objects.count(),
        assignment.task.allowed_media_variants.count(),
    ) == counts_before
