from __future__ import annotations

from flask import Blueprint, abort, jsonify
from flask_login import current_user, login_required

from app.services.operational_health_service import (
    collect_health_snapshot,
    collect_operational_audit_report,
)


observability_bp = Blueprint("observability", __name__)


@observability_bp.route("/health", methods=["GET"])
def health():
    payload = collect_health_snapshot()
    status_code = 200 if payload["status"] in {"ok", "info"} else 503
    return jsonify(payload), status_code


@observability_bp.route("/health/operational", methods=["GET"])
@login_required
def operational_health():
    if not getattr(current_user, "is_admin", False):
        abort(403)

    payload = collect_operational_audit_report()
    status_code = 200 if payload["status"] in {"ok", "info"} else 409
    return jsonify(payload), status_code

