"""Сценарии этапов 1-3 на двух хранилищах: фейки и настоящий SQLite.

PLAN.md, этап 4: «одинаковые поведенческие тесты проходят и на in-memory,
и на SQLite». Фикстура `world` (tests/conftest.py) параметризована двумя
бэкендами, тесты пользуются только портами `core/ports.py`, поэтому любое
расхождение адаптеров с фейками падает здесь.

Критерии приёмки: 4 (29 февраля), 5 (два пояса), 6 (смена пояса), 7 (смена
даты рождения), 14 (напоминания сразу), 15 (не себе), 16 (аккаунт в двух
семьях), 18 (заглушка), 22 (удаление), 24-27 (приглашения и роли).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from tests.core.fakes import FixedClock
from tests.scenario_repos import Snapshot, World
from tests.scenarios import (
    accept_invite_in,
    add_event_to,
    add_person_to,
    clear_override_in,
    create_family_for,
    delete_person_in,
    issue_invite_in,
    revoke_invite_in,
    set_override_in,
    update_account_settings_in,
    update_person_in,
)

from kinday.core.models import EventKind, Gender, ParentOf, SpouseOf
from kinday.core.relations import RelationKind, SiblingsQuestionRequired, infer_relation_text
from kinday.core.services import DEFAULT_OFFSETS_DAYS, NotFamilyOwner

CLOCK = FixedClock(datetime(2027, 1, 1, tzinfo=UTC))
LATER_CLOCK = FixedClock(datetime(2027, 1, 20, tzinfo=UTC))

ANTON_BIRTH = date(1990, 6, 15)
PETR_BIRTH = date(1980, 3, 1)
MARINA_BIRTH = date(1995, 4, 10)


async def _family_with_father(world: World) -> tuple[int, int, int, int]:
    """Семья Антона (владелец) и его отец Пётр. Возвращает id семьи, аккаунта и людей."""
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    petr = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    return family.id, membership.account_id, anton.id, petr.id


@pytest.mark.asyncio
async def test_create_family_writes_owner_without_self_reminder(world: World) -> None:
    """Критерий 15: о собственном дне рождения владельцу не напоминают."""
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )

    state = await world.snapshot(family.id)
    [anton] = state.people
    assert (anton.name, anton.gender, anton.birth_date) == ("Антон", Gender.MALE, ANTON_BIRTH)
    assert anton.is_placeholder is False
    [event] = state.events
    assert (event.kind, event.date, event.person_id) == (EventKind.BIRTHDAY, ANTON_BIRTH, anton.id)
    [membership] = state.memberships
    assert membership.person_id == anton.id
    [account] = state.accounts
    assert (account.timezone, account.offsets_days, account.time_of_day) == (
        "Europe/Moscow",
        DEFAULT_OFFSETS_DAYS,
        time(9, 0),
    )
    assert account.current_family_id == family.id
    assert state.reminders == []


@pytest.mark.asyncio
async def test_add_person_materializes_reminders_immediately(world: World) -> None:
    """Критерий 14: напоминания появляются сразу, без суточного задания."""
    family_id, _, anton_id, petr_id = await _family_with_father(world)

    state = await world.snapshot(family_id)
    [petr_birthday] = [e for e in state.events if e.person_id == petr_id]
    reminders = [r for r in state.reminders if r.event_id == petr_birthday.id]

    assert {r.person_id for r in reminders} == {anton_id}
    assert {r.offset_days for r in reminders} == set(DEFAULT_OFFSETS_DAYS)
    assert {r.occurrence_date for r in reminders} == {date(2027, 3, 1)}
    assert [r.due_at_utc for r in sorted(reminders, key=lambda r: r.offset_days)] == [
        datetime(2027, 3, 1, 6, tzinfo=UTC),
        datetime(2027, 2, 28, 6, tzinfo=UTC),
        datetime(2027, 2, 22, 6, tzinfo=UTC),
    ]
    [relation] = [r for r in state.relations if isinstance(r, ParentOf)]
    assert (relation.parent_id, relation.child_id) == (petr_id, anton_id)


@pytest.mark.asyncio
async def test_two_timezones_give_local_nine_for_same_event(world: World) -> None:
    """Критерий 5: одно событие, два пояса — разные моменты UTC, каждый в 09:00 по месту."""
    family_id, owner_account_id, _, petr_id = await _family_with_father(world)
    invite = await issue_invite_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )
    await accept_invite_in(
        world,
        clock=CLOCK,
        code=invite.code,
        telegram_user_id=200,
        timezone="Asia/Vladivostok",
    )
    marina = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=MARINA_BIRTH,
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=petr_id,
    )

    state = await world.snapshot(family_id)
    [marina_birthday] = [e for e in state.events if e.person_id == marina.id]
    due_by_person = {
        r.person_id: r.due_at_utc
        for r in state.reminders
        if r.event_id == marina_birthday.id and r.offset_days == 0
    }

    anton_person_id = next(
        m.person_id for m in state.memberships if m.account_id == owner_account_id
    )
    assert due_by_person[anton_person_id] == datetime(2027, 4, 10, 6, tzinfo=UTC)
    assert due_by_person[petr_id] == datetime(2027, 4, 9, 23, tzinfo=UTC)


@pytest.mark.asyncio
async def test_february_29_birthday_falls_back_to_28_in_common_year(world: World) -> None:
    """Критерий 4: в невисокосный год напоминаем 28 февраля, в високосный — 29-го."""
    family_id, owner_account_id, anton_id, _ = await _family_with_father(world)
    daughter = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Нина",
        gender=Gender.FEMALE,
        birth_date=date(2000, 2, 29),
        relation_kind=RelationKind.DAUGHTER,
        relative_to_person_id=anton_id,
    )

    state = await world.snapshot(family_id)
    [event] = [e for e in state.events if e.person_id == daughter.id]
    assert {r.occurrence_date for r in state.reminders if r.event_id == event.id} == {
        date(2027, 2, 28)
    }

    # Тот же день рождения, но материализация из середины 2027 года: в горизонт
    # 400 дней попадает уже високосный 2028-й.
    leap_clock = FixedClock(datetime(2027, 6, 1, tzinfo=UTC))
    son = await add_person_to(
        world,
        clock=leap_clock,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Лев",
        birth_date=date(2000, 2, 29),
        relation_kind=RelationKind.DAUGHTER,
        relative_to_person_id=anton_id,
    )
    state = await world.snapshot(family_id)
    [leap_event] = [e for e in state.events if e.person_id == son.id]
    assert {r.occurrence_date for r in state.reminders if r.event_id == leap_event.id} == {
        date(2028, 2, 29)
    }


@pytest.mark.asyncio
async def test_account_in_two_families_gets_reminders_of_both(world: World) -> None:
    """Критерий 16: один аккаунт в двух семьях получает напоминания обеих."""
    first_id, account_id, anton_id, _ = await _family_with_father(world)
    second = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    anton_in_second = next(m.person_id for m in await world.membership.list_by_family(second.id))
    await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=account_id,
        family_id=second.id,
        name="Ольга",
        gender=Gender.FEMALE,
        birth_date=date(1962, 5, 4),
        relation_kind=RelationKind.MOTHER,
        relative_to_person_id=anton_in_second,
    )

    first_state = await world.snapshot(first_id)
    second_state = await world.snapshot(second.id)
    assert {r.person_id for r in first_state.reminders} == {anton_id}
    assert {r.person_id for r in second_state.reminders} == {anton_in_second}
    assert len(await world.membership.list_by_account(account_id)) == 2


@pytest.mark.asyncio
async def test_birth_date_change_rebuilds_future_reminders(world: World) -> None:
    """Критерий 7: смена даты рождения перестраивает будущие напоминания."""
    family_id, owner_account_id, _, petr_id = await _family_with_father(world)

    await update_person_in(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        person_id=petr_id,
        name="Пётр",
        birth_date=date(1980, 9, 20),
    )

    state = await world.snapshot(family_id)
    [event] = [e for e in state.events if e.person_id == petr_id]
    assert event.date == date(1980, 9, 20)
    assert {r.occurrence_date for r in state.reminders if r.event_id == event.id} == {
        date(2027, 9, 20)
    }
    assert (await world.person.get(petr_id)).birth_date == date(1980, 9, 20)


@pytest.mark.asyncio
async def test_timezone_change_rebuilds_reminders_in_all_families(world: World) -> None:
    """Критерий 6: смена пояса перестраивает будущие напоминания во всех семьях аккаунта."""
    first_id, account_id, _, _ = await _family_with_father(world)
    second = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    anton_in_second = next(m.person_id for m in await world.membership.list_by_family(second.id))
    await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=account_id,
        family_id=second.id,
        name="Ольга",
        gender=Gender.FEMALE,
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.MOTHER,
        relative_to_person_id=anton_in_second,
    )

    await update_account_settings_in(
        world,
        clock=CLOCK,
        account_id=account_id,
        timezone="Asia/Vladivostok",
        offsets_days=(0,),
        time_of_day=time(9, 0),
    )

    reminders = await world.read_reminders()
    assert {r.offset_days for r in reminders} == {0}
    assert {r.due_at_utc for r in reminders} == {datetime(2027, 2, 28, 23, tzinfo=UTC)}
    families = {(await world.event.get(r.event_id)).family_id for r in reminders}
    assert families == {first_id, second.id}


@pytest.mark.asyncio
async def test_override_rebuilds_only_its_pair(world: World) -> None:
    """SPEC 5.6: переопределение меняет напоминания только своей пары «аккаунт и событие»."""
    family_id, owner_account_id, _, petr_id = await _family_with_father(world)
    state = await world.snapshot(family_id)
    [petr_birthday] = [e for e in state.events if e.person_id == petr_id]
    graduation = await add_event_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        person_id=petr_id,
        title="Выпускной",
        event_date=date(2027, 6, 3),
    )

    await set_override_in(
        world,
        clock=CLOCK,
        account_id=owner_account_id,
        event_id=petr_birthday.id,
        offsets_days=(2,),
        time_of_day=time(20, 0),
    )

    state = await world.snapshot(family_id)
    birthday_reminders = [r for r in state.reminders if r.event_id == petr_birthday.id]
    graduation_reminders = [r for r in state.reminders if r.event_id == graduation.id]
    assert {r.offset_days for r in birthday_reminders} == {2}
    assert {r.due_at_utc for r in birthday_reminders} == {datetime(2027, 2, 27, 17, tzinfo=UTC)}
    assert {r.offset_days for r in graduation_reminders} == set(DEFAULT_OFFSETS_DAYS)


@pytest.mark.asyncio
async def test_sibling_without_parents_creates_placeholder(world: World) -> None:
    """Критерий 18: заглушка несёт рёбра к обоим детям, событий и приглашений не имеет."""
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    marina = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=MARINA_BIRTH,
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=anton.id,
    )

    state = await world.snapshot(family.id)
    [placeholder] = [p for p in state.people if p.is_placeholder]
    assert (placeholder.name, placeholder.gender, placeholder.birth_date) == (None, None, None)
    assert {(r.parent_id, r.child_id) for r in state.relations if isinstance(r, ParentOf)} == {
        (placeholder.id, anton.id),
        (placeholder.id, marina.id),
    }
    assert [e for e in state.events if e.person_id == placeholder.id] == []

    with pytest.raises(ValueError, match="заглушка"):
        await issue_invite_in(
            world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            person_id=placeholder.id,
        )
    with pytest.raises(ValueError, match="заглушка"):
        await add_event_to(
            world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            family_id=family.id,
            person_id=placeholder.id,
            title="Юбилей",
            event_date=date(2027, 7, 1),
        )
    with pytest.raises(ValueError, match="заглушка"):
        await update_person_in(
            world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            person_id=placeholder.id,
            name="Неизвестный",
            birth_date=date(1950, 1, 1),
        )


@pytest.mark.asyncio
async def test_placeholder_merges_with_first_real_parent(world: World) -> None:
    """Критерий 19: настоящий отец занимает место заглушки, её рёбра переходят к нему."""
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    marina = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=MARINA_BIRTH,
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=anton.id,
    )
    petr = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )

    state = await world.snapshot(family.id)
    assert [p for p in state.people if p.is_placeholder] == []
    assert {p.id for p in state.people} == {anton.id, marina.id, petr.id}
    assert {(r.parent_id, r.child_id) for r in state.relations if isinstance(r, ParentOf)} == {
        (petr.id, anton.id),
        (petr.id, marina.id),
    }


@pytest.mark.asyncio
async def test_second_parent_asks_about_siblings_and_writes_nothing_meanwhile(
    world: World,
) -> None:
    """Критерий 20: второй родитель ждёт ответа про братьев, до ответа хранилище не меняется."""
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    petr = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Пётр",
        birth_date=PETR_BIRTH,
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton.id,
    )
    dima = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Дима",
        birth_date=date(1993, 5, 5),
        relation_kind=RelationKind.BROTHER,
        relative_to_person_id=anton.id,
    )
    before = await world.snapshot(family.id)

    with pytest.raises(SiblingsQuestionRequired) as question:
        await add_person_to(
            world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            family_id=family.id,
            name="Мария",
            gender=Gender.FEMALE,
            birth_date=date(1962, 7, 7),
            relation_kind=RelationKind.MOTHER,
            relative_to_person_id=anton.id,
        )
    assert question.value.sibling_ids == [dima.id]
    assert await world.snapshot(family.id) == before

    maria = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Мария",
        gender=Gender.FEMALE,
        birth_date=date(1962, 7, 7),
        relation_kind=RelationKind.MOTHER,
        relative_to_person_id=anton.id,
        also_parent_of_siblings=True,
    )

    state = await world.snapshot(family.id)
    assert {(r.parent_id, r.child_id) for r in state.relations if isinstance(r, ParentOf)} == {
        (petr.id, anton.id),
        (petr.id, dima.id),
        (maria.id, anton.id),
        (maria.id, dima.id),
    }


@pytest.mark.asyncio
async def test_third_parent_is_refused_without_a_trace(world: World) -> None:
    """Критерий 21: третий родитель отклоняется, следов отказавшего вызова не остаётся.

    На SQLite это проверяет откат транзакции: человек и его событие создаются
    раньше, чем выясняется отказ, — а после выхода из сценария их нет.
    """
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    for name, gender in (("Пётр", Gender.MALE), ("Мария", Gender.FEMALE)):
        await add_person_to(
            world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            family_id=family.id,
            name=name,
            gender=gender,
            birth_date=PETR_BIRTH,
            relation_kind=RelationKind.FATHER if gender == Gender.MALE else RelationKind.MOTHER,
            relative_to_person_id=anton.id,
        )
    before = await world.snapshot(family.id)

    with pytest.raises(ValueError, match="двое родителей"):
        await add_person_to(
            world,
            clock=CLOCK,
            acting_account_id=membership.account_id,
            family_id=family.id,
            name="Иван",
            birth_date=date(1958, 2, 2),
            relation_kind=RelationKind.FATHER,
            relative_to_person_id=anton.id,
        )

    assert await world.snapshot(family.id) == before


@pytest.mark.asyncio
async def test_delete_person_with_children_becomes_placeholder(world: World) -> None:
    """Критерий 22: запись с детьми превращается в заглушку, родство детей остаётся."""
    family_id, owner_account_id, anton_id, petr_id = await _family_with_father(world)
    marina = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=MARINA_BIRTH,
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=anton_id,
    )
    invite = await issue_invite_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )

    await delete_person_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )

    # SPEC 4.3: приглашения удалённой записи отзываются. Запись уцелела как
    # заглушка, поэтому отзыв видно в обоих хранилищах, а не только в фейке.
    [stored_invite] = await world.invite.list_by_person(petr_id)
    assert stored_invite.id == invite.id
    assert stored_invite.revoked_at == CLOCK.now()
    with pytest.raises(ValueError):
        await accept_invite_in(world, clock=CLOCK, code=invite.code, telegram_user_id=300)

    state = await world.snapshot(family_id)
    petr = next(p for p in state.people if p.id == petr_id)
    assert petr.is_placeholder is True
    assert (petr.name, petr.gender, petr.birth_date) == (None, None, None)
    assert {(r.parent_id, r.child_id) for r in state.relations if isinstance(r, ParentOf)} == {
        (petr_id, anton_id),
        (petr_id, marina.id),
    }
    assert [e for e in state.events if e.person_id == petr_id] == []
    assert [r for r in state.reminders if r.person_id == petr_id] == []


@pytest.mark.asyncio
async def test_delete_childless_person_frees_account(world: World) -> None:
    """Критерий 22: бездетную запись удаляем целиком, текущая семья аккаунта очищается."""
    family_id, owner_account_id, anton_id, _ = await _family_with_father(world)
    marina = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=MARINA_BIRTH,
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=anton_id,
    )
    invite = await issue_invite_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=marina.id
    )
    await accept_invite_in(world, clock=CLOCK, code=invite.code, telegram_user_id=200)
    guest = await world.account.get_by_telegram_user_id(200)
    assert guest is not None
    assert guest.current_family_id == family_id

    await delete_person_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=marina.id
    )

    state = await world.snapshot(family_id)
    assert [p.id for p in state.people if p.id == marina.id] == []
    assert [m for m in state.memberships if m.person_id == marina.id] == []
    assert (await world.account.get(guest.id)).current_family_id is None
    with pytest.raises(ValueError):
        await accept_invite_in(world, clock=CLOCK, code=invite.code, telegram_user_id=300)


@pytest.mark.asyncio
async def test_invite_binds_only_its_own_record(world: World) -> None:
    """Критерии 24-26: код привязан к записи, повтор и занятая запись отклоняются."""
    _, owner_account_id, _, petr_id = await _family_with_father(world)
    invite = await issue_invite_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )

    with pytest.raises(ValueError, match="не найдено"):
        await accept_invite_in(world, clock=CLOCK, code="совсем-другой-код", telegram_user_id=200)

    person = await accept_invite_in(world, clock=CLOCK, code=invite.code, telegram_user_id=200)
    assert person.id == petr_id

    with pytest.raises(ValueError):
        await accept_invite_in(world, clock=CLOCK, code=invite.code, telegram_user_id=300)
    with pytest.raises(ValueError, match="уже привязана"):
        await issue_invite_in(
            world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
        )


@pytest.mark.asyncio
async def test_expired_and_revoked_invites_are_rejected(world: World) -> None:
    """Критерий 25: просроченный и отозванный код не привязывает аккаунт."""
    _, owner_account_id, _, petr_id = await _family_with_father(world)
    expired = await issue_invite_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )
    with pytest.raises(ValueError, match="просрочено"):
        await accept_invite_in(world, clock=LATER_CLOCK, code=expired.code, telegram_user_id=200)

    revoked = await issue_invite_in(
        world, clock=LATER_CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )
    await revoke_invite_in(
        world, clock=LATER_CLOCK, acting_account_id=owner_account_id, invite_id=revoked.id
    )
    with pytest.raises(ValueError, match="отозвано"):
        await accept_invite_in(world, clock=LATER_CLOCK, code=revoked.code, telegram_user_id=200)

    assert await world.membership.get_by_person(petr_id) is None


@pytest.mark.asyncio
async def test_member_changes_own_settings_but_not_the_tree(world: World) -> None:
    """Критерий 27: участник не меняет дерево, но меняет свои настройки; неверные — отклоняются."""
    family_id, owner_account_id, anton_id, petr_id = await _family_with_father(world)
    invite = await issue_invite_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )
    await accept_invite_in(world, clock=CLOCK, code=invite.code, telegram_user_id=200)
    guest = await world.account.get_by_telegram_user_id(200)
    assert guest is not None

    with pytest.raises(NotFamilyOwner):
        await add_person_to(
            world,
            clock=CLOCK,
            acting_account_id=guest.id,
            family_id=family_id,
            name="Марина",
            gender=Gender.FEMALE,
            birth_date=MARINA_BIRTH,
            relation_kind=RelationKind.SISTER,
            relative_to_person_id=anton_id,
        )

    await update_account_settings_in(
        world,
        clock=CLOCK,
        account_id=guest.id,
        timezone="Asia/Vladivostok",
        offsets_days=(3,),
        time_of_day=time(8, 30),
    )
    updated = await world.account.get(guest.id)
    assert (updated.timezone, updated.offsets_days, updated.time_of_day) == (
        "Asia/Vladivostok",
        (3,),
        time(8, 30),
    )

    for offsets, timezone in (
        ((), "Asia/Vladivostok"),
        ((1, 1), "Asia/Vladivostok"),
        ((400,), "Asia/Vladivostok"),
        ((3,), "Europe/Нигде"),
    ):
        with pytest.raises(ValueError):
            await update_account_settings_in(
                world,
                clock=CLOCK,
                account_id=guest.id,
                timezone=timezone,
                offsets_days=offsets,
                time_of_day=time(7, 0),
            )

    unchanged = await world.account.get(guest.id)
    assert (unchanged.timezone, unchanged.offsets_days, unchanged.time_of_day) == (
        "Asia/Vladivostok",
        (3,),
        time(8, 30),
    )


@pytest.mark.asyncio
async def test_deleted_father_keeps_grandparent_link_through_placeholder(world: World) -> None:
    """SPEC 4.3: заглушка сохраняет связь вверх — внук не теряет деда вместе с отцом.

    Заглушка существует ровно чтобы родство не терялось. Если при удалении
    Петра снять его ребро к деду Ивану, Антон лишится «вашего дедушки»
    (SPEC 4.2, глубина 2) — того самого родства, которое заглушка и должна
    удержать.
    """
    family_id, owner_account_id, anton_id, petr_id = await _family_with_father(world)
    ivan = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Иван",
        birth_date=date(1955, 2, 20),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=petr_id,
    )

    await delete_person_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )

    state = await world.snapshot(family_id)
    placeholder = next(p for p in state.people if p.id == petr_id)
    assert placeholder.is_placeholder is True
    assert (ivan.id, petr_id) in {
        (r.parent_id, r.child_id) for r in state.relations if isinstance(r, ParentOf)
    }
    people = {p.id: p for p in state.people}
    assert infer_relation_text(anton_id, ivan.id, people, state.relations) == "ваш дедушка"


@pytest.mark.asyncio
async def test_merged_placeholder_hands_over_spouse_and_parent(world: World) -> None:
    """SPEC 4.1: настоящий родитель получает все рёбра заглушки, а не только рёбра к детям."""
    family_id, owner_account_id, anton_id, petr_id = await _family_with_father(world)
    ivan = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Иван",
        birth_date=date(1955, 2, 20),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=petr_id,
    )
    olga = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Ольга",
        gender=Gender.FEMALE,
        birth_date=date(1982, 8, 8),
        relation_kind=RelationKind.WIFE,
        relative_to_person_id=petr_id,
    )
    # Пётр становится заглушкой и сохраняет отца Ивана и супругу Ольгу.
    await delete_person_in(
        world, clock=CLOCK, acting_account_id=owner_account_id, person_id=petr_id
    )

    sergey = await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=owner_account_id,
        family_id=family_id,
        name="Сергей",
        birth_date=date(1979, 4, 4),
        relation_kind=RelationKind.FATHER,
        relative_to_person_id=anton_id,
    )

    state = await world.snapshot(family_id)
    assert [p for p in state.people if p.is_placeholder] == []
    assert {p.id for p in state.people} == {anton_id, ivan.id, olga.id, sergey.id}
    assert {(r.parent_id, r.child_id) for r in state.relations if isinstance(r, ParentOf)} == {
        (sergey.id, anton_id),
        (ivan.id, sergey.id),
    }
    assert {frozenset((r.a_id, r.b_id)) for r in state.relations if isinstance(r, SpouseOf)} == {
        frozenset((sergey.id, olga.id))
    }
    people = {p.id: p for p in state.people}
    assert infer_relation_text(anton_id, ivan.id, people, state.relations) == "ваш дедушка"


@pytest.mark.asyncio
async def test_placeholder_is_not_offered_as_a_relative(world: World) -> None:
    """SPEC 4.1: заглушку не выбрать родственником нового человека.

    Заглушка не показывается в списках, поэтому в боте её и не выбрать, но без
    проверки в ядре запрос всё равно прошёл бы. Связь «относительно заглушки»
    пользователь и не имел в виду: у неё нет ни имени, ни пола, ни даты
    рождения, так что осознанно указать её нельзя. Законный путь — добавить
    родителя тому, кто на заглушке висит, и заглушка сольётся с ним.
    """
    family = await create_family_for(
        world, clock=CLOCK, telegram_user_id=100, name="Антон", birth_date=ANTON_BIRTH
    )
    [anton] = await world.person.list_by_family(family.id)
    [membership] = await world.membership.list_by_family(family.id)
    await add_person_to(
        world,
        clock=CLOCK,
        acting_account_id=membership.account_id,
        family_id=family.id,
        name="Марина",
        gender=Gender.FEMALE,
        birth_date=MARINA_BIRTH,
        relation_kind=RelationKind.SISTER,
        relative_to_person_id=anton.id,
    )
    state = await world.snapshot(family.id)
    [placeholder] = [p for p in state.people if p.is_placeholder]

    for relation_kind in (
        RelationKind.HUSBAND,
        RelationKind.BROTHER,
        RelationKind.FATHER,
        RelationKind.SON,
    ):
        with pytest.raises(ValueError, match="заглушка"):
            await add_person_to(
                world,
                clock=CLOCK,
                acting_account_id=membership.account_id,
                family_id=family.id,
                name="Никто",
                birth_date=date(1960, 5, 5),
                relation_kind=relation_kind,
                relative_to_person_id=placeholder.id,
            )

    assert await world.snapshot(family.id) == state


@pytest.mark.asyncio
async def test_cleared_override_returns_reminders_to_account_settings(world: World) -> None:
    """SPEC 5.6: снятое переопределение — повод пересчитать, напоминания возвращаются к аккаунту.

    Сравниваются и смещения, и `due_at_utc`: SPEC 3.4 переопределяет смещения и
    время суток вместе, поэтому вернуться должны обе половины пары. Сравнение
    идёт с состоянием до переопределения, без id — строки пересозданы заново.
    """

    def birthday_reminders(state: Snapshot, event_id: int) -> set[tuple[int, date, datetime]]:
        return {
            (r.offset_days, r.occurrence_date, r.due_at_utc)
            for r in state.reminders
            if r.event_id == event_id
        }

    family_id, owner_account_id, _, petr_id = await _family_with_father(world)
    before = await world.snapshot(family_id)
    [petr_birthday] = [e for e in before.events if e.person_id == petr_id]
    await set_override_in(
        world,
        clock=CLOCK,
        account_id=owner_account_id,
        event_id=petr_birthday.id,
        offsets_days=(2,),
        time_of_day=time(20, 0),
    )
    overridden = await world.snapshot(family_id)
    assert birthday_reminders(overridden, petr_birthday.id) != birthday_reminders(
        before, petr_birthday.id
    )

    await clear_override_in(
        world, clock=CLOCK, account_id=owner_account_id, event_id=petr_birthday.id
    )

    assert await world.override.get(owner_account_id, petr_birthday.id) is None
    after = await world.snapshot(family_id)
    assert birthday_reminders(after, petr_birthday.id) == birthday_reminders(
        before, petr_birthday.id
    )
    assert {r.offset_days for r in after.reminders} == set(DEFAULT_OFFSETS_DAYS)


@pytest.mark.asyncio
async def test_clearing_absent_override_changes_nothing(world: World) -> None:
    """Снятие того, чего нет, — не ошибка: повторное «сбросить» не должно падать."""
    family_id, owner_account_id, _, petr_id = await _family_with_father(world)
    state = await world.snapshot(family_id)
    [petr_birthday] = [e for e in state.events if e.person_id == petr_id]

    await clear_override_in(
        world, clock=CLOCK, account_id=owner_account_id, event_id=petr_birthday.id
    )

    assert await world.snapshot(family_id) == state


@pytest.mark.asyncio
async def test_outsider_cannot_clear_override(world: World) -> None:
    """Снимать переопределение, как и ставить, может только участник семьи события (SPEC 2)."""
    family_id, _, _, petr_id = await _family_with_father(world)
    state = await world.snapshot(family_id)
    [petr_birthday] = [e for e in state.events if e.person_id == petr_id]
    outsider = await create_family_for(
        world, clock=CLOCK, telegram_user_id=900, name="Посторонний", birth_date=date(1970, 1, 1)
    )
    [outsider_membership] = await world.membership.list_by_family(outsider.id)

    with pytest.raises(ValueError, match="не состоит"):
        await clear_override_in(
            world,
            clock=CLOCK,
            account_id=outsider_membership.account_id,
            event_id=petr_birthday.id,
        )

    assert await world.snapshot(family_id) == state
