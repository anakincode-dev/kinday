"""Тесты базовых рёбер родства: add_parent, add_child, add_spouse, add_sibling,

create_placeholder_parent, merge_placeholder_into. Только рёбра в памяти,
без хранилища и без вывода родства текстом (см. test этапа 2b).
"""

from __future__ import annotations

import pytest

from kinday.core.models import ParentOf, SpouseOf
from kinday.core.relations import (
    add_child,
    add_parent,
    add_sibling,
    add_spouse,
    create_placeholder_parent,
    merge_placeholder_into,
)

FAMILY_ID = 1
FATHER_ID = 1
ANTON_ID = 2
MARINA_ID = 3
MOTHER_ID = 4
STRANGER_ID = 5
PLACEHOLDER_ID = 100


def test_add_parent_creates_edge_from_new_parent_to_person() -> None:
    edge = add_parent(ANTON_ID, FATHER_ID, relations=[])
    assert edge == ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)


def test_add_parent_rejects_third_parent() -> None:
    """Критерий приёмки 21: попытка добавить третьего родителя отклоняется."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
    ]
    with pytest.raises(ValueError, match="родител"):
        add_parent(ANTON_ID, STRANGER_ID, relations)


def test_add_child_creates_edge_from_person_to_new_child() -> None:
    edge = add_child(FATHER_ID, ANTON_ID, relations=[])
    assert edge == ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)


def test_add_child_rejects_third_parent_for_new_child() -> None:
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
    ]
    with pytest.raises(ValueError, match="родител"):
        add_child(STRANGER_ID, ANTON_ID, relations)


def test_add_spouse_creates_symmetric_edge() -> None:
    edge = add_spouse(FATHER_ID, MOTHER_ID)
    assert edge == SpouseOf(a_id=FATHER_ID, b_id=MOTHER_ID)


def test_add_sibling_with_known_parent_creates_edge_from_parent_to_sibling() -> None:
    """Критерий приёмки 17 (часть про ребро): отец уже заведён, добавляем сестру."""
    relations = [ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)]
    edges = add_sibling(ANTON_ID, MARINA_ID, relations)
    assert edges == [ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID)]


def test_add_sibling_with_two_known_parents_creates_edge_from_each() -> None:
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
    ]
    edges = add_sibling(ANTON_ID, MARINA_ID, relations)
    assert sorted(edges, key=lambda e: e.parent_id) == [
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=MARINA_ID),
    ]


def test_create_placeholder_parent_returns_placeholder_without_identity() -> None:
    placeholder, edge = create_placeholder_parent(FAMILY_ID, PLACEHOLDER_ID, ANTON_ID)
    assert placeholder.id == PLACEHOLDER_ID
    assert placeholder.family_id == FAMILY_ID
    assert placeholder.is_placeholder is True
    assert placeholder.name is None
    assert placeholder.gender is None
    assert placeholder.birth_date is None
    assert edge == ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID)


def test_add_sibling_without_known_parents_uses_placeholder() -> None:
    """Критерий приёмки 18: сестра без известных родителей — через заглушку."""
    placeholder, edge_to_anton = create_placeholder_parent(FAMILY_ID, PLACEHOLDER_ID, ANTON_ID)
    relations = [edge_to_anton]

    sibling_edges = add_sibling(ANTON_ID, MARINA_ID, relations)

    assert sibling_edges == [ParentOf(parent_id=placeholder.id, child_id=MARINA_ID)]


def test_merge_placeholder_into_real_parent_rewrites_edges() -> None:
    """Критерий приёмки 19: первый настоящий родитель занимает место заглушки."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=MARINA_ID),
    ]

    rewritten = merge_placeholder_into(PLACEHOLDER_ID, FATHER_ID, relations)

    assert rewritten == [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]


def test_merge_placeholder_into_ignores_unrelated_edges() -> None:
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
    ]

    rewritten = merge_placeholder_into(PLACEHOLDER_ID, FATHER_ID, relations)

    assert rewritten == [ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)]


def test_second_parent_answer_yes_propagates_to_existing_siblings() -> None:
    """Критерий приёмки 20, ответ «да»: рёбра протягиваются ко всем детям первого родителя."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    children_of_first_parent = [ANTON_ID, MARINA_ID]

    new_edges = [
        add_parent(child_id, MOTHER_ID, relations) for child_id in children_of_first_parent
    ]

    assert sorted(new_edges, key=lambda e: e.child_id) == [
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=MARINA_ID),
    ]


def test_second_parent_answer_no_affects_only_chosen_person() -> None:
    """Критерий приёмки 20, ответ «нет»: ребро только к выбранному человеку."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]

    new_edge = add_parent(ANTON_ID, MOTHER_ID, relations)

    assert new_edge == ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID)
    assert ParentOf(parent_id=MOTHER_ID, child_id=MARINA_ID) not in relations
