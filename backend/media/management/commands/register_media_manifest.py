from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from media.catalog import CosCatalogUnavailable, configured_cos_client
from media.manifest import (
    MediaManifestConflict,
    MediaManifestInvalid,
    MediaManifestUnavailable,
    register_media_manifest,
)


class Command(BaseCommand):
    help = "Register immutable COS object versions from a trusted uploader manifest."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("manifest", type=Path)

    def handle(self, *_args: Any, **options: Any) -> None:
        manifest_path = cast(Path, options["manifest"])
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            client, bucket = configured_cos_client()
            registrations = register_media_manifest(
                payload=payload,
                client=client,
                bucket=bucket,
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CommandError("The media manifest could not be read as JSON.") from error
        except (
            CosCatalogUnavailable,
            MediaManifestConflict,
            MediaManifestInvalid,
            MediaManifestUnavailable,
        ) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(f"Registered {len(registrations)} immutable COS object version(s).")
        )
