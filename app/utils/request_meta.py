from __future__ import annotations

from ipaddress import ip_address

from flask import current_app, request


def _normalized_ip(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None

    try:
        return str(ip_address(candidate))
    except ValueError:
        return None


def get_request_ip() -> str | None:
    remote_addr = _normalized_ip(request.remote_addr)
    if not bool(current_app.config.get("TRUST_PROXY_HEADERS", False)):
        return remote_addr

    forwarded_for = str(request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded_for:
        forwarded_candidate = _normalized_ip(forwarded_for.split(",")[0].strip())
        if forwarded_candidate:
            return forwarded_candidate

    return remote_addr
