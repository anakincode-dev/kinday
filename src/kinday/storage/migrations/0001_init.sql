-- Начальная схема. Таблицы families/people/accounts/relations/events/
-- reminder_overrides/reminders/invites добавятся отдельными миграциями
-- по мере реализации соответствующих этапов (см. PLAN.md).

CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

INSERT INTO schema_version (version) VALUES (1);
