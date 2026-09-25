"""Тик раз в минуту: claim, send, mark (см. SPEC 5.5)."""

from __future__ import annotations

from kinday.core.ports import Clock, Notifier, ReminderRepo

CLAIM_BATCH_SIZE = 100


async def run_tick(clock: Clock, notifier: Notifier, reminder_repo: ReminderRepo) -> None:
    """Забирает due-напоминания, отправляет, отмечает результат. Просроченные — missed."""
    raise NotImplementedError
