"""Формулировки родства и текстов напоминаний с учётом пола. Русский язык, без переводов."""

from __future__ import annotations

from datetime import date

from kinday.core.models import Gender


def relation_word(relation: str, gender: Gender | None) -> str:
    """«ваш брат» / «ваша сестра» и т.п. Заглушка пола не имеет и в текстах не участвует."""
    raise NotImplementedError


def reminder_text(
    name: str,
    relation_phrase: str,
    days_until: int,
    event_date: date,
    turning_age: int,
) -> str:
    """«Через 7 дней день рождения у Петра, вашего отца. 1 марта, исполнится 47».

    Если родство не выводится, остаётся только имя.
    """
    raise NotImplementedError
