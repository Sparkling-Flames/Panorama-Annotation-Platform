from __future__ import annotations

import os
import socket
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from work.batch_exports import process_next_batch_export
from work.jobs import process_next_analysis_job
from work.metric_snapshots import process_next_metric_snapshot


class Command(BaseCommand):
    help = "Continuously process each registered PostgreSQL job queue."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", default=1.0, type=float)
        parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        poll_seconds = options["poll_seconds"]
        if poll_seconds <= 0:
            raise CommandError("--poll-seconds must be positive")

        while True:
            close_old_connections()
            worker_id = options["worker_id"]
            processed = sum(
                item is not None
                for item in (
                    process_next_analysis_job(worker_id=worker_id),
                    process_next_metric_snapshot(worker_id=worker_id),
                    process_next_batch_export(worker_id=worker_id),
                )
            )
            if options["once"]:
                self.stdout.write(f"processed={processed}")
                return
            if processed == 0:
                time.sleep(poll_seconds)
