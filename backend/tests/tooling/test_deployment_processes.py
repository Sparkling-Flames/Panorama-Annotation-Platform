from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.django_db
def test_worker_process_polls_each_registered_queue_once() -> None:
    output = StringIO()

    with (
        patch("work.management.commands.run_worker.process_next_analysis_job") as analysis,
        patch("work.management.commands.run_worker.process_next_metric_snapshot") as metrics,
        patch("work.management.commands.run_worker.process_next_batch_export") as exports,
    ):
        analysis.return_value = object()
        metrics.return_value = None
        exports.return_value = object()
        call_command("run_worker", once=True, worker_id="deployment-test", stdout=output)

    analysis.assert_called_once_with(worker_id="deployment-test")
    metrics.assert_called_once_with(worker_id="deployment-test")
    exports.assert_called_once_with(worker_id="deployment-test")
    assert output.getvalue().strip() == "processed=2"


def test_production_process_and_rollback_contract_is_explicit() -> None:
    assert (PROJECT_ROOT / "Procfile").read_text(encoding="utf-8").splitlines() == [
        "release: python backend/manage.py migrate --noinput",
        (
            "web: gunicorn --chdir backend --bind 0.0.0.0:${PORT:-8000} "
            "panorama_annotation.wsgi:application"
        ),
        "worker: python backend/manage.py run_worker",
    ]
    assert "gunicorn==26.0.0" in (PROJECT_ROOT / "backend/requirements/base.in").read_text(
        encoding="utf-8"
    )
    assert "gunicorn==26.0.0" in (PROJECT_ROOT / "backend/requirements/base.lock").read_text(
        encoding="utf-8"
    )

    rollback = (PROJECT_ROOT / "deploy/README.md").read_text(encoding="utf-8")
    assert "重新部署上一不可变应用版本" in rollback
    assert "数据库保持向前迁移" in rollback
