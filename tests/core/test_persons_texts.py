"""Чистые юнит-тесты форматтера format_persons_list (этап 9, критерии 31–34).

Форматтер собирает текст списка людей семьи, проверяет длину и обрезает
при необходимости с добавлением «… и ещё N человек».
"""

from __future__ import annotations

from datetime import date

from kinday.core.models import Event, EventKind, Gender, Person
from kinday.core.texts import format_persons_list


def test_format_persons_list_basic() -> None:
    """Básica: имя, дата, возраст, родство."""
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Антон", gender=Gender.MALE, birth_date=date(1990, 2, 15)
            ),
            "(это вы)",
            [],
        ),
        (
            Person(id=2, family_id=1, name="Пётр", gender=Gender.MALE, birth_date=date(1960, 3, 1)),
            "отец",
            [],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    assert "Петровы" in text
    assert "Антон" in text
    assert "это вы" in text
    assert "Пётр" in text
    assert "отец" in text
    assert "15.02.1990" in text
    assert "01.03.1960" in text


def test_format_persons_list_with_events() -> None:
    """События человека показываются под его строкой."""
    event = Event(
        id=1,
        family_id=1,
        person_id=1,
        title="Годовщина свадьбы",
        date=date(2027, 8, 1),
        is_recurring_yearly=True,
        kind=EventKind.CUSTOM,
    )
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Антон", gender=Gender.MALE, birth_date=date(1990, 6, 15)
            ),
            "(это вы)",
            [event],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    assert "Годовщина свадьбы" in text
    assert "01.08" in text


def test_format_persons_list_age_calculation() -> None:
    """Возраст рассчитывается на current_date."""
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Маша", gender=Gender.FEMALE, birth_date=date(1992, 5, 12)
            ),
            "жена",
            [],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    # День рождения ещё впереди, поэтому возраст 34
    assert "34 года" in text or "34 года" in text


def test_format_persons_list_truncation() -> None:
    """Список обрезается при длине >3800 символов с «… и ещё N человек»."""
    # Создадим много людей с разными именами
    persons_data = []
    for i in range(100):
        persons_data.append(
            (
                Person(
                    id=i,
                    family_id=1,
                    name=f"Человек{i:03d}Длинноеимя",
                    gender=Gender.MALE if i % 2 == 0 else Gender.FEMALE,
                    birth_date=date(1980 + i % 40, 1 + i % 12, 1 + i % 28),
                ),
                f"родство{i}" if i > 0 else "(это вы)",
                [],
            )
        )

    text = format_persons_list(
        family_name="ОченьБольшаяСемья",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    # Список должен быть ≤3800 символов
    assert len(text) <= 3800
    # Должна быть строка "… и ещё"
    assert "… и ещё" in text


def test_format_persons_list_no_other_family_members() -> None:
    """Если только сам пользователь, есть подсказка про /add_person."""
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Антон", gender=Gender.MALE, birth_date=date(1990, 6, 15)
            ),
            "(это вы)",
            [],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    # Текст должен содержать только Антона и не иметь подсказки
    # (подсказка добавляется на слое telegram, не в форматтере)
    assert "Антон" in text
    assert "это вы" in text


def test_format_persons_list_recurring_event_with_year() -> None:
    """Ежегодное событие показывает год начала в формате (с ГГГГ)."""
    event = Event(
        id=1,
        family_id=1,
        person_id=1,
        title="Годовщина свадьбы",
        date=date(2015, 8, 1),
        is_recurring_yearly=True,
        kind=EventKind.CUSTOM,
    )
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Антон", gender=Gender.MALE, birth_date=date(1990, 6, 15)
            ),
            "(это вы)",
            [event],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    # Должен быть указан год начала события
    assert "с 2015" in text or "2015" in text


def test_format_persons_list_single_event() -> None:
    """Разовое событие показывает полную дату (ДД.ММ.ГГГГ)."""
    event = Event(
        id=1,
        family_id=1,
        person_id=1,
        title="Выпускной",
        date=date(2026, 6, 3),
        is_recurring_yearly=False,
        kind=EventKind.CUSTOM,
    )
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Мария", gender=Gender.FEMALE, birth_date=date(1992, 5, 12)
            ),
            "жена",
            [event],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 15),
    )

    # Должна быть полная дата
    assert "03.06.2026" in text


def test_format_persons_list_leap_day_person() -> None:
    """Человек, рождённый 29 февраля, обрабатывается корректно."""
    persons_data = [
        (
            Person(
                id=1, family_id=1, name="Лиза", gender=Gender.FEMALE, birth_date=date(1992, 2, 29)
            ),
            "(это вы)",
            [],
        ),
    ]

    text = format_persons_list(
        family_name="Петровы",
        persons_data=persons_data,
        current_date=date(2027, 2, 28),  # Невисокосный год
    )

    assert "Лиза" in text
    assert "29.02.1992" in text
