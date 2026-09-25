# kinday

Telegram-бот семейных напоминаний: хранит генеалогическое дерево вместе с
важными датами и заранее напоминает о них каждому родственнику в личном чате.
Полная спецификация — [SPEC.md](SPEC.md).

## Запуск

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # если uv ещё не установлен
uv sync                                           # зависимости
cp .env.example .env && $EDITOR .env              # заполнить BOT_TOKEN
uv run python -m kinday                           # старт бота и планировщика
```

## Повседневные команды

```bash
uv run pytest              # тесты
uv run ruff check .        # линтер
uv run ruff format .       # форматирование
uv run ty check            # проверка типов
```

## Структура

```
src/kinday/
  core/          доменная логика, чистый Python — не знает про Telegram и SQLite
  storage/       SQLite: адаптеры репозиториев, схема, миграции
  scheduler/     тик, материализация, отправка напоминаний
  telegram/      aiogram: роутеры, диалоги, реализация Notifier
  config.py      чтение переменных окружения
  __main__.py    точка входа
tests/           тесты, включая проверку границы core
.github/         CI и шаблоны issue и PR
.githooks/       проверки перед коммитом
```

Правила ведения работы описаны в [CONTRIBUTING.md](CONTRIBUTING.md), план
реализации — в [PLAN.md](PLAN.md).
