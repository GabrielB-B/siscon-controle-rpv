from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.models import User
from app.utils.account_security import normalizar_email, validar_senha
from app.utils.normalizers import normalizar_telefone, telefone_brasileiro_valido

usuarios_bp = Blueprint("usuarios", __name__, url_prefix="/usuarios")


def admin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            flash("Acesso restrito a administradores.", "danger")
            return redirect(url_for("dashboard.index"))
        return view_func(*args, **kwargs)

    return wrapped_view


def _normalizar_login(login: str | None) -> str:
    return str(login or "").strip().lower()


def _validar_email(email: str):
    if not email:
        return

    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Informe um email válido para o usuário.")


def _normalizar_telefone_form(telefone: str | None, *, obrigatorio: bool) -> str | None:
    telefone_texto = str(telefone or "").strip()

    if not telefone_texto:
        if obrigatorio:
            raise ValueError("Informe seu telefone.")
        return None

    telefone_limpo = normalizar_telefone(telefone_texto)
    if not telefone_brasileiro_valido(telefone_limpo):
        raise ValueError("Informe um telefone com DDD no formato (79) 99999-9999.")

    return telefone_limpo


def _buscar_usuario_por_login(login: str, usuario_id_ignorar: int | None = None) -> User | None:
    query = User.query.filter(func.lower(User.login) == login)
    if usuario_id_ignorar is not None:
        query = query.filter(User.id != usuario_id_ignorar)
    return query.first()


def _buscar_usuario_por_email(email: str, usuario_id_ignorar: int | None = None) -> User | None:
    if not email:
        return None

    query = User.query.filter(func.lower(User.email) == email)
    if usuario_id_ignorar is not None:
        query = query.filter(User.id != usuario_id_ignorar)
    return query.first()


def _metricas_usuarios() -> list[dict]:
    total = User.query.count()
    ativos = User.query.filter_by(ativo=True).count()
    admins = User.query.filter_by(is_admin=True, ativo=True).count()
    perfis_pendentes = User.query.filter_by(is_admin=False, ativo=True).all()
    total_pendentes = sum(1 for usuario in perfis_pendentes if usuario.perfil_pendente)

    return [
        {"label": "Usuários cadastrados", "valor": total, "nota": "Pessoas com acesso ao sistema"},
        {"label": "Usuários ativos", "valor": ativos, "nota": "Podem entrar e trabalhar normalmente"},
        {"label": "Administradores", "valor": admins, "nota": "Possuem acesso à gestão do sistema"},
        {"label": "Perfis pendentes", "valor": total_pendentes, "nota": "Ainda precisam completar o cadastro inicial"},
    ]


def _salvar_usuario(usuario: User | None = None) -> User:
    nome = request.form.get("nome", "").strip()
    login = _normalizar_login(request.form.get("login"))
    email = normalizar_email(request.form.get("email"))
    telefone = _normalizar_telefone_form(
        request.form.get("telefone"),
        obrigatorio=False,
    )
    cargo = request.form.get("cargo", "").strip() or None
    setor = request.form.get("setor", "").strip() or None
    senha = request.form.get("senha", "")
    ativo = request.form.get("ativo") == "1"
    is_admin = request.form.get("is_admin") == "1"
    forcar_troca_senha = request.form.get("forcar_troca_senha") == "1"

    if not nome:
        raise ValueError("Informe o nome completo do usuário.")

    if not login:
        raise ValueError("Informe um login para acesso.")

    _validar_email(email)

    if usuario is None and not senha:
        raise ValueError("Informe uma senha inicial para o novo usuário.")

    if _buscar_usuario_por_login(login, usuario_id_ignorar=getattr(usuario, "id", None)):
        raise ValueError("Já existe um usuário com esse login.")

    if _buscar_usuario_por_email(email, usuario_id_ignorar=getattr(usuario, "id", None)):
        raise ValueError("Já existe um usuário com esse email.")

    if usuario is None:
        usuario = User(
            nome=nome,
            login=login,
            email=email or None,
            telefone=telefone,
            cargo=cargo,
            setor=setor,
            ativo=ativo,
            is_admin=is_admin,
            forcar_troca_senha=forcar_troca_senha,
        )
        db.session.add(usuario)
    else:
        if usuario.id == current_user.id and not ativo:
            raise ValueError("Você não pode desativar o seu próprio usuário.")

        if usuario.id == current_user.id and not is_admin:
            raise ValueError("Você não pode remover o perfil administrador do seu próprio usuário.")

        usuario.nome = nome
        usuario.login = login
        usuario.email = email or None
        usuario.telefone = telefone
        usuario.cargo = cargo
        usuario.setor = setor
        usuario.ativo = ativo
        usuario.is_admin = is_admin
        usuario.forcar_troca_senha = forcar_troca_senha

    if senha:
        validar_senha(senha, login)
        usuario.set_password(senha)
        if request.form.get("forcar_troca_senha") == "1":
            usuario.forcar_troca_senha = True

    return usuario


def _salvar_meu_cadastro(usuario: User) -> User:
    nome = request.form.get("nome", "").strip()
    email = normalizar_email(request.form.get("email"))
    telefone = _normalizar_telefone_form(
        request.form.get("telefone"),
        obrigatorio=True,
    )
    cargo = request.form.get("cargo", "").strip()
    setor = request.form.get("setor", "").strip()

    if not nome:
        raise ValueError("Informe seu nome completo.")

    if not email:
        raise ValueError("Informe seu email.")

    _validar_email(email)

    if not cargo:
        raise ValueError("Informe seu cargo.")

    if not setor:
        raise ValueError("Informe seu setor.")

    if _buscar_usuario_por_email(email, usuario_id_ignorar=usuario.id):
        raise ValueError("Já existe outro usuário com esse email.")

    usuario.nome = nome
    usuario.email = email
    usuario.telefone = telefone
    usuario.cargo = cargo
    usuario.setor = setor

    return usuario


@usuarios_bp.route("/")
@login_required
@admin_required
def lista():
    usuarios = User.query.order_by(User.ativo.desc(), User.is_admin.desc(), User.nome.asc()).all()
    return render_template(
        "usuarios/lista.html",
        usuarios=usuarios,
        metricas=_metricas_usuarios(),
    )


@usuarios_bp.route("/novo", methods=["GET", "POST"])
@login_required
@admin_required
def novo():
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    if request.method == "POST":
        try:
            usuario = _salvar_usuario()
            db.session.commit()
            flash(f"Usuário {usuario.nome} criado com sucesso.", "success")
            return redirect(url_for("usuarios.lista"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao criar usuário: {exc}", "danger")

    return render_template(
        "usuarios/form.html",
        modo="novo",
        usuario=None,
        form_data=form_data,
    )


@usuarios_bp.route("/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@admin_required
def editar(usuario_id: int):
    usuario = User.query.get_or_404(usuario_id)
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    if request.method == "POST":
        try:
            _salvar_usuario(usuario)
            db.session.commit()
            flash(f"Usuário {usuario.nome} atualizado com sucesso.", "success")
            return redirect(url_for("usuarios.editar", usuario_id=usuario.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao atualizar usuário: {exc}", "danger")

    return render_template(
        "usuarios/form.html",
        modo="editar",
        usuario=usuario,
        form_data=form_data,
    )


@usuarios_bp.route("/meu-cadastro", methods=["GET", "POST"])
@login_required
def meu_cadastro():
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    if request.method == "POST":
        try:
            _salvar_meu_cadastro(current_user)
            db.session.commit()
            flash("Cadastro pessoal atualizado com sucesso.", "success")
            return redirect(url_for("dashboard.index"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao atualizar cadastro pessoal: {exc}", "danger")

    return render_template("usuarios/meu_cadastro.html", form_data=form_data)


@usuarios_bp.route("/minha-senha", methods=["GET", "POST"])
@login_required
def minha_senha():
    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        try:
            if not current_user.forcar_troca_senha and not current_user.check_password(senha_atual):
                raise ValueError("A senha atual informada não confere.")

            if nova_senha != confirmar_senha:
                raise ValueError("A nova senha e a confirmação precisam ser iguais.")

            validar_senha(nova_senha, current_user.login)
            current_user.set_password(nova_senha)
            db.session.commit()
            flash("Senha atualizada com sucesso.", "success")
            if current_user.perfil_pendente:
                flash("Complete seu cadastro antes de continuar.", "info")
                return redirect(url_for("usuarios.meu_cadastro"))
            return redirect(url_for("dashboard.index"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao atualizar senha: {exc}", "danger")

    return render_template("usuarios/minha_senha.html")
