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


def add_parent(person_id: int, new_parent_id: int, relations: list[ParentOf]) -> ParentOf:
    """Записать «отец»/«мать» как parent_of(new_parent_id, person_id).

    Отклоняет попытку добавить третьего родителя (см. SPEC 4.1).
    """
    raise NotImplementedError


def add_child(person_id: int, new_child_id: int, relations: list[ParentOf]) -> ParentOf:
    """Записать «сын»/«дочь» как parent_of(person_id, new_child_id)."""
    raise NotImplementedError


def add_spouse(person_id: int, new_spouse_id: int) -> SpouseOf:
    """Записать «супруг»/«супруга» как spouse_of(person_id, new_spouse_id)."""
    raise NotImplementedError


def add_sibling(person_id: int, new_sibling_id: int, relations: list[ParentOf]) -> list[ParentOf]:
    """Записать «брат»/«сестра»: parent_of(P, new_sibling_id) для каждого известного

    родителя `P` человека `person_id`. Если родителей ещё нет, вызывающий код
    должен сперва завести заглушку через create_placeholder_parent.
    """
    raise NotImplementedError


def create_placeholder_parent(family_id: int, placeholder_id: int, child_id: int) -> Person:
    """Скрытый узел-заглушка: без имени, даты рождения и пола, несёт рёбра к детям.

    `placeholder_id` выделяет вызывающий код (репозиторий), это чистая функция.
    """
    raise NotImplementedError


def merge_placeholder_into(
    placeholder_id: int, real_parent_id: int, relations: list[ParentOf]
) -> list[ParentOf]:
    """Первый добавленный настоящий родитель занимает место заглушки.

    Возвращает рёбра заглушки, переписанные на real_parent_id; сама заглушка
    удаляется вызывающим кодом.
    """
    raise NotImplementedError


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
