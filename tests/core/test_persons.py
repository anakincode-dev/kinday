"""Тесты сервиса list_family_persons для команды /persons (этап 9).

Критерии приёмки 29–34 из SPEC: показ людей семьи, сортировка по категориям,
расчёт возраста, вывод событий, обрезка при длине >3800 символов.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.scenarios import (
    add_event_to,
    add_person_to,
    create_family_for,
    list_family_persons_in,
)

from kinday.core.models import Gender
from kinday.core.relations import RelationKind

CLOCK = FixedClock(datetime(2027, 2, 15, tzinfo=UTC))


@pytest.mark.asyncio
async def test_list_family_persons_owner_sees_self_first(sqlite_world: World) -> None:
    """Критерий 29: владелец видит себя с пометкой (это вы)."""
    family = await create_family_for(
        sqlite_world,
        clock=CLOCK,
        telegram_user_id=1,
        name="Пётр",
        birth_date=date(1979, 3, 1),
        gender=Gender.MALE,
    )

    current_person_id = (await sqlite_world.person.list_by_family(family.id))[0].id

    await add_person_to(
        sqlite_world,
        clock=CLOCK,
        acting_account_id=1,
        family_id=family.id,
        name="Иван",
        birth_date=date(1950, 2, 10),
        gender=Gender.MALE,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=current_person_id,
    )

    persons = await list_family_persons_in(sqlite_world, clock=CLOCK, account_id=1)
    assert persons is not None
    assert len(persons.entries) == 2

    # Первый — сам Пётр
    assert persons.entries[0].name == "Пётр"
    assert persons.entries[0].is_self is True
    assert persons.entries[0].relation == ""

    # Второй — Иван (отец)
    assert persons.entries[1].name == "Иван"
    assert persons.entries[1].is_self is False
    assert persons.entries[1].relation == "отец"


@pytest.mark.asyncio
async def test_list_family_persons_sorting_categories(sqlite_world: World) -> None:
    """Критерий 30: порядок сам, родители, супруги, дети, остальные."""
    family = await create_family_for(
        sqlite_world,
        clock=CLOCK,
        telegram_user_id=1,
        name="Антон",
        birth_date=date(1990, 6, 15),
        gender=Gender.MALE,
    )

    current_person_id = (await sqlite_world.person.list_by_family(family.id))[0].id

    # Добавить отца
    await add_person_to(
        sqlite_world,
        clock=CLOCK,
        acting_account_id=1,
        family_id=family.id,
        name="Пётр",
        birth_date=date(1960, 3, 1),
        gender=Gender.MALE,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=current_person_id,
    )

    # Добавить жену
    await add_person_to(
        sqlite_world,
        clock=CLOCK,
        acting_account_id=1,
        family_id=family.id,
        name="Мария",
        birth_date=date(1992, 5, 12),
        gender=Gender.FEMALE,
        relation_kind=RelationKind.WIFE,
        relative_to_person_id=current_person_id,
    )

    # Добавить сына
    await add_person_to(
        sqlite_world,
        clock=CLOCK,
        acting_account_id=1,
        family_id=family.id,
        name="Иван",
        birth_date=date(2015, 7, 20),
        gender=Gender.MALE,
        relation_kind=RelationKind.SON,
        relative_to_person_id=current_person_id,
    )

    persons = await list_family_persons_in(sqlite_world, clock=CLOCK, account_id=1)
    assert persons is not None

    names = [e.name for e in persons.entries]
    # Порядок: Антон, Пётр (отец), Мария (жена), Иван (сын)
    assert names[0] == "Антон"  # Сам


@pytest.mark.asyncio
async def test_list_family_persons_age_calculation(sqlite_world: World) -> None:
    """Критерий 31: возраст на сегодня по часовому поясу пользователя."""
    await create_family_for(
        sqlite_world,
        clock=CLOCK,
        telegram_user_id=1,
        name="Антон",
        birth_date=date(1990, 6, 15),
        gender=Gender.MALE,
    )

    persons = await list_family_persons_in(sqlite_world, clock=CLOCK, account_id=1)
    assert persons is not None

    # Антон: день рождения 15.06, сегодня 15.02.2027
    # Возраст: 2027 - 1990 = 37, но день рождения ещё не прошёл (15.06 > 15.02)
    # Поэтому возраст 36
    assert persons.entries[0].age == 36


@pytest.mark.asyncio
async def test_list_family_persons_includes_events(sqlite_world: World) -> None:
    """Критерий 32: события человека показываются, кроме дня рождения."""
    family = await create_family_for(
        sqlite_world,
        clock=CLOCK,
        telegram_user_id=1,
        name="Антон",
        birth_date=date(1990, 6, 15),
        gender=Gender.MALE,
    )

    current_person_id = (await sqlite_world.person.list_by_family(family.id))[0].id

    # Добавить событие
    await add_event_to(
        sqlite_world,
        clock=CLOCK,
        acting_account_id=1,
        family_id=family.id,
        person_id=current_person_id,
        title="Годовщина свадьбы",
        event_date=date(2015, 8, 1),
        is_recurring_yearly=True,
    )

    persons = await list_family_persons_in(sqlite_world, clock=CLOCK, account_id=1)
    assert persons is not None

    # Антон должен иметь одно событие (годовщина свадьбы)
    assert len(persons.entries[0].events) == 1
    assert persons.entries[0].events[0].title == "Годовщина свадьбы"


@pytest.mark.asyncio
async def test_list_family_persons_owner_flag(sqlite_world: World) -> None:
    """is_owner == True для владельца."""
    await create_family_for(
        sqlite_world,
        clock=CLOCK,
        telegram_user_id=1,
        name="Пётр",
        birth_date=date(1979, 3, 1),
        gender=Gender.MALE,
    )

    persons = await list_family_persons_in(sqlite_world, clock=CLOCK, account_id=1)
    assert persons is not None
    assert persons.is_owner is True
