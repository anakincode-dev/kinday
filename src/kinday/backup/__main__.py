"""Точка входа для запуска бэкапа через python -m kinday.backup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    """Создаёт бэкап и выводит путь к нему."""
    parser = argparse.ArgumentParser(description="Создать бэкап базы Kinday")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("/var/lib/kinday/kinday.db"),
        help="Путь к базе данных",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/var/lib/kinday/backups"),
        help="Директория для бэкапов",
    )
    args = parser.parse_args()

    from kinday.backup import BackupManager

    manager = BackupManager(args.db_path, args.backup_dir)
    backup_path = manager.create_backup()
    print(f"Бэкап создан: {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
