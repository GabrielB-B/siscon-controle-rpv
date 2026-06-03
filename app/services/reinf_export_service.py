from __future__ import annotations

import csv
from collections.abc import Callable
from io import StringIO
from typing import Any


class ReinfExportService:
    @staticmethod
    def build_csv_content(
        registros: list[dict[str, Any]],
        *,
        decimal_formatter: Callable[[Any], str],
    ) -> str:
        buffer = StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(
            [
                "Origem",
                "Competência",
                "Data pagamento",
                "Beneficiário",
                "Documento",
                "Processo",
                "C.I.",
                "Resumo operacional",
                "Valor bruto",
                "IRRF",
                "Status REINF",
            ]
        )

        for registro in registros:
            writer.writerow(
                [
                    registro["tipo_origem"],
                    registro["competencia"],
                    registro["data_pagamento"].strftime("%d/%m/%Y") if registro["data_pagamento"] else "-",
                    registro["beneficiario"],
                    registro["documento_limpo"],
                    registro["processo"],
                    registro["ci"],
                    registro["resumo_operacional"],
                    decimal_formatter(registro["valor"]),
                    decimal_formatter(registro["imposto"]),
                    registro["reinf_status"],
                ]
            )

        return "\ufeff" + buffer.getvalue()
