"""Базовые рёбра родства, вывод родства обходом графа, заглушки и их слияние.

Хранятся только parent_of и spouse_of (см. models.py). Брат, сестра, бабушка,
дядя и кузен не хранятся, а выводятся обходом графа отсюда.
"""

from __future__ import annotations

from kinday.core.models import Gender, ParentOf, Person, SpouseOf

MAX_RELATION_DEPTH = 3


def add_parent(person: Person, new_parent_kind: str, relations: list[ParentOf]) -> ParentOf:
    """Записать «отец»/«мать» как parent_of(N, A). Отклонить третьего родителя."""
    raise NotImplementedError


def add_child(person: Person, relations: list[ParentOf]) -> ParentOf:
    """Записать «сын»/«дочь» как parent_of(A, N)."""
    raise NotImplementedError


def add_spouse(person: Person) -> SpouseOf:
    """Записать «супруг»/«супруга» как spouse_of(A, N)."""
    raise NotImplementedError


def add_sibling(person: Person, relations: list[ParentOf]) -> list[ParentOf]:
    """Записать «брат»/«сестра»: parent_of(P, N) для каждого известного родителя A.

    Если родителей ещё нет, создаёт скрытую заглушку (см. create_placeholder_parent).
    """
    raise NotImplementedError


def create_placeholder_parent(child_id: int) -> Person:
    """Скрытый узел-заглушка: без имени, даты рождения и пола, несёт рёбра к детям."""
    raise NotImplementedError


def merge_placeholder_into(placeholder: Person, real_parent: Person) -> list[ParentOf]:
    """Первый добавленный настоящий родитель занимает место заглушки."""
    raise NotImplementedError


def infer_relation_text(from_person_id: int, to_person_id: int, to_gender: Gender | None) -> str:
    """Родство от получателя к герою события, обход графа глубиной не больше трёх шагов.

    Если родство не выводится или лежит дальше MAX_RELATION_DEPTH, вернуть пустую строку.
    """
    raise NotImplementedError
