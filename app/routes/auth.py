from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.extensions import db
from app.models import User
from app.services.notification_service import NotificationDeliveryError, send_notification
from app.services.password_reset_service import PasswordResetService
from app.utils.account_security import validar_senha
from app.utils.datetime_utils import utc_now_naive

auth_bp = Blueprint("auth", __name__)

PASSWORD_RESET_SESSION_KEYS = (
    "password_reset_pending_request_id",
    "password_reset_pending_challenge_token",
    "password_reset_pending_expires_at",
    "password_reset_pending_destino",
    "password_reset_pending_channel",
    "password_reset_verified_request_id",
)


def _request_ip() -> str | None:
    return (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip() or None


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
    }


def _verified_password_reset_request_id() -> int | None:
    try:
        return int(session.get("password_reset_verified_request_id") or 0) or None
    except (TypeError, ValueError):
        session.pop("password_reset_verified_request_id", None)
        return None


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

        user = User.query.filter(func.lower(User.login) == login, User.ativo.is_(True)).first()

        if user and user.check_password(senha):
            user.ultimo_login_em = utc_now_naive()
            user.ultimo_login_ip = _request_ip()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            login_user(user)
            flash("Login realizado com sucesso.", "success")
            if getattr(user, "forcar_troca_senha", False):
                flash("Atualize sua senha antes de continuar.", "info")
                return redirect(url_for("usuarios.minha_senha"))
            if getattr(user, "perfil_pendente", False):
                flash("Complete seu cadastro antes de continuar.", "info")
                return redirect(url_for("usuarios.meu_cadastro"))
            return redirect(url_for("dashboard.index"))

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

    if request.method == "POST":
        identificador = request.form.get("identificador", "").strip()
        ttl_minutes = PasswordResetService.token_ttl_minutes()
        success_message = (
            "Se houver um acesso ativo com contato valido, enviaremos um codigo de recuperacao "
            "por email ou SMS."
        )

        _store_pending_password_reset_session(
            request_id=None,
            challenge_token=None,
            masked_destination="contato protegido cadastrado",
            channel="contato",
            expires_at=utc_now_naive() + timedelta(minutes=ttl_minutes),
        )

        try:
            usuario = PasswordResetService.buscar_usuario_por_identificador(identificador)
            if not usuario:
                PasswordResetService.perform_dummy_work()
            else:
                solicitacao, raw_code, destination, challenge_token = PasswordResetService.criar_solicitacao(
                    usuario=usuario,
                    request_ip=_request_ip(),
                    ttl_minutes=ttl_minutes,
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
                db.session.commit()
        except (NotificationDeliveryError, ValueError):
            db.session.rollback()
            _store_pending_password_reset_session(
                request_id=None,
                challenge_token=None,
                masked_destination="contato protegido cadastrado",
                channel="contato",
                expires_at=utc_now_naive() + timedelta(minutes=ttl_minutes),
            )
            current_app.logger.warning("Nao foi possivel concluir a solicitacao de redefinicao.")
        except Exception:
            db.session.rollback()
            _store_pending_password_reset_session(
                request_id=None,
                challenge_token=None,
                masked_destination="contato protegido cadastrado",
                channel="contato",
                expires_at=utc_now_naive() + timedelta(minutes=ttl_minutes),
            )
            current_app.logger.exception("Falha inesperada ao solicitar redefinicao de senha.")

        flash(success_message, "info")
        return redirect(url_for("auth.verify_reset_code"))

    return render_template("auth/forgot_password.html")


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
