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

from kinday.core.errors import DomainError, DomainErrorCode
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
    """Результат attach_parent: что добавить и что убрать из хранилища рёбер.

    Рёбра любого вида, а не только parent_of: в ветке со слиянием заглушки
    сюда попадают и её супружеские рёбра (merge_placeholder_into).
    """

    added: list[Relation]
    removed: list[Relation]
    removed_placeholder_id: int | None


def _parent_ids(person_id: int, relations: Sequence[ParentOf]) -> list[int]:
    return [r.parent_id for r in relations if r.child_id == person_id]


def _touches(relation: Relation, person_id: int) -> bool:
    """Ребро упирается в person_id любым концом — в любую сторону и любого вида."""
    if isinstance(relation, ParentOf):
        return person_id in (relation.parent_id, relation.child_id)
    return person_id in (relation.a_id, relation.b_id)


def _check_parent_slot_free(person_id: int, relations: list[ParentOf]) -> None:
    if len(_parent_ids(person_id, relations)) >= MAX_PARENTS:
        raise DomainError(
            DomainErrorCode.THIRD_PARENT, f"У человека {person_id} уже есть двое родителей"
        )


def add_parent(person_id: int, new_parent_id: int, relations: list[ParentOf]) -> ParentOf:
    """Записать «отец»/«мать» как parent_of(new_parent_id, person_id).

    Отклоняет попытку добавить третьего родителя (см. SPEC 4.1). Если среди
    уже известных родителей person_id есть заглушка, вызывающий код должен
    сначала слить её через merge_placeholder_into, а не занимать этой функцией
    её место — иначе заглушка останется висеть рядом с настоящим родителем
    вместо слияния с ним. Отклоняет попытку сделать человека родителем самому себе.
    """
    if person_id == new_parent_id:
        raise DomainError(
            DomainErrorCode.SELF_RELATION, "Человек не может быть родителем самому себе"
        )
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
        raise DomainError(
            DomainErrorCode.SELF_RELATION, "Человек не может быть своим собственным ребёнком"
        )
    _check_parent_slot_free(new_child_id, relations)
    return ParentOf(parent_id=person_id, child_id=new_child_id)


def add_spouse(person_id: int, new_spouse_id: int) -> SpouseOf:
    """Записать «супруг»/«супруга» как spouse_of(person_id, new_spouse_id).

    Отклоняет попытку сделать человека супругом самому себе.
    """
    if person_id == new_spouse_id:
        raise DomainError(
            DomainErrorCode.SELF_RELATION, "Человек не может быть супругом самому себе"
        )
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
        # Не доменный отказ, а нарушенный контракт вызова: кода для пользователя нет,
        # слой telegram ответит на такое общим текстом.
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
    placeholder_id: int, real_parent_id: int, relations: Sequence[Relation]
) -> list[Relation]:
    """Первый добавленный настоящий родитель занимает место заглушки.

    Возвращает все рёбра заглушки, переписанные на real_parent_id: к её детям,
    к её собственным родителям и к её супругу. Сама заглушка со всеми своими
    рёбрами удаляется вызывающим кодом, поэтому перенести нужно каждое: рёбра
    вверх и к супругу заглушка получает, когда в неё превратился удалённый
    человек с детьми (SPEC 4.3), и именно они держат родство внука с дедом.
    Оставить их без переноса значило бы молча его потерять.

    Отказывает, если у настоящего родителя после переноса оказалось бы больше
    MAX_PARENTS родителей: два своих плюс родители заглушки не поместятся, а
    выбрасывать лишнее по своему усмотрению функция не вправе. Общий родитель
    заглушки и настоящего родителя переносится один раз, без дубля ребра.
    """
    parents_above: list[int] = []
    own_parents: list[int] = []
    for relation in relations:
        if not isinstance(relation, ParentOf):
            continue
        if relation.child_id == placeholder_id:
            parents_above.append(relation.parent_id)
        elif relation.child_id == real_parent_id:
            own_parents.append(relation.parent_id)

    if real_parent_id in parents_above:
        raise DomainError(
            DomainErrorCode.SELF_RELATION,
            f"Заглушка {placeholder_id} — ребёнок человека {real_parent_id}: "
            "слияние сделало бы его родителем самому себе",
        )
    if len({*own_parents, *parents_above}) > MAX_PARENTS:
        raise DomainError(
            DomainErrorCode.THIRD_PARENT,
            f"После слияния с заглушкой {placeholder_id} у человека "
            f"{real_parent_id} оказалось бы больше {MAX_PARENTS} родителей",
        )

    moved: list[Relation] = []
    for relation in relations:
        if isinstance(relation, ParentOf):
            if relation.parent_id == placeholder_id:
                moved.append(ParentOf(parent_id=real_parent_id, child_id=relation.child_id))
            elif relation.child_id == placeholder_id and relation.parent_id not in own_parents:
                moved.append(ParentOf(parent_id=relation.parent_id, child_id=real_parent_id))
        elif placeholder_id in (relation.a_id, relation.b_id):
            spouse_id = relation.b_id if relation.a_id == placeholder_id else relation.a_id
            moved.append(add_spouse(real_parent_id, spouse_id))
    return moved


def attach_parent(
    person_id: int,
    new_parent_id: int,
    relations: Sequence[Relation],
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
      В `removed` попадают все рёбра заглушки, включая супружеские и рёбра к
      её собственным родителям: настоящий родитель получает их копии в
      `added`, а сама заглушка удаляется вызывающим кодом.
    - Родителей нет → одно ребро parent_of(new_parent_id, person_id).
    - Один настоящий родитель:
      - если среди других детей этого родителя (братьев и сестёр person_id)
        есть хотя бы один со свободным слотом (меньше двух родителей) и
        also_parent_of_siblings is None → SiblingsQuestionRequired со списком
        id только таких, свободных, братьев и сестёр — вызывающий код (бот)
        должен спросить пользователя и повторить вызов с явным True/False;
      - «да» — рёбра тянутся к person_id и к каждому из этого списка;
        «нет» — только к person_id;
      - других детей нет, либо все они уже сводные с двумя родителями —
        вопрос не нужен (спрашивать не о чем), ребро сразу к person_id.
    - Двое родителей (оба настоящие) → отказ (SPEC 4.1, критерий 21).

    `relations` — все рёбра семьи, а не только parent_of: слияние заглушки
    переносит и её супружеское ребро. `people` должен содержать запись для
    каждого id, уже фигурирующего как родитель person_id в `relations` — иначе
    поиск заглушки среди них упадёт с KeyError. Отклоняет попытку сделать
    человека родителем самому себе.
    """
    if person_id == new_parent_id:
        raise DomainError(
            DomainErrorCode.SELF_RELATION, "Человек не может быть родителем самому себе"
        )
    parent_relations = [r for r in relations if isinstance(r, ParentOf)]
    existing_parent_ids = _parent_ids(person_id, parent_relations)

    placeholder_id = next(
        (parent_id for parent_id in existing_parent_ids if people[parent_id].is_placeholder),
        None,
    )
    if placeholder_id is not None:
        removed = [r for r in relations if _touches(r, placeholder_id)]
        added = merge_placeholder_into(placeholder_id, new_parent_id, relations)
        return ParentChange(added=added, removed=removed, removed_placeholder_id=placeholder_id)

    if not existing_parent_ids:
        edge = ParentOf(parent_id=new_parent_id, child_id=person_id)
        return ParentChange(added=[edge], removed=[], removed_placeholder_id=None)

    if len(existing_parent_ids) >= MAX_PARENTS:
        raise DomainError(
            DomainErrorCode.THIRD_PARENT, f"У человека {person_id} уже есть двое родителей"
        )

    only_parent_id = existing_parent_ids[0]
    all_siblings = sorted(
        {
            r.child_id
            for r in parent_relations
            if r.parent_id == only_parent_id and r.child_id != person_id
        }
    )
    siblings_with_free_slot = [
        sibling_id
        for sibling_id in all_siblings
        if len(_parent_ids(sibling_id, parent_relations)) < MAX_PARENTS
    ]

    if not siblings_with_free_slot:
        edge = ParentOf(parent_id=new_parent_id, child_id=person_id)
        return ParentChange(added=[edge], removed=[], removed_placeholder_id=None)

    if also_parent_of_siblings is None:
        raise SiblingsQuestionRequired(siblings_with_free_slot)

    added = [ParentOf(parent_id=new_parent_id, child_id=person_id)]
    if also_parent_of_siblings:
        added.extend(
            ParentOf(parent_id=new_parent_id, child_id=sibling_id)
            for sibling_id in siblings_with_free_slot
        )

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


def infer_relation_category(
    from_person_id: int,
    to_person_id: int,
    people: dict[int, Person],
    relations: Sequence[Relation],
) -> str | None:
    """Категория родства («parent», «grandchild_child» и т.д.) без учёта пола.

    Обход графа `relations` глубиной не больше трёх шагов. None — если родство
    не выводится, лежит дальше MAX_RELATION_DEPTH, это один и тот же человек или
    `to_person_id` — заглушка либо неизвестный человек. Заглушки участвуют в обходе
    как промежуточные узлы — так родство братьев и сестёр выводится и через
    общую заглушку неизвестного родителя (критерий приёмки 18).
    """
    if from_person_id == to_person_id:
        return None
    to_person = people.get(to_person_id)
    if to_person is None or to_person.is_placeholder:
        return None

    path = _shortest_relation_path(from_person_id, to_person_id, relations)
    if path is None:
        return None
    return _RELATION_BY_PATH.get(path)


def infer_relation_text(
    from_person_id: int,
    to_person_id: int,
    people: dict[int, Person],
    relations: Sequence[Relation],
) -> str:
    """Родство от получателя к герою события в формулировке «ваш брат» / «ваша сестра».

    `people` даёт пол героя события. Если родство не выводится, возвращает
    пустую строку (см. `infer_relation_category`).
    """
    category = infer_relation_category(from_person_id, to_person_id, people, relations)
    if category is None:
        return ""
    return relation_word(category, people[to_person_id].gender)
