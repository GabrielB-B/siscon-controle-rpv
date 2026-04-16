import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from flask import Flask
from openpyxl import Workbook

from app.extensions import db
from app.models import (
    HistoricoAlteracao,
    Processo,
    RegistroRPV,
    SituacaoEmpenho,
    SituacaoImposto,
    TipoRPV,
    User,
)
from app.services.rpv_import_service import (
    aplicar_bloqueios_banco,
    aplicar_bloqueios_duplicidade,
    carregar_planilha_rpvs_normais,
    coletar_processos_existentes,
    marcar_conciliacoes_banco,
    reconciliar_registros_existentes,
)


HEADERS = [
    "EXERCICIO",
    "ELABORADOR",
    "DESCRICAO",
    "PROCESSO E-DOC",
    "NOME BENEFICIARIO",
    "CPF BENEFICIARIO",
    "NUMERO DO PROCESSO",
    "DATA",
    "VALOR",
    "IMPOSTO",
    "NOTA DE EMPENHO",
    "SITUACAO EMPENHO",
    "SITUACAO IMPOSTO",
    "ORDEM BANCARIA",
    "REINF",
    "OB IMPOSTO",
    "OBSERVACOES",
]


class ImportacaoRPVsNormaisConciliacaoTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="teste",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )

        db.init_app(self.app)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self._seed_base()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _seed_base(self):
        user_marina = User(
            nome="Operador D",
            login="operador.d",
            email="operador.d@controle-rpv.local",
            ativo=True,
            is_admin=True,
        )
        user_marina.set_password("senha123")

        tipo_honorarios = TipoRPV(nome="RPV honorários", ativo=True, ordem_exibicao=1)

        situacao_rpv_sem_tratamento = SituacaoEmpenho(
            nome="Sem Tratamento",
            cor_badge="badge-slate",
            ordem_fluxo=1,
            ativo=True,
            is_final=False,
        )
        situacao_rpv_guias_geradas = SituacaoEmpenho(
            nome="Guias Geradas",
            cor_badge="badge-amber",
            ordem_fluxo=2,
            ativo=True,
            is_final=False,
        )

        situacao_irrf_sem_tratamento = SituacaoImposto(
            nome="Sem Tratamento",
            cor_badge="badge-slate",
            ordem_fluxo=1,
            ativo=True,
            is_final=False,
        )
        situacao_irrf_aguardando_ob = SituacaoImposto(
            nome="Aguardando PGTO OB Principal",
            cor_badge="badge-amber",
            ordem_fluxo=2,
            ativo=True,
            is_final=False,
        )
        situacao_irrf_sem_irrf = SituacaoImposto(
            nome="Sem IRRF",
            cor_badge="badge-slate",
            ordem_fluxo=3,
            ativo=True,
            is_final=True,
        )

        db.session.add_all(
            [
                user_marina,
                tipo_honorarios,
                situacao_rpv_sem_tratamento,
                situacao_rpv_guias_geradas,
                situacao_irrf_sem_tratamento,
                situacao_irrf_aguardando_ob,
                situacao_irrf_sem_irrf,
            ]
        )
        db.session.commit()

        self.user_id = user_marina.id
        self.tipo_honorarios_id = tipo_honorarios.id
        self.situacao_rpv_sem_tratamento_id = situacao_rpv_sem_tratamento.id
        self.situacao_rpv_guias_geradas_id = situacao_rpv_guias_geradas.id
        self.situacao_irrf_sem_tratamento_id = situacao_irrf_sem_tratamento.id
        self.situacao_irrf_aguardando_ob_id = situacao_irrf_aguardando_ob.id
        self.situacao_irrf_sem_irrf_id = situacao_irrf_sem_irrf.id

    def _criar_planilha(self, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        for row in rows:
            sheet.append(row)

        tempdir = tempfile.TemporaryDirectory()
        path = Path(tempdir.name) / "rpvs.xlsx"
        workbook.save(path)
        self.addCleanup(tempdir.cleanup)
        return path

    def _criar_registro_existente(
        self,
        *,
        processo_edoc: str,
        numero_processo: str,
        nome_beneficiario: str,
        documento: str,
        situacao_imposto_id: int | None = None,
        sem_irrf: bool = False,
        nota_empenho: str | None = None,
    ) -> RegistroRPV:
        processo = Processo(
            exercicio="2026-03",
            processo_edoc=processo_edoc,
            numero_processo=numero_processo,
            data_ci=date(2026, 3, 10),
            criado_por_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        db.session.add(processo)
        db.session.flush()

        registro = RegistroRPV(
            processo_id=processo.id,
            elaborador_id=self.user_id,
            tipo_rpv_id=self.tipo_honorarios_id,
            nome_beneficiario=nome_beneficiario,
            nome_beneficiario_normalizado="",
            tipo_documento="CPF",
            documento_original=documento,
            documento_normalizado="",
            documento_corrigido=None,
            data_pagamento=None,
            data_pagamento_irrf=None,
            valor_bruto=Decimal("0.00"),
            valor_irrf=None,
            valor_liquido=Decimal("0.00"),
            possui_irrf=False,
            sem_irrf=sem_irrf,
            imposto_texto=None,
            nota_empenho=nota_empenho,
            situacao_empenho_id=self.situacao_rpv_sem_tratamento_id,
            situacao_imposto_id=(
                situacao_imposto_id
                if situacao_imposto_id is not None
                else self.situacao_irrf_sem_tratamento_id
            ),
            ordem_bancaria=None,
            reinf_status=None,
            ob_imposto=None,
            historico_auto="",
            observacoes=None,
            ativo=True,
            criado_por_id=self.user_id,
            atualizado_por_id=self.user_id,
        )
        registro.atualizar_campos_derivados()
        registro.gerar_historico_auto(
            processo_edoc=processo.processo_edoc,
            numero_processo=processo.numero_processo,
            descricao="RPV honorários",
            data_ci=processo.data_ci,
        )
        db.session.add(registro)
        db.session.commit()
        return registro

    def test_concilia_registro_existente_em_estado_inicial_com_status_da_planilha(self):
        registro = self._criar_registro_existente(
            processo_edoc="1549/2026",
            numero_processo="202540906027",
            nome_beneficiario="BENEFICIARIO TESTE",
            documento="90560477520",
        )

        path = self._criar_planilha(
            [
                [
                    date(2026, 3, 1),
                    "Operador D",
                    "RPV-HONORARIOS",
                    "1549/2026",
                    "BENEFICIARIO TESTE",
                    "90560477520",
                    "202540906027",
                    date(2026, 3, 24),
                    5859.14,
                    337.05,
                    None,
                    "GUIAS GERADAS",
                    "AGUARDANDO PGTO OB PRINCIPAL",
                    None,
                    None,
                    None,
                    None,
                ]
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)
        aplicar_bloqueios_duplicidade(linhas)
        aplicar_bloqueios_banco(linhas, chaves_existentes=coletar_processos_existentes())
        marcar_conciliacoes_banco(linhas)

        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        self.assertTrue(linha.duplicado_banco)
        self.assertTrue(linha.conciliavel_banco)
        self.assertEqual(linha.registro_existente_id, registro.id)

        stats = reconciliar_registros_existentes(linhas)

        self.assertEqual(stats.get("conciliados"), 1)
        db.session.expire_all()
        atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertIsNotNone(atualizado)
        self.assertEqual(atualizado.processo.processo_edoc, "1549/2026")
        self.assertEqual(atualizado.processo.numero_processo, "202540906027")
        self.assertEqual(atualizado.processo.exercicio, "2026-03")
        self.assertEqual(atualizado.nome_beneficiario, "BENEFICIARIO TESTE")
        self.assertEqual(atualizado.documento_original, "90560477520")
        self.assertEqual(Decimal(atualizado.valor_bruto), Decimal("5859.14"))
        self.assertEqual(Decimal(atualizado.valor_irrf), Decimal("337.05"))
        self.assertEqual(atualizado.situacao_empenho.nome, "Guias Geradas")
        self.assertEqual(atualizado.situacao_imposto.nome, "Aguardando PGTO OB Principal")

        historico = HistoricoAlteracao.query.filter_by(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
            acao="Conciliação de carga histórica",
        ).one_or_none()
        self.assertIsNotNone(historico)

    def test_nao_concilia_quando_registro_existente_ja_estava_trabalhado(self):
        self._criar_registro_existente(
            processo_edoc="1548/2026",
            numero_processo="202540906995",
            nome_beneficiario="BENEFICIARIO TESTE",
            documento="90560477520",
            nota_empenho="NE 123",
        )

        path = self._criar_planilha(
            [
                [
                    date(2026, 3, 1),
                    "Operador D",
                    "RPV-HONORARIOS",
                    "1548/2026",
                    "BENEFICIARIO TESTE",
                    "90560477520",
                    "202540906995",
                    date(2026, 3, 24),
                    5843.04,
                    330.48,
                    None,
                    "GUIAS GERADAS",
                    "AGUARDANDO PGTO OB PRINCIPAL",
                    None,
                    None,
                    None,
                    None,
                ]
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)
        aplicar_bloqueios_duplicidade(linhas)
        aplicar_bloqueios_banco(linhas, chaves_existentes=coletar_processos_existentes())
        marcar_conciliacoes_banco(linhas)

        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        self.assertTrue(linha.duplicado_banco)
        self.assertFalse(linha.conciliavel_banco)
        self.assertIn("trabalhado", linha.conciliacao_detalhe or "")

        stats = reconciliar_registros_existentes(linhas)
        self.assertEqual(stats, {})


if __name__ == "__main__":
    unittest.main()
