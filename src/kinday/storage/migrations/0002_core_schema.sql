-- Полная схема из SPEC 5.3: families, people, accounts, memberships, relations,
-- events, reminder_overrides, reminders, invites. Таблица schema_version создана
-- миграцией 0001, её номер ведёт apply_migrations.
--
-- Все моменты времени хранятся строками в UTC, даты и время суток — строками
-- ISO (см. storage/codecs.py): SQLite своего типа для них не имеет, а
-- фиксированный формат даёт лексикографическое сравнение, на котором работают
-- выборка тика и зачистка будущих напоминаний.
--
-- families.owner_account_id и accounts.current_family_id ссылаются друг на
-- друга; порядок создания таблиц значения не имеет — SQLite проверяет внешние
-- ключи при вставке, а сценарий создания семьи заводит аккаунт с пустой
-- текущей семьёй раньше самой семьи.

CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    -- Текущая семья (SPEC 2.1). NULL — как у только что созданного аккаунта.
    current_family_id INTEGER REFERENCES families (id) ON DELETE SET NULL,
    -- Настройки напоминаний принадлежат аккаунту целиком, отдельной таблицы
    -- настроек нет: один набор на все его семьи (SPEC 2.1, 5.3).
    timezone TEXT NOT NULL,
    offsets_days TEXT NOT NULL,
    time_of_day TEXT NOT NULL,
    chat_id INTEGER,
    delivery_enabled INTEGER NOT NULL DEFAULT 1 CHECK (delivery_enabled IN (0, 1))
);

CREATE TABLE families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner_account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    name TEXT,
    gender TEXT CHECK (gender IN ('male', 'female')),
    birth_date TEXT,
    is_placeholder INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
    -- Заглушка неизвестного родителя не имеет ни имени, ни пола, ни даты
    -- рождения (SPEC 4.1): иначе она показалась бы в списках и получила бы
    -- событие дня рождения.
    CHECK (
        is_placeholder = 0
        OR (name IS NULL AND gender IS NULL AND birth_date IS NULL)
    )
);

CREATE INDEX people_family ON people (family_id);

-- Связь Telegram-аккаунта с записью человека в конкретной семье (SPEC 5.3):
-- одна запись на пару (аккаунт, семья) и один аккаунт на запись человека.
CREATE TABLE memberships (
    account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    family_id INTEGER NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    PRIMARY KEY (account_id, family_id)
);

CREATE UNIQUE INDEX memberships_person ON memberships (person_id);

CREATE INDEX memberships_family ON memberships (family_id);

-- Хранятся только базовые рёбра (SPEC 4.1): parent_of направленное
-- (a_id — родитель, b_id — ребёнок), spouse_of симметричное и лежит одной
-- строкой, обход графа читает его в обе стороны. Первичный ключ не даёт
-- завести одно и то же ребро дважды.
CREATE TABLE relations (
    family_id INTEGER NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('parent_of', 'spouse_of')),
    a_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    b_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    PRIMARY KEY (family_id, kind, a_id, b_id)
);

CREATE INDEX relations_a ON relations (a_id);

CREATE INDEX relations_b ON relations (b_id);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    is_recurring_yearly INTEGER NOT NULL DEFAULT 1 CHECK (is_recurring_yearly IN (0, 1)),
    -- kind, а не совпадение title со строкой, определяет исключение из SPEC 3.4
    -- и формат текста напоминания (SPEC 5.3).
    kind TEXT NOT NULL DEFAULT 'custom' CHECK (kind IN ('birthday', 'custom'))
);

CREATE INDEX events_family ON events (family_id);

CREATE INDEX events_person ON events (person_id);

-- Единственное исключение из «настройки принадлежат аккаунту» (SPEC 5.3):
-- переопределение смещений и времени суток для пары «аккаунт и событие».
CREATE TABLE reminder_overrides (
    account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    offsets_days TEXT NOT NULL,
    time_of_day TEXT NOT NULL,
    PRIMARY KEY (account_id, event_id)
);

-- Источник истины по напоминаниям, в памяти состояние не хранится (SPEC 5.3).
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    offset_days INTEGER NOT NULL,
    occurrence_date TEXT NOT NULL,
    due_at_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'missed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT,
    -- Момент последнего перехода статуса: порты mark_sent/mark_missed/mark_failed
    -- получают его параметром `at`, а sent_at по SPEC значит именно фактическую
    -- отправку и для missed/failed остаётся пустым.
    status_changed_at TEXT
);

CREATE UNIQUE INDEX reminders_unique
    ON reminders (event_id, person_id, occurrence_date, offset_days);

CREATE INDEX reminders_due ON reminders (status, due_at_utc);

-- Приглашение привязано к конкретной записи человека (SPEC 3.2). Каскад по
-- person_id: удаление записи лишает код смысла, привязывать по нему нечего
-- (SPEC 4.3), а журнал удалённого вне скоупа первой версии (SPEC 7).
CREATE TABLE invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT
);

CREATE INDEX invites_person ON invites (person_id);
