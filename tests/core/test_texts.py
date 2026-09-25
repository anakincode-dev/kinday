"""Тесты формулировок родства и текста напоминания: relation_word, reminder_text."""

from __future__ import annotations

from datetime import date

from kinday.core.models import Gender
from kinday.core.texts import relation_word, reminder_text


def test_relation_word_sibling_male_is_brother_genitive() -> None:
    """Критерий приёмки 17: слово для брата с учётом пола, в падеже для вставки в текст."""
    assert relation_word("sibling", Gender.MALE) == "вашего брата"


def test_relation_word_sibling_female_is_sister_genitive() -> None:
    """Критерий приёмки 17: слово для сестры с учётом пола."""
    assert relation_word("sibling", Gender.FEMALE) == "вашей сестры"


def test_relation_word_parent_male_and_female() -> None:
    assert relation_word("parent", Gender.MALE) == "вашего отца"
    assert relation_word("parent", Gender.FEMALE) == "вашей матери"


def test_relation_word_grandparent_male_and_female() -> None:
    """Пример из SPEC 4.2: «ваша бабушка» — родство на глубину два шага."""
    assert relation_word("grandparent", Gender.MALE) == "вашего дедушки"
    assert relation_word("grandparent", Gender.FEMALE) == "вашей бабушки"


def test_relation_word_unknown_relation_returns_empty() -> None:
    assert relation_word("cousin", Gender.MALE) == ""


def test_relation_word_none_gender_returns_empty() -> None:
    """Заглушка пола не имеет и в текстах не участвует (SPEC 4.2)."""
    assert relation_word("sibling", None) == ""


def test_reminder_text_with_relation_phrase() -> None:
    """Пример из SPEC 3.3 и 5.5: полный текст с родством."""
    text = reminder_text(
        name="Петра",
        relation_phrase="вашего отца",
        days_until=7,
        event_date=date(2027, 3, 1),
        turning_age=47,
    )
    assert text == "Через 7 дней день рождения у Петра, вашего отца. 1 марта, исполнится 47."


def test_reminder_text_without_relation_phrase_uses_only_name() -> None:
    """Критерий приёмки 23 / SPEC 4.2: родство не выведено — остаётся только имя."""
    text = reminder_text(
        name="Марины",
        relation_phrase="",
        days_until=7,
        event_date=date(2027, 6, 3),
        turning_age=30,
    )
    assert text == "Через 7 дней день рождения у Марины. 3 июня, исполнится 30."


def test_reminder_text_today_offset() -> None:
    text = reminder_text(
        name="Марины",
        relation_phrase="",
        days_until=0,
        event_date=date(2027, 6, 3),
        turning_age=30,
    )
    assert text == "Сегодня день рождения у Марины. 3 июня, исполнится 30."


def test_reminder_text_day_word_agrees_with_number() -> None:
    base = {
        "name": "Марины",
        "relation_phrase": "",
        "event_date": date(2027, 6, 3),
        "turning_age": 30,
    }
    assert reminder_text(days_until=1, **base).startswith("Через 1 день")
    assert reminder_text(days_until=2, **base).startswith("Через 2 дня")
    assert reminder_text(days_until=5, **base).startswith("Через 5 дней")
    assert reminder_text(days_until=11, **base).startswith("Через 11 дней")
    assert reminder_text(days_until=21, **base).startswith("Через 21 день")
