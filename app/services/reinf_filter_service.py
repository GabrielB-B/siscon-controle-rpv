from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class ReinfFilterService:
    @staticmethod
    def normalize_filters(
        args: Mapping[str, Any],
        *,
        visao_normalizer: Callable[[str | None], str],
        competencia_operacional_resolver: Callable[[str | None], dict[str, Any]],
        competencia_livre_resolver: Callable[[str | None], dict[str, Any]],
        competencia_mes_atual_loader: Callable[[], str],
        ano_normalizer: Callable[[str | None, str], str],
        ordenacao_normalizer: Callable[[str | None], str],
        direcao_normalizer: Callable[..., str],
        pagina_normalizer: Callable[..., int],
        page_size_normalizer: Callable[..., int],
        status_padrao: str,
    ) -> dict[str, str | int | bool | list[str]]:
        visao = visao_normalizer(args.get("visao"))
        competencia_informada = str(args.get("competencia", "") or "").strip()
        resolucao_competencia = (
            competencia_operacional_resolver(competencia_informada)
            if visao == "operacional"
            else competencia_livre_resolver(competencia_informada)
        )
        ano_padrao = (
            str(resolucao_competencia["competencia_aplicada"])[:4]
            if resolucao_competencia["competencia_aplicada"]
            else competencia_mes_atual_loader()[:4]
        )

        return {
            "visao": visao,
            "competencia": str(resolucao_competencia["competencia_aplicada"] or ""),
            "ano": ano_normalizer(args.get("ano"), ano_padrao),
            "responsavel": str(args.get("responsavel", "todos") or "todos").strip() or "todos",
            "reinf_status": str(args.get("reinf_status", "") or "").strip() or status_padrao,
            "q": str(args.get("q", "") or "").strip(),
            "ordenar": ordenacao_normalizer(args.get("ordenar")),
            "direcao": direcao_normalizer(args.get("direcao"), padrao="asc"),
            "pagina": pagina_normalizer(args.get("pagina"), padrao=1),
            "por_pagina": page_size_normalizer(args.get("por_pagina"), padrao=20),
            "competencia_padrao": str(resolucao_competencia["competencia_padrao"] or ""),
            "competencias_pendentes": list(resolucao_competencia["competencias_pendentes"] or []),
            "competencia_bloqueada": bool(resolucao_competencia["competencia_bloqueada"]),
        }
