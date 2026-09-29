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
    """Возраст на `today` с правилом 29 февраля (критерий 31).

    В невисокосный год день рождения 29 февраля считается наступившим 28 февраля,
    поэтому в этот день человек становится старше. `age_on` считает полный год
    только на дату наступления; до неё из годовщины вычитается единица.
    """
    occurrence = next_occurrence(birth_date, today)
    years = age_on(birth_date, occurrence)
    return years if occurrence == today else years - 1
