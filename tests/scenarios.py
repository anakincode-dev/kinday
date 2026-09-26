"""Вызовы сценариев ядра через набор портов `World` — общие для обоих бэкендов.

Тонкие обёртки: раскладывают репозитории `World` по параметрам функций
`core/services.py`, чтобы сценарный тест читался как сценарий, а не как
перечисление девяти репозиториев.
"""

from __future__ import annotations

from datetime import date, time

from tests.scenario_repos import World

from kinday.core.models import Event, Family, Gender, Invite, Person, ReminderOverride
from kinday.core.ports import Clock
from kinday.core.relations import RelationKind
from kinday.core.services import (
    accept_invite,
    add_event,
    add_person,
    create_family,
    delete_person,
    issue_invite,
    revoke_invite,
    set_current_family,
    set_override,
    update_account_settings,
    update_person,
)


async def create_family_for(
    world: World,
    *,
    clock: Clock,
    telegram_user_id: int,
    name: str,
    birth_date: date,
    gender: Gender = Gender.MALE,
    timezone: str = "Europe/Moscow",
) -> Family:
    return await create_family(
        telegram_user_id,
        name,
        gender,
        birth_date,
        timezone,
        world.family,
        world.person,
        world.account,
        world.membership,
        world.event,
        world.reminder,
        clock,
        world.uow,
    )


async def add_person_to(
    world: World,
    *,
    clock: Clock,
    acting_account_id: int,
    family_id: int,
    name: str,
    birth_date: date,
    relation_kind: RelationKind,
    relative_to_person_id: int,
    gender: Gender = Gender.MALE,
    also_parent_of_siblings: bool | None = None,
) -> Person:
    return await add_person(
        acting_account_id,
        family_id,
        name,
        gender,
        birth_date,
        relation_kind,
        relative_to_person_id,
        also_parent_of_siblings,
        world.account,
        world.event,
        world.family,
        world.person,
        world.relation,
        world.membership,
        world.reminder,
        clock,
        world.uow,
    )


async def add_event_to(
    world: World,
    *,
    clock: Clock,
    acting_account_id: int,
    family_id: int,
    person_id: int,
    title: str,
    event_date: date,
    is_recurring_yearly: bool = False,
) -> Event:
    return await add_event(
        acting_account_id,
        family_id,
        person_id,
        title,
        event_date,
        is_recurring_yearly,
        world.account,
        world.event,
        world.family,
        world.person,
        world.membership,
        world.reminder,
        clock,
        world.uow,
    )


async def update_person_in(
    world: World,
    *,
    clock: Clock,
    acting_account_id: int,
    person_id: int,
    name: str,
    birth_date: date,
    gender: Gender = Gender.MALE,
) -> Person:
    return await update_person(
        acting_account_id,
        person_id,
        name,
        gender,
        birth_date,
        world.account,
        world.event,
        world.family,
        world.person,
        world.membership,
        world.override,
        world.reminder,
        clock,
        world.uow,
    )


async def delete_person_in(
    world: World, *, clock: Clock, acting_account_id: int, person_id: int
) -> None:
    await delete_person(
        acting_account_id,
        person_id,
        world.account,
        world.event,
        world.family,
        world.person,
        world.relation,
        world.membership,
        world.invite,
        world.reminder,
        clock,
        world.uow,
    )


async def issue_invite_in(
    world: World, *, clock: Clock, acting_account_id: int, person_id: int
) -> Invite:
    return await issue_invite(
        acting_account_id,
        person_id,
        world.family,
        world.person,
        world.membership,
        world.invite,
        clock,
        world.uow,
    )


async def revoke_invite_in(
    world: World, *, clock: Clock, acting_account_id: int, invite_id: int
) -> None:
    await revoke_invite(
        acting_account_id,
        invite_id,
        world.family,
        world.invite,
        clock,
        world.uow,
    )


async def accept_invite_in(
    world: World,
    *,
    clock: Clock,
    code: str,
    telegram_user_id: int,
    timezone: str = "Europe/Moscow",
) -> Person:
    return await accept_invite(
        code,
        telegram_user_id,
        timezone,
        world.invite,
        world.person,
        world.account,
        world.membership,
        world.event,
        world.override,
        world.reminder,
        clock,
        world.uow,
    )


async def update_account_settings_in(
    world: World,
    *,
    clock: Clock,
    account_id: int,
    timezone: str,
    offsets_days: tuple[int, ...],
    time_of_day: time,
) -> None:
    await update_account_settings(
        account_id,
        timezone,
        offsets_days,
        time_of_day,
        world.account,
        world.membership,
        world.event,
        world.override,
        world.reminder,
        clock,
        world.uow,
    )


async def set_override_in(
    world: World,
    *,
    clock: Clock,
    account_id: int,
    event_id: int,
    offsets_days: tuple[int, ...],
    time_of_day: time,
) -> ReminderOverride:
    return await set_override(
        account_id,
        event_id,
        offsets_days,
        time_of_day,
        world.override,
        world.event,
        world.account,
        world.membership,
        world.reminder,
        clock,
        world.uow,
    )


async def set_current_family_in(world: World, *, account_id: int, family_id: int) -> None:
    await set_current_family(
        account_id,
        family_id,
        world.account,
        world.membership,
        world.uow,
    )
