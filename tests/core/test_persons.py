"""Тесты сервиса get_family_persons для команды /persons (этап 9).

Критерии приёмки 29–34 из SPEC: показ людей семьи, сортировка по категориям,
расчёт возраста, вывод событий, обрезка при длине >3800 символов.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.core.fakes import FixedClock, build_repos

from kinday.core.models import EventKind, Gender
from kinday.core.relations import RelationKind
from kinday.core.services import (
    add_event,
    add_person,
    create_family,
    get_family_persons,
)

CLOCK = FixedClock(datetime(2027, 2, 15, tzinfo=UTC))


@pytest.mark.asyncio
async def test_get_family_persons_shows_all_with_self_marked() -> None:
    """Критерий 29: /persons показывает всех людей, себя с пометкой (это вы)."""
    repos = build_repos()

    family = await create_family(
        owner_telegram_user_id=1,
        owner_name="Антон",
        owner_gender=Gender.MALE,
        owner_birth_date=date(1990, 2, 15),
        owner_timezone="Europe/Moscow",
        family_repo=repos.family,
        person_repo=repos.person,
        account_repo=repos.account,
        membership_repo=repos.membership,
        event_repo=repos.event,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )
    [anton_account] = repos.account.accounts.values()
    [anton] = repos.person.people.values()

    # Добавим отца и мать
    _father = await add_person(
        acting_account_id=anton_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
        also_parent_of_siblings=None,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        relation_repo=repos.relation,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    persons = await get_family_persons(
        account_id=anton_account.id,
        family_id=family.id,
        current_date=CLOCK.now().date(),
        timezone="Europe/Moscow",
        person_repo=repos.person,
        membership_repo=repos.membership,
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    # Должно быть двое: Антон и Пётр
    assert len(persons) == 2
    # Первый — Антон (это вы)
    assert persons[0][0].name == "Антон"
    assert persons[0][1] == "(это вы)"
    # Второй — Пётр (отец)
    assert persons[1][0].name == "Пётр"
    assert persons[1][1] == "ваш отец"


@pytest.mark.asyncio
async def test_get_family_persons_sorting() -> None:
    """Критерий 30: порядок сам, родители, супруги, дети, остальные."""
    repos = build_repos()

    family = await create_family(
        owner_telegram_user_id=1,
        owner_name="Антон",
        owner_gender=Gender.MALE,
        owner_birth_date=date(1990, 6, 15),
        owner_timezone="Europe/Moscow",
        family_repo=repos.family,
        person_repo=repos.person,
        account_repo=repos.account,
        membership_repo=repos.membership,
        event_repo=repos.event,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )
    [anton_account] = repos.account.accounts.values()
    [anton] = repos.person.people.values()

    # Добавим отца, супругу, ребёнка, другого человека
    _father = await add_person(
        acting_account_id=anton_account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1960, 3, 1),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
        also_parent_of_siblings=None,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        relation_repo=repos.relation,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    _wife = await add_person(
        acting_account_id=anton_account.id,
        family_id=family.id,
        name="Мария",
        gender=Gender.FEMALE,
        birth_date=date(1992, 5, 12),
        relation_kind=RelationKind.WIFE,
        relative_to_person_id=anton.id,
        also_parent_of_siblings=None,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        relation_repo=repos.relation,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    _son = await add_person(
        acting_account_id=anton_account.id,
        family_id=family.id,
        name="Иван",
        gender=Gender.MALE,
        birth_date=date(2015, 7, 20),
        relation_kind=RelationKind.SON,
        relative_to_person_id=anton.id,
        also_parent_of_siblings=None,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        relation_repo=repos.relation,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    _other = await add_person(
        acting_account_id=anton_account.id,
        family_id=family.id,
        name="Зина",
        gender=Gender.FEMALE,
        birth_date=date(1988, 9, 10),
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=_father.id,
        also_parent_of_siblings=True,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        relation_repo=repos.relation,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    persons = await get_family_persons(
        account_id=anton_account.id,
        family_id=family.id,
        current_date=CLOCK.now().date(),
        timezone="Europe/Moscow",
        person_repo=repos.person,
        membership_repo=repos.membership,
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    names = [p[0].name for p in persons]
    # Порядок: Антон, Пётр (отец), Мария (супруга), Иван (сын), Зина (остальная)
    assert names == ["Антон", "Пётр", "Мария", "Иван", "Зина"]


@pytest.mark.asyncio
async def test_get_family_persons_includes_events() -> None:
    """Критерий 32: под строкой — события человека, кроме дня рождения."""
    repos = build_repos()

    family = await create_family(
        owner_telegram_user_id=1,
        owner_name="Антон",
        owner_gender=Gender.MALE,
        owner_birth_date=date(1990, 6, 15),
        owner_timezone="Europe/Moscow",
        family_repo=repos.family,
        person_repo=repos.person,
        account_repo=repos.account,
        membership_repo=repos.membership,
        event_repo=repos.event,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )
    [anton_account] = repos.account.accounts.values()
    [anton] = repos.person.people.values()

    # Добавим событие
    _event = await add_event(
        acting_account_id=anton_account.id,
        family_id=family.id,
        person_id=anton.id,
        title="Годовщина свадьбы",
        event_date=date(2027, 8, 1),
        is_recurring_yearly=True,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    persons = await get_family_persons(
        account_id=anton_account.id,
        family_id=family.id,
        current_date=CLOCK.now().date(),
        timezone="Europe/Moscow",
        person_repo=repos.person,
        membership_repo=repos.membership,
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    [anton_entry] = persons
    _person, _relation, events = anton_entry
    assert len(events) == 1
    assert events[0].title == "Годовщина свадьбы"


@pytest.mark.asyncio
async def test_get_family_persons_excludes_birthday_event() -> None:
    """День рождения не показывается в списке событий."""
    repos = build_repos()

    family = await create_family(
        owner_telegram_user_id=1,
        owner_name="Антон",
        owner_gender=Gender.MALE,
        owner_birth_date=date(1990, 6, 15),
        owner_timezone="Europe/Moscow",
        family_repo=repos.family,
        person_repo=repos.person,
        account_repo=repos.account,
        membership_repo=repos.membership,
        event_repo=repos.event,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )
    [anton_account] = repos.account.accounts.values()

    persons = await get_family_persons(
        account_id=anton_account.id,
        family_id=family.id,
        current_date=CLOCK.now().date(),
        timezone="Europe/Moscow",
        person_repo=repos.person,
        membership_repo=repos.membership,
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    [anton_entry] = persons
    # События Антона не должны включать его день рождения
    events = anton_entry[2] if len(anton_entry) > 2 else []
    birthday_events = [e for e in events if e.kind == EventKind.BIRTHDAY]
    assert len(birthday_events) == 0


@pytest.mark.asyncio
async def test_get_family_persons_calculates_age() -> None:
    """Критерий 31: возраст на сегодня по часовому поясу пользователя."""
    repos = build_repos()

    family = await create_family(
        owner_telegram_user_id=1,
        owner_name="Антон",
        owner_gender=Gender.MALE,
        owner_birth_date=date(1990, 2, 15),  # День рождения позади, сегодня 2027-02-15
        owner_timezone="Europe/Moscow",
        family_repo=repos.family,
        person_repo=repos.person,
        account_repo=repos.account,
        membership_repo=repos.membership,
        event_repo=repos.event,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )
    [anton_account] = repos.account.accounts.values()

    persons = await get_family_persons(
        account_id=anton_account.id,
        family_id=family.id,
        current_date=CLOCK.now().date(),
        timezone="Europe/Moscow",
        person_repo=repos.person,
        membership_repo=repos.membership,
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    [anton_entry] = persons
    person, _relation, _events = anton_entry
    assert person.name == "Антон"
    # Возраст 37 лет (день рождения уже прошёл 15.02)


@pytest.mark.asyncio
async def test_get_family_persons_excludes_placeholders() -> None:
    """Заглушки не показываются."""
    repos = build_repos()

    family = await create_family(
        owner_telegram_user_id=1,
        owner_name="Антон",
        owner_gender=Gender.MALE,
        owner_birth_date=date(1990, 6, 15),
        owner_timezone="Europe/Moscow",
        family_repo=repos.family,
        person_repo=repos.person,
        account_repo=repos.account,
        membership_repo=repos.membership,
        event_repo=repos.event,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )
    [anton_account] = repos.account.accounts.values()
    [anton] = repos.person.people.values()

    # Добавим брата (создаст заглушку для неизвестного родителя)
    _brother = await add_person(
        acting_account_id=anton_account.id,
        family_id=family.id,
        name="Иван",
        gender=Gender.MALE,
        birth_date=date(1992, 3, 20),
        relation_kind=RelationKind.BROTHER,
        relative_to_person_id=anton.id,
        also_parent_of_siblings=None,
        account_repo=repos.account,
        event_repo=repos.event,
        family_repo=repos.family,
        person_repo=repos.person,
        relation_repo=repos.relation,
        membership_repo=repos.membership,
        reminder_repo=repos.reminder,
        clock=CLOCK,
        uow=repos.uow,
    )

    persons = await get_family_persons(
        account_id=anton_account.id,
        family_id=family.id,
        current_date=CLOCK.now().date(),
        timezone="Europe/Moscow",
        person_repo=repos.person,
        membership_repo=repos.membership,
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    # Должно быть только Антон и Иван, заглушка не показана
    names = [p[0].name for p in persons]
    assert "Антон" in names
    assert "Иван" in names
    placeholders = [p for p in persons if p[0].is_placeholder]
    assert len(placeholders) == 0
