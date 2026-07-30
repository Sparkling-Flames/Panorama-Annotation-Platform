import os
from typing import Any

_TEST_SIGNING_KEY = "django-insecure-panorama-test-only"
os.environ["DJANGO_SECRET_KEY"] = _TEST_SIGNING_KEY

from .settings import *  # noqa: E402,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("PANORAMA_TEST_SQLITE_PATH", ":memory:"),
    }
}

ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

E2E_MEDIA_MANIFEST: dict[str, Any] = {
    "objects": [
        {
            "content_length": 8_388_608,
            "content_sha256": "a" * 64,
            "crc64ecma": "12345678901234567890",
            "format": "png",
            "height": 2048,
            "source_key": "incoming/e2e/high.png",
            "version_id": "e2e-high-v1",
            "width": 4096,
        },
        {
            "content_length": 1_048_576,
            "content_sha256": "b" * 64,
            "crc64ecma": "1234567890",
            "format": "jpeg",
            "height": 1024,
            "source_key": "incoming/e2e/compressed.jpg",
            "version_id": "e2e-compressed-v1",
            "width": 2048,
        },
    ],
    "schema_version": 1,
}


class E2ECosClient:
    def get_bucket_versioning(self, **_kwargs: object) -> dict[str, str]:
        return {"Status": "Enabled"}

    def head_object(self, **kwargs: object) -> dict[str, str]:
        entry = next(
            entry
            for entry in E2E_MEDIA_MANIFEST["objects"]
            if entry["source_key"] == kwargs["Key"] and entry["version_id"] == kwargs["VersionId"]
        )
        content_type = "image/png" if entry["format"] == "png" else "image/jpeg"
        return {
            "Content-Length": str(entry["content_length"]),
            "Content-Type": content_type,
            "x-cos-hash-crc64ecma": str(entry["crc64ecma"]),
            "x-cos-meta-content-sha256": str(entry["content_sha256"]),
            "x-cos-meta-height": str(entry["height"]),
            "x-cos-meta-width": str(entry["width"]),
            "x-cos-version-id": str(entry["version_id"]),
        }

    def get_presigned_url(self, **_kwargs: object) -> str:
        return "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="


def e2e_media_catalog() -> Any:
    from media.catalog import TencentCosCatalog

    return TencentCosCatalog(
        client=E2ECosClient(),
        bucket="e2e-controlled-cos",
        preview_url_seconds=300,
    )


if os.environ.get("PANORAMA_E2E_CONTROLLED_COS") == "1":
    MEDIA_CATALOG_FACTORY = e2e_media_catalog
