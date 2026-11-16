
"""Unified local and Supabase Storage service.

The application uses this service for uploaded documents and claim
attachments. Local storage remains the default for development.
Supabase Storage is accessed only from the Flask backend.
"""

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import json
import os


class StorageError(RuntimeError):
    """Raised when a storage operation fails."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class Storage:
    def __init__(self, config):
        self.backend = str(
            config.get("STORAGE_BACKEND", "local")
        ).strip().lower()

        if self.backend not in {"local", "supabase"}:
            raise ValueError(
                "STORAGE_BACKEND must be either 'local' or 'supabase'."
            )

        self.local_root = Path(
            config.get("UPLOAD_FOLDER", "instance/uploads")
        ).resolve()

        self.timeout = int(
            config.get("SUPABASE_STORAGE_TIMEOUT", 30)
        )

        self.base_url = str(
            config.get("SUPABASE_URL", "")
        ).strip().rstrip("/")

        self.service_key = str(
            config.get("SUPABASE_SERVICE_ROLE_KEY", "")
        ).strip()

        self.bucket = str(
            config.get(
                "SUPABASE_STORAGE_BUCKET",
                "warrantyvault-uploads",
            )
        ).strip()

        if self.backend == "local":
            self.local_root.mkdir(parents=True, exist_ok=True)

        if self.backend == "supabase":
            if not self.base_url or not self.service_key or not self.bucket:
                raise RuntimeError(
                    "Supabase storage requires SUPABASE_URL, "
                    "SUPABASE_SERVICE_ROLE_KEY, and "
                    "SUPABASE_STORAGE_BUCKET."
                )

            if not self.base_url.startswith("https://"):
                raise RuntimeError(
                    "SUPABASE_URL must use HTTPS."
                )

    @staticmethod
    def _validate_key(key):
        """Reject absolute paths and path traversal."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("A non-empty storage key is required.")

        if "\\" in key:
            raise ValueError("Invalid storage key.")

        path = PurePosixPath(key)

        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("Invalid storage key.")

        return path.as_posix()

    def _local_path(self, key):
        key = self._validate_key(key)
        destination = (self.local_root / Path(*PurePosixPath(key).parts)).resolve()

        if not destination.is_relative_to(self.local_root):
            raise ValueError("Storage key escapes the upload directory.")

        return destination

    def _object_url(self, key):
        key = self._validate_key(key)

        encoded_bucket = quote(self.bucket, safe="")
        encoded_key = quote(key, safe="/")

        return (
            f"{self.base_url}/storage/v1/object/"
            f"{encoded_bucket}/{encoded_key}"
        )

    def _remote_request(self, method, key, data=None, content_type=None):
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
        }

        if content_type:
            headers["Content-Type"] = content_type

        request = Request(
            self._object_url(key),
            data=data,
            headers=headers,
            method=method,
)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()

        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]

            # Supabase can report a missing object as HTTP 400
            # with a JSON body containing NoSuchKey / statusCode 404.
            missing_object = exc.code == 404

            if exc.code == 400:
                try:
                    error_data = json.loads(detail)
                    missing_object = (
                        error_data.get("code") == "NoSuchKey"
                        or str(error_data.get("statusCode")) == "404"
                    )
                except (ValueError, AttributeError):
                    pass

            if missing_object:
                raise FileNotFoundError(
                    f"Stored file not found: {key}"
                ) from exc

            raise StorageError(
                f"Supabase Storage request failed "
                f"(HTTP {exc.code}): {detail}",
                status_code=exc.code,
            ) from exc

        except (URLError, TimeoutError, OSError) as exc:
            raise StorageError(
                f"Could not communicate with Supabase Storage: {exc}"
            ) from exc

    def save(self, key, data, content_type=None):
        """Save bytes under the supplied storage key."""
        key = self._validate_key(key)

        if not isinstance(data, bytes):
            raise TypeError("Storage.save expects bytes.")

        if self.backend == "local":
            destination = self._local_path(key)
            destination.parent.mkdir(parents=True, exist_ok=True)

            temporary_path = None

            try:
                with NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=".upload-",
                    delete=False,
                ) as temporary:
                    temporary.write(data)
                    temporary_path = Path(temporary.name)

                os.replace(temporary_path, destination)

            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

            return key

        self._remote_request(
            "POST",
            key,
            data=data,
            content_type=content_type or "application/octet-stream",
        )

        return key

    def read(self, key):
        """Return the complete contents of a stored object."""
        key = self._validate_key(key)

        if self.backend == "local":
            path = self._local_path(key)

            if not path.is_file():
                raise FileNotFoundError(
                    f"Stored file not found: {key}"
                )

            return path.read_bytes()

        _, data = self._remote_request("GET", key)
        return data

    def exists(self, key):
        """Check whether an object exists."""
        key = self._validate_key(key)

        if self.backend == "local":
            return self._local_path(key).is_file()

        try:
            self._remote_request("HEAD", key)
            return True

        except FileNotFoundError:
            return False

        except StorageError:
            raise

    def delete(self, key):
        """Delete an object. Missing objects are treated as already deleted."""
        key = self._validate_key(key)

        if self.backend == "local":
            path = self._local_path(key)
            existed = path.is_file()
            path.unlink(missing_ok=True)
            return existed

        url = (
            f"{self.base_url}/storage/v1/object/"
            f"{quote(self.bucket, safe='')}"
        )
        payload = json.dumps({"prefixes": [key]}).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
            },
            method="DELETE",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status in (200, 204)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise StorageError(
                f"Supabase Storage request failed "
                f"(HTTP {exc.code}): {detail}",
                status_code=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise StorageError(
                f"Could not communicate with Supabase Storage: {exc}"
            ) from exc

    @contextmanager
    def temporary_file(self, key, suffix=None):
        """Provide a local path for OCR or email processing.

        Local storage yields the existing file directly.
        Supabase storage downloads to a temporary file and removes it
        automatically when the context exits.
        """
        key = self._validate_key(key)

        if self.backend == "local":
            path = self._local_path(key)

            if not path.is_file():
                raise FileNotFoundError(
                    f"Stored file not found: {key}"
                )

            yield path
            return

        data = self.read(key)

        temporary_path = None

        try:
            with NamedTemporaryFile(
                mode="wb",
                suffix=suffix or Path(key).suffix,
                delete=False,
            ) as temporary:
                temporary.write(data)
                temporary_path = Path(temporary.name)

            yield temporary_path

        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def get_storage():
    """Return a storage service using the active Flask configuration."""
    from flask import current_app

    return Storage(current_app.config)
