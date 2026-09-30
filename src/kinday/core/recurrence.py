"""Следующая дата события: ежегодное повторение и правило 29 февраля."""

from __future__ import annotations

from calendar import isleap
from datetime import date


def _occurrence_in_year(event_date: date, year: int) -> date:
    if event_date.month == 2 and event_date.day == 29 and not isleap(year):
        return date(year, 2, 28)
    return date(year, event_date.month, event_date.day)


def next_occurrence(event_date: date, after: date) -> date:
    """Ближайшая дата события на месте `after` или позже.

    29 февраля в невисокосный год даёт 28 февраля того года.
    """
    candidate = _occurrence_in_year(event_date, after.year)
    if candidate >= after:
        return candidate
    return _occurrence_in_year(event_date, after.year + 1)


def age_on(event_date: date, occurrence: date) -> int:
    """Возраст или номер годовщины на дату наступления события."""
    return occurrence.year - event_date.year


def age_as_of(birth_date: date, today: date) -> int:
    """Полный возраст на дату. День рождения 29.02 в невисокосный год считается за 28.02."""
    age = today.year - birth_date.year
    # Проверить, уже ли прошёл день рождения в этом году
    birth_this_year = _occurrence_in_year(birth_date, today.year)
    if today < birth_this_year:
        age -= 1
    return age
