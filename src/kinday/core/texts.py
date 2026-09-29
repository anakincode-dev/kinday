"""Формулировки родства и текстов напоминаний с учётом пола. Русский язык, без переводов."""

from __future__ import annotations

from datetime import date
from typing import Any

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
    "parent": {Gender.MALE: "ваш отец", Gender.FEMALE: "ваша мать"},
    "child": {Gender.MALE: "ваш сын", Gender.FEMALE: "ваша дочь"},
    "spouse": {Gender.MALE: "ваш муж", Gender.FEMALE: "ваша жена"},
    "sibling": {Gender.MALE: "ваш брат", Gender.FEMALE: "ваша сестра"},
    "grandparent": {Gender.MALE: "ваш дедушка", Gender.FEMALE: "ваша бабушка"},
    "grandchild": {Gender.MALE: "ваш внук", Gender.FEMALE: "ваша внучка"},
    "parent_sibling": {Gender.MALE: "ваш дядя", Gender.FEMALE: "ваша тётя"},
    "sibling_child": {Gender.MALE: "ваш племянник", Gender.FEMALE: "ваша племянница"},
    "grandparent_parent": {Gender.MALE: "ваш прадедушка", Gender.FEMALE: "ваша прабабушка"},
    "grandchild_child": {Gender.MALE: "ваш правнук", Gender.FEMALE: "ваша правнучка"},
}


def relation_word(relation: str, gender: Gender | None) -> str:
    """«ваш отец» / «ваша сестра» и т.п. — именительный падеж для вставки в reminder_text.

    Именительный, а не родительный, выбран намеренно: имя героя события перед
    этой фразой не склоняется (русские имена и особенно фамилии склоняются
    непредсказуемо), поэтому и связка после запятой должна остаться в форме,
    не требующей согласования с падежом имени. Заглушка пола не имеет и в
    текстах не участвует: gender=None даёт пустую строку, как и неизвестная
    категория родства.
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


def _years_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "года"
    return "лет"


def _format_date(value: date) -> str:
    return f"{value.day} {_MONTHS_GENITIVE[value.month - 1]}"


def _lead(days_until: int) -> str:
    if days_until == 0:
        return "Сегодня"
    if days_until == 1:
        return "Завтра"
    return f"Через {days_until} {_days_word(days_until)}"


def reminder_text(
    name: str,
    relation_phrase: str,
    days_until: int,
    event_title: str,
    is_birthday: bool,
    is_recurring_yearly: bool,
    event_date: date,
    years: int,
) -> str:
    """Текст напоминания в одном из трёх форматов (SPEC 3.3, 4.2):

    - день рождения: «Через 7 дней день рождения — Пётр, ваш отец.
      1 марта, исполнится 47.»
    - ежегодное событие: «Через 7 дней: Годовщина свадьбы — Пётр, ваш отец.
      1 марта, 25 лет.»
    - разовое событие: «Через 7 дней: Выпускной — Марина. 3 июня.»

    `name` — имя героя события в именительном падеже, не склоняется (см.
    relation_word). Если родство не выводится, `relation_phrase` пустая и
    остаётся только имя, без запятой. `days_until` — как формулировка
    «через N дней» считается в момент отправки, а не материализации
    (SPEC, критерий 13). `years` не участвует в тексте разового события
    (`is_recurring_yearly=False`), но параметр обязателен: вызывающий код
    (services.py) в этом случае годы не считает и передаёт заглушечное 0.
    """
    subject = f"{name}, {relation_phrase}" if relation_phrase else name
    lead = _lead(days_until)
    formatted_date = _format_date(event_date)

    if is_birthday:
        return f"{lead} день рождения — {subject}. {formatted_date}, исполнится {years}."
    if is_recurring_yearly:
        return f"{lead}: {event_title} — {subject}. {formatted_date}, {years} {_years_word(years)}."
    return f"{lead}: {event_title} — {subject}. {formatted_date}."


def format_persons_list(
    family_name: str,
    persons_data: list[tuple[Any, str, list[Any]]],
    current_date: date,
) -> str:
    """Форматтер списка людей семьи для команды /persons (этап 9, SPEC 3.5).

    Возвращает одно сообщение не длиннее 3800 символов. Если люди не помещаются,
    список обрывается на границе человека и заканчивается строкой
    «… и ещё N человек» с верным склонением (критерий 33).

    persons_data — список кортежей (Person, relation_category, events):
    - Person: запись человека (должна иметь name, birth_date, gender)
    - relation_category: текст родства или «(это вы)»
    - events: список событий (должны иметь title, date, is_recurring_yearly)
    """

    lines: list[str] = [f"Семья «{family_name}»:\n"]
    total_length = len(lines[0])
    skipped_count = 0

    for person, relation_text, events in persons_data:
        # Строка человека: имя, дата, возраст, родство
        age = _calculate_age(person.birth_date, current_date)
        birth_str = person.birth_date.strftime("%d.%m.%Y")
        age_str = f"{age} {_years_word(age)}"
        relation_str = (
            f" ({relation_text})"
            if relation_text and relation_text != "(это вы)"
            else f" {relation_text}"
        )

        person_line = f"{person.name} — {birth_str}, {age_str}{relation_str}"

        # События человека
        event_lines: list[str] = []
        for event in events:
            if event.is_recurring_yearly:
                year_suffix = f" (с {event.date.year})"
                date_str = f"{event.date.day:02d}.{event.date.month:02d}"
                event_line = f"  • {date_str} — {event.title}{year_suffix}"
            else:
                date_str = f"{event.date.day:02d}.{event.date.month:02d}.{event.date.year}"
                event_line = f"  • {date_str} — {event.title}"
            event_lines.append(event_line)

        person_section = person_line + ("\n" + "\n".join(event_lines) if event_lines else "")

        # Проверить, поместится ли в 3800
        candidate_length = total_length + len(person_section) + 1  # +1 для перевода строки
        if candidate_length > 3800 and lines[0] != lines[-1]:  # Если не первый человек
            skipped_count += 1
            continue

        lines.append(person_section)
        total_length += len(person_section) + 1

    # Если были пропущенные люди, добавить "… и ещё"
    if skipped_count > 0:
        remaining_count = len(persons_data) - len(lines) + 1
        if remaining_count < 0:
            remaining_count = skipped_count
        dots_line = f"\n… и ещё {remaining_count} {_people_word(remaining_count)}"
        lines.append(dots_line)

    return "\n".join(lines)


def _calculate_age(birth_date: date | None, current_date: date) -> int:
    """Вычислить возраст на текущую дату."""
    if birth_date is None:
        return 0
    age = current_date.year - birth_date.year
    if (current_date.month, current_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def _people_word(n: int) -> str:
    """Склонение слова 'человек' по количеству."""
    if n % 10 == 1 and n % 100 != 11:
        return "человек"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "человека"
    return "человек"
