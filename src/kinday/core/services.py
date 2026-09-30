"""Сценарии: создать семью, добавить человека, выдать приглашение и так далее.

Единственное место, где встречаются модели, порты и остальные модули core.
Внешние слои (telegram, будущий api) вызывают только эти функции.

Каждый сценарий целиком выполняется внутри переданного `UnitOfWork`: либо все
его записи видны вместе, либо ни одной (см. docstring протокола в ports.py).
Поэтому отказ посередине — обычный способ выйти из сценария: откат делает
единица работы, а ручной уборки записанного в функциях нет.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kinday.core import texts
from kinday.core.errors import DomainError, DomainErrorCode
from kinday.core.models import (
    BIRTHDAY_EVENT_TITLE,
    Account,
    Event,
    EventKind,
    Family,
    Gender,
    Invite,
    Membership,
    ParentOf,
    Person,
    Relation,
    Reminder,
    ReminderOverride,
    SpouseOf,
)
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
from kinday.core.recurrence import age_as_of, age_on
from kinday.core.relations import (
    RelationKind,
    add_sibling,
    add_spouse,
    attach_parent,
    infer_relation_category,
    infer_relation_text,
)
from kinday.core.reminders import (
    MISFIRE_GRACE,
    apply_override,
    is_overdue,
    materialize_for_event,
)
from kinday.core.texts import relation_word_bare, reminder_text

DEFAULT_OFFSETS_DAYS: tuple[int, ...] = (7, 1, 0)
DEFAULT_TIME_OF_DAY = time(9, 0)

# Предупредить можно не раньше чем за год: при большем смещении напоминание о
# ежегодном событии пришло бы раньше, чем напоминание о предыдущей его
# годовщине, то есть порядок предупреждений перевернулся бы.
MAX_OFFSET_DAYS = 365

# SPEC 5.7: код едет параметром start в t.me-ссылке, поэтому ограничен алфавитом
# [A-Za-z0-9_-] и 64 символами. token_urlsafe(16) даёт ровно этот алфавит и 22
# символа — 128 бит энтропии.
INVITE_CODE_BYTES = 16
INVITE_TTL = timedelta(days=7)

# Заведомо невозможный id человека: репозитории выдают только положительные id.
# Используется, чтобы прогнать attach_parent «вхолостую» и убедиться, что она
# не бросит исключение, прежде чем создавать нового человека в хранилище —
# см. комментарий в add_person.
_DRY_RUN_PERSON_ID = -1


def _with_real_id(relation: Relation, person_id: int) -> Relation:
    """Подставляет настоящий id вместо _DRY_RUN_PERSON_ID в посчитанное «вхолостую» ребро.

    Новый родитель встаёт на место заглушки, а она держит рёбра в любую
    сторону: к детям (parent_id), к своим родителям (child_id) и к супругу
    (merge_placeholder_into всегда кладёт нового родителя первым концом).
    Поэтому подстановка смотрит все поля, а не только parent_id.

    Ребро без заглушечного конца — ошибка в расчёте «вхолостую», а не
    в данных: проверка поднимает RuntimeError, а не assert, потому что
    assert выключается при запуске с `python -O` и тогда в хранилище ушло бы
    ребро с id -1.
    """
    if isinstance(relation, ParentOf):
        if relation.parent_id == _DRY_RUN_PERSON_ID:
            return ParentOf(parent_id=person_id, child_id=relation.child_id)
        if relation.child_id != _DRY_RUN_PERSON_ID:
            raise RuntimeError(f"В ребре {relation} нет заглушечного конца {_DRY_RUN_PERSON_ID}")
        return ParentOf(parent_id=relation.parent_id, child_id=person_id)
    if relation.a_id != _DRY_RUN_PERSON_ID:
        raise RuntimeError(f"В ребре {relation} нет заглушечного конца {_DRY_RUN_PERSON_ID}")
    return SpouseOf(a_id=person_id, b_id=relation.b_id)


class NotFamilyOwner(Exception):
    """Действие над деревом семьи доступно только владельцу (SPEC 2, критерий приёмки 27)."""


def _new_person(family_id: int, name: str, gender: Gender, birth_date: date) -> Person:
    return Person(id=0, family_id=family_id, name=name, gender=gender, birth_date=birth_date)


def _new_placeholder(family_id: int) -> Person:
    return Person(
        id=0, family_id=family_id, name=None, gender=None, birth_date=None, is_placeholder=True
    )


def _require_known_timezone(timezone: str) -> None:
    """Пояс должен существовать в базе zoneinfo (SPEC 5.3: строка IANA).

    Ровно этот вызов делает расчёт момента отправки (core/reminders.py), так
    что пояс, которого нет в базе, положил бы материализацию — в том числе
    суточную, то есть уже для всех семей, а не только для этого аккаунта.
    """
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise DomainError(
            DomainErrorCode.BAD_TIMEZONE, f"Неизвестный часовой пояс: {timezone!r}"
        ) from error


def _require_valid_offsets(offsets_days: tuple[int, ...]) -> None:
    """Смещения: непустой набор целых 0..MAX_OFFSET_DAYS без повторов.

    Пустой набор оставил бы аккаунт вообще без напоминаний, а отписки от
    событий в SPEC 3.4 нет: настраиваются только смещения, время суток и пояс.
    Повтор бессмыслен — две одинаковые строки схлопнул бы уникальный индекс
    reminders (SPEC 5.3), то есть набор молча оказался бы другим.
    Отрицательное смещение означало бы напоминание после события.
    """
    if not offsets_days:
        raise DomainError(DomainErrorCode.BAD_OFFSETS, "Набор смещений не может быть пустым")
    for offset in offsets_days:
        # bool — подкласс int, но True вместо 1 в настройках означает ошибку
        # вызывающего слоя, а не смещение «за один день».
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise DomainError(
                DomainErrorCode.BAD_OFFSETS, f"Смещение должно быть целым числом дней: {offset!r}"
            )
        if not 0 <= offset <= MAX_OFFSET_DAYS:
            raise DomainError(
                DomainErrorCode.BAD_OFFSETS,
                f"Смещение вне диапазона 0..{MAX_OFFSET_DAYS}: {offset}",
            )
    if len(set(offsets_days)) != len(offsets_days):
        raise DomainError(
            DomainErrorCode.BAD_OFFSETS, f"Смещения не могут повторяться: {offsets_days}"
        )


async def _require_owner(family_repo: FamilyRepo, family_id: int, acting_account_id: int) -> Family:
    family = await family_repo.get(family_id)
    if family.owner_account_id != acting_account_id:
        raise NotFamilyOwner(f"Аккаунт {acting_account_id} не владелец семьи {family_id}")
    return family


async def _recipients(
    family_id: int, membership_repo: MembershipRepo, account_repo: AccountRepo
) -> list[tuple[Membership, Account]]:
    memberships = await membership_repo.list_by_family(family_id)
    return [
        (membership, await account_repo.get(membership.account_id)) for membership in memberships
    ]


async def _materialize_birthday(
    person: Person,
    birth_date: date,
    family_id: int,
    event_repo: EventRepo,
    membership_repo: MembershipRepo,
    account_repo: AccountRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Event:
    """Заводит ежегодное событие «День рождения» и сразу материализует по нему напоминания.

    kind=BIRTHDAY, а не совпадение title со строкой — по нему materialize_for_event
    узнаёт единственное исключение из напоминаний всем (SPEC 3.4). У человека не
    может появиться второго такого события: эта функция вызывается ровно один
    раз для только что созданного person (в create_family и add_person), у
    которого до этого момента вообще не было ни одного события.
    """
    event = await event_repo.create(
        Event(
            id=0,
            family_id=family_id,
            person_id=person.id,
            title=BIRTHDAY_EVENT_TITLE,
            date=birth_date,
            is_recurring_yearly=True,
            kind=EventKind.BIRTHDAY,
        )
    )
    recipients = await _recipients(family_id, membership_repo, account_repo)
    reminders = materialize_for_event(event, recipients, clock)
    if reminders:
        await reminder_repo.add_many(reminders)
    return event


async def create_family(
    owner_telegram_user_id: int,
    owner_name: str,
    owner_gender: Gender,
    owner_birth_date: date,
    owner_timezone: str,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    event_repo: EventRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Family:
    """Создаёт семью, человека-владельца и привязку его Telegram-аккаунта.

    Если у telegram-аккаунта уже есть запись (он создаёт вторую семью, см. SPEC
    2.1), она переиспользуется как есть: настройки напоминаний принадлежат
    аккаунту целиком и не зависят от того, в скольких семьях он состоит,
    поэтому `owner_timezone` учитывается только при первом создании аккаунта.
    Проверяется он всё равно всегда (SPEC 3.4): неизвестный пояс — ошибка
    вызывающего слоя, и молчать о ней, потому что значение не понадобилось,
    значит откладывать отказ до случая, когда оно понадобится.
    """
    async with uow:
        _require_known_timezone(owner_timezone)

        account = await account_repo.get_by_telegram_user_id(owner_telegram_user_id)
        if account is None:
            account = await account_repo.save(
                Account(
                    id=0,
                    telegram_user_id=owner_telegram_user_id,
                    current_family_id=None,
                    timezone=owner_timezone,
                    offsets_days=DEFAULT_OFFSETS_DAYS,
                    time_of_day=DEFAULT_TIME_OF_DAY,
                )
            )

        family = await family_repo.create(
            Family(id=0, name=f"Семья {owner_name}", owner_account_id=account.id)
        )
        owner_person = await person_repo.create(
            _new_person(family.id, owner_name, owner_gender, owner_birth_date)
        )
        await membership_repo.create(
            Membership(account_id=account.id, family_id=family.id, person_id=owner_person.id)
        )

        if account.current_family_id is None:
            account.current_family_id = family.id
            await account_repo.save(account)

        await _materialize_birthday(
            owner_person,
            owner_birth_date,
            family.id,
            event_repo,
            membership_repo,
            account_repo,
            reminder_repo,
            clock,
        )

        return family


async def add_person(
    acting_account_id: int,
    family_id: int,
    name: str,
    gender: Gender,
    birth_date: date,
    relation_kind: RelationKind,
    relative_to_person_id: int,
    also_parent_of_siblings: bool | None,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Person:
    """Добавляет человека, переводит связь в базовые рёбра, заводит день рождения.

    Перевод relation_kind в рёбра делают функции core/relations.py:
    FATHER/MOTHER и SON/DAUGHTER — attach_parent (в обе стороны, см. её
    docstring), BROTHER/SISTER — add_sibling, HUSBAND/WIFE — add_spouse.
    Новый человек всегда создаётся с нуля, поэтому для SON/DAUGHTER
    attach_parent видит, что у него ещё нет родителей, и просто заводит одно
    ребро без вопросов; ветка с заглушкой и вопросом про братьев реально
    достижима только для FATHER/MOTHER, когда relative_to_person_id уже имеет
    одного известного родителя. Если также известных родителей нет у
    relative_to_person_id при BROTHER/SISTER, add_sibling заводит заглушку.

    `also_parent_of_siblings` имеет смысл только при добавлении второго
    родителя (relation_kind FATHER/MOTHER человеку, у которого уже есть один
    известный родитель с другими детьми): «да» — рёбра протягиваются ко всем
    его братьям и сёстрам, у кого есть свободный слот, «нет» — только к
    выбранному человеку (SPEC 4.1, критерий приёмки 20). В остальных случаях
    параметр не имеет значения и должен быть None.

    Если attach_parent бросает SiblingsQuestionRequired (нужен ответ про
    братьев и сестёр) или отклоняет (третий родитель — ValueError), в
    хранилище не остаётся никаких следов вызова: ни новый человек, ни рёбра,
    ни событие не записываются — вызывающий код (бот) должен переспросить
    пользователя и повторить вызов с явным also_parent_of_siblings, либо
    сообщить об отказе.

    Напоминания по новому дню рождения материализуются сразу же, не дожидаясь
    суточного задания (SPEC 3.1, критерий приёмки 14). Отклоняет действие,
    если acting_account не владелец семьи (family_repo), и если
    relative_to_person_id не принадлежит family_id — иначе рёбра могли бы
    связать между собой людей из разных семей (SPEC 2: семьи не видят данные
    друг друга).

    Отклоняет и заглушку в роли relative_to_person_id. Она не показывается в
    списках (SPEC 4.1), поэтому в боте её и не выбрать, а здесь проверяется
    потому, что записать связь «относительно заглушки» пользователь и не имел
    в виду: у неё нет ни имени, ни пола, ни даты рождения, так что выбрать её
    осознанно нельзя. Законный способ завести человека на её месте — добавить
    родителя тому, кто на заглушке висит (FATHER/MOTHER для ребёнка), тогда
    заглушка сливается с ним вместе со всеми своими рёбрами.
    """
    async with uow:
        await _require_owner(family_repo, family_id, acting_account_id)

        all_relations = await relation_repo.list_by_family(family_id)
        parent_relations = [r for r in all_relations if isinstance(r, ParentOf)]
        people = {p.id: p for p in await person_repo.list_by_family(family_id)}
        if relative_to_person_id not in people:
            raise DomainError(
                DomainErrorCode.PERSON_NOT_IN_FAMILY,
                f"Человек {relative_to_person_id} не найден в семье {family_id}",
            )
        if people[relative_to_person_id].is_placeholder:
            raise DomainError(
                DomainErrorCode.PLACEHOLDER,
                f"Запись {relative_to_person_id} — заглушка неизвестного родителя, "
                "родственником её выбрать нельзя",
            )

        match relation_kind:
            case RelationKind.FATHER | RelationKind.MOTHER:
                # attach_parent может бросить SiblingsQuestionRequired (нужен ответ
                # пользователя) или ValueError (третий родитель) — тогда в хранилище
                # не должно остаться ни человека, ни рёбер. Поэтому сначала прогоняем
                # attach_parent "вхолостую" с заведомо невозможным id нового родителя:
                # исключения не зависят от его значения, оно попадает только в уже
                # готовые рёбра результата (parent_id каждого добавленного ParentOf).
                # Человека создаём и id подставляем в рёбра только после того, как
                # убедились, что attach_parent не откажет.
                change = attach_parent(
                    relative_to_person_id,
                    _DRY_RUN_PERSON_ID,
                    all_relations,
                    also_parent_of_siblings,
                    people,
                )
                new_person = await person_repo.create(
                    _new_person(family_id, name, gender, birth_date)
                )
                for edge in change.removed:
                    await relation_repo.remove(family_id, edge)
                if change.removed_placeholder_id is not None:
                    await person_repo.delete(change.removed_placeholder_id)
                for edge in change.added:
                    await relation_repo.add(family_id, _with_real_id(edge, new_person.id))

            case RelationKind.SON | RelationKind.DAUGHTER:
                new_person = await person_repo.create(
                    _new_person(family_id, name, gender, birth_date)
                )
                change = attach_parent(new_person.id, relative_to_person_id, [], None, {})
                for edge in change.added:
                    await relation_repo.add(family_id, edge)

            case RelationKind.HUSBAND | RelationKind.WIFE:
                new_person = await person_repo.create(
                    _new_person(family_id, name, gender, birth_date)
                )
                await relation_repo.add(family_id, add_spouse(relative_to_person_id, new_person.id))

            case RelationKind.BROTHER | RelationKind.SISTER:
                existing_parent_ids = [
                    r.parent_id for r in parent_relations if r.child_id == relative_to_person_id
                ]
                new_person = await person_repo.create(
                    _new_person(family_id, name, gender, birth_date)
                )
                if existing_parent_ids:
                    attachment = add_sibling(relative_to_person_id, new_person.id, parent_relations)
                else:
                    placeholder = await person_repo.create(_new_placeholder(family_id))
                    attachment = add_sibling(
                        relative_to_person_id,
                        new_person.id,
                        parent_relations,
                        family_id=family_id,
                        placeholder_id=placeholder.id,
                    )
                for edge in attachment.edges:
                    await relation_repo.add(family_id, edge)

        await _materialize_birthday(
            new_person,
            birth_date,
            family_id,
            event_repo,
            membership_repo,
            account_repo,
            reminder_repo,
            clock,
        )

        return new_person


async def _rematerialize_event_for_person(
    event: Event,
    membership: Membership,
    account: Account,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> None:
    """Пересчитывает будущие pending-напоминания одного человека по одному событию.

    Удаляет будущие pending-строки только этой пары (событие, человек) и строит
    заново — остальных получателей события не трогает. Применяет переопределение
    (SPEC 5.6): если для пары (account_id, event_id) есть ReminderOverride, его
    смещения и время суток перекрывают настройки аккаунта — переопределение не
    должно теряться при смене общих настроек аккаунта или даты события.

    Порог удаления — MISFIRE_GRACE назад от текущего момента, а не сам момент:
    materialize_for_event тем же порогом (not_before в reminders.py) решает,
    какие строки ещё стоит построить заново. Если бы порог удаления был уже
    (например, ровно `now`), строка с due_at_utc в промежутке [now-MISFIRE_GRACE,
    now) осталась бы неудалённой со старым due_at_utc, а materialize_for_event
    всё равно попытался бы построить для того же (event_id, person_id,
    occurrence_date, offset_days) новую — в SQLite её отбросит уникальный
    индекс (SPEC 5.3), и смена настроек молча не применилась бы к этой строке.
    """
    override = await override_repo.get(membership.account_id, event.id)
    effective_account = apply_override(account, override)
    await reminder_repo.delete_future_pending_for_event_and_person(
        event.id, membership.person_id, clock.now() - timedelta(seconds=MISFIRE_GRACE)
    )
    reminders = materialize_for_event(event, [(membership, effective_account)], clock)
    if reminders:
        await reminder_repo.add_many(reminders)


async def update_person(
    acting_account_id: int,
    person_id: int,
    name: str,
    gender: Gender,
    birth_date: date,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    membership_repo: MembershipRepo,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Person:
    """Правит запись человека. Смена даты рождения перестраивает будущие

    напоминания по его дню рождения (SPEC 5.6, критерий приёмки 7): дата
    события дня рождения (EventRepo.update) переставляется на новую
    birth_date, будущие pending-напоминания по нему пересчитываются для всех
    получателей семьи (переопределения, если есть, сохраняются — см.
    _rematerialize_event_for_person). У человека всегда есть ровно одно
    событие kind=BIRTHDAY — оно заводится один раз при создании (см.
    _materialize_birthday) и больше не дублируется. Отклоняет действие, если
    acting_account не владелец семьи (family_repo).

    Отклоняет и заглушку: у неё нет ни имени, ни пола, ни даты рождения по
    определению (SPEC 4.1), и нет события дня рождения, которое перестраивают
    ниже. Правка превратила бы её в обычного человека в обход слияния с
    настоящим родителем («первый настоящий родитель сливается с заглушкой»),
    и свободный слот второго родителя её дети потеряли бы вместе с ней.
    Настоящий человек заводится на её месте через add_person.
    """
    async with uow:
        person = await person_repo.get(person_id)
        await _require_owner(family_repo, person.family_id, acting_account_id)
        if person.is_placeholder:
            raise DomainError(
                DomainErrorCode.PLACEHOLDER,
                f"Запись {person_id} — заглушка неизвестного родителя, не редактируется",
            )

        birth_date_changed = person.birth_date != birth_date
        person.name = name
        person.gender = gender
        person.birth_date = birth_date
        person = await person_repo.update(person)

        if birth_date_changed:
            [birthday_event] = [
                e
                for e in await event_repo.list_by_person(person_id)
                if e.kind == EventKind.BIRTHDAY
            ]
            birthday_event.date = birth_date
            birthday_event = await event_repo.update(birthday_event)

            recipients = await _recipients(person.family_id, membership_repo, account_repo)
            for membership, account in recipients:
                await _rematerialize_event_for_person(
                    birthday_event, membership, account, override_repo, reminder_repo, clock
                )

        return person


async def delete_person(
    acting_account_id: int,
    person_id: int,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    membership_repo: MembershipRepo,
    invite_repo: InviteRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> None:
    """Удаляет человека физически, либо, если у него есть дети, превращает

    его в заглушку: имя, пол, дата рождения и привязка аккаунта стираются,
    события и напоминания удаляются, а рёбра остаются все (SPEC 4.3, критерий
    приёмки 22) — и к детям, и к его родителям, и к супругу. Заглушка живёт
    ровно для того, чтобы родство не рвалось: сними её ребро к отцу, и внук
    потеряет деда (SPEC 4.2 — родство выводится обходом графа глубиной до
    трёх). Когда на её место встанет настоящий родитель, merge_placeholder_into
    перенесёт ему все эти рёбра.

    Человек без детей удаляется физически, вместе со всеми своими рёбрами.
    Заглушка-родитель, у которой после этого не осталось ни одного ребёнка,
    удаляется вслед за ним (_drop_childless_placeholders): без детей она не
    несёт смысла.

    Вместе с записью отзываются все выданные на неё неиспользованные
    приглашения (SPEC 3.2: приглашение привязано к конкретной записи): иначе
    код продолжал бы работать, а привязывать по нему было бы нечего — записи
    либо нет вовсе, либо она стала заглушкой, которой issue_invite приглашение
    и не выдаёт. Уже использованные приглашения не трогаются: они хранят
    историю, отзывать в них нечего.

    Если у удалённого человека был привязан аккаунт и его current_family_id
    указывал на эту семью, текущая семья переводится на любую другую семью
    аккаунта, а если других нет — на None. Текущая семья определяет, к какой
    семье относятся команды бота (SPEC 2.1), и указывать на семью, в которой
    аккаунт больше не состоит, она не может.

    Отклоняет действие, если acting_account не владелец семьи (family_repo), и
    если владелец удаляет собственную запись: передача владения семьёй вне
    скоупа первой версии (SPEC 7), поэтому families.owner_account_id указывал
    бы на аккаунт, у которого в этой семье больше нет записи человека.
    """
    async with uow:
        person = await person_repo.get(person_id)
        await _require_owner(family_repo, person.family_id, acting_account_id)

        own = await membership_repo.get_by_account_and_family(acting_account_id, person.family_id)
        if own is not None and own.person_id == person_id:
            raise DomainError(
                DomainErrorCode.OWN_RECORD,
                f"Аккаунт {acting_account_id} не может удалить собственную запись",
            )

        now = clock.now()
        for invite in await invite_repo.list_by_person(person_id):
            if invite.used_at is None and invite.revoked_at is None:
                await invite_repo.revoke(invite.id, now)

        for event in await event_repo.list_by_person(person_id):
            await reminder_repo.delete_all_for_event(event.id)
            await event_repo.delete(event.id)
        await reminder_repo.delete_all_for_person(person_id)

        freed = await membership_repo.get_by_person(person_id)
        await membership_repo.delete(person_id)
        if freed is not None:
            await _drop_current_family(
                freed.account_id, person.family_id, account_repo, membership_repo
            )

        relations = await relation_repo.list_by_family(person.family_id)
        has_children = any(isinstance(r, ParentOf) and r.parent_id == person_id for r in relations)

        if has_children:
            person.name = None
            person.gender = None
            person.birth_date = None
            person.is_placeholder = True
            await person_repo.update(person)
            return

        parent_ids = {
            r.parent_id for r in relations if isinstance(r, ParentOf) and r.child_id == person_id
        }
        for relation in relations:
            if (isinstance(relation, ParentOf) and relation.child_id == person_id) or (
                isinstance(relation, SpouseOf) and person_id in (relation.a_id, relation.b_id)
            ):
                await relation_repo.remove(person.family_id, relation)
        await person_repo.delete(person_id)
        await _drop_childless_placeholders(parent_ids, person.family_id, person_repo, relation_repo)


async def _drop_current_family(
    account_id: int,
    left_family_id: int,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
) -> None:
    """Уводит current_family_id аккаунта с семьи, в которой он больше не состоит.

    Вызывается после удаления Membership, поэтому list_by_account уже не
    содержит покинутую семью: подходит любая из оставшихся (SPEC 2.1 — выбор
    текущей семьи всё равно за пользователем, здесь важно лишь не оставить
    ссылку на чужую). Если других семей нет, остаётся None — как у только что
    созданного аккаунта.
    """
    account = await account_repo.get(account_id)
    if account.current_family_id != left_family_id:
        return

    remaining = await membership_repo.list_by_account(account_id)
    account.current_family_id = remaining[0].family_id if remaining else None
    await account_repo.save(account)


async def _drop_childless_placeholders(
    candidate_ids: set[int],
    family_id: int,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
) -> None:
    """Удаляет заглушки из `candidate_ids`, у которых больше не осталось детей.

    Заглушка нужна только чтобы связать братьев и сестёр между собой (SPEC 4.1);
    без детей она не несёт смысла, а показать её пользователю нельзя.

    Перед удалением снимаются все рёбра заглушки, а не только рёбра к детям:
    у заглушки, в которую превратился человек с детьми (SPEC 4.3), законно
    есть и свои родители, и супруг. Оставить их висеть нельзя — в SQLite при
    `PRAGMA foreign_keys=ON` (SPEC 6.2) висячее ребро либо не даст удалить
    строку, либо утащит каскадом чужие рёбра.

    Обход идёт вверх по цепочке по той же причине: снятое ребро могло быть
    последним ребёнком заглушки этажом выше. `candidate_ids` перебирается как
    очередь, каждый узел рассматривается один раз.
    """
    queue = set(candidate_ids)
    deleted: set[int] = set()
    while queue:
        candidate_id = queue.pop()
        if candidate_id in deleted:
            continue
        candidate = await person_repo.get(candidate_id)
        if not candidate.is_placeholder:
            continue

        relations = await relation_repo.list_by_family(family_id)
        if any(isinstance(r, ParentOf) and r.parent_id == candidate_id for r in relations):
            continue

        for relation in relations:
            if isinstance(relation, ParentOf) and relation.child_id == candidate_id:
                queue.add(relation.parent_id)
                await relation_repo.remove(family_id, relation)
            elif isinstance(relation, SpouseOf) and candidate_id in (relation.a_id, relation.b_id):
                await relation_repo.remove(family_id, relation)

        await person_repo.delete(candidate_id)
        deleted.add(candidate_id)


def _generate_invite_code() -> str:
    return secrets.token_urlsafe(INVITE_CODE_BYTES)


async def issue_invite(
    acting_account_id: int,
    person_id: int,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    membership_repo: MembershipRepo,
    invite_repo: InviteRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Invite:
    """Одноразовое приглашение сроком на семь дней.

    Отклоняет, если acting_account не владелец семьи (family_repo), и если
    запись уже привязана к какому-то аккаунту (критерий приёмки 26) — привязка
    проверяется по MembershipRepo.get_by_person, а не по факту существования
    приглашения: прежние отозванные или просроченные приглашения на ту же
    запись не мешают выдать новое. Отклоняет и заглушку (SPEC 4.1: без имени,
    пола и даты рождения, не показывается в списках) — приглашение по SPEC 3.2
    показывает получателю «Антон приглашает вас как Марину», а заглушке
    показывать нечего.
    """
    async with uow:
        person = await person_repo.get(person_id)
        await _require_owner(family_repo, person.family_id, acting_account_id)

        if person.is_placeholder:
            raise DomainError(
                DomainErrorCode.PLACEHOLDER,
                f"Запись {person_id} — заглушка неизвестного родителя, не приглашается",
            )
        if await membership_repo.get_by_person(person_id) is not None:
            raise DomainError(
                DomainErrorCode.ALREADY_LINKED, f"Запись {person_id} уже привязана к аккаунту"
            )

        now = clock.now()
        return await invite_repo.create(
            Invite(
                id=0,
                family_id=person.family_id,
                person_id=person_id,
                code=_generate_invite_code(),
                created_at=now,
                expires_at=now + INVITE_TTL,
            )
        )


async def revoke_invite(
    acting_account_id: int,
    invite_id: int,
    family_repo: FamilyRepo,
    invite_repo: InviteRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> None:
    """Отзывает ещё не использованное приглашение (SPEC 3.2, критерий приёмки 25).

    Отклоняет действие, если acting_account не владелец семьи, которой
    принадлежит приглашение, и если оно уже использовано — отзывать больше
    нечего, аккаунт уже привязан.
    """
    async with uow:
        invite = await invite_repo.get(invite_id)
        await _require_owner(family_repo, invite.family_id, acting_account_id)
        if invite.used_at is not None:
            raise DomainError(
                DomainErrorCode.INVITE_USED, f"Приглашение {invite_id} уже использовано"
            )
        await invite_repo.revoke(invite_id, clock.now())


def _require_usable_invite(invite: Invite | None, now: datetime) -> Invite:
    """Отказы по самому коду (SPEC 3.2, критерий приёмки 25): нет, отозван, использован, просрочен.

    Общая проверка для `preview_invite` и `accept_invite`: бот показывает
    «Антон приглашает вас как Марину» до подтверждения, и обе стороны обязаны
    отклонять один и тот же набор кодов — иначе бот спросил бы часовой пояс по
    коду, который сценарий всё равно не примет.
    """
    if invite is None:
        raise DomainError(DomainErrorCode.INVITE_NOT_FOUND, "Приглашение не найдено")
    if invite.revoked_at is not None:
        raise DomainError(DomainErrorCode.INVITE_REVOKED, "Приглашение отозвано")
    if invite.used_at is not None:
        raise DomainError(DomainErrorCode.INVITE_USED, "Приглашение уже использовано")
    if now > invite.expires_at:
        raise DomainError(DomainErrorCode.INVITE_EXPIRED, "Приглашение просрочено")
    return invite


async def _require_invited_person(invite: Invite, person_repo: PersonRepo) -> Person:
    """Запись, на которую выдан код: её может уже не быть или она стала заглушкой (SPEC 3.2).

    Ищется среди людей семьи, а не через PersonRepo.get: тот обязан вернуть
    Person и на отсутствующий id ответит ошибкой хранилища, а здесь нужен
    обычный отказ сценария.
    """
    person = next(
        (p for p in await person_repo.list_by_family(invite.family_id) if p.id == invite.person_id),
        None,
    )
    if person is None:
        raise DomainError(
            DomainErrorCode.INVITED_PERSON_GONE,
            f"Запись {invite.person_id} не найдена — приглашение недействительно",
        )
    if person.is_placeholder:
        raise DomainError(
            DomainErrorCode.PLACEHOLDER,
            f"Запись {invite.person_id} — заглушка неизвестного родителя",
        )
    return person


@dataclass(frozen=True, slots=True)
class InvitePreview:
    """Кто кого приглашает — то, что бот показывает до подтверждения (SPEC 3.2)."""

    family_id: int
    person_id: int
    person_name: str
    inviter_name: str | None


async def preview_invite(
    code: str,
    telegram_user_id: int,
    invite_repo: InviteRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
    family_repo: FamilyRepo,
    membership_repo: MembershipRepo,
    clock: Clock,
) -> InvitePreview:
    """Проверяет код и возвращает имена для строки «Антон приглашает вас как Марину».

    Отказы те же, что у accept_invite (кроме неизвестного пояса — его тут ещё не
    спрашивали): смысл в том, чтобы бот отклонил негодный код до вопроса про
    часовой пояс, а не после. Ничего не пишет, поэтому единицы работы не
    открывает.

    Имя приглашающего — это запись владельца семьи в ней же. Её может не быть
    (владелец не может удалить собственную запись, но семья могла быть заведена
    иначе), поэтому `inviter_name` необязательное: бот тогда обходится одним
    именем получателя.
    """
    invite = _require_usable_invite(await invite_repo.get_by_code(code), clock.now())
    if await membership_repo.get_by_person(invite.person_id) is not None:
        raise DomainError(
            DomainErrorCode.ALREADY_LINKED, f"Запись {invite.person_id} уже привязана к аккаунту"
        )
    person = await _require_invited_person(invite, person_repo)

    account = await account_repo.get_by_telegram_user_id(telegram_user_id)
    if (
        account is not None
        and await membership_repo.get_by_account_and_family(account.id, invite.family_id)
        is not None
    ):
        raise DomainError(
            DomainErrorCode.ALREADY_IN_FAMILY,
            f"Аккаунт {account.id} уже состоит в семье {invite.family_id}",
        )

    family = await family_repo.get(invite.family_id)
    owner = await membership_repo.get_by_account_and_family(
        family.owner_account_id, invite.family_id
    )
    inviter_name: str | None = None
    if owner is not None:
        inviter = next(
            (
                p
                for p in await person_repo.list_by_family(invite.family_id)
                if p.id == owner.person_id
            ),
            None,
        )
        inviter_name = None if inviter is None else inviter.name

    return InvitePreview(
        family_id=invite.family_id,
        person_id=invite.person_id,
        person_name=person.name or "",
        inviter_name=inviter_name,
    )


async def accept_invite(
    code: str,
    telegram_user_id: int,
    timezone: str,
    invite_repo: InviteRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    event_repo: EventRepo,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Person:
    """Привязывает Telegram-аккаунт к записи, на которую выдано приглашение.

    Отклоняет просроченный, уже использованный или отозванный код (критерий
    приёмки 25) и запись, к которой уже привязан аккаунт (критерий 26, тот же
    случай, что и в issue_invite — приглашение могло быть выдано раньше, чем
    запись оказалась занята другим приглашением). Код привязан к конкретной
    записи (критерий 24): чужой код не даёт привязаться ни к какой другой —
    person_id берётся из самого приглашения, а не передаётся вызывающим кодом.
    Отклоняет и случай, когда у аккаунта уже есть своя запись в этой самой
    семье (SPEC 5.3: не больше одной записи человека на пару account_id и
    family_id) — иначе в memberships оказалось бы два ряда с одинаковой парой.

    Если у telegram-аккаунта уже есть запись (он раньше создал или принял
    приглашение в другую семью, см. SPEC 2.1), она переиспользуется как есть и
    `timezone` учитывается только при первом создании аккаунта — так же, как
    в create_family. Новый участник сразу получает напоминания обо всех уже
    существующих событиях своей семьи (SPEC 5.6: «добавлен человек... привязан
    аккаунт» — повод для материализации), кроме собственного дня рождения, с
    учётом уже выставленных на эти события переопределений (SPEC 5.3, 5.6):
    аккаунт мог состоять в этой семье раньше и оставить ReminderOverride —
    удаление записи снимает привязку, но переопределения не трогает (SPEC 3.4),
    поэтому при возврате они снова вступают в силу.

    Отклоняет и приглашение на запись, которой уже нет или которая стала
    заглушкой. Удаление человека отзывает его приглашения само (delete_person),
    так что это вторая линия защиты, независимая от первой: привязывать аккаунт
    к заглушке нельзя — у неё нет ни имени, чтобы показать «Антон приглашает
    вас как Марину» (SPEC 3.2), ни даты рождения, ни события дня рождения.
    Проверка идёт до создания аккаунта, поэтому при отказе в хранилище не
    остаётся следов вызова. По той же причине первым проверяется `timezone`
    (SPEC 3.4): иначе неизвестный пояс уронил бы материализацию уже после
    mark_used, то есть одноразовый код сгорел бы ни за что.
    """
    async with uow:
        _require_known_timezone(timezone)

        now = clock.now()
        invite = _require_usable_invite(await invite_repo.get_by_code(code), now)
        if await membership_repo.get_by_person(invite.person_id) is not None:
            raise DomainError(
                DomainErrorCode.ALREADY_LINKED,
                f"Запись {invite.person_id} уже привязана к аккаунту",
            )
        person = await _require_invited_person(invite, person_repo)

        account = await account_repo.get_by_telegram_user_id(telegram_user_id)
        if account is None:
            account = await account_repo.save(
                Account(
                    id=0,
                    telegram_user_id=telegram_user_id,
                    current_family_id=None,
                    timezone=timezone,
                    offsets_days=DEFAULT_OFFSETS_DAYS,
                    time_of_day=DEFAULT_TIME_OF_DAY,
                )
            )
        elif (
            await membership_repo.get_by_account_and_family(account.id, invite.family_id)
            is not None
        ):
            raise DomainError(
                DomainErrorCode.ALREADY_IN_FAMILY,
                f"Аккаунт {account.id} уже состоит в семье {invite.family_id}",
            )

        membership = await membership_repo.create(
            Membership(
                account_id=account.id, family_id=invite.family_id, person_id=invite.person_id
            )
        )
        await invite_repo.mark_used(invite.id, now)

        if account.current_family_id is None:
            account.current_family_id = invite.family_id
            account = await account_repo.save(account)

        reminders: list[Reminder] = []
        for event in await event_repo.list_by_family(invite.family_id):
            override = await override_repo.get(account.id, event.id)
            effective_account = account
            if override is not None:
                effective_account = replace(
                    account, offsets_days=override.offsets_days, time_of_day=override.time_of_day
                )
            reminders.extend(materialize_for_event(event, [(membership, effective_account)], clock))
        if reminders:
            await reminder_repo.add_many(reminders)

        return person


async def add_event(
    acting_account_id: int,
    family_id: int,
    person_id: int,
    title: str,
    event_date: date,
    is_recurring_yearly: bool,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Event:
    """Добавляет событие и сразу материализует по нему напоминания.

    `is_recurring_yearly` различает ежегодное событие (например, годовщина
    свадьбы) и разовое (SPEC 3.3, 4). Отклоняет действие, если acting_account
    не владелец семьи (family_repo), и если person_id не принадлежит family_id.

    Отклоняет и заглушку: она не порождает событий (SPEC 4.1) — имени у неё
    нет, так что текст напоминания ушёл бы всей семье с пустым получателем.
    """
    async with uow:
        await _require_owner(family_repo, family_id, acting_account_id)

        person = await person_repo.get(person_id)
        if person.family_id != family_id:
            raise DomainError(
                DomainErrorCode.PERSON_NOT_IN_FAMILY,
                f"Человек {person_id} не принадлежит семье {family_id}",
            )
        if person.is_placeholder:
            raise DomainError(
                DomainErrorCode.PLACEHOLDER,
                f"Запись {person_id} — заглушка неизвестного родителя, событий не имеет",
            )

        event = await event_repo.create(
            Event(
                id=0,
                family_id=family_id,
                person_id=person_id,
                title=title,
                date=event_date,
                is_recurring_yearly=is_recurring_yearly,
                kind=EventKind.CUSTOM,
            )
        )
        recipients = await _recipients(family_id, membership_repo, account_repo)
        reminders = materialize_for_event(event, recipients, clock)
        if reminders:
            await reminder_repo.add_many(reminders)

        return event


async def update_account_settings(
    account_id: int,
    timezone: str,
    offsets_days: tuple[int, ...],
    time_of_day: time,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    event_repo: EventRepo,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Account:
    """Меняет пояс, смещения и время суток. Настройки принадлежат аккаунту, а не

    семье: перестраивает будущие напоминания во всех его семьях сразу (SPEC
    2.1, 5.6, критерий приёмки 6), по каждому активному событию каждой из них.
    Переопределения на отдельные события (ReminderOverrideRepo) сохраняют силу
    — их учитывает _rematerialize_event_for_person. Уже отправленные
    напоминания не трогает: пересчитываются только будущие pending-строки.

    Ввод проверяется целиком до первой записи (_require_known_timezone,
    _require_valid_offsets): иначе аккаунт остался бы с новыми настройками,
    а перематериализация упала бы на середине, оставив часть семей с
    напоминаниями по старым настройкам и часть — вообще без будущих строк.
    """
    async with uow:
        _require_known_timezone(timezone)
        _require_valid_offsets(offsets_days)

        account = await account_repo.get(account_id)
        account.timezone = timezone
        account.offsets_days = offsets_days
        account.time_of_day = time_of_day
        account = await account_repo.save(account)

        for membership in await membership_repo.list_by_account(account_id):
            for event in await event_repo.list_by_family(membership.family_id):
                await _rematerialize_event_for_person(
                    event, membership, account, override_repo, reminder_repo, clock
                )

        return account


async def set_override(
    account_id: int,
    event_id: int,
    offsets_days: tuple[int, ...],
    time_of_day: time,
    override_repo: ReminderOverrideRepo,
    event_repo: EventRepo,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> ReminderOverride:
    """Переопределяет смещения и время суток для пары (аккаунт, событие) и

    перестраивает будущие напоминания по этому событию для этого аккаунта
    (SPEC 5.6).

    Отклоняет аккаунт без Membership в семье события: переопределение есть
    только у участника (SPEC 2 — участник меняет свои смещения и своё время
    суток; SPEC 3.4 — переопределяется набор для события, о котором он получает
    напоминания). Посторонний аккаунт напоминаний по этой семье не получает,
    так что переопределять ему нечего.

    Обратное неверно: уже сохранённое переопределение переживает выход из
    семьи — delete_person снимает привязку аккаунта, а reminder_overrides не
    трогает. Если тот же аккаунт вернётся в семью, accept_invite прочитает
    переопределение при первой же материализации.

    Ввод проверяется до записи (_require_valid_offsets), как в
    update_account_settings: смещения переопределения попадают в те же
    reminders.
    """
    async with uow:
        _require_valid_offsets(offsets_days)

        event = await event_repo.get(event_id)
        membership = await membership_repo.get_by_account_and_family(account_id, event.family_id)
        if membership is None:
            raise DomainError(
                DomainErrorCode.NOT_MEMBER,
                f"Аккаунт {account_id} не состоит в семье {event.family_id}",
            )

        override = ReminderOverride(
            account_id=account_id,
            event_id=event_id,
            offsets_days=offsets_days,
            time_of_day=time_of_day,
        )
        await override_repo.set(override)

        account = await account_repo.get(account_id)
        await _rematerialize_event_for_person(
            event, membership, account, override_repo, reminder_repo, clock
        )

        return override


async def clear_override(
    account_id: int,
    event_id: int,
    override_repo: ReminderOverrideRepo,
    event_repo: EventRepo,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> None:
    """Снимает переопределение пары «аккаунт и событие» и возвращает будущие
    напоминания к общим настройкам аккаунта.

    SPEC 5.6 называет снятие переопределения поводом для пересчёта наравне с
    его добавлением: без пересчёта будущие pending-строки остались бы стоять по
    снятым смещениям и времени суток, то есть снятие не применилось бы до
    следующей правки.

    Как и set_override, отклоняет аккаунт без Membership в семье события:
    переопределение есть только у участника, постороннему снимать нечего.

    Снятие отсутствующего переопределения ошибкой не считается: пользователь
    видит в боте одну кнопку «вернуть общие настройки», и её повторное нажатие
    (или нажатие, когда переопределения и не было) должно быть безобидным.
    Но и пересчёта в этом случае не происходит: повод из SPEC 5.6 — снятое
    переопределение, а снимать было нечего. Иначе пересчёт впустую удалил бы и
    заново вставил те же будущие строки, сменив им id на ровном месте.
    """
    async with uow:
        event = await event_repo.get(event_id)
        membership = await membership_repo.get_by_account_and_family(account_id, event.family_id)
        if membership is None:
            raise DomainError(
                DomainErrorCode.NOT_MEMBER,
                f"Аккаунт {account_id} не состоит в семье {event.family_id}",
            )

        if await override_repo.get(account_id, event_id) is None:
            return
        await override_repo.revoke(account_id, event_id)

        account = await account_repo.get(account_id)
        await _rematerialize_event_for_person(
            event, membership, account, override_repo, reminder_repo, clock
        )


async def set_current_family(
    account_id: int,
    family_id: int,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    uow: UnitOfWork,
) -> Account:
    """Меняет текущую семью аккаунта, к которой относятся команды добавления и просмотра.

    Отклоняет семью, в которой у аккаунта нет своей записи (membership_repo).
    """
    async with uow:
        if await membership_repo.get_by_account_and_family(account_id, family_id) is None:
            raise DomainError(
                DomainErrorCode.NOT_MEMBER,
                f"Аккаунт {account_id} не состоит в семье {family_id}",
            )

        account = await account_repo.get(account_id)
        account.current_family_id = family_id
        return await account_repo.save(account)


class SkipReason(Enum):
    """Почему напоминание не отправляется (SPEC 5.5).

    MISSED — срок прошёл: простой сервиса дольше суток или дата события уже
    позади, отправлять поздно. UNDELIVERABLE — отправлять некуда: у записи
    получателя больше нет аккаунта, у аккаунта нет привязанного чата или
    доставка ему отключена после 403. Разница важна тику: в первом случае
    строка закрывается как missed, во втором как failed.
    """

    MISSED = "missed"
    UNDELIVERABLE = "undeliverable"


@dataclass(frozen=True, slots=True)
class SendPlan:
    """Что тику отправить по одному напоминанию: куда, какой текст и чей аккаунт.

    `account_id` нужен на случай 403: доставка отключается аккаунту получателя
    (disable_delivery), а искать его заново по напоминанию тику уже не надо.
    """

    account_id: int
    chat_id: int
    text: str


async def plan_reminder_delivery(
    reminder: Reminder,
    at: datetime,
    account_repo: AccountRepo,
    event_repo: EventRepo,
    membership_repo: MembershipRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
) -> SendPlan | SkipReason:
    """Собирает текст и адрес одной отправки на момент `at` — либо причину не отправлять.

    Вызывается тиком после claim и до send (SPEC 5.5), поэтому ничего не пишет:
    единственная задача — превратить строку `reminders` в готовое сообщение.

    Текст считается на момент отправки, а не материализации (критерий приёмки
    13): «через N дней» берётся из даты в поясе получателя прямо сейчас, так
    что напоминание, ушедшее с опозданием через сутки, говорит о сутках
    меньших. Родство выводится так же на момент отправки — дерево могло
    измениться после материализации.
    """
    membership = await membership_repo.get_by_person(reminder.person_id)
    if membership is None:
        return SkipReason.UNDELIVERABLE
    account = await account_repo.get(membership.account_id)
    # Просрочка проверяется раньше доставки: строка, у которой срок прошёл,
    # по SPEC 5.5 становится missed независимо от того, куда её собирались
    # отправить. Иначе аккаунт, заблокировавший бота во время простоя, получил
    # бы failed там, где критерий приёмки 12 требует missed.
    if is_overdue(reminder, at, account.timezone):
        return SkipReason.MISSED
    if not account.delivery_enabled or account.chat_id is None:
        return SkipReason.UNDELIVERABLE

    text = await compose_reminder_text(
        reminder, membership, account, at, event_repo, person_repo, relation_repo
    )
    return SendPlan(account_id=account.id, chat_id=account.chat_id, text=text)


async def compose_reminder_text(
    reminder: Reminder,
    membership: Membership,
    account: Account,
    at: datetime,
    event_repo: EventRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
) -> str:
    """Собирает текст одного напоминания на момент `at` (критерий приёмки 13).

    Отсчёт «через N дней» и формулировка даты берутся от «сегодня» получателя, а
    не по UTC: для аккаунта в Токио вечер 22 февраля по UTC — это уже 23-е, и
    «через 7 дней» там было бы неправдой. Поэтому локальная дата считается в
    поясе аккаунта, а не в зоне сервера.

    Родство выводится по текущему графу семьи (infer_relation_text), а не по
    состоянию на момент материализации: дерево могло измениться, и тогда
    «ваш отец» в готовом тексте оказалось бы устаревшим.

    Тик эту функцию только вызывает: вся формулировка живёт в ядре, чтобы её
    можно было проверить без транспорта и повторно использовать в любом другом
    слое (SPEC 5.1).
    """
    event = await event_repo.get(reminder.event_id)
    people = {person.id: person for person in await person_repo.list_by_family(event.family_id)}
    relations = await relation_repo.list_by_family(event.family_id)
    hero = people[event.person_id]

    today_for_recipient = at.astimezone(ZoneInfo(account.timezone)).date()
    return reminder_text(
        name=hero.name or "",
        relation_phrase=infer_relation_text(
            membership.person_id, event.person_id, people, relations
        ),
        days_until=(reminder.occurrence_date - today_for_recipient).days,
        event_title=event.title,
        is_birthday=event.is_birthday,
        is_recurring_yearly=event.is_recurring_yearly,
        event_date=reminder.occurrence_date,
        years=age_on(event.date, reminder.occurrence_date),
    )


async def disable_delivery(
    account_id: int,
    at: datetime,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
) -> int:
    """403: получатель заблокировал бота — снимаем привязку чата и отключаем доставку.

    SPEC 5.5: новых напоминаний такому аккаунту не строят (materialize_for_event
    пропускает delivery_enabled=False). Уже построенные pending-строки
    закрываются здесь же как failed, во всех семьях аккаунта: каждая следующая
    отправка вернула бы тот же 403, и держать их до конца горизонта значит
    стучаться в Telegram зря на каждом тике. Обратный путь — enable_delivery:
    закрытые строки будущего он удаляет и строит заново, иначе аккаунт,
    вернувшийся к боту, остался бы без напоминаний по уже построенному
    горизонту (уникальный индекс SPEC 5.3 на статус не смотрит).

    Возвращает число закрытых строк. Своей единицы работы не открывает:
    вызывается тиком внутри транзакции шага mark, а вложенных транзакций порт
    UnitOfWork не обещает.
    """
    account = await account_repo.get(account_id)
    await account_repo.save(replace(account, chat_id=None, delivery_enabled=False))
    closed = 0
    for membership in await membership_repo.list_by_account(account_id):
        closed += await reminder_repo.fail_pending_for_person(membership.person_id, at)
    return closed


async def enable_delivery(
    account_id: int,
    chat_id: int,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
    event_repo: EventRepo,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> Account:
    """Повторный запуск бота: привязываем чат, включаем доставку, возвращаем напоминания.

    SPEC 5.5 отключает доставку «до повторного запуска бота» — значит после него
    напоминания обязаны пойти снова. Одной записи `delivery_enabled=True` для
    этого мало: строки, закрытые disable_delivery как failed, никуда не делись, а
    уникальный индекс (SPEC 5.3) не позволит построить на их место новые. Поэтому
    будущие failed-строки сначала удаляются, а потом горизонт строится заново по
    всем событиям всех семей аккаунта — ровно так же, как при смене его настроек.

    Пересчёт делается только если доставка и правда была отключена. Обычный
    /start от живого аккаунта — это просто «бот, вот мой чат»: трогать его
    напоминания не за что, а любое удаление и перестройка здесь рискуют второй
    отправкой. Сам /start пользователь жмёт когда угодно, в том числе через
    минуту после того, как сообщение ушло.

    Что именно восстанавливать, решает `attempts` — единственный след того, что
    `send` по строке вызывался.

    Строка с отправкой в прошлом остаётся закрытой: сообщение могло дойти. Так
    закрываются строка, на которой пришёл 403, строка, застрявшая в `sending`
    после падения между send и mark (её закрыл fail_stuck_sending при старте), и
    строка, исчерпавшая пять попыток. Воскресишь такую — и следующий тик отправит
    то же сообщение второй раз, а SPEC 5.5 считает повтор хуже пропуска.

    Строка без отправки восстанавливается и в прошлом, в пределах окна просрочки
    (`MISFIRE_GRACE`). Иначе терялось бы вот что: 403 приходит на одну строку, а
    `disable_delivery` закрывает заодно весь горизонт аккаунта, и напоминания
    одного аккаунта приходятся на одно местное время, то есть в ту же минуту
    обычно наступило ещё несколько. По ним `send` не вызывался ни разу, и SPEC 4
    с критерием приёмки 12 требуют доставить их, если опоздание меньше суток и
    дата события ещё впереди.

    Вызывает этот сценарий Telegram-слой на /start (этап 6): ядро о транспорте
    ничего не знает и получает только chat_id.
    """
    async with uow:
        account = await account_repo.get(account_id)
        was_disabled = not account.delivery_enabled
        account = await account_repo.save(replace(account, chat_id=chat_id, delivery_enabled=True))
        if not was_disabled:
            return account
        now = clock.now()
        recoverable_since = now - timedelta(seconds=MISFIRE_GRACE)
        for membership in await membership_repo.list_by_account(account_id):
            await reminder_repo.delete_recoverable_failed_for_person(
                membership.person_id, now, recoverable_since
            )
            for event in await event_repo.list_by_family(membership.family_id):
                await _rematerialize_event_for_person(
                    event, membership, account, override_repo, reminder_repo, clock
                )
        return account


# Порядок групп в /persons: сам, родители, супруги, дети, остальные (SPEC 3.5).
_GROUP_SELF = 0
_GROUP_BY_CATEGORY = {"parent": 1, "spouse": 2, "child": 3}
_GROUP_OTHER = 4


def _name_sort_key(name: str) -> str:
    return name.casefold().replace("ё", "е")


async def list_family_persons(
    account_id: int,
    account_repo: AccountRepo,
    family_repo: FamilyRepo,
    membership_repo: MembershipRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    event_repo: EventRepo,
    clock: Clock,
    uow: UnitOfWork,
) -> texts.FamilyPersons | None:
    """Люди текущей семьи аккаунта для /persons; None — если текущей семьи нет.

    Состав один для всех участников семьи; родство, пометка «это вы» и порядок
    групп считаются от человека, за которого аккаунт состоит в семье. Группы:
    сам, родители, супруги, дети, остальные; внутри — по имени без учёта регистра
    и «ё», затем по id. Заглушки не показываются. Возраст — на сегодняшнюю дату
    в поясе аккаунта. Всё читается в одной транзакции.
    """
    async with uow:
        account = await account_repo.get(account_id)
        if account is None or account.current_family_id is None:
            return None
        family = await family_repo.get(account.current_family_id)
        if family is None:
            return None
        viewer_id = next(
            (
                membership.person_id
                for membership in await membership_repo.list_by_account(account_id)
                if membership.family_id == family.id
            ),
            None,
        )
        if viewer_id is None:
            return None

        people = {person.id: person for person in await person_repo.list_by_family(family.id)}
        relations = await relation_repo.list_by_family(family.id)
        events_by_person: dict[int, list[Event]] = {}
        for event in await event_repo.list_by_family(family.id):
            if event.kind is not EventKind.BIRTHDAY:
                events_by_person.setdefault(event.person_id, []).append(event)

    today = clock.now().astimezone(ZoneInfo(account.timezone)).date()

    ranked: list[tuple[int, str, int, texts.PersonEntry]] = []
    for person in people.values():
        # У настоящего человека дата рождения обязательна (SPEC 3.1); заглушка не показывается.
        if person.is_placeholder or person.birth_date is None or person.name is None:
            continue
        category = infer_relation_category(viewer_id, person.id, people, relations)
        if person.id == viewer_id:
            group = _GROUP_SELF
        else:
            group = _GROUP_BY_CATEGORY.get(category or "", _GROUP_OTHER)
        events = sorted(
            events_by_person.get(person.id, []),
            key=lambda e: (e.date.month, e.date.day, e.title),
        )
        entry = texts.PersonEntry(
            name=person.name,
            birth_date=person.birth_date,
            age=age_as_of(person.birth_date, today),
            relation=relation_word_bare(category, person.gender) if category else "",
            is_self=person.id == viewer_id,
            events=tuple(events),
        )
        ranked.append((group, _name_sort_key(person.name), person.id, entry))

    ranked.sort(key=lambda item: item[:3])
    return texts.FamilyPersons(
        family_name=family.name,
        is_owner=family.owner_account_id == account_id,
        entries=tuple(item[3] for item in ranked),
    )
