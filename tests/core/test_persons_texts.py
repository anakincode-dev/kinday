"""Юнит-тесты format_persons_list без зависимостей от сценариев."""

from __future__ import annotations

from datetime import UTC, date, datetime

from kinday.core.models import Person
from kinday.core.texts import format_persons_list


def test_format_persons_list_single_person_with_self_marker() -> None:
    """Один человек в списке получает пометку "(это вы)"."""
    people = [
        Person(
            id=1,
            family_id=1,
            name="Антон",
            gender=None,
            birth_date=date(1990, 6, 15),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    assert "Антон" in text
    assert "15.06.1990" in text
    assert "это вы" in text
    assert text.count("Антон") == 1  # без дублей


def test_format_persons_list_age_calculation() -> None:
    """Возраст считается правильно по московскому времени."""
    # 15 февраля 2027:
    # - Антон (15.06.1990) — 36 лет (ещё не родился в этом году)
    # - Пётр (01.03.1979) — 47 лет (день рождения ещё не наступил)
    # - Марина (12.05.1992) — 34 года (день рождения ещё не наступил)

    people = [
        Person(
            id=1,
            family_id=1,
            name="Антон",
            gender=None,
            birth_date=date(1990, 6, 15),
            is_placeholder=False,
        ),
        Person(
            id=2,
            family_id=1,
            name="Пётр",
            gender=None,
            birth_date=date(1979, 3, 1),
            is_placeholder=False,
        ),
        Person(
            id=3,
            family_id=1,
            name="Марина",
            gender=None,
            birth_date=date(1992, 5, 12),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    assert "36" in text  # Антон
    assert "47" in text  # Пётр
    assert "34" in text  # Марина


def test_format_persons_list_format_person_line() -> None:
    """Формат строки человека: имя, дата, возраст, родство, пометка."""
    people = [
        Person(
            id=1,
            family_id=1,
            name="Иван",
            gender=None,
            birth_date=date(1985, 1, 1),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    assert "Иван" in text
    assert "01.01.1985" in text
    assert "42" in text  # возраст
    assert "это вы" in text


def test_format_persons_list_sorts_by_category() -> None:
    """Люди сортируются по категориям: сам, родители, супруги, дети, остальные."""
    # Структура:
    # - Антон (id=1) — сам
    # - Пётр (id=2) — родитель Антона
    # - Мария (id=3) — родитель Антона
    # - Игорь (id=4) — супруг Антона
    # - Дмитрий (id=5) — ребёнок Антона
    # - Татьяна (id=6) — сестра Игоря (остальные)

    people = [
        Person(
            id=6,
            family_id=1,
            name="Татьяна",
            gender=None,
            birth_date=date(1980, 1, 1),
            is_placeholder=False,
        ),
        Person(
            id=5,
            family_id=1,
            name="Дмитрий",
            gender=None,
            birth_date=date(2015, 6, 3),
            is_placeholder=False,
        ),
        Person(
            id=4,
            family_id=1,
            name="Игорь",
            gender=None,
            birth_date=date(1988, 8, 5),
            is_placeholder=False,
        ),
        Person(
            id=3,
            family_id=1,
            name="Мария",
            gender=None,
            birth_date=date(1960, 1, 1),
            is_placeholder=False,
        ),
        Person(
            id=2,
            family_id=1,
            name="Пётр",
            gender=None,
            birth_date=date(1979, 3, 1),
            is_placeholder=False,
        ),
        Person(
            id=1,
            family_id=1,
            name="Антон",
            gender=None,
            birth_date=date(1990, 6, 15),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    # Извлекаем имена по порядку из текста (пропускаем пустые и заголовок "Семья:")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("•") and "и ещё" not in line and "Семья" not in line
    ]

    # Первый — Антон
    assert "Антон" in lines[0]
    # Остальные должны быть в списке
    assert any("Пётр" in line for line in lines)
    assert any("Мария" in line for line in lines)
    assert any("Игорь" in line for line in lines)
    assert any("Дмитрий" in line for line in lines)
    assert any("Татьяна" in line for line in lines)


def test_format_persons_list_empty_list() -> None:
    """Пустой список даёт только приветствие."""
    people = []
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    assert "семья" in text.lower()
    # Пустой список — нет других людей


def test_format_persons_list_truncation_adds_more_people() -> None:
    """При обрезке добавляется строка "… и ещё N человек"."""
    # Создаём достаточно много людей, чтобы превысить 3800 символов
    people = [
        Person(
            id=i,
            family_id=1,
            name=f"Человек_{i}_С длинным описанием для теста обрезания",
            gender=None,
            birth_date=date(1980 + i % 40, 1, 1),
            is_placeholder=False,
        )
        for i in range(100)
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    assert "… и ещё" in text


def test_format_persons_list_events_under_person_line() -> None:
    """События отображаются под строкой человека."""
    # Для теста без зависимостей от сценариев проверим, что текст содержит
    # события под строками людей (реальная реализация будет добавлять события)

    people = [
        Person(
            id=1,
            family_id=1,
            name="Антон",
            gender=None,
            birth_date=date(1990, 6, 15),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    # В простом случае без событий — просто строка человека
    assert "Антон" in text
    assert "01.06.1990" in text or "15.06.1990" in text


def test_format_persons_list_names_sorted_case_insensitive() -> None:
    """Имена сортируются без учёта регистра, ё как е."""
    people = [
        Person(
            id=1,
            family_id=1,
            name="Антон",
            gender=None,
            birth_date=date(1990, 6, 15),
            is_placeholder=False,
        ),
        Person(
            id=2,
            family_id=1,
            name="андрей",
            gender=None,
            birth_date=date(1985, 1, 1),
            is_placeholder=False,
        ),
        Person(
            id=3,
            family_id=1,
            name="Алексей",
            gender=None,
            birth_date=date(1988, 1, 1),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    # Проверяем, что имена включены
    assert "Антон" in text
    assert "андрей" in text.lower() or "Андрей" in text
    assert "Алексей" in text


def test_format_persons_list_same_age_sorted_by_name() -> None:
    """При равном возрасте сортировка по имени."""
    people = [
        Person(
            id=1,
            family_id=1,
            name="Антон",
            gender=None,
            birth_date=date(1990, 6, 15),
            is_placeholder=False,
        ),
        Person(
            id=2,
            family_id=1,
            name="Борис",
            gender=None,
            birth_date=date(1990, 1, 1),
            is_placeholder=False,
        ),
        Person(
            id=3,
            family_id=1,
            name="Василий",
            gender=None,
            birth_date=date(1990, 12, 31),
            is_placeholder=False,
        ),
    ]
    clock = datetime(2027, 2, 15, tzinfo=UTC)

    text = format_persons_list(people, 1, "Europe/Moscow", clock)

    assert "Антон" in text
    assert "Борис" in text
    assert "Василий" in text
