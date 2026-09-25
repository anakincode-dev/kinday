"""Сценарии: создать семью, добавить человека, выдать приглашение и так далее.

Единственное место, где встречаются модели, порты и остальные модули core.
Внешние слои (telegram, будущий api) вызывают только эти функции.
"""

from __future__ import annotations

from datetime import date, time

from kinday.core.models import Account, Event, Family, Gender, Invite, Person, ReminderOverride
from kinday.core.ports import (
    AccountRepo,
    Clock,
    EventRepo,
    FamilyRepo,
    InviteRepo,
    PersonRepo,
    RelationRepo,
    ReminderOverrideRepo,
    ReminderRepo,
)
from kinday.core.relations import RelationKind


async def create_family(
    owner_telegram_user_id: int,
    owner_name: str,
    owner_gender: Gender,
    owner_birth_date: date,
    owner_timezone: str,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
) -> Family:
    """Создаёт семью, человека-владельца и привязку его Telegram-аккаунта."""
    raise NotImplementedError


async def add_person(
    acting_account_id: int,
    family_id: int,
    name: str,
    gender: Gender,
    birth_date: date,
    relation_kind: RelationKind,
    relative_to_person_id: int,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Person:
    """Добавляет человека, переводит связь в базовые рёбра, заводит день рождения.

    Напоминания по новому дню рождения материализуются сразу же, не дожидаясь
    суточного задания (SPEC 3.1, критерий приёмки 14). Отклоняет действие,
    если acting_account не владелец семьи.
    """
    raise NotImplementedError


async def update_person(
    acting_account_id: int,
    person_id: int,
    name: str,
    gender: Gender,
    birth_date: date,
    person_repo: PersonRepo,
    event_repo: EventRepo,
    reminder_repo: ReminderRepo,
    account_repo: AccountRepo,
    clock: Clock,
) -> Person:
    """Правит запись человека. Смена даты рождения перестраивает будущие

    напоминания по его дню рождения (SPEC 5.6, критерий приёмки 7).
    Отклоняет действие, если acting_account не владелец семьи.
    """
    raise NotImplementedError


async def delete_person(
    acting_account_id: int,
    person_id: int,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    event_repo: EventRepo,
    reminder_repo: ReminderRepo,
) -> None:
    """Удаляет человека физически, либо, если у него есть дети, превращает

    его в заглушку: имя, пол, дата рождения и привязка аккаунта стираются,
    события и напоминания удаляются, рёбра к детям остаются (SPEC 4.3,
    критерий приёмки 22). Отклоняет действие, если acting_account не владелец семьи.
    """
    raise NotImplementedError


async def issue_invite(
    acting_account_id: int,
    person_id: int,
    invite_repo: InviteRepo,
    clock: Clock,
) -> Invite:
    """Одноразовое приглашение сроком на семь дней. Отклоняет, если человек уже привязан."""
    raise NotImplementedError


async def revoke_invite(
    acting_account_id: int,
    invite_id: int,
    invite_repo: InviteRepo,
) -> None:
    """Отзывает ещё не использованное приглашение (SPEC 3.2, критерий приёмки 25)."""
    raise NotImplementedError


async def accept_invite(
    code: str,
    telegram_user_id: int,
    timezone: str,
    invite_repo: InviteRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
    clock: Clock,
) -> Person:
    """Привязывает Telegram-аккаунт к записи, на которую выдано приглашение.

    Отклоняет просроченный, уже использованный или отозванный код
    (критерий приёмки 25) и запись, к которой уже привязан аккаунт (критерий 26).
    """
    raise NotImplementedError


async def add_event(
    acting_account_id: int,
    family_id: int,
    person_id: int,
    title: str,
    event_date: date,
    event_repo: EventRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Event:
    """Добавляет событие и сразу материализует по нему напоминания.

    Отклоняет действие, если acting_account не владелец семьи.
    """
    raise NotImplementedError


async def update_account_settings(
    account_id: int,
    timezone: str,
    offsets_days: tuple[int, ...],
    time_of_day: time,
    account_repo: AccountRepo,
    person_repo: PersonRepo,
    event_repo: EventRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Account:
    """Меняет пояс, смещения и время суток. Настройки принадлежат аккаунту, а не

    семье: перестраивает будущие напоминания во всех его семьях сразу
    (SPEC 2.1, 5.6, критерий приёмки 6). Уже отправленные напоминания не трогает.
    """
    raise NotImplementedError


async def set_override(
    account_id: int,
    event_id: int,
    offsets_days: tuple[int, ...],
    time_of_day: time,
    override_repo: ReminderOverrideRepo,
    event_repo: EventRepo,
    account_repo: AccountRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> ReminderOverride:
    """Переопределяет смещения и время суток для пары (аккаунт, событие) и

    перестраивает будущие напоминания по этому событию для этого аккаунта (SPEC 5.6).
    """
    raise NotImplementedError


async def set_current_family(
    account_id: int,
    family_id: int,
    account_repo: AccountRepo,
) -> Account:
    """Меняет текущую семью аккаунта, к которой относятся команды добавления и просмотра."""
    raise NotImplementedError
