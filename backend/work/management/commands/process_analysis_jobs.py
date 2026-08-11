from __future__ import annotations

import os
import socket

from django.core.management.base import BaseCommand, CommandError

from work.jobs import process_next_analysis_job
from work.models import AnalysisJob


class Command(BaseCommand):
    help = "Process queued analysis jobs from the PostgreSQL outbox."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("--max-jobs", default=100, type=int)
        parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        max_jobs = options["max_jobs"]
        if max_jobs < 1:
            raise CommandError("--max-jobs must be positive")
        processed = 0
        failures = 0
        for _ in range(max_jobs):
            job = process_next_analysis_job(worker_id=options["worker_id"])
            if job is None:
                break
            processed += 1
            failures += job.status == AnalysisJob.Status.FAILED
        self.stdout.write(f"processed={processed} failures={failures}")
