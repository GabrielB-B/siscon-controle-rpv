from __future__ import annotations

from typing import Any, Callable

from flask import url_for


class ReinfContextService:
    @staticmethod
    def build_index_context(
        *,
        filtros_request: Any,
        filtros: dict[str, Any],
        usuarios: list[Any],
        anos_disponiveis: list[str],
        registros: list[dict[str, Any]],
        paginacao: dict[str, Any],
        conferencia_mensal: dict[str, Any],
        conferencia_anual: dict[str, Any],
        export_url: str | None,
        url_retorno_atual: str,
        view_options: dict[str, str],
        status_opcoes: list[str],
        status_filtros: list[tuple[str, str]],
        ordenacao_opcoes: list[tuple[str, str]],
        direcao_opcoes: list[tuple[str, str]],
        competencia_legivel: Callable[[str | None], str],
        query_params_merger: Callable[..., dict[str, Any]],
        page_window_builder: Callable[[int, int], list[int]],
        sort_direction_resolver: Callable[[str, str, str], str],
    ) -> dict[str, Any]:
        visao = str(filtros["visao"])
        filtros_dict = filtros_request.to_dict()
        view_urls = {
            chave: url_for(
                "reinf.index",
                **query_params_merger(
                    filtros_dict,
                    visao=chave,
                    pagina=None,
                ),
            )
            for chave in view_options
        }

        filtros_ocultos: dict[str, Any] = {}
        sort_urls: dict[str, str] = {}
        paginas_visiveis: list[int] = []
        pagina_urls: dict[int, str] = {}
        pagina_anterior_url = None
        proxima_pagina_url = None

        if visao == "operacional":
            filtros_ocultos = query_params_merger(
                filtros_dict,
                pagina=None,
                por_pagina=None,
            )
            sort_keys = [
                "origem",
                "competencia",
                "data_pagamento",
                "beneficiario",
                "imposto",
                "status_reinf",
            ]
            sort_urls = {
                chave: url_for(
                    "reinf.index",
                    **query_params_merger(
                        filtros_dict,
                        ordenar=chave,
                        direcao=sort_direction_resolver(
                            str(filtros["ordenar"]),
                            str(filtros["direcao"]),
                            chave,
                        ),
                        pagina=1,
                    ),
                )
                for chave in sort_keys
            }
            paginas_visiveis = page_window_builder(
                int(paginacao["total_paginas"]),
                int(paginacao["pagina"]),
            )
            pagina_urls = {
                numero: url_for(
                    "reinf.index",
                    **query_params_merger(filtros_dict, pagina=numero),
                )
                for numero in paginas_visiveis
            }
            pagina_anterior_url = (
                url_for(
                    "reinf.index",
                    **query_params_merger(filtros_dict, pagina=paginacao["pagina_anterior"]),
                )
                if paginacao["tem_anterior"]
                else None
            )
            proxima_pagina_url = (
                url_for(
                    "reinf.index",
                    **query_params_merger(filtros_dict, pagina=paginacao["proxima_pagina"]),
                )
                if paginacao["tem_proxima"]
                else None
            )

        return {
            "visao_reinf": visao,
            "view_urls": view_urls,
            "visao_opcoes": view_options,
            "registros": registros,
            "conferencia_mensal": conferencia_mensal,
            "conferencia_anual": conferencia_anual,
            "anos_disponiveis": anos_disponiveis,
            "usuarios": usuarios,
            "reinf_status_opcoes": status_opcoes,
            "reinf_status_filtros": status_filtros,
            "reinf_ordenacao_opcoes": ordenacao_opcoes,
            "reinf_direcao_opcoes": direcao_opcoes,
            "reinf_ordenacao_labels": dict(ordenacao_opcoes),
            "export_url": export_url,
            "filtros_ocultos": filtros_ocultos,
            "filtro_competencia": filtros["competencia"],
            "filtro_ano": filtros["ano"],
            "filtro_responsavel": filtros["responsavel"],
            "filtro_reinf_status": filtros["reinf_status"],
            "filtro_busca": filtros["q"],
            "ordenar_atual": filtros["ordenar"],
            "direcao_atual": filtros["direcao"],
            "por_pagina": filtros["por_pagina"],
            "paginacao": paginacao,
            "paginas_visiveis": paginas_visiveis,
            "pagina_urls": pagina_urls,
            "pagina_anterior_url": pagina_anterior_url,
            "proxima_pagina_url": proxima_pagina_url,
            "sort_urls": sort_urls,
            "competencia_padrao": filtros["competencia_padrao"],
            "competencia_bloqueada": filtros["competencia_bloqueada"],
            "competencias_pendentes": filtros["competencias_pendentes"],
            "competencia_legivel": competencia_legivel,
            "url_retorno_atual": url_retorno_atual,
        }
