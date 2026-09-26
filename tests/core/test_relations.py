"""Тесты базовых рёбер родства и вывода родства текстом.

add_parent, add_child, add_spouse, add_sibling, attach_parent,
create_placeholder_parent, merge_placeholder_into, infer_relation_text.
Только рёбра в памяти, без хранилища.
"""

from __future__ import annotations

import pytest

from kinday.core.models import Gender, ParentOf, Person, SpouseOf
from kinday.core.relations import (
    ParentChange,
    SiblingAttachment,
    SiblingsQuestionRequired,
    add_child,
    add_parent,
    add_sibling,
    add_spouse,
    attach_parent,
    create_placeholder_parent,
    infer_relation_text,
    merge_placeholder_into,
)

FAMILY_ID = 1
FATHER_ID = 1
ANTON_ID = 2
MARINA_ID = 3
MOTHER_ID = 4
STRANGER_ID = 5
PLACEHOLDER_ID = 100
HALF_SIBLING_ID = 6
HALF_SIBLING_OTHER_PARENT_ID = 7
NEW_CHILD_ID = 8
GRANDFATHER_ID = 9


def _person(
    person_id: int, *, is_placeholder: bool = False, gender: Gender | None = None
) -> Person:
    return Person(
        id=person_id,
        family_id=FAMILY_ID,
        name=None if is_placeholder else f"person-{person_id}",
        gender=gender,
        birth_date=None,
        is_placeholder=is_placeholder,
    )


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


def test_add_parent_rejects_self_reference() -> None:
    with pytest.raises(ValueError, match="сам"):
        add_parent(ANTON_ID, ANTON_ID, relations=[])


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


def test_add_child_rejects_self_reference() -> None:
    with pytest.raises(ValueError, match="ребён"):
        add_child(ANTON_ID, ANTON_ID, relations=[])


def test_add_spouse_creates_symmetric_edge() -> None:
    edge = add_spouse(FATHER_ID, MOTHER_ID)
    assert edge == SpouseOf(a_id=FATHER_ID, b_id=MOTHER_ID)


def test_add_spouse_rejects_self_reference() -> None:
    with pytest.raises(ValueError, match="сам"):
        add_spouse(FATHER_ID, FATHER_ID)


def test_add_sibling_with_known_parent_creates_edge_from_parent_to_sibling() -> None:
    """Критерий приёмки 17 (часть про ребро): отец уже заведён, добавляем сестру."""
    relations = [ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)]
    result = add_sibling(ANTON_ID, MARINA_ID, relations)
    assert result == SiblingAttachment(
        placeholder=None, edges=[ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID)]
    )


def test_add_sibling_with_two_known_parents_creates_edge_from_each() -> None:
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
    ]
    result = add_sibling(ANTON_ID, MARINA_ID, relations)
    assert result.placeholder is None
    assert sorted(result.edges, key=lambda e: e.parent_id) == [
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


def test_add_sibling_without_known_parents_creates_placeholder_via_core_function() -> None:
    """Критерий приёмки 18: без родителей add_sibling сама заводит заглушку."""
    result = add_sibling(
        ANTON_ID,
        MARINA_ID,
        relations=[],
        family_id=FAMILY_ID,
        placeholder_id=PLACEHOLDER_ID,
    )

    assert result.placeholder is not None
    assert result.placeholder.id == PLACEHOLDER_ID
    assert result.placeholder.is_placeholder is True
    assert sorted(result.edges, key=lambda e: e.child_id) == [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=MARINA_ID),
    ]


def test_add_sibling_without_known_parents_and_without_placeholder_id_raises() -> None:
    """Раньше add_sibling молча возвращала [] — теперь это ошибка, а не тихий отказ."""
    with pytest.raises(ValueError, match="родител"):
        add_sibling(ANTON_ID, MARINA_ID, relations=[])


def test_merge_placeholder_into_real_parent_rewrites_edges() -> None:
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


def test_merge_placeholder_into_moves_edges_upwards_and_spouse() -> None:
    """Заглушка отдаёт настоящему родителю все свои рёбра, а не только рёбра к детям.

    Ребро вверх и супружеское заглушка получает, когда в неё превратился
    удалённый человек с детьми (SPEC 4.3). Не перенести их — значит молча
    порвать родство внука с дедом: удаление заглушки утащит рёбра каскадом.
    """
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=GRANDFATHER_ID, child_id=PLACEHOLDER_ID),
        SpouseOf(a_id=MOTHER_ID, b_id=PLACEHOLDER_ID),
    ]

    rewritten = merge_placeholder_into(PLACEHOLDER_ID, FATHER_ID, relations)

    assert rewritten == [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=GRANDFATHER_ID, child_id=FATHER_ID),
        SpouseOf(a_id=FATHER_ID, b_id=MOTHER_ID),
    ]


def test_merge_placeholder_into_rejects_third_parent_for_real_parent() -> None:
    """Двое своих родителей плюс родитель заглушки — третий родитель (SPEC 4.1, критерий 21).

    Выбросить лишнее ребро на своё усмотрение функция не вправе: отказ, чтобы
    вызывающий код сообщил пользователю, а не потерял связь тихо.
    """
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=GRANDFATHER_ID, child_id=PLACEHOLDER_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=FATHER_ID),
        ParentOf(parent_id=MARINA_ID, child_id=FATHER_ID),
    ]

    with pytest.raises(ValueError, match="больше 2 родителей"):
        merge_placeholder_into(PLACEHOLDER_ID, FATHER_ID, relations)


def test_merge_placeholder_into_does_not_duplicate_shared_parent() -> None:
    """Общий родитель заглушки и настоящего родителя переносится один раз, без дубля ребра."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=GRANDFATHER_ID, child_id=PLACEHOLDER_ID),
        ParentOf(parent_id=GRANDFATHER_ID, child_id=FATHER_ID),
    ]

    rewritten = merge_placeholder_into(PLACEHOLDER_ID, FATHER_ID, relations)

    assert rewritten == [ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)]


def test_merge_placeholder_into_rejects_merging_into_own_parent() -> None:
    """Слить заглушку в её собственного родителя — сделать его родителем самому себе."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=PLACEHOLDER_ID),
    ]

    with pytest.raises(ValueError, match="родителем самому себе"):
        merge_placeholder_into(PLACEHOLDER_ID, FATHER_ID, relations)


def test_attach_parent_with_no_known_parents_creates_single_edge() -> None:
    result = attach_parent(
        ANTON_ID, FATHER_ID, relations=[], also_parent_of_siblings=None, people={}
    )
    assert result == ParentChange(
        added=[ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)],
        removed=[],
        removed_placeholder_id=None,
    )


def test_attach_parent_rejects_self_reference() -> None:
    with pytest.raises(ValueError, match="сам"):
        attach_parent(ANTON_ID, ANTON_ID, relations=[], also_parent_of_siblings=None, people={})


def test_attach_parent_merges_placeholder_instead_of_asking_about_siblings() -> None:
    """Критерий приёмки 19: заглушка сливается через функцию ядра, вопрос про братьев не нужен."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=MARINA_ID),
    ]
    people = {PLACEHOLDER_ID: _person(PLACEHOLDER_ID, is_placeholder=True)}

    result = attach_parent(
        ANTON_ID, FATHER_ID, relations, also_parent_of_siblings=None, people=people
    )

    assert result.removed_placeholder_id == PLACEHOLDER_ID
    assert result.removed == [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=MARINA_ID),
    ]
    assert result.added == [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]


def test_attach_parent_placeholder_plus_real_parent_does_not_trigger_already_two_rejection() -> (
    None
):
    """Заглушка + настоящий родитель (двое рёбер) — это слияние, а не отказ «уже двое»."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
    ]
    people = {
        PLACEHOLDER_ID: _person(PLACEHOLDER_ID, is_placeholder=True),
        FATHER_ID: _person(FATHER_ID),
    }

    result = attach_parent(
        ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=None, people=people
    )

    assert result.removed_placeholder_id == PLACEHOLDER_ID
    assert result.added == [ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID)]


def test_attach_parent_single_parent_without_other_children_needs_no_question() -> None:
    relations = [ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID)]
    people = {FATHER_ID: _person(FATHER_ID)}

    result = attach_parent(
        ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=None, people=people
    )

    assert result == ParentChange(
        added=[ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID)],
        removed=[],
        removed_placeholder_id=None,
    )


def test_attach_parent_none_with_siblings_raises_siblings_question_required() -> None:
    """Критерий приёмки 20, часть «спросить»: None при наличии братьев поднимает вопрос."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    with pytest.raises(SiblingsQuestionRequired) as exc_info:
        attach_parent(ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=None, people=people)

    assert exc_info.value.sibling_ids == [MARINA_ID]


def test_attach_parent_question_lists_only_siblings_with_free_slot() -> None:
    """Уточнение: в списке вопроса — только братья/сёстры со свободным слотом."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
        ParentOf(parent_id=FATHER_ID, child_id=HALF_SIBLING_ID),
        ParentOf(parent_id=HALF_SIBLING_OTHER_PARENT_ID, child_id=HALF_SIBLING_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    with pytest.raises(SiblingsQuestionRequired) as exc_info:
        attach_parent(ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=None, people=people)

    assert exc_info.value.sibling_ids == [MARINA_ID]


def test_attach_parent_no_question_when_all_siblings_already_have_two_parents() -> None:
    """Уточнение: если свободного слота нет ни у кого, вопрос не задаётся вовсе."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=HALF_SIBLING_ID),
        ParentOf(parent_id=HALF_SIBLING_OTHER_PARENT_ID, child_id=HALF_SIBLING_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    result = attach_parent(
        ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=None, people=people
    )

    assert result == ParentChange(
        added=[ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID)],
        removed=[],
        removed_placeholder_id=None,
    )


def test_attach_parent_second_parent_via_son_daughter_path_also_asks() -> None:
    """Тот же путь для «сын/дочь»: вызов attach_parent(new_child_id, person_id, ...)

    тоже поднимает вопрос про братьев, если у ребёнка уже есть родитель с другими детьми.
    """
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=NEW_CHILD_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    with pytest.raises(SiblingsQuestionRequired) as exc_info:
        attach_parent(
            NEW_CHILD_ID, MOTHER_ID, relations, also_parent_of_siblings=None, people=people
        )

    assert exc_info.value.sibling_ids == [MARINA_ID]


def test_attach_parent_answer_yes_propagates_to_existing_siblings() -> None:
    """Критерий приёмки 20, ответ «да»: рёбра протягиваются ко всем детям первого родителя."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    result = attach_parent(
        ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=True, people=people
    )

    assert result.added == [
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=MARINA_ID),
    ]


def test_attach_parent_answer_yes_skips_half_sibling_who_already_has_two_parents() -> None:
    """Ответ «да» при сводном брате с двумя родителями — его пропускаем, не падаем."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=HALF_SIBLING_ID),
        ParentOf(parent_id=HALF_SIBLING_OTHER_PARENT_ID, child_id=HALF_SIBLING_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    result = attach_parent(
        ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=True, people=people
    )

    assert result.added == [ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID)]


def test_attach_parent_answer_no_affects_only_chosen_person() -> None:
    """Критерий приёмки 20, ответ «нет»: ребро только к выбранному человеку."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID)}

    result = attach_parent(
        ANTON_ID, MOTHER_ID, relations, also_parent_of_siblings=False, people=people
    )

    assert result.added == [ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID)]


def test_attach_parent_rejects_third_parent() -> None:
    """Критерий приёмки 21 через attach_parent: двое настоящих родителей — отказ."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=MOTHER_ID, child_id=ANTON_ID),
    ]
    people = {FATHER_ID: _person(FATHER_ID), MOTHER_ID: _person(MOTHER_ID)}

    with pytest.raises(ValueError, match="родител"):
        attach_parent(ANTON_ID, STRANGER_ID, relations, also_parent_of_siblings=None, people=people)


def test_infer_relation_text_sibling_returns_sister_word_for_female() -> None:
    """Критерий приёмки 17: отец известен, вывод родства даёт «сестра» с учётом пола."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {
        FATHER_ID: _person(FATHER_ID),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        MARINA_ID: _person(MARINA_ID, gender=Gender.FEMALE),
    }

    text = infer_relation_text(ANTON_ID, MARINA_ID, people, relations)

    assert "сестра" in text


def test_infer_relation_text_sibling_returns_brother_word_in_reverse_direction() -> None:
    """Критерий приёмки 17: то же родство в обратную сторону даёт «брат»."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {
        FATHER_ID: _person(FATHER_ID),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        MARINA_ID: _person(MARINA_ID, gender=Gender.FEMALE),
    }

    text = infer_relation_text(MARINA_ID, ANTON_ID, people, relations)

    assert "брат" in text


def test_infer_relation_text_sibling_through_placeholder_parent() -> None:
    """Критерий приёмки 18: родители неизвестны, общая заглушка всё равно даёт родство."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=MARINA_ID),
    ]
    people = {
        PLACEHOLDER_ID: _person(PLACEHOLDER_ID, is_placeholder=True),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        MARINA_ID: _person(MARINA_ID, gender=Gender.FEMALE),
    }

    text = infer_relation_text(ANTON_ID, MARINA_ID, people, relations)

    assert "сестра" in text


def test_infer_relation_text_parent_and_grandparent() -> None:
    """Родство на глубину 1 и 2 шага: отец и дедушка (пример из SPEC 4.2)."""
    grandfather_id = 9
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=grandfather_id, child_id=FATHER_ID),
    ]
    people = {
        FATHER_ID: _person(FATHER_ID, gender=Gender.MALE),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        grandfather_id: _person(grandfather_id, gender=Gender.MALE),
    }

    assert "отец" in infer_relation_text(ANTON_ID, FATHER_ID, people, relations)
    assert "дедушка" in infer_relation_text(ANTON_ID, grandfather_id, people, relations)


def test_infer_relation_text_uncle_at_exactly_three_steps() -> None:
    """Ровно три шага (родитель-родитель-ребёнок) — дядя, родство ещё выводится."""
    grandfather_id = 9
    uncle_id = 10
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=grandfather_id, child_id=FATHER_ID),
        ParentOf(parent_id=grandfather_id, child_id=uncle_id),
    ]
    people = {
        FATHER_ID: _person(FATHER_ID, gender=Gender.MALE),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        grandfather_id: _person(grandfather_id, gender=Gender.MALE),
        uncle_id: _person(uncle_id, gender=Gender.MALE),
    }

    text = infer_relation_text(ANTON_ID, uncle_id, people, relations)

    assert "дядя" in text


def test_infer_relation_text_beyond_three_steps_returns_empty() -> None:
    """Критерий приёмки 23: кузен дальше трёх шагов — родство не выводится."""
    grandfather_id = 9
    uncle_id = 10
    cousin_id = 11
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=grandfather_id, child_id=FATHER_ID),
        ParentOf(parent_id=grandfather_id, child_id=uncle_id),
        ParentOf(parent_id=uncle_id, child_id=cousin_id),
    ]
    people = {
        FATHER_ID: _person(FATHER_ID, gender=Gender.MALE),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        grandfather_id: _person(grandfather_id, gender=Gender.MALE),
        uncle_id: _person(uncle_id, gender=Gender.MALE),
        cousin_id: _person(cousin_id, gender=Gender.MALE),
    }

    assert infer_relation_text(ANTON_ID, cousin_id, people, relations) == ""


def test_infer_relation_text_no_path_returns_empty() -> None:
    """Родство не выводится вовсе (нет пути в графе) — пустая строка."""
    people = {
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        STRANGER_ID: _person(STRANGER_ID, gender=Gender.MALE),
    }

    assert infer_relation_text(ANTON_ID, STRANGER_ID, people, relations=[]) == ""


def test_infer_relation_text_same_person_returns_empty() -> None:
    people = {ANTON_ID: _person(ANTON_ID, gender=Gender.MALE)}

    assert infer_relation_text(ANTON_ID, ANTON_ID, people, relations=[]) == ""


def test_infer_relation_text_placeholder_as_hero_returns_empty() -> None:
    """Заглушка не порождает событий и не может быть героем события (SPEC 4.1)."""
    relations = [
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=PLACEHOLDER_ID, child_id=MARINA_ID),
    ]
    people = {
        PLACEHOLDER_ID: _person(PLACEHOLDER_ID, is_placeholder=True),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        MARINA_ID: _person(MARINA_ID, gender=Gender.FEMALE),
    }

    assert infer_relation_text(ANTON_ID, PLACEHOLDER_ID, people, relations) == ""


def test_infer_relation_text_hero_without_gender_returns_empty() -> None:
    """Уточнение: пол героя не задан (не заглушка) — текст остаётся только с именем."""
    relations = [
        ParentOf(parent_id=FATHER_ID, child_id=ANTON_ID),
        ParentOf(parent_id=FATHER_ID, child_id=MARINA_ID),
    ]
    people = {
        FATHER_ID: _person(FATHER_ID),
        ANTON_ID: _person(ANTON_ID, gender=Gender.MALE),
        MARINA_ID: _person(MARINA_ID, gender=None),
    }

    assert infer_relation_text(ANTON_ID, MARINA_ID, people, relations) == ""
