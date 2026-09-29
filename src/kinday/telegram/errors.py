"""Что видит пользователь, когда что-то не получилось, и что попадает в журнал.

Две отдельные задачи, но обе про одно — наружу не должно уезжать внутреннее.

`USER_TEXTS` переводит код доменного отказа (`kinday.core.errors`) в живую
фразу. Сообщения самих исключений ядра для этого не годятся: в них стоят id
записей и аккаунтов («У человека 17 уже есть двое родителей») — человеку они
ничего не говорят, а о внутренностях базы рассказывают. Кода, которого в словаре
нет, быть не должно (это проверяет тест), но если он появится — уйдёт общий
текст, а не трассировка.

`on_error` — последний рубеж для всего, что handler не предусмотрел: человек
получает одну короткую фразу, начатый диалог сбрасывается, а в журнал уходят тип
ошибки, update_id и id пользователя. Трассировки и текста сообщения там нет
намеренно: в `str(ошибки)` регулярно оказывается то, что человек написал боту
(«invalid literal for int(): ...»), а журнал сервиса не место для переписки.
Цена решения — отладка по типу ошибки и update_id, без стека; поэтому тип и
update_id пишутся всегда, даже если ответить пользователю не удалось.

`silence_aiogram_update_dump` делает то же со штатным дампом aiogram: тип ошибки
в журнале остаётся, апдейт целиком и текст ошибки — нет.
"""

from __future__ import annotations

import logging

from aiogram.fsm.context import FSMContext
from aiogram.loggers import event as aiogram_event_logger
from aiogram.types import ErrorEvent, ReplyKeyboardRemove

from kinday.core.errors import DomainError, DomainErrorCode

logger = logging.getLogger(__name__)

# Логгер, в который aiogram пишет «Cause exception while process update id=...»
# вместе с трассировкой и str(ошибки).
AIOGRAM_EVENT_LOGGER = aiogram_event_logger.name

GENERIC_FAILURE = "Что-то пошло не так, попробуйте ещё раз."

# Код отказа → фраза для человека. Без цифр: где в сообщении ядра стоял id, здесь
# стоит «человек», «запись», «эта семья» — пользователь и так знает, о ком речь,
# он только что назвал его сам.
USER_TEXTS: dict[DomainErrorCode, str] = {
    DomainErrorCode.THIRD_PARENT: (
        "У этого человека уже есть двое родителей — третьего добавить нельзя."
    ),
    DomainErrorCode.SELF_RELATION: "Человек не может быть родственником самому себе.",
    DomainErrorCode.PLACEHOLDER: (
        "Этот родитель ещё неизвестен: его нельзя ни выбрать, ни пригласить. "
        "Добавьте настоящего человека — он займёт это место."
    ),
    DomainErrorCode.PERSON_NOT_IN_FAMILY: (
        "Этого человека нет в текущей семье. Проверьте, та ли семья выбрана: /family"
    ),
    DomainErrorCode.NOT_MEMBER: "Вы не состоите в этой семье.",
    DomainErrorCode.ALREADY_IN_FAMILY: "Вы уже состоите в этой семье.",
    DomainErrorCode.ALREADY_LINKED: "Эта запись уже занята другим участником.",
    DomainErrorCode.OWN_RECORD: "Свою собственную запись удалить нельзя.",
    DomainErrorCode.INVITE_NOT_FOUND: "Такого приглашения нет — проверьте код.",
    DomainErrorCode.INVITE_EXPIRED: "Срок приглашения истёк — попросите новое.",
    DomainErrorCode.INVITE_USED: "Это приглашение уже использовано — попросите новое.",
    DomainErrorCode.INVITE_REVOKED: "Это приглашение отозвано — попросите новое.",
    DomainErrorCode.INVITED_PERSON_GONE: (
        "Записи, на которую выдано приглашение, больше нет — попросите новое."
    ),
    DomainErrorCode.BAD_TIMEZONE: "Не знаю такого часового пояса. Выберите город: /timezone",
    DomainErrorCode.BAD_OFFSETS: "Нужен непустой набор дней без повторов, от нуля до года.",
    DomainErrorCode.NONEXISTENT_LOCAL_TIME: (
        "В вашем поясе такого времени в этот день не существует — выберите другое: /settings"
    ),
}


def user_text(error: Exception) -> str:
    """Человеческий текст отказа: по коду, если он есть, иначе общий.

    Обычный `ValueError` из ядра сюда тоже попадает — это либо нарушенный
    контракт вызова, либо отказ, которому кода ещё не завели. И в том, и в
    другом случае показывать его сообщение нельзя.
    """
    if isinstance(error, DomainError):
        return USER_TEXTS.get(error.code, GENERIC_FAILURE)
    return GENERIC_FAILURE


class _StripUpdateDump(logging.Filter):
    """Оставляет в дампе aiogram тип ошибки, убирает апдейт, её текст и трассировку.

    Штатный `loggers.event.exception` печатает и стек, и `str(ошибки)`, куда
    попадает ввод пользователя. Запись при этом не выбрасывается, а
    переписывается: пока `on_error` работает, до этого дампа дело вообще не
    доходит (обработанная ошибка выше не всплывает), зато в редких путях, где
    `on_error` не сработал — упал сам или апдейт не прошёл разбор, — журнал
    остаётся единственным следом, и гасить его целиком нельзя.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None and record.exc_text is None:
            return True
        exc_type = record.exc_info[0] if record.exc_info is not None else None
        record.msg = "Сбой при обработке апдейта: %s (апдейт и текст ошибки не пишем)"
        record.args = (exc_type.__name__ if exc_type is not None else "неизвестная ошибка",)
        record.exc_info = None
        record.exc_text = None
        return True


def silence_aiogram_update_dump() -> None:
    """Ставит фильтр на логгер aiogram. Повторный вызов ничего не добавляет.

    Идемпотентность важна: Dispatcher собирается заново на каждый тест, а
    фильтры логгера — глобальное состояние процесса. Логгер берётся сам объект
    `aiogram.loggers.event`, а не по имени: переименуют — сломается импорт, а не
    тишина в проде.
    """
    if any(isinstance(existing, _StripUpdateDump) for existing in aiogram_event_logger.filters):
        return
    aiogram_event_logger.addFilter(_StripUpdateDump())


async def on_error(event: ErrorEvent, state: FSMContext | None = None) -> bool:
    """Общий обработчик: короткий ответ человеку, сброшенный диалог, факты в журнал.

    Состояние сбрасывается, потому что на шаге диалога оно уже не соответствует
    происходящему: сценарий не выполнился, а следующее сообщение пользователя
    приняли бы за ответ на вопрос, которого он больше не видит. Проще начать
    команду заново.

    `state` необязателен: FSM-middleware подкладывает его в те же данные, что
    видит обработчик ошибок, но апдейт без чата и пользователя (например,
    служебный) остаётся без контекста состояния.

    Возвращает True, чтобы aiogram счёл ошибку обработанной и не поднимал её
    выше — иначе она ушла бы в его собственный журнал вместе с апдейтом.
    """
    message = event.update.message
    user = message.from_user if message is not None else None
    import traceback

    logger.error(
        "Необработанная ошибка %s: update_id=%s, пользователь=%s\n%s",
        type(event.exception).__name__,
        event.update.update_id,
        user.id if user is not None else None,
        "".join(
            traceback.format_exception(
                type(event.exception), event.exception, event.exception.__traceback__
            )
        ),
    )
    if state is not None:
        await state.clear()
    if message is None:
        return True
    try:
        await message.answer(GENERIC_FAILURE, reply_markup=ReplyKeyboardRemove())
    except Exception:
        logger.error(
            "Не удалось ответить об ошибке: update_id=%s, пользователь=%s",
            event.update.update_id,
            user.id if user is not None else None,
        )
    return True
