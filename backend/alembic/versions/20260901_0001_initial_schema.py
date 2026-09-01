"""Initial RDM database schema.

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        """
        CREATE TABLE dict_values (
            sysname varchar(100) NOT NULL,
            code integer NOT NULL,
            description varchar(255) NOT NULL,
            visible boolean NOT NULL DEFAULT true,
            sort_order integer NOT NULL DEFAULT 0,
            PRIMARY KEY (sysname, code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id bigserial PRIMARY KEY,
            username varchar(100) NOT NULL UNIQUE,
            password_hash text NOT NULL,
            full_name varchar(255) NOT NULL,
            email varchar(255),
            phone varchar(50),
            omnidesk_staff_id varchar(100) UNIQUE,
            is_active boolean NOT NULL DEFAULT true
        )
        """
    )

    op.execute(
        """
        CREATE TABLE roles (
            id smallint PRIMARY KEY,
            name varchar(100) NOT NULL,
            description text,
            visible boolean NOT NULL DEFAULT true
        )
        """
    )

    op.execute(
        """
        CREATE TABLE user_roles (
            user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_id smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
            PRIMARY KEY (user_id, role_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE auth_sessions (
            id bigserial PRIMARY KEY,
            user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_hash text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            last_seen_at timestamptz,
            revoked_at timestamptz
        )
        """
    )

    op.execute(
        """
        CREATE TABLE user_settings (
            user_id bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            timezone varchar(64) NOT NULL DEFAULT 'Asia/Yekaterinburg',
            telegram_chat_id varchar(100),
            bitrix24_user_id varchar(100),
            notify_telegram boolean NOT NULL DEFAULT true,
            notify_bitrix24 boolean NOT NULL DEFAULT true
        )
        """
    )

    op.execute(
        """
        CREATE TABLE clients (
            id bigserial PRIMARY KEY,
            omnidesk_user_id varchar(100) UNIQUE,
            omnidesk_company_id varchar(100),
            display_name varchar(255),
            preferred_contact_type_code integer,
            preferred_contact_value varchar(255),
            last_confirmed_timezone varchar(64),
            timezone_source_code integer,
            last_synced_at timestamptz
        )
        """
    )

    op.execute(
        """
        CREATE TABLE connection_cards (
            id bigserial PRIMARY KEY,
            public_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
            number varchar(50) NOT NULL UNIQUE,
            omnidesk_ticket_number varchar(20) NOT NULL,
            client_id bigint REFERENCES clients(id) ON DELETE SET NULL,
            status_code integer NOT NULL,
            criticality_code integer NOT NULL DEFAULT 0,
            urgency_code integer NOT NULL DEFAULT 0,
            planned_start_at timestamptz NOT NULL,
            planned_duration_minutes integer NOT NULL,
            client_timezone_at_creation varchar(64),
            timezone_source_code integer,
            actual_start_at timestamptz,
            actual_end_at timestamptz,
            l1_owner_id bigint REFERENCES users(id) ON DELETE SET NULL,
            l2_engineer_id bigint REFERENCES users(id) ON DELETE SET NULL,
            assignment_method_code integer NOT NULL DEFAULT 0,
            unsuccessful_cycle_count integer NOT NULL DEFAULT 0,
            client_contact_type_code integer,
            client_contact_value varchar(255),
            description text,
            urgent_reason text,
            out_of_hours_flag boolean NOT NULL DEFAULT false,
            retroactive_flag boolean NOT NULL DEFAULT false,
            overdue_flag boolean NOT NULL DEFAULT false,
            result_code integer,
            engineer_report text,
            created_source_code integer NOT NULL DEFAULT 0,
            created_by_id bigint REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (omnidesk_ticket_number ~ '^[0-9]{3}-[0-9]{6}$'),
            CHECK (planned_duration_minutes BETWEEN 30 AND 720),
            CHECK (actual_end_at IS NULL OR actual_start_at IS NULL OR actual_end_at >= actual_start_at)
        )
        """
    )

    op.execute(
        """
        CREATE FUNCTION set_connection_card_number()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.number IS NULL OR NEW.number = '' THEN
                NEW.number := 'RDM-' || lpad(NEW.id::text, 6, '0');
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE FUNCTION make_connection_card_planned_range(
            planned_start_at timestamptz,
            planned_duration_minutes integer
        )
        RETURNS tstzrange AS $$
            SELECT tstzrange(
                planned_start_at,
                planned_start_at + planned_duration_minutes * interval '1 minute',
                '[)'
            )
        $$ LANGUAGE sql IMMUTABLE
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_connection_cards_set_number
        BEFORE INSERT ON connection_cards
        FOR EACH ROW
        EXECUTE FUNCTION set_connection_card_number()
        """
    )

    op.execute(
        """
        CREATE TABLE card_events (
            id bigserial PRIMARY KEY,
            card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
            event_type_code integer NOT NULL,
            actor_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
            actor_type_code integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            old_values jsonb,
            new_values jsonb,
            comment text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE assignment_cycles (
            id bigserial PRIMARY KEY,
            card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
            cycle_number integer NOT NULL,
            status_code integer NOT NULL,
            started_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            UNIQUE (card_id, cycle_number)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE assignment_attempts (
            id bigserial PRIMARY KEY,
            cycle_id bigint NOT NULL REFERENCES assignment_cycles(id) ON DELETE CASCADE,
            card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
            l2_engineer_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            status_code integer NOT NULL,
            assigned_at timestamptz NOT NULL DEFAULT now(),
            responded_at timestamptz,
            actor_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
            rejection_reason text,
            UNIQUE (cycle_id, l2_engineer_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE schedules (
            id bigserial PRIMARY KEY,
            user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            weekday smallint NOT NULL,
            start_time time NOT NULL,
            end_time time NOT NULL,
            timezone varchar(64) NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            valid_from date,
            valid_to date,
            CHECK (weekday BETWEEN 1 AND 7),
            CHECK (start_time < end_time),
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE absences (
            id bigserial PRIMARY KEY,
            user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            start_at timestamptz NOT NULL,
            end_at timestamptz NOT NULL,
            created_by_id bigint REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            reason varchar(255),
            CHECK (start_at < end_at)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE production_calendar_days (
            date date PRIMARY KEY,
            day_type_code integer NOT NULL,
            is_manual_override boolean NOT NULL DEFAULT false,
            updated_by_id bigint REFERENCES users(id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            comment text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE distribution_members (
            id bigserial PRIMARY KEY,
            user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            pool_code integer NOT NULL,
            is_enabled boolean NOT NULL DEFAULT false,
            enabled_by_id bigint REFERENCES users(id) ON DELETE SET NULL,
            enabled_at timestamptz,
            disabled_by_id bigint REFERENCES users(id) ON DELETE SET NULL,
            disabled_at timestamptz,
            comment text,
            UNIQUE (user_id, pool_code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE distribution_state (
            pool_code integer PRIMARY KEY,
            last_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE notification_templates (
            id bigserial PRIMARY KEY,
            code varchar(100) NOT NULL UNIQUE,
            channel_code integer NOT NULL,
            visible boolean NOT NULL DEFAULT true,
            subject_template text,
            body_template text NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE notifications (
            id bigserial PRIMARY KEY,
            card_id bigint REFERENCES connection_cards(id) ON DELETE CASCADE,
            recipient_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
            channel_code integer NOT NULL,
            event_type_code integer NOT NULL,
            status_code integer NOT NULL DEFAULT 0,
            scheduled_at timestamptz,
            sent_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            error_message text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE integration_attempts (
            id bigserial PRIMARY KEY,
            card_id bigint REFERENCES connection_cards(id) ON DELETE SET NULL,
            system_code integer NOT NULL,
            operation_code integer NOT NULL,
            status_code integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            request_meta jsonb,
            response_meta jsonb,
            error_message text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE audit_log (
            id bigserial PRIMARY KEY,
            actor_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
            actor_type_code integer NOT NULL,
            action_code integer NOT NULL,
            entity_type varchar(100) NOT NULL,
            entity_id bigint,
            created_at timestamptz NOT NULL DEFAULT now(),
            ip_address inet,
            user_agent text,
            old_values jsonb,
            new_values jsonb
        )
        """
    )

    op.execute(
        """
        CREATE TABLE system_settings (
            key varchar(100) PRIMARY KEY,
            value jsonb NOT NULL,
            updated_by_id bigint REFERENCES users(id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            description text
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX ux_connection_cards_one_active_per_ticket
        ON connection_cards (omnidesk_ticket_number)
        WHERE status_code NOT IN (5, 6)
        """
    )
    op.execute("CREATE INDEX ix_connection_cards_ticket ON connection_cards (omnidesk_ticket_number)")
    op.execute("CREATE INDEX ix_connection_cards_status_start ON connection_cards (status_code, planned_start_at)")
    op.execute("CREATE INDEX ix_connection_cards_l2_start ON connection_cards (l2_engineer_id, planned_start_at)")
    op.execute("CREATE INDEX ix_schedules_user_weekday ON schedules (user_id, weekday)")
    op.execute("CREATE INDEX ix_notifications_status_scheduled ON notifications (status_code, scheduled_at)")
    op.execute("CREATE INDEX ix_integration_attempts_system_operation_created ON integration_attempts (system_code, operation_code, created_at)")
    op.execute("CREATE INDEX ix_audit_log_entity_created ON audit_log (entity_type, entity_id, created_at)")

    op.execute(
        """
        ALTER TABLE connection_cards
        ADD CONSTRAINT ex_connection_cards_l2_no_overlap
        EXCLUDE USING gist (
            l2_engineer_id WITH =,
            make_connection_card_planned_range(
                planned_start_at,
                planned_duration_minutes
            ) WITH &&
        )
        WHERE (
            l2_engineer_id IS NOT NULL
            AND status_code IN (1, 2, 3)
        )
        """
    )

    op.execute(
        """
        INSERT INTO dict_values (sysname, code, description, visible, sort_order) VALUES
        ('card_status', 0, 'Создано', true, 10),
        ('card_status', 1, 'Назначено', true, 20),
        ('card_status', 2, 'Подтверждено', true, 30),
        ('card_status', 3, 'Выполняется', true, 40),
        ('card_status', 4, 'Отклонено', true, 50),
        ('card_status', 5, 'Завершено', true, 60),
        ('card_status', 6, 'Отменено', true, 70),

        ('connection_result', 0, 'Завершено успешно', true, 10),
        ('connection_result', 1, 'Требуется повторное подключение', true, 20),
        ('connection_result', 2, 'Клиент не предоставил доступ', true, 30),
        ('connection_result', 3, 'Работы не завершены', true, 40),

        ('contact_type', 0, 'Почта', true, 10),
        ('contact_type', 1, 'Телефон', true, 20),
        ('contact_type', 2, 'Telegram', true, 30),

        ('urgency', 0, 'Обычное подключение', true, 10),
        ('urgency', 1, 'Срочное подключение', true, 20),

        ('criticality', 0, 'Обычная', true, 10),
        ('criticality', 1, 'Повышенная', true, 20),
        ('criticality', 2, 'Критичная', true, 30),

        ('assignment_method', 0, 'Автоматически', true, 10),
        ('assignment_method', 1, 'Выбран руководителем', true, 20),
        ('assignment_method', 2, 'Инженер Л2 назначил себя', true, 30),
        ('assignment_method', 3, 'Создано задним числом', true, 40),

        ('created_source', 0, 'Внутренний интерфейс', true, 10),
        ('created_source', 1, 'Клиентский фрейм', true, 20),
        ('created_source', 2, 'Система', true, 30),
        ('created_source', 3, 'Интеграция', true, 40),

        ('timezone_source', 0, 'Определён браузером', true, 10),
        ('timezone_source', 1, 'Выбран вручную', true, 20),
        ('timezone_source', 2, 'Сохранён в RDM', true, 30),
        ('timezone_source', 3, 'По умолчанию', true, 40),

        ('assignment_cycle_status', 0, 'Идёт распределение', true, 10),
        ('assignment_cycle_status', 1, 'Инженер назначен', true, 20),
        ('assignment_cycle_status', 2, 'Все доступные инженеры отклонили', true, 30),
        ('assignment_cycle_status', 3, 'Цикл отменён', true, 40),

        ('assignment_attempt_status', 0, 'Ожидает ответа', true, 10),
        ('assignment_attempt_status', 1, 'Подтверждено', true, 20),
        ('assignment_attempt_status', 2, 'Отклонено', true, 30),
        ('assignment_attempt_status', 3, 'Пропущено', true, 40),

        ('distribution_pool', 1, 'Специалисты Л1', true, 10),
        ('distribution_pool', 2, 'Инженеры Л2', true, 20),

        ('calendar_day_type', 0, 'Рабочий день', true, 10),
        ('calendar_day_type', 1, 'Выходной день', true, 20),
        ('calendar_day_type', 2, 'Праздничный день', true, 30),
        ('calendar_day_type', 3, 'Сокращённый рабочий день', true, 40),

        ('notification_channel', 0, 'Telegram', true, 10),
        ('notification_channel', 1, 'Битрикс24', true, 20),
        ('notification_channel', 2, 'Omnidesk', true, 30),

        ('notification_event_type', 0, 'Карточка назначена', true, 10),
        ('notification_event_type', 1, 'Напоминание инженеру Л2', true, 20),
        ('notification_event_type', 2, 'Все инженеры отклонили карточку', true, 30),
        ('notification_event_type', 3, 'Л1 должен связаться с клиентом', true, 40),
        ('notification_event_type', 4, 'Уведомление клиента перед подключением', true, 50),
        ('notification_event_type', 5, 'Коллизия срочного подключения', true, 60),

        ('notification_status', 0, 'Ожидает отправки', true, 10),
        ('notification_status', 1, 'Отправлено', true, 20),
        ('notification_status', 2, 'Ошибка', true, 30),
        ('notification_status', 3, 'Отменено', true, 40),

        ('integration_system', 0, 'Omnidesk', true, 10),
        ('integration_system', 1, 'Telegram', true, 20),
        ('integration_system', 2, 'Битрикс24', true, 30),

        ('integration_operation', 0, 'Добавить заметку в тикет', true, 10),
        ('integration_operation', 1, 'Изменить статус тикета', true, 20),
        ('integration_operation', 2, 'Изменить ответственного тикета', true, 30),
        ('integration_operation', 3, 'Отправить сообщение', true, 40),
        ('integration_operation', 4, 'Получить данные тикета', true, 50),

        ('integration_attempt_status', 0, 'Успешно', true, 10),
        ('integration_attempt_status', 1, 'Ошибка', true, 20),

        ('actor_type', 0, 'Внутренний пользователь', true, 10),
        ('actor_type', 1, 'Клиентский фрейм', true, 20),
        ('actor_type', 2, 'Система', true, 30),
        ('actor_type', 3, 'Omnidesk', true, 40),

        ('card_event_type', 0, 'Карточка создана', true, 10),
        ('card_event_type', 1, 'Статус изменён', true, 20),
        ('card_event_type', 2, 'Инженер назначен', true, 30),
        ('card_event_type', 3, 'Подключение подтверждено', true, 40),
        ('card_event_type', 4, 'Подключение отклонено', true, 50),
        ('card_event_type', 5, 'Подключение начато', true, 60),
        ('card_event_type', 6, 'Подключение завершено', true, 70),
        ('card_event_type', 7, 'Подключение отменено', true, 80),
        ('card_event_type', 8, 'Время изменено', true, 90),

        ('audit_action', 0, 'Создание', true, 10),
        ('audit_action', 1, 'Изменение', true, 20),
        ('audit_action', 2, 'Удаление', true, 30),
        ('audit_action', 3, 'Вход', true, 40),
        ('audit_action', 4, 'Выход', true, 50)
        """
    )

    op.execute(
        """
        INSERT INTO roles (id, name, description, visible) VALUES
        (1, 'Специалист Л1', 'Первая линия, согласование времени с клиентом', true),
        (2, 'Инженер Л2', 'Выполнение удалённых подключений', true),
        (3, 'Руководитель', 'Управление распределением и спорными ситуациями', true),
        (4, 'Администратор', 'Настройка системы', true)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS system_settings")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS integration_attempts")
    op.execute("DROP TABLE IF EXISTS notifications")
    op.execute("DROP TABLE IF EXISTS notification_templates")
    op.execute("DROP TABLE IF EXISTS distribution_state")
    op.execute("DROP TABLE IF EXISTS distribution_members")
    op.execute("DROP TABLE IF EXISTS production_calendar_days")
    op.execute("DROP TABLE IF EXISTS absences")
    op.execute("DROP TABLE IF EXISTS schedules")
    op.execute("DROP TABLE IF EXISTS assignment_attempts")
    op.execute("DROP TABLE IF EXISTS assignment_cycles")
    op.execute("DROP TABLE IF EXISTS card_events")
    op.execute("DROP TABLE IF EXISTS connection_cards")
    op.execute("DROP FUNCTION IF EXISTS make_connection_card_planned_range")
    op.execute("DROP FUNCTION IF EXISTS set_connection_card_number")
    op.execute("DROP TABLE IF EXISTS clients")
    op.execute("DROP TABLE IF EXISTS user_settings")
    op.execute("DROP TABLE IF EXISTS auth_sessions")
    op.execute("DROP TABLE IF EXISTS user_roles")
    op.execute("DROP TABLE IF EXISTS roles")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS dict_values")
