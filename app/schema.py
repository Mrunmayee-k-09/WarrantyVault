"""Small additive schema upgrader for installations created by earlier ZIP builds.

This project intentionally avoids requiring Alembic for the local prototype.
Only newly introduced nullable/defaulted columns are added; existing records are kept.
"""
from sqlalchemy import inspect, text
from .extensions import db

ADDITIVE_COLUMNS = {
    "user": {
        "reminder_email_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "reminder_email": "VARCHAR(180)",
    },
    "claim": {
        "manufacturer_name": "VARCHAR(180)",
        "manufacturer_email": "VARCHAR(180)",
        "submission_channel": "VARCHAR(30)",
        "submitted_at": "DATETIME",
        "submission_message_id": "VARCHAR(255)",
        "submission_error": "TEXT",
    },
    "notification": {
        "event_key": "VARCHAR(255)",
        "email_sent_at": "DATETIME",
        "email_error": "TEXT",
    },
}


def ensure_schema_compatibility():
    """Add any new columns to an existing SQLite or MySQL database."""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    with db.engine.begin() as connection:
        for table_name, columns in ADDITIVE_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
            for column_name, sql_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}"
                    ))
