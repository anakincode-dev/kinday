---
name: stage
description: Реализовать этап из PLAN.md по номеру
disable-model-invocation: true
---
Реализуй этап $ARGUMENTS из PLAN.md, сверяясь с SPEC.md.

1. Создай ветку feat/stage-$ARGUMENTS от свежего main
   (git switch main, git pull --ff-only, git switch -c ...).
2. Сначала падающие тесты по критериям этапа, потом код.
3. Прогони uv run pytest, uv run ruff check ., uv run ruff format --check .,
   uv run ty check. Устраняй причину, а не симптом: не глуши ошибки
   и не подгоняй тесты.
4. Покажи вывод проверок как доказательство.
5. Субагентом проверь diff против SPEC.md и PLAN.md: только пробелы
   в требованиях и корректности, без стилистики. Исправь найденное.
6. Отметь этап в PLAN.md как готовый, закоммить, push, gh pr create.
7. Дождись CI (gh pr checks --watch). Если зелёный — слей squash-ом
   и удали ветку.
