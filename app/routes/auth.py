from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.extensions import db
from app.models import User
from app.services.notification_service import NotificationDeliveryError, channel_is_configured, send_notification
from app.services.password_reset_service import PasswordResetService
from app.utils.account_security import validar_senha
from app.utils.datetime_utils import utc_now_naive
from app.utils.request_throttle import request_throttle

auth_bp = Blueprint("auth", __name__)

PASSWORD_RESET_SESSION_KEYS = (
    "password_reset_identified_user_id",
    "password_reset_identified_expires_at",
    "password_reset_pending_request_id",
    "password_reset_pending_challenge_token",
    "password_reset_pending_expires_at",
    "password_reset_pending_destino",
    "password_reset_pending_channel",
    "password_reset_verified_request_id",
)


def _request_ip() -> str | None:
    return (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip() or None


def _format_retry_after(segundos: int) -> str:
    segundos = max(int(segundos or 0), 1)
    if segundos >= 60:
        minutos = max(1, segundos // 60)
        return f"{minutos} minuto(s)"
    return f"{segundos} segundo(s)"


def _login_throttle_limits() -> tuple[int, int]:
    return (
        int(current_app.config.get("LOGIN_THROTTLE_MAX_FAILURES", 5) or 0),
        int(current_app.config.get("LOGIN_THROTTLE_WINDOW_SECONDS", 60) or 0),
    )


def _password_reset_send_throttle_limits() -> tuple[int, int]:
    return (
        int(current_app.config.get("PASSWORD_RESET_SEND_THROTTLE_MAX_ATTEMPTS", 3) or 0),
        int(current_app.config.get("PASSWORD_RESET_SEND_THROTTLE_WINDOW_SECONDS", 600) or 0),
    )


def _login_throttle_key(login: str | None) -> str:
    ip = _request_ip() or "desconhecido"
    login_normalizado = str(login or "").strip().lower() or "anonimo"
    return f"login:{ip}:{login_normalizado}"


def _password_reset_send_throttle_key(usuario: User) -> str:
    ip = _request_ip() or "desconhecido"
    return f"password-reset-send:{ip}:{usuario.id}"


def _clear_password_reset_session(*, keep_verified: bool = False):
    for key in PASSWORD_RESET_SESSION_KEYS:
        if keep_verified and key == "password_reset_verified_request_id":
            continue
        session.pop(key, None)


def _store_pending_password_reset_session(
    *,
    request_id: int | None,
    challenge_token: str | None,
    masked_destination: str | None,
    channel: str | None,
    expires_at,
):
    _clear_password_reset_session()
    session["password_reset_pending_request_id"] = request_id
    session["password_reset_pending_challenge_token"] = challenge_token
    session["password_reset_pending_destino"] = masked_destination or "contato protegido cadastrado"
    session["password_reset_pending_channel"] = channel or "contato"
    session["password_reset_pending_expires_at"] = expires_at.isoformat()


def _store_identified_password_reset_session(*, usuario: User, ttl_minutes: int):
    _clear_password_reset_session()
    session["password_reset_identified_user_id"] = usuario.id
    session["password_reset_identified_expires_at"] = (
        utc_now_naive() + timedelta(minutes=max(int(ttl_minutes or 10), 5))
    ).isoformat()


def _identified_password_reset_context() -> dict | None:
    expires_at_raw = session.get("password_reset_identified_expires_at")
    usuario_id = session.get("password_reset_identified_user_id")
    if not expires_at_raw or not usuario_id:
        return None

    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
        usuario_id_int = int(usuario_id)
    except (TypeError, ValueError):
        _clear_password_reset_session()
        return None

    if expires_at <= utc_now_naive():
        _clear_password_reset_session()
        return None

    usuario = db.session.get(User, usuario_id_int)
    if not usuario or not usuario.ativo:
        _clear_password_reset_session()
        return None

    contact_options = _available_password_reset_contact_options(usuario)
    if not contact_options:
        _clear_password_reset_session()
        return None

    return {
        "usuario": usuario,
        "contact_options": contact_options,
        "expires_at": expires_at,
    }


def _pending_password_reset_context() -> dict | None:
    expires_at_raw = session.get("password_reset_pending_expires_at")
    if not expires_at_raw:
        return None

    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except (TypeError, ValueError):
        _clear_password_reset_session()
        return None

    request_id = session.get("password_reset_pending_request_id")
    solicitacao = PasswordResetService.obter_solicitacao_valida_por_id(request_id)
    if request_id and not solicitacao:
        _clear_password_reset_session()
        return None

    if solicitacao:
        expires_at = solicitacao.expira_em

    return {
        "request_id": request_id,
        "challenge_token": session.get("password_reset_pending_challenge_token"),
        "masked_destination": session.get("password_reset_pending_destino") or "contato protegido cadastrado",
        "channel": session.get("password_reset_pending_channel") or "contato",
        "expires_at": expires_at,
        "seconds_remaining": max(int((expires_at - utc_now_naive()).total_seconds()), 0),
        "solicitacao": solicitacao,
        "local_delivery": _password_reset_uses_local_delivery(),
    }


def _verified_password_reset_request_id() -> int | None:
    try:
        return int(session.get("password_reset_verified_request_id") or 0) or None
    except (TypeError, ValueError):
        session.pop("password_reset_verified_request_id", None)
        return None


def _password_reset_uses_local_delivery() -> bool:
    return str(current_app.config.get("NOTIFICATION_DELIVERY_MODE", "file") or "file").strip().lower() == "file"


def _send_password_reset_notification(*, channel: str, destination: str, raw_code: str) -> dict:
    ttl_minutes = PasswordResetService.token_ttl_minutes()

    if channel == "email":
        return send_notification(
            notification_type="password_reset_code",
            channel=channel,
            destination=destination,
            subject="SISCON | Codigo de recuperacao",
            body=(
                "Recebemos uma solicitacao para redefinir sua senha no SISCON.\n\n"
                f"Seu codigo de recuperacao: {raw_code}\n"
                f"Validade: {ttl_minutes} minutos.\n\n"
                "Se voce nao reconhece esta acao, ignore esta mensagem."
            ),
            metadata={"ttl_minutes": ttl_minutes},
        )

    return send_notification(
        notification_type="password_reset_code",
        channel=channel,
        destination=destination,
        subject="SISCON | Codigo de recuperacao",
        body=(
            f"SISCON: seu codigo de recuperacao e {raw_code}. "
            f"Valido por {ttl_minutes} min. Se nao foi voce, ignore."
        ),
        metadata={"ttl_minutes": ttl_minutes},
    )


def _available_password_reset_contact_options(usuario: User) -> list[dict]:
    return [
        canal
        for canal in PasswordResetService.canais_recuperacao_disponiveis(usuario)
        if channel_is_configured(canal["channel"])
    ]


@auth_bp.before_app_request
def exigir_troca_obrigatoria_de_senha():
    if not current_user.is_authenticated:
        return None

    endpoint = request.endpoint or ""

    if not getattr(current_user, "forcar_troca_senha", False):
        if getattr(current_user, "perfil_pendente", False):
            if endpoint in {"usuarios.meu_cadastro", "usuarios.minha_senha", "auth.logout", "static"} or endpoint.startswith("static"):
                return None
            return redirect(url_for("usuarios.meu_cadastro"))
        return None

    if endpoint in {"usuarios.minha_senha", "auth.logout", "static"} or endpoint.startswith("static"):
        return None

    return redirect(url_for("usuarios.minha_senha"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if getattr(current_user, "forcar_troca_senha", False):
            return redirect(url_for("usuarios.minha_senha"))
        if getattr(current_user, "perfil_pendente", False):
            return redirect(url_for("usuarios.meu_cadastro"))
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        login = request.form.get("login", "").strip().lower()
        senha = request.form.get("senha", "")
        throttle_key = _login_throttle_key(login)
        throttle_limit, throttle_window = _login_throttle_limits()
        throttle_decision = request_throttle.check(
            throttle_key,
            limit=throttle_limit,
            window_seconds=throttle_window,
        )
        if not throttle_decision.allowed:
            flash(
                "Muitas tentativas de login. Aguarde "
                f"{_format_retry_after(throttle_decision.retry_after_seconds)} antes de tentar novamente.",
                "warning",
            )
            return render_template("auth/login.html")

        user = User.query.filter(func.lower(User.login) == login, User.ativo.is_(True)).first()

        if user and user.check_password(senha):
            request_throttle.clear(throttle_key)
            user.ultimo_login_em = utc_now_naive()
            user.ultimo_login_ip = _request_ip()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(
                    "Falha ao persistir metadados do ultimo login. usuario_id=%s",
                    getattr(user, "id", None),
                    exc_info=True,
                )
            login_user(user)
            flash("Login realizado com sucesso.", "success")
            if getattr(user, "forcar_troca_senha", False):
                flash("Atualize sua senha antes de continuar.", "info")
                return redirect(url_for("usuarios.minha_senha"))
            if getattr(user, "perfil_pendente", False):
                flash("Complete seu cadastro antes de continuar.", "info")
                return redirect(url_for("usuarios.meu_cadastro"))
            return redirect(url_for("dashboard.index"))

        request_throttle.hit(throttle_key, window_seconds=throttle_window)
        throttle_decision = request_throttle.check(
            throttle_key,
            limit=throttle_limit,
            window_seconds=throttle_window,
        )
        if not throttle_decision.allowed:
            flash(
                "Muitas tentativas de login. Aguarde "
                f"{_format_retry_after(throttle_decision.retry_after_seconds)} antes de tentar novamente.",
                "warning",
            )
        else:
            flash("Login ou senha invalidos.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/esqueci-senha", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        if getattr(current_user, "forcar_troca_senha", False):
            return redirect(url_for("usuarios.minha_senha"))
        if getattr(current_user, "perfil_pendente", False):
            return redirect(url_for("usuarios.meu_cadastro"))
        return redirect(url_for("dashboard.index"))

    ttl_minutes = PasswordResetService.token_ttl_minutes()

    if request.method == "POST":
        form_step = request.form.get("form_step", "identificar").strip().lower()

        if form_step == "enviar_codigo":
            contexto_identificado = _identified_password_reset_context()
            if not contexto_identificado:
                flash("Informe seu login para escolher um canal de recuperacao.", "warning")
                return redirect(url_for("auth.forgot_password"))

            throttle_key = _password_reset_send_throttle_key(contexto_identificado["usuario"])
            throttle_limit, throttle_window = _password_reset_send_throttle_limits()
            throttle_decision = request_throttle.check(
                throttle_key,
                limit=throttle_limit,
                window_seconds=throttle_window,
            )
            if not throttle_decision.allowed:
                flash(
                    "Voce acabou de solicitar codigo de recuperacao. Aguarde "
                    f"{_format_retry_after(throttle_decision.retry_after_seconds)} antes de pedir outro.",
                    "warning",
                )
                return render_template(
                    "auth/forgot_password.html",
                    contact_options=contexto_identificado["contact_options"],
                    local_delivery=_password_reset_uses_local_delivery(),
                )

            canal_recuperacao = request.form.get("canal_recuperacao", "").strip().lower()
            canais_validos = {canal["channel"] for canal in contexto_identificado["contact_options"]}
            if canal_recuperacao not in canais_validos:
                flash("Escolha um canal cadastrado para receber o codigo.", "warning")
                return render_template(
                    "auth/forgot_password.html",
                    contact_options=contexto_identificado["contact_options"],
                    local_delivery=_password_reset_uses_local_delivery(),
                )

            try:
                solicitacao, raw_code, destination, challenge_token = PasswordResetService.criar_solicitacao(
                    usuario=contexto_identificado["usuario"],
                    request_ip=_request_ip(),
                    ttl_minutes=ttl_minutes,
                    preferred_channel=canal_recuperacao,
                )
                db.session.flush()
                _send_password_reset_notification(
                    channel=solicitacao.canal,
                    destination=destination,
                    raw_code=raw_code,
                )
                _store_pending_password_reset_session(
                    request_id=solicitacao.id,
                    challenge_token=challenge_token,
                    masked_destination=solicitacao.destino_mascarado,
                    channel=solicitacao.canal,
                    expires_at=solicitacao.expira_em,
                )
                request_throttle.hit(throttle_key, window_seconds=throttle_window)
                db.session.commit()
            except (NotificationDeliveryError, ValueError):
                db.session.rollback()
                _clear_password_reset_session()
                current_app.logger.warning("Nao foi possivel concluir a solicitacao de redefinicao.")
                flash("Nao foi possivel enviar o codigo agora. Tente novamente em instantes.", "warning")
                return redirect(url_for("auth.forgot_password"))
            except Exception:
                db.session.rollback()
                _clear_password_reset_session()
                current_app.logger.exception("Falha inesperada ao solicitar redefinicao de senha.")
                flash("Nao foi possivel enviar o codigo agora. Tente novamente.", "danger")
                return redirect(url_for("auth.forgot_password"))

            if _password_reset_uses_local_delivery():
                flash("Codigo registrado na caixa local de recuperacao para conferencia administrativa.", "info")
            else:
                flash("Codigo enviado para o contato cadastrado escolhido.", "info")
            return redirect(url_for("auth.verify_reset_code"))

        login = request.form.get("login", "").strip()
        if not login:
            flash("Informe seu login para continuar.", "warning")
            return render_template(
                "auth/forgot_password.html",
                local_delivery=_password_reset_uses_local_delivery(),
            )

        usuario = PasswordResetService.buscar_usuario_por_login(login)
        if not usuario:
            PasswordResetService.perform_dummy_work()
            _clear_password_reset_session()
            flash(
                "Se o login informado estiver ativo e tiver contato cadastrado, os canais aparecerao protegidos para escolha.",
                "info",
            )
            return render_template(
                "auth/forgot_password.html",
                local_delivery=_password_reset_uses_local_delivery(),
            )

        contact_options = _available_password_reset_contact_options(usuario)
        if not contact_options:
            PasswordResetService.perform_dummy_work()
            _clear_password_reset_session()
            flash(
                "Se o login informado estiver ativo e tiver contato cadastrado, os canais aparecerao protegidos para escolha.",
                "info",
            )
            return render_template(
                "auth/forgot_password.html",
                local_delivery=_password_reset_uses_local_delivery(),
            )

        _store_identified_password_reset_session(usuario=usuario, ttl_minutes=ttl_minutes)
        flash("Escolha onde deseja receber o codigo de recuperacao.", "info")
        return render_template(
            "auth/forgot_password.html",
            contact_options=contact_options,
            local_delivery=_password_reset_uses_local_delivery(),
        )

    contexto_identificado = _identified_password_reset_context()

    return render_template(
        "auth/forgot_password.html",
        contact_options=(contexto_identificado or {}).get("contact_options"),
        local_delivery=_password_reset_uses_local_delivery(),
    )


@auth_bp.route("/redefinir-senha/codigo", methods=["GET", "POST"])
def verify_reset_code():
    if current_user.is_authenticated:
        if getattr(current_user, "forcar_troca_senha", False):
            return redirect(url_for("usuarios.minha_senha"))
        return redirect(url_for("dashboard.index"))

    verified_request_id = _verified_password_reset_request_id()
    if verified_request_id:
        solicitacao_verificada = PasswordResetService.obter_solicitacao_valida_por_id(verified_request_id)
        if solicitacao_verificada and solicitacao_verificada.codigo_verificado:
            return redirect(url_for("auth.reset_password"))
        session.pop("password_reset_verified_request_id", None)

    contexto = _pending_password_reset_context()
    if not contexto:
        flash("Solicite um novo codigo para continuar a recuperacao.", "warning")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        try:
            solicitacao, mensagem, status = PasswordResetService.verificar_codigo(
                solicitacao_id=contexto["request_id"],
                challenge_token=contexto["challenge_token"],
                raw_code=request.form.get("codigo", ""),
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Falha inesperada ao validar codigo de redefinicao.")
            flash("Nao foi possivel validar o codigo agora. Tente novamente.", "danger")
            contexto = _pending_password_reset_context()
            if not contexto:
                flash("Solicite um novo codigo para continuar a recuperacao.", "warning")
                return redirect(url_for("auth.forgot_password"))
            return render_template("auth/verify_reset_code.html", contexto=contexto)

        if solicitacao:
            session["password_reset_verified_request_id"] = solicitacao.id
            _clear_password_reset_session(keep_verified=True)
            flash("Codigo confirmado. Agora defina sua nova senha.", "success")
            return redirect(url_for("auth.reset_password"))

        if status == "restart":
            _clear_password_reset_session()
            flash(mensagem or "Solicite um novo codigo para continuar.", "warning")
            return redirect(url_for("auth.forgot_password"))

        flash(mensagem or "Codigo invalido.", "danger")
        contexto = _pending_password_reset_context()
        if not contexto:
            flash("Solicite um novo codigo para continuar a recuperacao.", "warning")
            return redirect(url_for("auth.forgot_password"))

    return render_template("auth/verify_reset_code.html", contexto=contexto)


@auth_bp.route("/redefinir-senha/nova-senha", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        if getattr(current_user, "forcar_troca_senha", False):
            return redirect(url_for("usuarios.minha_senha"))
        return redirect(url_for("dashboard.index"))

    solicitacao_id = _verified_password_reset_request_id()
    solicitacao = PasswordResetService.obter_solicitacao_valida_por_id(solicitacao_id)
    if not solicitacao or not solicitacao.codigo_verificado:
        _clear_password_reset_session()
        flash("A validacao do codigo expirou. Solicite um novo codigo.", "warning")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        try:
            if nova_senha != confirmar_senha:
                raise ValueError("A nova senha e a confirmacao precisam ser iguais.")

            PasswordResetService.redefinir_senha(
                solicitacao_id=solicitacao.id,
                nova_senha=nova_senha,
                password_validator=validar_senha,
            )
            db.session.commit()
            _clear_password_reset_session()
            flash("Senha redefinida com sucesso. Entre com sua nova senha.", "success")
            return redirect(url_for("auth.login"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Falha inesperada ao redefinir senha.")
            flash("Nao foi possivel redefinir a senha agora. Tente novamente.", "danger")

        solicitacao = PasswordResetService.obter_solicitacao_valida_por_id(solicitacao.id)
        if not solicitacao or not solicitacao.codigo_verificado:
            _clear_password_reset_session()
            flash("A validacao do codigo expirou durante a operacao. Solicite um novo.", "warning")
            return redirect(url_for("auth.forgot_password"))

    return render_template("auth/reset_password.html", solicitacao=solicitacao)


@auth_bp.route("/redefinir-senha/<token>")
def legacy_reset_password_link(token: str):
    if token:
        current_app.logger.info("Link legado de redefinicao acessado e redirecionado para fluxo por codigo.")
    flash("Este fluxo agora usa codigo curto por email ou SMS. Solicite um novo codigo para continuar.", "info")
    return redirect(url_for("auth.forgot_password"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Voce saiu do sistema.", "info")
    return redirect(url_for("auth.login"))
