import secrets
from pathlib import Path

from flask import abort, current_app, request, session
from markupsafe import Markup, escape


CSRF_SESSION_KEY = "_csrf_token"
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _secret_key_file(app) -> Path:
    return Path(app.instance_path) / ".secret_key"


def _ensure_secret_key(app):
    configured_secret = str(app.config.get("SECRET_KEY") or "").strip()
    if configured_secret and configured_secret != "dev-secret-key":
        return

    secret_file = _secret_key_file(app)
    if secret_file.exists():
        secret_key = secret_file.read_text(encoding="utf-8").strip()
        if secret_key:
            app.config["SECRET_KEY"] = secret_key
            return

    secret_key = secrets.token_urlsafe(48)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret_key, encoding="utf-8")
    app.config["SECRET_KEY"] = secret_key


def _csrf_enabled() -> bool:
    return bool(current_app.config.get("CSRF_ENABLED", True))


def _csrf_token_value() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_token() -> str:
    return _csrf_token_value()


def csrf_input() -> Markup:
    token = escape(_csrf_token_value())
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


def _validar_csrf():
    if not _csrf_enabled():
        return

    if request.method in SAFE_HTTP_METHODS:
        return

    if request.endpoint == "static":
        return

    expected = str(session.get(CSRF_SESSION_KEY) or "")
    provided = str(
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or ""
    )

    if not expected or not provided or not secrets.compare_digest(expected, provided):
        abort(400, description="Token CSRF invalido ou ausente.")


def init_security(app):
    _ensure_secret_key(app)
    app.config.setdefault("CSRF_ENABLED", not app.config.get("TESTING", False))
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["csrf_input"] = csrf_input

    @app.before_request
    def enforce_csrf():
        _validar_csrf()

    @app.context_processor
    def inject_security_helpers():
        return {
            "csrf_token": csrf_token,
            "csrf_input": csrf_input,
        }
