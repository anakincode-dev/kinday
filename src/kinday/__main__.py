"""Точка входа: собирает зависимости и поднимает бота с планировщиком."""

from __future__ import annotations

from kinday.config import Settings


def main() -> None:
    Settings.from_env()
    raise NotImplementedError("сборка зависимостей и запуск бота ещё не реализованы")


if __name__ == "__main__":
    main()
