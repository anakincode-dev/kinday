"""Суточное задание: достраивает напоминания на 400 дней вперёд. Страховка, не основной путь."""

from __future__ import annotations

import logging

from kinday.core.ports import (
    AccountRepo,
    Clock,
    EventRepo,
    MembershipRepo,
    ReminderOverrideRepo,
    ReminderRepo,
)
from kinday.core.reminders import apply_override, materialize_for_event

logger = logging.getLogger(__name__)


async def run_daily_materialization(
    clock: Clock,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    membership_repo: MembershipRepo,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
) -> None:
    """Достраивает напоминания для всех событий и всех людей с аккаунтами.

    Только достраивает: удаления и пересчёта здесь нет. Пересчёт — дело
    перематериализации при смене входных данных (SPEC 5.6), а задание лишь
    сдвигает горизонт, когда до следующего наступления события остаётся меньше
    400 дней. Дубли отбрасывает уникальный индекс `reminders` (SPEC 5.3), так
    что повторный запуск ничего не меняет, а уже отправленную строку
    материализация не воскрешает.

    Поэтому и UnitOfWork не нужен: задание не делает половинчатых изменений —
    каждая вставка самостоятельна и идемпотентна, а прерванный на середине
    прогон досчитает следующий.

    Переопределения по событию (SPEC 5.6) применяются так же, как в сценариях
    ядра: смещения и время суток берутся из ReminderOverride, если он есть,
    иначе из настроек аккаунта.
    """
    added = 0
    for event in await event_repo.list_all():
        reminders = []
        for membership in await membership_repo.list_by_family(event.family_id):
            account = await account_repo.get(membership.account_id)
            override = await override_repo.get(membership.account_id, event.id)
            reminders.extend(
                materialize_for_event(
                    event, [(membership, apply_override(account, override))], clock
                )
            )
        if reminders:
            await reminder_repo.add_many(reminders)
            added += len(reminders)
    logger.info("Суточная материализация: построено не больше %s напоминаний", added)
