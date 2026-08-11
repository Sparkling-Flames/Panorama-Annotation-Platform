from __future__ import annotations

import json
from hashlib import sha256

PLATFORM_RELEASE_ID_V1 = "panorama-platform-v1"
ANNOTATION_VIEWER_V1 = "annotation-viewer-v1"
ANNOTATION_INTERACTION_V1 = "annotation-interaction-v1"


def json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode()).hexdigest()
