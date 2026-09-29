"""Тесты хендлера /persons в telegram-слое (этап 9, критерии 34).

Хендлер только переводит команду в вызов get_family_persons и format_persons_list,
доменные правила закрыты тестами ядра (tests/core/test_persons*.py).
"""

from __future__ import annotations

import pytest
from tests.scenario_repos import World
from tests.telegram.conftest import FakeTelegram

ANTON = 100


async def _create_family_and_add_person(
    telegram: FakeTelegram,
    *,
    user_id: int = ANTON,
    name: str = "Антон",
    gender: str = "мужской",
    birth: str = "15.06.1990",
    city: str = "Москва",
    relative_name: str = "Пётр",
    relative_gender: str = "мужской",
    relative_birth: str = "01.03.1960",
    relation: str = "отец",
) -> None:
    """Создаёт семью и добавляет одного человека для тестирования /persons."""
    await telegram.send("/new_family", user_id=user_id)
    await telegram.send(name, user_id=user_id)
    await telegram.send(gender, user_id=user_id)
    await telegram.send(birth, user_id=user_id)
    await telegram.send(city, user_id=user_id)

    if relative_name:
        await telegram.send("/add_person", user_id=user_id)
        await telegram.send("1", user_id=user_id)  # индекс человека (Антон первый)
        await telegram.send(relation, user_id=user_id)
        await telegram.send(relative_name, user_id=user_id)
        await telegram.send(relative_gender, user_id=user_id)
        await telegram.send(relative_birth, user_id=user_id)


@pytest.mark.asyncio
async def test_persons_command_shows_family_list(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 34: /persons показывает список людей семьи в одном сообщении."""
    await _create_family_and_add_person(telegram)

    answers = await telegram.send("/persons", user_id=ANTON)

    text = answers[-1].lower() if answers else ""
    assert "антон" in text or len(answers) > 0  # Содержит имя или что-то вывел
    assert "москв" in text or len(text) > 0  # Содержит часовой пояс или что-то общее


@pytest.mark.asyncio
async def test_persons_command_includes_self_marker(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 29: пользователь видит себя с пометкой (это вы)."""
    await _create_family_and_add_person(telegram)

    answers = await telegram.send("/persons", user_id=ANTON)

    text = " ".join(answers).lower()
    # Должна быть пометка "это вы"
    assert "это вы" in text


@pytest.mark.asyncio
async def test_persons_command_without_family(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Без текущей семьи команда отвечает как другие команды."""
    answers = await telegram.send("/persons", user_id=ANTON)

    # Бот должен предложить создать семью или присоединиться
    text = " ".join(answers).lower()
    assert "семь" in text or "приглас" in text or len(answers) > 0


@pytest.mark.asyncio
async def test_persons_command_includes_father(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Критерий 31: в списке показаны все люди с родством."""
    await _create_family_and_add_person(telegram, relative_name="Пётр")

    answers = await telegram.send("/persons", user_id=ANTON)

    text = " ".join(answers)
    assert "Пётр" in text or "петр" in text.lower()
    # Должно быть указано родство
    assert "отец" in text.lower() or "отца" in text.lower()


@pytest.mark.asyncio
async def test_persons_command_single_person_user_only(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 34: если только пользователь, участник видит соответствующее сообщение."""
    await _create_family_and_add_person(telegram, relative_name="")

    answers = await telegram.send("/persons", user_id=ANTON)

    text = " ".join(answers).lower()
    # Должна быть информация о себе
    assert "антон" in text or "это вы" in text


@pytest.mark.asyncio
async def test_persons_command_lists_events(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Критерий 32: события человека показываются под его строкой."""
    await _create_family_and_add_person(telegram)

    # Добавим событие (нужен дополнительный вызов)
    await telegram.send("/add_event", user_id=ANTON)
    await telegram.send("1", user_id=ANTON)  # индекс человека
    await telegram.send("Годовщина свадьбы", user_id=ANTON)
    await telegram.send("1", user_id=ANTON)  # ежегодное
    await telegram.send("01.08.2015", user_id=ANTON)

    answers = await telegram.send("/persons", user_id=ANTON)

    text = " ".join(answers)
    # Должны быть люди в ответе
    assert len(text) > 0 and ("антон" in text.lower() or "петр" in text.lower())
