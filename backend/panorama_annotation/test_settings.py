import json
import os
from typing import Any
from urllib.parse import quote

_TEST_SIGNING_KEY = "django-insecure-panorama-test-only"
os.environ["DJANGO_DEBUG"] = "true"
os.environ["DJANGO_SECRET_KEY"] = _TEST_SIGNING_KEY

from .settings import *  # noqa: E402,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("PANORAMA_TEST_SQLITE_PATH", ":memory:"),
        "OPTIONS": {"transaction_mode": "IMMEDIATE"},
    }
}

ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

_HIGH_RESOLUTION_OBJECT = {
    "content_length": 109,
    "content_sha256": "1f395f818a59308471cd4998d65be7f2d8ea81ea76d2bb521194e0f2d5498b8e",
    "crc64ecma": "13918488817461282678",
    "format": "png",
    "height": 16,
    "width": 32,
}
_COMPRESSED_OBJECT = {
    "content_length": 214,
    "content_sha256": "2536b2aa8248cfec5b5165ccbd69bcbb0510b74547c13232926772a41fc2b26c",
    "crc64ecma": "6145541332926041938",
    "format": "jpeg",
    "height": 8,
    "width": 16,
}


def _e2e_object(source_key: str, template: dict[str, Any]) -> dict[str, Any]:
    return {
        **template,
        "source_key": source_key,
        "version_id": f"e2e-{source_key.replace('/', '-')}-v1",
    }


_REGISTERED_MEDIA_OBJECTS = [
    _e2e_object("incoming/e2e/high.png", _HIGH_RESOLUTION_OBJECT),
    _e2e_object("incoming/e2e/compressed.jpg", _COMPRESSED_OBJECT),
    *(
        _e2e_object(f"incoming/e2e/{case}/high.png", _HIGH_RESOLUTION_OBJECT)
        for case in ("assignment", "cancel", "drift", "expire", "repeat", "review", "roles")
    ),
    *(
        _e2e_object(f"incoming/e2e/{case}/compressed.jpg", _COMPRESSED_OBJECT)
        for case in ("assignment", "cancel", "drift", "expire", "repeat", "review", "roles")
    ),
    *(
        _e2e_object(f"incoming/e2e/page/{index:03d}.png", _HIGH_RESOLUTION_OBJECT)
        for index in range(101)
    ),
]
_UNREGISTERED_MEDIA_OBJECT = _e2e_object(
    "incoming/e2e/unregistered/high.png",
    _HIGH_RESOLUTION_OBJECT,
)
E2E_COS_OBJECTS = {
    entry["source_key"]: entry for entry in [*_REGISTERED_MEDIA_OBJECTS, _UNREGISTERED_MEDIA_OBJECT]
}
E2E_MEDIA_MANIFEST: dict[str, Any] = {
    "objects": _REGISTERED_MEDIA_OBJECTS,
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
        entry = E2E_COS_OBJECTS[str(kwargs["Key"])]
        if entry["version_id"] != kwargs["VersionId"]:
            raise KeyError(kwargs["VersionId"])
        content_type = "image/png" if entry["format"] == "png" else "image/jpeg"
        headers = {
            "Content-Length": str(entry["content_length"]),
            "Content-Type": content_type,
            "x-cos-hash-crc64ecma": str(entry["crc64ecma"]),
            "x-cos-meta-content-sha256": str(entry["content_sha256"]),
            "x-cos-meta-height": str(entry["height"]),
            "x-cos-meta-width": str(entry["width"]),
            "x-cos-version-id": str(entry["version_id"]),
        }
        control_path = os.environ.get("PANORAMA_E2E_COS_CONTROL_FILE")
        if control_path:
            try:
                with open(control_path, encoding="utf-8") as control_file:
                    control = json.load(control_file)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                control = {}
            if control.get("drift_source_key") == entry["source_key"]:
                headers["Content-Length"] = str(entry["content_length"] + 1)
        return headers

    def get_presigned_url(self, **kwargs: object) -> str:
        entry = E2E_COS_OBJECTS[str(kwargs["Key"])]
        controlled_origin = os.environ.get("PANORAMA_E2E_COS_ORIGIN")
        if controlled_origin:
            params = kwargs["Params"]
            if not isinstance(params, dict):
                raise TypeError("Controlled COS signing requires query parameters")
            return (
                f"{controlled_origin}/{quote(str(kwargs['Key']), safe='/')}"
                f"?versionId={quote(str(params['versionId']))}&signature=e2e-controlled"
            )
        fallback_key = (
            "incoming/e2e/high.png" if entry["format"] == "png" else "incoming/e2e/compressed.jpg"
        )
        return E2E_MEDIA_PREVIEW_URLS.get(str(kwargs["Key"]), E2E_MEDIA_PREVIEW_URLS[fallback_key])


def e2e_media_catalog() -> Any:
    from media.catalog import TencentCosCatalog

    return TencentCosCatalog(
        client=E2ECosClient(),
        bucket="e2e-controlled-cos",
        preview_url_seconds=300,
    )


if os.environ.get("PANORAMA_E2E_CONTROLLED_COS") == "1":
    MEDIA_CATALOG_FACTORY = e2e_media_catalog
