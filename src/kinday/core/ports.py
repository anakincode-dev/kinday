"""Протоколы, которые ядро объявляет, а внешние слои реализуют.

Зависимости направлены внутрь: ядро ничего не знает о storage, scheduler и telegram.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol

from kinday.core.models import (
    Account,
    Event,
    Family,
    Invite,
    Membership,
    Person,
    Relation,
    Reminder,
    ReminderOverride,
)


class RecipientBlocked(Exception):
    """Получатель заблокировал бота (Telegram вернул 403)."""


class TransportError(Exception):
    """Сетевой сбой или таймаут при отправке."""


class Clock(Protocol):
    def now(self) -> datetime:
        """Всегда timezone-aware, в UTC."""
        ...


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str) -> None:
        """Поднимает RecipientBlocked при 403 и TransportError при сетевом сбое."""
        ...


class UnitOfWork(Protocol):
    """Одна транзакция хранилища на сценарий.

    Сценарий из services.py пишет в несколько таблиц сразу — человек, рёбра,
    событие, напоминания, — и половинчатый результат недопустим: человек без
    рёбер потерял бы родство, событие без напоминаний молчало бы до суточного
    задания. Поэтому каждый сценарий целиком выполняется внутри
    `async with unit_of_work:`: выход без исключения фиксирует изменения,
    выход с исключением откатывает их.

    Ядро не знает, чем транзакция обеспечена: у SQLite это BEGIN/COMMIT на
    соединении, у in-memory реализации в тестах — ничего (фейки не рвутся на
    середине). Вложенных транзакций протокол не обещает: сценарии друг друга
    не вызывают.
    """

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Возвращает None: исключение сценария наружу не глушится."""
        ...


class ReminderRepo(Protocol):
    async def claim_due(self, at: datetime, limit: int) -> list[Reminder]: ...
    async def mark_sent(self, reminder_id: int, at: datetime) -> None: ...
    async def mark_missed(self, reminder_id: int, at: datetime) -> None: ...
    async def release(self, reminder_id: int) -> None: ...

    async def mark_failed(self, reminder_id: int, at: datetime, *, attempted: bool) -> None:
        """Закрывает строку как failed. `attempted` — вызывался ли по ней `send`.

        Параметр не для журнала: он увеличивает `attempts`, и это единственный
        след того, что отправка была. След нужен при возобновлении доставки
        (`delete_recoverable_failed_for_person`): строку, по которой `send`
        вызывался, воскрешать нельзя — сообщение могло дойти, — а строку, до
        которой тик даже не добрался, вернуть обязательно.

        Без значения по умолчанию намеренно: `failed` ставится из шести разных
        мест тика, и «была ли отправка» там каждый раз своё. Угадывать за
        вызывающего здесь дороже, чем написать лишнее слово.
        """
        ...

    async def add_many(self, reminders: list[Reminder]) -> None:
        """Вставляет пачкой. Дубли по уникальному индексу

        (event_id, person_id, occurrence_date, offset_days) молча игнорируются
        (см. SPEC 5.3) — повторная материализация не создаёт дублей.
        """
        ...

    async def delete_future_pending_for_event(self, event_id: int, after: datetime) -> None:
        """Удаляет будущие pending-строки по событию перед перематериализацией (SPEC 5.6)."""
        ...

    async def delete_future_pending_for_person(self, person_id: int, after: datetime) -> None:
        """Удаляет будущие pending-строки по человеку перед перематериализацией (SPEC 5.6)."""
        ...

    async def delete_future_pending_for_event_and_person(
        self, event_id: int, person_id: int, after: datetime
    ) -> None:
        """Удаляет будущие pending-строки одного человека по одному событию.

        Более узкая версия delete_future_pending_for_event: используется, когда
        меняются настройки только одной пары «аккаунт и событие»
        (переопределение, SPEC 5.6) и остальных получателей события трогать не нужно.
        """
        ...

    async def delete_all_for_event(self, event_id: int) -> None:
        """Удаляет все строки по событию независимо от статуса — событие удалено целиком."""
        ...

    async def delete_all_for_person(self, person_id: int) -> None:
        """Удаляет все строки, где человек — получатель, независимо от статуса.

        Используется при удалении человека (SPEC 4.3): в отличие от
        delete_future_pending_for_person, трогает и прошлые строки, потому что
        сам человек как получатель исчезает.
        """
        ...

    async def delete_recoverable_failed_for_person(
        self, person_id: int, attempted_after: datetime, unattempted_after: datetime
    ) -> None:
        """Удаляет failed-строки человека, которые можно построить заново, перед /start.

        Парный метод к fail_pending_for_person: после 403 наступления лежат в
        failed, а уникальный индекс (SPEC 5.3) не смотрит на статус и не даст
        построить их заново. Без этого удаления аккаунт, вернувшийся к боту,
        больше никогда не получил бы напоминания по уже построенному горизонту,
        хотя SPEC 5.5 обещает обратное («не строим новых до повторного запуска
        бота»).

        Порогов два, потому что строки в failed бывают двух разных сортов.

        Строка, по которой `send` вызывался (`attempts` больше нуля), удаляется
        только если её срок строго позже `attempted_after`: сообщение могло
        дойти — строка могла остаться в `sending` после падения между send и
        mark или исчерпать пять попыток, — и воскрешать её нельзя, повторная
        отправка хуже пропуска (SPEC 5.5).

        Строка, до которой тик не добрался (`attempts` равно нулю), удаляется по
        более раннему порогу `unattempted_after`: отправки не было, терять её не
        за что. Такие строки появляются пачкой — `fail_pending_for_person`
        закрывает после 403 весь горизонт аккаунта, хотя 403 пришёл на одну
        строку. Порог здесь не «сейчас», а начало окна просрочки: напоминание,
        опоздавшее меньше чем на сутки, по SPEC 4 и критерию приёмки 12 ещё
        подлежит доставке.
        """
        ...

    async def fail_pending_for_person(self, person_id: int, at: datetime) -> int:
        """Закрывает как failed все pending-строки, где человек — получатель.

        Нужно при 403 (SPEC 5.5): аккаунт заблокировал бота, и каждая следующая
        отправка ему вернёт тот же отказ. Строки закрываются сразу, чтобы тик не
        стучался в Telegram зря до конца горизонта. Строки в остальных статусах
        не трогаются — прошлое не переписывается (SPEC 5.6). Обратный путь —
        delete_recoverable_failed_for_person при возобновлении доставки.

        `attempts` не увеличивается: по этим строкам `send` не вызывался, и
        именно нулевой счётчик потом позволит вернуть их обратно.

        Возвращает число закрытых строк: как и у fail_all_sending, запись в
        журнал без количества бессмысленна.
        """
        ...

    async def fail_all_sending(self, at: datetime) -> int:
        """При старте сервиса переводит все строки sending в failed (SPEC 5.5).

        `at` — момент перехода, как у mark_failed: строки остались от прошлого
        запуска, и когда именно их закрыли, из самих строк иначе не узнать.

        `attempts` увеличивается: в `sending` строка попадает только после
        захвата тиком, то есть `send` по ней уже вызывался, и дошло ли сообщение
        — неизвестно. Счётчик и есть та отметка, по которой /start потом отличит
        эту строку от закрытых заодно с ней.

        Возвращает число закрытых строк: SPEC 5.5 требует записи в журнал, а
        она имеет смысл только с количеством — каждая такая строка это
        сообщение, про которое неизвестно, дошло ли оно.
        """
        ...


class FamilyRepo(Protocol):
    async def get(self, family_id: int) -> Family: ...
    async def create(self, family: Family) -> Family: ...


class PersonRepo(Protocol):
    async def get(self, person_id: int) -> Person: ...
    async def create(self, person: Person) -> Person: ...
    async def update(self, person: Person) -> Person: ...
    async def delete(self, person_id: int) -> None: ...
    async def list_by_family(self, family_id: int) -> list[Person]: ...


class AccountRepo(Protocol):
    async def get(self, account_id: int) -> Account: ...
    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Account | None: ...
    async def save(self, account: Account) -> Account: ...


class MembershipRepo(Protocol):
    async def get_by_account_and_family(
        self, account_id: int, family_id: int
    ) -> Membership | None: ...
    async def list_by_account(self, account_id: int) -> list[Membership]: ...
    async def list_by_family(self, family_id: int) -> list[Membership]: ...
    async def get_by_person(self, person_id: int) -> Membership | None: ...
    async def create(self, membership: Membership) -> Membership: ...
    async def delete(self, person_id: int) -> None: ...


class EventRepo(Protocol):
    async def get(self, event_id: int) -> Event: ...
    async def create(self, event: Event) -> Event: ...
    async def update(self, event: Event) -> Event: ...
    async def delete(self, event_id: int) -> None: ...
    async def list_by_family(self, family_id: int) -> list[Event]: ...
    async def list_by_person(self, person_id: int) -> list[Event]: ...

    async def list_all(self) -> list[Event]:
        """Все события всех семей — нужно суточной материализации (SPEC 5.4).

        Она достраивает напоминания по всем событиям сразу и семьи не выбирает:
        обход по списку семей означал бы ещё один порт ради того же самого.
        """
        ...


class RelationRepo(Protocol):
    async def list_by_family(self, family_id: int) -> list[Relation]: ...
    async def add(self, family_id: int, relation: Relation) -> None: ...
    async def remove(self, family_id: int, relation: Relation) -> None: ...


class ReminderOverrideRepo(Protocol):
    async def get(self, account_id: int, event_id: int) -> ReminderOverride | None: ...
    async def set(self, override: ReminderOverride) -> None: ...
    async def revoke(self, account_id: int, event_id: int) -> None: ...


class InviteRepo(Protocol):
    async def get(self, invite_id: int) -> Invite: ...
    async def get_by_code(self, code: str) -> Invite | None: ...
    async def create(self, invite: Invite) -> Invite: ...

    async def list_by_person(self, person_id: int) -> list[Invite]:
        """Все приглашения на запись, включая использованные и отозванные.

        Нужен при удалении человека (SPEC 4.3): выданные на него приглашения
        отзываются, иначе код продолжал бы привязывать аккаунт к записи,
        которой уже нет или которая стала заглушкой.
        """
        ...

    async def revoke(self, invite_id: int, at: datetime) -> None: ...
    async def mark_used(self, invite_id: int, at: datetime) -> None: ...
