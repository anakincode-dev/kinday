"""Фиксированный список городов семьи, каждому — строка IANA."""

from __future__ import annotations

# {название на русском: IANA-идентификатор}
CITIES: dict[str, str] = {
    "Санкт-Петербург": "Europe/Moscow",
    "Новосибирск": "Asia/Novosibirsk",
    "Астана": "Asia/Almaty",
    "Павлодар": "Asia/Almaty",
    "Оснабрюк (Германия)": "Europe/Berlin",
}
