"""Application configuration."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    CLAIM_SUBMISSION_MODE = os.getenv("CLAIM_SUBMISSION_MODE", "smtp").lower()
    CLAIM_SIMULATION_ALLOWED = os.getenv(
    "CLAIM_SIMULATION_ALLOWED", "false"
    ).lower() in {"1", "true", "yes"}
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-this-secret-key")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'warrantyvault.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "instance" / "uploads"))
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "txt", "docx"}
    # SMTP is optional. Email actions remain disabled until all required values exist.
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() in {"1", "true", "yes"}
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() in {"1", "true", "yes"}
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER") or os.getenv("MAIL_USERNAME", "")
    MAIL_TIMEOUT = int(os.getenv("MAIL_TIMEOUT", "20"))
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
    REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "9"))
    REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "0"))
    TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
