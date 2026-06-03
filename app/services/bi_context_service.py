from __future__ import annotations

from typing import Any, Callable

from flask import url_for

from app.models import User
from app.services.bi_projection_service import BIProjectionService


class BIContextService:
    @staticmethod
    def _active_users():
        return User.query.filter_by(ativo=True).order_by(User.nome.asc()).all()

    @staticmethod
    def _types_from_dataset(dataset: list[dict]) -> list[str]:
        return sorted({row["tipo"] for row in dataset})

    @staticmethod
    def _export_url(filtros: dict[str, str], visao_bi: str) -> str:
        return url_for(
            "dashboard.exportar_bi_conferencia_csv"
            if visao_bi == "conferencia"
            else "dashboard.exportar_bi_csv",
            **{chave: valor for chave, valor in filtros.items() if valor},
        )

    @staticmethod
    def build_main_context(
        *,
        filtros: dict[str, str],
        visao_bi: str,
        current_user_id: int | None,
        dataset_loader: Callable[..., tuple[list[dict], list[dict]]],
        janela_loader: Callable[[str | None], int],
        calculators: dict[str, Callable[..., Any]],
        url_builders: dict[str, Callable[..., str]],
        constants: dict[str, Any],
    ) -> dict[str, Any]:
        dataset, dataset_filtrado = dataset_loader(filtros, visao=visao_bi)
        janela_meses = janela_loader(filtros.get("janela_meses"))
        projecao_operacional = None

        if BIProjectionService.supports_operational_projection(filtros, visao=visao_bi):
            projecao_operacional = BIProjectionService.build_operational_projection(
                filtros,
                current_user_id=current_user_id,
            )

        if projecao_operacional:
            resumo_grupos = calculators["resumo_grupos_projetado"](projecao_operacional, filtros)
            series_grupos_cota = calculators["series_grupos_projetado"](
                projecao_operacional,
                resumo_grupos,
                janela_meses=janela_meses,
                filtros=filtros,
            )
            serie_mensal_grupos = calculators["serie_mensal_grupos_projetada"](projecao_operacional)
            dativos_competencia = calculators["resumo_dativos_projetado"](
                projecao_operacional,
                resumo_grupos.get("competencia_referencia"),
            )
            serie_dativos = calculators["serie_dativos_projetada"](projecao_operacional)
        else:
            resumo_grupos = calculators["resumo_grupos"](dataset_filtrado, filtros)
            series_grupos_cota = calculators["series_grupos"](
                dataset_filtrado,
                resumo_grupos,
                janela_meses=janela_meses,
                filtros=filtros,
            )
            serie_mensal_grupos = calculators["serie_mensal_grupos"](dataset_filtrado)
            dativos_competencia = calculators["resumo_dativos"](
                dataset_filtrado,
                resumo_grupos.get("competencia_referencia"),
            )
            serie_dativos = calculators["serie_dativos"](dataset_filtrado)

        conferencia_bi = calculators["conferencia"](dataset_filtrado) if visao_bi == "conferencia" else None
        conferencia_pendencias_documentais = (
            calculators["conferencia_pendencias_documentais"](filtros)
            if visao_bi == "conferencia"
            else None
        )
        cards = calculators["cards"](dataset_filtrado, resumo_grupos, dativos_competencia)
        resumo_irrf = calculators["resumo_irrf"](
            dataset_filtrado,
            competencia_referencia=resumo_grupos.get("competencia_referencia"),
            janela_meses=janela_meses,
        )
        acumulado_anual_grupos = calculators["acumulado_anual"](resumo_grupos)
        pendencias_bi = calculators["pendencias"](dataset_filtrado)
        graficos_ciclo_operacional = calculators["graficos_ciclo"](
            resumo_grupos,
            dativos_competencia,
        )
        sinais_operacionais = calculators["sinais_operacionais"](
            dataset_filtrado,
            resumo_grupos,
            dativos_competencia,
        )
        serie_beneficiarios_fluxo = calculators["agrupar_beneficiarios_fluxo"](dataset_filtrado)
        top_beneficiarios_fluxo = serie_beneficiarios_fluxo[: constants["beneficiarios_destaque"]]
        top_beneficiarios_total = len(serie_beneficiarios_fluxo)
        top_beneficiarios_periodo = calculators["periodo_pagamentos"](dataset_filtrado, filtros)
        beneficiarios_url = url_builders["beneficiarios"](filtros)
        valores_por_status_reinf = calculators["agrupar_por_campo"](
            calculators["linhas_bi_pagas"](dataset_filtrado),
            "reinf_status",
        )
        registros_por_responsavel = calculators["agrupar_por_campo_quantidade"](
            dataset_filtrado,
            "responsavel",
        )
        registros_por_tipo = calculators["agrupar_por_campo_quantidade"](
            dataset_filtrado,
            "tipo",
        )
        distribuicao_pagamento = calculators["distribuicao_pagamento"](dataset_filtrado)
        total_linhas = len(dataset_filtrado)
        tipos_disponiveis = BIContextService._types_from_dataset(dataset)
        usuarios = BIContextService._active_users()
        visao_urls = {
            chave: url_builders["visao"](filtros, visao=chave)
            for chave in constants["visao_navegacao"]
        }
        janela_urls = {
            quantidade: url_builders["janela"](filtros, janela_meses=quantidade)
            for quantidade in constants["janela_opcoes"]
        }
        export_url = BIContextService._export_url(filtros, visao_bi)

        return {
            "filtros": filtros,
            "visao_bi": visao_bi,
            "visao_labels": constants["visao_labels"],
            "visao_navegacao": constants["visao_navegacao"],
            "visao_urls": visao_urls,
            "origem_opcoes": constants["origem_opcoes"],
            "pagamento_opcoes": constants["pagamento_opcoes"],
            "grupo_cota_opcoes": constants["grupo_cota_opcoes"],
            "janela_opcoes": constants["janela_opcoes"],
            "janela_meses": janela_meses,
            "janela_urls": janela_urls,
            "usuarios": usuarios,
            "tipo_opcoes": tipos_disponiveis,
            "cards": cards,
            "resumo_grupos": resumo_grupos,
            "conferencia_bi": conferencia_bi,
            "conferencia_pendencias_documentais": conferencia_pendencias_documentais,
            "series_grupos_cota": series_grupos_cota,
            "resumo_irrf": resumo_irrf,
            "serie_mensal_grupos": serie_mensal_grupos,
            "acumulado_anual_grupos": acumulado_anual_grupos,
            "pendencias_bi": pendencias_bi,
            "dativos_competencia": dativos_competencia,
            "graficos_ciclo_operacional": graficos_ciclo_operacional,
            "sinais_operacionais": sinais_operacionais,
            "serie_dativos": serie_dativos,
            "top_beneficiarios_fluxo": top_beneficiarios_fluxo,
            "top_beneficiarios_total": top_beneficiarios_total,
            "top_beneficiarios_periodo": top_beneficiarios_periodo,
            "beneficiarios_url": beneficiarios_url,
            "valores_por_status_reinf": valores_por_status_reinf,
            "registros_por_responsavel": registros_por_responsavel,
            "registros_por_tipo": registros_por_tipo,
            "distribuicao_pagamento": distribuicao_pagamento,
            "total_linhas": total_linhas,
            "export_url": export_url,
        }

    @staticmethod
    def build_beneficiaries_context(
        *,
        filtros: dict[str, str],
        filtros_beneficiarios: dict[str, str | int | bool],
        visao_bi: str,
        dataset_loader: Callable[..., tuple[list[dict], list[dict]]],
        calculators: dict[str, Callable[..., Any]],
        url_builders: dict[str, Callable[..., str]],
        constants: dict[str, Any],
    ) -> dict[str, Any]:
        dataset, dataset_filtrado = dataset_loader(filtros, visao=visao_bi)
        serie_beneficiarios_fluxo = calculators["agrupar_beneficiarios_fluxo"](dataset_filtrado)
        resumo_beneficiarios = calculators["resumo_beneficiarios"](serie_beneficiarios_fluxo)
        beneficiarios_exploracao = calculators["exploracao_beneficiarios"](
            serie_beneficiarios_fluxo,
            busca=filtros_beneficiarios.get("q"),
            pagina=filtros_beneficiarios.get("pagina", 1),
            fiscal=filtros_beneficiarios.get("fiscal"),
            destaque=0,
            pagina_tamanho=constants["beneficiarios_por_pagina"],
        )
        beneficiarios_exploracao_urls = {
            "limpar": url_builders["beneficiarios_pagina"](filtros, None, q="", pagina=1, fiscal="todos"),
            "anterior": (
                url_builders["beneficiarios_pagina"](
                    filtros,
                    filtros_beneficiarios,
                    pagina=max(int(beneficiarios_exploracao["pagina"]) - 1, 1),
                )
                if beneficiarios_exploracao["tem_anterior"]
                else ""
            ),
            "proxima": (
                url_builders["beneficiarios_pagina"](
                    filtros,
                    filtros_beneficiarios,
                    pagina=int(beneficiarios_exploracao["pagina"]) + 1,
                )
                if beneficiarios_exploracao["tem_proxima"]
                else ""
            ),
        }

        return {
            "filtros": filtros,
            "visao_bi": visao_bi,
            "origem_opcoes": constants["origem_opcoes"],
            "pagamento_opcoes": constants["pagamento_opcoes"],
            "grupo_cota_opcoes": constants["grupo_cota_opcoes"],
            "usuarios": BIContextService._active_users(),
            "tipo_opcoes": BIContextService._types_from_dataset(dataset),
            "fiscal_opcoes": constants["fiscal_opcoes"],
            "resumo_beneficiarios": resumo_beneficiarios,
            "top_beneficiarios_periodo": calculators["periodo_pagamentos"](dataset_filtrado, filtros),
            "beneficiarios_filtros": filtros_beneficiarios,
            "beneficiarios_exploracao": beneficiarios_exploracao,
            "beneficiarios_exploracao_urls": beneficiarios_exploracao_urls,
            "bi_url": url_builders["bi"](filtros),
            "export_url": BIContextService._export_url(filtros, visao_bi),
        }
