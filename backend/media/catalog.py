from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

from django.conf import settings
from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from .models import MediaObjectRegistration


class CosCatalogUnavailable(Exception):
    pass


class MediaCandidateNotFound(Exception):
    pass


class MediaCandidateInvalid(Exception):
    pass


class MediaCandidateIntegrityConflict(Exception):
    pass


@dataclass(frozen=True)
class MediaCatalogCandidate:
    source_key: str
    version_id: str
    content_sha256: str
    content_length: int
    crc64ecma: str
    width: int
    height: int
    format: str
    preview_url: str


@dataclass(frozen=True)
class MediaCatalogPage:
    candidates: tuple[MediaCatalogCandidate, ...]
    next_marker: str | None


class MediaCatalog(Protocol):
    def list_candidates(self, *, prefix: str, marker: str | None) -> MediaCatalogPage: ...

    def get_candidate(self, *, source_key: str) -> MediaCatalogCandidate: ...


class TencentCosCatalog:
    def __init__(self, *, client: Any, bucket: str, preview_url_seconds: int) -> None:
        self._client = client
        self._bucket = bucket
        self._preview_url_seconds = preview_url_seconds

    @classmethod
    def from_settings(cls) -> TencentCosCatalog:
        client, bucket = configured_cos_client()
        return cls(
            client=client,
            bucket=bucket,
            preview_url_seconds=settings.COS_ADMIN_PREVIEW_URL_SECONDS,
        )

    def list_candidates(self, *, prefix: str, marker: str | None) -> MediaCatalogPage:
        registrations = MediaObjectRegistration.objects.filter(source_key__startswith=prefix)
        if marker is not None:
            registrations = registrations.filter(source_key__gt=marker)
        page = list(registrations.order_by("source_key")[:101])
        candidates: list[MediaCatalogCandidate] = []
        for registration in page[:100]:
            try:
                candidates.append(self._candidate_from_registration(registration))
            except (MediaCandidateInvalid, MediaCandidateNotFound):
                continue
        return MediaCatalogPage(
            candidates=tuple(candidates),
            next_marker=page[99].source_key if len(page) > 100 else None,
        )

    def get_candidate(self, *, source_key: str) -> MediaCatalogCandidate:
        if not source_key:
            raise MediaCandidateNotFound(source_key)
        registration = MediaObjectRegistration.objects.filter(source_key=source_key).first()
        if registration is None:
            raise MediaCandidateNotFound(source_key)
        return self._candidate_from_registration(registration)

    def _candidate_from_registration(
        self,
        registration: MediaObjectRegistration,
    ) -> MediaCatalogCandidate:
        try:
            headers = self._client.head_object(
                Bucket=self._bucket,
                Key=registration.source_key,
                VersionId=registration.version_id,
            )
        except CosServiceError as error:
            if error.get_status_code() == 404:
                raise MediaCandidateNotFound(registration.source_key) from error
            raise CosCatalogUnavailable("COS object metadata is unavailable.") from error
        except CosClientError as error:
            raise CosCatalogUnavailable("COS object metadata is unavailable.") from error
        if not isinstance(headers, dict):
            raise MediaCandidateInvalid("COS returned invalid object metadata.")

        normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
        expected_headers = {
            "content-length": str(registration.content_length),
            "x-cos-hash-crc64ecma": registration.crc64ecma,
            "x-cos-version-id": registration.version_id,
        }
        if any(normalized_headers.get(name) != value for name, value in expected_headers.items()):
            raise MediaCandidateIntegrityConflict(
                "COS object integrity metadata differs from its trusted registration."
            )

        optional_headers = {
            "x-cos-meta-content-sha256": registration.content_sha256,
            "x-cos-meta-height": str(registration.height),
            "x-cos-meta-width": str(registration.width),
        }
        if any(
            name in normalized_headers and normalized_headers[name] != value
            for name, value in optional_headers.items()
        ):
            raise MediaCandidateIntegrityConflict(
                "COS custom metadata differs from its trusted registration."
            )

        format_name = _media_format(
            content_type=normalized_headers.get("content-type"),
            source_key=registration.source_key,
        )
        if format_name != registration.format:
            raise MediaCandidateIntegrityConflict(
                "COS media format differs from its trusted registration."
            )

        try:
            preview_url = self._client.get_presigned_url(
                Bucket=self._bucket,
                Key=registration.source_key,
                Method="GET",
                Expired=self._preview_url_seconds,
                Params={"versionId": registration.version_id},
            )
        except (CosClientError, CosServiceError) as error:
            raise CosCatalogUnavailable("COS preview signing is unavailable.") from error
        if not isinstance(preview_url, str):
            raise MediaCandidateInvalid("COS returned an invalid preview URL.")
        return MediaCatalogCandidate(
            source_key=registration.source_key,
            version_id=registration.version_id,
            content_sha256=registration.content_sha256,
            content_length=registration.content_length,
            crc64ecma=registration.crc64ecma,
            width=registration.width,
            height=registration.height,
            format=registration.format,
            preview_url=preview_url,
        )


def get_cos_catalog() -> MediaCatalog:
    factory = getattr(settings, "MEDIA_CATALOG_FACTORY", None)
    if factory is not None:
        return cast(Callable[[], MediaCatalog], factory)()
    return TencentCosCatalog.from_settings()


def configured_cos_client() -> tuple[Any, str]:
    required_values = {
        "bucket": settings.COS_BUCKET,
        "region": settings.COS_REGION,
        "secret_id": settings.COS_SECRET_ID,
        "secret_key": settings.COS_SECRET_KEY,
    }
    if any(not isinstance(value, str) or not value for value in required_values.values()):
        raise CosCatalogUnavailable("COS is not configured for media import.")

    config_kwargs: dict[str, str] = {
        "Region": settings.COS_REGION,
        "SecretId": settings.COS_SECRET_ID,
        "SecretKey": settings.COS_SECRET_KEY,
        "Scheme": "https",
    }
    if settings.COS_SESSION_TOKEN:
        config_kwargs["Token"] = settings.COS_SESSION_TOKEN
    return CosS3Client(CosConfig(**config_kwargs)), settings.COS_BUCKET


def _media_format(*, content_type: str | None, source_key: str) -> str | None:
    normalized_content_type = (
        content_type.split(";", maxsplit=1)[0].strip().lower() if content_type else ""
    )
    if normalized_content_type == "image/png":
        return "png"
    if normalized_content_type in {"image/jpeg", "image/jpg"}:
        return "jpeg"

    suffix = PurePosixPath(source_key).suffix.lower()
    if suffix == ".png":
        return "png"
    if suffix in {".jpeg", ".jpg"}:
        return "jpeg"
    return None
