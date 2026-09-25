"""Тесты сценариев ядра этапа 3b: приглашения, редактирование, настройки.

Фейковые in-memory реализации портов — tests/core/fakes.py. Критерии приёмки
22 (удаление человека с детьми превращает в заглушку, без детей — удаляет
целиком), 24-27 (приглашение привязано к записи, просроченное/использованное/
отозванное отклоняются, приглашение на занятую запись не выдаётся, не-владелец
не меняет дерево, но меняет свои настройки), плюс перематериализация при смене
даты рождения, настроек аккаунта и переопределения (SPEC 5.6).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from tests.core.fakes import FixedClock, Repos, build_repos

from kinday.core.models import (
    Account,
    Gender,
    Membership,
    ParentOf,
    Person,
    SpouseOf,
)
from kinday.core.relations import RelationKind
from kinday.core.services import (
    DEFAULT_OFFSETS_DAYS,
    NotFamilyOwner,
    accept_invite,
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

CLOCK = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))


async def _create_family(repos: Repos, *, telegram_user_id: int, name: str, birth_date: date):
    return await create_family(
        telegram_user_id,
        name,
        Gender.MALE,
        birth_date,
        "Europe/Moscow",
        repos.family,
        repos.person,
        repos.account,
        repos.membership,
        repos.event,
        repos.reminder,
        CLOCK,
    )


async def _add_person(
    repos: Repos,
    *,
    acting_account_id: int,
    family_id: int,
    name: str,
    gender: Gender,
    birth_date: date,
    relation_kind: RelationKind,
    relative_to_person_id: int,
    also_parent_of_siblings: bool | None = None,
):
    return await add_person(
        acting_account_id,
        family_id,
        name,
        gender,
        birth_date,
        relation_kind,
        relative_to_person_id,
        also_parent_of_siblings,
        repos.account,
        repos.event,
        repos.family,
        repos.person,
        repos.relation,
        repos.membership,
        repos.reminder,
        CLOCK,
    )


async def _delete_person(repos: Repos, *, acting_account_id: int, person_id: int) -> None:
    await delete_person(
        acting_account_id,
        person_id,
        repos.event,
        repos.family,
        repos.person,
        repos.relation,
        repos.membership,
        repos.reminder,
    )


async def _issue_invite(repos: Repos, *, acting_account_id: int, person_id: int):
    return await issue_invite(
        acting_account_id,
        person_id,
        repos.family,
        repos.person,
        repos.membership,
        repos.invite,
        CLOCK,
    )


async def _accept_invite(
    repos: Repos, *, code: str, telegram_user_id: int, timezone: str = "Europe/Moscow"
):
    return await accept_invite(
        code,
        telegram_user_id,
        timezone,
        repos.invite,
        repos.person,
        repos.account,
        repos.membership,
        repos.event,
        repos.override,
        repos.reminder,
        CLOCK,
    )


def _snapshot(repos: Repos) -> dict[str, object]:
    return {
        "people": dict(repos.person.people),
        "accounts": dict(repos.account.accounts),
        "memberships": list(repos.membership.memberships),
        "events": dict(repos.event.events),
        "relations": {k: list(v) for k, v in repos.relation.relations.items()},
        "reminders": list(repos.reminder.reminders),
    }


# --- Критерий 22: удаление человека ---------------------------------------


@pytest.mark.asyncio
async def test_delete_childless_person_removes_record_entirely() -> None:
    """Критерий приёмки 22: удаление бездетного человека удаляет запись целиком.

    Сестра — ребёнок в дереве (рёбра указывают к ней, не от неё), детей
    своих у неё нет, поэтому она должна исчезнуть физически, а не стать
    заглушкой.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=sister.id)

    assert sister.id not in repos.person.people
    relations = repos.relation.relations[family.id]
    assert all(
        not (isinstance(r, ParentOf) and (r.parent_id == sister.id or r.child_id == sister.id))
        for r in relations
    )
    assert all(e.person_id != sister.id for e in repos.event.events.values())
    assert all(r.person_id != sister.id for r in repos.reminder.reminders)


@pytest.mark.asyncio
async def test_delete_person_with_children_becomes_placeholder_keeps_child_edges() -> None:
    """Критерий приёмки 22: удаление человека с детьми превращает его в заглушку,

    родство между детьми сохраняется.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=father.id)

    placeholder = repos.person.people[father.id]
    assert placeholder.is_placeholder is True
    assert placeholder.name is None
    assert placeholder.gender is None
    assert placeholder.birth_date is None

    parent_edges = [r for r in repos.relation.relations[family.id] if isinstance(r, ParentOf)]
    assert {r.child_id for r in parent_edges if r.parent_id == father.id} == {
        owner_person.id,
        sister.id,
    }
    assert all(e.person_id != father.id for e in repos.event.events.values())
    assert all(r.person_id != father.id for r in repos.reminder.reminders)
    assert await repos.membership.get_by_person(father.id) is None


@pytest.mark.asyncio
async def test_delete_person_removes_spouse_edge() -> None:
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Марина", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    husband = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Игорь",
        gender=Gender.MALE,
        birth_date=date(1988, 2, 2),
        relation_kind=RelationKind.HUSBAND,
        relative_to_person_id=owner_person.id,
    )

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=husband.id)

    assert husband.id not in repos.person.people
    relations = repos.relation.relations[family.id]
    assert not any(isinstance(r, SpouseOf) and husband.id in (r.a_id, r.b_id) for r in relations)


@pytest.mark.asyncio
async def test_delete_person_rejects_non_owner_and_writes_nothing() -> None:
    repos = build_repos()
    await _create_family(repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15))
    [owner_person] = repos.person.people.values()
    intruder_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=999,
            current_family_id=None,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )
    before = _snapshot(repos)

    with pytest.raises(NotFamilyOwner):
        await _delete_person(
            repos, acting_account_id=intruder_account.id, person_id=owner_person.id
        )

    assert _snapshot(repos) == before


# --- Критерии 24-26: приглашения --------------------------------------------


@pytest.mark.asyncio
async def test_issue_invite_bound_to_specific_person() -> None:
    """Критерий приёмки 24: приглашение привязано к конкретному человеку."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )

    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)

    assert invite.person_id == sister.id
    accepted = await _accept_invite(repos, code=invite.code, telegram_user_id=222)
    assert accepted.id == sister.id
    membership = await repos.membership.get_by_person(sister.id)
    assert membership is not None
    assert membership.account_id != owner_account.id


@pytest.mark.asyncio
async def test_issue_invite_rejects_already_bound_person() -> None:
    """Критерий приёмки 26: приглашение на запись, к которой уже привязан аккаунт, не выдаётся."""
    repos = build_repos()
    await _create_family(repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15))
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()

    with pytest.raises(ValueError):
        await _issue_invite(repos, acting_account_id=owner_account.id, person_id=owner_person.id)


@pytest.mark.asyncio
async def test_accept_invite_rejects_used_code() -> None:
    """Критерий приёмки 25: уже использованное приглашение отклоняется."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)
    await _accept_invite(repos, code=invite.code, telegram_user_id=222)

    with pytest.raises(ValueError):
        await _accept_invite(repos, code=invite.code, telegram_user_id=333)


@pytest.mark.asyncio
async def test_accept_invite_rejects_revoked_code() -> None:
    """Критерий приёмки 25: отозванное приглашение отклоняется."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)
    await revoke_invite(owner_account.id, invite.id, repos.family, repos.invite, CLOCK)

    with pytest.raises(ValueError):
        await _accept_invite(repos, code=invite.code, telegram_user_id=222)


@pytest.mark.asyncio
async def test_accept_invite_rejects_expired_code() -> None:
    """Критерий приёмки 25: просроченное приглашение отклоняется."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)

    late_clock = FixedClock(CLOCK.now() + timedelta(days=8))
    with pytest.raises(ValueError):
        await accept_invite(
            invite.code,
            222,
            "Europe/Moscow",
            repos.invite,
            repos.person,
            repos.account,
            repos.membership,
            repos.event,
            repos.override,
            repos.reminder,
            late_clock,
        )


@pytest.mark.asyncio
async def test_revoke_invite_rejects_non_owner() -> None:
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)
    intruder_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=999,
            current_family_id=None,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )

    with pytest.raises(NotFamilyOwner):
        await revoke_invite(intruder_account.id, invite.id, repos.family, repos.invite, CLOCK)


@pytest.mark.asyncio
async def test_accept_invite_materializes_reminders_for_existing_events() -> None:
    """SPEC 5.6: присоединение аккаунта — повод построить напоминания по уже

    существующим событиям семьи, не дожидаясь суточного задания.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)

    await _accept_invite(repos, code=invite.code, telegram_user_id=222)

    # На момент приглашения в семье только один Event — день рождения
    # владельца: Марина сразу получает по нему напоминания (не дожидаясь
    # суточного задания), но не о собственном дне рождения — у неё самой
    # такого события в списке получателей нет.
    new_reminders = [r for r in repos.reminder.reminders if r.person_id == sister.id]
    assert len(new_reminders) == len(DEFAULT_OFFSETS_DAYS)
    assert all(repos.event.events[r.event_id].person_id == owner_person.id for r in new_reminders)


@pytest.mark.asyncio
async def test_accept_invite_respects_override_set_before_joining() -> None:
    """SPEC 5.6: переопределение, выставленное до присоединения, не теряется.

    set_override сохраняет ReminderOverride даже когда аккаунт ещё не состоит
    в семье события (docstring set_override). accept_invite обязан прочитать
    его при первой же материализации, а не только последующие
    перематериализации через _rematerialize_event_for_person.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)
    # Событие самой Марины сюда не годится: о своём дне рождения она
    # напоминания не получает (самоисключение, SPEC 3.4).
    [owner_event] = [e for e in repos.event.events.values() if e.person_id == owner_person.id]

    # Пока Марина ещё не приняла приглашение, у неё уже есть аккаунт
    # (например, он раньше состоял в другой семье) — владелец выставляет
    # переопределение по её будущему account_id.
    future_member_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=222,
            current_family_id=None,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )
    await set_override(
        future_member_account.id,
        owner_event.id,
        (2,),
        time(8, 0),
        repos.override,
        repos.event,
        repos.account,
        repos.membership,
        repos.reminder,
        CLOCK,
    )

    await _accept_invite(repos, code=invite.code, telegram_user_id=222)

    sister_reminders = [
        r
        for r in repos.reminder.reminders
        if r.person_id == sister.id and r.event_id == owner_event.id
    ]
    assert {r.offset_days for r in sister_reminders} == {2}


@pytest.mark.asyncio
async def test_accept_invite_rejects_second_record_in_same_family() -> None:
    """SPEC 5.3: не больше одной записи человека на пару (account_id, family_id).

    Один и тот же Telegram-аккаунт не может занять сразу две записи в одной
    семье через два разных приглашения.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    brother = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Дима",
        gender=Gender.MALE,
        birth_date=date(1993, 5, 5),
        relation_kind=RelationKind.BROTHER,
        relative_to_person_id=owner_person.id,
    )
    sister_invite = await _issue_invite(
        repos, acting_account_id=owner_account.id, person_id=sister.id
    )
    brother_invite = await _issue_invite(
        repos, acting_account_id=owner_account.id, person_id=brother.id
    )
    await _accept_invite(repos, code=sister_invite.code, telegram_user_id=222)

    with pytest.raises(ValueError):
        await _accept_invite(repos, code=brother_invite.code, telegram_user_id=222)


# --- Критерий 27: права участника ------------------------------------------


@pytest.mark.asyncio
async def test_non_owner_member_can_update_own_account_settings() -> None:
    """Критерий приёмки 27: не-владелец не меняет дерево, но меняет свои настройки."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=date(1992, 4, 4),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=owner_person.id,
    )
    invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=sister.id)
    await _accept_invite(repos, code=invite.code, telegram_user_id=222)
    [member_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 222]

    with pytest.raises(NotFamilyOwner):
        await _add_person(
            repos,
            acting_account_id=member_account.id,
            family_id=family.id,
            name="Чужой",
            gender=Gender.MALE,
            birth_date=date(1959, 1, 1),
            relation_kind=RelationKind.FATHER,
            relative_to_person_id=owner_person.id,
        )

    updated = await update_account_settings(
        member_account.id,
        "Asia/Tokyo",
        (3, 0),
        time(10, 0),
        repos.account,
        repos.membership,
        repos.event,
        repos.override,
        repos.reminder,
        CLOCK,
    )
    assert updated.timezone == "Asia/Tokyo"
    assert updated.offsets_days == (3, 0)


@pytest.mark.asyncio
async def test_update_person_rejects_non_owner() -> None:
    repos = build_repos()
    await _create_family(repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15))
    [owner_person] = repos.person.people.values()
    intruder_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=999,
            current_family_id=None,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )

    with pytest.raises(NotFamilyOwner):
        await update_person(
            intruder_account.id,
            owner_person.id,
            "Новое имя",
            Gender.MALE,
            date(1990, 6, 15),
            repos.account,
            repos.event,
            repos.family,
            repos.person,
            repos.membership,
            repos.override,
            repos.reminder,
            CLOCK,
        )


# --- Перематериализация: дата рождения, настройки аккаунта, переопределение -


@pytest.mark.asyncio
async def test_update_person_birth_date_change_rebuilds_birthday_reminders() -> None:
    """Критерий приёмки 7: смена даты рождения перестраивает будущие напоминания."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )
    [father_event] = [e for e in repos.event.events.values() if e.person_id == father.id]

    updated = await update_person(
        owner_account.id,
        father.id,
        "Пётр",
        Gender.MALE,
        date(1960, 4, 10),
        repos.account,
        repos.event,
        repos.family,
        repos.person,
        repos.membership,
        repos.override,
        repos.reminder,
        CLOCK,
    )

    assert updated.birth_date == date(1960, 4, 10)
    assert repos.event.events[father_event.id].date == date(1960, 4, 10)
    father_reminders = [r for r in repos.reminder.reminders if r.event_id == father_event.id]
    assert father_reminders
    assert all(
        r.occurrence_date.month == 4 and r.occurrence_date.day == 10 for r in father_reminders
    )
    assert len(father_reminders) == len(DEFAULT_OFFSETS_DAYS)


@pytest.mark.asyncio
async def test_set_override_replaces_offsets_for_single_account() -> None:
    """SPEC 5.3: переопределение заменяет смещения только для одного события и аккаунта."""
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    marina_account = await repos.account.save(
        Account(
            id=0,
            telegram_user_id=222,
            current_family_id=family.id,
            timezone="Europe/Moscow",
            offsets_days=DEFAULT_OFFSETS_DAYS,
            time_of_day=time(9, 0),
        )
    )
    marina = await repos.person.create(
        Person(id=0, family_id=family.id, name="Марина", gender=Gender.FEMALE, birth_date=None)
    )
    await repos.membership.create(
        Membership(account_id=marina_account.id, family_id=family.id, person_id=marina.id)
    )
    # Отец добавляется уже после того, как Марина стала участницей семьи, —
    # так его день рождения сразу материализуется обоим (owner и Марине),
    # ни один из них не герой этого события, самоисключение (SPEC 3.4) не
    # мешает проверке.
    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )
    [father_event] = [e for e in repos.event.events.values() if e.person_id == father.id]

    await set_override(
        owner_account.id,
        father_event.id,
        (2,),
        time(8, 0),
        repos.override,
        repos.event,
        repos.account,
        repos.membership,
        repos.reminder,
        CLOCK,
    )

    owner_reminders = [
        r
        for r in repos.reminder.reminders
        if r.event_id == father_event.id and r.person_id == owner_person.id
    ]
    marina_reminders = [
        r
        for r in repos.reminder.reminders
        if r.event_id == father_event.id and r.person_id == marina.id
    ]
    assert {r.offset_days for r in owner_reminders} == {2}
    assert {r.offset_days for r in marina_reminders} == set(DEFAULT_OFFSETS_DAYS)


@pytest.mark.asyncio
async def test_settings_change_rebuilds_reminder_inside_misfire_grace() -> None:
    """SPEC 5.6: окно удаления совпадает с окном построения (MISFIRE_GRACE).

    День рождения 1 января, «сейчас» — 1 января 00:00 UTC, пояс Europe/Moscow.
    Строка со смещением 1 день приходится на 31 декабря 06:00 UTC: она уже
    в прошлом относительно now, но ещё внутри MISFIRE_GRACE, поэтому
    materialize_for_event её строит. Если порог удаления взять равным now,
    старая строка переживёт перематериализацию, а новую в SQLite отбросит
    уникальный индекс (SPEC 5.3) — смена времени суток молча не применится.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    father = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 1, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=owner_person.id,
    )
    [father_event] = [e for e in repos.event.events.values() if e.person_id == father.id]

    def _inside_grace_reminders():
        return [
            r
            for r in repos.reminder.reminders
            if r.event_id == father_event.id
            and r.occurrence_date == date(2027, 1, 1)
            and r.offset_days == 1
        ]

    [before] = _inside_grace_reminders()
    assert before.due_at_utc == datetime(2026, 12, 31, 6, 0, tzinfo=UTC)
    assert before.due_at_utc < CLOCK.now()

    await update_account_settings(
        owner_account.id,
        "Europe/Moscow",
        DEFAULT_OFFSETS_DAYS,
        time(10, 0),
        repos.account,
        repos.membership,
        repos.event,
        repos.override,
        repos.reminder,
        CLOCK,
    )

    [after] = _inside_grace_reminders()
    assert after.due_at_utc == datetime(2026, 12, 31, 7, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_set_current_family_rejects_family_without_membership() -> None:
    repos = build_repos()
    family1 = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    family2 = await _create_family(
        repos, telegram_user_id=222, name="Борис", birth_date=date(1985, 8, 20)
    )
    [anton_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 111]

    with pytest.raises(ValueError):
        await set_current_family(anton_account.id, family2.id, repos.account, repos.membership)

    updated = await set_current_family(
        anton_account.id, family1.id, repos.account, repos.membership
    )
    assert updated.current_family_id == family1.id
