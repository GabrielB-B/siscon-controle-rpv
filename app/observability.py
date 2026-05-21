from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from flask import Flask, g, request

from app.utils.request_meta import get_request_ip


def init_observability(app: Flask) -> None:
    _configure_file_logging(app)

    @app.before_request
    def _capture_request_context():
        g.request_started_at = perf_counter()
        g.request_id = (
            str(request.headers.get("X-Request-ID") or "").strip()
            or uuid4().hex[:12]
        )

    @app.after_request
    def _annotate_response(response):
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers.setdefault("X-Request-ID", request_id)

        started_at = getattr(g, "request_started_at", None)
        if started_at is None:
            return response

        duration_ms = (perf_counter() - started_at) * 1000
        threshold_ms = float(app.config.get("APP_SLOW_REQUEST_THRESHOLD_MS", 750) or 750)
        should_log = response.status_code >= 500 or duration_ms >= threshold_ms
        if should_log and not request.path.startswith("/health"):
            level = logging.ERROR if response.status_code >= 500 else logging.WARNING
            app.logger.log(
                level,
                "request_completed method=%s path=%s endpoint=%s status=%s duration_ms=%.2f request_id=%s remote_addr=%s",
                request.method,
                request.path,
                request.endpoint or "-",
                response.status_code,
                duration_ms,
                request_id,
                get_request_ip() or "-",
            )

        return response


def _configure_file_logging(app: Flask) -> None:
    if not bool(app.config.get("OBSERVABILITY_ENABLE_FILE_LOGGING", True)):
        return

    log_dir = Path(
        str(app.config.get("APP_LOG_DIR") or (Path(app.instance_path) / "logs"))
    ).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / str(app.config.get("APP_LOG_FILE") or "app.log")
    max_bytes = int(app.config.get("APP_LOG_MAX_BYTES", 2_097_152) or 2_097_152)
    backup_count = int(app.config.get("APP_LOG_BACKUP_COUNT", 5) or 5)
    log_level = str(app.config.get("APP_LOG_LEVEL") or "INFO").strip().upper()

    for handler in app.logger.handlers:
        if getattr(handler, "_siscon_file_log", False) and getattr(handler, "baseFilename", None) == str(log_path):
            handler.setLevel(log_level)
            app.logger.setLevel(log_level)
            return

    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler._siscon_file_log = True  # type: ignore[attr-defined]
    handler.setLevel(log_level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    app.logger.addHandler(handler)
    app.logger.setLevel(log_level)
