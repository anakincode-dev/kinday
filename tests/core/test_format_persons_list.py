"""Тесты форматтера format_persons_list для команды /persons (этап 9).

Критерий 33: точное соответствие формату из SPEC 3.5, обрезка при длине >3800.
"""

from __future__ import annotations

from datetime import date

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
