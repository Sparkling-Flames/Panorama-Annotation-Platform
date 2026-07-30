from __future__ import annotations

import importlib
from typing import Any

import pytest

pytestmark = pytest.mark.django_db

SOURCE_KEY = "incoming/warehouse-001/high.png"
VERSION_ID = "cos-version-high-001"
CONTENT_SHA256 = "a" * 64
CONTENT_LENGTH = 8_388_608
CRC64ECMA = "12345678901234567890"


def catalog_module() -> Any:
    return importlib.import_module("media.catalog")


def register_candidate(*, source_key: str = SOURCE_KEY) -> Any:
    models = importlib.import_module("media.models")
    return models.MediaObjectRegistration.objects.create(
        source_key=source_key,
        version_id=VERSION_ID,
        content_sha256=CONTENT_SHA256,
        content_length=CONTENT_LENGTH,
        crc64ecma=CRC64ECMA,
        width=4096,
        height=2048,
        format="png",
        manifest_sha256="f" * 64,
    )


def test_tencent_cos_catalog_reads_metadata_and_returns_signed_preview_urls() -> None:
    module = catalog_module()

    class FakeCosClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def head_object(self, **kwargs: object) -> dict[str, str]:
            self.calls.append(("head_object", kwargs))
            return {
                "Content-Length": str(CONTENT_LENGTH),
                "Content-Type": "image/png",
                "x-cos-hash-crc64ecma": CRC64ECMA,
                "x-cos-meta-content-sha256": CONTENT_SHA256,
                "x-cos-meta-height": "2048",
                "x-cos-meta-width": "4096",
                "x-cos-version-id": VERSION_ID,
            }

        def get_presigned_url(self, **kwargs: object) -> str:
            self.calls.append(("get_presigned_url", kwargs))
            return f"https://cos.example.test/{kwargs['Key']}?signature=redacted"

    register_candidate()
    client = FakeCosClient()
    catalog = module.TencentCosCatalog(
        client=client,
        bucket="annotation-1250000000",
        preview_url_seconds=300,
    )

    page = catalog.list_candidates(prefix="incoming/warehouse-001", marker=None)

    assert page.next_marker is None
    assert page.candidates == (
        module.MediaCatalogCandidate(
            source_key=SOURCE_KEY,
            version_id=VERSION_ID,
            content_sha256=CONTENT_SHA256,
            content_length=CONTENT_LENGTH,
            crc64ecma=CRC64ECMA,
            width=4096,
            height=2048,
            format="png",
            preview_url=f"https://cos.example.test/{SOURCE_KEY}?signature=redacted",
        ),
    )
    assert client.calls == [
        (
            "head_object",
            {
                "Bucket": "annotation-1250000000",
                "Key": SOURCE_KEY,
                "VersionId": VERSION_ID,
            },
        ),
        (
            "get_presigned_url",
            {
                "Bucket": "annotation-1250000000",
                "Expired": 300,
                "Key": SOURCE_KEY,
                "Method": "GET",
                "Params": {"versionId": VERSION_ID},
            },
        ),
    ]


@pytest.mark.parametrize("content_sha256", ["not-a-hash", "A" * 64])
def test_tencent_cos_catalog_rejects_invalid_sha256_metadata(content_sha256: str) -> None:
    module = catalog_module()
    register_candidate()

    class FakeCosClient:
        def head_object(self, **_kwargs: object) -> dict[str, str]:
            return {
                "Content-Length": str(CONTENT_LENGTH),
                "Content-Type": "image/png",
                "x-cos-hash-crc64ecma": CRC64ECMA,
                "x-cos-meta-content-sha256": content_sha256,
                "x-cos-meta-height": "2048",
                "x-cos-meta-width": "4096",
                "x-cos-version-id": VERSION_ID,
            }

        def get_presigned_url(self, **_kwargs: object) -> str:
            raise AssertionError("invalid candidates must not receive signed URLs")

    catalog = module.TencentCosCatalog(
        client=FakeCosClient(),
        bucket="annotation-1250000000",
        preview_url_seconds=300,
    )

    with pytest.raises(module.MediaCandidateIntegrityConflict):
        catalog.get_candidate(source_key=SOURCE_KEY)


def test_tencent_cos_catalog_maps_only_cos_404_to_candidate_not_found() -> None:
    module = catalog_module()
    register_candidate()

    class MissingCosClient:
        def head_object(self, **_kwargs: object) -> dict[str, str]:
            raise module.CosServiceError("HEAD", {"code": "NoSuchKey"}, 404)

    catalog = module.TencentCosCatalog(
        client=MissingCosClient(),
        bucket="annotation-1250000000",
        preview_url_seconds=300,
    )

    with pytest.raises(module.MediaCandidateNotFound):
        catalog.get_candidate(source_key=SOURCE_KEY)


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            lambda module: module.CosServiceError("HEAD", {"code": "InternalError"}, 503),
            id="service-error",
        ),
        pytest.param(lambda module: module.CosClientError("timeout"), id="client-error"),
    ],
)
def test_tencent_cos_catalog_maps_temporary_failures_to_unavailable(error: Any) -> None:
    module = catalog_module()
    register_candidate()

    class UnavailableCosClient:
        def head_object(self, **_kwargs: object) -> dict[str, str]:
            raise error(module)

    catalog = module.TencentCosCatalog(
        client=UnavailableCosClient(),
        bucket="annotation-1250000000",
        preview_url_seconds=300,
    )

    with pytest.raises(module.CosCatalogUnavailable):
        catalog.get_candidate(source_key=SOURCE_KEY)


def test_tencent_cos_catalog_skips_objects_deleted_during_listing() -> None:
    module = catalog_module()
    register_candidate(source_key="incoming/disappeared.png")

    class DisappearingCosClient:
        def head_object(self, **_kwargs: object) -> dict[str, str]:
            raise module.CosServiceError("HEAD", {"code": "NoSuchKey"}, 404)

    catalog = module.TencentCosCatalog(
        client=DisappearingCosClient(),
        bucket="annotation-1250000000",
        preview_url_seconds=300,
    )

    assert catalog.list_candidates(prefix="incoming", marker=None).candidates == ()


def test_tencent_cos_catalog_does_not_expose_unregistered_cos_objects() -> None:
    module = catalog_module()

    class CosClientThatMustNotBeCalled:
        def head_object(self, **_kwargs: object) -> dict[str, str]:
            raise AssertionError("unregistered objects must not reach COS")

    catalog = module.TencentCosCatalog(
        client=CosClientThatMustNotBeCalled(),
        bucket="annotation-1250000000",
        preview_url_seconds=300,
    )

    assert catalog.list_candidates(prefix="incoming", marker=None).candidates == ()
    with pytest.raises(module.MediaCandidateNotFound):
        catalog.get_candidate(source_key="incoming/unregistered.png")
