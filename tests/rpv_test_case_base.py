import unittest
from datetime import date, datetime, timedelta
import json
import sqlite3
import shutil
import tempfile
from decimal import Decimal
from time import time
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
import re
from uuid import uuid4
from unittest.mock import patch

from flask import Flask, g
import pandas as pd
from sqlalchemy import inspect as sa_inspect
from app.extensions import db, login_manager
from app.models import (
    CotaRPVCompetencia,
    CotaRPVConsumo,
    CotaRPVMovimento,
    DativoCI,
    DativoItem,
    DativoLote,
    HistoricoAlteracao,
    PasswordResetToken,
    Processo,
    RegistroRPV,
    RPVPendenciaDocumento,
    SituacaoEmpenho,
    SituacaoImposto,
    TipoRPV,
    User,
)
from app.observability import init_observability
from app.routes.auth import auth_bp
from app.routes.cadastros import cadastros_bp, precisa_alerta_irrf
from app.routes.dashboard import (
    _agrupar_beneficiarios_fluxo_bi,
    _cards_bi,
    _coletar_dataset_bi,
    _exploracao_beneficiarios_fluxo_bi,
    _filtrar_dataset_bi,
    _query_dativos_bi,
    _query_registros_bi,
    _resumo_dativos_competencia,
    _resumo_dativos_competencia_projetado,
    _resumo_grupos_cota,
    _resumo_grupos_cota_projetado,
    _resumo_irrf_bi,
    _serie_mensal_grupos_cota,
    _serie_mensal_grupos_cota_projetada,
    _series_grupos_cota_bi,
    _series_grupos_cota_bi_projetado,
    dashboard_bp,
)
from app.routes.cotas import cotas_bp
from app.routes.dativos import dativos_bp
from app.routes.historico import historico_bp
from app.routes.observability import observability_bp
from app.routes.reinf import _coletar_base_reinf, _query_dativos_reinf, _query_rpvs_reinf, reinf_bp
from app.routes.usuarios import usuarios_bp
from app.services.irrf_calculator import calcular_irrf_operacional
from app.services.notification_service import send_notification
from app.services.password_reset_service import PasswordResetService
from app.services.cotas_rpv_service import CotasRPVService
from app.services.bi_projection_service import BIProjectionService
from app.security import init_security
from app.utils.datetime_utils import utc_now_naive
from app.utils.formatters import formatar_documento_br, moeda_br
from app.utils.irrf_rules import get_available_irrf_years
from app.utils.request_meta import get_request_ip
from app.utils.request_throttle import request_throttle


class FakeHTTPResponse:
    def __init__(self, body: str = "{}", status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class BaseRPVSemIRRFTestCase(unittest.TestCase):
    __test__ = False
    def setUp(self):
        self.notification_outbox = tempfile.mkdtemp(prefix="siscon-notifications-")
        self.instance_dir = tempfile.mkdtemp(prefix="siscon-instance-")
        request_throttle.clear_all()
        self.app = Flask(
            __name__,
            template_folder=str(Path(__file__).resolve().parents[1] / "app" / "templates"),
            instance_path=self.instance_dir,
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="teste",
            CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            APP_RELEASE_LABEL="Atualizacao Operacional Atlas | Beta interna 2026.05 | Patch 003",
            APP_EXTERNAL_URL="https://siscon.local",
            NOTIFICATION_DELIVERY_MODE="file",
            NOTIFICATION_OUTBOX_DIR=self.notification_outbox,
            REQUEST_THROTTLE_BACKEND="sqlite",
            OBSERVABILITY_ENABLE_FILE_LOGGING=False,
            PASSWORD_RESET_CODE_TTL_MINUTES=10,
            PASSWORD_RESET_TOKEN_TTL_MINUTES=10,
            PASSWORD_RESET_CODE_MAX_ATTEMPTS=5,
        )
        self.app.jinja_env.filters["moeda_br"] = moeda_br
        init_security(self.app)
        init_observability(self.app)

        @self.app.context_processor
        def inject_app_release_label():
            return {"app_release_label": self.app.config.get("APP_RELEASE_LABEL", "")}

        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.login_view = "auth.login"

        self.app.register_blueprint(auth_bp)
        self.app.register_blueprint(dashboard_bp)
        self.app.register_blueprint(cotas_bp)
        self.app.register_blueprint(cadastros_bp)
        self.app.register_blueprint(dativos_bp)
        self.app.register_blueprint(historico_bp)
        self.app.register_blueprint(observability_bp)
        self.app.register_blueprint(reinf_bp)
        self.app.register_blueprint(usuarios_bp)

        self.app_context = self.app.app_context()
        self.app_context.push()
        request_throttle.clear_all()
        db.create_all()

        self._seed_base()

        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
        shutil.rmtree(self.notification_outbox, ignore_errors=True)
        shutil.rmtree(self.instance_dir, ignore_errors=True)

    def _autenticar(self, user_id: int):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    def _arquivos_notificacao(self):
        return sorted(Path(self.notification_outbox).glob("*.json"))

    def _criar_planilha_ods(self, linhas: list[dict], nome_arquivo: str = "dativos_unico.ods") -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        caminho = Path(tempdir.name) / nome_arquivo
        pd.DataFrame(linhas).to_excel(caminho, index=False, engine="odf")
        return caminho

    def _ultima_notificacao(self) -> dict:
        arquivos = self._arquivos_notificacao()
        self.assertTrue(arquivos, "Nenhuma notificacao foi registrada no outbox de teste.")
        return json.loads(arquivos[-1].read_text(encoding="utf-8"))

    def _extrair_codigo_notificacao(self, notificacao: dict) -> str:
        match = re.search(r"\b(\d{6})\b", notificacao.get("body", ""))
        self.assertIsNotNone(match, "Nenhum codigo de 6 digitos foi encontrado na notificacao.")
        return match.group(1)

    def _preparar_sessao_recuperacao(
        self,
        cliente,
        *,
        request_id: int | None,
        challenge_token: str | None,
        expires_at,
        destino: str = "contato protegido cadastrado",
        channel: str = "email",
    ):
        with cliente.session_transaction() as session:
            session["password_reset_pending_request_id"] = request_id
            session["password_reset_pending_challenge_token"] = challenge_token
            session["password_reset_pending_expires_at"] = expires_at.isoformat()
            session["password_reset_pending_destino"] = destino
            session["password_reset_pending_channel"] = channel

    def _identificar_recuperacao(self, cliente, login: str, *, follow_redirects: bool = False):
        return cliente.post(
            "/esqueci-senha",
            data={"form_step": "identificar", "login": login},
            follow_redirects=follow_redirects,
        )

    def _enviar_codigo_recuperacao(
        self,
        cliente,
        *,
        login: str,
        channel: str = "email",
        follow_redirects: bool = True,
    ):
        self._identificar_recuperacao(cliente, login)
        return cliente.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": channel},
            follow_redirects=follow_redirects,
        )

    def _data_base_mes_atual(self):
        return date.today().replace(day=1)

    def _seed_base(self):
        user = User(
            nome="Usuário Teste",
            login="teste",
            email="teste@controle-rpv.local",
            ativo=True,
            is_admin=True,
        )
        user.set_password("senha123")

        tipo_honorarios = TipoRPV(nome="RPV honorários", ativo=True, ordem_exibicao=1)
        tipo_pessoal = TipoRPV(nome="RPV pessoal", ativo=True, ordem_exibicao=2)

        situacao_empenho = SituacaoEmpenho(
            nome="Sem Tratamento",
            cor_badge="badge-slate",
            ordem_fluxo=1,
            ativo=True,
            is_final=False,
        )
        situacao_imposto_pendente = SituacaoImposto(
            nome="Sem Tratamento",
            cor_badge="badge-slate",
            ordem_fluxo=1,
            ativo=True,
            is_final=False,
        )
        situacao_imposto_sem_irrf = SituacaoImposto(
            nome="Sem IRRF",
            cor_badge="badge-slate",
            ordem_fluxo=2,
            ativo=True,
            is_final=True,
        )

        db.session.add_all(
            [
                user,
                tipo_honorarios,
                tipo_pessoal,
                situacao_empenho,
                situacao_imposto_pendente,
                situacao_imposto_sem_irrf,
            ]
        )
        db.session.commit()

        self.user_id = user.id
        self.tipo_honorarios_id = tipo_honorarios.id
        self.tipo_pessoal_id = tipo_pessoal.id
        self.situacao_empenho_id = situacao_empenho.id
        self.situacao_imposto_pendente_id = situacao_imposto_pendente.id
        self.situacao_imposto_sem_irrf_id = situacao_imposto_sem_irrf.id

    def _criar_situacao_empenho(self, nome: str, *, ordem_fluxo: int, is_final: bool) -> SituacaoEmpenho:
        situacao = SituacaoEmpenho(
            nome=nome,
            cor_badge="badge-slate",
            ordem_fluxo=ordem_fluxo,
            ativo=True,
            is_final=is_final,
        )
        db.session.add(situacao)
        db.session.commit()
        return situacao

    def _criar_situacao_imposto(self, nome: str, *, ordem_fluxo: int, is_final: bool) -> SituacaoImposto:
        situacao = SituacaoImposto(
            nome=nome,
            cor_badge="badge-slate",
            ordem_fluxo=ordem_fluxo,
            ativo=True,
            is_final=is_final,
        )
        db.session.add(situacao)
        db.session.commit()
        return situacao

    def _criar_rpv(
        self,
        *,
        nome_beneficiario: str,
        tipo_rpv_id: int | None = None,
        valor_bruto: Decimal = Decimal("8000.00"),
        valor_irrf=None,
        sem_irrf: bool = False,
        situacao_imposto_id: int | None = None,
        data_pagamento=None,
        data_pagamento_irrf=None,
        documento_original: str | None = None,
        processo_edoc: str | None = None,
        numero_processo: str | None = None,
        numero_se: str | None = None,
        elaborador_id: int | None = None,
        criado_por_id: int | None = None,
        exercicio: str = "2026-03",
    ) -> RegistroRPV:
        sufixo = uuid4().hex[:8]

        processo = Processo(
            exercicio=exercicio,
            processo_edoc=processo_edoc or f"CI-{sufixo}",
            numero_processo=numero_processo or f"PROC-{sufixo}",
            data_ci=date(2026, 3, 10),
            data_cadastro=datetime(2026, 3, 10, 9, 0, 0),
            observacoes_gerais=None,
            criado_por_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(processo)
        db.session.flush()

        registro = RegistroRPV(
            processo_id=processo.id,
            tipo_rpv_id=tipo_rpv_id or self.tipo_honorarios_id,
            nome_beneficiario=nome_beneficiario,
            nome_beneficiario_normalizado="",
            tipo_documento="CPF",
            documento_original=documento_original or f"12345678{sufixo[:3]}",
            documento_normalizado="",
            documento_corrigido=None,
            data_pagamento=data_pagamento,
            data_pagamento_irrf=data_pagamento_irrf,
            valor_bruto=valor_bruto,
            valor_irrf=valor_irrf,
            valor_liquido=Decimal("0.00"),
            possui_irrf=False,
            sem_irrf=sem_irrf,
            imposto_texto=None,
            nota_empenho=None,
            numero_se=numero_se,
            situacao_empenho_id=self.situacao_empenho_id,
            situacao_imposto_id=(
                situacao_imposto_id
                if situacao_imposto_id is not None
                else (
                    self.situacao_imposto_sem_irrf_id
                    if sem_irrf
                    else self.situacao_imposto_pendente_id
                )
            ),
            ordem_bancaria=None,
            reinf_status=None,
            ob_imposto=None,
            historico_auto="",
            observacoes=None,
            ativo=True,
            criado_por_id=criado_por_id or self.user_id,
            atualizado_por_id=self.user_id,
            elaborador_id=elaborador_id or self.user_id,
        )

        registro.atualizar_campos_derivados()
        registro.gerar_historico_auto(
            processo_edoc=processo.processo_edoc,
            numero_processo=processo.numero_processo,
            descricao=registro.tipo_rpv.nome if registro.tipo_rpv else None,
            data_ci=processo.data_ci,
        )

        db.session.add(registro)
        db.session.commit()
        return registro

    def _criar_ci_dativo_vazia(
        self,
        *,
        processo_edoc: str | None = None,
        responsavel_id: int | None = None,
        exercicio: str = "2026-03",
    ) -> DativoCI:
        dativo_ci = DativoCI(
            exercicio=exercicio,
            processo_edoc=processo_edoc or f"CI-DAT-VAZIA-{uuid4().hex[:8]}",
            data_ci=date(2026, 3, 10),
            descricao="Dativo Geral",
            criado_por_id=self.user_id,
            responsavel_id=responsavel_id or self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.commit()
        return dativo_ci

    def _criar_dativo_sem_irrf(
        self,
        *,
        processo_edoc: str | None = None,
        itens: list[dict] | None = None,
        numero_se: str | None = None,
        responsavel_id: int | None = None,
        exercicio: str = "2026-03",
    ):
        sufixo = uuid4().hex[:8]
        dativo_ci = DativoCI(
            exercicio=exercicio,
            processo_edoc=processo_edoc or f"CI-DAT-{sufixo}",
            data_ci=date(2026, 3, 10),
            descricao="Dativo Geral",
            criado_por_id=self.user_id,
            responsavel_id=responsavel_id or self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.flush()

        lote = DativoLote(
            dativo_ci_id=dativo_ci.id,
            tipo_lote="sem_irrf",
            quantidade_itens=0,
            valor_total_bruto=Decimal("0.00"),
            valor_total_irrf=Decimal("0.00"),
            valor_total_liquido=Decimal("0.00"),
            nota_empenho=None,
            numero_se=numero_se,
            ordem_bancaria=None,
            situacao_rpv_id=self.situacao_empenho_id,
            situacao_imposto_id=self.situacao_imposto_sem_irrf_id,
            resumo_operacional="",
            observacoes=None,
            ativo=True,
            criado_por_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(lote)
        db.session.flush()

        itens_criados = []
        for indice, dados in enumerate(itens or [], start=1):
            item = DativoItem(
                dativo_ci_id=dativo_ci.id,
                dativo_lote_id=lote.id,
                grupo="sem_irrf",
                nome_beneficiario=dados["nome_beneficiario"],
                nome_beneficiario_normalizado="",
                cpf_original=dados.get("cpf_original", f"1234567890{indice}"),
                cpf_normalizado="",
                numero_processo=dados.get("numero_processo", f"PROC-DAT-{indice}-{sufixo}"),
                data_pagamento=dados.get("data_pagamento"),
                reinf_status=None,
                dispensa_irrf_confirmada=dados.get("dispensa_irrf_confirmada", False),
                valor_bruto=dados["valor_bruto"],
                valor_irrf=None,
                valor_liquido=Decimal("0.00"),
                nota_empenho=None,
                numero_se=numero_se,
                ordem_bancaria=None,
                ob_imposto=None,
                situacao_rpv_id=self.situacao_empenho_id,
                situacao_imposto_id=self.situacao_imposto_sem_irrf_id,
                resumo_operacional="",
                observacoes=None,
                ativo=True,
                criado_por_id=self.user_id,
                atualizado_por_id=self.user_id,
            )
            item.atualizar_campos_derivados()
            item.gerar_resumo_operacional(
                processo_edoc=dativo_ci.processo_edoc,
                data_ci=dativo_ci.data_ci,
            )
            db.session.add(item)
            itens_criados.append(item)

        db.session.flush()
        lote.atualizar_totais()
        lote.gerar_resumo_operacional()
        db.session.commit()

        return dativo_ci, lote, itens_criados

    def _criar_item_dativo_com_irrf(
        self,
        *,
        processo_edoc: str | None = None,
        nome_beneficiario: str = "Item com IRRF",
        cpf_original: str = "12345678901",
        numero_processo: str | None = None,
        valor_bruto: Decimal = Decimal("7000.00"),
        valor_irrf: Decimal | None = Decimal("700.00"),
        numero_se: str | None = None,
        responsavel_id: int | None = None,
    ):
        dativo_ci = DativoCI(
            exercicio="2026-03",
            processo_edoc=processo_edoc or f"CI-DAT-IRRF-{uuid4().hex[:8]}",
            data_ci=date(2026, 3, 10),
            descricao="Dativo Geral",
            criado_por_id=self.user_id,
            responsavel_id=responsavel_id or self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.flush()

        item = DativoItem(
            dativo_ci_id=dativo_ci.id,
            dativo_lote_id=None,
            grupo="com_irrf",
            nome_beneficiario=nome_beneficiario,
            nome_beneficiario_normalizado="",
            cpf_original=cpf_original,
            cpf_normalizado="",
            numero_processo=numero_processo or f"PROC-DAT-IRRF-{uuid4().hex[:6]}",
            data_pagamento=None,
            reinf_status=None,
            dispensa_irrf_confirmada=False,
            valor_bruto=valor_bruto,
            valor_irrf=valor_irrf,
            valor_liquido=Decimal("0.00"),
            nota_empenho=None,
            numero_se=numero_se,
            ordem_bancaria=None,
            ob_imposto=None,
            situacao_rpv_id=self.situacao_empenho_id,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            resumo_operacional="",
            observacoes=None,
            ativo=True,
            criado_por_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        item.atualizar_campos_derivados()
        item.gerar_resumo_operacional(
            processo_edoc=dativo_ci.processo_edoc,
            data_ci=dativo_ci.data_ci,
        )
        db.session.add(item)
        db.session.commit()

        return dativo_ci, item

