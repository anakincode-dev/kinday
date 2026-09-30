"""Команда /persons через настоящий Dispatcher (этап 9, SPEC 3.5, критерии 33–34)."""

from __future__ import annotations

import pytest
from tests.scenario_repos import World
from tests.telegram.conftest import FakeTelegram
from tests.telegram.test_routers import (
    ANTON,
    MARINA,
    _add_person,
    _create_family,
    _invite_code,
    _issue_invite,
)

from kinday.telegram.routers import NO_FAMILY


def _people_lines(text: str) -> list[str]:
    """Строки людей: без заголовка, событий и подсказок."""
    return [line for line in text.split("\n") if " — " in line and not line.startswith("  •")]


def _names(text: str) -> set[str]:
    return {line.split(" — ")[0] for line in _people_lines(text)}


async def _family_with_sister(telegram: FakeTelegram) -> None:
    await _create_family(telegram, user_id=ANTON)
    await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="сестра",
        name="Марина",
        gender="женский",
        birth="10.04.1995",
    )
    code = _invite_code(await _issue_invite(telegram, user_id=ANTON, person_name="Марина"))
    await telegram.send(f"/start {code}", user_id=MARINA)
    await telegram.send("Новосибирск", user_id=MARINA)


@pytest.mark.asyncio
async def test_persons_for_owner(telegram: FakeTelegram, sqlite_world: World) -> None:
    await _family_with_sister(telegram)

    [text] = await telegram.send("/persons", user_id=ANTON)

    assert text.startswith("Семья «")
    lines = _people_lines(text)
    assert lines[0].startswith("Антон — 15.06.1990,") and lines[0].endswith("(это вы)")
    assert lines[1].startswith("Марина — 10.04.1995,") and lines[1].endswith("(сестра)")


@pytest.mark.asyncio
async def test_persons_for_participant_same_people_other_text(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    await _family_with_sister(telegram)

    [owner_text] = await telegram.send("/persons", user_id=ANTON)
    [member_text] = await telegram.send("/persons", user_id=MARINA)

    assert _names(owner_text) == _names(member_text) == {"Антон", "Марина"}
    assert owner_text != member_text
    lines = _people_lines(member_text)
    assert lines[0].startswith("Марина —") and lines[0].endswith("(это вы)")
    assert lines[1].startswith("Антон —") and lines[1].endswith("(брат)")


@pytest.mark.asyncio
async def test_persons_without_family(telegram: FakeTelegram) -> None:
    answers = await telegram.send("/persons", user_id=ANTON)

    assert answers == [NO_FAMILY]


@pytest.mark.asyncio
async def test_persons_is_listed_in_menu(telegram: FakeTelegram) -> None:
    [menu] = await telegram.send("/help", user_id=ANTON)

    assert "/persons — люди семьи" in menu
    assert menu.count("/persons") == 1
