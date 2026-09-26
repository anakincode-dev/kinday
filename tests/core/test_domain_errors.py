"""Доменный отказ: код наружу, пояснение в журнал, совместимость с ValueError."""

from __future__ import annotations

import copy
import pickle
from collections.abc import Callable

import pytest

from kinday.core.errors import DomainError, DomainErrorCode


def test_domain_error_is_a_value_error() -> None:
    """Ядро годами бросало ValueError — вызывающий код и тесты ловят его до сих пор."""
    with pytest.raises(ValueError, match="уже есть двое родителей"):
        raise DomainError(DomainErrorCode.THIRD_PARENT, "У человека 17 уже есть двое родителей")


def test_detail_is_optional_and_falls_back_to_the_code() -> None:
    assert str(DomainError(DomainErrorCode.NOT_MEMBER)) == "NOT_MEMBER"


@pytest.mark.parametrize("restore", [copy.copy, lambda error: pickle.loads(pickle.dumps(error))])
def test_code_survives_copying(restore: Callable[[DomainError], DomainError]) -> None:
    """Базовый BaseException.__reduce__ отдаёт только args, и код бы потерялся."""
    original = DomainError(DomainErrorCode.INVITE_EXPIRED, "приглашение 42 просрочено")
    restored = restore(original)

    assert isinstance(restored, DomainError)
    assert restored.code is DomainErrorCode.INVITE_EXPIRED
    assert str(restored) == str(original)
