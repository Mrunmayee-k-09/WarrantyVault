
"""Additive schema upgrades and lightweight migration tracking."""

from sqlalchemy import inspect, text
from .extensions import db


SCHEMA_VERSIONS = (
    "001_additive_compatibility_columns",
    "002_unique_notification_event_key",
)


def ensure_schema_compatibility():
    """Add missing compatibility columns and record the schema baseline."""

    dialect = db.engine.dialect.name

    # Use SQL types and defaults supported by the active database.
    if dialect == "postgresql":
        boolean_type = "BOOLEAN NOT NULL DEFAULT FALSE"
        datetime_type = "TIMESTAMP"
        migration_datetime_type = "TIMESTAMP NOT NULL"
    else:
        boolean_type = "BOOLEAN NOT NULL DEFAULT 0"
        datetime_type = "DATETIME"
        migration_datetime_type = "DATETIME NOT NULL"

    additive_columns = {
        "user": {
            "reminder_email_enabled": boolean_type,
            "reminder_email": "VARCHAR(180)",
        },
        "claim": {
            "manufacturer_name": "VARCHAR(180)",
            "manufacturer_email": "VARCHAR(180)",
            "submission_channel": "VARCHAR(30)",
            "submitted_at": datetime_type,
            "submission_message_id": "VARCHAR(255)",
            "submission_error": "TEXT",
        },
        "notification": {
            "event_key": "VARCHAR(255)",
            "email_sent_at": datetime_type,
            "email_error": "TEXT",
        },
    }

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    with db.engine.begin() as connection:

        # 1. Add missing compatibility columns.
        for table_name, columns in additive_columns.items():
            if table_name not in existing_tables:
                continue

            existing_columns = {
                column["name"]
                for column in inspect(connection).get_columns(table_name)
            }

            for column_name, sql_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {sql_type}"
                        )
                    )

        # 2. Prevent duplicate reminder events for the same user.
        notification_indexes = inspect(connection).get_indexes("notification")
        notification_constraints = inspect(
            connection
        ).get_unique_constraints("notification")

        has_event_uniqueness = (
            any(
                index.get("unique")
                and index.get("column_names") == ["user_id", "event_key"]
                for index in notification_indexes
            )
            or any(
                constraint.get("column_names") == ["user_id", "event_key"]
                for constraint in notification_constraints
            )
        )

        if not has_event_uniqueness:
            duplicates = connection.execute(
                text("""
                    SELECT user_id, event_key, COUNT(*) AS occurrences
                    FROM notification
                    WHERE event_key IS NOT NULL
                    GROUP BY user_id, event_key
                    HAVING COUNT(*) > 1
                    LIMIT 5
                """)
            ).fetchall()

            if duplicates:
                raise RuntimeError(
                    "Cannot create the notification event uniqueness index. "
                    "Duplicate (user_id, event_key) values exist: "
                    f"{duplicates}"
                )

            connection.execute(text("""
                CREATE UNIQUE INDEX uq_notification_user_event
                ON notification (user_id, event_key)
            """))

        # 3. Create the migration history table if necessary.
        connection.execute(
            text(f"""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(50) PRIMARY KEY,
                    applied_at {migration_datetime_type}
                )
            """)
        )

        # 4. Record each schema version exactly once.
        for version in SCHEMA_VERSIONS:
            already_recorded = connection.execute(
                text("""
                    SELECT version
                    FROM schema_migrations
                    WHERE version = :version
                """),
                {"version": version},
            ).first()

            if already_recorded is None:
                connection.execute(
                    text("""
                        INSERT INTO schema_migrations (version, applied_at)
                        VALUES (:version, CURRENT_TIMESTAMP)
                    """),
                    {"version": version},
                )
