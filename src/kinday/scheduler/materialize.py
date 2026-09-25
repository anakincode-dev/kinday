"""Суточное задание: достраивает напоминания на 400 дней вперёд. Страховка, не основной путь."""

from __future__ import annotations

from kinday.core.ports import Clock, EventRepo, ReminderRepo


async def run_daily_materialization(
    clock: Clock, event_repo: EventRepo, reminder_repo: ReminderRepo
) -> None:
    """Достраивает напоминания для всех активных событий и людей с аккаунтами."""
    raise NotImplementedError
