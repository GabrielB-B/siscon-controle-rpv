import os
from pathlib import Path

from werkzeug.serving import run_simple


if not os.getenv("SESSION_COOKIE_SECURE"):
    os.environ["SESSION_COOKIE_SECURE"] = "1"

from app import create_app


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_path(name: str, default: str) -> Path:
    value = str(os.getenv(name, default)).strip() or default
    return Path(value).resolve()


app = create_app()
app.config["PREFERRED_URL_SCHEME"] = "https"


if __name__ == "__main__":
    host = str(os.getenv("APP_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = _env_int("APP_PORT", 8445)
    cert_file = _env_path("HTTPS_CERT_FILE", "instance/certs/controle_rpv_local.crt")
    key_file = _env_path("HTTPS_KEY_FILE", "instance/certs/controle_rpv_local.key")

    if not cert_file.exists() or not key_file.exists():
        missing = cert_file if not cert_file.exists() else key_file
        raise FileNotFoundError(f"Arquivo HTTPS nao encontrado: {missing}")

    print(f"Servidor HTTPS ativo em https://{host}:{port}")
    print(f"Certificado do servidor: {cert_file}")
    print("Use Ctrl+C para encerrar.")

    run_simple(
        hostname=host,
        port=port,
        application=app,
        ssl_context=(str(cert_file), str(key_file)),
        threaded=True,
        use_reloader=False,
        use_debugger=False,
    )
