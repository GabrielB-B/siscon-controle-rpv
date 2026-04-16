import os

from waitress import serve

from app import create_app


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


app = create_app()


if __name__ == "__main__":
    host = str(os.getenv("APP_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = _env_int("APP_PORT", 8080)
    threads = _env_int("APP_THREADS", 8)

    print(f"Servidor local ativo em http://{host}:{port}")
    print("Use Ctrl+C para encerrar.")
    serve(app, host=host, port=port, threads=threads)
