"""Тесты next_occurrence и age_on: ежегодное повторение и правило 29 февраля."""

from __future__ import annotations

from datetime import date

from kinday.core.recurrence import age_on, next_occurrence


def test_next_occurrence_regular_date_same_year() -> None:
    assert next_occurrence(date(1990, 6, 15), date(2027, 1, 1)) == date(2027, 6, 15)


def test_next_occurrence_regular_date_rolls_to_next_year() -> None:
    assert next_occurrence(date(1990, 6, 15), date(2027, 7, 1)) == date(2028, 6, 15)


def test_next_occurrence_on_exact_date_returns_same_date() -> None:
    assert next_occurrence(date(1990, 6, 15), date(2027, 6, 15)) == date(2027, 6, 15)


def test_next_occurrence_feb29_in_non_leap_year() -> None:
    """Критерий приёмки 4: невисокосный год -> напоминание (дата события) 28 февраля."""
    assert next_occurrence(date(2000, 2, 29), date(2023, 1, 1)) == date(2023, 2, 28)


def test_next_occurrence_feb29_in_leap_year() -> None:
    """Критерий приёмки 4: високосный год -> дата события 29 февраля."""
    assert next_occurrence(date(2000, 2, 29), date(2024, 1, 1)) == date(2024, 2, 29)


def test_next_occurrence_feb29_rolls_from_leap_to_non_leap() -> None:
    assert next_occurrence(date(2000, 2, 29), date(2024, 3, 1)) == date(2025, 2, 28)


def test_age_on_regular_birthday() -> None:
    assert age_on(date(1980, 3, 1), date(2027, 3, 1)) == 47


def test_age_on_feb29_birthday_in_non_leap_year() -> None:
    assert age_on(date(2000, 2, 29), date(2023, 2, 28)) == 23
