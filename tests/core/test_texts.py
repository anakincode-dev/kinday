"""Тесты формулировок родства и текста напоминания: relation_word, reminder_text."""

from __future__ import annotations

from datetime import date

from kinday.core.models import Gender
from kinday.core.texts import relation_word, reminder_text

# --- relation_word: именительный падеж, оба пола для всех 10 категорий родства ---


def test_relation_word_parent_male_and_female() -> None:
    assert relation_word("parent", Gender.MALE) == "ваш отец"
    assert relation_word("parent", Gender.FEMALE) == "ваша мать"


def test_relation_word_child_male_and_female() -> None:
    assert relation_word("child", Gender.MALE) == "ваш сын"
    assert relation_word("child", Gender.FEMALE) == "ваша дочь"


def test_relation_word_spouse_male_and_female() -> None:
    assert relation_word("spouse", Gender.MALE) == "ваш муж"
    assert relation_word("spouse", Gender.FEMALE) == "ваша жена"


def test_relation_word_sibling_male_and_female() -> None:
    """Критерий приёмки 17: слово для брата/сестры с учётом пола."""
    assert relation_word("sibling", Gender.MALE) == "ваш брат"
    assert relation_word("sibling", Gender.FEMALE) == "ваша сестра"


def test_relation_word_grandparent_male_and_female() -> None:
    """Пример из SPEC 4.2: «ваша бабушка» — родство на глубину два шага."""
    assert relation_word("grandparent", Gender.MALE) == "ваш дедушка"
    assert relation_word("grandparent", Gender.FEMALE) == "ваша бабушка"


def test_relation_word_grandchild_male_and_female() -> None:
    assert relation_word("grandchild", Gender.MALE) == "ваш внук"
    assert relation_word("grandchild", Gender.FEMALE) == "ваша внучка"


def test_relation_word_parent_sibling_male_and_female() -> None:
    assert relation_word("parent_sibling", Gender.MALE) == "ваш дядя"
    assert relation_word("parent_sibling", Gender.FEMALE) == "ваша тётя"


def test_relation_word_sibling_child_male_and_female() -> None:
    assert relation_word("sibling_child", Gender.MALE) == "ваш племянник"
    assert relation_word("sibling_child", Gender.FEMALE) == "ваша племянница"


def test_relation_word_grandparent_parent_male_and_female() -> None:
    assert relation_word("grandparent_parent", Gender.MALE) == "ваш прадедушка"
    assert relation_word("grandparent_parent", Gender.FEMALE) == "ваша прабабушка"


def test_relation_word_grandchild_child_male_and_female() -> None:
    assert relation_word("grandchild_child", Gender.MALE) == "ваш правнук"
    assert relation_word("grandchild_child", Gender.FEMALE) == "ваша правнучка"


def test_relation_word_unknown_relation_returns_empty() -> None:
    assert relation_word("cousin", Gender.MALE) == ""


def test_relation_word_none_gender_returns_empty() -> None:
    """Заглушка пола не имеет и в текстах не участвует (SPEC 4.2)."""
    assert relation_word("sibling", None) == ""


# --- reminder_text: три формата ---


def test_reminder_text_birthday_with_relation_phrase() -> None:
    """Пример из SPEC 3.3 и 4.2: день рождения, полный текст с родством."""
    text = reminder_text(
        name="Пётр",
        relation_phrase="ваш отец",
        days_until=7,
        event_title="День рождения",
        is_birthday=True,
        is_recurring_yearly=True,
        event_date=date(2027, 3, 1),
        years=47,
    )
    assert text == "Через 7 дней день рождения — Пётр, ваш отец. 1 марта, исполнится 47."


def test_reminder_text_recurring_yearly_event_with_relation_phrase() -> None:
    """Пример из SPEC 3.3: ежегодное событие (не день рождения), с родством."""
    text = reminder_text(
        name="Пётр",
        relation_phrase="ваш отец",
        days_until=7,
        event_title="Годовщина свадьбы",
        is_birthday=False,
        is_recurring_yearly=True,
        event_date=date(2027, 3, 1),
        years=25,
    )
    assert text == "Через 7 дней: Годовщина свадьбы — Пётр, ваш отец. 1 марта, 25 лет."


def test_reminder_text_one_time_event_without_relation_phrase() -> None:
    """Пример из SPEC 3.3 / критерий 23: разовое событие, родство не выведено."""
    text = reminder_text(
        name="Марина",
        relation_phrase="",
        days_until=7,
        event_title="Выпускной",
        is_birthday=False,
        is_recurring_yearly=False,
        event_date=date(2027, 6, 3),
        years=0,
    )
    assert text == "Через 7 дней: Выпускной — Марина. 3 июня."


def test_reminder_text_without_relation_phrase_uses_only_name_no_comma() -> None:
    text = reminder_text(
        name="Марина",
        relation_phrase="",
        days_until=7,
        event_title="День рождения",
        is_birthday=True,
        is_recurring_yearly=True,
        event_date=date(2027, 6, 3),
        years=30,
    )
    assert text == "Через 7 дней день рождения — Марина. 3 июня, исполнится 30."


def test_reminder_text_name_is_passed_in_nominative_case() -> None:
    """Имя не склоняется: «Пётр», а не «Петра» (правки этапа 2b)."""
    text = reminder_text(
        name="Пётр",
        relation_phrase="",
        days_until=7,
        event_title="День рождения",
        is_birthday=True,
        is_recurring_yearly=True,
        event_date=date(2027, 3, 1),
        years=47,
    )
    assert "Пётр" in text
    assert "Петра" not in text


def test_reminder_text_today_offset() -> None:
    text = reminder_text(
        name="Марина",
        relation_phrase="",
        days_until=0,
        event_title="День рождения",
        is_birthday=True,
        is_recurring_yearly=True,
        event_date=date(2027, 6, 3),
        years=30,
    )
    assert text == "Сегодня день рождения — Марина. 3 июня, исполнится 30."


def test_reminder_text_tomorrow_offset() -> None:
    text = reminder_text(
        name="Марина",
        relation_phrase="",
        days_until=1,
        event_title="День рождения",
        is_birthday=True,
        is_recurring_yearly=True,
        event_date=date(2027, 6, 3),
        years=30,
    )
    assert text == "Завтра день рождения — Марина. 3 июня, исполнится 30."


def test_reminder_text_day_word_agrees_with_number() -> None:
    base = {
        "name": "Марина",
        "relation_phrase": "",
        "event_title": "День рождения",
        "is_birthday": True,
        "is_recurring_yearly": True,
        "event_date": date(2027, 6, 3),
        "years": 30,
    }
    assert reminder_text(days_until=2, **base).startswith("Через 2 дня")
    assert reminder_text(days_until=5, **base).startswith("Через 5 дней")
    assert reminder_text(days_until=11, **base).startswith("Через 11 дней")
    assert reminder_text(days_until=21, **base).startswith("Через 21 день")


def test_reminder_text_year_word_agrees_with_number() -> None:
    base = {
        "name": "Пётр",
        "relation_phrase": "ваш отец",
        "days_until": 7,
        "event_title": "Годовщина свадьбы",
        "is_birthday": False,
        "is_recurring_yearly": True,
        "event_date": date(2027, 3, 1),
    }
    assert reminder_text(years=1, **base).endswith("1 марта, 1 год.")
    assert reminder_text(years=2, **base).endswith("1 марта, 2 года.")
    assert reminder_text(years=5, **base).endswith("1 марта, 5 лет.")
    assert reminder_text(years=11, **base).endswith("1 марта, 11 лет.")
    assert reminder_text(years=21, **base).endswith("1 марта, 21 год.")
    assert reminder_text(years=25, **base).endswith("1 марта, 25 лет.")
