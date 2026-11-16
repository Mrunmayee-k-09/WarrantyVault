
"""Application configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")



class Config:
    # Environment
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    IS_PRODUCTION = APP_ENV == "production"

    # Security
    SECRET_KEY = os.getenv("SECRET_KEY")

    if not SECRET_KEY:
        if IS_PRODUCTION:
            raise RuntimeError("SECRET_KEY must be set in production.")
        SECRET_KEY = "local-development-only-change-before-deployment"

    SESSION_COOKIE_SECURE = IS_PRODUCTION or os.getenv(
        "SESSION_COOKIE_SECURE", "false"
    ).lower() in {"1", "true", "yes"}

    REMEMBER_COOKIE_SECURE = IS_PRODUCTION or os.getenv(
        "REMEMBER_COOKIE_SECURE", "false"
    ).lower() in {"1", "true", "yes"}

    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    SQLALCHEMY_DATABASE_URI = (
        DATABASE_URL
        if DATABASE_URL
        else f"sqlite:///{BASE_DIR / 'instance' / 'warrantyvault.db'}"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False


    # Uploads
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    UPLOAD_FOLDER = os.getenv(
        "UPLOAD_FOLDER",
        str(BASE_DIR / "instance" / "uploads")
    )

    # File storage backend: local or supabase
    STORAGE_BACKEND = os.getenv(
        "STORAGE_BACKEND", "local"
    ).strip().lower()

    # Supabase Storage configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY", ""
    ).strip()
    SUPABASE_STORAGE_BUCKET = os.getenv(
        "SUPABASE_STORAGE_BUCKET", "warrantyvault-uploads"
    ).strip()
    SUPABASE_STORAGE_TIMEOUT = int(
        os.getenv("SUPABASE_STORAGE_TIMEOUT", "30")
    )

    ALLOWED_EXTENSIONS = {
        "pdf", "png", "jpg", "jpeg",
        "tif", "tiff", "txt", "docx"
    }

    # Email
    CLAIM_SUBMISSION_MODE = os.getenv(
        "CLAIM_SUBMISSION_MODE", "smtp"
    ).lower()

    CLAIM_SIMULATION_ALLOWED = os.getenv(
        "CLAIM_SIMULATION_ALLOWED", "false"
    ).lower() in {"1", "true", "yes"}

    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv(
        "MAIL_USE_TLS", "true"
    ).lower() in {"1", "true", "yes"}
    MAIL_USE_SSL = os.getenv(
        "MAIL_USE_SSL", "false"
    ).lower() in {"1", "true", "yes"}
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = (
        os.getenv("MAIL_DEFAULT_SENDER")
        or os.getenv("MAIL_USERNAME", "")
    )
    MAIL_TIMEOUT = int(os.getenv("MAIL_TIMEOUT", "20"))

    # Scheduler
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
    REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "9"))
    REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "0"))

    # OCR
    TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")
