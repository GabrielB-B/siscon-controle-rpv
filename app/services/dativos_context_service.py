from __future__ import annotations

from typing import Any, Callable

from flask import url_for


class DativosContextService:
    @staticmethod
    def build_ci_list_context(
        *,
        filtros_request: Any,
        linhas_paginadas: list[dict[str, Any]],
        usuarios: list[Any],
        filtro_responsavel: str,
        total_ci: int,
        total_lotes: int,
        total_itens_com_irrf: int,
        cis_incompletas: list[Any],
        cis_incompletas_ocultas: list[Any],
        cis_descartadas: list[Any],
        situacoes_rpv: list[Any],
        situacoes_imposto: list[Any],
        ordenar_atual: str,
        direcao_atual: str,
        por_pagina: int,
        paginacao: dict[str, Any],
        busca_processo_contexto: dict[str, Any] | None,
        mostrar_encerrados: bool,
        url_retorno_atual: str,
        page_window_builder: Callable[[int, int], list[int]],
        query_params_merger: Callable[..., dict[str, Any]],
        sort_direction_resolver: Callable[[str, str, str], str],
        route_endpoint: str = "dativos.lista_cis",
    ) -> dict[str, Any]:
        filtros_dict = filtros_request.to_dict()
        filtros_ocultos = query_params_merger(
            filtros_dict,
            pagina=None,
            por_pagina=None,
        )
        sort_urls = {
            chave: url_for(
                route_endpoint,
                **query_params_merger(
                    filtros_dict,
                    ordenar=chave,
                    direcao=sort_direction_resolver(
                        ordenar_atual,
                        direcao_atual,
                        chave,
                    ),
                    pagina=1,
                ),
            )
            for chave in ("exercicio", "grupo", "resumo", "valor", "imposto")
        }
        paginas_visiveis = page_window_builder(
            paginacao["total_paginas"],
            paginacao["pagina"],
        )
        pagina_urls = {
            numero: url_for(
                route_endpoint,
                **query_params_merger(filtros_dict, pagina=numero),
            )
            for numero in paginas_visiveis
        }
        pagina_anterior_url = (
            url_for(
                route_endpoint,
                **query_params_merger(filtros_dict, pagina=paginacao["pagina_anterior"]),
            )
            if paginacao["tem_anterior"]
            else None
        )
        proxima_pagina_url = (
            url_for(
                route_endpoint,
                **query_params_merger(filtros_dict, pagina=paginacao["proxima_pagina"]),
            )
            if paginacao["tem_proxima"]
            else None
        )

        return {
            "linhas": linhas_paginadas,
            "filtros": filtros_request,
            "filtros_ocultos": filtros_ocultos,
            "usuarios": usuarios,
            "filtro_responsavel": filtro_responsavel,
            "total_ci": total_ci,
            "total_ci_incompletas": len(cis_incompletas),
            "total_ci_incompletas_ocultas": len(cis_incompletas_ocultas),
            "total_ci_descartadas": len(cis_descartadas),
            "total_lotes": total_lotes,
            "total_itens_com_irrf": total_itens_com_irrf,
            "cis_incompletas": cis_incompletas,
            "cis_incompletas_ocultas": cis_incompletas_ocultas,
            "cis_descartadas": cis_descartadas,
            "situacoes_rpv": situacoes_rpv,
            "situacoes_imposto": situacoes_imposto,
            "ordenar_atual": ordenar_atual,
            "direcao_atual": direcao_atual,
            "por_pagina": por_pagina,
            "paginacao": paginacao,
            "paginas_visiveis": paginas_visiveis,
            "pagina_urls": pagina_urls,
            "pagina_anterior_url": pagina_anterior_url,
            "proxima_pagina_url": proxima_pagina_url,
            "sort_urls": sort_urls,
            "busca_processo_contexto": busca_processo_contexto,
            "mostrar_encerrados": mostrar_encerrados,
            "url_retorno_atual": url_retorno_atual,
        }
