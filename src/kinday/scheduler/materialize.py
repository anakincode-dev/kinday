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
    UnitOfWork,
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
    unit_of_work: UnitOfWork,
) -> None:
    """Достраивает напоминания для всех событий и всех людей с аккаунтами.

    Только достраивает: удаления и пересчёта здесь нет. Пересчёт — дело
    перематериализации при смене входных данных (SPEC 5.6), а задание лишь
    сдвигает горизонт, когда до следующего наступления события остаётся меньше
    400 дней. Дубли отбрасывает уникальный индекс `reminders` (SPEC 5.3), так
    что повторный запуск ничего не меняет, а уже отправленную строку
    материализация не воскрешает.

    Запись идёт тем же путём, что и в сценариях ядра: через UnitOfWork, но своей
    короткой транзакцией на каждое событие, а не одной на весь прогон. Событий в
    базе сколько угодно, и общая транзакция держала бы замок записи всё время
    прохода, мешая диалогам бота и тику. Прерванный на середине прогон не портит
    состояние: вставки идемпотентны, недостроенное досчитает следующий запуск.

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
            async with unit_of_work:
                await reminder_repo.add_many(reminders)
            added += len(reminders)
    logger.info("Суточная материализация: построено не больше %s напоминаний", added)
