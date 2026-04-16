import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from app.services.rpv_import_service import (
    aplicar_bloqueios_duplicidade,
    carregar_planilha_rpvs_normais,
)


HEADERS = [
    "EXERCÍCIO",
    "ELABORADOR",
    "DESCRIÇÃO",
    "PROCESSO E-DOC",
    "NOME BENEFICIÁRIO",
    "CPF BENEFICIÁRIO",
    "Nº DO PROCESSO",
    "DATA",
    "VALOR",
    "IMPOSTO",
    "NOTA DE EMPENHO",
    "SITUAÇÃO EMPENHO",
    "SITUAÇÃO IMPOSTO",
    "ORDEM BANCÁRIA",
    "REINF",
    "OB IMPOSTO",
    "OBSERVAÇÕES",
]


class ImportacaoRPVsNormaisTestCase(unittest.TestCase):
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

    def test_carrega_planilha_ajusta_documento_e_mapeia_lee(self):
        path = self._criar_planilha(
            [
                [
                    datetime(2026, 2, 1),
                    "LE",
                    "RPV-HONORARIOS",
                    "123/2026",
                    "FULANO DE TAL",
                    9994377710,
                    "202512000635",
                    datetime(2026, 1, 31),
                    5765.81,
                    298.96,
                    "NE 185",
                    "CONCLUÍDA",
                    "CONCLUÍDA",
                    "OB 287",
                    "Preenchida",
                    "OB 303",
                    None,
                ]
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)

        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        self.assertEqual(linha.elaborador_destino, "Operador C")
        self.assertEqual(linha.documento_ajustado, "09994377710")
        self.assertEqual(linha.tipo_documento, "CPF")
        self.assertEqual(linha.reinf_destino, "Concluído")
        self.assertEqual(linha.situacao_empenho_destino, "Concluída")
        self.assertEqual(linha.data_pagamento.strftime("%Y-%m-%d"), "2026-02-01")
        self.assertFalse(linha.issues)

    def test_infere_sem_irrf_quando_planilha_traz_sif_e_situacao_vazia(self):
        path = self._criar_planilha(
            [
                [
                    datetime(2026, 3, 1),
                    "Operador B",
                    "RPV_CUSTEIO",
                    "1072/2026",
                    "EMPRESA TESTE LTDA",
                    "03.263.975/0001-09",
                    "202540900001",
                    datetime(2026, 3, 6),
                    2458.66,
                    "S/IF",
                    "NE 350",
                    "PAGO",
                    None,
                    None,
                    None,
                    "-",
                    None,
                ]
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)

        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        self.assertTrue(linha.sem_irrf)
        self.assertEqual(linha.situacao_imposto_destino, "Sem IRRF")
        self.assertEqual(linha.tipo_documento, "CNPJ")
        self.assertFalse(linha.issues)

    def test_marca_documento_invalido_pelos_digitos_como_pendente(self):
        path = self._criar_planilha(
            [
                [
                    datetime(2026, 3, 1),
                    "Operador A",
                    "RPV-PESSOAL",
                    "500/2026",
                    "PESSOA INVALIDA",
                    "12345678901",
                    "202640900500",
                    datetime(2026, 3, 6),
                    1200.00,
                    "S/IF",
                    "NE 500",
                    "PAGO",
                    "SEM IRRF",
                    "OB 500",
                    None,
                    "-",
                    None,
                ]
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)

        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        self.assertEqual(linha.documento_ajustado, "12345678901")
        self.assertIn("Documento pendente", linha.issues)

    def test_mapeia_danos_morais_como_tipo_proprio_sem_alerta(self):
        path = self._criar_planilha(
            [
                [
                    datetime(2026, 4, 1),
                    "Operador A",
                    "RPV-DANOS MORAIS",
                    "201/2026",
                    "PESSOA TESTE",
                    "52998224725",
                    "202640900001",
                    datetime(2026, 4, 1),
                    3200,
                    "S/IF",
                    "NE 15",
                    "PAGO",
                    "SEM IRRF",
                    "OB 88",
                    None,
                    "-",
                    None,
                ]
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)

        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        self.assertEqual(linha.tipo_rpv_destino, "Danos Morais")
        self.assertFalse(any("DANOS MORAIS" in issue.upper() for issue in linha.issues))
        self.assertFalse(linha.issues)

    def test_bloqueia_todas_as_linhas_de_processo_repetido_na_planilha(self):
        path = self._criar_planilha(
            [
                [
                    datetime(2026, 2, 1),
                    "Operador A",
                    "RPV-PESSOAL",
                    "101/2026",
                    "PESSOA A",
                    "28001238938",
                    "0001-11.2026.5.20.0001",
                    datetime(2026, 2, 2),
                    1000,
                    "S/IF",
                    "NE 1",
                    "CONCLUÍDA",
                    "SEM IRRF",
                    "OB 1",
                    None,
                    "-",
                    None,
                ],
                [
                    datetime(2026, 2, 1),
                    "Operador D",
                    "RPV-PESSOAL",
                    "102/2026",
                    "PESSOA B",
                    "39053344705",
                    "0001-11.2026.5.20.0001",
                    datetime(2026, 2, 3),
                    1200,
                    "S/IF",
                    "NE 2",
                    "CONCLUÍDA",
                    "SEM IRRF",
                    "OB 2",
                    None,
                    "-",
                    None,
                ],
            ]
        )

        linhas = carregar_planilha_rpvs_normais(path)
        aplicar_bloqueios_duplicidade(linhas)

        self.assertEqual(len(linhas), 2)
        self.assertTrue(all(linha.duplicado_planilha for linha in linhas))
        self.assertTrue(all("linhas" in (linha.duplicado_detalhe or "") for linha in linhas))
        self.assertTrue(all(not linha.apta_importacao for linha in linhas))


if __name__ == "__main__":
    unittest.main()
