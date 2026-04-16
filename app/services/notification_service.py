import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import current_app

from app.utils.datetime_utils import utc_now_naive


class NotificationDeliveryError(RuntimeError):
    pass


def _delivery_mode() -> str:
    return str(current_app.config.get("NOTIFICATION_DELIVERY_MODE", "file") or "file").strip().lower()


def _outbox_dir() -> Path:
    outbox_dir = Path(
        current_app.config.get(
            "NOTIFICATION_OUTBOX_DIR",
            Path(current_app.instance_path) / "notifications",
        )
    )
    outbox_dir.mkdir(parents=True, exist_ok=True)
    return outbox_dir


def _write_outbox_message(
    *,
    notification_type: str,
    channel: str,
    destination: str,
    subject: str,
    body: str,
    metadata: dict | None = None,
) -> dict:
    payload = {
        "id": uuid4().hex,
        "type": notification_type,
        "channel": channel,
        "destination": destination,
        "subject": subject,
        "body": body,
        "metadata": metadata or {},
        "created_at": utc_now_naive().isoformat(timespec="seconds"),
    }
    file_path = _outbox_dir() / f"{payload['created_at'].replace(':', '').replace('-', '')}_{payload['id']}.json"
    file_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    payload["file_path"] = str(file_path)
    return payload


def _send_email_smtp(*, destination: str, subject: str, body: str):
    host = str(current_app.config.get("SMTP_HOST") or "").strip()
    if not host:
        raise NotificationDeliveryError("SMTP_HOST não configurado para envio de email.")

    port = int(current_app.config.get("SMTP_PORT", 587) or 587)
    username = str(current_app.config.get("SMTP_USERNAME") or "").strip() or None
    password = str(current_app.config.get("SMTP_PASSWORD") or "").strip() or None
    from_address = str(
        current_app.config.get("EMAIL_FROM_ADDRESS") or "nao-responda@siscon.local"
    ).strip()
    use_tls = bool(current_app.config.get("SMTP_USE_TLS", True))

    message = EmailMessage()
    message["From"] = from_address
    message["To"] = destination
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    except OSError as exc:
        raise NotificationDeliveryError(f"Falha ao enviar email por SMTP: {exc}") from exc


def _send_sms_webhook(*, destination: str, body: str):
    webhook_url = str(current_app.config.get("SMS_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        raise NotificationDeliveryError("SMS_WEBHOOK_URL não configurado para envio de SMS.")

    auth_token = str(current_app.config.get("SMS_WEBHOOK_AUTH_TOKEN") or "").strip()
    payload = json.dumps({"to": destination, "message": body}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    request = Request(webhook_url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=15) as response:
            status_code = getattr(response, "status", 200)
            if status_code >= 400:
                raise NotificationDeliveryError(
                    f"Falha ao enviar SMS. Resposta do provedor: {status_code}."
                )
    except (HTTPError, URLError, OSError) as exc:
        raise NotificationDeliveryError(f"Falha ao enviar SMS: {exc}") from exc


def send_notification(
    *,
    notification_type: str,
    channel: str,
    destination: str,
    subject: str,
    body: str,
    metadata: dict | None = None,
) -> dict:
    mode = _delivery_mode()

    if mode == "file":
        return _write_outbox_message(
            notification_type=notification_type,
            channel=channel,
            destination=destination,
            subject=subject,
            body=body,
            metadata=metadata,
        )

    if channel == "email":
        _send_email_smtp(destination=destination, subject=subject, body=body)
        return {"channel": channel, "destination": destination, "mode": "smtp"}

    if channel == "sms":
        _send_sms_webhook(destination=destination, body=body)
        return {"channel": channel, "destination": destination, "mode": "sms_webhook"}

    raise NotificationDeliveryError("Canal de notificação sem suporte.")
