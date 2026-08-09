from pathlib import PurePosixPath


def media_format(*, content_type: str | None, source_key: str) -> str | None:
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
