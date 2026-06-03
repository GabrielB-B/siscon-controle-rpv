from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class CadastrosFilterService:
    @staticmethod
    def normalize_rpvs_filters(
        args: Mapping[str, Any],
        *,
        closed_queue_normalizer: Callable[[Any, str], bool],
        sort_direction_normalizer: Callable[..., str],
        page_normalizer: Callable[..., int],
        page_size_normalizer: Callable[..., int],
    ) -> dict[str, str | int | bool]:
        filtro_empenho = str(args.get("situacao_empenho_id", "") or "").strip()
        filtro_imposto = str(args.get("situacao_imposto_id", "") or "").strip()

        return {
            "q": str(args.get("q", "") or "").strip(),
            "ne": str(args.get("ne", "") or "").strip(),
            "exercicio": str(args.get("exercicio", "") or "").strip(),
            "responsavel": str(args.get("responsavel", "meus") or "meus").strip() or "meus",
            "situacao_empenho_id": filtro_empenho,
            "situacao_imposto_id": filtro_imposto,
            "mostrar_encerrados": closed_queue_normalizer(
                args.get("mostrar_encerrados"),
                filtro_empenho or filtro_imposto,
            ),
            "ordenar": str(args.get("ordenar", "competencia") or "competencia").strip()
            or "competencia",
            "direcao": sort_direction_normalizer(args.get("direcao"), padrao="desc"),
            "pagina": page_normalizer(args.get("pagina"), padrao=1),
            "por_pagina": page_size_normalizer(args.get("por_pagina"), padrao=20),
        }
