"""Диалоги бота без сети: фейковый апдейт aiogram, настоящий Dispatcher (PLAN.md, этап 6).

Слой telegram по SPEC 5.7 только переводит команды в вызовы сценариев ядра,
поэтому тест проверяет две вещи: что после диалога в базе оказалось то, что
записал бы сценарий, и что пользователь получил понятный ответ. Доменные
правила закрыты тестами ядра и хранилища, здесь они не дублируются.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from tests.core.fakes import FixedClock
from tests.scenario_repos import World
from tests.telegram.conftest import BOT_USERNAME, FakeTelegram

from kinday.core.models import EventKind, Gender, ParentOf, ReminderStatus

ANTON = 100
MARINA = 200


def _answer(answers: list[str]) -> str:
    """Последний ответ бота на сообщение, в нижнем регистре — тесты ищут в нём подсказку."""
    assert answers, "бот не ответил ни одним сообщением"
    return answers[-1].lower()


async def _create_family(
    telegram: FakeTelegram,
    *,
    user_id: int = ANTON,
    name: str = "Антон",
    gender: str = "мужской",
    birth: str = "15.06.1990",
    city: str = "Москва",
) -> list[str]:
    await telegram.send("/new_family", user_id=user_id)
    await telegram.send(name, user_id=user_id)
    await telegram.send(gender, user_id=user_id)
    await telegram.send(birth, user_id=user_id)
    return await telegram.send(city, user_id=user_id)


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
    await telegram.send("/add_person", user_id=user_id)
    await telegram.send(str(relative_index), user_id=user_id)
    await telegram.send(relation, user_id=user_id)
    await telegram.send(name, user_id=user_id)
    await telegram.send(gender, user_id=user_id)
    return await telegram.send(birth, user_id=user_id)


@pytest.mark.asyncio
async def test_start_without_account_offers_family_or_invite(telegram: FakeTelegram) -> None:
    """Незнакомому пользователю бот предлагает два входа из SPEC 3.1 и 3.2."""
    answers = await telegram.send("/start", user_id=ANTON)

    text = _answer(answers)
    assert "/new_family" in text
    assert "/join" in text


@pytest.mark.asyncio
async def test_new_family_dialog_creates_family_and_binds_chat(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """SPEC 3.1: имя, пол, дата рождения и город дают семью, человека и привязку чата.

    Чат обязателен: без него тик считает, что отправлять некуда, и закрывает
    напоминание как failed (SPEC 5.5).
    """
    answers = await _create_family(telegram, user_id=ANTON, city="Москва")

    assert "создана" in _answer(answers)
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None
    assert (account.timezone, account.chat_id, account.delivery_enabled) == (
        "Europe/Moscow",
        ANTON,
        True,
    )
    assert account.current_family_id is not None
    state = await sqlite_world.snapshot(account.current_family_id)
    [anton] = state.people
    assert anton.birth_date is not None
    assert (anton.name, anton.gender, anton.birth_date.isoformat()) == (
        "Антон",
        Gender.MALE,
        "1990-06-15",
    )
    [event] = state.events
    assert event.kind is EventKind.BIRTHDAY
    # Критерий 15: о собственном дне рождения не напоминают.
    assert state.reminders == []


@pytest.mark.asyncio
async def test_unknown_city_is_rejected_and_dialog_continues(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """SPEC 3.4: пояс выбирается из списка городов, посторонний ввод не записывается."""
    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send("Антон", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)
    await telegram.send("15.06.1990", user_id=ANTON)

    rejected = await telegram.send("Атлантида", user_id=ANTON)

    assert "город" in _answer(rejected)
    assert await sqlite_world.account.get_by_telegram_user_id(ANTON) is None

    accepted = await telegram.send("Москва", user_id=ANTON)
    assert "создана" in _answer(accepted)


@pytest.mark.asyncio
async def test_invalid_birth_date_asks_again(telegram: FakeTelegram, sqlite_world: World) -> None:
    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send("Антон", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)

    rejected = await telegram.send("вчера", user_id=ANTON)

    assert "дд.мм.гггг" in _answer(rejected)
    await telegram.send("15.06.1990", user_id=ANTON)
    assert "создана" in _answer(await telegram.send("Москва", user_id=ANTON))


@pytest.mark.asyncio
async def test_cancel_resets_dialog(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Прерванный диалог не оставляет состояния: следующий текст боту — не ответ на шаг."""
    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send("/cancel", user_id=ANTON)

    answers = await telegram.send("Антон", user_id=ANTON)

    assert "не понял" in _answer(answers)
    assert await sqlite_world.account.get_by_telegram_user_id(ANTON) is None


@pytest.mark.asyncio
async def test_start_after_block_restores_delivery(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """SPEC 5.5: доставка отключена «до повторного запуска бота» — /start её возвращает."""
    await _create_family(telegram, user_id=ANTON)
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None
    family_id = account.current_family_id
    assert family_id is not None
    [anton] = await sqlite_world.person.list_by_family(family_id)
    await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1980",
    )
    assert anton.id
    account.chat_id = None
    account.delivery_enabled = False
    await sqlite_world.account.save(account)
    for reminder in await sqlite_world.read_reminders():
        # Так их закрывает disable_delivery: отправки по ним не было.
        await sqlite_world.reminder.mark_failed(
            reminder.id, datetime(2027, 1, 1, tzinfo=UTC), attempted=False
        )

    answers = await telegram.send("/start", user_id=ANTON)

    assert "напоминания" in _answer(answers)
    restored = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert restored is not None
    assert (restored.chat_id, restored.delivery_enabled) == (ANTON, True)
    statuses = {r.status for r in await sqlite_world.read_reminders()}
    assert ReminderStatus.PENDING in statuses


@pytest.mark.asyncio
async def test_add_person_writes_parent_edge_and_birthday(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """SPEC 4.1: «отец» становится ребром parent_of, критерий 14 — напоминание сразу."""
    await _create_family(telegram, user_id=ANTON)
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None and account.current_family_id is not None
    family_id = account.current_family_id

    answers = await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1980",
    )

    assert "пётр" in _answer(answers)
    state = await sqlite_world.snapshot(family_id)
    people = {person.name: person for person in state.people}
    assert set(people) == {"Антон", "Пётр"}
    assert state.relations == [ParentOf(parent_id=people["Пётр"].id, child_id=people["Антон"].id)]
    assert {r.person_id for r in state.reminders} == {people["Антон"].id}


@pytest.mark.asyncio
async def test_add_person_asks_about_siblings(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Критерий 20: второй родитель — бот спрашивает про братьев и сестёр и ждёт ответа."""
    await _create_family(telegram, user_id=ANTON)
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None and account.current_family_id is not None
    family_id = account.current_family_id
    await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1980",
    )
    await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="сестра",
        name="Марина",
        gender="женский",
        birth="10.04.1995",
    )

    question = await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="мать",
        name="Ольга",
        gender="женский",
        birth="05.05.1960",
    )

    assert "сёстрам" in _answer(question) or "сестрам" in _answer(question)
    # Пока ответа нет, в базе нет ни человека, ни рёбер: сценарий откатился целиком.
    assert {p.name for p in await sqlite_world.person.list_by_family(family_id)} == {
        "Антон",
        "Пётр",
        "Марина",
    }

    await telegram.send("да", user_id=ANTON)

    state = await sqlite_world.snapshot(family_id)
    people = {person.name: person for person in state.people}
    olga = people["Ольга"].id
    children = {
        r.child_id for r in state.relations if isinstance(r, ParentOf) and r.parent_id == olga
    }
    assert children == {people["Антон"].id, people["Марина"].id}


@pytest.mark.asyncio
async def test_add_person_rejected_for_participant(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """Критерий 27: участник, не владелец, дерево не меняет и получает понятный отказ."""
    await _create_family(telegram, user_id=ANTON)
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None and account.current_family_id is not None
    family_id = account.current_family_id
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

    await telegram.send("/add_person", user_id=MARINA)
    await telegram.send("1", user_id=MARINA)
    await telegram.send("отец", user_id=MARINA)
    await telegram.send("Пётр", user_id=MARINA)
    await telegram.send("мужской", user_id=MARINA)
    answers = await telegram.send("01.03.1980", user_id=MARINA)

    assert "владелец" in _answer(answers)
    # Заглушка общего родителя у брата с сестрой (SPEC 4.1) в семье есть по праву,
    # поэтому сравниваются только настоящие записи: Пётр среди них не появился.
    people = await sqlite_world.person.list_by_family(family_id)
    assert {p.name for p in people if not p.is_placeholder} == {"Антон", "Марина"}


async def _issue_invite(telegram: FakeTelegram, *, user_id: int, person_name: str) -> list[str]:
    listing = await telegram.send("/invite", user_id=user_id)
    number = next(
        line.split(".", 1)[0].strip()
        for line in listing[-1].splitlines()
        if person_name in line and "." in line
    )
    return await telegram.send(number, user_id=user_id)


def _invite_code(answers: list[str]) -> str:
    link = next(part for part in answers[-1].split() if "?start=" in part)
    return link.split("?start=", 1)[1]


@pytest.mark.asyncio
async def test_invite_link_and_deep_link_join(telegram: FakeTelegram, sqlite_world: World) -> None:
    """SPEC 3.2: ссылка t.me/<bot>?start=<код>, переход по ней показывает, кто кого зовёт."""
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
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None and account.current_family_id is not None
    family_id = account.current_family_id

    issued = await _issue_invite(telegram, user_id=ANTON, person_name="Марина")
    assert f"t.me/{BOT_USERNAME}?start=" in issued[-1]
    code = _invite_code(issued)

    greeting = await telegram.send(f"/start {code}", user_id=MARINA)
    assert "антон" in _answer(greeting)
    assert "марина" in _answer(greeting)

    joined = await telegram.send("Новосибирск", user_id=MARINA)

    assert "марина" in _answer(joined)
    marina_account = await sqlite_world.account.get_by_telegram_user_id(MARINA)
    assert marina_account is not None
    assert (marina_account.timezone, marina_account.chat_id) == ("Asia/Novosibirsk", MARINA)
    state = await sqlite_world.snapshot(family_id)
    marina = next(person for person in state.people if person.name == "Марина")
    assert {m.person_id for m in state.memberships} >= {marina.id}
    # Критерий 15 и 16: новая участница получает напоминания обо всех событиях
    # семьи, кроме своего дня рождения.
    assert {r.person_id for r in state.reminders if r.person_id == marina.id}


@pytest.mark.asyncio
async def test_used_invite_code_is_rejected(telegram: FakeTelegram, sqlite_world: World) -> None:
    """Критерий 25: одноразовый код второй раз не срабатывает, и бот это объясняет."""
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

    answers = await telegram.send(f"/start {code}", user_id=300)

    assert "использовано" in _answer(answers)
    assert await sqlite_world.account.get_by_telegram_user_id(300) is None


@pytest.mark.asyncio
async def test_timezone_dialog_rebuilds_reminders(
    telegram: FakeTelegram, sqlite_world: World, clock: FixedClock
) -> None:
    """Критерий 6: смена пояса через бота перестраивает будущие напоминания аккаунта."""
    await _create_family(telegram, user_id=ANTON)
    await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="отец",
        name="Пётр",
        gender="мужской",
        birth="01.03.1980",
    )
    before = {
        (r.occurrence_date, r.offset_days): r.due_at_utc
        for r in await sqlite_world.read_reminders()
    }
    assert before

    await telegram.send("/timezone", user_id=ANTON)
    answers = await telegram.send("Новосибирск", user_id=ANTON)

    assert "новосибирск" in _answer(answers)
    account = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert account is not None
    assert (account.timezone, account.offsets_days, account.time_of_day) == (
        "Asia/Novosibirsk",
        (7, 1, 0),
        time(9, 0),
    )
    after = {
        (r.occurrence_date, r.offset_days): r.due_at_utc
        for r in await sqlite_world.read_reminders()
    }
    assert set(after) == set(before)
    assert all(after[key] < before[key] for key in before), (after, before)


@pytest.mark.asyncio
async def test_current_family_dialog_switches_family(
    telegram: FakeTelegram, sqlite_world: World
) -> None:
    """SPEC 2.1: у аккаунта в двух семьях есть выбор текущей — к ней относятся команды."""
    await _create_family(telegram, user_id=ANTON, name="Антон")
    first = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert first is not None
    first_family = first.current_family_id
    await _create_family(telegram, user_id=ANTON, name="Антон", city="Новосибирск")
    accounts = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert accounts is not None
    assert accounts.current_family_id == first_family, "первая семья остаётся текущей"

    listing = await telegram.send("/family", user_id=ANTON)
    numbers = [line.split(".", 1)[0].strip() for line in listing[-1].splitlines() if "." in line]
    assert len(numbers) == 2
    answers = await telegram.send(numbers[-1], user_id=ANTON)

    assert "текущая семья" in _answer(answers)
    switched = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert switched is not None
    assert switched.current_family_id != first_family


@pytest.mark.asyncio
async def test_add_person_without_family_explains_what_to_do(telegram: FakeTelegram) -> None:
    answers = await telegram.send("/add_person", user_id=ANTON)

    assert "/new_family" in _answer(answers)


@pytest.mark.asyncio
async def test_second_family_warns_that_current_did_not_change(telegram: FakeTelegram) -> None:
    """Вторая семья текущей не становится (SPEC 2.1) — бот не должен звать в /add_person."""
    await _create_family(telegram, user_id=ANTON, name="Антон")

    answers = await _create_family(telegram, user_id=ANTON, name="Антон", city="Новосибирск")

    text = _answer(answers)
    assert "создана" in text
    assert "/family" in text


@pytest.mark.asyncio
async def test_group_chat_is_ignored(telegram: FakeTelegram, sqlite_world: World) -> None:
    """SPEC 7: бот работает только в личных сообщениях — групповой chat_id не привязывается."""
    await _create_family(telegram, user_id=ANTON)
    before = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert before is not None and before.chat_id == ANTON

    group = -1001
    assert await telegram.send("/start", user_id=ANTON, chat_id=group, chat_type="group") == []
    assert await telegram.send("/invite", user_id=ANTON, chat_id=group, chat_type="group") == []

    after = await sqlite_world.account.get_by_telegram_user_id(ANTON)
    assert after is not None
    assert after.chat_id == ANTON, "доставка осталась в личном чате"


@pytest.mark.asyncio
async def test_non_text_message_inside_dialog_gets_answer(telegram: FakeTelegram) -> None:
    """Стикер вместо даты не оставляет диалог без ответа: подсказка и /cancel на месте."""
    await telegram.send("/new_family", user_id=ANTON)
    await telegram.send("Антон", user_id=ANTON)
    await telegram.send("мужской", user_id=ANTON)

    answers = await telegram.send(None, user_id=ANTON)

    assert "/cancel" in _answer(answers)
    # Диалог не сломался: следующая настоящая дата принимается.
    await telegram.send("15.06.1990", user_id=ANTON)
    assert "создана" in _answer(await telegram.send("Москва", user_id=ANTON))


@pytest.mark.asyncio
async def test_invite_to_family_where_account_already_is_rejected_before_city(
    telegram: FakeTelegram,
) -> None:
    """Критерий 26: вторая запись в той же семье не выдаётся, и город для этого не спрашивают."""
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
    await _add_person(
        telegram,
        user_id=ANTON,
        relative_index=1,
        relation="мать",
        name="Ольга",
        gender="женский",
        birth="01.02.1965",
    )
    code = _invite_code(await _issue_invite(telegram, user_id=ANTON, person_name="Марина"))

    answers = await telegram.send(f"/start {code}", user_id=ANTON)

    text = _answer(answers)
    assert "уже состоит" in text
    assert "город" not in text
