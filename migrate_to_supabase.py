
import json
import os
import sqlite3
import sys

from datetime import date, datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Json


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
SOURCE_DB = BASE_DIR / "instance" / "backups" / "warrantyvault_pre_supabase_20261116.db"

TABLES = [
    "user",
    "product",
    "document",
    "verification",
    "verification_field",
    "claim",
    "notification",
    "audit_log",
]

BOOLEAN_COLUMNS = {
    "reminder_email_enabled",
    "is_read",
    "review_required",
}

JSON_COLUMNS = {
    "audit_log": {"details"},
    "document": {"extracted_data"},
    "verification": {"notes"},
}

DATE_COLUMNS = {
    "purchase_date",
    "warranty_start",
    "warranty_expiry",
}

DATETIME_COLUMNS = {
    "created_at",
    "updated_at",
    "uploaded_at",
    "submitted_at",
    "email_sent_at",
}


# --------------------------------------------------
# VALUE CONVERSION
# --------------------------------------------------

def convert_value(table, column, value):
    if value is None:
        return None

    if column in BOOLEAN_COLUMNS:
        return bool(value)

    if column in JSON_COLUMNS.get(table, set()):
        if isinstance(value, str):
            value = json.loads(value)
        return Json(value)

    if column in DATE_COLUMNS:
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    if column in DATETIME_COLUMNS:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    return value


# --------------------------------------------------
# MIGRATION
# --------------------------------------------------

def main():
    load_dotenv(BASE_DIR / ".env", override=True)

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing from .env")

    if not SOURCE_DB.is_file():
        raise FileNotFoundError(f"Source backup not found: {SOURCE_DB}")

    print("=" * 55)
    print("WARRANTYVAULT SQLITE -> SUPABASE MIGRATION")
    print("=" * 55)

    source = sqlite3.connect(SOURCE_DB)
    source.row_factory = sqlite3.Row

    source_counts = {}
    source_rows = {}

    try:
        # Read and validate all source records first.
        for table in TABLES:
            rows = source.execute(
                f'SELECT * FROM "{table}" ORDER BY id'
            ).fetchall()

            source_rows[table] = rows
            source_counts[table] = len(rows)

        print("\nSOURCE RECORD COUNTS:")
        for table in TABLES:
            print(f"  {table}: {source_counts[table]}")

        print(
            f"\nTOTAL APPLICATION RECORDS: "
            f"{sum(source_counts.values())}"
        )

        with psycopg.connect(
            database_url,
            connect_timeout=15
        ) as destination:

            with destination.transaction():

                # Refuse to merge with existing application records.
                print("\nCHECKING DESTINATION...")

                for table in TABLES:
                    count = destination.execute(
                        sql.SQL("SELECT COUNT(*) FROM public.{}").format(
                            sql.Identifier(table)
                        )
                    ).fetchone()[0]

                    if count != 0:
                        raise RuntimeError(
                            f"Destination table {table} is not empty "
                            f"({count} records). Migration aborted."
                        )

                # Verify exact column compatibility.
                print("CHECKING COLUMN NAME COMPATIBILITY...")

                for table in TABLES:
                    source_columns = [
                        row[1]
                        for row in source.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                    ]

                    destination_columns = [
                        row[0]
                        for row in destination.execute(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = %s
                            ORDER BY ordinal_position
                            """,
                            (table,),
                        ).fetchall()
                    ]

                    if set(source_columns) != set(destination_columns):
                        raise RuntimeError(
                            f"Column mismatch in {table}.\n"
                            f"Source: {source_columns}\n"
                            f"Destination: {destination_columns}"
                        )

                print("COLUMN COMPATIBILITY PASSED.")

                # Insert records in foreign-key order.
                print("\nTRANSFERRING RECORDS...")

                for table in TABLES:
                    rows = source_rows[table]

                    if not rows:
                        continue

                    columns = list(rows[0].keys())

                    insert_query = sql.SQL(
                        "INSERT INTO public.{} ({}) VALUES ({})"
                    ).format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(
                            sql.Identifier(column)
                            for column in columns
                        ),
                        sql.SQL(", ").join(
                            sql.Placeholder()
                            for _ in columns
                        ),
                    )

                    values = [
                        tuple(
                            convert_value(table, column, row[column])
                            for column in columns
                        )
                        for row in rows
                    ]

                    with destination.cursor() as cursor:
                        cursor.executemany(
                        insert_query,
                        values
                    )

                    print(f"  {table}: {len(rows)} records inserted")

                # Synchronize ID sequences.
                print("\nSYNCHRONIZING ID SEQUENCES...")

                for table in TABLES:
                    relation = f'public."{table}"'

                    sequence = destination.execute(
                        "SELECT pg_get_serial_sequence(%s, 'id')",
                        (relation,),
                    ).fetchone()[0]

                    if sequence:
                        destination.execute(
                            sql.SQL(
                                """
                                SELECT setval(
                                    %s::regclass,
                                    GREATEST(COALESCE(MAX(id), 1), 1),
                                    COUNT(*) > 0
                                )
                                FROM public.{}
                                """
                            ).format(sql.Identifier(table)),
                            (sequence,),
                        )

                # Verify every table before transaction commit.
                print("\nVERIFYING RECORD COUNTS...")

                for table in TABLES:
                    actual = destination.execute(
                        sql.SQL("SELECT COUNT(*) FROM public.{}").format(
                            sql.Identifier(table)
                        )
                    ).fetchone()[0]

                    expected = source_counts[table]

                    print(
                        f"  {table}: expected={expected}, actual={actual}"
                    )

                    if actual != expected:
                        raise RuntimeError(
                            f"Record count mismatch in {table}"
                        )

                print("\nALL CHECKS PASSED.")
                print("PostgreSQL transaction will now commit.")

        print("\n" + "=" * 55)
        print("MIGRATION COMPLETED SUCCESSFULLY!")
        print("=" * 55)

    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nMIGRATION FAILED:", error)
        print("Any uncommitted PostgreSQL changes were rolled back.")
        sys.exit(1)
