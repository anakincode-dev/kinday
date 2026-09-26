"""Тик раз в минуту: claim, send, mark (см. SPEC 5.5).

Три шага — три отдельные короткие транзакции. Отправка в Telegram сетевой
вызов, и держать на ней открытую транзакцию нельзя: замок базы стоял бы всё
время таймаута Telegram, а вместе с ним встали бы и диалоги бота. Поэтому
claim фиксируется своей единицей работы, `Notifier.send` вызывается вне любой
транзакции, итоговый статус ставится своей единицей работы на каждую строку.

Гарантия — сообщение уходит не больше одного раза: при сомнениях строка
закрывается, а не отправляется повторно.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kinday.core.models import Reminder
from kinday.core.ports import (
    AccountRepo,
    Clock,
    EventRepo,
    MembershipRepo,
    Notifier,
    PersonRepo,
    RecipientBlocked,
    RelationRepo,
    ReminderRepo,
    TransportError,
    UnitOfWork,
)
from kinday.core.services import SendPlan, SkipReason, disable_delivery, plan_reminder_delivery

logger = logging.getLogger(__name__)

# Сколько напоминаний тик разбирает за один запуск. Накопившийся после простоя
# хвост расходится за несколько минут, зато один тик не растягивается на сотни
# сетевых вызовов при max_instances=1 (SPEC 5.4).
MAX_REMINDERS_PER_TICK = 100

# SPEC 5.5: после пятой неудачной попытки строка становится failed.
MAX_ATTEMPTS = 5


async def run_tick(
    clock: Clock,
    notifier: Notifier,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    membership_repo: MembershipRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    reminder_repo: ReminderRepo,
    unit_of_work: UnitOfWork,
) -> None:
    """Забирает due-напоминания по одному, отправляет, отмечает результат. Просроченные — missed.

    Строки забираются по одной, хотя `claim_due` умеет и пачку. Причина в цене
    потери: строка, оставшаяся в `sending` после падения процесса, больше не
    отправляется (SPEC 5.5), а захват пачкой означал бы, что падение на первой
    отправке закрывает и остальные девяносто девять строк, по которым `send`
    даже не вызывался. Так в `sending` в любой момент лежит не больше одной
    строки — ровно та, про которую и правда неизвестно, дошло ли сообщение.

    Момент `now` берётся один раз на тик и служит и отметкой времени, и точкой
    отсчёта для текста: «через N дней» считается на момент отправки
    (критерий приёмки 13). Порядок выборки — по сроку, так что первыми уходят
    самые старые.

    Сетевой сбой обрывает тик: строка уже вернулась в pending и снова стоит
    первой в очереди, так что продолжать значило бы забрать её тем же тиком
    заново и израсходовать все пять попыток за одну минуту вместо пяти.
    Повторная попытка — дело следующего тика (критерий приёмки 11), а сбой
    транспорта всё равно накрывает и остальные строки.
    """
    now = clock.now()
    for _ in range(MAX_REMINDERS_PER_TICK):
        async with unit_of_work:
            claimed = await reminder_repo.claim_due(now, 1)
        if not claimed:
            return
        keep_going = await _handle_one(
            claimed[0],
            now,
            notifier,
            account_repo,
            event_repo,
            membership_repo,
            person_repo,
            relation_repo,
            reminder_repo,
            unit_of_work,
        )
        if not keep_going:
            return
    logger.info("Тик разобрал %s напоминаний, остальные уйдут следующим", MAX_REMINDERS_PER_TICK)


async def _handle_one(
    reminder: Reminder,
    now: datetime,
    notifier: Notifier,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    membership_repo: MembershipRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    reminder_repo: ReminderRepo,
    unit_of_work: UnitOfWork,
) -> bool:
    """Решает судьбу одной забранной строки: отправить, пропустить или закрыть.

    Возвращает False, если тик надо прервать (сетевой сбой), и True, если можно
    брать следующую строку.

    Адрес и текст считает ядро (`plan_reminder_delivery`): у кого нет аккаунта,
    привязанного чата или включённой доставки, тот получает `failed` без
    единого обращения к Telegram, а просроченная строка — `missed`. Решение
    принимается до отправки, поэтому в сеть такие строки не уходят вовсе.

    `attempts` проверяется до отправки, а не только после неудачи: между
    `release` и `mark_failed` в `_send_one` строка на миг лежит в `pending` с
    исчерпанным счётчиком, и падение сервиса ровно в этом окне иначе дало бы
    шестую попытку (SPEC 5.5: «attempts достигло 5 — больше не пробуем»).

    Ошибка сборки сообщения (пропавшее событие, битые данные) закрывает строку
    как failed: отправлять нечего, а оставить её в `sending` значит копить
    мусор, который разберёт только перезапуск.
    """
    if reminder.attempts >= MAX_ATTEMPTS:
        logger.warning(
            "Напоминание %s уже израсходовало %s попыток, помечено failed",
            reminder.id,
            reminder.attempts,
        )
        async with unit_of_work:
            # Отправки сейчас не было: счётчик и так исчерпан, трогать его нечем.
            await reminder_repo.mark_failed(reminder.id, now, attempted=False)
        return True

    try:
        plan = await plan_reminder_delivery(
            reminder,
            now,
            account_repo,
            event_repo,
            membership_repo,
            person_repo,
            relation_repo,
        )
    except Exception:
        logger.exception("Не удалось собрать напоминание %s, помечено failed", reminder.id)
        async with unit_of_work:
            # До Telegram дело не дошло: если данные починятся, /start вернёт строку.
            await reminder_repo.mark_failed(reminder.id, now, attempted=False)
        return True

    if plan is SkipReason.MISSED:
        logger.info("Напоминание %s просрочено, помечено missed", reminder.id)
        async with unit_of_work:
            await reminder_repo.mark_missed(reminder.id, now)
        return True
    if plan is SkipReason.UNDELIVERABLE:
        logger.info("Напоминание %s отправлять некуда, помечено failed", reminder.id)
        async with unit_of_work:
            # Чата нет или доставка выключена — отправки не было. Появится чат,
            # и /start вернёт строку, если она опоздала меньше чем на сутки.
            await reminder_repo.mark_failed(reminder.id, now, attempted=False)
        return True
    return await _send_one(
        reminder.id,
        reminder.attempts,
        plan,
        now,
        notifier,
        account_repo,
        membership_repo,
        reminder_repo,
        unit_of_work,
    )


async def _send_one(
    reminder_id: int,
    attempts: int,
    plan: SendPlan,
    now: datetime,
    notifier: Notifier,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
    unit_of_work: UnitOfWork,
) -> bool:
    """Отправляет одно сообщение и ставит итоговый статус по результату (таблица SPEC 5.5).

    Возвращает False при сетевом сбое — тику дальше идти незачем.

    `notifier.send` вызывается до любого `async with unit_of_work`: транзакция
    открывается только на запись результата, уже после того как сеть ответила.

    Сетевая ошибка возвращает строку в pending и увеличивает `attempts`
    (`release`); если это была пятая попытка, строка тут же закрывается как
    failed — счётчик к этому моменту уже увеличен, так что в базе остаётся
    честное «пять попыток, больше не пробуем».

    403 закрывает не только эту строку: доставка аккаунту отключается, а его
    остальные pending-строки закрываются в той же транзакции (disable_delivery).
    Иначе каждый следующий тик заново получал бы тот же отказ.

    Неизвестная ошибка транспорта трактуется как падение между send и mark:
    дошло сообщение или нет, неизвестно, а повторная отправка хуже пропуска
    (SPEC 5.5), поэтому строка закрывается как failed.
    """
    try:
        await notifier.send(plan.chat_id, plan.text)
    except RecipientBlocked:
        async with unit_of_work:
            # Отправка была, пусть и отвергнута: строку воскрешать не за что,
            # а счётчик отличает её от закрытых заодно с ней ниже, в
            # disable_delivery, — те вернутся при следующем /start.
            await reminder_repo.mark_failed(reminder_id, now, attempted=True)
            closed = await disable_delivery(
                plan.account_id, now, account_repo, membership_repo, reminder_repo
            )
        logger.warning(
            "Аккаунт %s заблокировал бота: доставка отключена, напоминание %s — failed, "
            "закрыто ещё %s ожидавших строк",
            plan.account_id,
            reminder_id,
            closed,
        )
        return True
    except TransportError as error:
        async with unit_of_work:
            await reminder_repo.release(reminder_id)
            if attempts + 1 >= MAX_ATTEMPTS:
                # release уже учёл эту попытку — второй раз её считать нельзя.
                await reminder_repo.mark_failed(reminder_id, now, attempted=False)
        if attempts + 1 >= MAX_ATTEMPTS:
            logger.warning(
                "Напоминание %s не отправлено с %s попыток, помечено failed: %s",
                reminder_id,
                MAX_ATTEMPTS,
                error,
            )
        else:
            logger.info("Напоминание %s вернулось в pending после сбоя: %s", reminder_id, error)
        return False
    except Exception:
        logger.exception(
            "Неизвестная ошибка отправки напоминания %s: повторно не отправляем", reminder_id
        )
        async with unit_of_work:
            # send вызывался, а чем кончился — неизвестно: повторять нельзя.
            await reminder_repo.mark_failed(reminder_id, now, attempted=True)
        return True
    async with unit_of_work:
        await reminder_repo.mark_sent(reminder_id, now)
    return True


async def fail_stuck_sending(clock: Clock, reminder_repo: ReminderRepo) -> None:
    """Вызывается при старте сервиса: строки, застрявшие в sending, закрываются как failed.

    SPEC 5.5: такая строка осталась от падения между send и mark, и дошло ли
    сообщение — неизвестно. Повторная отправка хуже пропуска, поэтому строка
    закрывается и больше не отправляется (критерий приёмки 9).

    Запись в журнал — часть требования: о возможно потерянном сообщении должен
    узнать человек, поэтому в журнал идёт число закрытых строк, а при пустом
    результате (обычный чистый запуск) записи нет вовсе.
    """
    closed = await reminder_repo.fail_all_sending(clock.now())
    if closed:
        logger.warning(
            "Закрыто %s напоминаний, застрявших в sending с прошлого запуска: "
            "дошли они или нет, неизвестно, повторно не отправляем",
            closed,
        )
