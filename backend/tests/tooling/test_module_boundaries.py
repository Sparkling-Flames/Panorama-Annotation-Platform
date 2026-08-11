import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _imported_top_level_modules(directory: Path) -> set[str]:
    imported: set[str] = set()
    for path in directory.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", maxsplit=1)[0])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
    return imported


def test_media_domain_does_not_depend_on_work_orchestration() -> None:
    assert "work" not in _imported_top_level_modules(PROJECT_ROOT / "backend" / "media")
