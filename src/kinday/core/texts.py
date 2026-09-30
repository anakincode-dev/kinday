"""Формулировки родства и текстов напоминаний с учётом пола. Русский язык, без переводов."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from kinday.core.models import Event, Gender


@dataclass(frozen=True, slots=True)
class PersonEntry:
    """Запись человека в списке людей семьи для команды /persons."""

    name: str
    birth_date: date
    age: int
    relation: str
    is_self: bool
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class FamilyPersons:
    """Список людей семьи, отформатированный для команды /persons."""

    family_name: str
    is_owner: bool
    entries: tuple[PersonEntry, ...]


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


def relation_word_bare(relation: str, gender: Gender | None) -> str:
    """Короткое родство для /persons (например, «отец», «сестра»).

    Без префиксов типа «ваш»/«ваша». Для себя возвращает пустую строку.
    """
    if gender is None or not relation:
        return ""

    words = {
        "parent": {Gender.MALE: "отец", Gender.FEMALE: "мать"},
        "child": {Gender.MALE: "сын", Gender.FEMALE: "дочь"},
        "spouse": {Gender.MALE: "муж", Gender.FEMALE: "жена"},
        "sibling": {Gender.MALE: "брат", Gender.FEMALE: "сестра"},
        "grandparent": {Gender.MALE: "дедушка", Gender.FEMALE: "бабушка"},
        "grandchild": {Gender.MALE: "внук", Gender.FEMALE: "внучка"},
        "parent_sibling": {Gender.MALE: "дядя", Gender.FEMALE: "тётя"},
        "sibling_child": {Gender.MALE: "племянник", Gender.FEMALE: "племянница"},
    }
    return words.get(relation, {}).get(gender, "")


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


def _people_word(n: int) -> str:
    """Склонение слова 'человек' по количеству."""
    if n % 10 == 1 and n % 100 != 11:
        return "человек"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "человека"
    return "человек"


def format_persons_list(data: FamilyPersons, max_length: int = 3800) -> str:
    """Форматтер списка людей семьи для команды /persons (SPEC 3.5).

    Возвращает текст не длиннее max_length символов. Если люди не помещаются,
    список обрывается на границе человека и заканчивается строкой
    «… и ещё N человек» с верным склонением.
    """
    lines: list[str] = []

    # Заголовок
    header = f"Семья «{data.family_name}»:"
    lines.append(header)
    lines.append("")

    # Форматировать людей
    formatted_entries: list[str] = []
    for entry in data.entries:
        # Строка человека
        relation_part = f" ({entry.relation})" if entry.relation else ""
        if entry.is_self:
            relation_part = " (это вы)"

        person_line = (
            f"{entry.name} — {entry.birth_date.strftime('%d.%m.%Y')}, "
            f"{entry.age} {_years_word(entry.age)}{relation_part}"
        )

        # События человека
        event_lines: list[str] = []
        for event in entry.events:
            if event.is_recurring_yearly:
                year_suffix = f" (с {event.date.year})"
                date_str = event.date.strftime("%d.%m")
                event_line = f"  • {date_str} — {event.title}{year_suffix}"
            else:
                date_str = event.date.strftime("%d.%m.%Y")
                event_line = f"  • {date_str} — {event.title}"
            event_lines.append(event_line)

        person_section = person_line
        if event_lines:
            person_section += "\n" + "\n".join(event_lines)

        formatted_entries.append(person_section)

    # Добавить людей до лимита
    skipped_count = 0
    for i, person_section in enumerate(formatted_entries):
        candidate = "\n".join([*lines, person_section, ""])
        if len(candidate) > max_length and i > 0:
            skipped_count = len(formatted_entries) - i
            break
        lines.append(person_section)
        lines.append("")

    # Если только один человек (сам)
    if len(data.entries) == 1:
        # Убрать последнюю пустую строку
        if lines and lines[-1] == "":
            lines.pop()
        lines.append("")
        if data.is_owner:
            lines.append("Добавьте родственников командой /add_person.")
        else:
            lines.append("Других людей в семье пока нет.")

    # Если были пропущенные люди
    if skipped_count > 0:
        lines.append(f"… и ещё {skipped_count} {_people_word(skipped_count)}")

    return "\n".join(lines)
