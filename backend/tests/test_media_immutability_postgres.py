from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction
from media.ingestion import MediaImportPlan, MediaVariantCandidate, preview_import, publish_import
from media.models import Asset, MediaObjectRegistration, MediaVariant

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL trigger integration test",
    ),
]


@pytest.fixture
def published_variant() -> MediaVariant:
    asset = Asset.objects.create(source_key="panoramas/postgres-immutable")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="incoming/postgres-immutable/high.png",
        object_version="cos-version-postgres-immutable-001",
        content_sha256="a" * 64,
        content_length=8_388_608,
        content_crc64ecma="12345678901234567890",
        width=4096,
        height=2048,
        format=MediaVariant.Format.PNG,
        role=MediaVariant.Role.HIGH_RESOLUTION,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    return variant


@pytest.mark.parametrize(
    "changes",
    [
        {"content_sha256": "b" * 64},
        {"object_version": "cos-version-postgres-immutable-002"},
        {"content_length": 1},
        {"content_crc64ecma": "1"},
        {"source_key": "incoming/postgres-immutable/replaced.png"},
        {"role": MediaVariant.Role.COMPRESSED},
        {"format": MediaVariant.Format.JPEG},
        {"width": 2048},
        {"height": 1024},
        {"coordinate_mapping": "changed"},
        {"published_at": None},
    ],
)
def test_postgresql_rejects_queryset_updates_to_published_variants(
    published_variant: MediaVariant,
    changes: dict[str, Any],
) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        MediaVariant.objects.filter(pk=published_variant.pk).update(**changes)


def test_postgresql_rejects_raw_sql_updates_to_published_variants(
    published_variant: MediaVariant,
) -> None:
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE media_mediavariant SET content_sha256 = %s WHERE media_variant_id = %s",
            ["b" * 64, published_variant.pk],
        )


def test_postgresql_rejects_deleting_published_variants(
    published_variant: MediaVariant,
) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        MediaVariant.objects.filter(pk=published_variant.pk).delete()


def test_postgresql_concurrent_identical_imports_are_idempotent() -> None:
    plan = MediaImportPlan(
        asset_source_key="panoramas/postgres-concurrent",
        variants=(
            MediaVariantCandidate(
                source_key="incoming/postgres-concurrent/high.png",
                object_version="cos-version-postgres-high-001",
                content_sha256="a" * 64,
                content_length=8_388_608,
                content_crc64ecma="12345678901234567890",
                width=4096,
                height=2048,
                format="png",
                role="high_resolution",
            ),
            MediaVariantCandidate(
                source_key="incoming/postgres-concurrent/compressed.jpg",
                object_version="cos-version-postgres-compressed-001",
                content_sha256="b" * 64,
                content_length=1_048_576,
                content_crc64ecma="1234567890",
                width=2048,
                height=1024,
                format="jpeg",
                role="compressed",
            ),
        ),
    )

    def publish() -> tuple[object, tuple[object, ...]]:
        close_old_connections()
        try:
            result = publish_import(preview_import(plan))
            return result.asset.asset_id, tuple(
                variant.media_variant_id for variant in result.media_variants
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: publish(), range(2)))

    assert results[0] == results[1]
    assert Asset.objects.count() == 1
    assert MediaVariant.objects.count() == 2


@pytest.fixture
def registered_object() -> MediaObjectRegistration:
    return MediaObjectRegistration.objects.create(
        source_key="incoming/postgres-registered/high.png",
        version_id="cos-version-postgres-registered-001",
        content_sha256="c" * 64,
        content_length=8_388_608,
        crc64ecma="12345678901234567890",
        width=4096,
        height=2048,
        format=MediaVariant.Format.PNG,
        manifest_sha256="d" * 64,
    )


def test_postgresql_rejects_registered_object_updates(
    registered_object: MediaObjectRegistration,
) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        MediaObjectRegistration.objects.filter(pk=registered_object.pk).update(
            content_sha256="e" * 64
        )


def test_postgresql_rejects_registered_object_deletion(
    registered_object: MediaObjectRegistration,
) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        MediaObjectRegistration.objects.filter(pk=registered_object.pk).delete()
