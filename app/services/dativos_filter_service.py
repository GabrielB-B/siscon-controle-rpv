from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


class DativosFilterService:
    @staticmethod
    def normalize_ci_filters(
        args: Mapping[str, Any],
        *,
        closed_queue_normalizer: Callable[[Any, str], bool],
        sort_direction_normalizer: Callable[..., str],
        page_normalizer: Callable[..., int],
        page_size_normalizer: Callable[..., int],
    ) -> dict[str, str | int | bool]:
        situacao_rpv_id = str(args.get("situacao_rpv_id", "") or "").strip()

        return {
            "q": str(args.get("q", "") or "").strip(),
            "ne": str(args.get("ne", "") or "").strip(),
            "exercicio": str(args.get("exercicio", "") or "").strip(),
            "ci": str(args.get("ci", "") or "").strip(),
            "responsavel": str(args.get("responsavel", "meus") or "meus").strip() or "meus",
            "grupo": str(args.get("grupo", "todos") or "todos").strip() or "todos",
            "situacao_rpv_id": situacao_rpv_id,
            "situacao_imposto_id": str(args.get("situacao_imposto_id", "") or "").strip(),
            "mostrar_encerrados": closed_queue_normalizer(
                args.get("mostrar_encerrados"),
                situacao_rpv_id,
            ),
            "ordenar": str(args.get("ordenar", "exercicio") or "exercicio").strip() or "exercicio",
            "direcao": sort_direction_normalizer(args.get("direcao"), padrao="desc"),
            "pagina": page_normalizer(args.get("pagina"), padrao=1),
            "por_pagina": page_size_normalizer(args.get("por_pagina"), padrao=20),
        }
