import os
import hashlib
import mimetypes
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.services.storage import Storage


# Load project environment variables.
load_dotenv()

BACKUP_DB = Path(
    "instance/backups/warrantyvault_pre_supabase_20261116.db"
)

BACKUP_UPLOADS = Path(
    "instance/backups/uploads_pre_supabase"
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    print("=" * 60)
    print("WARRANTYVAULT -> SUPABASE STORAGE MIGRATION")
    print("=" * 60)

    # Validate backup files before connecting to Supabase.
    if not BACKUP_DB.is_file():
        raise FileNotFoundError(f"Backup database missing: {BACKUP_DB}")

    if not BACKUP_UPLOADS.is_dir():
        raise FileNotFoundError(f"Backup uploads folder missing: {BACKUP_UPLOADS}")

    # Read exact storage keys from the database backup.
    connection = sqlite3.connect(BACKUP_DB)

    document_keys = [
        row[0]
        for row in connection.execute(
            "SELECT stored_name FROM document ORDER BY id"
        )
    ]

    claim_keys = [
        row[0]
        for row in connection.execute(
            """
            SELECT attachment_path
            FROM claim
            WHERE attachment_path IS NOT NULL
            ORDER BY id
            """
        )
    ]

    connection.close()

    keys = document_keys + claim_keys

    if len(keys) != 6 or len(set(keys)) != 6:
        raise RuntimeError(
            f"Expected six unique storage keys, found {len(keys)}."
        )

    # Validate every local file before uploading anything.
    files = {}

    print("\nCHECKING SOURCE FILES...")

    for key in keys:
        source = (BACKUP_UPLOADS / key).resolve()

        if source.parent != BACKUP_UPLOADS.resolve():
            raise RuntimeError(f"Invalid storage key: {key}")

        if not source.is_file():
            raise FileNotFoundError(f"Backup file missing: {key}")

        data = source.read_bytes()

        files[key] = {
            "data": data,
            "hash": sha256(data),
            "size": len(data),
            "content_type": (
                mimetypes.guess_type(key)[0]
                or "application/octet-stream"
            ),
        }

        print(f"  VERIFIED: {key} ({len(data)} bytes)")

    # Use Supabase explicitly without changing the application's .env setting.
    storage_config = {
        "STORAGE_BACKEND": "supabase",
        "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),
        "SUPABASE_SERVICE_ROLE_KEY": os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY", ""
),
        "SUPABASE_STORAGE_BUCKET": os.getenv(
    "SUPABASE_STORAGE_BUCKET", "warrantyvault-uploads"
),
        "SUPABASE_STORAGE_TIMEOUT": 30,
    }

    storage = Storage(storage_config)

    print("\nUPLOADING AND VERIFYING FILES...")

    uploaded = 0
    already_present = 0

    for key, details in files.items():
        source_data = details["data"]
        source_hash = details["hash"]

        # Check whether the exact key already exists.
        try:
            existing_data = storage.read(key)

        except FileNotFoundError:
            existing_data = None

        if existing_data is not None:
            if sha256(existing_data) != source_hash:
                raise RuntimeError(
                    f"Existing Supabase object differs from backup: {key}. "
                    "Nothing will be overwritten."
                )

            print(f"  ALREADY VERIFIED: {key}")
            already_present += 1
            continue

        # Upload using the original database storage key.
        storage.save(
            key,
            source_data,
            content_type=details["content_type"],
        )

        # Download and compare the actual uploaded bytes.
        downloaded = storage.read(key)

        if sha256(downloaded) != source_hash:
            raise RuntimeError(
                f"SHA-256 verification failed after upload: {key}"
            )

        if len(downloaded) != details["size"]:
            raise RuntimeError(
                f"File size verification failed: {key}"
            )

        print(f"  UPLOADED AND VERIFIED: {key}")
        uploaded += 1

    print("\n" + "=" * 60)
    print("STORAGE MIGRATION COMPLETED")
    print("=" * 60)
    print(f"New uploads: {uploaded}")
    print(f"Already verified: {already_present}")
    print(f"Total verified: {uploaded + already_present}")
    print("SHA-256 verification: PASSED")
    print("Original backup files: UNTOUCHED")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nMIGRATION STOPPED:")
        print(f"{type(error).__name__}: {error}")
        sys.exit(1)
