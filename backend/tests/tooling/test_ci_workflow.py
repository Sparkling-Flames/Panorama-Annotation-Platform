from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_ci_bandit_scans_all_backend_modules() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "python -m bandit -q -c pyproject.toml -r backend scripts" in workflow
    assert 'exclude_dirs = ["tests"]' in pyproject


def test_ci_runs_media_immutability_against_postgresql() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "services:" in workflow
    assert "postgres:" in workflow
    assert "backend/tests/test_media_immutability_postgres.py" in workflow
    assert "backend/tests/identity/test_audit_event_immutability_postgres.py" in workflow
    assert "backend/tests/test_task_lifecycle.py" in workflow
    assert "python backend/manage.py migrate work 0001 --noinput" in workflow
    assert 'DJANGO_SECRET_KEY: "django-insecure-ci-postgres-only"' in workflow
    assert "--ds=panorama_annotation.settings" in workflow
