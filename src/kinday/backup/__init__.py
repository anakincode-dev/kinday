"""Модуль бэкапа базы данных.

Использует sqlite3 Connection.backup для создания согласованного слепка
на работающей базе с включённым WAL.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path


class BackupManager:
    """Управляет бэкапами SQLite базы.

    Создаёт бэкапы в указанную директорию, хранит последние 14 копий,
    удаляя более старые.
    """

    def __init__(self, db_path: Path, backup_dir: Path) -> None:
        """Инициализирует менеджер бэкапов.

        Args:
            db_path: Путь к основной базе данных.
            backup_dir: Директория для хранения бэкапов.
        """
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.max_backups = 14

    def create_backup(self) -> Path:
        """Создаёт новый бэкап базы данных.

        Returns:
            Путь к созданному файлу бэкапа.
        """
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Имя файла: kinday-YYYYMMDD-HHMMSS-NNN.db
        # Добавляем счётчик для разрешения коллизий при быстром создании
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for attempt in range(100):
            backup_name = f"kinday-{timestamp}-{attempt:03d}.db"
            backup_path = self.backup_dir / backup_name
            if not backup_path.exists():
                break
        else:
            # Если всё занято, используем уникальный идентификатор
            backup_name = f"kinday-{timestamp}-{os.getpid()}-{id(self):08x}.db"
            backup_path = self.backup_dir / backup_name

        # Создаём бэкап через sqlite3
        source = sqlite3.connect(str(self.db_path))
        target = sqlite3.connect(str(backup_path))

        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        # Проводим ротацию
        self._rotate()

        return backup_path

    def _rotate(self) -> None:
        """Удаляет старые бэкапы, оставляя только последние 14."""
        backup_files = sorted(self.backup_dir.glob("kinday-*.db"))

        # Удаляем самые старые, если их больше 14
        while len(backup_files) > self.max_backups:
            oldest = backup_files.pop(0)
            oldest.unlink()
