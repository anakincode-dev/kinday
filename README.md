# femevmen

Приватный проект на Python 3.12.

## Быстрый старт

```bash
# установка uv, если его ещё нет
curl -LsSf https://astral.sh/uv/install.sh | sh

# зависимости и dev-инструменты
uv sync

# активация git-хуков этого репозитория
git config core.hooksPath .githooks
```

## Повседневные команды

```bash
uv run pytest              # тесты
uv run ruff check .        # линтер
uv run ruff format .       # форматирование
```

## Структура

```
src/femevmen/    код пакета
tests/           тесты
.github/         CI и шаблоны issue и PR
.githooks/       проверки перед коммитом
```

Правила ведения работы описаны в [CONTRIBUTING.md](CONTRIBUTING.md).
