"""Базовые рёбра родства, вывод родства обходом графа, заглушки и их слияние.

Хранятся только parent_of и spouse_of (см. models.py). Брат, сестра, бабушка,
дядя и кузен не хранятся, а выводятся обходом графа отсюда.

Функции этого модуля не читают и не пишут хранилище: список рёбер и нужные
записи людей всегда передаются явно вызывающим кодом (services.py), а
результат — новые рёбра или узлы — тоже возвращается явно, без побочных эффектов.
"""

from __future__ import annotations

from enum import Enum

from kinday.core.models import ParentOf, Person, Relation, SpouseOf

MAX_RELATION_DEPTH = 3


class RelationKind(Enum):
    """Как пользователь назвал связь нового человека `N` относительно выбранного `A`."""

    FATHER = "father"
    MOTHER = "mother"
    SON = "son"
    DAUGHTER = "daughter"
    HUSBAND = "husband"
    WIFE = "wife"
    BROTHER = "brother"
    SISTER = "sister"


MAX_PARENTS = 2


def _parent_ids(person_id: int, relations: list[ParentOf]) -> list[int]:
    return [r.parent_id for r in relations if r.child_id == person_id]


def _check_parent_slot_free(person_id: int, relations: list[ParentOf]) -> None:
    if len(_parent_ids(person_id, relations)) >= MAX_PARENTS:
        raise ValueError(f"У человека {person_id} уже есть двое родителей")


def add_parent(person_id: int, new_parent_id: int, relations: list[ParentOf]) -> ParentOf:
    """Записать «отец»/«мать» как parent_of(new_parent_id, person_id).

    Отклоняет попытку добавить третьего родителя (см. SPEC 4.1). Если среди
    уже известных родителей person_id есть заглушка, вызывающий код должен
    сначала слить её через merge_placeholder_into, а не занимать этой функцией
    её место — иначе заглушка останется висеть рядом с настоящим родителем
    вместо слияния с ним.
    """
    _check_parent_slot_free(person_id, relations)
    return ParentOf(parent_id=new_parent_id, child_id=person_id)


def add_child(person_id: int, new_child_id: int, relations: list[ParentOf]) -> ParentOf:
    """Записать «сын»/«дочь» как parent_of(person_id, new_child_id).

    Отклоняет попытку добавить третьего родителя новому ребёнку (см. SPEC 4.1).
    Как и add_parent, не заменяет собой merge_placeholder_into: если у
    new_child_id уже есть заглушка среди родителей, её нужно сливать отдельно.
    """
    _check_parent_slot_free(new_child_id, relations)
    return ParentOf(parent_id=person_id, child_id=new_child_id)


def add_spouse(person_id: int, new_spouse_id: int) -> SpouseOf:
    """Записать «супруг»/«супруга» как spouse_of(person_id, new_spouse_id)."""
    return SpouseOf(a_id=person_id, b_id=new_spouse_id)


def add_sibling(person_id: int, new_sibling_id: int, relations: list[ParentOf]) -> list[ParentOf]:
    """Записать «брат»/«сестра»: parent_of(P, new_sibling_id) для каждого известного

    родителя `P` человека `person_id`. Если родителей ещё нет, вызывающий код
    должен сперва завести заглушку через create_placeholder_parent — эта
    функция вернёт пустой список, если в `relations` для person_id нет ни
    одного ребра parent_of.
    """
    return [
        ParentOf(parent_id=parent_id, child_id=new_sibling_id)
        for parent_id in _parent_ids(person_id, relations)
    ]


def create_placeholder_parent(
    family_id: int, placeholder_id: int, child_id: int
) -> tuple[Person, ParentOf]:
    """Скрытый узел-заглушка: без имени, даты рождения и пола, несёт ребро к первому ребёнку.

    `placeholder_id` выделяет вызывающий код (репозиторий), это чистая функция.
    Второй ребёнок присоединяется позже через add_sibling, который найдёт
    заглушку среди родителей первого ребёнка в `relations`.
    """
    placeholder = Person(
        id=placeholder_id,
        family_id=family_id,
        name=None,
        gender=None,
        birth_date=None,
        is_placeholder=True,
    )
    edge = ParentOf(parent_id=placeholder_id, child_id=child_id)
    return placeholder, edge


def merge_placeholder_into(
    placeholder_id: int, real_parent_id: int, relations: list[ParentOf]
) -> list[ParentOf]:
    """Первый добавленный настоящий родитель занимает место заглушки.

    Возвращает рёбра заглушки, переписанные на real_parent_id; сама заглушка
    удаляется вызывающим кодом.
    """
    return [
        ParentOf(parent_id=real_parent_id, child_id=r.child_id)
        for r in relations
        if r.parent_id == placeholder_id
    ]


def infer_relation_text(
    from_person_id: int,
    to_person_id: int,
    people: dict[int, Person],
    relations: list[Relation],
) -> str:
    """Родство от получателя к герою события, обход графа `relations` глубиной не больше трёх шагов.

    `people` даёт пол для формулировки («ваш брат» vs «ваша сестра»).
    Если родство не выводится или лежит дальше MAX_RELATION_DEPTH, вернуть пустую строку.
    Заглушки (Person.is_placeholder) в текст не попадают.
    """
    raise NotImplementedError
