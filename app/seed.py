import os
import secrets
from pathlib import Path

import click
from flask import current_app

from app.extensions import db
from app.models import User, TipoRPV, SituacaoEmpenho, SituacaoImposto
from app.utils.domain_profile import get_domain_profile


def seed_tipos_rpv():
    profile = get_domain_profile()

    for definicao in profile.tipos_rpv:
        existente = TipoRPV.query.filter_by(nome=definicao.nome).first()
        if existente:
            existente.ativo = True
            existente.ordem_exibicao = definicao.ordem_exibicao
        else:
            db.session.add(
                TipoRPV(
                    nome=definicao.nome,
                    ativo=True,
                    ordem_exibicao=definicao.ordem_exibicao,
                )
            )


def seed_situacoes_empenho():
    profile = get_domain_profile()

    for definicao in profile.situacoes_empenho:
        existente = SituacaoEmpenho.query.filter_by(nome=definicao.nome).first()
        if existente:
            existente.cor_badge = definicao.cor_badge
            existente.ordem_fluxo = definicao.ordem_fluxo
            existente.ativo = definicao.ativo
            existente.is_final = definicao.is_final
        else:
            db.session.add(
                SituacaoEmpenho(
                    nome=definicao.nome,
                    cor_badge=definicao.cor_badge,
                    ordem_fluxo=definicao.ordem_fluxo,
                    ativo=definicao.ativo,
                    is_final=definicao.is_final,
                )
            )


def seed_situacoes_imposto():
    profile = get_domain_profile()

    for definicao in profile.situacoes_imposto:
        existente = SituacaoImposto.query.filter_by(nome=definicao.nome).first()
        if existente:
            existente.cor_badge = definicao.cor_badge
            existente.ordem_fluxo = definicao.ordem_fluxo
            existente.ativo = definicao.ativo
            existente.is_final = definicao.is_final
        else:
            db.session.add(
                SituacaoImposto(
                    nome=definicao.nome,
                    cor_badge=definicao.cor_badge,
                    ordem_fluxo=definicao.ordem_fluxo,
                    ativo=definicao.ativo,
                    is_final=definicao.is_final,
                )
            )


def seed_usuario_admin():
    usuario = User.query.filter_by(login="admin").first()

    if not usuario:
        usuario = User(
            nome="Administrador",
            login="admin",
            email="admin@controle-rpv.local",
            cargo="Administrador do sistema",
            setor="Administracao",
            ativo=True,
            is_admin=True,
        )
        senha_bootstrap, origem = _resolver_senha_bootstrap_admin()
        usuario.set_password(senha_bootstrap)
        usuario.forcar_troca_senha = True
        db.session.add(usuario)
        return origem

    return None


def _resolver_senha_bootstrap_admin():
    senha_ambiente = str(os.getenv("ADMIN_INITIAL_PASSWORD") or "").strip()
    if senha_ambiente:
        return senha_ambiente, "variavel de ambiente ADMIN_INITIAL_PASSWORD"

    arquivo_bootstrap = Path(current_app.instance_path) / "admin_bootstrap_password.txt"
    if arquivo_bootstrap.exists():
        senha_existente = arquivo_bootstrap.read_text(encoding="utf-8").strip()
        if senha_existente:
            return senha_existente, f"arquivo {arquivo_bootstrap}"

    senha_bootstrap = secrets.token_urlsafe(18)
    arquivo_bootstrap.parent.mkdir(parents=True, exist_ok=True)
    arquivo_bootstrap.write_text(senha_bootstrap, encoding="utf-8")
    return senha_bootstrap, f"arquivo {arquivo_bootstrap}"


def seed_all():
    seed_tipos_rpv()
    seed_situacoes_empenho()
    seed_situacoes_imposto()
    origem_admin = seed_usuario_admin()
    db.session.commit()
    return origem_admin


def register_seed_commands(app):
    @app.cli.command("seed-data")
    def seed_data_command():
        origem_admin = seed_all()
        click.echo("Dados iniciais cadastrados com sucesso.")
        if origem_admin:
            click.echo(f"Senha bootstrap do admin definida por: {origem_admin}")
