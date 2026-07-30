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
            "content_length": 109,
            "content_sha256": "1f395f818a59308471cd4998d65be7f2d8ea81ea76d2bb521194e0f2d5498b8e",
            "crc64ecma": "13918488817461282678",
            "format": "png",
            "height": 16,
            "source_key": "incoming/e2e/high.png",
            "version_id": "e2e-high-v1",
            "width": 32,
        },
        {
            "content_length": 214,
            "content_sha256": "2536b2aa8248cfec5b5165ccbd69bcbb0510b74547c13232926772a41fc2b26c",
            "crc64ecma": "6145541332926041938",
            "format": "jpeg",
            "height": 8,
            "source_key": "incoming/e2e/compressed.jpg",
            "version_id": "e2e-compressed-v1",
            "width": 16,
        },
    ],
    "schema_version": 1,
}

E2E_MEDIA_PREVIEW_URLS = {
    "incoming/e2e/high.png": (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAQCAIAAAD4YuoOAAAANElEQVR4nO3PMREAIAwEwYXBAFUM4F8jEr5Klyuu3wVc1fStueMV6PoIYiOIH0FsBPHtgg/rgQ2SFRRBtwAAAABJRU5ErkJggg=="
    ),
    "incoming/e2e/compressed.jpg": (
        "data:image/jpeg;base64,"
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/wAALCAAIABABAREA/8QAFQABAQAAAAAAAAAAAAAAAAAABgf/xAAjEAABAgUDBQAAAAAAAAAAAAACAREFIgQAAwcSIQYWIzNR/9oACAEBAAA/AAmm0C9Uny7X1ZG+wNOKyK4JYlmajoJX85orFyJJKKEbEjLs2vyl/wD/2Q=="
    ),
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

    def get_presigned_url(self, **kwargs: object) -> str:
        return E2E_MEDIA_PREVIEW_URLS[str(kwargs["Key"])]


def e2e_media_catalog() -> Any:
    from media.catalog import TencentCosCatalog

    return TencentCosCatalog(
        client=E2ECosClient(),
        bucket="e2e-controlled-cos",
        preview_url_seconds=300,
    )


if os.environ.get("PANORAMA_E2E_CONTROLLED_COS") == "1":
    MEDIA_CATALOG_FACTORY = e2e_media_catalog
