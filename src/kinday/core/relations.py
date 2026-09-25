"""Базовые рёбра родства, вывод родства обходом графа, заглушки и их слияние.

Хранятся только parent_of и spouse_of (см. models.py). Брат, сестра, бабушка,
дядя и кузен не хранятся, а выводятся обходом графа отсюда.

Функции этого модуля не читают и не пишут хранилище: список рёбер и нужные
записи людей всегда передаются явно вызывающим кодом (services.py), а
результат — новые рёбра или узлы — тоже возвращается явно, без побочных эффектов.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from kinday.core.models import ParentOf, Person, Relation, SpouseOf
from kinday.core.texts import relation_word

MAX_RELATION_DEPTH = 3

# Категория родства по шагам кратчайшего пути от получателя к герою события.
# UP — шаг к родителю, DOWN — шаг к ребёнку, SPOUSE — шаг к супругу.
_RELATION_BY_PATH: dict[tuple[str, ...], str] = {
    ("UP",): "parent",
    ("DOWN",): "child",
    ("SPOUSE",): "spouse",
    ("UP", "UP"): "grandparent",
    ("DOWN", "DOWN"): "grandchild",
    ("UP", "DOWN"): "sibling",
    ("UP", "UP", "UP"): "grandparent_parent",
    ("DOWN", "DOWN", "DOWN"): "grandchild_child",
    ("UP", "UP", "DOWN"): "parent_sibling",
    ("UP", "DOWN", "DOWN"): "sibling_child",
}


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


class SiblingsQuestionRequired(Exception):
    """Второй настоящий родитель добавляется человеку, у которого уже есть

    братья и сёстры от первого родителя. Вызывающий код (бот) должен спросить
    пользователя «да»/«нет» и повторить attach_parent с явным
    also_parent_of_siblings.
    """

    def __init__(self, sibling_ids: list[int]) -> None:
        self.sibling_ids = sibling_ids
        super().__init__(f"Нужно уточнить: новый родитель тоже родитель для {sibling_ids}?")


@dataclass(slots=True)
class ParentChange:
    """Результат attach_parent: что добавить и что убрать из хранилища рёбер."""

    added: list[ParentOf]
    removed: list[ParentOf]
    removed_placeholder_id: int | None


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
    вместо слияния с ним. Отклоняет попытку сделать человека родителем самому себе.
    """
    if person_id == new_parent_id:
        raise ValueError("Человек не может быть родителем самому себе")
    _check_parent_slot_free(person_id, relations)
    return ParentOf(parent_id=new_parent_id, child_id=person_id)


def add_child(person_id: int, new_child_id: int, relations: list[ParentOf]) -> ParentOf:
    """Записать «сын»/«дочь» как parent_of(person_id, new_child_id).

    Отклоняет попытку добавить третьего родителя новому ребёнку (см. SPEC 4.1).
    Как и add_parent, не заменяет собой merge_placeholder_into: если у
    new_child_id уже есть заглушка среди родителей, её нужно сливать отдельно.
    Отклоняет попытку сделать человека своим собственным ребёнком.
    """
    if person_id == new_child_id:
        raise ValueError("Человек не может быть своим собственным ребёнком")
    _check_parent_slot_free(new_child_id, relations)
    return ParentOf(parent_id=person_id, child_id=new_child_id)


def add_spouse(person_id: int, new_spouse_id: int) -> SpouseOf:
    """Записать «супруг»/«супруга» как spouse_of(person_id, new_spouse_id).

    Отклоняет попытку сделать человека супругом самому себе.
    """
    if person_id == new_spouse_id:
        raise ValueError("Человек не может быть супругом самому себе")
    return SpouseOf(a_id=person_id, b_id=new_spouse_id)


@dataclass(slots=True)
class SiblingAttachment:
    """Результат add_sibling: заглушка (если пришлось её завести) и новые рёбра."""

    placeholder: Person | None
    edges: list[ParentOf]


def add_sibling(
    person_id: int,
    new_sibling_id: int,
    relations: list[ParentOf],
    family_id: int | None = None,
    placeholder_id: int | None = None,
) -> SiblingAttachment:
    """Записать «брат»/«сестра»: parent_of(P, new_sibling_id) для каждого известного

    родителя `P` человека `person_id`. Если родителей ещё нет, у person_id
    заводится заглушка (family_id и placeholder_id обязаны быть переданы —
    без placeholder_id функция бросает ValueError, не возвращает молча
    пустой список) и рёбра от неё протягиваются к обоим детям.
    """
    parent_ids = _parent_ids(person_id, relations)
    if parent_ids:
        edges = [ParentOf(parent_id=parent_id, child_id=new_sibling_id) for parent_id in parent_ids]
        return SiblingAttachment(placeholder=None, edges=edges)

    if placeholder_id is None or family_id is None:
        raise ValueError(
            f"У человека {person_id} нет родителей: нужны family_id и placeholder_id для заглушки"
        )

    placeholder, edge_to_person = create_placeholder_parent(family_id, placeholder_id, person_id)
    edge_to_sibling = ParentOf(parent_id=placeholder_id, child_id=new_sibling_id)
    return SiblingAttachment(placeholder=placeholder, edges=[edge_to_person, edge_to_sibling])


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


def attach_parent(
    person_id: int,
    new_parent_id: int,
    relations: list[ParentOf],
    also_parent_of_siblings: bool | None,
    people: dict[int, Person],
) -> ParentChange:
    """Присоединить нового родителя к person_id, учитывая заглушки и братьев.

    Один и тот же путь обслуживает и «отец/мать» (person_id — тот, кому
    добавляют родителя), и «сын/дочь» (тогда вызывающий код передаёт
    attach_parent(new_child_id, person_id, ...), потому что с точки зрения
    рёбер это то же самое: у нового ребёнка появляется родитель person_id).

    - Среди родителей person_id есть заглушка → она сливается с new_parent_id
      (merge_placeholder_into); вопрос про братьев и сестёр не нужен — они
      уже висят на заглушке и получат правильного родителя вместе с ней.
    - Родителей нет → одно ребро parent_of(new_parent_id, person_id).
    - Один настоящий родитель:
      - если у него есть другие дети (братья и сёстры person_id) и
        also_parent_of_siblings is None → SiblingsQuestionRequired со списком
        их id, вызывающий код (бот) должен спросить пользователя и повторить
        вызов с явным True/False;
      - «да» — рёбра тянутся к person_id и к каждому брату/сестре, у кого
        есть свободный слот (кто уже сводный с двумя родителями — пропускаем,
        не падаем); «нет» — только к person_id;
      - других детей нет — вопрос не нужен, ребро сразу к person_id.
    - Двое родителей (оба настоящие) → отказ (SPEC 4.1, критерий 21).

    `people` должен содержать запись для каждого id, уже фигурирующего как
    родитель person_id в `relations` — иначе поиск заглушки среди них упадёт
    с KeyError. Отклоняет попытку сделать человека родителем самому себе.
    """
    if person_id == new_parent_id:
        raise ValueError("Человек не может быть родителем самому себе")
    existing_parent_ids = _parent_ids(person_id, relations)

    placeholder_id = next(
        (parent_id for parent_id in existing_parent_ids if people[parent_id].is_placeholder),
        None,
    )
    if placeholder_id is not None:
        removed = [r for r in relations if r.parent_id == placeholder_id]
        added = merge_placeholder_into(placeholder_id, new_parent_id, relations)
        return ParentChange(added=added, removed=removed, removed_placeholder_id=placeholder_id)

    if not existing_parent_ids:
        edge = ParentOf(parent_id=new_parent_id, child_id=person_id)
        return ParentChange(added=[edge], removed=[], removed_placeholder_id=None)

    if len(existing_parent_ids) >= MAX_PARENTS:
        raise ValueError(f"У человека {person_id} уже есть двое родителей")

    only_parent_id = existing_parent_ids[0]
    siblings = sorted(
        {r.child_id for r in relations if r.parent_id == only_parent_id and r.child_id != person_id}
    )

    if not siblings:
        edge = ParentOf(parent_id=new_parent_id, child_id=person_id)
        return ParentChange(added=[edge], removed=[], removed_placeholder_id=None)

    if also_parent_of_siblings is None:
        raise SiblingsQuestionRequired(siblings)

    added = [ParentOf(parent_id=new_parent_id, child_id=person_id)]
    if also_parent_of_siblings:
        for sibling_id in siblings:
            if len(_parent_ids(sibling_id, relations)) < MAX_PARENTS:
                added.append(ParentOf(parent_id=new_parent_id, child_id=sibling_id))

    return ParentChange(added=added, removed=[], removed_placeholder_id=None)


def _relation_adjacency(
    relations: Sequence[Relation],
) -> tuple[dict[int, list[int]], dict[int, list[int]], dict[int, list[int]]]:
    """Списки соседей по родителям, детям и супругам для обхода в infer_relation_text."""
    parents: dict[int, list[int]] = {}
    children: dict[int, list[int]] = {}
    spouses: dict[int, list[int]] = {}
    for relation in relations:
        if isinstance(relation, ParentOf):
            parents.setdefault(relation.child_id, []).append(relation.parent_id)
            children.setdefault(relation.parent_id, []).append(relation.child_id)
        else:
            spouses.setdefault(relation.a_id, []).append(relation.b_id)
            spouses.setdefault(relation.b_id, []).append(relation.a_id)
    return parents, children, spouses


def _shortest_relation_path(
    from_person_id: int, to_person_id: int, relations: Sequence[Relation]
) -> tuple[str, ...] | None:
    """BFS до MAX_RELATION_DEPTH шагов, гарантирует кратчайший путь по числу рёбер."""
    parents, children, spouses = _relation_adjacency(relations)
    queue: deque[tuple[int, tuple[str, ...]]] = deque([(from_person_id, ())])
    visited = {from_person_id}
    while queue:
        node, path = queue.popleft()
        if len(path) >= MAX_RELATION_DEPTH:
            continue
        neighbours = (
            [(parent_id, "UP") for parent_id in parents.get(node, [])]
            + [(child_id, "DOWN") for child_id in children.get(node, [])]
            + [(spouse_id, "SPOUSE") for spouse_id in spouses.get(node, [])]
        )
        for neighbour_id, step in neighbours:
            if neighbour_id in visited:
                continue
            new_path = (*path, step)
            if neighbour_id == to_person_id:
                return new_path
            visited.add(neighbour_id)
            queue.append((neighbour_id, new_path))
    return None


def infer_relation_text(
    from_person_id: int,
    to_person_id: int,
    people: dict[int, Person],
    relations: Sequence[Relation],
) -> str:
    """Родство от получателя к герою события, обход графа `relations` глубиной не больше трёх шагов.

    `people` даёт пол героя события для формулировки («вашего брата» vs
    «вашей сестры»). Если родство не выводится или лежит дальше
    MAX_RELATION_DEPTH, возвращает пустую строку. Заглушки (Person.is_placeholder)
    сами по себе героями события не бывают (см. models.py), но участвуют в обходе
    как промежуточные узлы — так родство братьев и сестёр выводится и через
    общую заглушку неизвестного родителя (критерий приёмки 18).
    """
    if from_person_id == to_person_id:
        return ""
    to_person = people.get(to_person_id)
    if to_person is None or to_person.is_placeholder:
        return ""

    path = _shortest_relation_path(from_person_id, to_person_id, relations)
    if path is None:
        return ""

    category = _RELATION_BY_PATH.get(path)
    if category is None:
        return ""

    return relation_word(category, to_person.gender)
