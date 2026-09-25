"""core — чистый Python: не импортирует Telegram/SQLite/APScheduler и внешние слои.

Правило из SPEC.md 5.1: зависимости направлены внутрь, ядро о внешних слоях
не знает. Тест разбирает AST модулей core, а не просто ищет подстроки, чтобы
не зависеть от форматирования импортов.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent / "src" / "kinday" / "core"

FORBIDDEN_ROOTS = {
    "aiogram",
    "apscheduler",
    "sqlite3",
    "kinday.storage",
    "kinday.scheduler",
    "kinday.telegram",
}


def _imported_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _is_forbidden(module_name: str) -> str | None:
    for forbidden in FORBIDDEN_ROOTS:
        if module_name == forbidden or module_name.startswith(forbidden + "."):
            return forbidden
    return None


def test_core_does_not_import_outer_layers() -> None:
    violations: list[str] = []
    for path in sorted(CORE_DIR.rglob("*.py")):
        imported = _imported_names(path.read_text(encoding="utf-8"))
        for module_name in imported:
            forbidden = _is_forbidden(module_name)
            if forbidden is not None:
                violations.append(f"{path.relative_to(CORE_DIR)} импортирует {module_name}")

    assert not violations, "core импортирует внешние слои:\n" + "\n".join(violations)
