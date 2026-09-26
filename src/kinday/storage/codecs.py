"""Перевод доменных значений в столбцы SQLite и обратно.

У SQLite нет типов для даты, времени и момента, поэтому всё хранится строками
в фиксированном формате. Формат важен не только для чтения: выборка тика и
зачистка будущих напоминаний сравнивают `due_at_utc` прямо в SQL, то есть
лексикографически, и это верно только пока все моменты записаны в UTC одной и
той же длиной строки.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from kinday.core.models import Gender

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def dump_datetime(value: datetime) -> str:
    """Момент в UTC строкой фиксированной длины.

    Naive-значение отвергается: пояс потерялся бы молча, а сравнение с
    остальными моментами стало бы бессмысленным. Часы ядра по контракту
    (`core/ports.Clock`) всегда возвращают aware-время в UTC.
    """
    if value.tzinfo is None:
        raise ValueError(f"Момент времени должен быть timezone-aware: {value!r}")
    return value.astimezone(UTC).strftime(_DATETIME_FORMAT)


def load_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def dump_date(value: date) -> str:
    return value.isoformat()


def load_date(raw: str) -> date:
    return date.fromisoformat(raw)


def dump_time(value: time) -> str:
    return value.isoformat()


def load_time(raw: str) -> time:
    return time.fromisoformat(raw)


def dump_offsets(offsets_days: tuple[int, ...]) -> str:
    """Набор смещений одной строкой: порядок сохраняется, отдельной таблицы нет (SPEC 5.3)."""
    return ",".join(str(offset) for offset in offsets_days)


def load_offsets(raw: str) -> tuple[int, ...]:
    if not raw:
        return ()
    return tuple(int(part) for part in raw.split(","))


def dump_gender(value: Gender | None) -> str | None:
    return None if value is None else value.value


def load_gender(raw: str | None) -> Gender | None:
    return None if raw is None else Gender(raw)
