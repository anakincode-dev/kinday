"""Доменные отказы: наружу уходит код, пояснение с id остаётся внутри.

SPEC 5.7 оставляет доменные правила ядру, но формулировка отказа — дело слоя
telegram: человеку нужно «у человека уже есть двое родителей», а не «У человека
17 уже есть двое родителей». Пока ядро бросало обычный `ValueError`, роутеры
подставляли его текст в ответ бота как есть, и внутренние id уезжали
пользователю. Теперь ядро бросает `DomainError` с кодом из `DomainErrorCode`, а
`kinday.telegram.errors` переводит код в человеческую фразу.

`DomainError` наследует `ValueError` намеренно: до этого весь вызывающий код и
все тесты ловили `except ValueError`, и смена базового класса сломала бы их без
всякой пользы. Пояснение (`detail`) остаётся в `str(ошибки)` — оно для журнала и
тестов ядра, пользователю его не показывают.
"""

from __future__ import annotations

from enum import Enum


class DomainErrorCode(Enum):
    """Причины отказа, на которые у слоя telegram есть свой текст.

    Значение — та же строка, что имя: код попадает в журнал, и читать
    `THIRD_PARENT` удобнее, чем порядковый номер.
    """

    THIRD_PARENT = "THIRD_PARENT"
    SELF_RELATION = "SELF_RELATION"
    PLACEHOLDER = "PLACEHOLDER"
    PERSON_NOT_IN_FAMILY = "PERSON_NOT_IN_FAMILY"
    NOT_MEMBER = "NOT_MEMBER"
    ALREADY_IN_FAMILY = "ALREADY_IN_FAMILY"
    ALREADY_LINKED = "ALREADY_LINKED"
    OWN_RECORD = "OWN_RECORD"
    INVITE_NOT_FOUND = "INVITE_NOT_FOUND"
    INVITE_EXPIRED = "INVITE_EXPIRED"
    INVITE_USED = "INVITE_USED"
    INVITE_REVOKED = "INVITE_REVOKED"
    INVITED_PERSON_GONE = "INVITED_PERSON_GONE"
    BAD_TIMEZONE = "BAD_TIMEZONE"
    BAD_OFFSETS = "BAD_OFFSETS"
    NONEXISTENT_LOCAL_TIME = "NONEXISTENT_LOCAL_TIME"


class DomainError(ValueError):
    """Отказ домена: `code` — для внешнего слоя, `str(ошибки)` — для журнала.

    Наследник `ValueError`, поэтому старые `except ValueError` продолжают
    работать. Отказ, для которого кода нет (например, нарушенный контракт
    вызывающего слоя), так и остаётся обычным `ValueError` — слой telegram
    ответит на него общим текстом.
    """

    def __init__(self, code: DomainErrorCode, detail: str = "") -> None:
        super().__init__(detail or code.value)
        self.code = code

    def __reduce__(self) -> tuple[type[DomainError], tuple[DomainErrorCode, str]]:
        """Восстановление при копировании и pickle.

        Базовый `BaseException.__reduce__` отдаёт только `args`, а туда уходит
        одно пояснение — конструктор получил бы строку вместо кода и упал.
        """
        return (type(self), (self.code, str(self)))
