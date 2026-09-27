# kinday

Telegram-бот семейных напоминаний: хранит генеалогическое дерево вместе с
важными датами и заранее напоминает о них каждому родственнику в личном чате.
Полная спецификация — [SPEC.md](SPEC.md).

## Установка на сервере

1. Установить зависимости и скопировать конфигурацию:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh   # если uv ещё не установлен
   sudo apt-get update && sudo apt-get install -y sqlite3  # для отладки
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin kinday
   sudo mkdir -p /opt/kinday /var/lib/kinday/backups /etc/kinday
   sudo chown -R kinday:kinday /var/lib/kinday
   ```

2. Скопировать репозиторий и установить зависимости:
   ```bash
   cd /opt
   sudo git clone /workspace/femevmen /opt/kinday
   sudo chown -R root:root /opt/kinday
   cd /opt/kinday
   sudo -u kinday uv sync --frozen --python /usr/bin/python3.12
   # Проверка: readlink -f /opt/kinday/.venv/bin/python
   # Должно быть: /usr/bin/python3.12
   ```

3. Настроить токен и запустить сервис:
   ```bash
   # Создать /etc/kinday/token.env с BOT_TOKEN (права 600, владелец root)
   sudo bash -c 'umask 077; echo "BOT_TOKEN=your_token_here" > /etc/kinday/token.env'
   sudo chmod 600 /etc/kinday/token.env
   # Дополнительно: ограничить журнал (см. ниже)
   sudo cp deploy/kinday.service /etc/systemd/system/
   sudo cp deploy/kinday-backup.service /etc/systemd/system/
   sudo cp deploy/kinday-backup.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now kinday kinday-backup.timer
   ```

4. Ограничить журнал (опционально, но рекомендуется):
   ```bash
   sudo mkdir -p /etc/systemd/journald.conf.d
   sudo cp deploy/journald-kinday.conf /etc/systemd/journald.conf.d/
   sudo systemctl reload systemd-journald
   ```

## Повседневные команды

```bash
cd /opt/kinday
sudo -u kinday uv run pytest              # тесты
sudo -u kinday uv run ruff check .        # линтер
sudo -u kinday uv run ruff format .       # форматирование
sudo -u kinday uv run ty check            # проверка типов
```

## Обновление

```bash
cd /opt/kinday
sudo git pull --ff-only
sudo -u kinday uv sync --frozen --python /usr/bin/python3.12
sudo systemctl restart kinday
```

## Восстановление из бэкапа

Если база повреждена, восстановите из последнего бэкапа:

```bash
# Остановить сервис
sudo systemctl stop kinday

# Подменить файл базы (последний бэкап)
sudo cp /var/lib/kinday/backups/kinday-*.db /var/lib/kinday/kinday.db
# Удалить WAL-файлы от повреждённой базы
sudo rm -f /var/lib/kinday/kinday.db-wal /var/lib/kinday/kinday.db-shm

# Запустить сервис
sudo systemctl start kinday
```

## Просмотр логов

```bash
# Журнал сервиса в реальном времени
sudo journalctl -u kinday -f

# Журнал таймера бэкапа
sudo journalctl -u kinday-backup.timer -f

# Журнал последнего бэкапа
sudo journalctl -u kinday-backup.service -n 50

# Ограничение размера журнала — 200M (см. deploy/journald-kinday.conf)
sudo du -sh /var/log/journal/
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
deploy/        systemd-юниты и конфигурация
tests/         тесты, включая проверку границы core
.github/       CI и шаблоны issue и PR
.githooks/     проверки перед коммитом
```

Правила ведения работы описаны в [CONTRIBUTING.md](CONTRIBUTING.md), план
реализации — в [PLAN.md](PLAN.md).
