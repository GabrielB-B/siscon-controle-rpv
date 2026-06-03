from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class BIFilterService:
    @staticmethod
    def normalize_main_filters(
        args: Mapping[str, Any],
        *,
        visao_normalizer: Callable[[str | None], str],
        competencia_normalizer: Callable[[str | None], str],
        janela_normalizer: Callable[[str | None], int],
    ) -> dict[str, str]:
        visao = visao_normalizer(args.get("visao"))
        return {
            "visao": visao,
            "q": str(args.get("q", "") or "").strip(),
            "competencia_inicial": competencia_normalizer(args.get("competencia_inicial")),
            "competencia_final": competencia_normalizer(args.get("competencia_final")),
            "origem": str(args.get("origem", "todos") or "todos").strip() or "todos",
            "grupo_cota": str(args.get("grupo_cota", "todos") or "todos").strip() or "todos",
            "tipo": str(args.get("tipo", "") or "").strip(),
            "reinf": str(args.get("reinf", "todos") or "todos").strip() or "todos",
            "responsavel": str(args.get("responsavel", "todos") or "todos").strip() or "todos",
            "pagamento": (
                "pagos"
                if visao == "conferencia"
                else (str(args.get("pagamento", "todos") or "todos").strip() or "todos")
            ),
            "janela_meses": str(janela_normalizer(args.get("janela_meses"))),
        }

    @staticmethod
    def normalize_beneficiary_filters(
        args: Mapping[str, Any],
        *,
        integer_normalizer: Callable[[Any, int], int],
        fiscal_options: dict[str, str],
    ) -> dict[str, str | int | bool]:
        busca = str(args.get("beneficiario_q", "") or "").strip()
        pagina = integer_normalizer(args.get("pagina"), 1)
        fiscal = str(args.get("fiscal", "todos") or "todos").strip() or "todos"
        if fiscal not in fiscal_options:
            fiscal = "todos"

        explorar = (
            str(args.get("beneficiarios_explorar", "") or "").strip() == "1"
            or bool(busca)
            or pagina > 1
        )
        return {
            "q": busca,
            "pagina": pagina,
            "fiscal": fiscal,
            "explorar": explorar,
        }

    @staticmethod
    def memory_filters(
        filtros: dict[str, str] | None,
        *,
        text_normalizer: Callable[[str | None], str],
    ) -> dict[str, str]:
        filtros = filtros or {}
        return {
            "texto": text_normalizer(filtros.get("q")),
            "grupo_cota": str(filtros.get("grupo_cota", "todos") or "todos").strip(),
            "tipo": str(filtros.get("tipo", "") or "").strip(),
            "reinf": str(filtros.get("reinf", "todos") or "todos").strip(),
        }
