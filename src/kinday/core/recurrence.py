"""Следующая дата события: ежегодное повторение и правило 29 февраля."""

from __future__ import annotations

from datetime import date


def next_occurrence(event_date: date, after: date) -> date:
    """Ближайшая дата события на месте `after` или позже.

    29 февраля в невисокосный год даёт 28 февраля того года.
    """
    raise NotImplementedError


def age_on(event_date: date, occurrence: date) -> int:
    """Возраст или номер годовщины на дату наступления события."""
    raise NotImplementedError
