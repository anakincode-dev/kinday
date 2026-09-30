"""Тесты форматтера format_persons_list для команды /persons (этап 9).

Критерий 33: точное соответствие формату из SPEC 3.5, обрезка при длине >3800.
"""

from __future__ import annotations

from datetime import date

import pytest

from kinday.core.models import Event, EventKind
from kinday.core.texts import FamilyPersons, PersonEntry, format_persons_list


def test_format_persons_list_exact_format_spec() -> None:
    """Критерий 33: формат точно как в примере SPEC 3.5."""
    event = Event(
        id=1,
        family_id=1,
        person_id=1,
        kind=EventKind.CUSTOM,
        title="годовщина свадьбы",
        date=date(2015, 8, 3),
        is_recurring_yearly=True,
    )

    entries = (
        PersonEntry(
            name="Антон Петров",
            birth_date=date(1990, 2, 3),
            age=36,
            relation="",
            is_self=True,
            events=(event,),
        ),
        PersonEntry(
            name="Пётр Петров",
            birth_date=date(1979, 3, 1),
            age=47,
            relation="отец",
            is_self=False,
            events=(),
        ),
    )

    data = FamilyPersons(
        family_name="Петровы",
        is_owner=True,
        entries=entries,
    )

    result = format_persons_list(data)

    # Проверить заголовок
    assert "Семья «Петровы»:" in result
    # Проверить формат даты (03.02, не 3.02)
    assert "03.02.1990" in result
    # Проверить возраст
    assert "36 лет" in result
    # Проверить пометку (это вы)
    assert "(это вы)" in result
    # Проверить родство
    assert "(отец)" in result
    # Проверить событие с форматом даты (03.08, не 3.8)
    assert "03.08" in result
    # Проверить год события
    assert "(с 2015)" in result


def test_format_persons_list_truncation_at_500() -> None:
    """Критерий 33: обрезка при превышении лимита символов."""
    # Создать много людей, чтобы превысить лимит 500 символов
    entries = []
    for i in range(50):
        entry = PersonEntry(
            name=f"Человек {i:02d}",
            birth_date=date(1980 + i % 40, 1 + i % 12, 1),
            age=40,
            relation="",
            is_self=False,
            events=(),
        )
        entries.append(entry)

    data = FamilyPersons(
        family_name="Семья",
        is_owner=True,
        entries=tuple(entries),
    )

    result = format_persons_list(data, max_length=500)

    # Должна быть строка "… и ещё"
    assert "… и ещё" in result
    # Результат должен быть не длиннее 500 символов
    assert len(result) <= 500


def test_format_persons_list_single_person_owner() -> None:
    """Для одного человека (владельца) показать подсказку /add_person."""
    entries = (
        PersonEntry(
            name="Пётр",
            birth_date=date(1979, 3, 1),
            age=47,
            relation="",
            is_self=True,
            events=(),
        ),
    )

    data = FamilyPersons(
        family_name="Петровы",
        is_owner=True,
        entries=entries,
    )

    result = format_persons_list(data)

    # Должна быть подсказка для владельца
    assert "/add_person" in result


def test_format_persons_list_single_person_participant() -> None:
    """Для одного человека (участника) показать другую подсказку."""
    entries = (
        PersonEntry(
            name="Пётр",
            birth_date=date(1979, 3, 1),
            age=47,
            relation="",
            is_self=True,
            events=(),
        ),
    )

    data = FamilyPersons(
        family_name="Петровы",
        is_owner=False,
        entries=entries,
    )

    result = format_persons_list(data)

    # Должна быть подсказка для участника
    assert "Других людей в семье пока нет" in result


def test_format_persons_list_recurring_event_format() -> None:
    """Событие с is_recurring_yearly=True показывать с форматом DD.MM (с YYYY)."""
    event = Event(
        id=1,
        family_id=1,
        person_id=1,
        kind=EventKind.CUSTOM,
        title="Годовщина свадьбы",
        date=date(2015, 8, 3),
        is_recurring_yearly=True,
    )

    entries = (
        PersonEntry(
            name="Пётр",
            birth_date=date(1979, 3, 1),
            age=47,
            relation="",
            is_self=True,
            events=(event,),
        ),
    )

    data = FamilyPersons(
        family_name="Петровы",
        is_owner=True,
        entries=entries,
    )

    result = format_persons_list(data)

    # Должен быть формат DD.MM (с YYYY)
    assert "03.08 — Годовщина свадьбы (с 2015)" in result or "03.08" in result


def test_format_persons_list_single_event_format() -> None:
    """Событие с is_recurring_yearly=False показывать с полной датой DD.MM.YYYY."""
    event = Event(
        id=1,
        family_id=1,
        person_id=1,
        kind=EventKind.CUSTOM,
        title="Выпускной",
        date=date(2024, 6, 1),
        is_recurring_yearly=False,
    )

    entries = (
        PersonEntry(
            name="Иван",
            birth_date=date(2005, 3, 1),
            age=19,
            relation="",
            is_self=True,
            events=(event,),
        ),
    )

    data = FamilyPersons(
        family_name="Иванов",
        is_owner=True,
        entries=entries,
    )

    result = format_persons_list(data)

    # Должен быть формат DD.MM.YYYY
    assert "01.06.2024" in result


def _entry(name: str, *, is_self: bool = False, relation: str = "") -> PersonEntry:
    return PersonEntry(
        name=name,
        birth_date=date(1990, 2, 3),
        age=36,
        relation=relation,
        is_self=is_self,
        events=(),
    )


def test_format_persons_list_matches_spec_example_byte_for_byte() -> None:
    wedding = Event(
        id=1,
        family_id=1,
        person_id=1,
        kind=EventKind.CUSTOM,
        title="годовщина свадьбы",
        date=date(2015, 3, 8),
        is_recurring_yearly=True,
    )
    anton = PersonEntry("Антон Петров", date(1990, 2, 3), 36, "", True, (wedding,))
    petr = PersonEntry("Пётр Петров", date(1979, 3, 1), 47, "отец", False, ())

    result = format_persons_list(FamilyPersons("Петровы", True, (anton, petr)))

    assert result == (
        "Семья «Петровы»:\n"
        "\n"
        "Антон Петров — 03.02.1990, 36 лет (это вы)\n"
        "  • 08.03 — годовщина свадьбы (с 2015)\n"
        "\n"
        "Пётр Петров — 01.03.1979, 47 лет (отец)"
    )


def test_format_persons_list_single_person_hints_byte_for_byte() -> None:
    alone = (_entry("Антон", is_self=True),)
    head = "Семья «Петровы»:\n\nАнтон — 03.02.1990, 36 лет (это вы)\n\n"

    assert format_persons_list(FamilyPersons("Петровы", True, alone)) == (
        head + "Добавьте родственников командой /add_person."
    )
    assert format_persons_list(FamilyPersons("Петровы", False, alone)) == (
        head + "Других людей в семье пока нет."
    )


@pytest.mark.parametrize("name_length", range(1, 81))
def test_format_persons_list_never_exceeds_limit(name_length: int) -> None:
    entries = tuple(_entry("Я" * name_length, relation="брат") for _ in range(400))

    result = format_persons_list(FamilyPersons("Семья", True, entries))

    assert len(result) <= 3800
    assert "… и ещё " in result


@pytest.mark.parametrize(
    ("count", "word"),
    [
        (1, "человек"),
        (2, "человека"),
        (5, "человек"),
        (11, "человек"),
        (21, "человек"),
        (22, "человека"),
    ],
)
def test_format_persons_list_tail_declension(count: int, word: str) -> None:
    entries = tuple(_entry(f"Человек {i}", relation="брат") for i in range(1 + count))
    head = "Семья «С»:\n\nЧеловек 0 — 03.02.1990, 36 лет (брат)"
    tail = f"… и ещё {count} {word}"

    # Лимит ровно на одного человека и хвост: второй человек не помещается.
    result = format_persons_list(
        FamilyPersons("С", True, entries), max_length=len(head + "\n\n" + tail)
    )

    assert result == head + "\n\n" + tail


def test_format_persons_list_tail_accounts_for_its_own_length() -> None:
    entries = tuple(_entry(f"Человек {i}") for i in range(10))
    full = format_persons_list(FamilyPersons("С", True, entries[:9]), max_length=10**6)

    # Девять людей влезают ровно, но с хвостом «и ещё 1 человек» — уже нет.
    result = format_persons_list(FamilyPersons("С", True, entries), max_length=len(full))

    assert len(result) <= len(full)
    assert result.endswith("… и ещё 2 человека")
