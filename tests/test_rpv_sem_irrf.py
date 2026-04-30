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
    _cards_bi,
    _coletar_dataset_bi,
    _query_dativos_bi,
    _query_registros_bi,
    _resumo_grupos_cota,
    _resumo_irrf_bi,
    _series_grupos_cota_bi,
    dashboard_bp,
)
from app.routes.dativos import dativos_bp
from app.routes.historico import historico_bp
from app.routes.observability import observability_bp
from app.routes.reinf import _coletar_base_reinf, _query_dativos_reinf, _query_rpvs_reinf, reinf_bp
from app.routes.usuarios import usuarios_bp
from app.services.irrf_calculator import calcular_irrf_operacional
from app.services.notification_service import send_notification
from app.services.password_reset_service import PasswordResetService
from app.security import init_security
from app.utils.datetime_utils import utc_now_naive
from app.utils.formatters import formatar_documento_br, moeda_br
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


class RPVSemIRRFTestCase(unittest.TestCase):
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
            APP_RELEASE_LABEL="Atualizacao Operacional Atlas | Beta interna 2026.03 | Patch 002",
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

    def test_alerta_irrf_respeita_marcacao_sem_irrf(self):
        self.assertTrue(
            precisa_alerta_irrf("RPV honorários", Decimal("8000.00"), sem_irrf=False)
        )
        self.assertFalse(
            precisa_alerta_irrf("RPV honorários", Decimal("8000.00"), sem_irrf=True)
        )
        self.assertFalse(
            precisa_alerta_irrf("RPV pessoal", Decimal("8000.00"), sem_irrf=False)
        )
        self.assertFalse(
            precisa_alerta_irrf(
                "RPV honorários",
                Decimal("8000.00"),
                sem_irrf=False,
                valor_irrf=Decimal("800.00"),
            )
        )

    def test_calculo_irrf_operacional_reproduz_estudo_de_6000(self):
        resultado = calcular_irrf_operacional(
            competencia="2026-03",
            valor_bruto_tributavel="6000,00",
            documento="12345678901",
            tipo_documento="CPF",
            sem_irrf_forcado=False,
        )

        self.assertTrue(resultado.aplicavel)
        self.assertEqual(resultado.valor_irrf, Decimal("394.54"))
        self.assertEqual(resultado.valor_irrf_input, "394,54")
        self.assertFalse(resultado.sugerir_sem_irrf)

    def test_calculo_irrf_operacional_desconsidera_retencao_ate_dez_reais(self):
        resultado = calcular_irrf_operacional(
            competencia="2026-03",
            valor_bruto_tributavel="5020,00",
            documento="12345678901",
            tipo_documento="CPF",
            sem_irrf_forcado=False,
        )

        self.assertTrue(resultado.aplicavel)
        self.assertEqual(resultado.valor_irrf, Decimal("0.00"))
        self.assertTrue(resultado.sugerir_sem_irrf)
        self.assertTrue(resultado.desconsiderado_limite_minimo)

    def test_calculo_irrf_operacional_nao_aplica_para_cnpj(self):
        resultado = calcular_irrf_operacional(
            competencia="2026-03",
            valor_bruto_tributavel="6000,00",
            documento="12345678000199",
            tipo_documento="CNPJ",
            sem_irrf_forcado=False,
        )

        self.assertFalse(resultado.aplicavel)
        self.assertIn("apenas para CPF", resultado.resumo)

    def test_endpoint_rpv_calcula_irrf_sugerido(self):
        resposta = self.client.post(
            "/rpvs/calcular-irrf",
            json={
                "competencia": "2026-03",
                "valor_bruto": "6000,00",
                "documento": "12345678901",
                "tipo_documento": "CPF",
                "sem_irrf": False,
            },
        )
        payload = resposta.get_json()

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(payload["aplicavel"])
        self.assertEqual(payload["valor_irrf_input"], "394,54")
        self.assertEqual(payload["versao_regra"], "operacional_simplificado_2026_v1")

    def test_endpoint_dativo_calcula_irrf_sugerido(self):
        resposta = self.client.post(
            "/dativos/calcular-irrf",
            json={
                "competencia": "2026-03",
                "valor_bruto": "6000,00",
                "documento": "12345678901",
                "sem_irrf": False,
            },
        )
        payload = resposta.get_json()

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(payload["aplicavel"])
        self.assertEqual(payload["valor_irrf_input"], "394,54")

    def test_telas_exibem_acao_de_calculo_irrf_assistido(self):
        registro = self._criar_rpv(
            nome_beneficiario="RPV Com Calculo",
            valor_irrf=None,
        )
        dativo_ci, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Dativo Com Calculo",
            valor_irrf=None,
        )

        resposta_novo = self.client.get("/rpvs/novo")
        html_novo = resposta_novo.get_data(as_text=True)
        resposta_edicao = self.client.get(f"/rpvs/{registro.id}/editar")
        html_edicao = resposta_edicao.get_data(as_text=True)
        resposta_ci = self.client.get(f"/dativos/ci/{dativo_ci.id}")
        html_ci = resposta_ci.get_data(as_text=True)
        resposta_dativo = self.client.get(f"/dativos/itens-com-irrf/{item.id}")
        html_dativo = resposta_dativo.get_data(as_text=True)

        self.assertEqual(resposta_novo.status_code, 200)
        self.assertIn("Calcular IRRF sugerido", html_novo)
        self.assertIn("data-irrf-calculator", html_novo)
        self.assertEqual(resposta_edicao.status_code, 200)
        self.assertIn("Calcular IRRF sugerido", html_edicao)
        self.assertEqual(resposta_ci.status_code, 200)
        self.assertIn("Calcular IRRF sugerido", html_ci)
        self.assertEqual(resposta_dativo.status_code, 200)
        self.assertIn("Calcular IRRF sugerido", html_dativo)

    def test_shell_exibe_branding_siscon_e_modulos_renomeados(self):
        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("SISCON", html)
        self.assertIn("RPVs normais", html)
        self.assertIn("RPVs dativos", html)
        self.assertIn("Patch 002", html)
        self.assertNotIn("Cadastrar RPV", html)

    def test_login_exibe_branding_siscon(self):
        cliente_anonimo = self.app.test_client()
        resposta = cliente_anonimo.get("/login")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("<title>Login - SISCON</title>", html)
        self.assertIn("SISCON", html)
        self.assertIn("Acesse sua conta", html)
        self.assertIn("Esqueci minha senha", html)
        self.assertNotIn("Fluxo diario", html)
        self.assertNotIn("RPVs dativos", html)

    def test_esqueci_senha_envia_codigo_por_email_quando_usuario_tem_email(self):
        cliente_anonimo = self.app.test_client()

        resposta_identificacao = self._identificar_recuperacao(cliente_anonimo, "teste")
        html_identificacao = resposta_identificacao.get_data(as_text=True)

        self.assertEqual(resposta_identificacao.status_code, 200)
        self.assertIn("Escolha onde receber", html_identificacao)
        self.assertIn("te***@controle-rpv.local", html_identificacao)
        self.assertNotIn("Login, email ou telefone", html_identificacao)
        self.assertEqual(PasswordResetToken.query.count(), 0)

        resposta = cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": "email"},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Codigo registrado na caixa local", html)
        self.assertIn("Informe o codigo", html)
        self.assertEqual(PasswordResetToken.query.count(), 1)

        notificacao = self._ultima_notificacao()
        self.assertEqual(notificacao["channel"], "email")
        self.assertEqual(notificacao["destination"], "teste@controle-rpv.local")
        self.assertIn("codigo de recuperacao", notificacao["subject"].lower())
        self.assertRegex(notificacao["body"], r"\b\d{6}\b")

    def test_esqueci_senha_permite_escolher_sms_quando_usuario_tem_email_e_telefone(self):
        usuario = User(
            nome="Usuario Dois Canais",
            login="usuario.doiscanais",
            email="usuario.doiscanais@controle-rpv.local",
            telefone="79997776666",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("SenhaSms123")
        db.session.add(usuario)
        db.session.commit()

        cliente_anonimo = self.app.test_client()
        resposta_identificacao = self._identificar_recuperacao(cliente_anonimo, "usuario.doiscanais")
        html_identificacao = resposta_identificacao.get_data(as_text=True)

        self.assertEqual(resposta_identificacao.status_code, 200)
        self.assertIn("us***@controle-rpv.local", html_identificacao)
        self.assertIn("(**) *****-6666", html_identificacao)

        resposta = cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": "sms"},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Informe o codigo", html)
        self.assertEqual(PasswordResetToken.query.filter_by(user_id=usuario.id).count(), 1)

        notificacao = self._ultima_notificacao()
        self.assertEqual(notificacao["channel"], "sms")
        self.assertEqual(notificacao["destination"], "79997776666")
        self.assertRegex(notificacao["body"], r"\b\d{6}\b")

    def test_esqueci_senha_nao_cria_codigo_quando_canal_escolhido_nao_existe_no_cadastro(self):
        usuario = User(
            nome="Usuario Sem SMS",
            login="usuario.semsms",
            email="usuario.semsms@controle-rpv.local",
            telefone=None,
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("SenhaSms123")
        db.session.add(usuario)
        db.session.commit()

        cliente_anonimo = self.app.test_client()
        resposta_identificacao = self._identificar_recuperacao(cliente_anonimo, "usuario.semsms")
        html_identificacao = resposta_identificacao.get_data(as_text=True)

        self.assertEqual(resposta_identificacao.status_code, 200)
        self.assertIn("Email cadastrado", html_identificacao)
        self.assertNotIn("SMS cadastrado", html_identificacao)

        resposta = cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": "sms"},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Escolha um canal cadastrado", html)
        self.assertEqual(PasswordResetToken.query.filter_by(user_id=usuario.id).count(), 0)
        self.assertEqual(len(self._arquivos_notificacao()), 0)

    def test_esqueci_senha_envia_codigo_por_sms_quando_usuario_tem_so_telefone(self):
        usuario = User(
            nome="Usuario SMS",
            login="usuario.sms",
            email=None,
            telefone="79998887777",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("SenhaSms123")
        db.session.add(usuario)
        db.session.commit()

        cliente_anonimo = self.app.test_client()
        resposta_identificacao = self._identificar_recuperacao(cliente_anonimo, "usuario.sms")
        html_identificacao = resposta_identificacao.get_data(as_text=True)

        self.assertEqual(resposta_identificacao.status_code, 200)
        self.assertIn("SMS cadastrado", html_identificacao)
        self.assertIn("(**) *****-7777", html_identificacao)
        self.assertNotIn("Email cadastrado", html_identificacao)

        resposta = cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": "sms"},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Informe o codigo", html)
        self.assertEqual(PasswordResetToken.query.filter_by(user_id=usuario.id).count(), 1)

        notificacao = self._ultima_notificacao()
        self.assertEqual(notificacao["channel"], "sms")
        self.assertEqual(notificacao["destination"], "79998887777")
        self.assertRegex(notificacao["body"], r"\b\d{6}\b")

    def test_admin_visualiza_codigo_de_recuperacao_no_outbox_local(self):
        cliente_anonimo = self.app.test_client()
        self._enviar_codigo_recuperacao(cliente_anonimo, login="teste", channel="email", follow_redirects=False)
        notificacao = self._ultima_notificacao()
        codigo = self._extrair_codigo_notificacao(notificacao)

        self._autenticar(self.user_id)
        resposta = self.client.get("/usuarios/notificacoes-recuperacao")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Codigos de recuperacao", html)
        self.assertIn("teste@controle-rpv.local", html)
        self.assertIn(codigo, html)

    def test_brevo_api_envia_email_transacional(self):
        self.app.config.update(
            NOTIFICATION_DELIVERY_MODE="brevo_api",
            BREVO_API_KEY="xkeysib-teste",
            BREVO_API_URL="https://api.brevo.com/v3/smtp/email",
            BREVO_SENDER_EMAIL="nao-responda@siscon.local",
            BREVO_SENDER_NAME="SISCON",
        )

        with patch("app.services.notification_service.urlopen") as urlopen_mock:
            urlopen_mock.return_value = FakeHTTPResponse('{"messageId":"abc-123"}', status=201)
            resultado = send_notification(
                notification_type="password_reset_code",
                channel="email",
                destination="teste@controle-rpv.local",
                subject="SISCON | Codigo de recuperacao",
                body="Codigo 123456",
            )

        requisicao = urlopen_mock.call_args.args[0]
        payload = json.loads(requisicao.data.decode("utf-8"))

        self.assertEqual(resultado["mode"], "brevo_api")
        self.assertEqual(resultado["message_id"], "abc-123")
        self.assertEqual(requisicao.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(payload["sender"]["email"], "nao-responda@siscon.local")
        self.assertEqual(payload["to"][0]["email"], "teste@controle-rpv.local")
        self.assertEqual(payload["textContent"], "Codigo 123456")

    def test_sms_webhook_open_source_gammu_usa_payload_e_auth_basic(self):
        self.app.config.update(
            NOTIFICATION_DELIVERY_MODE="smtp",
            SMS_WEBHOOK_URL="http://127.0.0.1:5000/sms",
            SMS_WEBHOOK_AUTH_TYPE="basic",
            SMS_WEBHOOK_USERNAME="admin",
            SMS_WEBHOOK_PASSWORD="password",
            SMS_WEBHOOK_PAYLOAD_STYLE="gammu",
        )

        with patch("app.services.notification_service.urlopen") as urlopen_mock:
            urlopen_mock.return_value = FakeHTTPResponse("{}", status=200)
            resultado = send_notification(
                notification_type="password_reset_code",
                channel="sms",
                destination="79998887777",
                subject="SISCON | Codigo de recuperacao",
                body="SISCON: seu codigo e 123456.",
            )

        requisicao = urlopen_mock.call_args.args[0]
        payload = json.loads(requisicao.data.decode("utf-8"))

        self.assertEqual(resultado["mode"], "sms_webhook")
        self.assertEqual(requisicao.full_url, "http://127.0.0.1:5000/sms")
        self.assertEqual(payload, {"number": "79998887777", "text": "SISCON: seu codigo e 123456."})
        self.assertTrue(requisicao.get_header("Authorization").startswith("Basic "))

    def test_recuperacao_modo_brevo_exibe_apenas_email_configurado(self):
        self.app.config.update(
            NOTIFICATION_DELIVERY_MODE="brevo_api",
            BREVO_API_KEY="xkeysib-teste",
            BREVO_SENDER_EMAIL="nao-responda@siscon.local",
        )
        usuario = User(
            nome="Usuario Brevo",
            login="usuario.brevo",
            email="usuario.brevo@controle-rpv.local",
            telefone="79991112222",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("SenhaBrevo123")
        db.session.add(usuario)
        db.session.commit()

        cliente_anonimo = self.app.test_client()
        resposta = self._identificar_recuperacao(cliente_anonimo, "usuario.brevo")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Email cadastrado", html)
        self.assertIn("us***@controle-rpv.local", html)
        self.assertNotIn("SMS cadastrado", html)
        self.assertNotIn("(**) *****-2222", html)

    def test_esqueci_senha_mantem_resposta_generica_para_usuario_inexistente(self):
        cliente_anonimo = self.app.test_client()

        resposta = cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "identificar", "login": "nao.existe"},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Se o login informado estiver ativo", html)
        self.assertIn("Ambiente local", html)
        self.assertEqual(PasswordResetToken.query.count(), 0)
        self.assertEqual(len(self._arquivos_notificacao()), 0)
        self.assertIn("Continuar", html)
        self.assertNotIn("Escolha onde receber", html)

    def test_redefinicao_de_senha_consumo_codigo_e_permitem_novo_login(self):
        cliente_anonimo = self.app.test_client()
        self._enviar_codigo_recuperacao(cliente_anonimo, login="teste", channel="email", follow_redirects=False)

        notificacao = self._ultima_notificacao()
        codigo = self._extrair_codigo_notificacao(notificacao)

        resposta = cliente_anonimo.post(
            "/redefinir-senha/codigo",
            data={"codigo": codigo},
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/redefinir-senha/nova-senha", resposta.headers.get("Location", ""))

        resposta = cliente_anonimo.post(
            "/redefinir-senha/nova-senha",
            data={
                "nova_senha": "SenhaNova456",
                "confirmar_senha": "SenhaNova456",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/login", resposta.headers.get("Location", ""))

        usuario = User.query.filter_by(login="teste").first()
        self.assertTrue(usuario.check_password("SenhaNova456"))

        token = PasswordResetToken.query.one()
        self.assertIsNotNone(token.verificado_em)
        self.assertIsNotNone(token.utilizado_em)

        resposta_login = cliente_anonimo.post(
            "/login",
            data={"login": "teste", "senha": "SenhaNova456"},
            follow_redirects=False,
        )
        self.assertEqual(resposta_login.status_code, 302)

    def test_codigo_expirado_de_recuperacao_redireciona_para_nova_solicitacao(self):
        usuario = User.query.filter_by(login="teste").first()
        solicitacao, _, _, challenge_token = PasswordResetService.criar_solicitacao(
            usuario=usuario,
            request_ip="127.0.0.1",
            ttl_minutes=5,
        )
        db.session.flush()
        solicitacao.expira_em = utc_now_naive() - timedelta(minutes=1)
        db.session.commit()

        cliente_anonimo = self.app.test_client()
        self._preparar_sessao_recuperacao(
            cliente_anonimo,
            request_id=solicitacao.id,
            challenge_token=challenge_token,
            expires_at=solicitacao.expira_em,
            destino=solicitacao.destino_mascarado or "contato protegido cadastrado",
            channel=solicitacao.canal,
        )
        resposta = cliente_anonimo.get("/redefinir-senha/codigo", follow_redirects=True)
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Solicite um novo codigo", html)
        self.assertIn("Continuar", html)

    def test_codigo_invalido_bloqueia_solicitacao_apos_limite_de_tentativas(self):
        usuario = User.query.filter_by(login="teste").first()
        solicitacao, _, _, challenge_token = PasswordResetService.criar_solicitacao(
            usuario=usuario,
            request_ip="127.0.0.1",
            ttl_minutes=5,
        )
        db.session.commit()

        cliente_anonimo = self.app.test_client()
        self._preparar_sessao_recuperacao(
            cliente_anonimo,
            request_id=solicitacao.id,
            challenge_token=challenge_token,
            expires_at=solicitacao.expira_em,
            destino=solicitacao.destino_mascarado or "contato protegido cadastrado",
            channel=solicitacao.canal,
        )

        for tentativa in range(1, 5):
            resposta = cliente_anonimo.post(
                "/redefinir-senha/codigo",
                data={"codigo": "111111"},
                follow_redirects=True,
            )
            html = resposta.get_data(as_text=True)
            self.assertEqual(resposta.status_code, 200)
            self.assertIn("Codigo invalido", html)
            token = db.session.get(PasswordResetToken, solicitacao.id)
            self.assertEqual(token.tentativas_codigo, tentativa)
            self.assertIsNone(token.utilizado_em)

        resposta = cliente_anonimo.post(
            "/redefinir-senha/codigo",
            data={"codigo": "111111"},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Solicite um novo para continuar", html)

        token = db.session.get(PasswordResetToken, solicitacao.id)
        self.assertEqual(token.tentativas_codigo, 5)
        self.assertIsNotNone(token.utilizado_em)

    def test_nova_senha_exige_codigo_validado(self):
        cliente_anonimo = self.app.test_client()
        resposta = cliente_anonimo.get("/redefinir-senha/nova-senha", follow_redirects=True)
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("A validacao do codigo expirou", html)
        self.assertIn("Continuar", html)

    def test_novo_rpv_fica_contextualizado_em_rpvs_normais(self):
        resposta = self.client.get("/rpvs/novo")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("<title>Nova RPV normal - SISCON</title>", html)
        self.assertIn("Nova RPV normal", html)
        self.assertIn("Voltar para RPVs normais", html)
        self.assertIn("RPVs normais", html)
        self.assertNotIn("Cadastrar RPV", html)

    def test_novo_rpv_com_irrf_preenchido_nao_exige_confirmacao_de_pendencia(self):
        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-IRRF-PREENCHIDO",
                "numero_processo": "PROC-IRRF-PREENCHIDO",
                "data_ci": "2026-03-28",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "IRRF Ja Calculado",
                "tipo_documento": "CPF",
                "documento_original": "52998224725",
                "valor_bruto": "8000,00",
                "valor_irrf": "800,00",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)

        registro = RegistroRPV.query.filter_by(nome_beneficiario="IRRF Ja Calculado").first()
        self.assertIsNotNone(registro)
        self.assertEqual(registro.valor_irrf, Decimal("800.00"))

    def test_novo_rpv_invalido_vira_pendencia_documental_sem_criar_registro(self):
        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-DOC",
                "numero_processo": "PROC-PENDENTE-DOC",
                "data_ci": "2026-03-20",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Pendente",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Documento Pendente"
        ).one()

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            resposta.headers.get("Location", ""),
        )
        self.assertFalse(pendencia.documento_valido)
        self.assertEqual(pendencia.status, "aberta")
        self.assertIsNone(
            RegistroRPV.query.filter_by(nome_beneficiario="Documento Pendente").first()
        )

    def test_novo_rpv_sem_documento_vira_pendencia_documental_sem_criar_registro(self):
        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-SEM-DOCUMENTO",
                "numero_processo": "PROC-SEM-DOCUMENTO",
                "data_ci": "2026-03-20",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Beneficiario Sem Documento",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "4500,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Beneficiario Sem Documento"
        ).one()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(pendencia.status, "aberta")
        self.assertTrue(pendencia.documento_ausente)
        self.assertEqual(pendencia.documento_status_legivel, "Sem documento")
        self.assertFalse(pendencia.pode_continuar_fluxo_oficial)
        self.assertIsNone(
            RegistroRPV.query.filter_by(nome_beneficiario="Beneficiario Sem Documento").first()
        )

    def test_pendencia_documental_conferencia_manual_nao_vira_rpv_oficial_sem_documento_valido(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-CONF",
                "numero_processo": "PROC-PENDENTE-CONF",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Confirmado",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Documento Confirmado"
        ).one()

        resposta_confirmacao = self.client.post(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            data={
                "acao": "confirmar_documento",
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-CONF",
                "numero_processo": "PROC-PENDENTE-CONF",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Confirmado",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "elaborador_id": str(self.user_id),
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
                "observacoes": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(resposta_confirmacao.status_code, 302)

        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)
        self.assertTrue(pendencia.documento_confirmado_manual)
        self.assertFalse(pendencia.pode_continuar_fluxo_oficial)

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "pendencia_id": str(pendencia.id),
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-CONF",
                "numero_processo": "PROC-PENDENTE-CONF",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Confirmado",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(pendencia.status, "aberta")
        self.assertIsNone(pendencia.registro_rpv_convertido_id)
        self.assertIsNone(
            RegistroRPV.query.filter_by(nome_beneficiario="Documento Confirmado").first()
        )

    def test_pendencia_documental_com_documento_corrigido_vira_rpv_oficial(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-CORRIGE",
                "numero_processo": "PROC-PENDENTE-CORRIGE",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Corrigido",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Documento Corrigido"
        ).one()

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "pendencia_id": str(pendencia.id),
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-CORRIGE",
                "numero_processo": "PROC-PENDENTE-CORRIGE",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Corrigido",
                "tipo_documento": "CPF",
                "documento_original": "52998224725",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        registro = RegistroRPV.query.filter_by(nome_beneficiario="Documento Corrigido").one()
        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(pendencia.status, "convertida")
        self.assertEqual(pendencia.registro_rpv_convertido_id, registro.id)
        self.assertTrue(registro.sem_irrf)
        self.assertEqual(registro.documento_normalizado, "52998224725")

    def test_pendencia_documental_corrigida_fica_pronta_para_oficializar(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-PRONTA",
                "numero_processo": "PROC-PENDENTE-PRONTA",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Pronto",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )
        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Documento Pronto"
        ).one()

        resposta_salvar = self.client.post(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            data={
                "acao": "salvar",
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-PRONTA",
                "numero_processo": "PROC-PENDENTE-PRONTA",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Pronto",
                "tipo_documento": "CPF",
                "documento_original": "52998224725",
                "elaborador_id": str(self.user_id),
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
                "observacoes": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(resposta_salvar.status_code, 302)

        db.session.expire_all()
        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)
        self.assertEqual(pendencia.status, "aberta")
        self.assertTrue(pendencia.documento_valido)
        self.assertEqual(pendencia.status_legivel, "Pronto para oficializar")

        resposta_detalhe = self.client.get(f"/rpvs/pendencias-documentais/{pendencia.id}")
        html = resposta_detalhe.get_data(as_text=True)

        self.assertEqual(resposta_detalhe.status_code, 200)
        self.assertIn("Pronto para oficializar", html)
        self.assertIn("Confirmar e oficializar RPV", html)
        self.assertNotIn("Seguir para cadastro oficial", html)

    def test_novo_rpv_avisa_quando_processo_existe_em_pendencia_documental(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-CRUZAMENTO",
                "numero_processo": "PROC-PENDENTE-CRUZAMENTO",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Cruzamento Original",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-NOVO-CRUZAMENTO",
                "numero_processo": "PROC-PENDENTE-CRUZAMENTO",
                "data_ci": "2026-03-22",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Novo Com Processo Pendente",
                "tipo_documento": "CPF",
                "documento_original": "52998224725",
                "valor_bruto": "3500,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("RPV pendente", html)
        self.assertIn("Pendencia Cruzamento Original", html)
        self.assertIsNone(
            RegistroRPV.query.filter_by(nome_beneficiario="Novo Com Processo Pendente").first()
        )

    def test_lista_rpvs_busca_mostra_pendencia_documental_no_cruzamento(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PENDENTE-BUSCA",
                "numero_processo": "PROC-PENDENTE-BUSCA",
                "data_ci": "2026-03-21",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Busca Cruzada",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "3000,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        resposta = self.client.get("/rpvs/", query_string={"q": "PROC-PENDENTE-BUSCA"})
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendências documentais", html)
        self.assertIn("1 caso(s)", html)
        self.assertIn("pendência(s)", html)
        self.assertIn("Pendencia Busca Cruzada", html)
        self.assertIn("Abrir pendência", html)

    def test_lista_pendencias_documentais_filtra_todos_e_busca_por_processo(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-FILTRO",
                "numero_processo": "PROC-PENDENTE-FILTRO",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Filtro Documento",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "1800,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        resposta = self.client.get(
            "/rpvs/pendencias-documentais",
            query_string={"responsavel": "todos", "status": "todas", "q": "PROC-PENDENTE-FILTRO"},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendencia Filtro Documento", html)
        self.assertIn("PROC-PENDENTE-FILTRO", html)
        self.assertIn("Todos", html)

    def test_usuario_nao_admin_pode_ver_todos_e_tratar_pendencia_documental_de_outro_usuario(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-ACESSO",
                "numero_processo": "PROC-PENDENTE-ACESSO",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Acesso Restrito",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "1800,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )
        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Pendencia Acesso Restrito"
        ).one()

        usuario_sem_vinculo = User(
            nome="Usuario Sem Vinculo Pendencia",
            login="usuario.sem.vinculo.pendencia",
            email="usuario.sem.vinculo.pendencia@controle-rpv.local",
            telefone="61999990000",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario_sem_vinculo.set_password("Senha1234")
        db.session.add(usuario_sem_vinculo)
        db.session.commit()

        cliente_sem_vinculo = self.app.test_client()
        with cliente_sem_vinculo.session_transaction() as session:
            session["_user_id"] = str(usuario_sem_vinculo.id)
            session["_fresh"] = True
        g.pop("_login_user", None)

        resposta_lista = cliente_sem_vinculo.get(
            "/rpvs/pendencias-documentais",
            query_string={"responsavel": "todos", "status": "todas"},
        )
        html_lista = resposta_lista.get_data(as_text=True)
        self.assertEqual(resposta_lista.status_code, 200)
        self.assertIn("Pendencia Acesso Restrito", html_lista)
        self.assertIn("Todos", html_lista)

        resposta_detalhe = cliente_sem_vinculo.get(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            follow_redirects=False,
        )
        html_detalhe = resposta_detalhe.get_data(as_text=True)
        self.assertEqual(resposta_detalhe.status_code, 200)
        self.assertIn("Pendencia Acesso Restrito", html_detalhe)

        resposta_post = cliente_sem_vinculo.post(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            data={
                "acao": "confirmar_documento",
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-ACESSO",
                "numero_processo": "PROC-PENDENTE-ACESSO",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Acesso Restrito",
                "tipo_documento": "CPF",
                "documento_original": "",
                "elaborador_id": str(usuario_sem_vinculo.id),
                "valor_bruto": "1800,00",
                "valor_irrf": "",
                "sem_irrf": "1",
                "observacoes": "",
            },
            follow_redirects=False,
        )
        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)
        self.assertEqual(resposta_post.status_code, 302)
        self.assertEqual(pendencia.status, "aberta")
        self.assertTrue(pendencia.documento_confirmado_manual)
        self.assertEqual(pendencia.documento_confirmado_por_id, usuario_sem_vinculo.id)

    def test_pendencias_documentais_abrem_em_todos_por_padrao(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-PADRAO",
                "numero_processo": "PROC-PENDENTE-PADRAO",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Padrao Compartilhada",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "1800,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        usuario_sem_vinculo = User(
            nome="Usuario Padrao Pendencia",
            login="usuario.padrao.pendencia",
            email="usuario.padrao.pendencia@controle-rpv.local",
            telefone="61999990002",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario_sem_vinculo.set_password("Senha1234")
        db.session.add(usuario_sem_vinculo)
        db.session.commit()

        cliente_sem_vinculo = self.app.test_client()
        with cliente_sem_vinculo.session_transaction() as session:
            session["_user_id"] = str(usuario_sem_vinculo.id)
            session["_fresh"] = True
        g.pop("_login_user", None)

        resposta = cliente_sem_vinculo.get("/rpvs/pendencias-documentais")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('option value="todos" selected', html)
        self.assertIn("Pendencia Padrao Compartilhada", html)

    def test_busca_pendencias_documentais_sem_fila_explicita_varre_toda_a_fila(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "7413/2025",
                "numero_processo": "PROC-PENDENTE-BUSCA-COMPARTILHADA",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Busca Compartilhada",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "1800,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        usuario_sem_vinculo = User(
            nome="Usuario Busca Pendencia",
            login="usuario.busca.pendencia",
            email="usuario.busca.pendencia@controle-rpv.local",
            telefone="61999990003",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario_sem_vinculo.set_password("Senha1234")
        db.session.add(usuario_sem_vinculo)
        db.session.commit()

        cliente_sem_vinculo = self.app.test_client()
        with cliente_sem_vinculo.session_transaction() as session:
            session["_user_id"] = str(usuario_sem_vinculo.id)
            session["_fresh"] = True
        g.pop("_login_user", None)

        resposta = cliente_sem_vinculo.get(
            "/rpvs/pendencias-documentais",
            query_string={"status": "todas", "q": "7413/2025"},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('option value="todos" selected', html)
        self.assertIn("Pendencia Busca Compartilhada", html)
        self.assertIn("7413/2025", html)

    def test_usuario_nao_admin_pode_filtrar_pendencias_documentais_por_responsavel_especifico(self):
        usuario_secundario = User(
            nome="Usuario Filtro Pendencia",
            login="usuario.filtro.pendencia",
            email="usuario.filtro.pendencia@controle-rpv.local",
            telefone="61999990001",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-FILTRO-ADM",
                "numero_processo": "PROC-PENDENTE-FILTRO-ADM",
                "data_ci": "2026-04-08",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Filtro Admin",
                "tipo_documento": "CPF",
                "documento_original": "",
                "elaborador_id": str(self.user_id),
                "valor_bruto": "1900,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        self._autenticar(usuario_secundario.id)

        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-FILTRO-SEC",
                "numero_processo": "PROC-PENDENTE-FILTRO-SEC",
                "data_ci": "2026-04-08",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia Filtro Secundario",
                "tipo_documento": "CPF",
                "documento_original": "",
                "elaborador_id": str(usuario_secundario.id),
                "valor_bruto": "2100,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        resposta = self.client.get(
            "/rpvs/pendencias-documentais",
            query_string={"responsavel": str(self.user_id), "status": "todas"},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('option value="1" selected', html)
        self.assertIn("Pendencia Filtro Admin", html)
        self.assertNotIn("Pendencia Filtro Secundario", html)

    def test_bi_conferencia_exibe_documentos_pendentes_sem_somar_com_pagamentos(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-BI",
                "numero_processo": "PROC-PENDENTE-BI",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pendencia BI Documento",
                "tipo_documento": "CPF",
                "documento_original": "",
                "valor_bruto": "1234,56",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        resposta = self.client.get("/bi", query_string={"visao": "conferencia"})
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Conferencia dos cadastros fora do fluxo oficial", html)
        self.assertIn("Pendencia BI Documento", html)
        self.assertIn("PROC-PENDENTE-BI", html)
        self.assertIn("CI-PENDENTE-BI", html)
        self.assertIn("1.234,56", html)
        self.assertIn("Nenhum pagamento efetivo encontrou esse conjunto de filtros para a conferencia.", html)

    def test_pendencia_documental_pode_ser_cancelada_e_reaberta_sem_apagar_historico(self):
        self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-CANCELA",
                "numero_processo": "PROC-PENDENTE-CANCELA",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Cancelavel",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "valor_bruto": "200,00",
                "valor_irrf": "",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        pendencia = RPVPendenciaDocumento.query.filter_by(
            nome_beneficiario="Documento Cancelavel"
        ).one()

        resposta_cancelamento = self.client.post(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            data={
                "acao": "descartar",
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-CANCELA",
                "numero_processo": "PROC-PENDENTE-CANCELA",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Cancelavel",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "elaborador_id": str(self.user_id),
                "valor_bruto": "200,00",
                "valor_irrf": "",
                "sem_irrf": "1",
                "observacoes": "",
            },
            follow_redirects=False,
        )

        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)
        self.assertEqual(resposta_cancelamento.status_code, 302)
        self.assertEqual(pendencia.status, "descartada")

        resposta_reabertura = self.client.post(
            f"/rpvs/pendencias-documentais/{pendencia.id}",
            data={
                "acao": "reabrir_pendencia",
                "exercicio": "2026-04",
                "processo_edoc": "CI-PENDENTE-CANCELA",
                "numero_processo": "PROC-PENDENTE-CANCELA",
                "data_ci": "2026-04-07",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Documento Cancelavel",
                "tipo_documento": "CPF",
                "documento_original": "12345678901",
                "elaborador_id": str(self.user_id),
                "valor_bruto": "200,00",
                "valor_irrf": "",
                "sem_irrf": "1",
                "observacoes": "",
            },
            follow_redirects=False,
        )

        pendencia = db.session.get(RPVPendenciaDocumento, pendencia.id)
        self.assertEqual(resposta_reabertura.status_code, 302)
        self.assertEqual(pendencia.status, "aberta")

    def test_resumo_operacional_indica_irrf_pendente_quando_nao_esta_marcado_sem_irrf(self):
        registro = self._criar_rpv(
            nome_beneficiario="Beneficiario Pendente",
            valor_irrf=None,
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )

        self.assertIn("IRRF PENDENTE", registro.resumo_operacional)
        self.assertIn("IRRF PENDENTE", registro.historico_auto)
        self.assertIn("10/03/2026", registro.resumo_operacional)
        self.assertIn("10/03/2026", registro.historico_auto)
        self.assertIn(
            f"_{registro.nome_beneficiario}_10/03/2026_IRRF PENDENTE",
            registro.resumo_operacional,
        )
        self.assertFalse(registro.sem_irrf_efetivo)

    def test_dativo_item_resumo_operacional_inclui_data_da_ci(self):
        _, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-DATIVO-DATA",
            nome_beneficiario="Beneficiario com Data CI",
            numero_processo="PROC-DATIVO-DATA",
            valor_bruto=Decimal("6200.00"),
            valor_irrf=Decimal("620.00"),
        )

        self.assertIn("10/03/2026", item.resumo_operacional)
        self.assertIn("CI-DATIVO-DATA", item.resumo_operacional)
        self.assertIn("PROC-DATIVO-DATA", item.resumo_operacional)
        self.assertIn(
            "_Beneficiario com Data CI_10/03/2026_IRRF 620,00",
            item.resumo_operacional,
        )

    def test_rpv_reconhece_cnpj_mesmo_com_tipo_documento_inicial_diferente(self):
        registro = self._criar_rpv(
            nome_beneficiario="Empresa Documento",
            documento_original="12345678000199",
        )

        self.assertEqual(registro.tipo_documento, "CNPJ")
        self.assertEqual(registro.tipo_documento_efetivo, "CNPJ")
        self.assertEqual(registro.documento_normalizado, "12345678000199")
        self.assertEqual(registro.documento_formatado, formatar_documento_br("12345678000199"))

    def test_dativo_reconhece_cnpj_automaticamente_quando_importado_sem_mascara(self):
        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Empresa Dativo",
            cpf_original="98765432000155",
        )

        self.assertEqual(item.tipo_documento_efetivo, "CNPJ")
        self.assertEqual(item.cpf_normalizado, "98765432000155")
        self.assertEqual(item.documento_formatado, formatar_documento_br("98765432000155"))

    def test_admin_pode_criar_usuario(self):
        resposta = self.client.post(
            "/usuarios/novo",
            data={
                "nome": "Operador Novo",
                "login": "operador.novo",
                "senha": "Senha@123",
                "ativo": "1",
                "is_admin": "",
                "forcar_troca_senha": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        usuario = User.query.filter_by(login="operador.novo").first()
        self.assertIsNotNone(usuario)
        self.assertEqual(usuario.nome, "Operador Novo")
        self.assertIsNone(usuario.email)
        self.assertTrue(usuario.ativo)
        self.assertFalse(usuario.is_admin)
        self.assertTrue(usuario.check_password("Senha@123"))
        self.assertTrue(usuario.forcar_troca_senha)
        self.assertTrue(usuario.perfil_pendente)

    def test_admin_pode_editar_usuario_e_redefinir_senha(self):
        usuario = User(
            nome="Operador Antigo",
            login="operador.antigo",
            email="operador.antigo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("SenhaAntiga")
        db.session.add(usuario)
        db.session.commit()

        resposta = self.client.post(
            f"/usuarios/{usuario.id}/editar",
            data={
                "nome": "Operador Atualizado",
                "login": "operador.atualizado",
                "email": "operador.atualizado@controle-rpv.local",
                "telefone": "61999990000",
                "cargo": "Analista",
                "setor": "RPV",
                "senha": "SenhaNova@123",
                "ativo": "1",
                "is_admin": "1",
                "forcar_troca_senha": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        usuario_atualizado = db.session.get(User, usuario.id)
        self.assertEqual(usuario_atualizado.nome, "Operador Atualizado")
        self.assertEqual(usuario_atualizado.login, "operador.atualizado")
        self.assertEqual(usuario_atualizado.email, "operador.atualizado@controle-rpv.local")
        self.assertEqual(usuario_atualizado.telefone, "61999990000")
        self.assertEqual(usuario_atualizado.cargo, "Analista")
        self.assertEqual(usuario_atualizado.setor, "RPV")
        self.assertTrue(usuario_atualizado.is_admin)
        self.assertTrue(usuario_atualizado.check_password("SenhaNova@123"))
        self.assertTrue(usuario_atualizado.forcar_troca_senha)

    def test_admin_normaliza_telefone_formatado_no_cadastro_de_usuario(self):
        usuario = User(
            nome="Operador Telefone",
            login="operador.telefone",
            email="operador.telefone@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("SenhaAntiga")
        db.session.add(usuario)
        db.session.commit()

        resposta = self.client.post(
            f"/usuarios/{usuario.id}/editar",
            data={
                "nome": "Operador Telefone",
                "login": "operador.telefone",
                "email": "operador.telefone@controle-rpv.local",
                "telefone": "(79) 99888-7777",
                "cargo": "Analista",
                "setor": "RPV",
                "senha": "",
                "ativo": "1",
                "is_admin": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        usuario_atualizado = db.session.get(User, usuario.id)
        self.assertEqual(usuario_atualizado.telefone, "79998887777")

        resposta_form = self.client.get(f"/usuarios/{usuario.id}/editar")
        html_form = resposta_form.get_data(as_text=True)
        self.assertIn("(79) 99888-7777", html_form)

    def test_usuario_nao_admin_nao_acessa_gestao_de_usuarios(self):
        usuario = User(
            nome="Usuário Comum",
            login="usuario.comum",
            email="usuario.comum@controle-rpv.local",
            telefone="61999990000",
            cargo="Analista",
            setor="RPV",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha@123")
        db.session.add(usuario)
        db.session.commit()

        self._autenticar(usuario.id)

        resposta = self.client.get("/usuarios/", follow_redirects=False)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/", resposta.headers.get("Location", ""))

    def test_admin_nao_pode_desativar_o_proprio_usuario(self):
        resposta = self.client.post(
            f"/usuarios/{self.user_id}/editar",
            data={
                "nome": "Usuário Teste",
                "login": "teste",
                "email": "teste@controle-rpv.local",
                "senha": "",
                "ativo": "",
                "is_admin": "1",
            },
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Você não pode desativar o seu próprio usuário.", html)
        usuario = db.session.get(User, self.user_id)
        self.assertTrue(usuario.ativo)

    def test_login_redireciona_para_troca_obrigatoria_de_senha(self):
        cliente = self.app.test_client()
        usuario = User(
            nome="Usuário Forçado",
            login="usuario.forcado",
            email="usuario.forcado@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha1234")
        usuario.forcar_troca_senha = True
        db.session.add(usuario)
        db.session.commit()

        resposta = cliente.post(
            "/login",
            data={
                "login": "USUARIO.FORCADO",
                "senha": "Senha1234",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/usuarios/minha-senha", resposta.headers.get("Location", ""))
        usuario_atualizado = db.session.get(User, usuario.id)
        self.assertIsNotNone(usuario_atualizado.ultimo_login_em)
        self.assertTrue(usuario_atualizado.ultimo_login_ip)

    def test_usuario_com_troca_obrigatoria_e_redirecionado_na_navegacao(self):
        usuario = User(
            nome="Usuário Bloqueado",
            login="usuario.bloqueado",
            email="usuario.bloqueado@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha1234")
        usuario.forcar_troca_senha = True
        db.session.add(usuario)
        db.session.commit()

        self._autenticar(usuario.id)

        resposta = self.client.get("/", follow_redirects=False)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/usuarios/minha-senha", resposta.headers.get("Location", ""))

    def test_minha_senha_atualiza_credenciais_e_libera_usuario(self):
        usuario = User(
            nome="Usuário Senha",
            login="usuario.senha",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha1234")
        usuario.forcar_troca_senha = True
        db.session.add(usuario)
        db.session.commit()

        self._autenticar(usuario.id)

        resposta = self.client.post(
            "/usuarios/minha-senha",
            data={
                "nova_senha": "SenhaNova123",
                "confirmar_senha": "SenhaNova123",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/usuarios/meu-cadastro", resposta.headers.get("Location", ""))
        usuario_atualizado = db.session.get(User, usuario.id)
        self.assertTrue(usuario_atualizado.check_password("SenhaNova123"))
        self.assertFalse(usuario_atualizado.forcar_troca_senha)
        self.assertIsNotNone(usuario_atualizado.senha_alterada_em)

    def test_login_redireciona_para_completar_cadastro_quando_perfil_esta_pendente(self):
        cliente = self.app.test_client()
        usuario = User(
            nome="Usuário Perfil",
            login="usuario.perfil",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha1234")
        db.session.add(usuario)
        db.session.commit()

        resposta = cliente.post(
            "/login",
            data={
                "login": "usuario.perfil",
                "senha": "Senha1234",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/usuarios/meu-cadastro", resposta.headers.get("Location", ""))

    def test_meu_cadastro_completa_perfil_e_libera_usuario(self):
        usuario = User(
            nome="Usuário Completar",
            login="usuario.completar",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha1234")
        db.session.add(usuario)
        db.session.commit()

        self._autenticar(usuario.id)

        resposta = self.client.post(
            "/usuarios/meu-cadastro",
            data={
                "nome": "Usuário Completar Silva",
                "email": "usuario.completar@controle-rpv.local",
                "telefone": "61988887777",
                "cargo": "Assistente",
                "setor": "RPV",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/", resposta.headers.get("Location", ""))
        usuario_atualizado = db.session.get(User, usuario.id)
        self.assertEqual(usuario_atualizado.email, "usuario.completar@controle-rpv.local")
        self.assertEqual(usuario_atualizado.telefone, "61988887777")
        self.assertEqual(usuario_atualizado.cargo, "Assistente")
        self.assertEqual(usuario_atualizado.setor, "RPV")
        self.assertFalse(usuario_atualizado.perfil_pendente)

    def test_meu_cadastro_rejeita_telefone_sem_ddd(self):
        usuario = User(
            nome="Usuário Telefone Inválido",
            login="usuario.telefone.invalido",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("Senha1234")
        db.session.add(usuario)
        db.session.commit()

        self._autenticar(usuario.id)

        resposta = self.client.post(
            "/usuarios/meu-cadastro",
            data={
                "nome": "Usuário Telefone Inválido",
                "email": "usuario.telefone.invalido@controle-rpv.local",
                "telefone": "9888-7777",
                "cargo": "Assistente",
                "setor": "RPV",
            },
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Informe um telefone com DDD", html)
        usuario_atualizado = db.session.get(User, usuario.id)
        self.assertIsNone(usuario_atualizado.telefone)

    def test_cadastro_salva_sem_irrf_explicito_no_rpv_normal(self):
        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-CADASTRO-1",
                "numero_processo": "PROC-CADASTRO-1",
                "data_ci": "2026-03-10",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Cadastro Sem IRRF",
                "tipo_documento": "CPF",
                "documento_original": "28001238938",
                "valor_bruto": "8000,00",
                "valor_irrf": "",
                "data_pagamento": "2026-03-15",
                "sem_irrf": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)

        registro = RegistroRPV.query.filter_by(nome_beneficiario="Cadastro Sem IRRF").first()
        self.assertIsNotNone(registro)
        self.assertTrue(registro.sem_irrf)
        self.assertTrue(registro.sem_irrf_efetivo)
        self.assertIsNone(registro.valor_irrf)
        self.assertEqual(registro.situacao_imposto.nome, "Sem IRRF")

    def test_lista_rpvs_abre_andamento_preservando_filtro_atual_no_retorno(self):
        registro = self._criar_rpv(
            nome_beneficiario="Retorno Filtro Atual",
            documento_original="52998224725",
            numero_processo="PROC-RETORNO-FILTRO",
        )

        resposta = self.client.get(
            "/rpvs/?q=Retorno&responsavel=todos&mostrar_encerrados=1"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(f"/rpvs/{registro.id}/editar?retorno=", html)
        self.assertIn("q%3DRetorno", html)
        self.assertIn("responsavel%3Dtodos", html)
        self.assertIn("mostrar_encerrados%3D1", html)

    def test_edicao_rpv_retorna_para_filtro_de_origem(self):
        registro = self._criar_rpv(
            nome_beneficiario="Retorno Para Origem",
            documento_original="98765432100",
            numero_processo="PROC-RETORNO-ORIGEM",
        )

        retorno = "/rpvs/?q=Origem&responsavel=todos&mostrar_encerrados=1"
        resposta = self.client.get(
            f"/rpvs/{registro.id}/editar",
            query_string={"retorno": retorno},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(
            'href="/rpvs/?q=Origem&amp;responsavel=todos&amp;mostrar_encerrados=1">Retornar',
            html,
        )
        self.assertIn(
            'href="/rpvs/?q=Origem&amp;responsavel=todos&amp;mostrar_encerrados=1">Cancelar',
            html,
        )
        self.assertIn(
            f'action="/rpvs/{registro.id}/transferir-responsavel?retorno=/rpvs/?q%3DOrigem%26responsavel%3Dtodos%26mostrar_encerrados%3D1"',
            html,
        )

    def test_edicao_rpv_preserva_retorno_apos_salvar(self):
        registro = self._criar_rpv(
            nome_beneficiario="Retorno Apos Salvar",
            documento_original="39053344705",
            numero_processo="PROC-RETORNO-SALVAR",
        )

        retorno = "/rpvs/?q=Salvar&responsavel=todos&mostrar_encerrados=1"
        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            query_string={"retorno": retorno},
            data={
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": registro.nome_beneficiario,
                "tipo_documento": registro.tipo_documento,
                "documento_original": registro.documento_original,
                "exercicio": registro.processo.exercicio,
                "valor_bruto": str(registro.valor_bruto),
                "valor_irrf": "",
                "nota_empenho": "",
                "numero_se": "",
                "ordem_bancaria": "",
                "situacao_empenho_id": str(registro.situacao_empenho_id),
                "situacao_imposto_id": str(registro.situacao_imposto_id),
                "data_pagamento": "",
                "data_pagamento_irrf": "",
                "ob_imposto": "",
                "observacoes": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        location = resposta.headers["Location"]
        self.assertIn(f"/rpvs/{registro.id}/editar?retorno=/rpvs/", location)
        self.assertIn("q%3DSalvar", location)
        self.assertIn("responsavel%3Dtodos", location)
        self.assertIn("mostrar_encerrados%3D1", location)

    def test_edicao_rpv_ignora_retorno_externo(self):
        registro = self._criar_rpv(
            nome_beneficiario="Retorno Externo Bloqueado",
            documento_original="39053344705",
            numero_processo="PROC-RETORNO-EXTERNO",
        )

        resposta = self.client.get(
            f"/rpvs/{registro.id}/editar",
            query_string={"retorno": "https://exemplo.invalid/rpvs"},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('href="/rpvs/">Retornar', html)
        self.assertNotIn("exemplo.invalid", html)

    def test_lista_dativos_preserva_retorno_para_lote_sem_irrf(self):
        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-RETORNO-DATIVO",
            itens=[
                {
                    "nome_beneficiario": "Dativo Retorno",
                    "cpf_original": "52998224725",
                    "numero_processo": "PROC-DATIVO-RETORNO",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )

        resposta = self.client.get(
            "/dativos/cis?q=Retorno&responsavel=todos&mostrar_encerrados=1"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(f"/dativos/lotes-sem-irrf/{lote.id}?retorno=", html)
        self.assertIn("q%3DRetorno", html)
        self.assertIn("responsavel%3Dtodos", html)
        self.assertIn("mostrar_encerrados%3D1", html)

    def test_lotes_dativos_retorna_para_filtro_de_origem(self):
        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-RETORNO-LOTE",
            itens=[
                {
                    "nome_beneficiario": "Lote Retorno",
                    "cpf_original": "98765432100",
                    "numero_processo": "PROC-LOTE-RETORNO",
                    "valor_bruto": Decimal("2200.00"),
                }
            ],
        )

        retorno = "/dativos/lotes-sem-irrf?q=Retorno&responsavel=todos&mostrar_encerrados=1"
        resposta = self.client.get(
            f"/dativos/lotes-sem-irrf/{lote.id}",
            query_string={"retorno": retorno},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(
            'href="/dativos/lotes-sem-irrf?q=Retorno&amp;responsavel=todos&amp;mostrar_encerrados=1">Retornar',
            html,
        )
        self.assertIn(
            f'action="/dativos/lotes-sem-irrf/{lote.id}/salvar?retorno=/dativos/lotes-sem-irrf?q%3DRetorno%26responsavel%3Dtodos%26mostrar_encerrados%3D1"',
            html,
        )

    def test_itens_dativos_retorna_para_filtro_de_origem(self):
        _, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-RETORNO-ITEM",
            nome_beneficiario="Item Retorno",
            cpf_original="39053344705",
            numero_processo="PROC-ITEM-RETORNO",
        )

        resposta_lista = self.client.get(
            "/dativos/itens-com-irrf?q=Retorno&responsavel=todos&mostrar_encerrados=1"
        )
        html_lista = resposta_lista.get_data(as_text=True)

        self.assertEqual(resposta_lista.status_code, 200)
        self.assertIn(f"/dativos/itens-com-irrf/{item.id}?retorno=", html_lista)
        self.assertIn("q%3DRetorno", html_lista)
        self.assertIn("responsavel%3Dtodos", html_lista)

        retorno = "/dativos/itens-com-irrf?q=Retorno&responsavel=todos&mostrar_encerrados=1"
        resposta = self.client.get(
            f"/dativos/itens-com-irrf/{item.id}",
            query_string={"retorno": retorno},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(
            'href="/dativos/itens-com-irrf?q=Retorno&amp;responsavel=todos&amp;mostrar_encerrados=1">Retornar',
            html,
        )
        self.assertIn(
            f'action="/dativos/itens-com-irrf/{item.id}/salvar?retorno=/dativos/itens-com-irrf?q%3DRetorno%26responsavel%3Dtodos%26mostrar_encerrados%3D1"',
            html,
        )

    def test_busca_cruzada_em_rpvs_retorna_para_lista_original_ao_abrir_dativo(self):
        _, lote, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-CRUZADO-RETORNO",
            itens=[
                {
                    "nome_beneficiario": "Dativo Cruzado Retorno",
                    "cpf_original": "52998224725",
                    "numero_processo": "PROC-CRUZADO-RETORNO",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )
        item = itens[0]
        self._criar_rpv(
            nome_beneficiario="RPV Cruzado Retorno",
            documento_original="98765432100",
            numero_processo=item.numero_processo,
        )

        resposta_lista = self.client.get(
            "/rpvs/?q=PROC-CRUZADO-RETORNO&responsavel=todos&mostrar_encerrados=1"
        )
        html_lista = resposta_lista.get_data(as_text=True)

        self.assertEqual(resposta_lista.status_code, 200)
        self.assertIn(
            f"/dativos/lotes-sem-irrf/{lote.id}?retorno=%2Frpvs%2F%3Fq%3DPROC-CRUZADO-RETORNO%26responsavel%3Dtodos%26mostrar_encerrados%3D1",
            html_lista,
        )

        resposta_detalhe = self.client.get(
            f"/dativos/lotes-sem-irrf/{lote.id}",
            query_string={
                "retorno": "/rpvs/?q=PROC-CRUZADO-RETORNO&responsavel=todos&mostrar_encerrados=1"
            },
        )
        html_detalhe = resposta_detalhe.get_data(as_text=True)

        self.assertEqual(resposta_detalhe.status_code, 200)
        self.assertIn(
            'href="/rpvs/?q=PROC-CRUZADO-RETORNO&amp;responsavel=todos&amp;mostrar_encerrados=1">Retornar',
            html_detalhe,
        )

    def test_detalhe_dativo_ignora_retorno_externo(self):
        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-RETORNO-EXTERNO",
            itens=[
                {
                    "nome_beneficiario": "Dativo Retorno Externo",
                    "cpf_original": "52998224725",
                    "numero_processo": "PROC-DATIVO-RETORNO-EXTERNO",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )

        resposta = self.client.get(
            f"/dativos/lotes-sem-irrf/{lote.id}",
            query_string={"retorno": "https://exemplo.invalid/dativos"},
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('href="/dativos/cis">Retornar', html)
        self.assertNotIn("exemplo.invalid", html)

    def test_reinf_preserva_retorno_ao_abrir_rpv(self):
        registro = self._criar_rpv(
            nome_beneficiario="Retorno REINF",
            documento_original="39053344705",
            numero_processo="PROC-RETORNO-REINF",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get(
            "/reinf/?competencia=2026-03&reinf_status=todos&q=Retorno&responsavel=todos&ordenar=imposto&direcao=desc"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(f"/rpvs/{registro.id}/editar?retorno=", html)
        href_inicio = html.index(f'/rpvs/{registro.id}/editar?retorno=')
        href_fim = html.index('"', href_inicio)
        abrir_href = html[href_inicio:href_fim]
        retorno = parse_qs(urlsplit(abrir_href).query).get("retorno", [""])[0]

        self.assertTrue(retorno.startswith("/reinf/?competencia=2026-03"))
        self.assertIn("q=Retorno", retorno)
        self.assertIn("responsavel=todos", retorno)
        self.assertIn("ordenar=imposto", retorno)
        self.assertIn("direcao=desc", retorno)

    def test_reinf_atualizacao_status_preserva_retorno_explicito(self):
        registro = self._criar_rpv(
            nome_beneficiario="Status REINF Retorno",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        retorno = "/reinf/?competencia=2026-03&reinf_status=todos&q=Retorno&responsavel=todos"

        resposta = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "reinf_status": "Concluído",
                "retorno": retorno,
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["Location"], retorno)

    def test_reinf_atualizacao_status_ignora_retorno_externo(self):
        registro = self._criar_rpv(
            nome_beneficiario="Status REINF Externo",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )

        resposta = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "reinf_status": "Concluído",
                "retorno": "https://exemplo.invalid/reinf",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["Location"], "/reinf/")

    def test_edicao_sem_irrf_oculta_fluxo_fiscal_do_rpv(self):
        registro = self._criar_rpv(
            nome_beneficiario="RPV Sem Fiscal",
            sem_irrf=True,
            valor_irrf=None,
        )

        resposta = self.client.get(f"/rpvs/{registro.id}/editar")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Fluxo fiscal não se aplica", html)
        self.assertNotIn("Pagamento do IRRF", html)
        self.assertNotIn('name="situacao_imposto_id"', html)

    def test_novo_rpv_nao_exibe_data_pagamento_no_cadastro_inicial(self):
        resposta = self.client.get("/rpvs/novo")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pagamento lançado depois", html)
        self.assertNotIn('id="data_pagamento"', html)

    def test_login_rejeita_post_sem_csrf_quando_habilitado(self):
        self.app.config["CSRF_ENABLED"] = True
        client = self.app.test_client()

        resposta = client.post(
            "/login",
            data={"login": "teste", "senha": "senha123"},
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Token CSRF invalido ou ausente.", resposta.get_data(as_text=True))

    def test_login_aceita_post_com_csrf_quando_habilitado(self):
        self.app.config["CSRF_ENABLED"] = True
        client = self.app.test_client()

        client.get("/login")
        with client.session_transaction() as session:
            token = session.get("_csrf_token")

        resposta = client.post(
            "/login",
            data={
                "login": "teste",
                "senha": "senha123",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)

    def test_login_renderiza_formulario_com_campo_csrf(self):
        self.app.config["CSRF_ENABLED"] = True
        client = self.app.test_client()

        resposta = client.get("/login")

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('name="csrf_token"', resposta.get_data(as_text=True))

    def test_login_aplica_throttle_minimo_apos_falhas_repetidas(self):
        self.app.config.update(
            LOGIN_THROTTLE_MAX_FAILURES=2,
            LOGIN_THROTTLE_WINDOW_SECONDS=60,
        )
        client = self.app.test_client()
        ip_origem = {"REMOTE_ADDR": "172.20.10.25"}

        for _ in range(2):
            client.post(
                "/login",
                data={"login": "teste", "senha": "senha-errada"},
                follow_redirects=True,
                environ_overrides=ip_origem,
            )

        resposta = client.post(
            "/login",
            data={"login": "teste", "senha": "senha123"},
            follow_redirects=True,
            environ_overrides=ip_origem,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Muitas tentativas de login", html)
        self.assertIn("Acesse sua conta", html)

    def test_login_throttle_persiste_em_armazenamento_sqlite_apos_reset_local(self):
        self.app.config.update(
            LOGIN_THROTTLE_MAX_FAILURES=2,
            LOGIN_THROTTLE_WINDOW_SECONDS=60,
            REQUEST_THROTTLE_BACKEND="sqlite",
        )
        client = self.app.test_client()
        ip_origem = {"REMOTE_ADDR": "172.20.10.26"}

        for _ in range(2):
            client.post(
                "/login",
                data={"login": "teste", "senha": "senha-errada"},
                follow_redirects=True,
                environ_overrides=ip_origem,
            )

        with patch("app.utils.request_throttle.has_app_context", return_value=False):
            request_throttle.clear_all()

        resposta = client.post(
            "/login",
            data={"login": "teste", "senha": "senha123"},
            follow_redirects=True,
            environ_overrides=ip_origem,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Muitas tentativas de login", html)
        self.assertTrue((Path(self.instance_dir) / "request_throttle.sqlite3").exists())

    def test_recuperacao_aplica_throttle_minimo_para_reenvio_de_codigo(self):
        self.app.config.update(
            PASSWORD_RESET_SEND_THROTTLE_MAX_ATTEMPTS=1,
            PASSWORD_RESET_SEND_THROTTLE_WINDOW_SECONDS=600,
        )
        ip_origem = {"REMOTE_ADDR": "172.20.10.30"}

        cliente_anonimo = self.app.test_client()
        cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "identificar", "login": "teste"},
            environ_overrides=ip_origem,
        )
        resposta_primeiro_envio = cliente_anonimo.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": "email"},
            follow_redirects=True,
            environ_overrides=ip_origem,
        )

        self.assertEqual(resposta_primeiro_envio.status_code, 200)
        self.assertEqual(PasswordResetToken.query.count(), 1)

        cliente_bloqueado = self.app.test_client()
        cliente_bloqueado.post(
            "/esqueci-senha",
            data={"form_step": "identificar", "login": "teste"},
            environ_overrides=ip_origem,
        )
        resposta_bloqueio = cliente_bloqueado.post(
            "/esqueci-senha",
            data={"form_step": "enviar_codigo", "canal_recuperacao": "email"},
            follow_redirects=True,
            environ_overrides=ip_origem,
        )
        html = resposta_bloqueio.get_data(as_text=True)

        self.assertEqual(resposta_bloqueio.status_code, 200)
        self.assertIn("acabou de solicitar codigo de recuperacao", html)
        self.assertEqual(PasswordResetToken.query.count(), 1)
        self.assertEqual(len(self._arquivos_notificacao()), 1)

    def test_request_throttle_sqlite_limpa_hits_antigos_globalmente(self):
        self.app.config.update(
            REQUEST_THROTTLE_BACKEND="sqlite",
            REQUEST_THROTTLE_GC_INTERVAL_SECONDS=1,
            REQUEST_THROTTLE_GC_MAX_AGE_SECONDS=3600,
        )
        throttle_path = Path(self.instance_dir) / "request_throttle.sqlite3"
        request_throttle.clear_all()
        request_throttle.hit("bucket-ativo", window_seconds=60)

        with sqlite3.connect(str(throttle_path)) as connection:
            connection.execute(
                "INSERT INTO throttle_hits (bucket_key, hit_at) VALUES (?, ?)",
                ("bucket-antigo", time() - 7200),
            )
            connection.execute(
                "INSERT OR REPLACE INTO throttle_meta (meta_key, value) VALUES (?, ?)",
                ("last_global_cleanup_at", str(time() - 10)),
            )

        decisao = request_throttle.check("bucket-ativo", limit=5, window_seconds=60)

        self.assertTrue(decisao.allowed)
        with sqlite3.connect(str(throttle_path)) as connection:
            antigo = connection.execute(
                "SELECT 1 FROM throttle_hits WHERE bucket_key = ?",
                ("bucket-antigo",),
            ).fetchone()
            ativo = connection.execute(
                "SELECT 1 FROM throttle_hits WHERE bucket_key = ?",
                ("bucket-ativo",),
            ).fetchone()
            meta = connection.execute(
                "SELECT value FROM throttle_meta WHERE meta_key = ?",
                ("last_global_cleanup_at",),
            ).fetchone()

        self.assertIsNone(antigo)
        self.assertIsNotNone(ativo)
        self.assertIsNotNone(meta)

    def test_healthcheck_basico_responde_ok_com_request_id(self):
        resposta = self.client.get("/health")
        payload = resposta.get_json()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("X-Request-ID", resposta.headers)
        checks = {check["component"]: check for check in payload["checks"]}
        throttle_check = checks["request_throttle"]
        self.assertEqual(checks["database"]["status"], "ok")
        self.assertEqual(throttle_check["status"], "ok")
        self.assertEqual(throttle_check["backend"], "sqlite")
        self.assertTrue(throttle_check["storage_path"].endswith("request_throttle.sqlite3"))

    def test_healthcheck_operacional_admin_retorna_resumo(self):
        resposta = self.client.get("/health/operational")
        payload = resposta.get_json()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("summary", payload)
        self.assertEqual(payload["summary"]["rpvs_ativos"], 0)
        self.assertEqual(payload["summary"]["issues_por_severidade"], {})

    def test_healthcheck_operacional_exige_admin(self):
        usuario = User(
            nome="Operador Simples",
            login="operador",
            email="operador@controle-rpv.local",
            telefone="79999999999",
            cargo="Operador",
            setor="Financeiro",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("senha123")
        db.session.add(usuario)
        db.session.commit()
        self._autenticar(usuario.id)

        resposta = self.client.get("/health/operational")

        self.assertEqual(resposta.status_code, 403)
        self._autenticar(self.user_id)

    def test_cadastro_ajusta_exercicio_para_mes_do_pagamento(self):
        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-PAGAMENTO-ABRIL",
                "numero_processo": "PROC-PAGAMENTO-ABRIL",
                "data_ci": "2026-03-28",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Pagamento Abril",
                "tipo_documento": "CPF",
                "documento_original": "39053344705",
                "valor_bruto": "8000,00",
                "valor_irrf": "800,00",
                "data_pagamento": "2026-04-02",
                "confirmar_alerta_irrf": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)

        registro = RegistroRPV.query.filter_by(nome_beneficiario="Pagamento Abril").first()
        self.assertIsNotNone(registro)
        self.assertEqual(registro.processo.exercicio, "2026-04")

        resposta_marco = self.client.get("/reinf/?competencia=2026-03")
        html_marco = resposta_marco.get_data(as_text=True)
        self.assertNotIn("Pagamento Abril", html_marco)

        resposta_abril = self.client.get("/reinf/?competencia=2026-04")
        html_abril = resposta_abril.get_data(as_text=True)
        self.assertIn("Pagamento Abril", html_abril)

    def test_acompanhamento_aplica_ordenacao_e_paginacao(self):
        self._criar_rpv(
            nome_beneficiario="RPV Baixo",
            valor_bruto=Decimal("1000.00"),
            valor_irrf=Decimal("100.00"),
        )
        self._criar_rpv(
            nome_beneficiario="RPV Alto",
            valor_bruto=Decimal("9000.00"),
            valor_irrf=Decimal("900.00"),
        )
        self._criar_rpv(
            nome_beneficiario="RPV Medio",
            valor_bruto=Decimal("5000.00"),
            valor_irrf=Decimal("500.00"),
        )

        resposta_pagina_1 = self.client.get(
            "/rpvs/?ordenar=valor&direcao=desc&por_pagina=1&pagina=1"
        )
        html_pagina_1 = resposta_pagina_1.get_data(as_text=True)

        self.assertEqual(resposta_pagina_1.status_code, 200)
        self.assertIn("RPV Alto", html_pagina_1)
        self.assertNotIn("RPV Medio", html_pagina_1)
        self.assertIn("Página 1 de 3", html_pagina_1)

        resposta_pagina_2 = self.client.get(
            "/rpvs/?ordenar=valor&direcao=desc&por_pagina=1&pagina=2"
        )
        html_pagina_2 = resposta_pagina_2.get_data(as_text=True)

        self.assertEqual(resposta_pagina_2.status_code, 200)
        self.assertIn("RPV Medio", html_pagina_2)
        self.assertNotIn("RPV Alto", html_pagina_2)

    def test_acompanhamento_abre_filtrando_so_os_meus_por_padrao(self):
        usuario_secundario = User(
            nome="Usuário Terceiro",
            login="usuario.terceiro",
            email="usuario.terceiro@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        registro_meu = self._criar_rpv(
            nome_beneficiario="RPV Meu Padrao",
            valor_bruto=Decimal("2200.00"),
            valor_irrf=Decimal("220.00"),
        )
        registro_outro = self._criar_rpv(
            nome_beneficiario="RPV Outro Padrao",
            valor_bruto=Decimal("3300.00"),
            valor_irrf=Decimal("330.00"),
        )
        registro_outro.elaborador_id = usuario_secundario.id
        db.session.commit()

        resposta_padrao = self.client.get("/rpvs/")
        html_padrao = resposta_padrao.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn("RPV Meu Padrao", html_padrao)
        self.assertNotIn("RPV Outro Padrao", html_padrao)
        self.assertEqual(registro_meu.elaborador_id, self.user_id)
        self.assertEqual(registro_outro.elaborador_id, usuario_secundario.id)

        resposta_todos = self.client.get("/rpvs/?responsavel=todos")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn("RPV Meu Padrao", html_todos)
        self.assertIn("RPV Outro Padrao", html_todos)

    def test_acompanhamento_exibe_responsavel_no_resumo_operacional(self):
        usuario_secundario = User(
            nome="Usuário Responsável RPV",
            login="usuario.responsavel.rpv",
            email="usuario.responsavel.rpv@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="RPV Com Responsavel Visivel",
            valor_bruto=Decimal("4100.00"),
            valor_irrf=Decimal("410.00"),
            numero_se="SE-RPV-2026-001",
        )
        registro.elaborador_id = usuario_secundario.id
        registro.criado_por_id = usuario_secundario.id
        db.session.commit()

        resposta = self.client.get("/rpvs/?responsavel=todos")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Responsavel:", html)
        self.assertIn("Usuário Responsável RPV", html)
        self.assertIn("SE: SE-RPV-2026-001", html)
        self.assertIn('value="{}"'.format(registro.resumo_operacional), html)
        self.assertNotIn(
            'value="{}"'.format(
                f"{registro.resumo_operacional}_Usuário Responsável RPV"
            ),
            html,
        )

    def test_acompanhamento_oculta_concluidos_e_cancelados_por_padrao(self):
        situacao_concluida = self._criar_situacao_empenho(
            "Concluída",
            ordem_fluxo=98,
            is_final=True,
        )
        situacao_cancelado = self._criar_situacao_empenho(
            "Cancelado",
            ordem_fluxo=99,
            is_final=True,
        )
        situacao_imposto_concluida = self._criar_situacao_imposto(
            "Concluída",
            ordem_fluxo=98,
            is_final=True,
        )

        self._criar_rpv(
            nome_beneficiario="RPV Ativo Fila",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        registro_concluido = self._criar_rpv(
            nome_beneficiario="RPV Concluido Fila",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        registro_concluido.situacao_empenho_id = situacao_concluida.id
        registro_concluido.situacao_imposto_id = situacao_imposto_concluida.id
        registro_cancelado = self._criar_rpv(
            nome_beneficiario="RPV Cancelado Fila",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        registro_cancelado.situacao_empenho_id = situacao_cancelado.id
        db.session.commit()

        resposta_padrao = self.client.get("/rpvs/")
        html_padrao = resposta_padrao.get_data(as_text=True)
        resposta_todos = self.client.get("/rpvs/?mostrar_encerrados=1")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn("RPV Ativo Fila", html_padrao)
        self.assertNotIn("RPV Concluido Fila", html_padrao)
        self.assertNotIn("RPV Cancelado Fila", html_padrao)
        self.assertIn("Fila ativa", html_padrao)
        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn("RPV Concluido Fila", html_todos)
        self.assertIn("RPV Cancelado Fila", html_todos)
        self.assertIn("Todos os status", html_todos)

    def test_acompanhamento_mantem_rpv_concluido_na_fila_quando_irrf_ainda_esta_pendente(self):
        situacao_concluida = self._criar_situacao_empenho(
            "Concluída",
            ordem_fluxo=98,
            is_final=True,
        )

        registro = self._criar_rpv(
            nome_beneficiario="RPV Fiscal Ainda Pendente",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        registro.situacao_empenho_id = situacao_concluida.id
        db.session.commit()

        resposta = self.client.get("/rpvs/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("RPV Fiscal Ainda Pendente", html)
        self.assertIn("IRRF: Sem Tratamento", html)

    def test_novo_rpv_permite_definir_responsavel_diferente_do_criador(self):
        usuario_secundario = User(
            nome="Usuário Destino RPV",
            login="usuario.destino.rpv",
            email="usuario.destino.rpv@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-RPV-RESPONSAVEL",
                "numero_processo": "PROC-RPV-RESPONSAVEL",
                "data_ci": "2026-03-10",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "RPV Responsabilidade Direta",
                "tipo_documento": "CPF",
                "documento_original": "28001238938",
                "valor_bruto": "5000,00",
                "valor_irrf": "",
                "elaborador_id": str(usuario_secundario.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro = RegistroRPV.query.filter_by(nome_beneficiario="RPV Responsabilidade Direta").one()
        self.assertEqual(registro.criado_por_id, self.user_id)
        self.assertEqual(registro.elaborador_id, usuario_secundario.id)

    def test_rpv_permite_transferir_responsabilidade_com_historico(self):
        usuario_secundario = User(
            nome="Usuário Transferido RPV",
            login="usuario.transferido.rpv",
            email="usuario.transferido.rpv@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        registro = self._criar_rpv(nome_beneficiario="RPV Transferência Responsável")

        resposta = self.client.post(
            f"/rpvs/{registro.id}/transferir-responsavel",
            data={"elaborador_id": str(usuario_secundario.id)},
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.criado_por_id, self.user_id)
        self.assertEqual(registro_atualizado.elaborador_id, usuario_secundario.id)

        evento = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="registro_rpv",
                entidade_id=registro.id,
                acao="Transferência de responsabilidade",
            )
            .order_by(HistoricoAlteracao.id.desc())
            .first()
        )
        self.assertIsNotNone(evento)
        self.assertIn("Usuário Transferido RPV", evento.resumo)

    def test_dativos_lista_cis_aplica_ordenacao_e_paginacao(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-DAT-LOW",
            itens=[
                {"nome_beneficiario": "Beneficiario Lote Baixo", "valor_bruto": Decimal("3000.00")},
            ],
        )
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-DAT-HIGH",
            itens=[
                {"nome_beneficiario": "Beneficiario Lote Alto", "valor_bruto": Decimal("9000.00")},
            ],
        )

        resposta = self.client.get(
            "/dativos/cis?ordenar=valor&direcao=desc&por_pagina=1&pagina=1"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("CI-DAT-HIGH", html)
        self.assertNotIn("CI-DAT-LOW", html)
        self.assertIn("Página 1 de 2", html)

    def test_dativos_abre_filtrando_so_os_meus_por_padrao(self):
        usuario_secundario = User(
            nome="Usuário Dativo",
            login="usuario.dativo",
            email="usuario.dativo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        ci_meu, _, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-MEU-DATIVO")
        ci_outro, _, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-OUTRO-DATIVO")
        ci_outro.responsavel_id = usuario_secundario.id
        db.session.commit()

        resposta_padrao = self.client.get("/dativos/cis")
        html_padrao = resposta_padrao.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn(ci_meu.processo_edoc, html_padrao)
        self.assertNotIn(ci_outro.processo_edoc, html_padrao)

        resposta_todos = self.client.get("/dativos/cis?responsavel=todos")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn(ci_meu.processo_edoc, html_todos)
        self.assertIn(ci_outro.processo_edoc, html_todos)

    def test_dativos_lista_cis_exibe_responsavel_no_resumo_operacional(self):
        usuario_secundario = User(
            nome="Usuário Responsável Dativo",
            login="usuario.responsavel.dativo",
            email="usuario.responsavel.dativo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        dativo_ci, lote, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-RESPONSAVEL",
            itens=[
                {
                    "nome_beneficiario": "Beneficiario Dativo Responsavel",
                    "valor_bruto": Decimal("5100.00"),
                },
            ],
            numero_se="SE-DAT-2026-001",
        )
        dativo_ci.responsavel_id = usuario_secundario.id
        db.session.commit()

        resposta = self.client.get("/dativos/cis?responsavel=todos")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("CI-DATIVO-RESPONSAVEL", html)
        self.assertIn("Responsavel:", html)
        self.assertIn("Usuário Responsável Dativo", html)
        self.assertIn("SE: SE-DAT-2026-001", html)

    def test_dativos_lista_cis_sinaliza_ci_incompleta_sem_planilha(self):
        dativo_ci = DativoCI(
            exercicio="2026-03",
            processo_edoc="CI-DATIVO-INCOMPLETA",
            data_ci=date(2026, 3, 18),
            descricao="Dativo Geral",
            criado_por_id=self.user_id,
            responsavel_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.commit()

        resposta = self.client.get("/dativos/cis")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("C.I.s aguardando planilha", html)
        self.assertIn("Abrir C.I. CI-DATIVO-INCOMPLETA", html)
        self.assertIn("Pendentes de planilha:", html)

    def test_lotes_sem_irrf_exibem_se_no_resumo_operacional(self):
        dativo_ci, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-SE-LOTE",
            numero_se="SE-LOTE-2026-001",
        )

        resposta = self.client.get("/dativos/lotes-sem-irrf")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(dativo_ci.processo_edoc, html)
        self.assertIn("SE: SE-LOTE-2026-001", html)

    def test_itens_com_irrf_exibem_se_no_resumo_operacional(self):
        dativo_ci, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-DATIVO-SE-ITEM",
            nome_beneficiario="Beneficiario Com SE",
            numero_processo="PROC-DATIVO-SE-ITEM",
            numero_se="SE-ITEM-2026-001",
        )

        resposta = self.client.get("/dativos/itens-com-irrf")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(dativo_ci.processo_edoc, html)
        self.assertIn("SE: SE-ITEM-2026-001", html)

    def test_dativos_lista_cis_oculta_encerrados_por_padrao(self):
        situacao_concluida = self._criar_situacao_empenho(
            "Concluída",
            ordem_fluxo=98,
            is_final=True,
        )
        situacao_cancelado = self._criar_situacao_empenho(
            "Cancelado",
            ordem_fluxo=99,
            is_final=True,
        )

        ci_ativo, _, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-ATIVA-FILA")
        ci_concluida, lote_concluido, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-CONCLUIDA-FILA"
        )
        lote_concluido.situacao_rpv_id = situacao_concluida.id
        ci_cancelado, item_cancelado = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-CANCELADA-FILA",
            nome_beneficiario="Item Cancelado Fila",
            numero_processo="PROC-CANCELADO-FILA",
        )
        item_cancelado.situacao_rpv_id = situacao_cancelado.id
        db.session.commit()

        resposta_padrao = self.client.get("/dativos/cis")
        html_padrao = resposta_padrao.get_data(as_text=True)
        resposta_todos = self.client.get("/dativos/cis?mostrar_encerrados=1")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn(ci_ativo.processo_edoc, html_padrao)
        self.assertNotIn(ci_concluida.processo_edoc, html_padrao)
        self.assertNotIn(ci_cancelado.processo_edoc, html_padrao)
        self.assertIn("Fila ativa", html_padrao)
        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn(ci_concluida.processo_edoc, html_todos)
        self.assertIn(ci_cancelado.processo_edoc, html_todos)
        self.assertIn("Todos os status", html_todos)

    def test_dativos_lista_cis_destaca_ci_do_processo_quando_grade_fica_vazia_por_filtro(self):
        usuario_secundario = User(
            nome="Usuário Externo Dativo",
            login="usuario.externo.dativo",
            email="usuario.externo.dativo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        self._criar_dativo_sem_irrf(
            processo_edoc="823/2026",
            responsavel_id=usuario_secundario.id,
            itens=[
                {
                    "nome_beneficiario": "BETANIA CARLA SANTOS MELO",
                    "numero_processo": "202677000494",
                    "valor_bruto": Decimal("5855.02"),
                },
            ],
        )

        resposta = self.client.get("/dativos/cis?q=202677000494")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Processo localizado em dativos", html)
        self.assertIn("A grade atual não trouxe linhas com os filtros aplicados", html)
        self.assertIn("C.I.", html)
        self.assertIn("823/2026", html)
        self.assertIn("Abrir C.I.", html)
        self.assertIn("Abrir lote", html)

    def test_lista_rpvs_destaca_quando_processo_existe_em_dativos(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="823/2026",
            itens=[
                {
                    "nome_beneficiario": "BETANIA CARLA SANTOS MELO",
                    "numero_processo": "202677000494",
                    "valor_bruto": Decimal("5855.02"),
                },
            ],
        )

        resposta = self.client.get("/rpvs/?q=202677000494")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Processo encontrado em dativos", html)
        self.assertIn("202677000494", html)
        self.assertIn("823/2026", html)
        self.assertIn("Abrir C.I.", html)

    def test_lista_rpvs_destaca_quando_ci_de_dativo_e_pesquisada_no_modulo_normal(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-BUSCA-TRANSVERSAL",
            itens=[
                {
                    "nome_beneficiario": "BENEFICIARIO BUSCA TRANSVERSAL",
                    "numero_processo": "PROC-DATIVO-BUSCA-TRANSVERSAL",
                    "valor_bruto": Decimal("5855.02"),
                },
            ],
        )

        resposta = self.client.get("/rpvs/?q=CI-DATIVO-BUSCA-TRANSVERSAL")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Busca encontrada em dativos", html)
        self.assertIn("CI-DATIVO-BUSCA-TRANSVERSAL", html)
        self.assertIn("Localizado por: C.I./eDOC", html)
        self.assertIn("Abrir C.I.", html)

    def test_lista_rpvs_mensagem_cruzada_reflete_rpv_normal_e_dativo_mesma_ci(self):
        usuario_secundario = User(
            nome="Usuário Dono RPV Cruzada",
            login="usuario.dono.rpv.cruzada",
            email="usuario.dono.rpv.cruzada@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="BENEFICIARIO NORMAL CI COMPARTILHADA",
            processo_edoc="CI-COMPARTILHADA-TRANSVERSAL",
            numero_processo="PROC-NORMAL-CI-COMPARTILHADA",
            elaborador_id=usuario_secundario.id,
            criado_por_id=usuario_secundario.id,
        )
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-COMPARTILHADA-TRANSVERSAL",
            itens=[
                {
                    "nome_beneficiario": "BENEFICIARIO DATIVO CI COMPARTILHADA",
                    "numero_processo": "PROC-DATIVO-CI-COMPARTILHADA",
                    "valor_bruto": Decimal("5855.02"),
                },
            ],
        )

        resposta = self.client.get("/rpvs/?q=CI-COMPARTILHADA-TRANSVERSAL")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Busca encontrada em RPVs normais e dativos", html)
        self.assertIn("A grade atual não trouxe linhas com os filtros aplicados", html)
        self.assertNotIn("Nenhum RPV normal bateu com a consulta atual", html)
        self.assertIn("Há 1 ocorrência(s) em RPVs normais", html)
        self.assertIn("também em dativos na C.I.", html)
        self.assertIn("CI-COMPARTILHADA-TRANSVERSAL", html)
        self.assertIn("Abrir C.I.", html)
        self.assertIn("Abrir RPV normal", html)

    def test_lista_dativos_destaca_quando_ci_de_rpv_normal_e_pesquisada_em_dativos(self):
        self._criar_rpv(
            nome_beneficiario="BENEFICIARIO NORMAL BUSCA TRANSVERSAL",
            processo_edoc="CI-RPV-BUSCA-TRANSVERSAL",
            numero_processo="PROC-RPV-BUSCA-TRANSVERSAL",
        )

        resposta = self.client.get("/dativos/cis?q=CI-RPV-BUSCA-TRANSVERSAL")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Busca encontrada em RPVs normais", html)
        self.assertIn("CI-RPV-BUSCA-TRANSVERSAL", html)
        self.assertIn("Localizado por: C.I./eDOC", html)
        self.assertIn("Abrir RPV normal", html)

    def test_lista_rpvs_destaca_quando_ne_de_dativo_e_pesquisada_no_filtro_ne(self):
        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-NE-TRANSVERSAL",
            itens=[
                {
                    "nome_beneficiario": "BENEFICIARIO NE TRANSVERSAL",
                    "numero_processo": "PROC-DATIVO-NE-TRANSVERSAL",
                    "valor_bruto": Decimal("5855.02"),
                },
            ],
        )
        lote.nota_empenho = "NE-DATIVO-BUSCA-TRANSVERSAL"
        db.session.commit()

        resposta = self.client.get("/rpvs/?ne=NE-DATIVO-BUSCA-TRANSVERSAL")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Busca encontrada em dativos", html)
        self.assertIn("CI-DATIVO-NE-TRANSVERSAL", html)
        self.assertIn("Localizado por: Nota de empenho do lote", html)
        self.assertIn("Abrir C.I.", html)

    def test_nova_ci_dativo_permite_definir_responsavel_diferente_do_criador(self):
        usuario_secundario = User(
            nome="Usuário Destino Dativo",
            login="usuario.destino.dativo",
            email="usuario.destino.dativo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        resposta = self.client.post(
            "/dativos/novo-ci",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-DATIVO-CRIACAO-RESP",
                "data_ci": "2026-03-10",
                "responsavel_id": str(usuario_secundario.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        dativo_ci = DativoCI.query.filter_by(processo_edoc="CI-DATIVO-CRIACAO-RESP").one()
        self.assertEqual(dativo_ci.criado_por_id, self.user_id)
        self.assertEqual(dativo_ci.responsavel_id, usuario_secundario.id)

    def test_ci_dativo_permite_transferir_responsabilidade_e_reflete_nos_filtros(self):
        usuario_secundario = User(
            nome="Usuário Transferido Dativo",
            login="usuario.transferido.dativo",
            email="usuario.transferido.dativo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        dativo_ci, _, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-DATIVO-TRANSFERENCIA")

        resposta = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/transferir-responsavel",
            data={"responsavel_id": str(usuario_secundario.id)},
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        dativo_ci_atualizado = db.session.get(DativoCI, dativo_ci.id)
        self.assertEqual(dativo_ci_atualizado.criado_por_id, self.user_id)
        self.assertEqual(dativo_ci_atualizado.responsavel_id, usuario_secundario.id)

        resposta_padrao = self.client.get("/dativos/cis")
        html_padrao = resposta_padrao.get_data(as_text=True)
        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertNotIn("CI-DATIVO-TRANSFERENCIA", html_padrao)

        resposta_filtrada = self.client.get(f"/dativos/cis?responsavel={usuario_secundario.id}")
        html_filtrada = resposta_filtrada.get_data(as_text=True)
        self.assertEqual(resposta_filtrada.status_code, 200)
        self.assertIn("CI-DATIVO-TRANSFERENCIA", html_filtrada)

        evento = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="dativo_ci",
                entidade_id=dativo_ci.id,
                acao="Transferência de responsabilidade",
            )
            .order_by(HistoricoAlteracao.id.desc())
            .first()
        )
        self.assertIsNotNone(evento)
        self.assertIn("Usuário Transferido Dativo", evento.resumo)

    def test_dativos_com_irrf_exibem_data_da_ci_em_resumo_legado(self):
        dativo_ci, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-DATIVO-LEGADO",
            nome_beneficiario="Beneficiario Legado IRRF",
            numero_processo="PROC-LEGADO-IRRF",
            valor_bruto=Decimal("7300.00"),
            valor_irrf=Decimal("730.00"),
        )
        item.resumo_operacional = (
            f"C.I. {dativo_ci.processo_edoc}_{item.numero_processo}_"
            f"Dativo_{item.nome_beneficiario}_IRRF 730,00"
        )
        db.session.commit()

        resposta_lista = self.client.get("/dativos/cis")
        html_lista = resposta_lista.get_data(as_text=True)
        resposta_detalhe = self.client.get(f"/dativos/itens-com-irrf/{item.id}")
        html_detalhe = resposta_detalhe.get_data(as_text=True)

        self.assertEqual(resposta_lista.status_code, 200)
        self.assertEqual(resposta_detalhe.status_code, 200)
        self.assertIn("10/03/2026", html_lista)
        self.assertIn("10/03/2026", html_detalhe)
        self.assertIn("CI-DATIVO-LEGADO", html_lista)
        self.assertIn('name="numero_se"', html_detalhe)

    def test_layout_exibe_rodape_da_atualizacao(self):
        resposta = self.client.get("/rpvs/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Atualizacao Operacional Atlas", html)

    def test_paginas_operacionais_exibem_filtros_salvos(self):
        paginas = [
            (
                "/rpvs/",
                'data-saved-filters-scope="rpvs"',
                'data-saved-form-id="rpvs-filter-form"',
                'data-filter-panel="rpvs"',
            ),
            (
                "/dativos/cis",
                'data-saved-filters-scope="dativos"',
                'data-saved-form-id="dativos-filter-form"',
                'data-filter-panel="dativos_cis"',
            ),
            (
                "/dativos/lotes-sem-irrf",
                'data-saved-filters-scope="dativos_lotes"',
                'data-saved-form-id="dativos-lotes-filter-form"',
                'data-filter-panel="dativos_lotes"',
            ),
            (
                "/dativos/itens-com-irrf",
                'data-saved-filters-scope="dativos_itens"',
                'data-saved-form-id="dativos-itens-filter-form"',
                'data-filter-panel="dativos_itens"',
            ),
            (
                "/reinf/?competencia=2026-03",
                'data-saved-filters-scope="reinf"',
                'data-saved-form-id="reinf-filter-form"',
                'data-filter-panel="reinf"',
            ),
            (
                "/bi",
                'data-saved-filters-scope="bi"',
                'data-saved-form-id="bi-filter-form"',
                'data-filter-panel="bi"',
            ),
        ]

        for url, marcador_scope, marcador_form, marcador_painel in paginas:
            with self.subTest(url=url):
                resposta = self.client.get(url)
                html = resposta.get_data(as_text=True)
                self.assertEqual(resposta.status_code, 200)
                self.assertIn('data-user-scope="{}"'.format(self.user_id), html)
                self.assertIn(marcador_scope, html)
                self.assertIn(marcador_form, html)
                self.assertIn(marcador_painel, html)

    def test_lotes_sem_irrf_abre_filtrando_so_os_meus_por_padrao(self):
        usuario_secundario = User(
            nome="Usuário Lote Externo",
            login="usuario.lote.externo",
            email="usuario.lote.externo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        ci_meu, _, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-LOTE-MEU")
        ci_outro, lote_outro, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-LOTE-OUTRO")
        ci_outro.responsavel_id = usuario_secundario.id
        db.session.commit()

        resposta_padrao = self.client.get("/dativos/lotes-sem-irrf")
        html_padrao = resposta_padrao.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn(ci_meu.processo_edoc, html_padrao)
        self.assertNotIn(ci_outro.processo_edoc, html_padrao)

        resposta_todos = self.client.get("/dativos/lotes-sem-irrf?responsavel=todos")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn(ci_meu.processo_edoc, html_todos)
        self.assertIn(ci_outro.processo_edoc, html_todos)

    def test_lotes_sem_irrf_ocultam_encerrados_por_padrao(self):
        situacao_concluida = self._criar_situacao_empenho(
            "Concluída",
            ordem_fluxo=98,
            is_final=True,
        )
        situacao_cancelado = self._criar_situacao_empenho(
            "Cancelado",
            ordem_fluxo=99,
            is_final=True,
        )

        ci_ativo, _, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-LOTE-ATIVO")
        ci_concluido, lote_concluido, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-LOTE-CONCLUIDO"
        )
        lote_concluido.situacao_rpv_id = situacao_concluida.id
        ci_cancelado, lote_cancelado, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-LOTE-CANCELADO"
        )
        lote_cancelado.situacao_rpv_id = situacao_cancelado.id
        db.session.commit()

        resposta_padrao = self.client.get("/dativos/lotes-sem-irrf")
        html_padrao = resposta_padrao.get_data(as_text=True)
        resposta_todos = self.client.get("/dativos/lotes-sem-irrf?mostrar_encerrados=1")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn(ci_ativo.processo_edoc, html_padrao)
        self.assertNotIn(ci_concluido.processo_edoc, html_padrao)
        self.assertNotIn(ci_cancelado.processo_edoc, html_padrao)
        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn(ci_concluido.processo_edoc, html_todos)
        self.assertIn(ci_cancelado.processo_edoc, html_todos)

    def test_itens_com_irrf_abre_filtrando_so_os_meus_por_padrao(self):
        usuario_secundario = User(
            nome="Usuário Item Externo",
            login="usuario.item.externo",
            email="usuario.item.externo@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        _, item_meu = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-ITEM-MEU",
            nome_beneficiario="Item Meu",
            numero_processo="PROC-ITEM-MEU",
        )
        ci_item_outro, item_outro = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-ITEM-OUTRO",
            nome_beneficiario="Item Outro",
            numero_processo="PROC-ITEM-OUTRO",
        )
        ci_item_outro.responsavel_id = usuario_secundario.id
        db.session.commit()

        resposta_padrao = self.client.get("/dativos/itens-com-irrf")
        html_padrao = resposta_padrao.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn("CI-ITEM-MEU", html_padrao)
        self.assertNotIn("CI-ITEM-OUTRO", html_padrao)

        resposta_todos = self.client.get("/dativos/itens-com-irrf?responsavel=todos")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn("CI-ITEM-MEU", html_todos)
        self.assertIn("CI-ITEM-OUTRO", html_todos)

    def test_itens_com_irrf_ocultam_encerrados_por_padrao(self):
        situacao_concluida = self._criar_situacao_empenho(
            "Concluída",
            ordem_fluxo=98,
            is_final=True,
        )
        situacao_cancelado = self._criar_situacao_empenho(
            "Cancelado",
            ordem_fluxo=99,
            is_final=True,
        )

        ci_ativo, _ = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-ITEM-ATIVO",
            nome_beneficiario="Item Ativo",
            numero_processo="PROC-ITEM-ATIVO",
        )
        ci_concluido, item_concluido = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-ITEM-CONCLUIDO",
            nome_beneficiario="Item Concluido",
            numero_processo="PROC-ITEM-CONCLUIDO",
        )
        item_concluido.situacao_rpv_id = situacao_concluida.id
        ci_cancelado, item_cancelado = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-ITEM-CANCELADO",
            nome_beneficiario="Item Cancelado",
            numero_processo="PROC-ITEM-CANCELADO",
        )
        item_cancelado.situacao_rpv_id = situacao_cancelado.id
        db.session.commit()

        resposta_padrao = self.client.get("/dativos/itens-com-irrf")
        html_padrao = resposta_padrao.get_data(as_text=True)
        resposta_todos = self.client.get("/dativos/itens-com-irrf?mostrar_encerrados=1")
        html_todos = resposta_todos.get_data(as_text=True)

        self.assertEqual(resposta_padrao.status_code, 200)
        self.assertIn(ci_ativo.processo_edoc, html_padrao)
        self.assertNotIn(ci_concluido.processo_edoc, html_padrao)
        self.assertNotIn(ci_cancelado.processo_edoc, html_padrao)
        self.assertEqual(resposta_todos.status_code, 200)
        self.assertIn(ci_concluido.processo_edoc, html_todos)
        self.assertIn(ci_cancelado.processo_edoc, html_todos)

    def test_acompanhamento_atualiza_status_em_lote(self):
        situacao_empenho_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=2,
            ativo=True,
            is_final=True,
        )
        situacao_imposto_concluida = SituacaoImposto(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=3,
            ativo=True,
            is_final=True,
        )
        db.session.add_all([situacao_empenho_pago, situacao_imposto_concluida])
        db.session.commit()

        registro_1 = self._criar_rpv(
            nome_beneficiario="RPV Lote Um",
            valor_irrf=Decimal("120.00"),
        )
        registro_2 = self._criar_rpv(
            nome_beneficiario="RPV Lote Dois",
            valor_irrf=Decimal("180.00"),
        )
        registro_1.nota_empenho = "2026NELOTE001"
        registro_1.ordem_bancaria = "2026OBLOTE001"
        registro_2.nota_empenho = "2026NELOTE002"
        registro_2.ordem_bancaria = "2026OBLOTE002"
        db.session.commit()

        resposta = self.client.post(
            "/rpvs/atualizacao-lote",
            data={
                "selecionados": [str(registro_1.id), str(registro_2.id)],
                "situacao_empenho_id_lote": str(situacao_empenho_pago.id),
                "situacao_imposto_id_lote": str(situacao_imposto_concluida.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_1_atualizado = db.session.get(RegistroRPV, registro_1.id)
        registro_2_atualizado = db.session.get(RegistroRPV, registro_2.id)
        self.assertEqual(registro_1_atualizado.situacao_empenho_id, situacao_empenho_pago.id)
        self.assertEqual(registro_2_atualizado.situacao_empenho_id, situacao_empenho_pago.id)
        self.assertEqual(registro_1_atualizado.situacao_imposto_id, situacao_imposto_concluida.id)
        self.assertEqual(registro_2_atualizado.situacao_imposto_id, situacao_imposto_concluida.id)
        self.assertEqual(registro_1_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(registro_2_atualizado.data_pagamento, self._data_base_mes_atual())

    def test_filtro_cancelado_na_lista_rpvs_nao_altera_registro(self):
        situacao_cancelado = SituacaoEmpenho(
            nome="Cancelado",
            cor_badge="badge-red",
            ordem_fluxo=99,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_cancelado)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Filtro Cancelado Seguro",
            valor_irrf=Decimal("155.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        historicos_antes = HistoricoAlteracao.query.filter_by(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
        ).count()

        resposta = self.client.get(
            f"/rpvs/?responsavel=todos&situacao_empenho_id={situacao_cancelado.id}"
        )

        self.assertEqual(resposta.status_code, 200)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        historicos_depois = HistoricoAlteracao.query.filter_by(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
        ).count()
        self.assertEqual(registro_atualizado.situacao_empenho_id, self.situacao_empenho_id)
        self.assertEqual(historicos_depois, historicos_antes)

    def test_flash_warning_recebe_layout_padrao(self):
        resposta = self.client.post(
            "/rpvs/atualizacao-lote",
            data={},
            follow_redirects=True,
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("flash-warning", html)
        self.assertIn("Revise antes de seguir", html)
        self.assertIn("Selecione pelo menos um RPV para atualizar em lote.", html)

    def test_atualizacao_rapida_rpv_carimba_data_pagamento_ao_marcar_pago(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Carimbo Pago RPV",
            valor_irrf=Decimal("200.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.post(
            f"/rpvs/{registro.id}/atualizacao-rapida",
            data={
                "nota_empenho": "2026NERAPIDA001",
                "ordem_bancaria": "2026OBRAPIDA001",
                "ob_imposto": "",
                "situacao_empenho_id": str(situacao_pago.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.situacao_empenho_id, situacao_pago.id)
        self.assertEqual(registro_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(
            registro_atualizado.processo.exercicio,
            self._data_base_mes_atual().strftime("%Y-%m"),
        )

    def test_atualizacao_rapida_rpv_move_competencia_para_mes_atual_quando_pagamento_e_lancado_depois(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Pago Mes Corrente",
            valor_irrf=Decimal("215.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )
        registro.processo.exercicio = "2026-02"
        db.session.commit()

        resposta = self.client.post(
            f"/rpvs/{registro.id}/atualizacao-rapida",
            data={
                "nota_empenho": "2026NERAPIDA002",
                "ordem_bancaria": "2026OBRAPIDA002",
                "ob_imposto": "",
                "situacao_empenho_id": str(situacao_pago.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(
            registro_atualizado.processo.exercicio,
            self._data_base_mes_atual().strftime("%Y-%m"),
        )

        competencia_atual = self._data_base_mes_atual().strftime("%Y-%m")
        resposta_reinf_competencia = self.client.get(f"/reinf/?competencia={competencia_atual}")
        self.assertIn("Pago Mes Corrente", resposta_reinf_competencia.get_data(as_text=True))

    def test_edicao_rpv_carimba_data_pagamento_no_mes_do_exercicio_ao_marcar_concluida(self):
        situacao_concluida = SituacaoEmpenho(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_concluida)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Concluida Retroativa",
            valor_irrf=Decimal("210.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-02",
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": registro.nome_beneficiario,
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "210,00",
                "sem_irrf": "",
                "nota_empenho": "2026NEEDIT001",
                "ordem_bancaria": "2026OBEDIT001",
                "ob_imposto": "",
                "reinf_status": "",
                "situacao_empenho_id": str(situacao_concluida.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "",
                "data_pagamento_irrf": "",
                "observacoes": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.situacao_empenho_id, situacao_concluida.id)
        self.assertEqual(registro_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(
            registro_atualizado.processo.exercicio,
            self._data_base_mes_atual().strftime("%Y-%m"),
        )

    def test_edicao_rpv_move_competencia_para_mes_atual_sem_data_manual(self):
        situacao_concluida = SituacaoEmpenho(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_concluida)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Concluida Mes Atual",
            valor_irrf=Decimal("230.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": registro.nome_beneficiario,
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "230,00",
                "sem_irrf": "",
                "nota_empenho": "2026NEEDIT002",
                "ordem_bancaria": "2026OBEDIT002",
                "ob_imposto": "",
                "reinf_status": "",
                "situacao_empenho_id": str(situacao_concluida.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "",
                "data_pagamento_irrf": "",
                "observacoes": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(
            registro_atualizado.processo.exercicio,
            self._data_base_mes_atual().strftime("%Y-%m"),
        )

    def test_atualizacao_rapida_rpv_cancelado_limpa_pagamento_e_some_do_bi(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        situacao_cancelado = SituacaoEmpenho(
            nome="Cancelado",
            cor_badge="badge-red",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add_all([situacao_pago, situacao_cancelado])
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="RPV Cancelado BI",
            valor_irrf=Decimal("200.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
            data_pagamento_irrf=date(2026, 3, 20),
        )
        registro.situacao_empenho_id = situacao_pago.id
        registro.reinf_status = "Concluído"
        db.session.commit()

        resposta = self.client.post(
            f"/rpvs/{registro.id}/atualizacao-rapida",
            data={
                "nota_empenho": "",
                "ordem_bancaria": "",
                "ob_imposto": "",
                "situacao_empenho_id": str(situacao_cancelado.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.situacao_empenho_id, situacao_cancelado.id)
        self.assertIsNone(registro_atualizado.data_pagamento)
        self.assertIsNone(registro_atualizado.data_pagamento_irrf)
        self.assertIsNone(registro_atualizado.reinf_status)

        with self.app.test_request_context("/bi"):
            dataset = _coletar_dataset_bi()

        self.assertNotIn(
            "RPV Cancelado BI",
            {row["nome"] for row in dataset},
        )

        resposta_reinf = self.client.get("/reinf/?competencia=2026-03")
        self.assertEqual(resposta_reinf.status_code, 200)
        self.assertNotIn("RPV Cancelado BI", resposta_reinf.get_data(as_text=True))

    def test_bi_precarrega_situacoes_para_filtro_de_cancelamento(self):
        registro = self._criar_rpv(
            nome_beneficiario="BI Eager RPV",
            valor_irrf=Decimal("180.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        _, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-BI-EAGER",
            nome_beneficiario="BI Eager Dativo",
            cpf_original="12345678901",
            numero_processo="PROC-BI-EAGER",
            valor_bruto=Decimal("4200.00"),
            valor_irrf=Decimal("420.00"),
        )
        item.data_pagamento = date(2026, 3, 20)
        db.session.commit()

        filtros = {
            "origem": "todos",
            "responsavel": "todos",
            "pagamento": "todos",
        }

        with self.app.test_request_context("/bi"):
            registro_bi = _query_registros_bi(filtros).filter(RegistroRPV.id == registro.id).one()
            item_bi = _query_dativos_bi(filtros).filter(DativoItem.id == item.id).one()

        self.assertNotIn("situacao_empenho", sa_inspect(registro_bi).unloaded)
        self.assertNotIn("situacao_rpv", sa_inspect(item_bi).unloaded)

    def test_reinf_precarrega_situacoes_para_filtro_de_cancelamento(self):
        registro = self._criar_rpv(
            nome_beneficiario="REINF Eager RPV",
            valor_irrf=Decimal("210.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 12),
        )
        _, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-REINF-EAGER",
            nome_beneficiario="REINF Eager Dativo",
            cpf_original="10987654321",
            numero_processo="PROC-REINF-EAGER",
            valor_bruto=Decimal("5300.00"),
            valor_irrf=Decimal("530.00"),
        )
        item.data_pagamento = date(2026, 3, 14)
        db.session.commit()

        registro_reinf = _query_rpvs_reinf(
            competencia="2026-03",
            filtro_responsavel="todos",
            filtro_busca="",
            ano=None,
        ).filter(RegistroRPV.id == registro.id).one()
        item_reinf = _query_dativos_reinf(
            competencia="2026-03",
            filtro_responsavel="todos",
            filtro_busca="",
            ano=None,
        ).filter(DativoItem.id == item.id).one()

        self.assertNotIn("situacao_empenho", sa_inspect(registro_reinf).unloaded)
        self.assertNotIn("situacao_rpv", sa_inspect(item_reinf).unloaded)

    def test_dativos_atualiza_status_em_lote_para_lote_e_item(self):
        situacao_rpv_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=4,
            ativo=True,
            is_final=True,
        )
        situacao_irrf_concluida = SituacaoImposto(
            nome="Concluída Dativo",
            cor_badge="badge-green",
            ordem_fluxo=4,
            ativo=True,
            is_final=True,
        )
        db.session.add_all([situacao_rpv_pago, situacao_irrf_concluida])
        db.session.commit()

        dativo_ci, lote, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-LOTE-BATCH")
        lote.nota_empenho = "2026NELOTEBATCH"
        lote.ordem_bancaria = "2026OBLOTEBATCH"
        item_com_irrf = DativoItem(
            dativo_ci_id=dativo_ci.id,
            dativo_lote_id=None,
            grupo="com_irrf",
            nome_beneficiario="Item Lote Batch",
            nome_beneficiario_normalizado="",
            cpf_original="12345678901",
            cpf_normalizado="",
            numero_processo="PROC-ITEM-BATCH",
            data_pagamento=None,
            reinf_status=None,
            valor_bruto=Decimal("7000.00"),
            valor_irrf=Decimal("700.00"),
            valor_liquido=Decimal("0.00"),
            nota_empenho=None,
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
        item_com_irrf.atualizar_campos_derivados()
        item_com_irrf.gerar_resumo_operacional(
            processo_edoc=dativo_ci.processo_edoc,
            data_ci=dativo_ci.data_ci,
        )
        item_com_irrf.nota_empenho = "2026NEITEMBATCH"
        item_com_irrf.ordem_bancaria = "2026OBITEMBATCH"
        db.session.add(item_com_irrf)
        db.session.commit()

        resposta = self.client.post(
            "/dativos/atualizacao-lote",
            data={
                "selecionados": [f"lote:{lote.id}", f"item:{item_com_irrf.id}"],
                "situacao_rpv_id_lote": str(situacao_rpv_pago.id),
                "situacao_imposto_id_lote": str(situacao_irrf_concluida.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        lote_atualizado = db.session.get(DativoLote, lote.id)
        item_atualizado = db.session.get(DativoItem, item_com_irrf.id)
        self.assertEqual(lote_atualizado.situacao_rpv_id, situacao_rpv_pago.id)
        self.assertEqual(item_atualizado.situacao_rpv_id, situacao_rpv_pago.id)
        self.assertEqual(lote_atualizado.situacao_imposto_id, self.situacao_imposto_sem_irrf_id)
        self.assertEqual(item_atualizado.situacao_imposto_id, situacao_irrf_concluida.id)
        self.assertEqual(lote_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(item_atualizado.data_pagamento, self._data_base_mes_atual())

    def test_dativo_lote_exibe_data_pagamento_e_carimba_quando_marca_pago(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-PAGO",
            itens=[
                {
                    "nome_beneficiario": "Beneficiario Lote Pago",
                    "valor_bruto": Decimal("1200.00"),
                }
            ],
        )

        resposta_get = self.client.get(f"/dativos/lotes-sem-irrf/{lote.id}")
        html_get = resposta_get.get_data(as_text=True)

        self.assertEqual(resposta_get.status_code, 200)
        self.assertIn('name="data_pagamento"', html_get)
        self.assertIn('name="numero_se"', html_get)

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/salvar",
            data={
                "nota_empenho": "2026NELOTEPAGO",
                "numero_se": "2026SELOTEPAGO",
                "ordem_bancaria": "2026OBLOTEPAGO",
                "data_pagamento": "",
                "situacao_rpv_id": str(situacao_pago.id),
                "observacoes": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        lote_atualizado = db.session.get(DativoLote, lote.id)
        itens_atualizados = DativoItem.query.filter_by(dativo_lote_id=lote.id, grupo="sem_irrf").all()
        self.assertEqual(lote_atualizado.situacao_rpv_id, situacao_pago.id)
        self.assertEqual(lote_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(lote_atualizado.numero_se, "2026SELOTEPAGO")
        self.assertTrue(itens_atualizados)
        self.assertTrue(
            all(item.situacao_rpv_id == situacao_pago.id for item in itens_atualizados)
        )
        self.assertTrue(
            all(item.data_pagamento == self._data_base_mes_atual() for item in itens_atualizados)
        )
        self.assertTrue(all(item.numero_se == "2026SELOTEPAGO" for item in itens_atualizados))

    def test_item_com_irrf_carimba_data_pagamento_quando_marca_pago(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Item Pago Automatico",
            cpf_original="12345678901",
        )

        resposta = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data={
                "valor_irrf": "700,00",
                "data_pagamento": "",
                "nota_empenho": "2026NEITEMPAGO",
                "numero_se": "2026SEITEMPAGO",
                "ordem_bancaria": "2026OBITEMPAGO",
                "ob_imposto": "",
                "situacao_rpv_id": str(situacao_pago.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "observacoes": "",
                "reinf_status": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertEqual(item_atualizado.situacao_rpv_id, situacao_pago.id)
        self.assertEqual(item_atualizado.data_pagamento, self._data_base_mes_atual())
        self.assertEqual(item_atualizado.numero_se, "2026SEITEMPAGO")

    def test_rpv_normal_exige_confirmacao_para_corrigir_valor_bruto(self):
        registro = self._criar_rpv(
            nome_beneficiario="RPV Corrige Bruto",
            valor_bruto=Decimal("8000.00"),
            valor_irrf=Decimal("500.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )

        resposta_get = self.client.get(f"/rpvs/{registro.id}/editar")
        html_get = resposta_get.get_data(as_text=True)

        self.assertEqual(resposta_get.status_code, 200)
        self.assertIn("Financeiro", html_get)
        self.assertIn("Editar valor bruto", html_get)
        self.assertIn('data-sensitive-value-edit', html_get)
        self.assertIn('name="confirmar_edicao_valor_bruto"', html_get)
        self.assertIn("readonly", html_get)

        dados_correcao = {
            "exercicio": "2026-03",
            "tipo_rpv_id": str(registro.tipo_rpv_id),
            "nome_beneficiario": registro.nome_beneficiario,
            "tipo_documento": registro.tipo_documento,
            "documento_original": registro.documento_original,
            "valor_bruto": "7500,00",
            "valor_irrf": "500,00",
            "sem_irrf": "",
            "nota_empenho": "",
            "numero_se": "",
            "ordem_bancaria": "",
            "ob_imposto": "",
            "situacao_empenho_id": str(self.situacao_empenho_id),
            "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
            "data_pagamento": "",
            "data_pagamento_irrf": "",
            "observacoes": "",
            "confirmar_data_pagamento_manual": "0",
        }

        resposta_sem_confirmar = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data=dados_correcao,
            follow_redirects=True,
        )
        html_sem_confirmar = resposta_sem_confirmar.get_data(as_text=True)

        self.assertEqual(resposta_sem_confirmar.status_code, 200)
        self.assertIn("Confirme a edicao do valor bruto antes de salvar.", html_sem_confirmar)
        db.session.expire_all()
        registro_sem_confirmar = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_sem_confirmar.valor_bruto, Decimal("8000.00"))
        self.assertEqual(registro_sem_confirmar.valor_irrf, Decimal("500.00"))

        dados_correcao["confirmar_edicao_valor_bruto"] = "1"
        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data=dados_correcao,
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.valor_bruto, Decimal("7500.00"))
        self.assertEqual(registro_atualizado.valor_irrf, Decimal("500.00"))
        self.assertEqual(registro_atualizado.valor_liquido, Decimal("7000.00"))
        historico = HistoricoAlteracao.query.filter_by(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
            acao="Alteração manual",
        ).order_by(HistoricoAlteracao.id.desc()).first()
        self.assertIsNotNone(historico)
        self.assertTrue(
            any(
                alteracao.get("campo") == "valor_bruto"
                and alteracao.get("antes") == "R$ 8.000,00"
                and alteracao.get("depois") == "R$ 7.500,00"
                for alteracao in historico.alteracoes
            )
        )

    def test_item_com_irrf_permite_corrigir_valor_bruto(self):
        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Item Corrige Bruto",
            cpf_original="12345678901",
            valor_bruto=Decimal("7000.00"),
            valor_irrf=Decimal("700.00"),
        )

        resposta_get = self.client.get(f"/dativos/itens-com-irrf/{item.id}")
        html_get = resposta_get.get_data(as_text=True)

        self.assertEqual(resposta_get.status_code, 200)
        self.assertIn('name="valor_bruto"', html_get)
        self.assertIn('data-irrf-input="valor_bruto"', html_get)
        self.assertIn('data-sensitive-value-edit', html_get)
        self.assertIn('readonly', html_get)
        self.assertIn("Editar valor bruto", html_get)
        self.assertEqual(html_get.count('name="valor_bruto"'), 1)
        self.assertLess(html_get.index("Resumo do item"), html_get.index("Editar valor bruto"))
        self.assertLess(html_get.index("Editar valor bruto"), html_get.index("Andamento do item"))

        dados_correcao = {
            "valor_bruto": "6500,00",
            "valor_irrf": "450,00",
            "data_pagamento": "",
            "nota_empenho": "",
            "numero_se": "",
            "ordem_bancaria": "",
            "ob_imposto": "",
            "situacao_rpv_id": str(self.situacao_empenho_id),
            "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
            "observacoes": "",
            "reinf_status": "",
        }

        resposta_sem_confirmar = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data=dados_correcao,
            follow_redirects=True,
        )
        html_sem_confirmar = resposta_sem_confirmar.get_data(as_text=True)

        self.assertEqual(resposta_sem_confirmar.status_code, 200)
        self.assertIn("Confirme a edicao do valor bruto antes de salvar.", html_sem_confirmar)
        db.session.expire_all()
        item_sem_confirmar = db.session.get(DativoItem, item.id)
        self.assertEqual(item_sem_confirmar.valor_bruto, Decimal("7000.00"))
        self.assertEqual(item_sem_confirmar.valor_irrf, Decimal("700.00"))

        dados_correcao["confirmar_edicao_valor_bruto"] = "1"
        resposta = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data=dados_correcao,
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertEqual(item_atualizado.valor_bruto, Decimal("6500.00"))
        self.assertEqual(item_atualizado.valor_irrf, Decimal("450.00"))
        self.assertEqual(item_atualizado.valor_liquido, Decimal("6050.00"))
        historico = HistoricoAlteracao.query.filter_by(
            entidade_tipo="dativo_item",
            entidade_id=item.id,
            acao="Alteração manual",
        ).order_by(HistoricoAlteracao.id.desc()).first()
        self.assertIsNotNone(historico)
        alteracoes = historico.alteracoes
        self.assertTrue(
            any(
                alteracao.get("campo") == "valor_bruto"
                and alteracao.get("antes") == "R$ 7.000,00"
                and alteracao.get("depois") == "R$ 6.500,00"
                for alteracao in alteracoes
            )
        )

    def test_telas_pos_cadastro_exigem_liberacao_para_editar_valor_bruto(self):
        registro = self._criar_rpv(
            nome_beneficiario="RPV Protege Bruto",
            processo_edoc="CI-PROTEGE-BRUTO",
            numero_processo="PROC-PROTEGE-BRUTO",
        )
        _, lote, itens_sem_irrf = self._criar_dativo_sem_irrf(
            processo_edoc="CI-PROTEGE-SEM",
            itens=[
                {
                    "nome_beneficiario": "Dativo Sem Protege Bruto",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )
        _, item_com_irrf = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Dativo Com Protege Bruto",
            cpf_original="12345678901",
        )

        urls = [
            f"/rpvs/{registro.id}/editar",
            f"/dativos/lotes-sem-irrf/{lote.id}/item/{itens_sem_irrf[0].id}/editar",
            f"/dativos/itens-com-irrf/{item_com_irrf.id}",
        ]

        for url in urls:
            with self.subTest(url=url):
                resposta = self.client.get(url)
                html = resposta.get_data(as_text=True)

                self.assertEqual(resposta.status_code, 200)
                self.assertIn('name="confirmar_edicao_valor_bruto"', html)
                self.assertIn('data-sensitive-value-edit', html)
                self.assertIn('data-sensitive-value-input', html)
                self.assertIn('data-sensitive-value-action', html)
                self.assertIn("Editar valor bruto", html)
                self.assertIn("historico", html.lower())

    def test_dativo_lote_exige_confirmacao_para_data_pagamento_manual(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, lote, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-DATIVO-CONFIRMA")

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/salvar",
            data={
                "nota_empenho": "2026NELOTECONF",
                "ordem_bancaria": "2026OBLOTECONF",
                "data_pagamento": "2026-02-20",
                "situacao_rpv_id": str(situacao_pago.id),
                "observacoes": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Confirme a alteracao manual da data do pagamento.", html)
        lote_atualizado = db.session.get(DativoLote, lote.id)
        self.assertIsNone(lote_atualizado.data_pagamento)

    def test_dativo_lote_permuta_data_pagamento_manual_com_confirmacao(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-CONFIRMA-OK",
            itens=[
                {
                    "nome_beneficiario": "Beneficiario Lote Manual",
                    "valor_bruto": Decimal("1200.00"),
                }
            ],
        )

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/salvar",
            data={
                "nota_empenho": "2026NELOTECONFOK",
                "ordem_bancaria": "2026OBLOTECONFOK",
                "data_pagamento": "2026-02-20",
                "situacao_rpv_id": str(situacao_pago.id),
                "observacoes": "",
                "confirmar_data_pagamento_manual": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        lote_atualizado = db.session.get(DativoLote, lote.id)
        self.assertEqual(lote_atualizado.data_pagamento, date(2026, 2, 20))
        itens_atualizados = DativoItem.query.filter_by(dativo_lote_id=lote.id, grupo="sem_irrf").all()
        self.assertTrue(itens_atualizados)
        self.assertTrue(all(item.data_pagamento == date(2026, 2, 20) for item in itens_atualizados))

    def test_cadastro_de_item_em_lote_pago_herda_pagamento_e_status_do_lote(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, lote, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-DATIVO-HERDA")
        lote.situacao_rpv_id = situacao_pago.id
        lote.data_pagamento = date(2026, 2, 20)
        lote.numero_se = "SE-HERDADA-001"
        db.session.commit()

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/item/novo",
            data={
                "nome_beneficiario": "Novo Beneficiario Herdado",
                "cpf_original": "12345678909",
                "numero_processo": "PROC-HERDADO-001",
                "valor_bruto": "1000,00",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item = (
            DativoItem.query.filter_by(
                dativo_lote_id=lote.id,
                grupo="sem_irrf",
                numero_processo="PROC-HERDADO-001",
            )
            .order_by(DativoItem.id.desc())
            .first()
        )
        self.assertIsNotNone(item)
        self.assertEqual(item.situacao_rpv_id, situacao_pago.id)
        self.assertEqual(item.data_pagamento, date(2026, 2, 20))
        self.assertEqual(item.numero_se, "SE-HERDADA-001")

    def test_atualizacao_rapida_lote_cancelado_limpa_pagamento_dos_itens_filhos(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        situacao_cancelado = SituacaoEmpenho(
            nome="Cancelado",
            cor_badge="badge-slate",
            ordem_fluxo=99,
            ativo=True,
            is_final=True,
        )
        db.session.add_all([situacao_pago, situacao_cancelado])
        db.session.commit()

        _, lote, itens = self._criar_dativo_sem_irrf(processo_edoc="CI-DATIVO-CANCELADO")
        lote.situacao_rpv_id = situacao_pago.id
        lote.data_pagamento = date(2026, 2, 20)
        for item in itens:
            item.situacao_rpv_id = situacao_pago.id
            item.data_pagamento = date(2026, 2, 20)
        db.session.commit()

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/atualizacao-rapida",
            data={
                "nota_empenho": "",
                "ordem_bancaria": "",
                "situacao_rpv_id": str(situacao_cancelado.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        db.session.expire_all()
        lote_atualizado = db.session.get(DativoLote, lote.id)
        itens_atualizados = DativoItem.query.filter_by(dativo_lote_id=lote.id, grupo="sem_irrf").all()
        self.assertEqual(lote_atualizado.situacao_rpv_id, situacao_cancelado.id)
        self.assertIsNone(lote_atualizado.data_pagamento)
        self.assertTrue(all(item.situacao_rpv_id == situacao_cancelado.id for item in itens_atualizados))
        self.assertTrue(all(item.data_pagamento is None for item in itens_atualizados))

    def test_item_com_irrf_exige_confirmacao_para_data_pagamento_manual(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Item Confirmacao Data Manual",
            cpf_original="12345678901",
        )

        resposta = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data={
                "valor_irrf": "700,00",
                "data_pagamento": "2026-02-20",
                "nota_empenho": "2026NEITEMCONF",
                "ordem_bancaria": "2026OBITEMCONF",
                "ob_imposto": "",
                "situacao_rpv_id": str(situacao_pago.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "observacoes": "",
                "reinf_status": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Confirme a alteracao manual da data do pagamento.", html)
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertIsNone(item_atualizado.data_pagamento)

    def test_item_com_irrf_permuta_data_pagamento_manual_com_confirmacao(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Item Confirmacao Data Manual OK",
            cpf_original="12345678901",
        )

        resposta = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data={
                "valor_irrf": "700,00",
                "data_pagamento": "2026-02-20",
                "nota_empenho": "2026NEITEMCONFOK",
                "ordem_bancaria": "2026OBITEMCONFOK",
                "ob_imposto": "",
                "situacao_rpv_id": str(situacao_pago.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "observacoes": "",
                "reinf_status": "",
                "confirmar_data_pagamento_manual": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertEqual(item_atualizado.data_pagamento, date(2026, 2, 20))

    def test_item_com_irrf_exibe_status_reinf_como_leitura_e_preserva_valor(self):
        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Item REINF Cancelado",
            cpf_original="12345678901",
        )
        item.reinf_status = "Concluído"
        db.session.commit()

        resposta_get = self.client.get(f"/dativos/itens-com-irrf/{item.id}")
        html_get = resposta_get.get_data(as_text=True)

        self.assertEqual(resposta_get.status_code, 200)
        self.assertNotIn('name="reinf_status"', html_get)
        self.assertIn("Atualize esse status somente na aba REINF mensal.", html_get)
        self.assertIn("Concluído", html_get)

        resposta = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data={
                "valor_irrf": "700,00",
                "data_pagamento": "2026-03-22",
                "nota_empenho": "",
                "ordem_bancaria": "",
                "ob_imposto": "",
                "situacao_rpv_id": str(self.situacao_empenho_id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "observacoes": "",
                "reinf_status": "Cancelado",
                "confirmar_data_pagamento_manual": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertEqual(item_atualizado.reinf_status, "Concluído")

    def test_acompanhamento_exibe_documento_formatado_na_visao_resumida(self):
        self._criar_rpv(
            nome_beneficiario="Empresa Resumida",
            documento_original="12345678000199",
        )

        resposta = self.client.get("/rpvs/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("CNPJ: 12.345.678/0001-99", html)

    def test_dativos_exibem_cnpj_formatado_na_visao_resumida(self):
        _, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-DATIVO-CNPJ",
            nome_beneficiario="Fornecedor Dativo",
            cpf_original="98765432000155",
        )

        resposta = self.client.get("/dativos/cis")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(item.nome_beneficiario, html)
        self.assertIn("CNPJ: 98.765.432/0001-55", html)

    def test_acompanhamento_exibe_presets_e_colunas_fixas(self):
        self._criar_rpv(
            nome_beneficiario="Preset RPV",
            valor_irrf=Decimal("150.00"),
        )

        resposta = self.client.get("/rpvs/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('data-view-scope="rpvs"', html)
        self.assertIn("Visão da grade", html)
        self.assertIn("Resumida", html)
        self.assertIn("Fiscal", html)
        self.assertIn("Pagamento", html)
        self.assertIn("sticky-left-1", html)
        self.assertIn("sticky-right-1", html)
        self.assertIn("summary-status-row", html)
        self.assertIn("Empenho:", html)
        self.assertIn("IRRF:", html)
        self.assertIn("IRRF: R$ 150,00", html)
        self.assertIn("Copiar", html)

    def test_dativos_exibe_presets_e_colunas_fixas(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-PRESET-DATIVO",
            itens=[
                {"nome_beneficiario": "Beneficiario Preset", "valor_bruto": Decimal("3500.00")},
            ],
        )

        resposta = self.client.get("/dativos/cis")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('data-view-scope="dativos"', html)
        self.assertIn("Visão da grade", html)
        self.assertIn("Resumida", html)
        self.assertIn("Fiscal", html)
        self.assertIn("Pagamento", html)
        self.assertIn("sticky-left-1", html)
        self.assertIn("sticky-right-1", html)
        self.assertIn("summary-status-row", html)
        self.assertIn("RPV:", html)
        self.assertIn("IRRF:", html)
        self.assertIn("IRRF: Sem IRRF", html)
        self.assertIn("Copiar", html)

    def test_dashboard_exibe_fila_critica_irrf_na_home(self):
        self._criar_rpv(
            nome_beneficiario="Painel Critico",
            valor_irrf=None,
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("O que pede continuidade agora", html)
        self.assertIn("Sinalizacao geral de RPVs normais", html)
        self.assertIn("Fila ativa por modulo", html)
        self.assertIn("Sinalizacao geral de RPVs dativos", html)
        self.assertNotIn("Dativos pagos em", html)
        self.assertNotIn("priority-summary-row", html)
        self.assertIn("Pendencias operacionais de IRRF", html)
        self.assertIn("Fechamento REINF de", html)
        self.assertIn("Abrir REINF da competencia", html)
        self.assertIn("Painel Critico", html)
        self.assertIn("Responsavel: Usuário Teste", html)

    def test_dashboard_home_sinaliza_rpvs_normais_por_responsavel_e_status(self):
        usuario_secundario = User(
            nome="Gabriel Apoio",
            login="gabriel.apoio.dashboard",
            email="gabriel.apoio.dashboard@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        situacao_se = self._criar_situacao_empenho(
            "SE Aguardando Aprovação",
            ordem_fluxo=2,
            is_final=False,
        )
        situacao_retorno = self._criar_situacao_empenho(
            "Aguardando Retorno Banco",
            ordem_fluxo=3,
            is_final=False,
        )

        self._criar_rpv(
            nome_beneficiario="RPV Sem Tratamento Setor",
            valor_irrf=Decimal("0.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            elaborador_id=usuario_secundario.id,
        )
        registro_se = self._criar_rpv(
            nome_beneficiario="RPV SE Setor",
            valor_irrf=Decimal("0.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            elaborador_id=usuario_secundario.id,
        )
        registro_retorno = self._criar_rpv(
            nome_beneficiario="RPV Retorno Banco Setor",
            valor_irrf=Decimal("0.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            elaborador_id=self.user_id,
        )
        registro_se.situacao_empenho_id = situacao_se.id
        registro_retorno.situacao_empenho_id = situacao_retorno.id
        db.session.commit()

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("RPVs normais criticos: 3", html)
        self.assertIn("Responsaveis sinalizados: 2", html)
        self.assertIn("Gabriel Apoio", html)
        self.assertIn("Sem tratamento", html)
        self.assertIn("SE aguardando aprovacao", html)
        self.assertIn("Aguardando retorno banco", html)
        self.assertIn(f"responsavel={usuario_secundario.id}", html)
        self.assertIn(f"situacao_empenho_id={situacao_se.id}", html)
        self.assertIn(f"situacao_empenho_id={situacao_retorno.id}", html)

    def test_dashboard_home_sinaliza_rpvs_dativos_por_responsavel_e_status(self):
        usuario_secundario = User(
            nome="Equipe Dativos",
            login="equipe.dativos.dashboard",
            email="equipe.dativos.dashboard@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("Senha1234")
        db.session.add(usuario_secundario)
        db.session.commit()

        situacao_se = self._criar_situacao_empenho(
            "SE Aguardando Aprovação",
            ordem_fluxo=2,
            is_final=False,
        )
        situacao_retorno = self._criar_situacao_empenho(
            "Aguardando Retorno Banco",
            ordem_fluxo=3,
            is_final=False,
        )

        _, lote_secundario, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DATIVO-HOME-UM",
            itens=[
                {"nome_beneficiario": "Dativo Sem Tratamento Um", "valor_bruto": Decimal("1800.00")},
            ],
            responsavel_id=usuario_secundario.id,
        )
        _, item_retorno = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-DATIVO-HOME-DOIS",
            nome_beneficiario="Dativo Retorno Banco",
            valor_bruto=Decimal("4200.00"),
            valor_irrf=Decimal("420.00"),
            responsavel_id=self.user_id,
        )
        lote_secundario.situacao_rpv_id = situacao_se.id
        item_retorno.situacao_rpv_id = situacao_retorno.id
        db.session.commit()

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Sinalizacao geral de RPVs dativos", html)
        self.assertIn("Equipe Dativos", html)
        self.assertIn("1 lote(s) sem IRRF | 0 item(ns) com IRRF", html)
        self.assertIn("0 lote(s) sem IRRF | 1 item(ns) com IRRF", html)
        self.assertIn('/dativos/cis?responsavel=todos', html)
        self.assertIn(f"responsavel={usuario_secundario.id}", html)
        self.assertIn(f"situacao_rpv_id={situacao_se.id}", html)
        self.assertIn(f"situacao_rpv_id={situacao_retorno.id}", html)

    def test_dashboard_home_sinaliza_modulo_zerado_sem_destacar_pendencia(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-HOME-CLEAR",
            itens=[
                {"nome_beneficiario": "Beneficiario Ativo Home", "valor_bruto": Decimal("2500.00")},
            ],
        )

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Sem pendencias", html)
        self.assertIn("Tudo em dia", html)
        self.assertNotIn("Abrir RPVs normais", html)

    def test_dashboard_home_sinaliza_pendencias_documentais_do_responsavel(self):
        pendencia = RPVPendenciaDocumento(
            exercicio="2026-03",
            processo_edoc="CI-HOME-PEND",
            numero_processo="PROC-HOME-PEND",
            data_ci=date(2026, 3, 20),
            tipo_rpv_id=self.tipo_honorarios_id,
            responsavel_id=self.user_id,
            nome_beneficiario="Pendencia Home",
            nome_beneficiario_normalizado="",
            tipo_documento="CPF",
            documento_original="12345678901",
            documento_normalizado="",
            valor_bruto=Decimal("1800.00"),
            valor_irrf=None,
            sem_irrf=True,
            observacoes=None,
            motivo_pendencia="",
            status="aberta",
            criado_por_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        pendencia.atualizar_campos_derivados()
        db.session.add(pendencia)
        db.session.commit()

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendencias documentais", html)
        self.assertIn("Abrir pendencias", html)

    def test_dashboard_nao_alerta_dativo_sem_irrf_pelo_total_do_lote(self):
        self._criar_dativo_sem_irrf(
            itens=[
                {"nome_beneficiario": "Beneficiario Um", "valor_bruto": Decimal("3000.00")},
                {"nome_beneficiario": "Beneficiario Dois", "valor_bruto": Decimal("2600.00")},
            ]
        )

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Alertas criticos de IRRF: 0", html)
        self.assertNotIn("Beneficiario Um", html)
        self.assertNotIn("Beneficiario Dois", html)

    def test_dashboard_alerta_dativo_sem_irrf_por_item_no_corte(self):
        self._criar_dativo_sem_irrf(
            itens=[
                {"nome_beneficiario": "Beneficiario no Corte", "valor_bruto": Decimal("5040.00")},
                {"nome_beneficiario": "Beneficiario Abaixo", "valor_bruto": Decimal("2200.00")},
            ]
        )

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Alertas criticos de IRRF: 1", html)
        self.assertIn("Beneficiario no Corte", html)
        self.assertNotIn("Beneficiario Abaixo", html)

    def test_dashboard_nao_alerta_item_sem_irrf_quando_dispensa_foi_confirmada(self):
        self._criar_dativo_sem_irrf(
            itens=[
                {
                    "nome_beneficiario": "Beneficiario Confirmado",
                    "valor_bruto": Decimal("5040.00"),
                    "dispensa_irrf_confirmada": True,
                }
            ]
        )

        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Alertas criticos de IRRF: 0", html)
        self.assertNotIn("Beneficiario Confirmado", html)

    def test_dashboard_home_separa_reinf_cancelado_de_concluido(self):
        registro = self._criar_rpv(
            nome_beneficiario="Dashboard REINF Cancelado",
            valor_irrf=Decimal("510.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 20),
        )

        resposta_status = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "reinf_status": "Cancelado",
            },
            follow_redirects=False,
        )
        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta_status.status_code, 302)
        self.assertEqual(resposta.status_code, 200)
        self.assertRegex(
            html,
            re.compile(
                r"Pendentes de envio</span>\s*<strong>.*?<span class=\"value-amount\">0</span>",
                re.S,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r"Concluidos</span>\s*<strong>.*?<span class=\"value-amount\">0</span>",
                re.S,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r"Cancelados</span>\s*<strong>.*?<span class=\"value-amount\">1</span>",
                re.S,
            ),
        )

    def test_dashboard_home_reflete_mesma_base_mensal_da_reinf(self):
        self._criar_rpv(
            nome_beneficiario="Dashboard REINF Pendente",
            valor_irrf=Decimal("310.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 11),
        )
        registro_concluido = self._criar_rpv(
            nome_beneficiario="Dashboard REINF Concluido",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 12),
        )
        registro_concluido.reinf_status = "Concluído"
        registro_cancelado = self._criar_rpv(
            nome_beneficiario="Dashboard REINF Cancelado Base",
            valor_irrf=Decimal("510.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 13),
        )
        registro_cancelado.reinf_status = "Cancelado"
        db.session.commit()

        with self.app.test_request_context("/"):
            base_reinf = _coletar_base_reinf("2026-03", "todos", "")
        resposta = self.client.get("/")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(base_reinf), 3)
        self.assertRegex(
            html,
            re.compile(
                r"Registros do mes</span>\s*<strong>.*?<span class=\"value-amount\">3</span>",
                re.S,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r"Pendentes de envio</span>\s*<strong>.*?<span class=\"value-amount\">1</span>",
                re.S,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r"Concluidos</span>\s*<strong>.*?<span class=\"value-amount\">1</span>",
                re.S,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r"Cancelados</span>\s*<strong>.*?<span class=\"value-amount\">1</span>",
                re.S,
            ),
        )

    def test_reinf_ignora_registro_sem_data_pagamento(self):
        self._criar_rpv(
            nome_beneficiario="Sem Data REINF",
            valor_irrf=Decimal("500.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("Sem Data REINF", html)

    def test_bi_filtra_origem_e_exporta_csv(self):
        self._criar_rpv(
            nome_beneficiario="RPV Normal BI",
            valor_irrf=Decimal("800.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 15),
        )
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-BI-1",
            itens=[
                {
                    "nome_beneficiario": "Dativo BI",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-BI-1",
                    "valor_bruto": Decimal("5100.00"),
                }
            ],
        )

        resposta = self.client.get(
            "/bi?origem=dativo_sem_irrf&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("BI operacional", html)
        self.assertIn("Filtros de pesquisa", html)
        self.assertIn("filters-toggle", html)
        self.assertIn("Dativo BI", html)
        self.assertNotIn("RPV Normal BI", html)

        csv_resposta = self.client.get(
            "/bi/exportar.csv?origem=dativo_sem_irrf&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        conteudo = csv_resposta.get_data(as_text=True)

        self.assertEqual(csv_resposta.status_code, 200)
        self.assertIn("text/csv", csv_resposta.headers.get("Content-Type", ""))
        self.assertIn("Dativo BI", conteudo)
        self.assertIn("Grupo", conteudo)
        self.assertIn("Fluxo IRRF", conteudo)
        self.assertIn("Comum", conteudo)
        self.assertIn("Sem IRRF", conteudo)
        self.assertNotIn("RPV Normal BI", conteudo)

    def test_bi_filtra_reinf_cancelado(self):
        registro_cancelado = self._criar_rpv(
            nome_beneficiario="RPV BI Cancelado",
            valor_irrf=Decimal("420.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 14),
        )
        registro_cancelado.reinf_status = "Cancelado"

        registro_concluido = self._criar_rpv(
            nome_beneficiario="RPV BI Concluido",
            valor_irrf=Decimal("390.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro_concluido.reinf_status = "Conclu\u00eddo"
        db.session.commit()

        resposta = self.client.get(
            "/bi?reinf=cancelado&pagamento=pagos&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('value="cancelado"', html)
        self.assertIn("RPV BI Cancelado", html)
        self.assertNotIn("RPV BI Concluido", html)

    def test_bi_filtra_por_responsavel_pagamento_e_documento_limpo(self):
        usuario_secundario = User(
            nome="Usuário Secundário",
            login="secundario",
            email="secundario@controle-rpv.local",
            ativo=True,
            is_admin=False,
        )
        usuario_secundario.set_password("senha123")
        db.session.add(usuario_secundario)
        db.session.commit()

        registro_meu = self._criar_rpv(
            nome_beneficiario="BI Meu Pago",
            valor_irrf=Decimal("180.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 12),
            documento_original="123.456.789-01",
        )
        self._criar_rpv(
            nome_beneficiario="BI Meu Sem Data",
            valor_irrf=Decimal("210.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
            documento_original="555.444.333-22",
        )
        registro_outro = self._criar_rpv(
            nome_beneficiario="BI Outro Pago",
            valor_irrf=Decimal("240.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 13),
            documento_original="999.888.777-66",
        )
        registro_outro.elaborador_id = usuario_secundario.id
        db.session.commit()

        resposta = self.client.get(
            "/bi?q=12345678901&responsavel=meus&pagamento=pagos&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Situacao de pagamento", html)
        self.assertIn("Maiores volumes por responsavel", html)
        self.assertIn("Grupo de cota", html)
        self.assertIn("BI Meu Pago", html)
        self.assertIn("12345678901", html)
        self.assertNotIn("BI Meu Sem Data", html)
        self.assertNotIn("BI Outro Pago", html)
        self.assertEqual(registro_meu.elaborador_id, self.user_id)
        self.assertEqual(registro_outro.elaborador_id, usuario_secundario.id)

    def test_bi_filtra_grupo_de_cota_pessoal(self):
        tipo_trabalhista = TipoRPV(nome="RPV trabalhista", ativo=True, ordem_exibicao=3)
        tipo_pericial = TipoRPV(nome="RPV periciais", ativo=True, ordem_exibicao=4)
        db.session.add_all([tipo_trabalhista, tipo_pericial])
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="RPV Pessoal BI",
            tipo_rpv_id=self.tipo_pessoal_id,
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 11),
        )
        self._criar_rpv(
            nome_beneficiario="RPV Trabalhista BI",
            tipo_rpv_id=tipo_trabalhista.id,
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 12),
        )
        self._criar_rpv(
            nome_beneficiario="RPV Pericial BI",
            tipo_rpv_id=tipo_pericial.id,
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 13),
        )
        self._criar_rpv(
            nome_beneficiario="RPV Comum BI",
            tipo_rpv_id=self.tipo_honorarios_id,
            valor_irrf=Decimal("150.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 14),
        )

        resposta = self.client.get(
            "/bi?grupo_cota=pessoal&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("RPV Pessoal BI", html)
        self.assertIn("RPV Trabalhista BI", html)
        self.assertNotIn("RPV Pericial BI", html)
        self.assertNotIn("RPV Comum BI", html)

    def test_bi_reconhece_tipo_periciais_no_grupo_pericial(self):
        tipo_pericial = TipoRPV(nome="RPV periciais", ativo=True, ordem_exibicao=3)
        db.session.add(tipo_pericial)
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="RPV Periciais BI",
            tipo_rpv_id=tipo_pericial.id,
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 13),
        )
        self._criar_rpv(
            nome_beneficiario="RPV Comum Fora",
            tipo_rpv_id=self.tipo_honorarios_id,
            valor_irrf=Decimal("150.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 14),
        )

        resposta = self.client.get(
            "/bi?grupo_cota=pericial&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("RPV Periciais BI", html)
        self.assertNotIn("RPV Comum Fora", html)

    def test_bi_destaca_cota_previsao_e_beneficiarios_por_fluxo(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-BI-PAGO-ATUAL",
            itens=[
                {
                    "nome_beneficiario": "Dativo Pago Atual",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-BI-ATUAL",
                    "valor_bruto": Decimal("5100.00"),
                    "data_pagamento": date(2026, 3, 18),
                }
            ],
        )
        _, item_com_irrf = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-BI-PAGO-ANTERIOR",
            nome_beneficiario="Dativo Pago Anterior",
            cpf_original="99988877766",
            numero_processo="PROC-BI-ANTERIOR",
            valor_bruto=Decimal("7300.00"),
            valor_irrf=Decimal("630.00"),
        )
        item_com_irrf.data_pagamento = date(2026, 2, 12)
        db.session.commit()

        resposta = self.client.get(
            "/bi?pagamento=pagos&competencia_inicial=2026-01&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Janela modular dos graficos", html)
        self.assertIn("IRRF retido por mes", html)
        self.assertIn("Leitura executiva do IRRF", html)
        self.assertIn("O que ainda pede tratamento no recorte", html)
        self.assertIn("Dativos dentro do grupo comum", html)
        self.assertIn("Top beneficiarios pagos com separacao fiscal", html)
        self.assertIn("Origem e leitura dos dados", html)
        self.assertIn("Leitura principal do BI", html)

    def test_bi_conferencia_exibe_totais_mensais_e_total_geral(self):
        tipo_pericial = TipoRPV(nome="RPV periciais", ativo=True, ordem_exibicao=4)
        db.session.add(tipo_pericial)
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="Conferencia Pessoal",
            tipo_rpv_id=self.tipo_pessoal_id,
            valor_bruto=Decimal("1000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 11),
            exercicio="2026-03",
        )
        self._criar_rpv(
            nome_beneficiario="Conferencia Pericial",
            tipo_rpv_id=tipo_pericial.id,
            valor_bruto=Decimal("3000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 12),
            exercicio="2026-03",
        )
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-CONF-BI",
            exercicio="2026-03",
            itens=[
                {
                    "nome_beneficiario": "Conferencia Comum",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-CONF-BI",
                    "valor_bruto": Decimal("5000.00"),
                    "data_pagamento": date(2026, 3, 18),
                }
            ],
        )

        resposta = self.client.get(
            "/bi?visao=conferencia&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Conferencia mensal por competencia de pagamento", html)
        self.assertIn("Bruto pago, IRRF e liquido por mes", html)
        self.assertIn("Bruto pago do mes", html)
        self.assertIn("IRRF do mes", html)
        self.assertIn("Liquido do mes", html)
        self.assertIn("Totais mensais e total geral", html)
        self.assertIn("Total geral", html)
        self.assertIn("Pagamento efetivo", html)
        self.assertIn("9.000,00", html)
        self.assertIn("1.000,00", html)
        self.assertIn("3.000,00", html)
        self.assertIn("5.000,00", html)
        self.assertNotIn("Projecao seguinte", html)
        self.assertNotIn("Janela dos graficos", html)
        self.assertNotIn("Pago do mes", html)

    def test_bi_conferencia_exporta_csv_de_validacao(self):
        tipo_pericial = TipoRPV(nome="RPV periciais", ativo=True, ordem_exibicao=4)
        db.session.add(tipo_pericial)
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="CSV Conferencia Pessoal",
            tipo_rpv_id=self.tipo_pessoal_id,
            valor_bruto=Decimal("1000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 11),
            exercicio="2026-03",
        )
        self._criar_rpv(
            nome_beneficiario="CSV Conferencia Pericial",
            tipo_rpv_id=tipo_pericial.id,
            valor_bruto=Decimal("3000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 12),
            exercicio="2026-03",
        )

        resposta = self.client.get(
            "/bi/conferencia.csv?visao=conferencia&competencia_inicial=2026-03&competencia_final=2026-03"
        )
        conteudo = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("text/csv", resposta.headers.get("Content-Type", ""))
        self.assertIn("Competencia pagamento", conteudo)
        self.assertIn("Valor bruto pago", conteudo)
        self.assertIn("Total geral", conteudo)
        self.assertIn("1.000,00", conteudo)
        self.assertIn("3.000,00", conteudo)
        self.assertNotIn("Valor pago;", conteudo)
        self.assertNotIn("Valor em aberto", conteudo)

    def test_bi_conferencia_prioriza_competencia_de_pagamento_no_filtro_e_na_tabela(self):
        self._criar_rpv(
            nome_beneficiario="Pagamento Manda na Conferencia",
            documento_original="12345678901",
            valor_bruto=Decimal("3200.00"),
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 1, 19),
            exercicio="2025-12",
        )
        db.session.commit()

        resposta = self.client.get(
            "/bi?visao=conferencia&competencia_inicial=2026-01&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Conferencia mensal por competencia de pagamento", html)
        self.assertIn("Bruto pago do mes", html)
        self.assertIn("jan/2026", html)
        self.assertNotIn("dez/2025", html)

        csv_resposta = self.client.get(
            "/bi/conferencia.csv?visao=conferencia&competencia_inicial=2026-01&competencia_final=2026-03"
        )
        conteudo = csv_resposta.get_data(as_text=True)

        self.assertEqual(csv_resposta.status_code, 200)
        self.assertIn("jan/2026", conteudo)
        self.assertNotIn("dez/2025", conteudo)

    def test_bi_query_operacional_prioriza_pagamento_sobre_cadastro_em_rpvs(self):
        rpv_pago_janeiro = self._criar_rpv(
            nome_beneficiario="RPV Pago Janeiro Query",
            valor_bruto=Decimal("3200.00"),
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 1, 19),
            exercicio="2025-12",
        )
        rpv_aberto_janeiro = self._criar_rpv(
            nome_beneficiario="RPV Aberto Janeiro Query",
            valor_bruto=Decimal("2800.00"),
            valor_irrf=Decimal("0.00"),
            sem_irrf=True,
            data_pagamento=None,
            exercicio="2026-01",
        )
        rpv_pago_fevereiro = self._criar_rpv(
            nome_beneficiario="RPV Pago Fevereiro Query",
            valor_bruto=Decimal("4100.00"),
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 2, 4),
            exercicio="2026-01",
        )

        filtros = {
            "competencia_inicial": "2026-01",
            "competencia_final": "2026-01",
            "pagamento": "todos",
        }

        with self.app.test_request_context("/bi"):
            registros = _query_registros_bi(filtros, visao="operacional").all()

        ids = {registro.id for registro in registros}
        self.assertIn(rpv_pago_janeiro.id, ids)
        self.assertIn(rpv_aberto_janeiro.id, ids)
        self.assertNotIn(rpv_pago_fevereiro.id, ids)

    def test_bi_query_operacional_prioriza_pagamento_sobre_cadastro_em_dativos(self):
        _, item_pago_janeiro = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-BI-QUERY-JAN",
            nome_beneficiario="Dativo Pago Janeiro Query",
            cpf_original="12345678901",
            numero_processo="PROC-BI-QUERY-JAN",
            valor_bruto=Decimal("5300.00"),
            valor_irrf=Decimal("530.00"),
        )
        item_pago_janeiro.dativo_ci.exercicio = "2025-12"
        item_pago_janeiro.data_pagamento = date(2026, 1, 21)

        _, _, itens_abertos = self._criar_dativo_sem_irrf(
            processo_edoc="CI-BI-QUERY-ABERTO",
            exercicio="2026-01",
            itens=[
                {
                    "nome_beneficiario": "Dativo Aberto Janeiro Query",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-BI-QUERY-ABERTO",
                    "valor_bruto": Decimal("2700.00"),
                    "data_pagamento": None,
                }
            ],
        )
        item_aberto_janeiro = itens_abertos[0]

        _, item_pago_fevereiro = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-BI-QUERY-FEV",
            nome_beneficiario="Dativo Pago Fevereiro Query",
            cpf_original="99988877766",
            numero_processo="PROC-BI-QUERY-FEV",
            valor_bruto=Decimal("6100.00"),
            valor_irrf=Decimal("610.00"),
        )
        item_pago_fevereiro.dativo_ci.exercicio = "2026-01"
        item_pago_fevereiro.data_pagamento = date(2026, 2, 9)
        db.session.commit()

        filtros = {
            "competencia_inicial": "2026-01",
            "competencia_final": "2026-01",
            "pagamento": "todos",
        }

        with self.app.test_request_context("/bi"):
            itens = _query_dativos_bi(filtros, visao="operacional").all()

        ids = {item.id for item in itens}
        self.assertIn(item_pago_janeiro.id, ids)
        self.assertIn(item_aberto_janeiro.id, ids)
        self.assertNotIn(item_pago_fevereiro.id, ids)

    def test_bi_monta_series_prioritarias_e_irrf_com_janela_modular(self):
        tipo_trabalhista = TipoRPV(nome="RPV trabalhista", ativo=True, ordem_exibicao=3)
        tipo_pericial = TipoRPV(nome="RPV pericial", ativo=True, ordem_exibicao=4)
        db.session.add_all([tipo_trabalhista, tipo_pericial])
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="BI Pessoal Serie",
            tipo_rpv_id=self.tipo_pessoal_id,
            valor_bruto=Decimal("1000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 11),
        )
        self._criar_rpv(
            nome_beneficiario="BI Trabalhista Serie",
            tipo_rpv_id=tipo_trabalhista.id,
            valor_bruto=Decimal("2000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 2, 12),
        )
        self._criar_rpv(
            nome_beneficiario="BI Pericial Serie",
            tipo_rpv_id=tipo_pericial.id,
            valor_bruto=Decimal("3000.00"),
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 13),
        )
        self._criar_rpv(
            nome_beneficiario="BI Comum Serie",
            tipo_rpv_id=self.tipo_honorarios_id,
            valor_bruto=Decimal("4000.00"),
            valor_irrf=Decimal("400.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 1, 14),
        )
        self._criar_rpv(
            nome_beneficiario="BI Comum Serie Mes",
            tipo_rpv_id=self.tipo_honorarios_id,
            valor_bruto=Decimal("2500.00"),
            valor_irrf=Decimal("250.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 16),
        )
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-BI-COMUM",
            itens=[
                {
                    "nome_beneficiario": "BI Dativo Comum",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-BI-COMUM",
                    "valor_bruto": Decimal("5000.00"),
                    "data_pagamento": date(2026, 3, 18),
                }
            ],
        )

        filtros = {
            "competencia_inicial": "2026-01",
            "competencia_final": "2026-03",
            "grupo_cota": "todos",
        }

        with self.app.test_request_context("/bi"):
            dataset = _coletar_dataset_bi()
            resumo_grupos = _resumo_grupos_cota(dataset, filtros)
            series_grupos = _series_grupos_cota_bi(
                dataset,
                resumo_grupos,
                janela_meses=12,
                filtros=filtros,
            )
            resumo_irrf = _resumo_irrf_bi(
                dataset,
                competencia_referencia=resumo_grupos["competencia_referencia"],
                janela_meses=12,
            )

        grupos_por_chave = {grupo["chave"]: grupo for grupo in series_grupos}

        self.assertEqual(len(series_grupos), 3)
        self.assertEqual(len(grupos_por_chave["pessoal"]["serie"]), 12)
        self.assertEqual(len(grupos_por_chave["pericial"]["serie"]), 12)
        self.assertEqual(len(grupos_por_chave["comum"]["serie"]), 12)
        self.assertEqual(grupos_por_chave["pessoal"]["valor_total_janela"], Decimal("3000.00"))
        self.assertEqual(grupos_por_chave["pericial"]["valor_total_janela"], Decimal("3000.00"))
        self.assertEqual(grupos_por_chave["comum"]["valor_total_janela"], Decimal("11500.00"))
        self.assertEqual(resumo_irrf["irrf_mes"], Decimal("250.00"))
        self.assertEqual(resumo_irrf["acumulado_recorte"], Decimal("650.00"))
        self.assertEqual(resumo_irrf["acumulado_ano"], Decimal("650.00"))
        self.assertEqual(resumo_irrf["pagamentos_com_irrf"], 2)

    def test_bi_exibe_irrf_retido_por_beneficiario_e_competencia(self):
        self._criar_rpv(
            nome_beneficiario="Beneficiario IRRF Mensal",
            documento_original="12345678901",
            valor_irrf=Decimal("120.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        self._criar_rpv(
            nome_beneficiario="Beneficiario IRRF Mensal",
            documento_original="12345678901",
            valor_irrf=Decimal("80.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 2, 14),
        )

        resposta = self.client.get(
            "/bi?pagamento=pagos&competencia_inicial=2026-02&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Beneficiario IRRF Mensal", html)
        self.assertIn("IRRF retido:", html)
        self.assertIn("120,00", html)
        self.assertIn("80,00", html)

    def test_bi_conferencia_remove_bloco_fiscal_detalhado_e_mantem_resumo_contabil(self):
        self._criar_rpv(
            nome_beneficiario="Fiscal Tela",
            documento_original="12345678901",
            valor_bruto=Decimal("3200.00"),
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 1, 19),
            exercicio="2025-12",
        )
        _, item_fevereiro = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-FISCAL-TELA-FEV",
            nome_beneficiario="Fiscal Tela",
            cpf_original="12345678901",
            numero_processo="PROC-FISCAL-TELA-FEV",
            valor_bruto=Decimal("2800.00"),
            valor_irrf=Decimal("280.00"),
        )
        item_fevereiro.data_pagamento = date(2026, 2, 14)

        self._criar_dativo_sem_irrf(
            processo_edoc="CI-FISCAL-TELA-LIVRE",
            itens=[
                {
                    "nome_beneficiario": "Fiscal Sem Retencao",
                    "cpf_original": "99988877766",
                    "numero_processo": "PROC-FISCAL-TELA-LIVRE",
                    "valor_bruto": Decimal("1400.00"),
                    "data_pagamento": date(2026, 2, 22),
                }
            ],
        )
        db.session.commit()

        resposta = self.client.get(
            "/bi?visao=conferencia&competencia_inicial=2026-01&competencia_final=2026-03"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Conferencia mensal por competencia de pagamento", html)
        self.assertIn("Bruto pago, IRRF e liquido por mes", html)
        self.assertIn("Totais mensais e total geral", html)
        self.assertIn("Conferencia fiscal detalhada", html)
        self.assertIn("jan/2026", html)
        self.assertIn("fev/2026", html)
        self.assertIn("Total geral", html)
        self.assertNotIn("Leitura fiscal do recorte", html)
        self.assertNotIn("Conferencia fiscal anual por beneficiario", html)
        self.assertNotIn("Tabela dinamica", html)
        self.assertNotIn("Incluir sem IRRF", html)
        self.assertNotIn('name="fiscal_sem_irrf"', html)
        self.assertNotIn('name="fiscal_visualizacao"', html)

    def test_bi_nao_duplica_lote_sem_irrf_nos_totais(self):
        self._criar_dativo_sem_irrf(
            processo_edoc="CI-BI-SEM-DUPLICIDADE",
            itens=[
                {
                    "nome_beneficiario": "Dativo Um",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-DUP-1",
                    "valor_bruto": Decimal("1000.00"),
                },
                {
                    "nome_beneficiario": "Dativo Dois",
                    "cpf_original": "55566677788",
                    "numero_processo": "PROC-DUP-2",
                    "valor_bruto": Decimal("2000.00"),
                },
            ],
        )

        with self.app.test_request_context("/bi"):
            dataset = _coletar_dataset_bi()
            cards = _cards_bi(dataset)

        self.assertEqual(len(dataset), 2)
        self.assertTrue(all(row["origem_chave"] == "dativo_sem_irrf" for row in dataset))
        self.assertTrue(all(row["grupo_cota"] == "comum" for row in dataset))
        self.assertTrue(all(row["fluxo_irrf_label"] == "Sem IRRF" for row in dataset))

        cards_por_label = {card["label"]: card["valor"] for card in cards}
        pago_mes = next(card["valor"] for card in cards if card["label"].startswith("Pago em ") and "/" in card["label"])
        pago_ano = next(card["valor"] for card in cards if card["label"] == "Pago em 2026")
        dativos_pago = next(card["valor"] for card in cards if card["label"].startswith("Dativos pagos em "))
        projecao = next(card["valor"] for card in cards if card["label"].startswith("Projecao "))
        self.assertEqual(cards_por_label["Carteira em aberto"], Decimal("3000.00"))
        self.assertEqual(cards_por_label["Beneficiarios em aberto"], 2)
        self.assertEqual(cards_por_label["REINF pendente"], 0)
        self.assertEqual(pago_mes, Decimal("0.00"))
        self.assertEqual(pago_ano, Decimal("0.00"))
        self.assertEqual(dativos_pago, Decimal("0.00"))
        self.assertEqual(projecao, Decimal("0.00"))

    def test_cadastro_manual_de_item_sem_irrf_exige_confirmacao_para_processo_repetido(self):
        dativo_ci, _, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DUP-UX",
            itens=[
                {
                    "nome_beneficiario": "Duplicidade UX",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-DUP-UX",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )
        item_original = itens[0]

        resposta = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/adicionar-sem-irrf",
            data={
                "nome_beneficiario": "Duplicidade UX 2",
                "cpf_original": item_original.cpf_original,
                "numero_processo": item_original.numero_processo,
                "valor_bruto": "1500,00",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Processo já encontrado no sistema", html)
        self.assertIn("Confirmar e continuar mesmo assim", html)
        self.assertEqual(
            DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf").count(),
            1,
        )

    def test_cadastro_manual_de_item_sem_irrf_permite_confirmacao_e_registra_historico(self):
        dativo_ci, _, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DUP-CONF",
            itens=[
                {
                    "nome_beneficiario": "Duplicidade Confirmada",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-DUP-CONF",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )
        item_original = itens[0]

        resposta = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/adicionar-sem-irrf",
            data={
                "nome_beneficiario": "Duplicidade Confirmada 2",
                "cpf_original": item_original.cpf_original,
                "numero_processo": item_original.numero_processo,
                "valor_bruto": "1500,00",
                "confirmar_processo_existente": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)

        itens_atualizados = (
            DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf")
            .order_by(DativoItem.id.asc())
            .all()
        )
        self.assertEqual(len(itens_atualizados), 2)

        novo_item = itens_atualizados[-1]
        self.assertEqual(novo_item.nome_beneficiario, "Duplicidade Confirmada 2")

        eventos = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="dativo_item",
                entidade_id=novo_item.id,
            )
            .order_by(HistoricoAlteracao.id.asc())
            .all()
        )
        acoes = [evento.acao for evento in eventos]
        self.assertIn("Cadastro manual", acoes)
        self.assertIn("Confirmação de repetição de processo", acoes)
        evento_confirmacao = next(
            evento
            for evento in eventos
            if evento.acao == "Confirmação de repetição de processo"
        )
        self.assertIn("Repetição confirmada pelo operador", evento_confirmacao.resumo)

    def test_cadastro_manual_de_item_sem_irrf_reexibe_confirmacao_com_hidden_resetado(self):
        dativo_ci, _, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DUP-RESET",
            itens=[
                {
                    "nome_beneficiario": "Duplicidade Reset",
                    "cpf_original": "11122233344",
                    "numero_processo": "PROC-DUP-RESET",
                    "valor_bruto": Decimal("1500.00"),
                }
            ],
        )
        item_original = itens[0]

        resposta = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/adicionar-sem-irrf",
            data={
                "nome_beneficiario": "Duplicidade Reset 2",
                "cpf_original": item_original.cpf_original,
                "numero_processo": item_original.numero_processo,
                "valor_bruto": "1500,00",
                "observacoes": "Primeira tentativa sem confirmar",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('id="confirmar_processo_existente_sem" value="0"', html)

    def test_cadastro_manual_de_item_sem_irrf_salva_observacoes(self):
        dativo_ci, _, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-OBS-MANUAL",
            itens=[],
        )

        resposta = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/adicionar-sem-irrf",
            data={
                "nome_beneficiario": "Beneficiario Observacao",
                "cpf_original": "12345678901",
                "numero_processo": "PROC-OBS-MANUAL",
                "valor_bruto": "2200,00",
                "observacoes": "Observacao inicial do beneficiario",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item = DativoItem.query.filter_by(
            dativo_ci_id=dativo_ci.id,
            grupo="sem_irrf",
            numero_processo="PROC-OBS-MANUAL",
        ).one()
        self.assertEqual(item.observacoes, "Observacao inicial do beneficiario")

    def test_importacao_unica_classifica_cpf_e_cnpj_sem_quebrar_fluxo_atual(self):
        dativo_ci = DativoCI(
            exercicio="2026-03",
            processo_edoc="CI-IMPORT-UNICO",
            data_ci=date(2026, 3, 24),
            descricao="Importacao unica",
            criado_por_id=self.user_id,
            responsavel_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.commit()

        caminho_planilha = self._criar_planilha_ods(
            [
                {
                    "Nome": "CPF Lote",
                    "CPF/CNPJ": "11122233344",
                    "Processo": "PROC-UNICO-SEM",
                    "Valor": "3200,00",
                },
                {
                    "Nome": "CPF Corte",
                    "CPF/CNPJ": "55566677788",
                    "Processo": "PROC-UNICO-COM",
                    "Valor": "6500,00",
                },
                {
                    "Nome": "Empresa Corte",
                    "CPF/CNPJ": "12345678000199",
                    "Processo": "PROC-UNICO-CNPJ",
                    "Valor": "9900,00",
                },
            ]
        )

        with open(caminho_planilha, "rb") as arquivo:
            resposta = self.client.post(
                f"/dativos/ci/{dativo_ci.id}/importar-unico/analisar",
                data={"arquivo_unico": (arquivo, "dativos_unico.ods")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        html = resposta.get_data(as_text=True)
        token_match = re.search(r'name="preview_token" value="([^"]+)"', html)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Previa da planilha unica", html)
        self.assertIn("CPF Lote", html)
        self.assertIn("CPF Corte", html)
        self.assertIn("Empresa Corte", html)
        self.assertIn("CNPJ mantido em sem IRRF", html)
        self.assertIsNotNone(token_match)

        resposta_confirmacao = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/importar-unico/confirmar",
            data={"preview_token": token_match.group(1)},
            follow_redirects=False,
        )

        self.assertEqual(resposta_confirmacao.status_code, 302)

        itens_sem_irrf = DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf").all()
        itens_com_irrf = DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="com_irrf").all()

        self.assertEqual(len(itens_sem_irrf), 2)
        self.assertEqual(len(itens_com_irrf), 1)
        self.assertTrue(any(item.nome_beneficiario == "Empresa Corte" for item in itens_sem_irrf))
        self.assertTrue(any(item.nome_beneficiario == "CPF Corte" for item in itens_com_irrf))

    def test_importacao_unica_sinaliza_repetidos_com_linha_e_aguarda_confirmacao(self):
        dativo_ci = DativoCI(
            exercicio="2026-03",
            processo_edoc="CI-IMPORT-DUP",
            data_ci=date(2026, 3, 24),
            descricao="Importacao unica duplicada",
            criado_por_id=self.user_id,
            responsavel_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.commit()

        caminho_planilha = self._criar_planilha_ods(
            [
                {
                    "Nome": "Duplicado Um",
                    "CPF/CNPJ": "11122233344",
                    "Processo": "PROC-DUP-UNICO",
                    "Valor": "3100,00",
                },
                {
                    "Nome": "Duplicado Dois",
                    "CPF/CNPJ": "11122233344",
                    "Processo": "PROC-DUP-UNICO",
                    "Valor": "3100,00",
                },
            ],
            nome_arquivo="dativos_dup.ods",
        )

        with open(caminho_planilha, "rb") as arquivo:
            resposta = self.client.post(
                f"/dativos/ci/{dativo_ci.id}/importar-unico/analisar",
                data={"arquivo_unico": (arquivo, "dativos_dup.ods")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        html = resposta.get_data(as_text=True)
        token_match = re.search(r'name="preview_token" value="([^"]+)"', html)
        pendencia_match = re.search(r'name="pendencias_confirmadas" value="([^"]+)"', html)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendencias para confirmacao", html)
        self.assertIn("Repete documento e processo da linha 2", html)
        self.assertIn("Linha repetida na mesma classificacao", html)
        self.assertIn("Duplicado Dois", html)
        self.assertIsNotNone(token_match)
        self.assertIsNotNone(pendencia_match)

        resposta_confirmacao = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/importar-unico/confirmar",
            data={
                "preview_token": token_match.group(1),
                "pendencias_confirmadas": [pendencia_match.group(1)],
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta_confirmacao.status_code, 302)
        itens = (
            DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf")
            .order_by(DativoItem.id.asc())
            .all()
        )
        self.assertEqual(len(itens), 2)

    def test_importacao_unica_trata_processo_existente_no_sistema_como_pendencia(self):
        self._criar_rpv(
            nome_beneficiario="Processo Ja Existente",
            documento_original="52998224725",
            processo_edoc="CI-CROSS-UNICO",
            numero_processo="PROC-CROSS-UNICO",
        )

        dativo_ci = DativoCI(
            exercicio="2026-03",
            processo_edoc="CI-IMPORT-CROSS-UNICO",
            data_ci=date(2026, 3, 24),
            descricao="Importacao unica com processo repetido",
            criado_por_id=self.user_id,
            responsavel_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.commit()

        caminho_planilha = self._criar_planilha_ods(
            [
                {
                    "Nome": "Beneficiario Cruzado",
                    "CPF/CNPJ": "11122233344",
                    "Processo": "PROC-CROSS-UNICO",
                    "Valor": "3200,00",
                }
            ],
            nome_arquivo="dativos_cross_unico.ods",
        )

        with open(caminho_planilha, "rb") as arquivo:
            resposta = self.client.post(
                f"/dativos/ci/{dativo_ci.id}/importar-unico/analisar",
                data={"arquivo_unico": (arquivo, "dativos_cross_unico.ods")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendencias para confirmacao", html)
        self.assertIn("Processo ja encontrado no sistema", html)
        self.assertEqual(
            DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf").count(),
            0,
        )

    def test_importacao_sem_irrf_com_processo_existente_exige_previa_e_confirmacao(self):
        self._criar_rpv(
            nome_beneficiario="Processo Ja Existente Sem IRRF",
            documento_original="52998224725",
            processo_edoc="CI-CROSS-SEM",
            numero_processo="PROC-CROSS-SEM",
        )

        dativo_ci = DativoCI(
            exercicio="2026-03",
            processo_edoc="CI-IMPORT-CROSS-SEM",
            data_ci=date(2026, 3, 24),
            descricao="Importacao sem IRRF com processo repetido",
            criado_por_id=self.user_id,
            responsavel_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(dativo_ci)
        db.session.commit()

        caminho_planilha = self._criar_planilha_ods(
            [
                {
                    "Nome": "Beneficiario Sem IRRF Cruzado",
                    "CPF/CNPJ": "11122233344",
                    "Processo": "PROC-CROSS-SEM",
                    "Valor": "2800,00",
                }
            ],
            nome_arquivo="dativos_cross_sem.ods",
        )

        with open(caminho_planilha, "rb") as arquivo:
            resposta = self.client.post(
                f"/dativos/ci/{dativo_ci.id}/importar-sem-irrf",
                data={"arquivo_sem_irrf": (arquivo, "dativos_cross_sem.ods")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        html = resposta.get_data(as_text=True)
        token_match = re.search(r'name="preview_token" value="([^"]+)"', html)
        pendencia_match = re.search(r'name="pendencias_confirmadas" value="([^"]+)"', html)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Previa da importacao sem IRRF", html)
        self.assertIn("Pendencias para confirmacao", html)
        self.assertIn("Processo ja encontrado no sistema", html)
        self.assertIsNotNone(token_match)
        self.assertIsNotNone(pendencia_match)
        self.assertEqual(
            DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf").count(),
            0,
        )

        resposta_confirmacao = self.client.post(
            f"/dativos/ci/{dativo_ci.id}/importar-unico/confirmar",
            data={
                "preview_token": token_match.group(1),
                "pendencias_confirmadas": [pendencia_match.group(1)],
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta_confirmacao.status_code, 302)
        self.assertEqual(
            DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf").count(),
            1,
        )

    def test_edicao_item_lote_permite_confirmar_dispensa_irrf(self):
        _, lote, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-DISP-IRRF",
            itens=[
                {
                    "nome_beneficiario": "CNPJ Sem Retencao",
                    "cpf_original": "50734299000186",
                    "numero_processo": "PROC-DISP-IRRF",
                    "valor_bruto": Decimal("5855.02"),
                }
            ],
        )
        item = itens[0]

        resposta_get = self.client.get(
            f"/dativos/lotes-sem-irrf/{lote.id}/item/{item.id}/editar"
        )
        html_get = resposta_get.get_data(as_text=True)

        self.assertEqual(resposta_get.status_code, 200)
        self.assertIn("Conferencia fiscal do beneficiario", html_get)
        self.assertIn('name="dispensa_irrf_confirmada"', html_get)

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/item/{item.id}/editar",
            data={
                "nome_beneficiario": item.nome_beneficiario,
                "cpf_original": item.cpf_original,
                "numero_processo": item.numero_processo,
                "valor_bruto": "5855,02",
                "dispensa_irrf_confirmada": "1",
                "confirmar_processo_existente": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertTrue(item_atualizado.dispensa_irrf_confirmada)

        eventos = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="dativo_item",
                entidade_id=item.id,
            )
            .order_by(HistoricoAlteracao.id.desc())
            .all()
        )
        self.assertTrue(
            any(
                any(
                    alteracao["campo"] == "dispensa_irrf_confirmada"
                    and alteracao["depois"] == "Sim"
                    for alteracao in evento.alteracoes
                )
                for evento in eventos
            )
        )

    def test_edicao_item_lote_salva_observacoes(self):
        _, lote, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-OBS-EDICAO",
            itens=[
                {
                    "nome_beneficiario": "Beneficiario Observacao Edicao",
                    "cpf_original": "12345678901",
                    "numero_processo": "PROC-OBS-EDICAO",
                    "valor_bruto": Decimal("2500.00"),
                }
            ],
        )
        item = itens[0]

        resposta = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/item/{item.id}/editar",
            data={
                "nome_beneficiario": item.nome_beneficiario,
                "cpf_original": item.cpf_original,
                "numero_processo": item.numero_processo,
                "valor_bruto": "2500,00",
                "observacoes": "Observacao salva na edicao do lote",
                "confirmar_processo_existente": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertEqual(item_atualizado.observacoes, "Observacao salva na edicao do lote")

    def test_detalhe_lote_marca_beneficiario_com_pendencia_irrf(self):
        _, lote, itens = self._criar_dativo_sem_irrf(
            processo_edoc="CI-MARCADOR-IRRF",
            itens=[
                {
                    "nome_beneficiario": "Beneficiario com Pendencia",
                    "cpf_original": "50734299000186",
                    "numero_processo": "PROC-MARCADOR-IRRF",
                    "valor_bruto": Decimal("5855.02"),
                }
            ],
        )
        item = itens[0]

        resposta = self.client.get(f"/dativos/lotes-sem-irrf/{lote.id}")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('class="section-card lot-beneficiaries-toggle"', html)
        self.assertIn("Abra esta seção para conferir, ajustar ou incluir beneficiários.", html)
        self.assertIn("1 beneficiário", html)
        self.assertIn("beneficiary-alert-marker", html)
        self.assertIn("Beneficiario com Pendencia", html)

        item.dispensa_irrf_confirmada = True
        db.session.commit()

        resposta_confirmada = self.client.get(f"/dativos/lotes-sem-irrf/{lote.id}")
        html_confirmado = resposta_confirmada.get_data(as_text=True)

        self.assertEqual(resposta_confirmada.status_code, 200)
        self.assertNotIn("beneficiary-alert-marker", html_confirmado)

    def test_novo_rpv_exige_confirmacao_para_ci_repetida_em_processos_diferentes(self):
        self._criar_rpv(
            nome_beneficiario="RPV CI Repetida Original",
            documento_original="12345678901",
            processo_edoc="CI-MULTI-RPV",
            numero_processo="PROC-CI-001",
        )

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-MULTI-RPV",
                "numero_processo": "PROC-CI-002",
                "data_ci": "2026-03-10",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "RPV CI Repetida Novo",
                "tipo_documento": "CPF",
                "documento_original": "98765432100",
                "valor_bruto": "5000,00",
                "valor_irrf": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Esta C.I./eDOC ja existe em outros RPVs normais", html)
        self.assertIn("Confirmar C.I./eDOC repetida e continuar", html)
        self.assertEqual(RegistroRPV.query.count(), 1)

    def test_novo_rpv_permite_confirmacao_para_ci_repetida_e_registra_historico(self):
        self._criar_rpv(
            nome_beneficiario="RPV CI Confirmada Original",
            documento_original="12345678901",
            processo_edoc="CI-MULTI-CONF",
            numero_processo="PROC-CI-CONF-001",
        )

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-MULTI-CONF",
                "numero_processo": "PROC-CI-CONF-002",
                "data_ci": "2026-03-10",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "RPV CI Confirmada Novo",
                "tipo_documento": "CPF",
                "documento_original": "98765432100",
                "valor_bruto": "5000,00",
                "valor_irrf": "",
                "confirmar_ci_existente": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registros = RegistroRPV.query.order_by(RegistroRPV.id.asc()).all()
        self.assertEqual(len(registros), 2)
        novo_registro = registros[-1]
        self.assertEqual(novo_registro.nome_beneficiario, "RPV CI Confirmada Novo")
        self.assertEqual(novo_registro.processo.processo_edoc, "CI-MULTI-CONF")
        self.assertEqual(novo_registro.processo.numero_processo, "PROC-CI-CONF-002")

        evento = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="registro_rpv",
                entidade_id=novo_registro.id,
                acao="Confirmação de C.I./eDOC repetida",
            )
            .order_by(HistoricoAlteracao.id.desc())
            .first()
        )
        self.assertIsNotNone(evento)
        self.assertIn("C.I./eDOC repetida confirmada pelo operador", evento.resumo)
        self.assertIn("PROC-CI-CONF-001", evento.resumo)

    def test_novo_rpv_com_processo_repetido_e_ci_diferente_preserva_ci_informada(self):
        registro_original = self._criar_rpv(
            nome_beneficiario="RPV Processo Repetido Original",
            documento_original="52998224725",
            processo_edoc="CI-PROC-ORIGINAL",
            numero_processo="PROC-MESMA-NUMERACAO",
            exercicio="2026-03",
        )
        processo_original_id = registro_original.processo_id

        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-04",
                "processo_edoc": "CI-PROC-NOVA",
                "numero_processo": "PROC-MESMA-NUMERACAO",
                "data_ci": "2026-04-02",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "RPV Processo Repetido Nova CI",
                "tipo_documento": "CPF",
                "documento_original": "98765432100",
                "valor_bruto": "5000,00",
                "valor_irrf": "",
                "confirmar_processo_existente": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registros = RegistroRPV.query.order_by(RegistroRPV.id.asc()).all()
        self.assertEqual(len(registros), 2)

        novo_registro = registros[-1]
        self.assertNotEqual(novo_registro.processo_id, processo_original_id)
        self.assertEqual(novo_registro.processo.processo_edoc, "CI-PROC-NOVA")
        self.assertEqual(novo_registro.processo.numero_processo, "PROC-MESMA-NUMERACAO")
        self.assertEqual(novo_registro.processo.exercicio, "2026-04")
        self.assertEqual(novo_registro.processo.data_ci, date(2026, 4, 2))
        self.assertIn("C.I. CI-PROC-NOVA_PROC-MESMA-NUMERACAO", novo_registro.historico_auto)

        processo_original = db.session.get(Processo, processo_original_id)
        self.assertEqual(processo_original.processo_edoc, "CI-PROC-ORIGINAL")
        self.assertEqual(processo_original.exercicio, "2026-03")
        self.assertEqual(processo_original.data_ci, date(2026, 3, 10))

        evento = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="registro_rpv",
                entidade_id=novo_registro.id,
                acao="Confirmação de repetição de processo",
            )
            .order_by(HistoricoAlteracao.id.desc())
            .first()
        )
        self.assertIsNotNone(evento)
        self.assertIn("Processo repetido confirmado pelo operador", evento.resumo)

    def test_novo_rpv_salva_observacoes_no_cadastro_inicial(self):
        resposta = self.client.post(
            "/rpvs/novo",
            data={
                "exercicio": "2026-03",
                "processo_edoc": "CI-OBS-RPV",
                "numero_processo": "PROC-OBS-RPV",
                "data_ci": "2026-03-12",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "RPV com Observacao",
                "tipo_documento": "CPF",
                "documento_original": "52998224725",
                "valor_bruto": "5000,00",
                "valor_irrf": "",
                "observacoes": "Observacao criada no cadastro inicial",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro = RegistroRPV.query.filter_by(nome_beneficiario="RPV com Observacao").one()
        self.assertEqual(registro.observacoes, "Observacao criada no cadastro inicial")

    def test_historico_registra_cadastro_e_edicao_de_rpv(self):
        registro = self._criar_rpv(
            nome_beneficiario="Historico RPV",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        historico_inicial = HistoricoAlteracao.query.filter_by(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
        ).all()
        self.assertEqual(len(historico_inicial), 0)

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(self.tipo_honorarios_id),
                "nome_beneficiario": "Historico RPV Atualizado",
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "320,00",
                "situacao_empenho_id": str(self.situacao_empenho_id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "nota_empenho": "NE-123",
                "ordem_bancaria": "",
                "ob_imposto": "",
                "reinf_status": "",
                "observacoes": "Atualizacao para auditoria",
                "data_pagamento": "",
                "data_pagamento_irrf": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        eventos = (
            HistoricoAlteracao.query.filter_by(entidade_tipo="registro_rpv", entidade_id=registro.id)
            .order_by(HistoricoAlteracao.id.asc())
            .all()
        )
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0].acao, "Alteração manual")
        self.assertTrue(any(item["rotulo"] == "Beneficiário" for item in eventos[0].alteracoes))

    def test_reinf_bloqueia_competencia_posterior_com_pendencia_anterior(self):
        self._criar_rpv(
            nome_beneficiario="REINF Marco Pendente",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        self._criar_rpv(
            nome_beneficiario="REINF Abril Pendente",
            valor_irrf=Decimal("510.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 4, 3),
        )

        resposta = self.client.get("/reinf/?competencia=2026-04")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('value="2026-03"', html)
        self.assertIn("fechamento do REINF permanece em", html)
        self.assertIn("REINF Marco Pendente", html)
        self.assertNotIn("REINF Abril Pendente", html)

    def test_reinf_cancelado_nao_bloqueia_competencia_posterior(self):
        registro_marco = self._criar_rpv(
            nome_beneficiario="REINF Marco Cancelado",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro_marco.reinf_status = "Cancelado"
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="REINF Abril Livre",
            valor_irrf=Decimal("510.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 4, 3),
        )

        resposta = self.client.get("/reinf/?competencia=2026-04&reinf_status=todos")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('value="2026-04"', html)
        self.assertNotIn("fechamento do REINF permanece em", html)
        self.assertIn("REINF Abril Livre", html)

    def test_reinf_exporta_csv_com_documento_e_resumo(self):
        self._criar_rpv(
            nome_beneficiario="Exporta REINF",
            valor_irrf=Decimal("350.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 16),
        )

        resposta = self.client.get("/reinf/exportar.csv?competencia=2026-03&reinf_status=todos")
        conteudo = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Exporta REINF", conteudo)
        self.assertIn("12345678", conteudo)
        self.assertIn("IRRF", conteudo)

    def test_reinf_exibe_beneficiario_com_documento_limpo_e_sem_cards_analiticos(self):
        registro = self._criar_rpv(
            nome_beneficiario="Beneficiario Limpo",
            valor_irrf=Decimal("420.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 16),
            documento_original="123.456.789-01",
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Filtros de pesquisa", html)
        self.assertIn("Beneficiario Limpo", html)
        self.assertIn("12345678901", html)
        self.assertIn("Conferencia mensal", html)
        self.assertIn("Conferencia anual", html)
        self.assertNotIn("Menu da area REINF", html)
        self.assertNotIn("Pagos com IRRF", html)
        self.assertNotIn("Pendentes de envio", html)
        self.assertNotIn(f"Processo {registro.processo.numero_processo}", html)

    def test_reinf_operacional_abre_em_ordem_alfabetica_por_beneficiario(self):
        self._criar_rpv(
            nome_beneficiario="Zeta REINF Padrao",
            documento_original="33333333333",
            valor_irrf=Decimal("330.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 5),
        )
        self._criar_rpv(
            nome_beneficiario="Alfa REINF Padrao",
            documento_original="11111111111",
            valor_irrf=Decimal("110.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 19),
        )
        self._criar_rpv(
            nome_beneficiario="Alfa REINF Padrao",
            documento_original="22222222222",
            valor_irrf=Decimal("220.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 7),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03&reinf_status=todos")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Ordenação:</strong> Beneficiário", html)
        self.assertIn("Direção:</strong> Crescente", html)
        primeira_alfa = html.index("11111111111")
        segunda_alfa = html.index("22222222222")
        zeta = html.index("Zeta REINF Padrao")
        self.assertLess(primeira_alfa, segunda_alfa)
        self.assertLess(segunda_alfa, zeta)

    def test_reinf_conferencia_mensal_exibe_tabela_limpa_por_beneficiario(self):
        self._criar_rpv(
            nome_beneficiario="Conferencia Mensal",
            documento_original="12345678901",
            valor_bruto=Decimal("3000.00"),
            valor_irrf=Decimal("300.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 10),
        )
        _, item_irrf = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-CONF-MENSAL",
            nome_beneficiario="Conferencia Mensal",
            cpf_original="12345678901",
            numero_processo="PROC-CONF-MENSAL-2",
            valor_bruto=Decimal("2200.00"),
            valor_irrf=Decimal("220.00"),
        )
        item_irrf.data_pagamento = date(2026, 3, 21)
        self._criar_rpv(
            nome_beneficiario="Conferencia Mensal CPF Menor",
            documento_original="02345678901",
            valor_bruto=Decimal("1000.00"),
            valor_irrf=Decimal("100.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 12),
        )

        self._criar_dativo_sem_irrf(
            processo_edoc="CI-CONF-MENSAL-LIVRE",
            itens=[
                {
                    "nome_beneficiario": "Nao Deve Entrar",
                    "cpf_original": "99988877766",
                    "numero_processo": "PROC-CONF-MENSAL-LIVRE",
                    "valor_bruto": Decimal("900.00"),
                    "data_pagamento": date(2026, 3, 25),
                }
            ],
        )
        db.session.commit()

        resposta = self.client.get("/reinf/?visao=conferencia_mensal&competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Voltar para REINF mensal", html)
        self.assertIn("Ir para anual", html)
        self.assertIn("Conferencia mensal por beneficiario", html)
        self.assertIn("Valor base", html)
        self.assertIn("IRRF retido", html)
        self.assertLess(
            html.index("023.456.789-01"),
            html.index("123.456.789-01"),
        )
        self.assertIn("Conferencia Mensal", html)
        self.assertIn("023.456.789-01", html)
        self.assertIn("123.456.789-01", html)
        self.assertIn("março/2026", html)
        self.assertIn("5.200,00", html)
        self.assertIn("520,00", html)
        self.assertIn("Ver processos", html)
        self.assertIn("Valor bruto", html)
        self.assertIn("Liquido", html)
        self.assertIn("PROC-CONF-MENSAL-2", html)
        self.assertNotIn("Abrir", html)
        self.assertNotIn("Nao Deve Entrar", html)
        self.assertNotIn("Atualizar selecionados", html)
        self.assertNotIn("Status REINF", html)

    def test_reinf_conferencia_anual_exibe_total_do_ano_e_detalhamento_por_exercicio(self):
        self._criar_rpv(
            nome_beneficiario="Conferencia Anual",
            documento_original="12345678901",
            valor_bruto=Decimal("3100.00"),
            valor_irrf=Decimal("310.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 2, 12),
        )
        _, item_irrf = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-CONF-ANUAL",
            nome_beneficiario="Conferencia Anual",
            cpf_original="12345678901",
            numero_processo="PROC-CONF-ANUAL-2",
            valor_bruto=Decimal("2400.00"),
            valor_irrf=Decimal("240.00"),
        )
        item_irrf.data_pagamento = date(2026, 4, 5)

        self._criar_dativo_sem_irrf(
            processo_edoc="CI-CONF-ANUAL-LIVRE",
            itens=[
                {
                    "nome_beneficiario": "Fora da Anual",
                    "cpf_original": "99988877766",
                    "numero_processo": "PROC-CONF-ANUAL-LIVRE",
                    "valor_bruto": Decimal("1500.00"),
                    "data_pagamento": date(2026, 6, 8),
                }
            ],
        )
        db.session.commit()

        resposta = self.client.get("/reinf/?visao=conferencia_anual&ano=2026")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Conferencia anual por beneficiario", html)
        self.assertIn("Valor total do ano", html)
        self.assertIn("IRRF retido no ano", html)
        self.assertIn("Conferencia Anual", html)
        self.assertIn("123.456.789-01", html)
        self.assertIn("5.500,00", html)
        self.assertIn("550,00", html)
        self.assertIn("Voltar para REINF mensal", html)
        self.assertIn("Ir para mensal", html)
        self.assertIn("Ver exercicios", html)
        self.assertIn("fevereiro/2026", html)
        self.assertIn("abril/2026", html)
        self.assertIn("Valor bruto", html)
        self.assertIn("3.100,00", html)
        self.assertIn("310,00", html)
        self.assertIn("2.790,00", html)
        self.assertIn("2.400,00", html)
        self.assertIn("240,00", html)
        self.assertIn("2.160,00", html)
        self.assertNotIn("PROC-CONF-ANUAL-2", html)
        self.assertNotIn("CI-CONF-ANUAL", html)
        self.assertNotIn("Abrir", html)
        self.assertNotIn("Fora da Anual", html)
        self.assertNotIn("Atualizar selecionados", html)
        self.assertNotIn("Status REINF", html)

    def test_reinf_atualiza_status_em_lote(self):
        registro = self._criar_rpv(
            nome_beneficiario="Lote REINF",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )

        resposta = self.client.post(
            "/reinf/atualizar-status-lote",
            data={
                "reinf_status_lote": "Concluído",
                "selecionados": [f"rpv:{registro.id}"],
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status_legivel, "Concluído")

    def test_reinf_nao_sobrescreve_status_individual_sem_status_no_post(self):
        registro = self._criar_rpv(
            nome_beneficiario="REINF Sem Status Individual",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro.reinf_status = "Concluído"
        db.session.commit()

        resposta = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status, "Concluído")

    def test_reinf_nao_sobrescreve_status_lote_sem_status_no_post(self):
        registro = self._criar_rpv(
            nome_beneficiario="REINF Sem Status Lote",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro.reinf_status = "Cancelado"
        db.session.commit()

        resposta = self.client.post(
            "/reinf/atualizar-status-lote",
            data={
                "selecionados": [f"rpv:{registro.id}"],
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status, "Cancelado")

    def test_admin_pode_limpar_status_reinf(self):
        registro = self._criar_rpv(
            nome_beneficiario="Limpeza REINF Admin",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro.reinf_status = "Concluído"
        db.session.commit()

        resposta = self.client.post(
            "/reinf/limpar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertIsNone(registro_atualizado.reinf_status)

        evento = HistoricoAlteracao.query.filter_by(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
            acao="Limpeza administrativa REINF",
        ).first()
        self.assertIsNotNone(evento)

    def test_usuario_nao_admin_nao_limpa_status_reinf(self):
        registro = self._criar_rpv(
            nome_beneficiario="Limpeza REINF Negada",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro.reinf_status = "Concluído"

        usuario = User(
            nome="Usuário Operacional",
            login="operacional.reinf",
            email="operacional.reinf@controle-rpv.local",
            telefone="79999999999",
            cargo="Analista",
            setor="Financeiro",
            ativo=True,
            is_admin=False,
        )
        usuario.set_password("senha123")
        db.session.add(usuario)
        db.session.commit()
        self._autenticar(usuario.id)

        resposta = self.client.post(
            "/reinf/limpar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status, "Concluído")

    def test_reinf_aplica_ordenacao_e_paginacao(self):
        self._criar_rpv(
            nome_beneficiario="REINF Menor",
            valor_irrf=Decimal("100.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        self._criar_rpv(
            nome_beneficiario="REINF Maior",
            valor_irrf=Decimal("450.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 19),
        )

        resposta = self.client.get(
            "/reinf/?competencia=2026-03&reinf_status=todos&ordenar=imposto&direcao=desc&por_pagina=1&pagina=1"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("REINF Maior", html)
        self.assertNotIn("REINF Menor", html)
        self.assertIn("Página 1 de 2", html)

    def test_reinf_nao_exibe_opcao_meus_no_filtro_de_responsavel(self):
        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('option value="meus"', html)

    def test_reinf_exibe_colunas_fixas_na_grade(self):
        self._criar_rpv(
            nome_beneficiario="REINF Sticky",
            valor_irrf=Decimal("220.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 20),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("sticky-left-1", html)
        self.assertIn("sticky-right-1", html)

    def test_reinf_operacional_exibe_bloco_financeiro_com_bruto_e_irrf(self):
        self._criar_rpv(
            nome_beneficiario="REINF Financeiro",
            valor_bruto=Decimal("4321.98"),
            valor_irrf=Decimal("321.98"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 22),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03&reinf_status=todos&q=Financeiro")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("reinf-finance-cell", html)
        self.assertIn("Financeiro", html)
        self.assertIn("Bruto", html)
        self.assertIn("4.321,98", html)
        self.assertIn("IRRF", html)
        self.assertIn("321,98", html)

    def test_edicao_salva_data_pagamento_principal_e_irrf_em_campos_separados(self):
        registro = self._criar_rpv(
            nome_beneficiario="Edicao Datas",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )

        resposta_edicao = self.client.get(f"/rpvs/{registro.id}/editar")
        html_edicao = resposta_edicao.get_data(as_text=True)

        self.assertEqual(resposta_edicao.status_code, 200)
        self.assertIn("Pagamento principal", html_edicao)
        self.assertIn("Pagamento do IRRF", html_edicao)

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": "Edicao Datas",
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "410,00",
                "sem_irrf": "",
                "nota_empenho": "2026NE0001",
                "ordem_bancaria": "2026OB0001",
                "ob_imposto": "2026OBIRRF0001",
                "reinf_status": "Concluído",
                "situacao_empenho_id": str(self.situacao_empenho_id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "2026-03-20",
                "data_pagamento_irrf": "2026-03-25",
                "confirmar_data_pagamento_manual": "1",
                "observacoes": "Teste de datas separadas",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)

        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.data_pagamento, date(2026, 3, 20))
        self.assertEqual(registro_atualizado.data_pagamento_irrf, date(2026, 3, 25))

    def test_edicao_rpv_exige_confirmacao_para_alteracao_manual_da_data_pagamento(self):
        situacao_concluida = SituacaoEmpenho(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_concluida)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Confirmacao Data Manual",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": registro.nome_beneficiario,
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "410,00",
                "sem_irrf": "",
                "nota_empenho": "2026NE0099",
                "ordem_bancaria": "2026OB0099",
                "ob_imposto": "",
                "reinf_status": "",
                "situacao_empenho_id": str(situacao_concluida.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "2026-02-20",
                "data_pagamento_irrf": "",
                "observacoes": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Confirme a alteracao manual da data do pagamento principal.", html)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertIsNone(registro_atualizado.data_pagamento)
        self.assertEqual(registro_atualizado.processo.exercicio, "2026-03")

    def test_edicao_rpv_permuta_data_pagamento_manual_com_confirmacao(self):
        situacao_concluida = SituacaoEmpenho(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_concluida)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="Confirmacao Data Manual OK",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": registro.nome_beneficiario,
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "410,00",
                "sem_irrf": "",
                "nota_empenho": "2026NE0100",
                "numero_se": "2026SE0100",
                "ordem_bancaria": "2026OB0100",
                "ob_imposto": "",
                "reinf_status": "",
                "situacao_empenho_id": str(situacao_concluida.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "2026-02-20",
                "data_pagamento_irrf": "",
                "observacoes": "",
                "confirmar_data_pagamento_manual": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.data_pagamento, date(2026, 2, 20))
        self.assertEqual(registro_atualizado.processo.exercicio, "2026-02")
        self.assertEqual(registro_atualizado.numero_se, "2026SE0100")

    def test_edicao_rpv_exibe_status_reinf_como_leitura_e_preserva_valor(self):
        registro = self._criar_rpv(
            nome_beneficiario="Edicao REINF Cancelado",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )
        registro.reinf_status = "Concluído"
        db.session.commit()

        resposta_edicao = self.client.get(f"/rpvs/{registro.id}/editar")
        html_edicao = resposta_edicao.get_data(as_text=True)

        self.assertEqual(resposta_edicao.status_code, 200)
        self.assertIn('name="numero_se"', html_edicao)
        self.assertNotIn('name="reinf_status"', html_edicao)
        self.assertIn("Atualize esse status somente na aba REINF mensal.", html_edicao)
        self.assertIn("Concluído", html_edicao)

        resposta = self.client.post(
            f"/rpvs/{registro.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(registro.tipo_rpv_id),
                "nome_beneficiario": "Edicao REINF Cancelado",
                "tipo_documento": "CPF",
                "documento_original": registro.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "410,00",
                "sem_irrf": "",
                "nota_empenho": "2026NE0002",
                "ordem_bancaria": "2026OB0002",
                "ob_imposto": "2026OBIRRF0002",
                "reinf_status": "Cancelado",
                "situacao_empenho_id": str(self.situacao_empenho_id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "2026-03-20",
                "data_pagamento_irrf": "2026-03-25",
                "confirmar_data_pagamento_manual": "1",
                "observacoes": "Teste de cancelamento REINF",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status, "Concluído")

    def test_reinf_exclui_rpv_marcado_sem_irrf(self):
        self._criar_rpv(
            nome_beneficiario="Oculto Reinf",
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("Oculto Reinf", html)

    def test_reinf_exibe_rpv_pendente_quando_irrf_ainda_nao_foi_informado(self):
        self._criar_rpv(
            nome_beneficiario="Pendente Reinf",
            valor_irrf=None,
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendente Reinf", html)
        self.assertIn("IRRF PENDENTE", html)

    def test_reinf_preserva_compatibilidade_com_registro_legado_sem_irrf(self):
        registro = self._criar_rpv(
            nome_beneficiario="Legado Sem IRRF",
            valor_irrf=None,
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_sem_irrf_id,
            data_pagamento=date(2026, 3, 15),
        )

        self.assertTrue(registro.sem_irrf_efetivo)

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("Legado Sem IRRF", html)

    def test_edicao_rpv_impede_ne_repetida_e_indica_onde_ela_esta(self):
        registro_origem = self._criar_rpv(
            nome_beneficiario="Origem NE",
            processo_edoc="CI-NE-ORIGEM",
            numero_processo="PROC-NE-ORIGEM",
        )
        registro_origem.nota_empenho = "2026NE777"
        db.session.commit()

        registro_destino = self._criar_rpv(
            nome_beneficiario="Destino NE",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )

        resposta = self.client.post(
            f"/rpvs/{registro_destino.id}/editar",
            data={
                "exercicio": "2026-03",
                "tipo_rpv_id": str(registro_destino.tipo_rpv_id),
                "nome_beneficiario": registro_destino.nome_beneficiario,
                "tipo_documento": "CPF",
                "documento_original": registro_destino.documento_original,
                "valor_bruto": "8000,00",
                "valor_irrf": "410,00",
                "sem_irrf": "",
                "nota_empenho": "2026 NE 777",
                "ordem_bancaria": "2026OB9001",
                "ob_imposto": "",
                "reinf_status": "",
                "situacao_empenho_id": str(self.situacao_empenho_id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "data_pagamento": "",
                "data_pagamento_irrf": "",
                "observacoes": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("2026 NE 777", html)
        self.assertIn("já está em uso em RPV", html)
        self.assertIn("Origem NE", html)

        db.session.expire_all()
        registro_atualizado = db.session.get(RegistroRPV, registro_destino.id)
        self.assertIsNone(registro_atualizado.nota_empenho)

    def test_atualizacao_rapida_rpv_exige_ne_e_ob_para_marcar_pago(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="RPV Sem Referencias",
            valor_irrf=Decimal("200.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )

        resposta = self.client.post(
            f"/rpvs/{registro.id}/atualizacao-rapida",
            data={
                "nota_empenho": "",
                "ordem_bancaria": "",
                "ob_imposto": "",
                "situacao_empenho_id": str(situacao_pago.id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("informe NE e OB", html)
        self.assertIn("RPV Sem Referencias", html)

        db.session.expire_all()
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.situacao_empenho_id, self.situacao_empenho_id)
        self.assertIsNone(registro_atualizado.data_pagamento)

    def test_atualizacao_em_lote_rpv_exige_ne_e_ob_para_quitar_pagamento(self):
        situacao_concluida = SituacaoEmpenho(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_concluida)
        db.session.commit()

        registro = self._criar_rpv(
            nome_beneficiario="RPV Lote Sem Referencias",
            valor_irrf=Decimal("230.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
        )

        resposta = self.client.post(
            "/rpvs/atualizacao-lote",
            data={
                "selecionados": [str(registro.id)],
                "situacao_empenho_id_lote": str(situacao_concluida.id),
                "situacao_imposto_id_lote": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("informe NE e OB", html)
        self.assertIn("RPV Lote Sem Referencias", html)

        db.session.expire_all()
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.situacao_empenho_id, self.situacao_empenho_id)

    def test_item_com_irrf_impede_ob_repetida_e_indica_origem(self):
        registro_origem = self._criar_rpv(
            nome_beneficiario="Origem OB",
            processo_edoc="CI-OB-ORIGEM",
            numero_processo="PROC-OB-ORIGEM",
        )
        registro_origem.ordem_bancaria = "2026OB777"
        db.session.commit()

        _, item = self._criar_item_dativo_com_irrf(
            nome_beneficiario="Destino OB",
            cpf_original="98765432100",
        )

        resposta = self.client.post(
            f"/dativos/itens-com-irrf/{item.id}/salvar",
            data={
                "valor_irrf": "700,00",
                "data_pagamento": "",
                "nota_empenho": "2026NEITEM01",
                "ordem_bancaria": "2026 OB 777",
                "ob_imposto": "",
                "situacao_rpv_id": str(self.situacao_empenho_id),
                "situacao_imposto_id": str(self.situacao_imposto_pendente_id),
                "observacoes": "",
                "reinf_status": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("2026 OB 777", html)
        self.assertIn("já está em uso em RPV", html)
        self.assertIn("Origem OB", html)

        db.session.expire_all()
        item_atualizado = db.session.get(DativoItem, item.id)
        self.assertIsNone(item_atualizado.ordem_bancaria)

    def test_lote_sem_irrf_compartilha_ne_e_ob_com_os_itens_sem_gerar_auto_conflito(self):
        situacao_pago = SituacaoEmpenho(
            nome="Pago",
            cor_badge="badge-green",
            ordem_fluxo=10,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_pago)
        db.session.commit()

        _, lote, _ = self._criar_dativo_sem_irrf(
            processo_edoc="CI-LOTE-REFERENCIAS",
            itens=[
                {
                    "nome_beneficiario": "Beneficiário Lote A",
                    "valor_bruto": Decimal("1200.00"),
                }
            ],
        )

        resposta_inicial = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/salvar",
            data={
                "nota_empenho": "2026NELOTE01",
                "ordem_bancaria": "2026OBLOTE01",
                "data_pagamento": "",
                "situacao_rpv_id": str(situacao_pago.id),
                "observacoes": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(resposta_inicial.status_code, 302)

        db.session.expire_all()
        lote_atualizado = db.session.get(DativoLote, lote.id)
        itens_atualizados = DativoItem.query.filter_by(dativo_lote_id=lote.id, grupo="sem_irrf").all()
        self.assertTrue(itens_atualizados)
        self.assertTrue(all(item.nota_empenho == "2026NELOTE01" for item in itens_atualizados))
        self.assertTrue(all(item.ordem_bancaria == "2026OBLOTE01" for item in itens_atualizados))

        resposta_repetida = self.client.post(
            f"/dativos/lotes-sem-irrf/{lote.id}/salvar",
            data={
                "nota_empenho": "2026NELOTE01",
                "ordem_bancaria": "2026OBLOTE01",
                "data_pagamento": "",
                "situacao_rpv_id": str(situacao_pago.id),
                "observacoes": "Segunda gravação",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta_repetida.status_code, 302)

    def test_atualizacao_em_lote_dativos_exige_ne_e_ob_para_quitar_pagamento(self):
        situacao_concluida = SituacaoEmpenho(
            nome="Concluída",
            cor_badge="badge-green",
            ordem_fluxo=11,
            ativo=True,
            is_final=True,
        )
        db.session.add(situacao_concluida)
        db.session.commit()

        _, lote, _ = self._criar_dativo_sem_irrf(processo_edoc="CI-LOTE-SEM-REFERENCIAS")

        resposta = self.client.post(
            "/dativos/atualizacao-lote",
            data={
                "selecionados": [f"lote:{lote.id}"],
                "situacao_rpv_id_lote": str(situacao_concluida.id),
                "situacao_imposto_id_lote": "",
            },
            follow_redirects=True,
        )

        html = resposta.get_data(as_text=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("informe NE e OB", html)
        self.assertIn("CI-LOTE-SEM-REFERENCIAS", html)

        db.session.expire_all()
        lote_atualizado = db.session.get(DativoLote, lote.id)
        self.assertEqual(lote_atualizado.situacao_rpv_id, self.situacao_empenho_id)


if __name__ == "__main__":
    unittest.main()



