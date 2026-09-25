"""Встроенный список городов для выбора часового пояса, каждому — строка IANA.

Покрывает города России и соседних стран плюс крупные мировые. Расширяется
правкой этого файла.
"""

from __future__ import annotations

# {название на русском: IANA-идентификатор}
CITIES: dict[str, str] = {
    "Москва": "Europe/Moscow",
    "Санкт-Петербург": "Europe/Moscow",
    "Калининград": "Europe/Kaliningrad",
    "Екатеринбург": "Asia/Yekaterinburg",
    "Новосибирск": "Asia/Novosibirsk",
    "Красноярск": "Asia/Krasnoyarsk",
    "Иркутск": "Asia/Irkutsk",
    "Владивосток": "Asia/Vladivostok",
    "Минск": "Europe/Minsk",
    "Киев": "Europe/Kyiv",
    "Алматы": "Asia/Almaty",
    "Ташкент": "Asia/Tashkent",
    "Ереван": "Asia/Yerevan",
    "Тбилиси": "Asia/Tbilisi",
    "Баку": "Asia/Baku",
    "Лондон": "Europe/London",
    "Берлин": "Europe/Berlin",
    "Нью-Йорк": "America/New_York",
    "Лос-Анджелес": "America/Los_Angeles",
    "Дубай": "Asia/Dubai",
}
