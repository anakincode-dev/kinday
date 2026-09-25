"""Сценарии: создать семью, добавить человека, выдать приглашение и так далее.

Единственное место, где встречаются модели, порты и остальные модули core.
Внешние слои (telegram, будущий api) вызывают только эти функции.
"""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import date, time, timedelta

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
)
from kinday.core.relations import RelationKind, add_sibling, add_spouse, attach_parent
from kinday.core.reminders import MISFIRE_GRACE, materialize_for_event

DEFAULT_OFFSETS_DAYS: tuple[int, ...] = (7, 1, 0)
DEFAULT_TIME_OF_DAY = time(9, 0)

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
    effective_account = account
    if override is not None:
        effective_account = replace(
            account, offsets_days=override.offsets_days, time_of_day=override.time_of_day
        )
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
    """
    person = await person_repo.get(person_id)
    await _require_owner(family_repo, person.family_id, acting_account_id)

    birth_date_changed = person.birth_date != birth_date
    person.name = name
    person.gender = gender
    person.birth_date = birth_date
    person = await person_repo.update(person)

    if birth_date_changed:
        [birthday_event] = [
            e for e in await event_repo.list_by_person(person_id) if e.kind == EventKind.BIRTHDAY
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
    event_repo: EventRepo,
    family_repo: FamilyRepo,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
    membership_repo: MembershipRepo,
    reminder_repo: ReminderRepo,
) -> None:
    """Удаляет человека физически, либо, если у него есть дети, превращает

    его в заглушку: имя, пол, дата рождения и привязка аккаунта стираются,
    события и напоминания удаляются, рёбра к детям остаются (SPEC 4.3,
    критерий приёмки 22). Рёбра, где person_id сам ребёнок (к своим родителям)
    или супруг, удаляются в обоих случаях — SPEC называет заглушкой узел без
    имени, пола и даты рождения, несущий рёбра только к детям (см.
    create_placeholder_parent), поэтому у заглушки не может остаться ни
    родителей, ни супруга. Заглушка-родитель, у которой после удаления не
    осталось ни одного ребёнка, удаляется вместе с ним
    (_drop_childless_placeholders).

    Отклоняет действие, если acting_account не владелец семьи (family_repo), и
    если владелец удаляет собственную запись: передача владения семьёй вне
    скоупа первой версии (SPEC 7), поэтому families.owner_account_id указывал
    бы на аккаунт, у которого в этой семье больше нет записи человека.
    """
    person = await person_repo.get(person_id)
    await _require_owner(family_repo, person.family_id, acting_account_id)

    own = await membership_repo.get_by_account_and_family(acting_account_id, person.family_id)
    if own is not None and own.person_id == person_id:
        raise ValueError(f"Аккаунт {acting_account_id} не может удалить собственную запись")

    for event in await event_repo.list_by_person(person_id):
        await reminder_repo.delete_all_for_event(event.id)
        await event_repo.delete(event.id)
    await reminder_repo.delete_all_for_person(person_id)
    await membership_repo.delete(person_id)

    relations = await relation_repo.list_by_family(person.family_id)
    has_children = any(isinstance(r, ParentOf) and r.parent_id == person_id for r in relations)
    parent_ids = {
        r.parent_id for r in relations if isinstance(r, ParentOf) and r.child_id == person_id
    }
    for relation in relations:
        if (isinstance(relation, ParentOf) and relation.child_id == person_id) or (
            isinstance(relation, SpouseOf) and person_id in (relation.a_id, relation.b_id)
        ):
            await relation_repo.remove(person.family_id, relation)

    if has_children:
        person.name = None
        person.gender = None
        person.birth_date = None
        person.is_placeholder = True
        await person_repo.update(person)
    else:
        await person_repo.delete(person_id)

    await _drop_childless_placeholders(parent_ids, person.family_id, person_repo, relation_repo)


async def _drop_childless_placeholders(
    candidate_ids: set[int],
    family_id: int,
    person_repo: PersonRepo,
    relation_repo: RelationRepo,
) -> None:
    """Удаляет заглушки из `candidate_ids`, у которых больше не осталось детей.

    Заглушка нужна только чтобы связать братьев и сестёр между собой (SPEC 4.1);
    без детей она не несёт смысла, а показать её пользователю нельзя.

    Перед удалением снимаются все рёбра заглушки, а не только рёбра к детям.
    Рассчитывать на то, что у заглушки есть лишь рёбра вниз, нельзя: так она
    только создаётся (create_placeholder_parent), но `add_person` не запрещает
    выбрать заглушку как `relative_to_person_id`, поэтому у неё может
    появиться супруг, а добавленный ей брат заведёт над ней вторую заглушку.
    Висячее ребро на удалённого человека в SQLite при `PRAGMA foreign_keys=ON`
    (SPEC 6.2) либо не даст удалить строку, либо утащит каскадом чужие рёбра.

    Поэтому и обход идёт вверх по цепочке: снятое ребро могло быть последним
    ребёнком заглушки этажом выше. `candidate_ids` перебирается как очередь,
    каждый узел рассматривается один раз.
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
    person = await person_repo.get(person_id)
    await _require_owner(family_repo, person.family_id, acting_account_id)

    if person.is_placeholder:
        raise ValueError(f"Запись {person_id} — заглушка неизвестного родителя, не приглашается")
    if await membership_repo.get_by_person(person_id) is not None:
        raise ValueError(f"Запись {person_id} уже привязана к аккаунту")

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
) -> None:
    """Отзывает ещё не использованное приглашение (SPEC 3.2, критерий приёмки 25).

    Отклоняет действие, если acting_account не владелец семьи, которой
    принадлежит приглашение, и если оно уже использовано — отзывать больше
    нечего, аккаунт уже привязан.
    """
    invite = await invite_repo.get(invite_id)
    await _require_owner(family_repo, invite.family_id, acting_account_id)
    if invite.used_at is not None:
        raise ValueError(f"Приглашение {invite_id} уже использовано")
    await invite_repo.revoke(invite_id, clock.now())


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
    владелец мог настроить ReminderOverride для этого аккаунта на событие ещё
    до того, как приглашение было принято.
    """
    invite = await invite_repo.get_by_code(code)
    if invite is None:
        raise ValueError("Приглашение не найдено")

    now = clock.now()
    if invite.revoked_at is not None:
        raise ValueError("Приглашение отозвано")
    if invite.used_at is not None:
        raise ValueError("Приглашение уже использовано")
    if now > invite.expires_at:
        raise ValueError("Приглашение просрочено")
    if await membership_repo.get_by_person(invite.person_id) is not None:
        raise ValueError(f"Запись {invite.person_id} уже привязана к аккаунту")

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
    elif await membership_repo.get_by_account_and_family(account.id, invite.family_id) is not None:
        raise ValueError(f"Аккаунт {account.id} уже состоит в семье {invite.family_id}")

    membership = await membership_repo.create(
        Membership(account_id=account.id, family_id=invite.family_id, person_id=invite.person_id)
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

    return await person_repo.get(invite.person_id)


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
    membership_repo: MembershipRepo,
    event_repo: EventRepo,
    override_repo: ReminderOverrideRepo,
    reminder_repo: ReminderRepo,
    clock: Clock,
) -> Account:
    """Меняет пояс, смещения и время суток. Настройки принадлежат аккаунту, а не

    семье: перестраивает будущие напоминания во всех его семьях сразу (SPEC
    2.1, 5.6, критерий приёмки 6), по каждому активному событию каждой из них.
    Переопределения на отдельные события (ReminderOverrideRepo) сохраняют силу
    — их учитывает _rematerialize_event_for_person. Уже отправленные
    напоминания не трогает: пересчитываются только будущие pending-строки.
    """
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
) -> ReminderOverride:
    """Переопределяет смещения и время суток для пары (аккаунт, событие) и

    перестраивает будущие напоминания по этому событию для этого аккаунта
    (SPEC 5.6). Если аккаунт не состоит в семье этого события, переопределение
    всё равно сохраняется (пригодится, если он позже присоединится), но
    пересчитывать пока нечего.
    """
    override = ReminderOverride(
        account_id=account_id,
        event_id=event_id,
        offsets_days=offsets_days,
        time_of_day=time_of_day,
    )
    await override_repo.set(override)

    event = await event_repo.get(event_id)
    membership = await membership_repo.get_by_account_and_family(account_id, event.family_id)
    if membership is not None:
        account = await account_repo.get(account_id)
        await _rematerialize_event_for_person(
            event, membership, account, override_repo, reminder_repo, clock
        )

    return override


async def set_current_family(
    account_id: int,
    family_id: int,
    account_repo: AccountRepo,
    membership_repo: MembershipRepo,
) -> Account:
    """Меняет текущую семью аккаунта, к которой относятся команды добавления и просмотра.

    Отклоняет семью, в которой у аккаунта нет своей записи (membership_repo).
    """
    if await membership_repo.get_by_account_and_family(account_id, family_id) is None:
        raise ValueError(f"Аккаунт {account_id} не состоит в семье {family_id}")

    account = await account_repo.get(account_id)
    account.current_family_id = family_id
    return await account_repo.save(account)
