"""Роутеры и диалоги: перевод команд aiogram в вызовы kinday.core.services.

Доменных правил здесь нет (SPEC 5.7): слой разбирает текст в аргументы
сценариев ядра, ловит их отказы и превращает в понятные ответы. Всё, что решает,
можно ли действие и что оно меняет в базе, живёт в `core/services.py`.

Диалоги собраны на машине состояний aiogram: у каждого свой `StatesGroup`, шаг
хранит уже собранные ответы в данных состояния и вызывает сценарий последним
шагом — так отказ на любом шаге не оставляет в базе половины действия.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from kinday.core import services, texts
from kinday.core.models import Account, Event, Gender, Person
from kinday.core.ports import (
    AccountRepo,
    Clock,
    EventRepo,
    FamilyRepo,
    InviteRepo,
    MembershipRepo,
    PersonRepo,
    RelationRepo,
    ReminderOverrideRepo,
    ReminderRepo,
    UnitOfWork,
)
from kinday.core.relations import RelationKind, SiblingsQuestionRequired
from kinday.telegram.cities import CITIES
from kinday.telegram.errors import on_error, user_text

logger = logging.getLogger(__name__)

DATE_FORMAT = "%d.%m.%Y"
DATE_HINT = "ДД.ММ.ГГГГ"

GENDER_WORDS: dict[str, Gender] = {
    "м": Gender.MALE,
    "муж": Gender.MALE,
    "мужской": Gender.MALE,
    "ж": Gender.FEMALE,
    "жен": Gender.FEMALE,
    "женский": Gender.FEMALE,
}

# Ровно те слова, которыми SPEC 3.1 разрешает назвать связь нового человека.
RELATION_WORDS: dict[str, RelationKind] = {
    "отец": RelationKind.FATHER,
    "мать": RelationKind.MOTHER,
    "сын": RelationKind.SON,
    "дочь": RelationKind.DAUGHTER,
    "супруг": RelationKind.HUSBAND,
    "супруга": RelationKind.WIFE,
    "брат": RelationKind.BROTHER,
    "сестра": RelationKind.SISTER,
}

YES_WORDS = frozenset({"да", "ага", "конечно"})
NO_WORDS = frozenset({"нет", "не"})

TIME_FORMAT = "%H:%M"
TIME_HINT = "ЧЧ:ММ"

# Подписи кнопок выбора в /event_settings. Ответ узнаётся по первому слову:
# нажатая кнопка приходит целиком, а руками пишут короче — «свои», «как раньше».
OWN_SETTINGS = "свои настройки"
ACCOUNT_SETTINGS = "как в настройках"

OFFSETS_HINT = (
    "За сколько дней напоминать? Перечислите числа через запятую, "
    "0 — в день события. Например: 7, 1, 0"
)
OFFSETS_NOT_UNDERSTOOD = "Не понял: нужны числа через запятую, например 7, 1, 0."

MENU = (
    "Что я умею:\n"
    "/new_family — создать семью\n"
    "/join — присоединиться по коду приглашения\n"
    "/add_person — добавить родственника\n"
    "/add_event — добавить событие\n"
    "/invite — выдать приглашение на запись\n"
    "/timezone — сменить часовой пояс\n"
    "/settings — за сколько дней и во сколько напоминать\n"
    "/event_settings — своё расписание для одного события\n"
    "/persons — люди семьи\n"
    "/family — выбрать текущую семью\n"
    "/cancel — прервать диалог"
)

WELCOME = (
    "Я напоминаю о днях рождения и других важных датах вашей семьи.\n\n"
    "Создайте своё дерево командой /new_family или присоединитесь к чужому "
    "по коду приглашения: /join"
)

NO_FAMILY = (
    "У вас пока нет семьи. Создайте её командой /new_family "
    "или присоединитесь по приглашению: /join"
)

NOT_UNDERSTOOD = "Не понял сообщение.\n\n" + MENU


@dataclass(frozen=True, slots=True)
class Deps:
    """Порты и часы, которые роутеры раскладывают по параметрам сценариев ядра.

    Собирается точкой входа (`telegram/bot.py`) и приезжает в handler'ы через
    данные Dispatcher: слой telegram не знает, что за ними SQLite, ровно как
    ядро не знает про aiogram.
    """

    clock: Clock
    uow: UnitOfWork
    account: AccountRepo
    event: EventRepo
    family: FamilyRepo
    invite: InviteRepo
    membership: MembershipRepo
    override: ReminderOverrideRepo
    person: PersonRepo
    relation: RelationRepo
    reminder: ReminderRepo


class NewFamily(StatesGroup):
    """Создание семьи (SPEC 3.1): имя, пол, дата рождения, город."""

    name = State()
    gender = State()
    birth_date = State()
    city = State()


class NewPerson(StatesGroup):
    """Добавление родственника: относительно кого, кем приходится, кто он."""

    relative = State()
    kind = State()
    name = State()
    gender = State()
    birth_date = State()
    siblings = State()


class NewEvent(StatesGroup):
    """Добавление события (SPEC 4.2): о ком оно, как называется, когда, повторяется ли."""

    person = State()
    title = State()
    event_date = State()
    yearly = State()


class ChangingSettings(StatesGroup):
    """Свои смещения и время суток — они у аккаунта, а не у семьи (SPEC 2.1, критерий 27)."""

    offsets = State()
    time_of_day = State()


class ChangingEventSettings(StatesGroup):
    """Переопределение по одному событию и его снятие (SPEC 5.6, критерий 27)."""

    event = State()
    mode = State()
    offsets = State()
    time_of_day = State()


class Joining(StatesGroup):
    """Присоединение по приглашению (SPEC 3.2): код и город."""

    code = State()
    city = State()


class ChangingTimezone(StatesGroup):
    city = State()


class ChoosingFamily(StatesGroup):
    family = State()


class IssuingInvite(StatesGroup):
    person = State()


def _keyboard(options: Sequence[str], per_row: int = 2) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=option) for option in options[start : start + per_row]]
        for start in range(0, len(options), per_row)
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _numbered(labels: Sequence[str]) -> str:
    return "\n".join(f"{index}. {label}" for index, label in enumerate(labels, start=1))


def _pick(text: str, count: int) -> int | None:
    """Номер из ответа вида «2» или «2. Марина», приведённый к индексу списка."""
    head = text.strip().split(".", 1)[0].strip()
    if not head.isdigit():
        return None
    number = int(head)
    if not 1 <= number <= count:
        return None
    return number - 1


def _city_question() -> str:
    return (
        "Выберите город — по нему я считаю время отправки:\n"
        + ", ".join(CITIES)
        + "\n\nНапишите название города."
    )


def _cities_keyboard() -> ReplyKeyboardMarkup:
    return _keyboard(list(CITIES), per_row=3)


def _timezone_of(text: str) -> str | None:
    wanted = text.strip().casefold()
    for city, timezone in CITIES.items():
        if city.casefold() == wanted:
            return timezone
    return None


def _parse_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        return None


def _parse_time(text: str) -> time | None:
    try:
        return datetime.strptime(text.strip(), TIME_FORMAT).time()
    except ValueError:
        return None


def _parse_offsets(text: str) -> tuple[int, ...] | None:
    """Разбирает «7, 1, 0» в набор смещений. Годность набора решает ядро.

    Здесь только форма записи: разделители и целые неотрицательные числа. Что
    набор не пуст, без повторов и в пределах 0..365 — доменное правило, его
    проверяет `update_account_settings` (SPEC 2, критерий приёмки 27), и
    дублировать его в слое telegram нельзя.
    """
    parts = [part for part in re.split(r"[,;\s]+", text.strip()) if part]
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _parse_yes_no(text: str) -> bool | None:
    answer = text.strip().casefold()
    if answer in YES_WORDS:
        return True
    if answer in NO_WORDS:
        return False
    return None


def _schedule_words(offsets_days: Sequence[int], time_of_day: time) -> str:
    """«напоминаю за 7, 1, 0 дней (0 — в день события) в 09:00 по вашему времени»."""
    return (
        f"напоминаю за {', '.join(str(days) for days in offsets_days)} дней "
        f"(0 — в день события) в {time_of_day.strftime(TIME_FORMAT)} по вашему времени"
    )


def _text(message: Message) -> str:
    return (message.text or "").strip()


def _person_labels(people: Sequence[Person]) -> list[str]:
    return [person.name or "" for person in people]


async def _listable_people(deps: Deps, family_id: int) -> list[Person]:
    """Люди семьи без заглушек: заглушка в списках не показывается (SPEC 4.1)."""
    return [
        person
        for person in await deps.person.list_by_family(family_id)
        if not person.is_placeholder
    ]


async def _event_labels(deps: Deps, family_id: int, events: Sequence[Event]) -> list[str]:
    """«День рождения — Пётр»: у всех дней рождения одно название, различает имя."""
    names = {person.id: person.name for person in await deps.person.list_by_family(family_id)}
    return [f"{event.title} — {names.get(event.person_id) or ''}".strip(" —") for event in events]


async def _account_of(message: Message, deps: Deps) -> Account | None:
    if message.from_user is None:
        return None
    return await deps.account.get_by_telegram_user_id(message.from_user.id)


async def _current_family_id(message: Message, deps: Deps) -> int | None:
    """Текущая семья аккаунта (SPEC 2.1) или None с уже отправленным объяснением."""
    account = await _account_of(message, deps)
    if account is None or account.current_family_id is None:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return None
    return account.current_family_id


async def _bind_chat(message: Message, deps: Deps, telegram_user_id: int) -> bool:
    """Записывает chat_id и включает доставку: без чата тику отправлять некуда (SPEC 5.5).

    Отказ здесь не роняет handler и не отменяет то, что уже сделано (семья
    создана, приглашение принято): пользователю говорится, что напоминания пока
    не включены и это лечится повторным /start, а диалог закрывается как обычно.
    Иначе состояние машины осталось бы на шаге города, и следующий город создал
    бы вторую семью.
    """
    account = await deps.account.get_by_telegram_user_id(telegram_user_id)
    if account is None:
        logger.error("Аккаунт для telegram_user_id=%s не найден после сценария", telegram_user_id)
        await message.answer("Напоминания пока не включены — пришлите /start ещё раз.")
        return False
    try:
        await services.enable_delivery(
            account.id,
            message.chat.id,
            deps.account,
            deps.membership,
            deps.event,
            deps.override,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        logger.warning("Не удалось включить доставку аккаунту %s: %s", account.id, error)
        await message.answer("Напоминания пока не включены — пришлите /start ещё раз.")
        return False
    return True


async def cmd_start_with_code(
    message: Message, command: CommandObject, state: FSMContext, deps: Deps
) -> None:
    """Переход по ссылке-приглашению t.me/<bot>?start=<код> (SPEC 3.2).

    Это тоже запуск бота, поэтому чат привязывается сразу, ещё до разбора кода:
    иначе аккаунт, которому доставку отключил 403, остался бы без напоминаний,
    если код в ссылке окажется негодным (SPEC 5.5).
    """
    await state.clear()
    if message.from_user is not None and await _account_of(message, deps) is not None:
        await _bind_chat(message, deps, message.from_user.id)
    await _begin_joining(message, state, deps, (command.args or "").strip())


async def cmd_start(message: Message, state: FSMContext, deps: Deps) -> None:
    """Повторный запуск бота: привязывает чат и возвращает доставку, закрытую после 403.

    SPEC 5.5 отключает доставку «до повторного запуска бота», и именно этот
    handler его отмечает: `enable_delivery` пишет chat_id, включает доставку и
    восстанавливает горизонт напоминаний.
    """
    await state.clear()
    account = await _account_of(message, deps)
    if account is None:
        await message.answer(WELCOME, reply_markup=ReplyKeyboardRemove())
        return
    await services.enable_delivery(
        account.id,
        message.chat.id,
        deps.account,
        deps.membership,
        deps.event,
        deps.override,
        deps.reminder,
        deps.clock,
        deps.uow,
    )
    await message.answer(
        "С возвращением! Напоминания включены.\n\n" + MENU, reply_markup=ReplyKeyboardRemove()
    )


async def cmd_help(message: Message) -> None:
    await message.answer(MENU, reply_markup=ReplyKeyboardRemove())


async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменил.\n\n" + MENU, reply_markup=ReplyKeyboardRemove())


async def cmd_new_family(message: Message, state: FSMContext) -> None:
    await state.set_state(NewFamily.name)
    await message.answer(
        "Создаём семью. Как вас зовут? Имя в именительном падеже — я его не склоняю.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def new_family_name(message: Message, state: FSMContext) -> None:
    name = _text(message)
    if not name:
        await message.answer("Имя не может быть пустым. Как вас зовут?")
        return
    await state.update_data(name=name)
    await state.set_state(NewFamily.gender)
    await message.answer("Ваш пол?", reply_markup=_keyboard(["мужской", "женский"]))


async def new_family_gender(message: Message, state: FSMContext) -> None:
    gender = GENDER_WORDS.get(_text(message).casefold())
    if gender is None:
        await message.answer("Не понял пол. Выберите «мужской» или «женский».")
        return
    await state.update_data(gender=gender.value)
    await state.set_state(NewFamily.birth_date)
    await message.answer(
        f"Ваша дата рождения в формате {DATE_HINT}?", reply_markup=ReplyKeyboardRemove()
    )


async def new_family_birth_date(message: Message, state: FSMContext) -> None:
    birth_date = _parse_date(_text(message))
    if birth_date is None:
        await message.answer(f"Не понял дату. Нужен формат {DATE_HINT}, например 15.06.1990.")
        return
    await state.update_data(birth_date=birth_date.isoformat())
    await state.set_state(NewFamily.city)
    await message.answer(_city_question(), reply_markup=_cities_keyboard())


async def new_family_city(message: Message, state: FSMContext, deps: Deps) -> None:
    timezone = _timezone_of(_text(message))
    if timezone is None:
        await message.answer("Не нашёл такой город.\n\n" + _city_question())
        return
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        family = await services.create_family(
            message.from_user.id,
            str(data["name"]),
            Gender(data["gender"]),
            date.fromisoformat(str(data["birth_date"])),
            timezone,
            deps.family,
            deps.person,
            deps.account,
            deps.membership,
            deps.event,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        await message.answer(f"Не получилось создать семью. {user_text(error)}")
        return
    await state.clear()
    await _bind_chat(message, deps, message.from_user.id)
    # Вторая семья текущей не становится (SPEC 2.1), и /add_person пойдёт в
    # прежнюю — иначе совет «добавьте родственников» уводил бы людей не туда.
    account = await _account_of(message, deps)
    is_current = account is not None and account.current_family_id == family.id
    await message.answer(
        f"Семья создана: {family.name}.\n"
        + (
            "Добавьте родственников командой /add_person — напоминания о их днях "
            "рождения начнут приходить сразу."
            if is_current
            else "Текущей осталась прежняя семья: переключитесь командой /family, "
            "иначе /add_person добавит человека не сюда."
        ),
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_join(message: Message, state: FSMContext) -> None:
    await state.set_state(Joining.code)
    await message.answer(
        "Пришлите код приглашения — его выдаёт владелец семьи.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def joining_code(message: Message, state: FSMContext, deps: Deps) -> None:
    await _begin_joining(message, state, deps, _text(message))


async def _begin_joining(message: Message, state: FSMContext, deps: Deps, code: str) -> None:
    """Показывает «Антон приглашает вас как Марину» и спрашивает город (SPEC 3.2).

    Код проверяется до вопроса про город (`preview_invite`): спрашивать пояс по
    негодному коду значит вести диалог, который всё равно закончится отказом.
    """
    if message.from_user is None:
        return
    try:
        preview = await services.preview_invite(
            code,
            message.from_user.id,
            deps.invite,
            deps.person,
            deps.account,
            deps.family,
            deps.membership,
            deps.clock,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(user_text(error), reply_markup=ReplyKeyboardRemove())
        return
    await state.set_state(Joining.city)
    await state.update_data(code=code)
    inviter = (
        f"{preview.inviter_name} приглашает вас"
        if preview.inviter_name
        else "Вас приглашают в семью"
    )
    await message.answer(
        f"{inviter} как {preview.person_name}.\n\n" + _city_question(),
        reply_markup=_cities_keyboard(),
    )


async def joining_city(message: Message, state: FSMContext, deps: Deps) -> None:
    timezone = _timezone_of(_text(message))
    if timezone is None:
        await message.answer("Не нашёл такой город.\n\n" + _city_question())
        return
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        person = await services.accept_invite(
            str(data["code"]),
            message.from_user.id,
            timezone,
            deps.invite,
            deps.person,
            deps.account,
            deps.membership,
            deps.event,
            deps.override,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(user_text(error), reply_markup=ReplyKeyboardRemove())
        return
    await _bind_chat(message, deps, message.from_user.id)
    await state.clear()
    await message.answer(
        f"Готово: вы в семье как {person.name}. Напоминания будут приходить сюда.\n\n" + MENU,
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_add_person(message: Message, state: FSMContext, deps: Deps) -> None:
    family_id = await _current_family_id(message, deps)
    if family_id is None:
        return
    people = await _listable_people(deps, family_id)
    if not people:
        await message.answer(NO_FAMILY)
        return
    await state.set_state(NewPerson.relative)
    await state.update_data(family_id=family_id)
    await message.answer(
        "Относительно кого добавляем человека?\n" + _numbered(_person_labels(people)),
        reply_markup=_keyboard([f"{i}. {p.name}" for i, p in enumerate(people, 1)]),
    )


async def new_person_relative(message: Message, state: FSMContext, deps: Deps) -> None:
    data = await state.get_data()
    people = await _listable_people(deps, int(data["family_id"]))
    index = _pick(_text(message), len(people))
    if index is None:
        await message.answer("Нужен номер из списка.\n" + _numbered(_person_labels(people)))
        return
    await state.update_data(relative_to_person_id=people[index].id)
    await state.set_state(NewPerson.kind)
    await message.answer(
        f"Кем новый человек приходится {people[index].name}?",
        reply_markup=_keyboard(list(RELATION_WORDS), per_row=4),
    )


async def new_person_kind(message: Message, state: FSMContext) -> None:
    kind = RELATION_WORDS.get(_text(message).casefold())
    if kind is None:
        await message.answer("Не понял связь. Выберите: " + ", ".join(RELATION_WORDS))
        return
    await state.update_data(relation_kind=kind.value)
    await state.set_state(NewPerson.name)
    await message.answer("Как его зовут?", reply_markup=ReplyKeyboardRemove())


async def new_person_name(message: Message, state: FSMContext) -> None:
    name = _text(message)
    if not name:
        await message.answer("Имя не может быть пустым. Как его зовут?")
        return
    await state.update_data(name=name)
    await state.set_state(NewPerson.gender)
    await message.answer("Его пол?", reply_markup=_keyboard(["мужской", "женский"]))


async def new_person_gender(message: Message, state: FSMContext) -> None:
    gender = GENDER_WORDS.get(_text(message).casefold())
    if gender is None:
        await message.answer("Не понял пол. Выберите «мужской» или «женский».")
        return
    await state.update_data(gender=gender.value)
    await state.set_state(NewPerson.birth_date)
    await message.answer(
        f"Дата рождения в формате {DATE_HINT}?", reply_markup=ReplyKeyboardRemove()
    )


async def new_person_birth_date(message: Message, state: FSMContext, deps: Deps) -> None:
    birth_date = _parse_date(_text(message))
    if birth_date is None:
        await message.answer(f"Не понял дату. Нужен формат {DATE_HINT}, например 01.03.1980.")
        return
    await state.update_data(birth_date=birth_date.isoformat())
    await _try_add_person(message, state, deps, also_parent_of_siblings=None)


async def new_person_siblings(message: Message, state: FSMContext, deps: Deps) -> None:
    """Ответ на вопрос про братьев и сестёр при втором родителе (критерий приёмки 20)."""
    also = _parse_yes_no(_text(message))
    if also is None:
        await message.answer("Ответьте «да» или «нет».", reply_markup=_keyboard(["да", "нет"]))
        return
    await _try_add_person(message, state, deps, also_parent_of_siblings=also)


async def _try_add_person(
    message: Message, state: FSMContext, deps: Deps, *, also_parent_of_siblings: bool | None
) -> None:
    """Последний шаг диалога: вызывает сценарий и переводит его отказы в ответы бота.

    SiblingsQuestionRequired — не ошибка, а требование доспросить (SPEC 4.1):
    сценарий откатился целиком, диалог переходит в состояние вопроса и повторит
    вызов с явным ответом.
    """
    account = await _account_of(message, deps)
    if account is None:
        await message.answer(NO_FAMILY)
        await state.clear()
        return
    data = await state.get_data()
    try:
        person = await services.add_person(
            account.id,
            int(data["family_id"]),
            str(data["name"]),
            Gender(data["gender"]),
            date.fromisoformat(str(data["birth_date"])),
            RelationKind(data["relation_kind"]),
            int(data["relative_to_person_id"]),
            also_parent_of_siblings,
            deps.account,
            deps.event,
            deps.family,
            deps.person,
            deps.relation,
            deps.membership,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except SiblingsQuestionRequired:
        await state.set_state(NewPerson.siblings)
        await message.answer(
            f"{data['name']} приходится родителем и братьям с сёстрами тоже?",
            reply_markup=_keyboard(["да", "нет"]),
        )
        return
    except services.NotFamilyOwner:
        await state.clear()
        await message.answer(
            "Менять дерево может только владелец семьи. Свои настройки вы меняете сами: /timezone",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Не получилось добавить. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    await message.answer(
        f"Добавил: {person.name}. Напоминания о дне рождения уже построены.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_add_event(message: Message, state: FSMContext, deps: Deps) -> None:
    family_id = await _current_family_id(message, deps)
    if family_id is None:
        return
    people = await _listable_people(deps, family_id)
    if not people:
        await message.answer(NO_FAMILY)
        return
    await state.set_state(NewEvent.person)
    await state.update_data(family_id=family_id)
    await message.answer(
        "О чьей дате напоминать?\n" + _numbered(_person_labels(people)),
        reply_markup=_keyboard([f"{i}. {p.name}" for i, p in enumerate(people, 1)]),
    )


async def new_event_person(message: Message, state: FSMContext, deps: Deps) -> None:
    data = await state.get_data()
    people = await _listable_people(deps, int(data["family_id"]))
    index = _pick(_text(message), len(people))
    if index is None:
        await message.answer("Нужен номер из списка.\n" + _numbered(_person_labels(people)))
        return
    await state.update_data(person_id=people[index].id)
    await state.set_state(NewEvent.title)
    await message.answer(
        "Как назвать событие? Например «Годовщина свадьбы».",
        reply_markup=ReplyKeyboardRemove(),
    )


async def new_event_title(message: Message, state: FSMContext) -> None:
    title = _text(message)
    if not title:
        await message.answer("Название не может быть пустым. Как назвать событие?")
        return
    await state.update_data(title=title)
    await state.set_state(NewEvent.event_date)
    await message.answer(f"Дата события в формате {DATE_HINT}?")


async def new_event_date(message: Message, state: FSMContext) -> None:
    event_date = _parse_date(_text(message))
    if event_date is None:
        await message.answer(f"Не понял дату. Нужен формат {DATE_HINT}, например 12.07.2010.")
        return
    await state.update_data(event_date=event_date.isoformat())
    await state.set_state(NewEvent.yearly)
    await message.answer("Отмечается каждый год?", reply_markup=_keyboard(["да", "нет"]))


async def new_event_yearly(message: Message, state: FSMContext, deps: Deps) -> None:
    """Последний шаг: ежегодное событие или разовое (SPEC 4.2), затем вызов сценария."""
    yearly = _parse_yes_no(_text(message))
    if yearly is None:
        await message.answer("Ответьте «да» или «нет».", reply_markup=_keyboard(["да", "нет"]))
        return
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    data = await state.get_data()
    try:
        event = await services.add_event(
            account.id,
            int(data["family_id"]),
            int(data["person_id"]),
            str(data["title"]),
            date.fromisoformat(str(data["event_date"])),
            yearly,
            deps.account,
            deps.event,
            deps.family,
            deps.person,
            deps.membership,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except services.NotFamilyOwner:
        await state.clear()
        await message.answer(
            "События добавляет владелец семьи. Своё расписание вы меняете сами: /settings",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Не получилось добавить событие. {user_text(error)}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await state.clear()
    await message.answer(
        f"Добавил событие: {event.title}. Напоминания уже построены.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_settings(message: Message, state: FSMContext, deps: Deps) -> None:
    """Смещения и время суток принадлежат аккаунту и действуют во всех его семьях (SPEC 2.1)."""
    account = await _account_of(message, deps)
    if account is None:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    await state.set_state(ChangingSettings.offsets)
    await message.answer(
        f"Сейчас {_schedule_words(account.offsets_days, account.time_of_day)}.\n\n" + OFFSETS_HINT,
        reply_markup=ReplyKeyboardRemove(),
    )


async def changing_settings_offsets(message: Message, state: FSMContext) -> None:
    offsets = _parse_offsets(_text(message))
    if offsets is None:
        await message.answer(OFFSETS_NOT_UNDERSTOOD)
        return
    await state.update_data(offsets_days=list(offsets))
    await state.set_state(ChangingSettings.time_of_day)
    await message.answer(f"Во сколько напоминать? Формат {TIME_HINT}, например 09:00.")


async def changing_settings_time(message: Message, state: FSMContext, deps: Deps) -> None:
    """Пояс не спрашивается: его меняет /timezone, а сценарий требует весь набор настроек."""
    time_of_day = _parse_time(_text(message))
    if time_of_day is None:
        await message.answer(f"Не понял время. Нужен формат {TIME_HINT}, например 09:00.")
        return
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    data = await state.get_data()
    offsets = tuple(int(days) for days in data["offsets_days"])
    try:
        updated = await services.update_account_settings(
            account.id,
            account.timezone,
            offsets,
            time_of_day,
            deps.account,
            deps.membership,
            deps.event,
            deps.override,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Настройки не изменены. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    await message.answer(
        f"Готово: {_schedule_words(updated.offsets_days, updated.time_of_day)}.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_event_settings(message: Message, state: FSMContext, deps: Deps) -> None:
    """Переопределение по одному событию: своё расписание или возврат к общему (SPEC 5.6)."""
    family_id = await _current_family_id(message, deps)
    if family_id is None:
        return
    events = await deps.event.list_by_family(family_id)
    if not events:
        await message.answer(
            "В этой семье пока нет событий. Добавьте родственника (/add_person) "
            "или событие (/add_event).",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    labels = await _event_labels(deps, family_id, events)
    await state.set_state(ChangingEventSettings.event)
    await state.update_data(family_id=family_id)
    await message.answer(
        "Для какого события задать своё расписание?\n" + _numbered(labels),
        reply_markup=_keyboard([f"{i}. {label}" for i, label in enumerate(labels, 1)], per_row=1),
    )


async def changing_event_settings_event(message: Message, state: FSMContext, deps: Deps) -> None:
    data = await state.get_data()
    family_id = int(data["family_id"])
    events = await deps.event.list_by_family(family_id)
    labels = await _event_labels(deps, family_id, events)
    index = _pick(_text(message), len(events))
    if index is None:
        await message.answer("Нужен номер из списка.\n" + _numbered(labels))
        return
    await state.update_data(event_id=events[index].id)
    await state.set_state(ChangingEventSettings.mode)
    await message.answer(
        f"«{labels[index]}»: задать своё расписание или вернуть общие настройки?",
        reply_markup=_keyboard([OWN_SETTINGS, ACCOUNT_SETTINGS], per_row=1),
    )


async def changing_event_settings_mode(message: Message, state: FSMContext, deps: Deps) -> None:
    """Развилка: «свои настройки» ведёт к вопросам, «как в настройках» снимает переопределение.

    Снятие отсутствующего переопределения ядро считает безобидным, поэтому
    отдельного вопроса «а было ли оно» здесь нет (SPEC 5.6).
    """
    answer = _text(message).casefold()
    if answer.startswith("свои"):
        await state.set_state(ChangingEventSettings.offsets)
        await message.answer(OFFSETS_HINT, reply_markup=ReplyKeyboardRemove())
        return
    if not answer.startswith("как"):
        await message.answer(
            f"Выберите «{OWN_SETTINGS}» или «{ACCOUNT_SETTINGS}».",
            reply_markup=_keyboard([OWN_SETTINGS, ACCOUNT_SETTINGS], per_row=1),
        )
        return
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    data = await state.get_data()
    try:
        await services.clear_override(
            account.id,
            int(data["event_id"]),
            deps.override,
            deps.event,
            deps.account,
            deps.membership,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Расписание не изменено. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    await message.answer(
        "Вернул общие настройки аккаунта: "
        f"{_schedule_words(account.offsets_days, account.time_of_day)}.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def changing_event_settings_offsets(message: Message, state: FSMContext) -> None:
    offsets = _parse_offsets(_text(message))
    if offsets is None:
        await message.answer(OFFSETS_NOT_UNDERSTOOD)
        return
    await state.update_data(offsets_days=list(offsets))
    await state.set_state(ChangingEventSettings.time_of_day)
    await message.answer(f"Во сколько напоминать об этом событии? Формат {TIME_HINT}.")


async def changing_event_settings_time(message: Message, state: FSMContext, deps: Deps) -> None:
    time_of_day = _parse_time(_text(message))
    if time_of_day is None:
        await message.answer(f"Не понял время. Нужен формат {TIME_HINT}, например 09:00.")
        return
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    data = await state.get_data()
    offsets = tuple(int(days) for days in data["offsets_days"])
    try:
        override = await services.set_override(
            account.id,
            int(data["event_id"]),
            offsets,
            time_of_day,
            deps.override,
            deps.event,
            deps.account,
            deps.membership,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Расписание не изменено. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    await message.answer(
        f"Для этого события {_schedule_words(override.offsets_days, override.time_of_day)}. "
        "Вернуть общие настройки — снова /event_settings.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_invite(message: Message, state: FSMContext, deps: Deps) -> None:
    family_id = await _current_family_id(message, deps)
    if family_id is None:
        return
    people = await _listable_people(deps, family_id)
    await state.set_state(IssuingInvite.person)
    await state.update_data(family_id=family_id)
    await message.answer(
        "Кому выдать приглашение?\n" + _numbered(_person_labels(people)),
        reply_markup=_keyboard([f"{i}. {p.name}" for i, p in enumerate(people, 1)]),
    )


async def issuing_invite_person(message: Message, state: FSMContext, deps: Deps, bot: Bot) -> None:
    data = await state.get_data()
    people = await _listable_people(deps, int(data["family_id"]))
    index = _pick(_text(message), len(people))
    if index is None:
        await message.answer("Нужен номер из списка.\n" + _numbered(_person_labels(people)))
        return
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY)
        return
    try:
        invite = await services.issue_invite(
            account.id,
            people[index].id,
            deps.family,
            deps.person,
            deps.membership,
            deps.invite,
            deps.clock,
            deps.uow,
        )
    except services.NotFamilyOwner:
        await state.clear()
        await message.answer(
            "Приглашения выдаёт владелец семьи.", reply_markup=ReplyKeyboardRemove()
        )
        return
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Приглашение не выдано. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    me = await bot.me()
    await message.answer(
        f"Ссылка для {people[index].name}, одноразовая и на семь дней:\n"
        f"https://t.me/{me.username}?start={invite.code}",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_timezone(message: Message, state: FSMContext, deps: Deps) -> None:
    account = await _account_of(message, deps)
    if account is None:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    await state.set_state(ChangingTimezone.city)
    await message.answer(_city_question(), reply_markup=_cities_keyboard())


async def changing_timezone_city(message: Message, state: FSMContext, deps: Deps) -> None:
    """Смена пояса перестраивает будущие напоминания во всех семьях аккаунта (критерий 6).

    Смещения и время суток остаются прежними: сценарий проверяет весь набор
    настроек целиком, поэтому передаются текущие значения аккаунта, а не пустые.
    """
    timezone = _timezone_of(_text(message))
    if timezone is None:
        await message.answer("Не нашёл такой город.\n\n" + _city_question())
        return
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    try:
        updated = await services.update_account_settings(
            account.id,
            timezone,
            account.offsets_days,
            account.time_of_day,
            deps.account,
            deps.membership,
            deps.event,
            deps.override,
            deps.reminder,
            deps.clock,
            deps.uow,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Настройки не изменены. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    await message.answer(
        f"Часовой пояс: {_text(message)} ({updated.timezone}). "
        f"Время напоминаний: {updated.time_of_day.strftime('%H:%M')}.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cmd_family(message: Message, state: FSMContext, deps: Deps) -> None:
    account = await _account_of(message, deps)
    if account is None:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    memberships = await deps.membership.list_by_account(account.id)
    if not memberships:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    families = [await deps.family.get(membership.family_id) for membership in memberships]
    await state.set_state(ChoosingFamily.family)
    await message.answer(
        "Какую семью сделать текущей?\n" + _numbered([family.name for family in families]),
        reply_markup=_keyboard([f"{i}. {f.name}" for i, f in enumerate(families, 1)], per_row=1),
    )


async def choosing_family(message: Message, state: FSMContext, deps: Deps) -> None:
    account = await _account_of(message, deps)
    if account is None:
        await state.clear()
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return
    memberships = await deps.membership.list_by_account(account.id)
    families = [await deps.family.get(membership.family_id) for membership in memberships]
    index = _pick(_text(message), len(families))
    if index is None:
        await message.answer("Нужен номер из списка.\n" + _numbered([f.name for f in families]))
        return
    try:
        await services.set_current_family(
            account.id, families[index].id, deps.account, deps.membership, deps.uow
        )
    except ValueError as error:
        await state.clear()
        await message.answer(
            f"Не переключил. {user_text(error)}", reply_markup=ReplyKeyboardRemove()
        )
        return
    await state.clear()
    await message.answer(
        f"Текущая семья: {families[index].name}.", reply_markup=ReplyKeyboardRemove()
    )


async def cmd_persons(message: Message, deps: Deps) -> None:
    account = await _account_of(message, deps)
    if account is None:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return

    family_persons = await services.list_family_persons(
        account.id,
        deps.account,
        deps.family,
        deps.membership,
        deps.person,
        deps.relation,
        deps.event,
        deps.clock,
        deps.uow,
    )

    if family_persons is None:
        await message.answer(NO_FAMILY, reply_markup=ReplyKeyboardRemove())
        return

    text = texts.format_persons_list(family_persons)
    await message.answer(text, reply_markup=ReplyKeyboardRemove())


async def fallback(message: Message) -> None:
    await message.answer(NOT_UNDERSTOOD, reply_markup=ReplyKeyboardRemove())


def build_router() -> Router:
    """Собирает роутер с диалогами: создание семьи, добавление человека, приглашения.

    Каждый вызов даёт новый Router: aiogram не разрешает подключить один и тот же
    экземпляр к двум Dispatcher'ам, а тесты поднимают свой на каждый сценарий.

    Порядок регистрации — это приоритет. Сначала команды, чтобы /cancel и /start
    работали посреди любого диалога; затем шаги диалогов по состояниям; последним
    — ответ на всё непонятое, иначе он перехватывал бы шаги.
    """
    router = Router(name="kinday")
    # Бот работает только в личных сообщениях (SPEC 7). Фильтр на весь роутер, а
    # не на отдельные команды: /start в группе привязал бы к аккаунту групповой
    # chat_id, и все напоминания семьи ушли бы в общий чат, а /invite напечатал
    # бы там одноразовую ссылку.
    router.message.filter(F.chat.type == "private")

    router.message.register(cmd_start_with_code, CommandStart(deep_link=True))
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_cancel, Command("cancel"))
    router.message.register(cmd_new_family, Command("new_family"))
    router.message.register(cmd_join, Command("join"))
    router.message.register(cmd_add_person, Command("add_person"))
    router.message.register(cmd_add_event, Command("add_event"))
    router.message.register(cmd_invite, Command("invite"))
    router.message.register(cmd_timezone, Command("timezone"))
    router.message.register(cmd_settings, Command("settings"))
    router.message.register(cmd_event_settings, Command("event_settings"))
    router.message.register(cmd_persons, Command("persons"))
    router.message.register(cmd_family, Command("family"))

    router.message.register(new_family_name, NewFamily.name, F.text)
    router.message.register(new_family_gender, NewFamily.gender, F.text)
    router.message.register(new_family_birth_date, NewFamily.birth_date, F.text)
    router.message.register(new_family_city, NewFamily.city, F.text)

    router.message.register(joining_code, Joining.code, F.text)
    router.message.register(joining_city, Joining.city, F.text)

    router.message.register(new_person_relative, NewPerson.relative, F.text)
    router.message.register(new_person_kind, NewPerson.kind, F.text)
    router.message.register(new_person_name, NewPerson.name, F.text)
    router.message.register(new_person_gender, NewPerson.gender, F.text)
    router.message.register(new_person_birth_date, NewPerson.birth_date, F.text)
    router.message.register(new_person_siblings, NewPerson.siblings, F.text)

    router.message.register(new_event_person, NewEvent.person, F.text)
    router.message.register(new_event_title, NewEvent.title, F.text)
    router.message.register(new_event_date, NewEvent.event_date, F.text)
    router.message.register(new_event_yearly, NewEvent.yearly, F.text)

    router.message.register(changing_settings_offsets, ChangingSettings.offsets, F.text)
    router.message.register(changing_settings_time, ChangingSettings.time_of_day, F.text)

    router.message.register(changing_event_settings_event, ChangingEventSettings.event, F.text)
    router.message.register(changing_event_settings_mode, ChangingEventSettings.mode, F.text)
    router.message.register(changing_event_settings_offsets, ChangingEventSettings.offsets, F.text)
    router.message.register(changing_event_settings_time, ChangingEventSettings.time_of_day, F.text)

    router.message.register(issuing_invite_person, IssuingInvite.person, F.text)
    router.message.register(changing_timezone_city, ChangingTimezone.city, F.text)
    router.message.register(choosing_family, ChoosingFamily.family, F.text)

    # Без StateFilter: сюда попадает и текст вне диалога, и нетекстовое сообщение
    # на шаге диалога (стикер вместо даты). Молчать нельзя — пользователь не
    # поймёт, что бот ждёт другого, и не вспомнит про /cancel.
    router.message.register(fallback)

    # Всё, что не предусмотрел ни один handler. Регистрируется здесь, а не на
    # Dispatcher, чтобы роутер оставался самодостаточным: собрали — значит, и
    # сбои уже накрыты. Фильтр `private` на этот observer не действует, он
    # относится только к сообщениям.
    router.errors.register(on_error)
    return router
