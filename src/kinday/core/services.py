"""Сценарии: создать семью, добавить человека, выдать приглашение.

Единственное место, где встречаются модели, порты и остальные модули core.
Внешние слои (telegram, будущий api) вызывают только эти функции.
"""

from __future__ import annotations

from datetime import date

from kinday.core.models import Event, Family, Gender, Invite, Person
from kinday.core.ports import AccountRepo, EventRepo, FamilyRepo, InviteRepo, PersonRepo


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
    relation_kind: str,
    relative_to_person_id: int,
    person_repo: PersonRepo,
    event_repo: EventRepo,
) -> Person:
    """Добавляет человека, переводит связь в базовые рёбра, заводит день рождения.

    Отклоняет действие, если acting_account не владелец семьи.
    """
    raise NotImplementedError


async def issue_invite(
    acting_account_id: int,
    person_id: int,
    invite_repo: InviteRepo,
) -> Invite:
    """Одноразовое приглашение сроком на семь дней. Отклоняет, если человек уже привязан."""
    raise NotImplementedError


async def accept_invite(
    code: str,
    telegram_user_id: int,
    timezone: str,
    invite_repo: InviteRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
) -> Person:
    """Привязывает Telegram-аккаунт к записи, на которую выдано приглашение."""
    raise NotImplementedError


async def add_event(
    acting_account_id: int,
    family_id: int,
    person_id: int,
    title: str,
    event_date: date,
    event_repo: EventRepo,
) -> Event:
    """Добавляет событие и сразу материализует по нему напоминания."""
    raise NotImplementedError
