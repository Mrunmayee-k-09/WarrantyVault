
"""Full SQLite database and uploaded-file backup/restore utilities."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from sqlalchemy.engine import make_url

from ..extensions import db


FORMAT_VERSION = 1


def _database_path(app):
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])

    if not url.drivername.startswith("sqlite"):
        raise RuntimeError("Full backup currently supports SQLite databases only.")

    if not url.database or url.database == ":memory:":
        raise RuntimeError("A file-based SQLite database is required.")

    return Path(url.database).resolve()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def _sqlite_backup(source_path, destination_path):
    """Create a consistent SQLite backup and close both connections."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    source = None
    destination = None

    try:
        source = sqlite3.connect(str(source_path))
        destination = sqlite3.connect(str(destination_path))

        source.backup(destination)

        result = destination.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        if not result or result[0] != "ok":
            raise RuntimeError(
                "SQLite backup failed its integrity check."
            )

    finally:
        if destination is not None:
            destination.close()

        if source is not None:
            source.close()


def create_backup(app, destination=None):
    """Create a ZIP backup containing the database, uploads and manifest."""
    database_path = _database_path(app)
    upload_dir = Path(app.config["UPLOAD_FOLDER"]).resolve()

    if not database_path.is_file():
        raise FileNotFoundError(f"Database not found: {database_path}")

    if destination is None:
        backup_dir = Path(app.instance_path) / "backups"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        destination = backup_dir / f"warrantyvault_{timestamp}.zip"

    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination == database_path or upload_dir in destination.parents:
        raise ValueError("Backup destination cannot be inside the upload folder.")

    db.session.remove()
    db.engine.dispose()

    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        staged_db = staging / "database.sqlite3"
        staged_uploads = staging / "uploads"
        staged_uploads.mkdir()

        _sqlite_backup(database_path, staged_db)

        if upload_dir.exists():
            shutil.copytree(upload_dir, staged_uploads, dirs_exist_ok=True)

        files = {"database.sqlite3": _sha256(staged_db)}

        for path in sorted(staged_uploads.rglob("*")):
            if path.is_symlink():
                raise ValueError("Symbolic links are not allowed in uploads.")
            if path.is_file():
                relative = path.relative_to(staging).as_posix()
                files[relative] = _sha256(path)

        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }

        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        temporary_archive = destination.with_name(
            destination.name + f".{uuid4().hex}.tmp"
        )

        try:
            with zipfile.ZipFile(
                temporary_archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(staged_db, "database.sqlite3")
                archive.write(manifest_path, "manifest.json")

                for path in staged_uploads.rglob("*"):
                    if path.is_file():
                        archive.write(
                            path,
                            path.relative_to(staging).as_posix(),
                        )

            os.replace(temporary_archive, destination)
        finally:
            temporary_archive.unlink(missing_ok=True)

    return destination


def _validate_archive(archive_path, staging):
    """Validate archive paths, contents, checksums and SQLite integrity."""
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()

        if len(names) != len(set(names)):
            raise ValueError("Backup contains duplicate archive paths.")

        for name in names:
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] not in {"database.sqlite3", "uploads", "manifest.json"}
            ):
                raise ValueError("Backup contains an unsafe archive path.")

        if "database.sqlite3" not in names or "manifest.json" not in names:
            raise ValueError("Backup is missing required files.")

        for name in names:
            if name.endswith("/"):
                continue
            target = staging.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    manifest = json.loads(
        (staging / "manifest.json").read_text(encoding="utf-8")
    )

    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError("Unsupported backup format version.")

    expected = manifest.get("files")
    if not isinstance(expected, dict) or "database.sqlite3" not in expected:
        raise ValueError("Backup manifest is invalid.")

    actual_names = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }

    if actual_names != set(expected):
        raise ValueError("Backup contents do not match the manifest.")

    for name, checksum in expected.items():
        path = staging.joinpath(*PurePosixPath(name).parts)
        if not path.is_file() or _sha256(path) != checksum:
            raise ValueError(f"Backup integrity check failed: {name}")

    database = staging / "database.sqlite3"
    connection = sqlite3.connect(str(database))
    try:
        result = connection.execute(
        "PRAGMA integrity_check"
    ).fetchone()
    finally:
        connection.close()

    if not result or result[0] != "ok":
        raise ValueError("Backup database failed its SQLite integrity check.")

    return database, staging / "uploads"


def restore_backup(app, archive_path):
    """Restore a validated backup after preserving the current installation."""
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"Backup not found: {archive_path}")

    database_path = _database_path(app)
    upload_dir = Path(app.config["UPLOAD_FOLDER"]).resolve()

    if archive_path == database_path or upload_dir in archive_path.parents:
        raise ValueError("Backup archive cannot be inside the upload folder.")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    upload_dir.parent.mkdir(parents=True, exist_ok=True)

    db.session.remove()
    db.engine.dispose()

    with tempfile.TemporaryDirectory(dir=database_path.parent) as temporary:
        staging = Path(temporary)
        restored_db, restored_uploads = _validate_archive(
            archive_path, staging
        )

        staged_db = database_path.parent / f".{uuid4().hex}.restore"
        staged_uploads = upload_dir.parent / f".{uuid4().hex}.restore"
        old_uploads = upload_dir.parent / f".{uuid4().hex}.previous"

        shutil.copy2(restored_db, staged_db)
        shutil.copytree(restored_uploads, staged_uploads)

        # Preserve the current installation before replacing anything.
        safety_backup = create_backup(
            app,
            Path(app.instance_path) / "backups" /
            f"pre_restore_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.zip",
        )

        try:
            if upload_dir.exists():
                os.replace(upload_dir, old_uploads)

            os.replace(staged_uploads, upload_dir)

            # Replace the database last. This is an atomic file replacement.
            os.replace(staged_db, database_path)

        except Exception:
            if upload_dir.exists() and old_uploads.exists():
                shutil.rmtree(upload_dir)
                os.replace(old_uploads, upload_dir)
            elif old_uploads.exists():
                os.replace(old_uploads, upload_dir)
            raise

        finally:
            staged_db.unlink(missing_ok=True)
            if staged_uploads.exists():
                shutil.rmtree(staged_uploads, ignore_errors=True)

        if old_uploads.exists():
            shutil.rmtree(old_uploads, ignore_errors=True)

    return safety_backup
