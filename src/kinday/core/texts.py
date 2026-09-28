"""Формулировки родства и текстов напоминаний с учётом пола. Русский язык, без переводов."""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

from kinday.core.models import Event, EventKind, Gender, Person

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


class PersonInfo(NamedTuple):
    """Информация о человеке для списка семьи (SPEC 3.5)."""

    person: Person
    events: list[Event]
    relation_text: str | None
    age: int | None


def _format_person_with_events(
    name: str,
    birth_date: date | None,
    age: int | None,
    relation_text: str | None,
    events: list[Event],
    is_current_user: bool,
) -> str:
    """Форматирует строку человека в списке семьи.

    - Имя, дата рождения ДД.ММ.ГГГГ, возраст на сегодня по часовому поясу пользователя,
      родство одним словом («отец», «сестра», «дядя»).
    - Под строкой — события человека, кроме дня рождения.
    """
    lines: list[str] = []

    # Основная строка человека
    date_part = f", {birth_date.strftime('%d.%m.%Y')}" if birth_date else ""
    age_part = f", {age} {_years_word(age)}" if age is not None else ""
    relation_part = f" ({relation_text})" if relation_text else ""
    current_user_part = " (это вы)" if is_current_user else ""

    lines.append(f"{name}{date_part}{age_part}{relation_part}{current_user_part}")

    # События (кроме дня рождения)
    for event in events:
        if event.kind == EventKind.BIRTHDAY:
            continue
        formatted_date = _format_date(event.date)
        if event.is_recurring_yearly:
            lines.append(f"  • {formatted_date} — {event.title} (с {event.date.year})")
        else:
            lines.append(f"  • {event.date.strftime('%d.%m.%Y')} — {event.title}")

    return "\n".join(lines)


def format_persons_list(
    persons: list[PersonInfo],
    family_name: str,
    current_user_person_id: int,
    max_length: int = 3800,
) -> str:
    """Формирует текст списка людей семьи (SPEC 3.5, критерий 33).

    - Порядок: сам пользователь, его родители, супруги, дети, остальные
      (группы определяются прямыми связями `parent_of` и `spouse_of`).
    - Один человек = одна строка + события (если есть).
    - Если текст длиннее `max_length`, обрывается на границе человека с «… и ещё N человек».
    """
    lines: list[str] = [f"Семья «{family_name}»:"]
    total_count = len(persons)

    # Собираем строки, пока не достигнем лимита
    current_length = 0
    shown_count = 0
    overflow_count = 0

    for info in persons:
        person_str = _format_person_with_events(
            name=info.person.name or "Без имени",
            birth_date=info.person.birth_date,
            age=info.age,
            relation_text=info.relation_text,
            events=info.events,
            is_current_user=info.person.id == current_user_person_id,
        )

        # Добавляем разделитель перед строкой (кроме первой после заголовка)
        new_lines = [person_str]
        if lines[-1] != f"Семья «{family_name}»:":  # Не первая группа
            new_lines.insert(0, "")  # Пустая строка-разделитель

        for new_line in new_lines:
            if current_length + len(new_line) + 1 > max_length:
                overflow_count = total_count - shown_count
                break
            current_length += len(new_line) + 1  # +1 для \n

        if overflow_count > 0:
            break

        lines.append(person_str)
        current_length += 1  # разделитель

    # Если был обрыв, добавляем счётчик
    if overflow_count > 0:
        # Склонение "человек/человека/человек"
        if overflow_count % 10 == 1 and overflow_count % 100 != 11:
            people_word = "человек"
        elif 2 <= overflow_count % 10 <= 4 and not 12 <= overflow_count % 100 <= 14:
            people_word = "человека"
        else:
            people_word = "человек"
        lines.append(f"… и ещё {overflow_count} {people_word}")

    return "\n".join(lines)
