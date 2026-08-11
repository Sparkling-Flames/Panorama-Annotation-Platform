from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest
from identity.http import request_json

from .catalog import MediaCatalogCandidate
from .ingestion import SHA256_PATTERN, MediaVariantCandidate


class ImportRequestError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def publication_request(request: HttpRequest) -> tuple[UUID, str]:
    payload = request_json(request)
    if payload is None or set(payload) != {"expected_plan_sha256", "preview_id"}:
        raise ImportRequestError("invalid_media_import_publication", 400)
    preview_id = payload.get("preview_id")
    expected_plan_sha256 = payload.get("expected_plan_sha256")
    if (
        not isinstance(preview_id, str)
        or not isinstance(expected_plan_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_plan_sha256) is None
    ):
        raise ImportRequestError("invalid_media_import_publication", 400)
    try:
        return UUID(preview_id), expected_plan_sha256
    except ValueError as error:
        raise ImportRequestError("invalid_media_import_publication", 400) from error


def variant_candidate(candidate: MediaCatalogCandidate, *, role: str) -> MediaVariantCandidate:
    return MediaVariantCandidate(
        source_key=candidate.source_key,
        object_version=candidate.version_id,
        content_sha256=candidate.content_sha256,
        content_length=candidate.content_length,
        content_crc64ecma=candidate.crc64ecma,
        width=candidate.width,
        height=candidate.height,
        format=candidate.format,
        role=role,
    )
