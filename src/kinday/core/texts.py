"""Формулировки родства и текстов напоминаний с учётом пола. Русский язык, без переводов."""

from __future__ import annotations

from dataclasses import dataclass
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

_RELATION_WORDS_BARE: dict[str, dict[Gender, str]] = {
    "parent": {Gender.MALE: "отец", Gender.FEMALE: "мать"},
    "child": {Gender.MALE: "сын", Gender.FEMALE: "дочь"},
    "spouse": {Gender.MALE: "муж", Gender.FEMALE: "жена"},
    "sibling": {Gender.MALE: "брат", Gender.FEMALE: "сестра"},
    "grandparent": {Gender.MALE: "дедушка", Gender.FEMALE: "бабушка"},
    "grandchild": {Gender.MALE: "внук", Gender.FEMALE: "внучка"},
    "parent_sibling": {Gender.MALE: "дядя", Gender.FEMALE: "тётя"},
    "sibling_child": {Gender.MALE: "племянник", Gender.FEMALE: "племянница"},
    "grandparent_parent": {Gender.MALE: "прадедушка", Gender.FEMALE: "прабабушка"},
    "grandchild_child": {Gender.MALE: "правнук", Gender.FEMALE: "правнучка"},
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
    """«отец» / «сестра» и т.п. — для вывода в /persons без префикса «ваш/ваша»."""
    if gender is None:
        return ""
    return _RELATION_WORDS_BARE.get(relation, {}).get(gender, "")


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


PERSONS_LIST_MAX_LENGTH = 3800

_ONLY_SELF_OWNER_HINT = "Добавьте родственников командой /add_person."
_ONLY_SELF_MEMBER_HINT = "Других людей пока нет."


@dataclass(frozen=True, slots=True)
class PersonEventLine:
    """Строка события под человеком в /persons, кроме дня рождения (критерий 32)."""

    title: str
    event_date: date
    is_recurring_yearly: bool


@dataclass(frozen=True, slots=True)
class PersonListItem:
    """Один человек в списке /persons: данные, которые уже посчитал сервис."""

    name: str
    birth_date: date
    age: int
    relation_word: str
    is_self: bool
    events: tuple[PersonEventLine, ...]


def _people_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "человек"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "человека"
    return "человек"


def _format_event_line(event: PersonEventLine) -> str:
    if event.is_recurring_yearly:
        return (
            f"  • {event.event_date.strftime('%d.%m')} — {event.title} (с {event.event_date.year})"
        )
    return f"  • {event.event_date.strftime('%d.%m.%Y')} — {event.title}"


def _person_block(item: PersonListItem) -> str:
    line = (
        f"{item.name} — {item.birth_date.strftime('%d.%m.%Y')}, {item.age} {_years_word(item.age)}"
    )
    if item.is_self:
        line += " (это вы)"
    elif item.relation_word:
        line += f" ({item.relation_word})"
    if not item.events:
        return line
    return line + "\n" + "\n".join(_format_event_line(event) for event in item.events)


async def format_persons_list(
    persons: list,  # list[Person]
    viewing_person_id: int,
    timezone: str,
    now,  # datetime
    relation_repo=None,  # RelationRepo
    event_repo=None,  # EventRepo
    max_length: int = PERSONS_LIST_MAX_LENGTH,
) -> str:
    """Собирает одно сообщение /persons не длиннее `max_length` символов (SPEC 3.5, критерии 29-34).

    Параметры:
    - persons: люди семьи в нужном порядке
    - viewing_person_id: ID зрителя (для пометки "это вы" и определения родства)
    - timezone: часовой пояс зрителя (для расчёта возраста)
    - now: текущий момент в UTC (для расчёта возраста)
    - relation_repo: репозиторий отношений (для получения типа родства)
    - event_repo: репозиторий событий (для получения событий человека)

    Обрыв — на границе человека, с «… и ещё N человек». Если в семье только
    сам зритель, показывает подсказку.
    """
    from zoneinfo import ZoneInfo

    from kinday.core.recurrence import age_as_of
    from kinday.core.relations import infer_relation_text

    # Преобразуем UTC в локальное время зрителя для расчёта возраста
    local_tz = ZoneInfo(timezone)
    local_now = now.astimezone(local_tz)
    today_for_viewer = local_now.date()

    # Сортируем по ID и исключаем заглушки
    sorted_persons = sorted((p for p in persons if not p.is_placeholder), key=lambda p: p.id)

    # Получаем отношения и события
    relations = []
    all_events = []
    if len(sorted_persons) > 0:
        if relation_repo:
            relations = await relation_repo.list_by_family(sorted_persons[0].family_id)
        if event_repo:
            all_events = await event_repo.list_by_family(sorted_persons[0].family_id)

    # Создаём словарь людей для infer_relation_text
    people_dict = {p.id: p for p in sorted_persons}

    blocks: list[str] = []
    for person in sorted_persons:
        # Вычисляем возраст
        age = age_as_of(person.birth_date, today_for_viewer) if person.birth_date else 0

        # Форматируем строку человека
        line = f"{person.name} — {person.birth_date.strftime('%d.%m.%Y')}, {age} {_years_word(age)}"
        if person.id == viewing_person_id:
            line += " (это вы)"
        else:
            # Получаем слово родства через infer_relation_text
            rel_phrase = infer_relation_text(viewing_person_id, person.id, people_dict, relations)
            if rel_phrase:
                # rel_phrase имеет вид "ваш отец", нужно оставить только "отец"
                words = rel_phrase.split()
                if len(words) > 1:
                    rel_word = words[-1]
                    line += f", {rel_word}"

        # Получаем события человека (кроме дня рождения)
        person_events = [e for e in all_events if e.person_id == person.id and not e.is_birthday]
        for event in person_events:
            event_line = f"{event.date.month}.{event.date.day:02d}"
            if not event.is_recurring_yearly:
                event_line += f".{event.date.year}"
                line += f"\n{event_line} — {event.title}"
            else:
                event_line += f" — {event.title} (с {event.date.year})"
                line += f"\n{event_line}"

        blocks.append(line)

    # Собираем текст с ограничением по длине
    header = "Семья:\n\n"
    chosen: list[str] = []

    for index, block in enumerate(blocks):
        rest_after = len(blocks) - index - 1
        trial_all = header + "\n\n".join([*chosen, block, *blocks[index + 1 :]])
        if len(trial_all) <= max_length:
            chosen.append(block)
            continue
        trial_cut = header + "\n\n".join([*chosen, block])
        if rest_after:
            trial_cut += "\n\n" + f"… и ещё {rest_after} {_people_word(rest_after)}"
        if len(trial_cut) <= max_length:
            chosen.append(block)
            continue
        break

    text = header + "\n\n".join(chosen)
    rest = len(blocks) - len(chosen)
    if rest:
        text += "\n\n" + f"… и ещё {rest} {_people_word(rest)}"
    return text
