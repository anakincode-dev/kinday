"""Сценарии: создать семью, добавить человека, выдать приглашение и так далее.

Единственное место, где встречаются модели, порты и остальные модули core.
Внешние слои (telegram, будущий api) вызывают только эти функции.
"""

from __future__ import annotations

from datetime import date, time

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
    ReminderOverride,
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
)
from kinday.core.relations import RelationKind, add_sibling, add_spouse, attach_parent
from kinday.core.reminders import materialize_for_event

DEFAULT_OFFSETS_DAYS: tuple[int, ...] = (7, 1, 0)
DEFAULT_TIME_OF_DAY = time(9, 0)

# Заведомо невозможный id человека: репозитории выдают только положительные id.
# Используется, чтобы прогнать attach_parent «вхолостую» и убедиться, что она
# не бросит исключение, прежде чем создавать нового человека в хранилище —
# см. комментарий в add_person.
_DRY_RUN_PERSON_ID = -1


class NotFamilyOwner(Exception):
    """Действие над деревом семьи доступно только владельцу (SPEC 2, критерий приёмки 27)."""


def _new_person(family_id: int, name: str, gender: Gender, birth_date: date) -> Person:
    return Person(id=0, family_id=family_id, name=name, gender=gender, birth_date=birth_date)


def _new_placeholder(family_id: int) -> Person:
    return Person(
        id=0, family_id=family_id, name=None, gender=None, birth_date=None, is_placeholder=True
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
) -> Family:
    """Создаёт семью, человека-владельца и привязку его Telegram-аккаунта.

    Если у telegram-аккаунта уже есть запись (он создаёт вторую семью, см. SPEC
    2.1), она переиспользуется как есть: настройки напоминаний принадлежат
    аккаунту целиком и не зависят от того, в скольких семьях он состоит,
    поэтому `owner_timezone` учитывается только при первом создании аккаунта.
    """
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
    """
    await _require_owner(family_repo, family_id, acting_account_id)

    all_relations = await relation_repo.list_by_family(family_id)
    parent_relations = [r for r in all_relations if isinstance(r, ParentOf)]
    people = {p.id: p for p in await person_repo.list_by_family(family_id)}
    if relative_to_person_id not in people:
        raise ValueError(f"Человек {relative_to_person_id} не найден в семье {family_id}")

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
                parent_relations,
                also_parent_of_siblings,
                people,
            )
            new_person = await person_repo.create(_new_person(family_id, name, gender, birth_date))
            for edge in change.removed:
                await relation_repo.remove(family_id, edge)
            if change.removed_placeholder_id is not None:
                await person_repo.delete(change.removed_placeholder_id)
            for edge in change.added:
                assert edge.parent_id == _DRY_RUN_PERSON_ID
                await relation_repo.add(
                    family_id, ParentOf(parent_id=new_person.id, child_id=edge.child_id)
                )

        case RelationKind.SON | RelationKind.DAUGHTER:
            new_person = await person_repo.create(_new_person(family_id, name, gender, birth_date))
            change = attach_parent(new_person.id, relative_to_person_id, [], None, {})
            for edge in change.added:
                await relation_repo.add(family_id, edge)

        case RelationKind.HUSBAND | RelationKind.WIFE:
            new_person = await person_repo.create(_new_person(family_id, name, gender, birth_date))
            await relation_repo.add(family_id, add_spouse(relative_to_person_id, new_person.id))

        case RelationKind.BROTHER | RelationKind.SISTER:
            existing_parent_ids = [
                r.parent_id for r in parent_relations if r.child_id == relative_to_person_id
            ]
            new_person = await person_repo.create(_new_person(family_id, name, gender, birth_date))
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
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Person:
    """Правит запись человека. Смена даты рождения перестраивает будущие

    напоминания по его дню рождения (SPEC 5.6, критерий приёмки 7).
    Отклоняет действие, если acting_account не владелец семьи (family_repo).
    """
    raise NotImplementedError


async def delete_person(
    acting_account_id: int,
    person_id: int,
    event_repo: EventRepo,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    reminder_repo: ReminderRepo,
) -> None:
    """Удаляет человека физически, либо, если у него есть дети, превращает

    его в заглушку: имя, пол, дата рождения и привязка аккаунта стираются,
    события и напоминания удаляются, рёбра к детям остаются (SPEC 4.3,
    критерий приёмки 22). Отклоняет действие, если acting_account не владелец
    семьи (family_repo).
    """
    raise NotImplementedError


async def issue_invite(
    acting_account_id: int,
    person_id: int,
    invite_repo: InviteRepo,
    clock: Clock,
) -> Invite:
    """Одноразовое приглашение сроком на семь дней. Отклоняет, если человек уже привязан."""
    raise NotImplementedError


async def revoke_invite(
    acting_account_id: int,
    invite_id: int,
    invite_repo: InviteRepo,
) -> None:
    """Отзывает ещё не использованное приглашение (SPEC 3.2, критерий приёмки 25)."""
    raise NotImplementedError


async def accept_invite(
    code: str,
    telegram_user_id: int,
    timezone: str,
    invite_repo: InviteRepo,
    person_repo: PersonRepo,
    account_repo: AccountRepo,
    clock: Clock,
) -> Person:
    """Привязывает Telegram-аккаунт к записи, на которую выдано приглашение.

    Отклоняет просроченный, уже использованный или отозванный код
    (критерий приёмки 25) и запись, к которой уже привязан аккаунт (критерий 26).
    """
    raise NotImplementedError


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
) -> Event:
    """Добавляет событие и сразу материализует по нему напоминания.

    `is_recurring_yearly` различает ежегодное событие (например, годовщина
    свадьбы) и разовое (SPEC 3.3, 4). Отклоняет действие, если acting_account
    не владелец семьи (family_repo), и если person_id не принадлежит family_id.
    """
    await _require_owner(family_repo, family_id, acting_account_id)

    person = await person_repo.get(person_id)
    if person.family_id != family_id:
        raise ValueError(f"Человек {person_id} не принадлежит семье {family_id}")

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
    person_repo: PersonRepo,
    event_repo: EventRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Account:
    """Меняет пояс, смещения и время суток. Настройки принадлежат аккаунту, а не

    семье: перестраивает будущие напоминания во всех его семьях сразу
    (SPEC 2.1, 5.6, критерий приёмки 6). Уже отправленные напоминания не трогает.
    """
    raise NotImplementedError


async def set_override(
    account_id: int,
    event_id: int,
    offsets_days: tuple[int, ...],
    time_of_day: time,
    override_repo: ReminderOverrideRepo,
    event_repo: EventRepo,
    account_repo: AccountRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> ReminderOverride:
    """Переопределяет смещения и время суток для пары (аккаунт, событие) и

    перестраивает будущие напоминания по этому событию для этого аккаунта (SPEC 5.6).
    """
    raise NotImplementedError


async def set_current_family(
    account_id: int,
    family_id: int,
    account_repo: AccountRepo,
) -> Account:
    """Меняет текущую семью аккаунта, к которой относятся команды добавления и просмотра."""
    raise NotImplementedError
