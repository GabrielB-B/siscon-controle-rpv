from __future__ import annotations

from typing import Any, Callable

from flask import url_for


class CadastrosContextService:
    @staticmethod
    def build_rpvs_list_context(
        *,
        filtros_request: Any,
        filtros: dict[str, Any],
        registros: list[Any],
        tipos_rpv: list[Any],
        situacoes_empenho: list[Any],
        situacoes_imposto: list[Any],
        usuarios: list[Any],
        paginacao: dict[str, Any],
        sort_fields: list[str],
        busca_processo_contexto: dict[str, Any] | None,
        mostrar_encerrados: bool,
        total_pendencias_documentais: int,
        url_retorno_atual: str,
        query_params_merger: Callable[..., dict[str, Any]],
        page_window_builder: Callable[[int, int], list[int]],
        sort_direction_resolver: Callable[[str, str, str], str],
        route_endpoint: str = "cadastros.lista_rpvs",
    ) -> dict[str, Any]:
        filtros_dict = filtros_request.to_dict()
        ordenar_atual = str(filtros["ordenar"])
        direcao_atual = str(filtros["direcao"])

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
            for chave in sort_fields
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
            "registros": registros,
            "tipos_rpv": tipos_rpv,
            "situacoes_empenho": situacoes_empenho,
            "situacoes_imposto": situacoes_imposto,
            "usuarios": usuarios,
            "filtros": filtros_request,
            "filtro_responsavel": filtros["responsavel"],
            "filtros_ocultos": filtros_ocultos,
            "ordenar_atual": ordenar_atual,
            "direcao_atual": direcao_atual,
            "por_pagina": filtros["por_pagina"],
            "paginacao": paginacao,
            "paginas_visiveis": paginas_visiveis,
            "pagina_urls": pagina_urls,
            "pagina_anterior_url": pagina_anterior_url,
            "proxima_pagina_url": proxima_pagina_url,
            "sort_urls": sort_urls,
            "busca_processo_contexto": busca_processo_contexto,
            "mostrar_encerrados": mostrar_encerrados,
            "total_pendencias_documentais": total_pendencias_documentais,
            "url_retorno_atual": url_retorno_atual,
        }
