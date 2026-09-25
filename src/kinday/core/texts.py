"""Формулировки родства и текстов напоминаний с учётом пола. Русский язык, без переводов."""

from __future__ import annotations

from datetime import date

from kinday.core.models import Gender

_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

_RELATION_WORDS: dict[str, dict[Gender, str]] = {
    "parent": {Gender.MALE: "вашего отца", Gender.FEMALE: "вашей матери"},
    "child": {Gender.MALE: "вашего сына", Gender.FEMALE: "вашей дочери"},
    "spouse": {Gender.MALE: "вашего мужа", Gender.FEMALE: "вашей жены"},
    "sibling": {Gender.MALE: "вашего брата", Gender.FEMALE: "вашей сестры"},
    "grandparent": {Gender.MALE: "вашего дедушки", Gender.FEMALE: "вашей бабушки"},
    "grandchild": {Gender.MALE: "вашего внука", Gender.FEMALE: "вашей внучки"},
    "parent_sibling": {Gender.MALE: "вашего дяди", Gender.FEMALE: "вашей тёти"},
    "sibling_child": {Gender.MALE: "вашего племянника", Gender.FEMALE: "вашей племянницы"},
    "grandparent_parent": {Gender.MALE: "вашего прадедушки", Gender.FEMALE: "вашей прабабушки"},
    "grandchild_child": {Gender.MALE: "вашего правнука", Gender.FEMALE: "вашей правнучки"},
}


def relation_word(relation: str, gender: Gender | None) -> str:
    """«вашего отца» / «вашей сестры» и т.п. — родительный падеж для вставки в reminder_text.

    Заглушка пола не имеет и в текстах не участвует: gender=None даёт пустую
    строку, как и неизвестная категория родства.
    """
    if gender is None:
        return ""
    return _RELATION_WORDS.get(relation, {}).get(gender, "")


def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def _format_date(value: date) -> str:
    return f"{value.day} {_MONTHS_GENITIVE[value.month - 1]}"


def reminder_text(
    name: str,
    relation_phrase: str,
    days_until: int,
    event_date: date,
    turning_age: int,
) -> str:
    """«Через 7 дней день рождения у Петра, вашего отца. 1 марта, исполнится 47».

    Если родство не выводится, остаётся только имя. `days_until` — как формулировка
    «через N дней» считается в момент отправки, а не материализации (SPEC, критерий 13).
    """
    subject = f"{name}, {relation_phrase}" if relation_phrase else name
    lead = "Сегодня" if days_until == 0 else f"Через {days_until} {_days_word(days_until)}"
    return (
        f"{lead} день рождения у {subject}. {_format_date(event_date)}, исполнится {turning_age}."
    )
