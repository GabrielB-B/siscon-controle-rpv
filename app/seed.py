import os
import secrets
from pathlib import Path

import click
from flask import current_app

from app.extensions import db
from app.models import User, TipoRPV, SituacaoEmpenho, SituacaoImposto


TIPOS_RPV_INICIAIS = [
    "RPV pessoal",
    "RPV custeio",
    "RPV honorários",
    "RPV periciais",
    "RPV trabalhista",
    "RPV federal",
    "Guia de custas",
    "Indenização",
    "Danos Morais",
    "RPV dativo",
]

SITUACOES_EMPENHO_INICIAIS = [
    "Sem Tratamento",
    "Guias Geradas",
    "SE Aguardando Aprovação",
    "SE Aprovada - Gerar NE",
    "NE Aguardando Assinatura",
    "VD à Liquidar",
    "PD em Lote Carregada",
    "PD Gerada - SEFAZ",
    "Pagamento - OB Gerada",
    "Aguardando Assinatura da OB",
    "Assinado - Levar ao Banco",
    "Aguardando Retorno Banco",
    "Pago",
    "Concluída",
    "Devolvido",
    "Cancelado",
]

SITUACOES_IMPOSTO_INICIAIS = [
    "Sem Tratamento",
    "Sem IRRF",
    "Aguardando PGTO OB Principal",
    "PD IRRF - Aguardando SEFAZ",
    "PGTO IRRF - OB Gerada",
    "Concluída",
    "Devolvido",
    "Cancelado",
]

CORES_EMPENHO = {
    "Sem Tratamento": "badge-slate",
    "Guias Geradas": "badge-sky",
    "SE Aguardando Aprovação": "badge-amber",
    "SE Aprovada - Gerar NE": "badge-blue",
    "NE Aguardando Assinatura": "badge-indigo",
    "VD à Liquidar": "badge-violet",
    "PD em Lote Carregada": "badge-cyan",
    "PD Gerada - SEFAZ": "badge-teal",
    "Pagamento - OB Gerada": "badge-purple",
    "Aguardando Assinatura da OB": "badge-amber",
    "Assinado - Levar ao Banco": "badge-purple",
    "Aguardando Retorno Banco": "badge-fuchsia",
    "Pago": "badge-emerald",
    "Concluída": "badge-green",
    "Devolvido": "badge-red",
    "Cancelado": "badge-zinc",
}

CORES_IMPOSTO = {
    "Sem Tratamento": "badge-slate",
    "Sem IRRF": "badge-slate",
    "Aguardando PGTO OB Principal": "badge-amber",
    "PD IRRF - Aguardando SEFAZ": "badge-cyan",
    "PGTO IRRF - OB Gerada": "badge-purple",
    "Concluída": "badge-green",
    "Devolvido": "badge-red",
    "Cancelado": "badge-zinc",
}


def seed_tipos_rpv():
    for ordem, nome in enumerate(TIPOS_RPV_INICIAIS, start=1):
        existente = TipoRPV.query.filter_by(nome=nome).first()
        if existente:
            existente.ativo = True
            existente.ordem_exibicao = ordem
        else:
            db.session.add(
                TipoRPV(
                    nome=nome,
                    ativo=True,
                    ordem_exibicao=ordem,
                )
            )


def seed_situacoes_empenho():
    finais = {"Concluída", "Devolvido", "Cancelado"}

    for ordem, nome in enumerate(SITUACOES_EMPENHO_INICIAIS, start=1):
        existente = SituacaoEmpenho.query.filter_by(nome=nome).first()
        if existente:
            existente.cor_badge = CORES_EMPENHO.get(nome)
            existente.ordem_fluxo = ordem
            existente.ativo = True
            existente.is_final = nome in finais
        else:
            db.session.add(
                SituacaoEmpenho(
                    nome=nome,
                    cor_badge=CORES_EMPENHO.get(nome),
                    ordem_fluxo=ordem,
                    ativo=True,
                    is_final=nome in finais,
                )
            )


def seed_situacoes_imposto():
    finais = {"Sem IRRF", "Concluída", "Devolvido", "Cancelado"}

    for ordem, nome in enumerate(SITUACOES_IMPOSTO_INICIAIS, start=1):
        existente = SituacaoImposto.query.filter_by(nome=nome).first()
        if existente:
            existente.cor_badge = CORES_IMPOSTO.get(nome)
            existente.ordem_fluxo = ordem
            existente.ativo = True
            existente.is_final = nome in finais
        else:
            db.session.add(
                SituacaoImposto(
                    nome=nome,
                    cor_badge=CORES_IMPOSTO.get(nome),
                    ordem_fluxo=ordem,
                    ativo=True,
                    is_final=nome in finais,
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
