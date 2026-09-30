"""Фиксированный список городов для выбора часового пояса (SPEC 5.7)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from tests.scenario_repos import World
from tests.telegram.conftest import FakeTelegram
from tests.telegram.test_routers import ANTON

from kinday.telegram.cities import CITIES
from kinday.telegram.routers import _cities_keyboard, _timezone_of


def test_cities_are_the_fixed_list_in_order() -> None:
    assert list(CITIES.items()) == [
        ("Санкт-Петербург", "Europe/Moscow"),
        ("Новосибирск", "Asia/Novosibirsk"),
        ("Астана", "Asia/Almaty"),
        ("Павлодар", "Asia/Almaty"),
        ("Оснабрюк (Германия)", "Europe/Berlin"),
    ]


@pytest.mark.parametrize("timezone", sorted(set(CITIES.values())))
def test_every_city_timezone_is_valid(timezone: str) -> None:
    assert ZoneInfo(timezone).key == timezone


def test_keyboard_has_five_buttons() -> None:
    buttons = [button.text for row in _cities_keyboard().keyboard for button in row]

    assert buttons == list(CITIES)
    assert len(buttons) == 5


@pytest.mark.parametrize("text", ["оснабрюк", "Оснабрюк", " ОСНАБРЮК ", "Оснабрюк (Германия)"])
def test_osnabruck_is_found_without_country(text: str) -> None:
    assert _timezone_of(text) == "Europe/Berlin"


@pytest.mark.asyncio
async def test_moscow_is_not_in_the_list(telegram: FakeTelegram, sqlite_world: World) -> None:
    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send("Антон", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)
    await telegram.send("15.06.1990", user_id=ANTON)

    [answer] = await telegram.send("Москва", user_id=ANTON)

    assert answer.startswith("Не нашёл такой город")
    assert await sqlite_world.account.get_by_telegram_user_id(ANTON) is None
