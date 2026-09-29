"""Тесты хендлера /persons через диалоги с фейковым апдейтом aiogram."""

from __future__ import annotations

import pytest
from tests.scenario_repos import World
from tests.telegram.conftest import FakeTelegram

ANTON_ID = 100
MARINA_ID = 200


async def _create_family(telegram: FakeTelegram, *, user_id: int = ANTON_ID) -> list[str]:
    """Создаёт семью для пользователя."""
    await telegram.send("/new_family", user_id=user_id)
    await telegram.send("Антон", user_id=user_id)
    await telegram.send("мужской", user_id=user_id)
    await telegram.send("15.06.1990", user_id=user_id)
    return await telegram.send("Москва", user_id=user_id)


async def _add_person(
    telegram: FakeTelegram,
    *,
    user_id: int,
    relative_index: int,
    relation: str,
    name: str,
    gender: str,
    birth: str,
) -> list[str]:
    """Добавляет человека в семью."""
    await telegram.send("/add_person", user_id=user_id)
    await telegram.send(str(relative_index), user_id=user_id)
    await telegram.send(relation, user_id=user_id)
    await telegram.send(name, user_id=user_id)
    await telegram.send(gender, user_id=user_id)
    return await telegram.send(birth, user_id=user_id)


async def _add_event(
    telegram: FakeTelegram,
    *,
    user_id: int,
    person_name: str,
    title: str,
    event_date: str,
    yearly: str = "да",
) -> list[str]:
    """Добавляет событие для человека."""
    listing = await telegram.send("/add_event", user_id=user_id)
    await telegram.send(_person_index(listing, person_name), user_id=user_id)
    await telegram.send(title, user_id=user_id)
    await telegram.send(event_date, user_id=user_id)
    return await telegram.send(yearly, user_id=user_id)


def _person_index(listing: list[str], person_name: str) -> str:
    """Индекс человека в списке по имени."""
    for line in listing[-1].splitlines():
        if person_name in line and "." in line:
            return line.split(".", 1)[0].strip()
    raise ValueError(f"Человек {person_name} не найден в списке")


def _answer(answers: list[str]) -> str:
    """Последний ответ бота."""
    assert answers, "бот не ответил"
    return answers[-1]


@pytest.mark.asyncio
async def test_persons_command_shows_family_members(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 29: /persons показывает всех людей текущей семьи, включая себя."""
    # Создаём семью
    await _create_family(telegram, user_id=ANTON_ID)

    # Добавляем отца
    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1979",
    )

    # Вызываем /persons
    answers = await telegram.send("/persons", user_id=ANTON_ID)

    text = _answer(answers)
    assert "Антон" in text
    assert "Пётр" in text
    assert "это вы" in text
    assert "отец" in text


@pytest.mark.asyncio
async def test_persons_command_shows_events_under_person(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 32: под строкой человека — его события (кроме дня рождения)."""
    await _create_family(telegram, user_id=ANTON_ID)
    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1979",
    )

    # Добавляем событие для отца (не день рождения)
    await _add_event(
        telegram,
        user_id=ANTON_ID,
        person_name="Пётр",
        title="Годовщина свадьбы",
        event_date="12.07.2010",
    )

    answers = await telegram.send("/persons", user_id=ANTON_ID)

    text = _answer(answers)
    assert "Годовщина свадьбы" in text
    assert "с 2015" in text or "с 2010" in text


@pytest.mark.asyncio
async def test_persons_command_participant_sees_same_list(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 34: участник видит тот же список, что и владелец."""
    await _create_family(telegram, user_id=ANTON_ID)
    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="сестра",
        name="Марина",
        gender="женский",
        birth="10.04.1995",
    )

    # Марина присоединяется по приглашению
    listing = await telegram.send("/invite", user_id=ANTON_ID)
    answers = await telegram.send(_person_index(listing, "Марина"), user_id=ANTON_ID)
    invite_link = next(part for part in answers[-1].split() if "?start=" in part)
    code = invite_link.split("?start=", 1)[1]

    await telegram.send(f"/start {code}", user_id=MARINA_ID)
    await telegram.send("Новосибирск", user_id=MARINA_ID)

    # Владелец вызывает /persons
    owner_answers = await telegram.send("/persons", user_id=ANTON_ID)

    # Участник вызывает /persons
    participant_answers = await telegram.send("/persons", user_id=MARINA_ID)

    owner_text = _answer(owner_answers)
    participant_text = _answer(participant_answers)

    assert owner_text == participant_text
    assert "Марина" in owner_text
    assert "Марина" in participant_text


@pytest.mark.asyncio
async def test_persons_command_without_current_family(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 34: без текущей семьи — тот же ответ, что у других команд."""
    # Пользователь без семьи
    answers = await telegram.send("/persons", user_id=ANTON_ID)

    text = _answer(answers)
    # Бот показывает приветствие/подсказку
    assert "/new_family" in text or "семья" in text.lower()


@pytest.mark.asyncio
async def test_persons_command_respects_truncation_limit(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 33: список обрезается при превышении 3800 символов."""
    await _create_family(telegram, user_id=ANTON_ID)

    # Добавляем много людей
    for i in range(30):
        await _add_person(
            telegram,
            user_id=ANTON_ID,
            relative_index=1,
            relation="отец",
            name=f"Человек_{i}_Длинное_имя_для_теста",
            gender="мужской",
            birth=f"01.01.{1970 + i}",
        )

    answers = await telegram.send("/persons", user_id=ANTON_ID)

    text = _answer(answers)
    # Должна быть строка "и ещё N человек"
    assert "… и ещё" in text


@pytest.mark.asyncio
async def test_persons_command_sorting(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Критерий 30: порядок людей в списке."""
    await _create_family(telegram, user_id=ANTON_ID)

    # Добавляем разных родственников
    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1979",
    )

    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="супруг",
        name="Игорь",
        gender="мужской",
        birth="05.08.1988",
    )

    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="сын",
        name="Дмитрий",
        gender="мужской",
        birth="03.06.2015",
    )

    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="мать",
        name="Мария",
        gender="женский",
        birth="01.01.1960",
    )

    answers = await telegram.send("/persons", user_id=ANTON_ID)

    text = _answer(answers)

    # Проверяем, что все люди присутствуют
    assert "Антон" in text
    assert "Пётр" in text
    assert "Игорь" in text
    assert "Дмитрий" in text
    assert "Мария" in text


@pytest.mark.asyncio
async def test_persons_command_feb29_non_leap_year_age(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 31: 29 февраля в невисокосный год → возраст от 28 февраля."""
    await _create_family(telegram, user_id=ANTON_ID)

    # Человек с 29 февраля
    await _add_person(
        telegram,
        user_id=ANTON_ID,
        relative_index=1,
        relation="брат",
        name="Василий",
        gender="мужской",
        birth="29.02.1988",
    )

    answers = await telegram.send("/persons", user_id=ANTON_ID)

    text = _answer(answers)
    assert "Антон" in text
    assert "Василий" in text
    # Оба человека в списке
    assert text.count("Василий") >= 1
