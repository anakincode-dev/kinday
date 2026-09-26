"""Границы слоёв: core — чистый Python, роутеры не знают SQL.

Правило из SPEC.md 5.1: зависимости направлены внутрь, ядро о внешних слоях
не знает. Тест разбирает AST модулей core, а не просто ищет подстроки, чтобы
не зависеть от форматирования импортов. Учитываются относительные импорты
(`from . import x`) и импорт подмодуля через пакет (`from kinday import storage`).

Вторая граница — обратная: handler'ы в `telegram/` переводят апдейты в вызовы
сценариев ядра и форматируют ответы, поэтому хранилище им недоступно. Сборка
зависимостей (`telegram/bot.py`) — единственное исключение: где-то репозитории
SQLite всё равно приходится подставлять в порты ядра.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src"
CORE_DIR = SRC_DIR / "kinday" / "core"
TELEGRAM_DIR = SRC_DIR / "kinday" / "telegram"

FORBIDDEN_ROOTS = {
    "aiogram",
    "apscheduler",
    "sqlite3",
    "kinday.storage",
    "kinday.scheduler",
    "kinday.telegram",
}

# Чего не знают handler'ы: ни SQL напрямую, ни репозиториев SQLite, ни
# планировщика. Тик — сосед по внешнему слою, а не слой под роутерами: отправкой
# распоряжается он сам, и вызов оттуда обошёл бы всю логику трёх шагов (SPEC 5.5).
FORBIDDEN_FOR_TELEGRAM = {"sqlite3", "kinday.storage", "kinday.scheduler"}

# Сборка зависимостей: единственное место в слое, которое знает и aiogram, и SQLite.
# Путь, а не имя файла: иначе любой будущий `telegram/что-то/bot.py` тоже выпал бы
# из проверки, хотя сборкой зависимостей не является.
TELEGRAM_WIRING = {"bot.py"}


def _module_name(path: Path) -> str:
    """Полный dotted-путь модуля, например src/kinday/core/relations.py -> kinday.core.relations."""
    parts = list(path.relative_to(SRC_DIR).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_name(path: Path) -> str:
    """__package__ модуля: для __init__.py это сам модуль, иначе — его родитель."""
    module = _module_name(path)
    if path.stem == "__init__":
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def _resolve_import_from(node: ast.ImportFrom, path: Path) -> set[str]:
    """Полные dotted-имена, которые импортирует `from ... import ...`, включая алиасы.

    Алиасы добавляются отдельно (module + "." + alias), чтобы поймать
    `from kinday import storage`, где сам модуль ("kinday") не запрещён,
    а подмодуль ("kinday.storage") — запрещён.
    """
    if node.level == 0:
        base = node.module or ""
    else:
        package_parts = _package_name(path).split(".") if _package_name(path) else []
        strip = node.level - 1
        base_parts = package_parts[: len(package_parts) - strip] if strip else package_parts
        base = ".".join(part for part in [*base_parts, node.module] if part)

    names = {base} if base else set()
    for alias in node.names:
        names.add(f"{base}.{alias.name}" if base else alias.name)
    return names


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(_resolve_import_from(node, path))
    return names


def _is_forbidden(module_name: str) -> str | None:
    for forbidden in FORBIDDEN_ROOTS:
        if module_name == forbidden or module_name.startswith(forbidden + "."):
            return forbidden
    return None


def test_core_does_not_import_outer_layers() -> None:
    paths = sorted(CORE_DIR.rglob("*.py"))
    assert paths, f"В {CORE_DIR} не найдено ни одного файла .py — проверка ничего не проверяет"

    violations: list[str] = []
    for path in paths:
        for module_name in _imported_names(path):
            forbidden = _is_forbidden(module_name)
            if forbidden is not None:
                violations.append(f"{path.relative_to(CORE_DIR)} импортирует {module_name}")

    assert not violations, "core импортирует внешние слои:\n" + "\n".join(violations)


def test_telegram_handlers_do_not_touch_storage() -> None:
    """Роутеры не работают с базой: только вызовы сценариев ядра и форматирование ответов.

    Доменные правила глазами теста не увидеть, а вот попытку сходить в базу
    мимо ядра — вполне: она начинается с импорта `sqlite3` или `kinday.storage`.
    """
    paths = sorted(
        path
        for path in TELEGRAM_DIR.rglob("*.py")
        if str(path.relative_to(TELEGRAM_DIR)) not in TELEGRAM_WIRING
    )
    assert paths, f"В {TELEGRAM_DIR} не найдено ни одного файла .py — проверка ничего не проверяет"

    violations: list[str] = []
    for path in paths:
        for module_name in _imported_names(path):
            for forbidden in FORBIDDEN_FOR_TELEGRAM:
                if module_name == forbidden or module_name.startswith(forbidden + "."):
                    violations.append(f"{path.relative_to(TELEGRAM_DIR)} импортирует {module_name}")

    assert not violations, "слой telegram лезет в хранилище мимо ядра:\n" + "\n".join(violations)
