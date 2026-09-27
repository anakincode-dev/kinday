"""Тесты systemd-конфигурации.

Проверяют, что файлы юнитов и таймеров существуют и имеют корректный синтаксис.
"""

from __future__ import annotations

import pathlib

# Директория с конфигурацией
DEPLOY_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "deploy"


class TestSystemdUnits:
    """Тесты systemd-юнитов."""

    def test_kinday_service_exists(self) -> None:
        """Файл kinday.service существует."""
        service_file = DEPLOY_DIR / "kinday.service"
        assert service_file.exists(), f"Файл {service_file} не найден"

    def test_kinday_backup_service_exists(self) -> None:
        """Файл kinday-backup.service существует."""
        service_file = DEPLOY_DIR / "kinday-backup.service"
        assert service_file.exists(), f"Файл {service_file} не найден"

    def test_kinday_backup_timer_exists(self) -> None:
        """Файл kinday-backup.timer существует."""
        timer_file = DEPLOY_DIR / "kinday-backup.timer"
        assert timer_file.exists(), f"Файл {timer_file} не найден"

    def test_kinday_service_has_correct_user(self) -> None:
        """kinday.service использует системного пользователя kinday."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "User=kinday" in content, "Юнит должен запускаться от пользователя kinday"

    def test_kinday_service_has_working_directory(self) -> None:
        """kinday.service указывает WorkingDirectory."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "WorkingDirectory=" in content, "Юнит должен указывать WorkingDirectory"

    def test_kinday_service_has_environment_file(self) -> None:
        """kinday.service указывает EnvironmentFile с токеном."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "EnvironmentFile=" in content, "Юнит должен указывать EnvironmentFile"
        assert "token.env" in content, "EnvironmentFile должен указывать на token.env"

    def test_kinday_service_has_exec_start(self) -> None:
        """kinday.service указывает ExecStart."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "ExecStart=" in content, "Юнит должен указывать ExecStart"
        # Проверяем, что используется python -m kinday, а не uv run
        assert "-m kinday" in content, "ExecStart должен использовать python -m kinday"
        assert "uv run" not in content, "ExecStart не должен использовать uv run"

    def test_kinday_service_has_restart_on_failure(self) -> None:
        """kinday.service имеет Restart=on-failure."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "Restart=on-failure" in content, "Юнит должен иметь Restart=on-failure"

    def test_kinday_service_has_kill_signal(self) -> None:
        """kinday.service имеет KillSignal=SIGTERM."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "KillSignal=SIGTERM" in content, "Юнит должен иметь KillSignal=SIGTERM"

    def test_kinday_service_has_timeout_stop_sec(self) -> None:
        """kinday.service имеет TimeoutStopSec=30."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "TimeoutStopSec=30" in content, "Юнит должен иметь TimeoutStopSec=30"

    def test_kinday_service_has_noprivileges(self) -> None:
        """kinday.service имеет NoNewPrivileges=yes."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "NoNewPrivileges=yes" in content, "Юнит должен иметь NoNewPrivileges=yes"

    def test_kinday_service_has_protect_system(self) -> None:
        """kinday.service имеет ProtectSystem=strict."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "ProtectSystem=strict" in content, "Юнит должен иметь ProtectSystem=strict"

    def test_kinday_service_has_protect_home(self) -> None:
        """kinday.service имеет ProtectHome=yes."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "ProtectHome=yes" in content, "Юнит должен иметь ProtectHome=yes"

    def test_kinday_service_has_private_tmp(self) -> None:
        """kinday.service имеет PrivateTmp=yes."""
        content = (DEPLOY_DIR / "kinday.service").read_text()
        assert "PrivateTmp=yes" in content, "Юнит должен иметь PrivateTmp=yes"

    def test_kinday_backup_service_has_backup_command(self) -> None:
        """kinday-backup.service имеет команду создания бэкапа."""
        content = (DEPLOY_DIR / "kinday-backup.service").read_text()
        assert "ExecStart=" in content, "Юнит должен указывать ExecStart"
        assert "kinday.backup" in content, "ExecStart должен вызывать kinday.backup"

    def test_kinday_backup_timer_runs_daily(self) -> None:
        """kinday-backup.timer запускается раз в сутки."""
        content = (DEPLOY_DIR / "kinday-backup.timer").read_text()
        assert "OnCalendar=" in content, "Таймер должен указывать OnCalendar"
        # Проверяем формат daily
        assert "daily" in content or "*-*-*" in content, "Таймер должен запускаться ежедневно"
