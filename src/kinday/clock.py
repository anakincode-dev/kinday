"""Настоящие часы — реализация core.ports.Clock для запуска сервиса.

Ядро объявляет протокол и получает часы снаружи (SPEC 5.2), чтобы тесты двигали
время подменой, а не `sleep`. В боевом запуске подставляется этот класс.
"""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Системное время, всегда aware и всегда в UTC (SPEC 5.2, 5.3)."""

    def now(self) -> datetime:
        return datetime.now(UTC)
