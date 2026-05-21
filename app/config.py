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
    if url.startswith("sqlite:///"):
        sqlite_path = url.replace("sqlite:///", "", 1)
        is_windows_absolute = len(sqlite_path) > 1 and sqlite_path[1] == ":"
        if sqlite_path and sqlite_path != ":memory:" and not (
            Path(sqlite_path).is_absolute() or is_windows_absolute
        ):
            return f"sqlite:///{(BASE_DIR / sqlite_path).resolve().as_posix()}"
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


def _session_cookie_name_for_base_dir(base_dir: str | Path) -> str:
    folder_name = Path(str(base_dir)).name.strip().lower()
    if folder_name.endswith("_runtime"):
        return "siscon_runtime_session"
    if folder_name == "controle_rpv":
        return "siscon_dev_session"

    normalized_name = "".join(ch if ch.isalnum() else "_" for ch in folder_name)
    normalized_name = "_".join(part for part in normalized_name.split("_") if part) or "siscon"
    return f"{normalized_name}_session"


def _default_session_cookie_name() -> str:
    return _session_cookie_name_for_base_dir(BASE_DIR)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.getenv("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)
    DEBUG = _env_flag("APP_DEBUG", False)
    CSRF_ENABLED = _env_flag("CSRF_ENABLED", True)
    APP_RELEASE_LABEL = os.getenv(
        "APP_RELEASE_LABEL",
        "Atualizacao Operacional Atlas | Beta interna 2026.05 | Patch 003",
    )
    APP_EXTERNAL_URL = str(os.getenv("APP_EXTERNAL_URL") or "").strip() or None
    PASSWORD_RESET_CODE_TTL_MINUTES = _env_int("PASSWORD_RESET_CODE_TTL_MINUTES", 10)
    PASSWORD_RESET_TOKEN_TTL_MINUTES = _env_int(
        "PASSWORD_RESET_TOKEN_TTL_MINUTES",
        PASSWORD_RESET_CODE_TTL_MINUTES,
    )
    PASSWORD_RESET_CODE_MAX_ATTEMPTS = _env_int("PASSWORD_RESET_CODE_MAX_ATTEMPTS", 5)
    LOGIN_THROTTLE_MAX_FAILURES = _env_int("LOGIN_THROTTLE_MAX_FAILURES", 5)
    LOGIN_THROTTLE_WINDOW_SECONDS = _env_int("LOGIN_THROTTLE_WINDOW_SECONDS", 60)
    PASSWORD_RESET_SEND_THROTTLE_MAX_ATTEMPTS = _env_int(
        "PASSWORD_RESET_SEND_THROTTLE_MAX_ATTEMPTS",
        3,
    )
    PASSWORD_RESET_SEND_THROTTLE_WINDOW_SECONDS = _env_int(
        "PASSWORD_RESET_SEND_THROTTLE_WINDOW_SECONDS",
        600,
    )
    REQUEST_THROTTLE_BACKEND = str(
        os.getenv("REQUEST_THROTTLE_BACKEND") or "sqlite"
    ).strip().lower()
    REQUEST_THROTTLE_STORAGE_PATH = str(
        os.getenv("REQUEST_THROTTLE_STORAGE_PATH") or ""
    ).strip() or None
    REQUEST_THROTTLE_GC_INTERVAL_SECONDS = _env_int(
        "REQUEST_THROTTLE_GC_INTERVAL_SECONDS",
        3600,
    )
    REQUEST_THROTTLE_GC_MAX_AGE_SECONDS = _env_int(
        "REQUEST_THROTTLE_GC_MAX_AGE_SECONDS",
        604800,
    )
    OBSERVABILITY_ENABLE_FILE_LOGGING = _env_flag("OBSERVABILITY_ENABLE_FILE_LOGGING", True)
    APP_LOG_LEVEL = str(os.getenv("APP_LOG_LEVEL") or "INFO").strip().upper()
    APP_LOG_DIR = str(os.getenv("APP_LOG_DIR") or (BASE_DIR / "instance" / "logs")).strip()
    APP_LOG_FILE = str(os.getenv("APP_LOG_FILE") or "app.log").strip()
    APP_LOG_MAX_BYTES = _env_int("APP_LOG_MAX_BYTES", 2_097_152)
    APP_LOG_BACKUP_COUNT = _env_int("APP_LOG_BACKUP_COUNT", 5)
    APP_SLOW_REQUEST_THRESHOLD_MS = _env_int("APP_SLOW_REQUEST_THRESHOLD_MS", 750)
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
    BREVO_API_URL = str(
        os.getenv("BREVO_API_URL") or "https://api.brevo.com/v3/smtp/email"
    ).strip()
    BREVO_API_KEY = str(os.getenv("BREVO_API_KEY") or "").strip() or None
    BREVO_SENDER_EMAIL = str(os.getenv("BREVO_SENDER_EMAIL") or "").strip() or None
    BREVO_SENDER_NAME = str(os.getenv("BREVO_SENDER_NAME") or "SISCON").strip()
    SMS_WEBHOOK_URL = str(os.getenv("SMS_WEBHOOK_URL") or "").strip() or None
    SMS_WEBHOOK_AUTH_TOKEN = str(os.getenv("SMS_WEBHOOK_AUTH_TOKEN") or "").strip() or None
    SMS_WEBHOOK_AUTH_TYPE = str(os.getenv("SMS_WEBHOOK_AUTH_TYPE") or "bearer").strip().lower()
    SMS_WEBHOOK_USERNAME = str(os.getenv("SMS_WEBHOOK_USERNAME") or "").strip() or None
    SMS_WEBHOOK_PASSWORD = str(os.getenv("SMS_WEBHOOK_PASSWORD") or "").strip() or None
    SMS_WEBHOOK_PAYLOAD_STYLE = str(os.getenv("SMS_WEBHOOK_PAYLOAD_STYLE") or "generic").strip().lower()
    TRUST_PROXY_HEADERS = _env_flag("TRUST_PROXY_HEADERS", False)
    SESSION_COOKIE_NAME = str(
        os.getenv("SESSION_COOKIE_NAME") or _default_session_cookie_name()
    ).strip() or _default_session_cookie_name()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_NAME = str(
        os.getenv("REMEMBER_COOKIE_NAME") or f"{SESSION_COOKIE_NAME}_remember"
    ).strip() or f"{SESSION_COOKIE_NAME}_remember"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
