"""Критерий приёмки 3 из SPEC.md: без BOT_TOKEN сервис не стартует и объясняет почему."""

from __future__ import annotations

import pytest

from kinday.config import ConfigError, Settings


def test_missing_bot_token_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        Settings.from_env()


def test_blank_bot_token_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "   ")

    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        Settings.from_env()
