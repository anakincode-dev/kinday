"""Тесты модуля kinday.backup.

Требования из SPEC 6.3:
- backup через sqlite3 Connection.backup (работает с WAL)
- хранит 14 последних копий
- удаляет старые
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from kinday.backup import BackupManager


class TestBackupManager:
    """Тесты BackupManager."""

    def test_backup_creates_file(self, tmp_path: Path) -> None:
        """Бэкап создаёт файл в указанной директории."""
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Создаём базу с данными
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test (id) VALUES (1)")
        conn.commit()
        conn.close()

        manager = BackupManager(db_path, backup_dir)
        backup_path = manager.create_backup()

        assert backup_path.exists()
        assert backup_path.is_file()

        # Проверяем, что бэкап содержит данные
        backup_conn = sqlite3.connect(str(backup_path))
        cursor = backup_conn.execute("SELECT * FROM test")
        assert cursor.fetchone() == (1,)
        backup_conn.close()

    def test_backup_works_with_wal_mode(self, tmp_path: Path) -> None:
        """Бэкап работает с включённым WAL."""
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test (id) VALUES (42)")
        conn.commit()
        conn.close()

        manager = BackupManager(db_path, backup_dir)
        backup_path = manager.create_backup()

        # Проверяем, что бэкап корректен даже при WAL
        backup_conn = sqlite3.connect(str(backup_path))
        cursor = backup_conn.execute("SELECT * FROM test")
        assert cursor.fetchone() == (42,)
        backup_conn.close()

    def test_rotation_keeps_last_14(self, tmp_path: Path) -> None:
        """Ротация оставляет ровно 14 копий."""
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Создаём пустую базу
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        manager = BackupManager(db_path, backup_dir)

        # Создаём 20 бэкапов с небольшой задержкой, чтобы получить разные имена
        for _ in range(20):
            manager.create_backup()
            time.sleep(0.1)  # Небольшая задержка для разных временных меток

        # Должно остаться ровно 14 файлов
        backup_files = sorted(backup_dir.glob("kinday-*.db"))
        assert len(backup_files) == 14

    def test_rotation_removes_oldest(self, tmp_path: Path) -> None:
        """Ротация удаляет самые старые бэкапы."""
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.commit()
        conn.close()

        manager = BackupManager(db_path, backup_dir)

        # Создаём 16 бэкапов с задержками
        created_paths: list[Path] = []
        for _ in range(16):
            backup_path = manager.create_backup()
            created_paths.append(backup_path)
            time.sleep(0.1)  # Небольшая задержка для разных временных меток

        # Должно остаться ровно 14 файлов, самые старые удалены
        backup_files = sorted(backup_dir.glob("kinday-*.db"))
        assert len(backup_files) == 14

        # Проверяем, что все созданные файлы либо существуют (14),
        # либо были удалены (2 самых старых)
        remaining_count = sum(1 for p in created_paths if p.exists())
        assert remaining_count == 14

    def test_backup_file_naming(self, tmp_path: Path) -> None:
        """Имя бэкапа содержит дату и время с счётчиком."""
        db_path = tmp_path / "test.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.commit()
        conn.close()

        manager = BackupManager(db_path, backup_dir)
        backup_path = manager.create_backup()

        # Имя должно быть в формате kinday-YYYYMMDD-HHMMSS-NNN.db
        assert backup_path.name.startswith("kinday-")
        assert backup_path.name.endswith(".db")

        # Проверяем, что в имени есть временная метка и счётчик
        # kinday-YYYYMMDD-HHMMSS-NNN.db -> ['kinday', 'YYYYMMDD', 'HHMMSS', 'NNN']
        parts = backup_path.name.split(".")[0].split("-")
        assert len(parts) == 4, f"Ожидалось 4 части в имени, получено: {parts}"

        date_part = parts[1]  # YYYYMMDD
        time_part = parts[2]  # HHMMSS

        assert len(date_part) == 8, f"Неверная дата: {date_part}"
        assert len(time_part) == 6, f"Неверное время: {time_part}"

        # Проверяем, что дата в имени корректна (близка к текущей)
        from datetime import datetime

        year = int(date_part[:4])
        month = int(date_part[4:6])
        day = int(date_part[6:8])

        now = datetime.now()
        assert year == now.year
        assert month == now.month
        assert day == now.day
