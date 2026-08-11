from __future__ import annotations

import os
import socket

from django.core.management.base import BaseCommand, CommandError

from work.batch_exports import process_next_batch_export


class Command(BaseCommand):
    help = "Process queued batch export snapshots."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("--max-jobs", default=100, type=int)
        parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        if options["max_jobs"] < 1:
            raise CommandError("--max-jobs must be positive")
        processed = 0
        failures = 0
        for _ in range(options["max_jobs"]):
            snapshot = process_next_batch_export(worker_id=options["worker_id"])
            if snapshot is None:
                break
            processed += 1
            failures += snapshot.status == snapshot.Status.FAILED
        self.stdout.write(f"processed={processed} failures={failures}")
