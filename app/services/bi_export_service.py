from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Callable


class BIExportService:
    @staticmethod
    def build_operational_csv(
        dataset: list[dict],
        *,
        decimal_formatter: Callable[[object], str],
        file_date: date,
    ) -> dict[str, str]:
        buffer = StringIO()
        writer = csv.writer(buffer, delimiter=";")

        writer.writerow(
            [
                "Competencia cadastro",
                "Competencia pagamento",
                "Status pagamento",
                "Origem",
                "Grupo",
                "Tipo",
                "Fluxo IRRF",
                "Responsavel",
                "Beneficiario",
                "Documento",
                "Processo",
                "C.I.",
                "Data pagamento",
                "Valor bruto",
                "Valor pago",
                "Valor em aberto",
                "Valor IRRF",
                "Valor liquido",
                "REINF",
            ]
        )

        for row in dataset:
            writer.writerow(
                [
                    row["competencia_cadastro_legivel"],
                    row["competencia_pagamento_legivel"],
                    row["pagamento_status"],
                    row["origem"],
                    row["grupo_cota_label"],
                    row["tipo"],
                    row["fluxo_irrf_label"],
                    row["responsavel"],
                    row["nome"],
                    row["documento_limpo"],
                    row["processo"],
                    row["ci"],
                    row["data_pagamento_legivel"],
                    decimal_formatter(row["valor_bruto"]),
                    decimal_formatter(row["valor_pago"]),
                    decimal_formatter(row["valor_previsto_aberto"]),
                    decimal_formatter(row["valor_irrf"]),
                    decimal_formatter(row["valor_liquido"]),
                    row["reinf_status"],
                ]
            )

        return {
            "filename": f"bi_rpvs_{file_date.isoformat()}.csv",
            "content": "\ufeff" + buffer.getvalue(),
        }

    @staticmethod
    def build_conference_csv(
        conferencia: dict,
        *,
        decimal_formatter: Callable[[object], str],
        file_date: date,
    ) -> dict[str, str]:
        buffer = StringIO()
        writer = csv.writer(buffer, delimiter=";")

        writer.writerow(
            [
                "Competencia pagamento",
                "Quantidade",
                "Pessoal",
                "Pericial",
                "Comum",
                "Valor bruto pago",
                "Valor IRRF",
                "Valor liquido",
            ]
        )

        for linha in conferencia["linhas"]:
            writer.writerow(
                [
                    linha["label"],
                    linha["quantidade"],
                    decimal_formatter(linha["valor_pessoal"]),
                    decimal_formatter(linha["valor_pericial"]),
                    decimal_formatter(linha["valor_comum"]),
                    decimal_formatter(linha["valor_bruto"]),
                    decimal_formatter(linha["valor_irrf"]),
                    decimal_formatter(linha["valor_liquido"]),
                ]
            )

        writer.writerow(
            [
                "Total geral",
                conferencia["totais"]["quantidade"],
                decimal_formatter(conferencia["totais"]["valor_pessoal"]),
                decimal_formatter(conferencia["totais"]["valor_pericial"]),
                decimal_formatter(conferencia["totais"]["valor_comum"]),
                decimal_formatter(conferencia["totais"]["valor_bruto"]),
                decimal_formatter(conferencia["totais"]["valor_irrf"]),
                decimal_formatter(conferencia["totais"]["valor_liquido"]),
            ]
        )

        return {
            "filename": f"bi_conferencia_{file_date.isoformat()}.csv",
            "content": "\ufeff" + buffer.getvalue(),
        }
