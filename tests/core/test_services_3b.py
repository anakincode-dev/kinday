"""Тесты сценариев ядра этапа 3b: приглашения, редактирование, настройки.

Фейковые in-memory реализации портов — tests/core/fakes.py. Критерии приёмки
22 (удаление человека с детьми превращает в заглушку, без детей — удаляет
целиком), 24-27 (приглашение привязано к записи, просроченное/использованное/
отозванное отклоняются, приглашение на занятую запись не выдаётся, не-владелец
не меняет дерево, но меняет свои настройки), плюс перематериализация при смене
даты рождения, настроек аккаунта и переопределения (SPEC 5.6).
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta

import pytest
from tests.core.fakes import FixedClock, Repos, build_repos

from kinday.core.models import (
    Account,
    Gender,
    Membership,
    ParentOf,
    Person,
    ReminderStatus,
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
    """Глубокая копия всего хранилища — для проверок «ничего не записано».

    Копия именно глубокая: сценарии меняют поля Person и Reminder на месте
    (например, превращение в заглушку), а поверхностная копия контейнеров
    такую правку не заметила бы и тест прошёл бы при сломанном коде.
    """
    return deepcopy(
        {
            "people": repos.person.people,
            "accounts": repos.account.accounts,
            "memberships": repos.membership.memberships,
            "events": repos.event.events,
            "relations": repos.relation.relations,
            "reminders": repos.reminder.reminders,
        }
    )


def _dangling_relations(repos: Repos, family_id: int) -> list[object]:
    """Рёбра, ссылающиеся на несуществующего человека.

    Физическое удаление обязано снимать все рёбра удаляемого узла: в SQLite
    при PRAGMA foreign_keys=ON (SPEC 6.2) висячее ребро либо не даст удалить
    строку, либо утащит за собой чужие рёбра каскадом.
    """
    alive = set(repos.person.people)
    dangling: list[object] = []
    for relation in repos.relation.relations.get(family_id, []):
        if isinstance(relation, ParentOf):
            ids = {relation.parent_id, relation.child_id}
        else:
            ids = {relation.a_id, relation.b_id}
        if ids - alive:
            dangling.append(relation)
    return dangling


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
async def test_delete_person_rejects_own_record_and_writes_nothing() -> None:
    """Владелец не может удалить собственную запись.

    Иначе семья осталась бы без владельца: передача владения вне скоупа
    первой версии (SPEC 7), а `families.owner_account_id` продолжал бы
    указывать на аккаунт, у которого в этой семье больше нет записи.
    """
    repos = build_repos()
    await _create_family(repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15))
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    before = _snapshot(repos)

    with pytest.raises(ValueError):
        await _delete_person(repos, acting_account_id=owner_account.id, person_id=owner_person.id)

    assert _snapshot(repos) == before


@pytest.mark.asyncio
async def test_delete_person_removes_placeholder_left_without_children() -> None:
    """Заглушка без детей удаляется вместе с последним ребёнком.

    Заглушка существует только чтобы связать братьев и сестёр между собой
    (SPEC 4.1). У Ольги и Светы общая заглушка-родитель; когда удалены обе,
    держать её больше не за что — иначе в дереве остался бы невидимый узел
    без единого ребра.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    wife = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Ольга",
        gender=Gender.FEMALE,
        birth_date=date(1991, 5, 5),
        relation_kind=RelationKind.WIFE,
        relative_to_person_id=owner_person.id,
    )
    # Родители Ольги неизвестны, поэтому её сестра цепляется к заглушке
    # (SPEC 4.1) — у заглушки оказывается ровно двое детей.
    wife_sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Света",
        gender=Gender.FEMALE,
        birth_date=date(1993, 7, 7),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=wife.id,
    )
    [placeholder] = [p for p in repos.person.people.values() if p.is_placeholder]
    assert {
        r.child_id
        for r in repos.relation.relations[family.id]
        if isinstance(r, ParentOf) and r.parent_id == placeholder.id
    } == {wife.id, wife_sister.id}

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=wife.id)
    # После первого удаления у заглушки ещё есть Света — она должна остаться.
    assert placeholder.id in repos.person.people

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=wife_sister.id)

    assert placeholder.id not in repos.person.people
    assert _dangling_relations(repos, family.id) == []
    # Владелец не задет: удаление жены и её сестры его записи не касается.
    assert owner_person.id in repos.person.people


@pytest.mark.asyncio
async def test_delete_placeholder_with_spouse_leaves_no_dangling_edges() -> None:
    """Удаление заглушки снимает и её ребро супруга, а не только рёбра к детям.

    Заглушка «несёт рёбра только к детям» — инвариант её создания, но не
    инвариант жизни узла: `add_person` не запрещает выбрать заглушку как
    `relative_to_person_id`, поэтому супруг у неё появиться может. Если при
    удалении такое ребро не снять, оно укажет на несуществующего человека —
    в SQLite (PRAGMA foreign_keys=ON, SPEC 6.2) это либо отказ удаления, либо
    каскад, а обход дерева споткнётся о призрачный узел.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    wife = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Ольга",
        gender=Gender.FEMALE,
        birth_date=date(1991, 5, 5),
        relation_kind=RelationKind.WIFE,
        relative_to_person_id=owner_person.id,
    )
    wife_sister = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Света",
        gender=Gender.FEMALE,
        birth_date=date(1993, 7, 7),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=wife.id,
    )
    [placeholder] = [p for p in repos.person.people.values() if p.is_placeholder]
    await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Иван",
        gender=Gender.MALE,
        birth_date=date(1950, 1, 1),
        relation_kind=RelationKind.HUSBAND,
        relative_to_person_id=placeholder.id,
    )

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=wife_sister.id)
    await _delete_person(repos, acting_account_id=owner_account.id, person_id=wife.id)

    assert placeholder.id not in repos.person.people
    assert _dangling_relations(repos, family.id) == []


@pytest.mark.asyncio
async def test_delete_person_cascades_through_stacked_placeholders() -> None:
    """Заглушка над заглушкой тоже удаляется, когда лишилась последнего ребёнка.

    Удаление человека с детьми превращает его в заглушку (SPEC 4.3), а его
    брат, добавленный после этого, заводит над ним вторую заглушку. Когда
    у нижней заглушки не остаётся детей, она удаляется — и тем самым может
    лишить детей верхнюю, поэтому проверка идёт вверх по цепочке, а не на
    один шаг.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    son = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Сын",
        gender=Gender.MALE,
        birth_date=date(2010, 1, 1),
        relation_kind=RelationKind.SON,
        relative_to_person_id=owner_person.id,
    )
    grandson = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Внук",
        gender=Gender.MALE,
        birth_date=date(2030, 1, 1),
        relation_kind=RelationKind.SON,
        relative_to_person_id=son.id,
    )
    # Сын становится заглушкой: у него есть ребёнок.
    await _delete_person(repos, acting_account_id=owner_account.id, person_id=son.id)
    assert repos.person.people[son.id].is_placeholder is True
    # Брат сына цепляется к заглушке-сыну, заводя над ней вторую заглушку.
    brother = await _add_person(
        repos,
        acting_account_id=owner_account.id,
        family_id=family.id,
        name="Брат",
        gender=Gender.MALE,
        birth_date=date(2012, 1, 1),
        relation_kind=RelationKind.BROTHER,
        relative_to_person_id=son.id,
    )
    upper = [p for p in repos.person.people.values() if p.is_placeholder and p.id not in (son.id,)]
    assert len(upper) == 1
    [upper_placeholder] = upper

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=grandson.id)
    # Заглушка-сын осталась без детей и должна исчезнуть, не оставив рёбер.
    assert son.id not in repos.person.people
    assert _dangling_relations(repos, family.id) == []

    await _delete_person(repos, acting_account_id=owner_account.id, person_id=brother.id)

    assert upper_placeholder.id not in repos.person.people
    assert _dangling_relations(repos, family.id) == []
    assert set(repos.person.people) == {owner_person.id}


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
async def test_invite_code_fits_telegram_start_parameter() -> None:
    """SPEC 5.7: код едет параметром start, отсюда алфавит [A-Za-z0-9_-] и длина.

    22 символа этого алфавита — 128 бит энтропии, и это заметно меньше
    предельных 64 символов параметра start. Коды двух приглашений не совпадают.
    """
    repos = build_repos()
    family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [owner_person] = repos.person.people.values()
    [owner_account] = repos.account.accounts.values()
    codes: set[str] = set()
    for index, name in enumerate(("Марина", "Света")):
        person = await _add_person(
            repos,
            acting_account_id=owner_account.id,
            family_id=family.id,
            name=name,
            gender=Gender.FEMALE,
            birth_date=date(1992, 4, 4 + index),
            relation_kind=RelationKind.SISTER,
            relative_to_person_id=owner_person.id,
        )
        invite = await _issue_invite(repos, acting_account_id=owner_account.id, person_id=person.id)
        assert re.fullmatch(r"[A-Za-z0-9_-]{22}", invite.code), invite.code
        codes.add(invite.code)

    assert len(codes) == 2


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
async def test_timezone_change_rebuilds_in_every_family_of_account() -> None:
    """Критерий приёмки 6: пояс перестраивает напоминания во всех семьях аккаунта.

    Настройки лежат в аккаунте, а не в семье (SPEC 2.1, 5.6), поэтому смена
    пояса обязана задеть и семью, которая для аккаунта не текущая.
    """
    repos = build_repos()
    own_family = await _create_family(
        repos, telegram_user_id=111, name="Антон", birth_date=date(1990, 6, 15)
    )
    [anton_person] = repos.person.people.values()
    [anton_account] = repos.account.accounts.values()
    father = await _add_person(
        repos,
        acting_account_id=anton_account.id,
        family_id=own_family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton_person.id,
    )

    # Вторая семья, где Антон — приглашённый участник, а не владелец.
    other_family = await _create_family(
        repos, telegram_user_id=222, name="Борис", birth_date=date(1985, 8, 20)
    )
    [boris_person] = [p for p in repos.person.people.values() if p.family_id == other_family.id]
    [boris_account] = [a for a in repos.account.accounts.values() if a.telegram_user_id == 222]
    anton_record = await _add_person(
        repos,
        acting_account_id=boris_account.id,
        family_id=other_family.id,
        name="Антон",
        gender=Gender.MALE,
        birth_date=date(1990, 6, 15),
        relation_kind=RelationKind.BROTHER,
        relative_to_person_id=boris_person.id,
    )
    invite = await _issue_invite(
        repos, acting_account_id=boris_account.id, person_id=anton_record.id
    )
    await _accept_invite(repos, code=invite.code, telegram_user_id=111)
    assert anton_account.current_family_id == own_family.id

    [father_event] = [e for e in repos.event.events.values() if e.person_id == father.id]
    [boris_event] = [e for e in repos.event.events.values() if e.person_id == boris_person.id]
    anton_ids = {anton_person.id, anton_record.id}
    before = {
        r.event_id
        for r in repos.reminder.reminders
        if r.person_id in anton_ids and r.status == ReminderStatus.PENDING
    }
    assert before == {father_event.id, boris_event.id}

    await update_account_settings(
        anton_account.id,
        "Asia/Tokyo",
        DEFAULT_OFFSETS_DAYS,
        time(9, 0),
        repos.account,
        repos.membership,
        repos.event,
        repos.override,
        repos.reminder,
        CLOCK,
    )

    anton_reminders = [r for r in repos.reminder.reminders if r.person_id in anton_ids]
    # 09:00 в Токио — 00:00 UTC, в обеих семьях сразу.
    assert {r.event_id for r in anton_reminders} == {father_event.id, boris_event.id}
    assert all(r.due_at_utc.hour == 0 and r.due_at_utc.minute == 0 for r in anton_reminders)
    # Напоминания Бориса остались по его поясу: чужие настройки не задеты.
    boris_reminders = [r for r in repos.reminder.reminders if r.person_id == boris_person.id]
    assert boris_reminders
    assert all(r.due_at_utc.hour == 6 for r in boris_reminders)


@pytest.mark.asyncio
async def test_timezone_change_keeps_sent_and_rebuilds_pending() -> None:
    """Критерий приёмки 6: смена пояса перестраивает pending, отправленное не трогает.

    Отправленную строку нельзя ни удалить, ни перезаписать (SPEC 5.6:
    «прошлое не переписывается»), и новой вместо неё тоже не появляется —
    её отбрасывает уникальный индекс по
    (event_id, person_id, occurrence_date, offset_days) (SPEC 5.3).
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
    [father_event] = [e for e in repos.event.events.values() if e.person_id == father.id]
    assert {r.offset_days for r in repos.reminder.reminders} == set(DEFAULT_OFFSETS_DAYS)

    # 09:00 в Москве — это 06:00 UTC; отмечаем строку «за 7 дней» отправленной.
    [sent] = [r for r in repos.reminder.reminders if r.offset_days == 7]
    assert sent.due_at_utc == datetime(2027, 2, 22, 6, 0, tzinfo=UTC)
    sent.status = ReminderStatus.SENT
    sent.sent_at = datetime(2027, 2, 22, 6, 0, 30, tzinfo=UTC)

    await update_account_settings(
        owner_account.id,
        "Asia/Tokyo",
        DEFAULT_OFFSETS_DAYS,
        time(9, 0),
        repos.account,
        repos.membership,
        repos.event,
        repos.override,
        repos.reminder,
        CLOCK,
    )

    by_offset = {r.offset_days: r for r in repos.reminder.reminders}
    assert len(repos.reminder.reminders) == len(DEFAULT_OFFSETS_DAYS)
    # Отправленная строка осталась ровно как была, дубля рядом с ней нет.
    assert by_offset[7].status == ReminderStatus.SENT
    assert by_offset[7].due_at_utc == datetime(2027, 2, 22, 6, 0, tzinfo=UTC)
    assert by_offset[7].sent_at == datetime(2027, 2, 22, 6, 0, 30, tzinfo=UTC)
    # Остальные пересобраны по новому поясу: 09:00 в Токио — это 00:00 UTC.
    assert by_offset[1].status == ReminderStatus.PENDING
    assert by_offset[1].due_at_utc == datetime(2027, 2, 28, 0, 0, tzinfo=UTC)
    assert by_offset[0].status == ReminderStatus.PENDING
    assert by_offset[0].due_at_utc == datetime(2027, 3, 1, 0, 0, tzinfo=UTC)
    assert all(r.event_id == father_event.id for r in repos.reminder.reminders)


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
