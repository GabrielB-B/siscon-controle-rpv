import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


BASE_DIR = Path(__file__).resolve().parents[1]


def _default_sqlite_uri() -> str:
    caminho_banco = BASE_DIR / "instance" / "controle_rpv.db"
    return f"sqlite:///{caminho_banco.as_posix()}"


def _normalize_database_url(database_url: str | None) -> str:
    url = str(database_url or "").strip()
    if not url:
        return _default_sqlite_uri()
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)
    return url


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _engine_options(database_url: str) -> dict:
    if database_url.startswith("mysql"):
        return {
            "pool_pre_ping": True,
            "pool_recycle": 280,
        }
    return {}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.getenv("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)
    DEBUG = _env_flag("APP_DEBUG", False)
    CSRF_ENABLED = _env_flag("CSRF_ENABLED", True)
    APP_RELEASE_LABEL = os.getenv(
        "APP_RELEASE_LABEL",
        "Atualizacao Operacional Atlas | Beta interna 2026.03 | Patch 002",
    )
    APP_EXTERNAL_URL = str(os.getenv("APP_EXTERNAL_URL") or "").strip() or None
    PASSWORD_RESET_CODE_TTL_MINUTES = _env_int("PASSWORD_RESET_CODE_TTL_MINUTES", 10)
    PASSWORD_RESET_TOKEN_TTL_MINUTES = _env_int(
        "PASSWORD_RESET_TOKEN_TTL_MINUTES",
        PASSWORD_RESET_CODE_TTL_MINUTES,
    )
    PASSWORD_RESET_CODE_MAX_ATTEMPTS = _env_int("PASSWORD_RESET_CODE_MAX_ATTEMPTS", 5)
    NOTIFICATION_DELIVERY_MODE = str(
        os.getenv("NOTIFICATION_DELIVERY_MODE") or "file"
    ).strip().lower()
    NOTIFICATION_OUTBOX_DIR = str(
        os.getenv("NOTIFICATION_OUTBOX_DIR")
        or (BASE_DIR / "instance" / "notifications")
    )
    EMAIL_FROM_ADDRESS = str(
        os.getenv("EMAIL_FROM_ADDRESS") or "nao-responda@siscon.local"
    ).strip()
    SMTP_HOST = str(os.getenv("SMTP_HOST") or "").strip() or None
    SMTP_PORT = _env_int("SMTP_PORT", 587)
    SMTP_USERNAME = str(os.getenv("SMTP_USERNAME") or "").strip() or None
    SMTP_PASSWORD = str(os.getenv("SMTP_PASSWORD") or "").strip() or None
    SMTP_USE_TLS = _env_flag("SMTP_USE_TLS", True)
    SMS_WEBHOOK_URL = str(os.getenv("SMS_WEBHOOK_URL") or "").strip() or None
    SMS_WEBHOOK_AUTH_TOKEN = str(os.getenv("SMS_WEBHOOK_AUTH_TOKEN") or "").strip() or None
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
