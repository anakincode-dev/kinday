"""Тесты команды /persons: get_family_persons и format_persons_list.

Критерии приёмки 29-34:
29. /persons показывает всех людей текущей семьи аккаунта, включая его самого с пометкой "(это вы)".
30. Порядок: сам пользователь, его родители, супруги, дети, остальные.
31. Строка человека: имя, дата рождения ДД.ММ.ГГГГ, возраст на сегодня по поясу
    пользователя, родство одним словом. 29 февраля в невисокосный год — старше 28 февраля.
32. Под строкой — события человека, кроме дня рождения.
33. Ответ не длиннее 3800 символов, обрыв на границе с "… и ещё N человек".
34. Владелец и участник видят одинаковый список. Без аккаунта/текущей семьи — тот же ответ.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.core.fakes import FixedClock, build_repos

from kinday.core.models import Gender
from kinday.core.services import (
    create_family,
    get_family_persons,
)
from kinday.core.texts import format_persons_list


@pytest.mark.asyncio
async def test_format_persons_list_empty_family() -> None:
    """Пустая семья: владелец один, других нет."""
    repos = build_repos()
    CLOCK = FixedClock(datetime(2027, 2, 15, tzinfo=UTC))

    family = await create_family(
        owner_telegram_user_id=100,
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

    anton = next(p for p in repos.person.people.values() if p.name == "Антон")

    persons = await get_family_persons(family.id, repos.person, repos.relation)
    text = await format_persons_list(
        persons,
        anton.id,
        "Europe/Moscow",
        CLOCK.now(),
        relation_repo=repos.relation,
        event_repo=repos.event,
    )

    assert "Антон" in text
    assert "15.06.1990" in text
    assert "36" in text  # возраст на 15.02.2027
    assert "это вы" in text
    assert "и ещё" not in text


@pytest.mark.asyncio
async def test_get_family_persons_returns_all_real_people() -> None:
    """get_family_persons возвращает всех людей семьи, исключая заглушки."""
    repos = build_repos()
    CLOCK = FixedClock(datetime(2027, 2, 15, tzinfo=UTC))

    family = await create_family(
        owner_telegram_user_id=100,
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

    anton = next(p for p in repos.person.people.values() if p.name == "Антон")
    account = next(a for a in repos.account.accounts.values() if a.telegram_user_id == 100)

    # Добавляем отца
    from kinday.core.relations import RelationKind
    from kinday.core.services import add_person

    repos.uow = build_repos().uow

    await add_person(
        acting_account_id=account.id,
        family_id=family.id,
        name="Пётр",
        gender=Gender.MALE,
        birth_date=date(1979, 3, 1),
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

    persons = await get_family_persons(family.id, repos.person, repos.relation)
    person_names = [p.name for p in persons]

    assert "Антон" in person_names
    assert "Пётр" in person_names
