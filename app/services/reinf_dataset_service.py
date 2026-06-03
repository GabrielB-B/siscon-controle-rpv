from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.models import DativoCI, DativoItem, Processo, RegistroRPV
from app.utils.normalizers import normalizar_documento


class ReinfDatasetService:
    @staticmethod
    def build_rpvs_query(
        *,
        competencia: str | None,
        filtro_responsavel: str,
        filtro_busca: str,
        ano: str | None,
        text_normalizer: Callable[[str | None], str],
        payment_filter_applier: Callable[..., Any],
    ):
        busca_bruta = str(filtro_busca or "").strip()
        busca = text_normalizer(filtro_busca)
        busca_doc = normalizar_documento(filtro_busca)

        query = (
            RegistroRPV.query.options(
                joinedload(RegistroRPV.elaborador),
                joinedload(RegistroRPV.processo),
                joinedload(RegistroRPV.situacao_imposto),
                joinedload(RegistroRPV.situacao_empenho),
            )
            .filter(RegistroRPV.ativo.is_(True), RegistroRPV.sem_irrf.is_(False))
            .order_by(RegistroRPV.criado_em.desc())
        )

        if filtro_responsavel not in ("", "todos", "meus"):
            query = query.filter(RegistroRPV.elaborador_id == filtro_responsavel)

        query = payment_filter_applier(
            query,
            RegistroRPV.data_pagamento,
            competencia=competencia,
            ano=ano,
        )

        if busca or busca_doc:
            query = query.join(RegistroRPV.processo)
            filtros_busca = []
            if busca:
                filtros_busca.extend(
                    [
                        RegistroRPV.nome_beneficiario_normalizado.contains(busca),
                        Processo.numero_processo.ilike(f"%{busca_bruta}%"),
                        Processo.processo_edoc.ilike(f"%{busca_bruta}%"),
                        RegistroRPV.historico_auto.ilike(f"%{busca_bruta}%"),
                    ]
                )
            if busca_doc:
                filtros_busca.append(RegistroRPV.documento_normalizado.contains(busca_doc))
            query = query.filter(or_(*filtros_busca))

        return query

    @staticmethod
    def build_dativos_query(
        *,
        competencia: str | None,
        filtro_responsavel: str,
        filtro_busca: str,
        ano: str | None,
        text_normalizer: Callable[[str | None], str],
        payment_filter_applier: Callable[..., Any],
    ):
        busca_bruta = str(filtro_busca or "").strip()
        busca = text_normalizer(filtro_busca)
        busca_doc = normalizar_documento(filtro_busca)

        query = (
            DativoItem.query.options(
                joinedload(DativoItem.dativo_ci).joinedload(DativoCI.responsavel),
                joinedload(DativoItem.situacao_rpv),
            )
            .filter(DativoItem.grupo == "com_irrf", DativoItem.ativo.is_(True))
            .order_by(DativoItem.criado_em.desc())
        )

        if filtro_responsavel not in ("", "todos", "meus"):
            query = query.join(DativoItem.dativo_ci).filter(DativoCI.responsavel_id == filtro_responsavel)

        query = payment_filter_applier(
            query,
            DativoItem.data_pagamento,
            competencia=competencia,
            ano=ano,
        )

        if busca or busca_doc:
            if filtro_responsavel in ("", "todos", "meus"):
                query = query.join(DativoItem.dativo_ci)
            filtros_busca = []
            if busca:
                filtros_busca.extend(
                    [
                        DativoItem.nome_beneficiario_normalizado.contains(busca),
                        DativoItem.numero_processo.ilike(f"%{busca_bruta}%"),
                        DativoCI.processo_edoc.ilike(f"%{busca_bruta}%"),
                        DativoItem.resumo_operacional.ilike(f"%{busca_bruta}%"),
                    ]
                )
            if busca_doc:
                filtros_busca.append(DativoItem.cpf_normalizado.contains(busca_doc))
            query = query.filter(or_(*filtros_busca))

        return query

    @staticmethod
    def collect_base(
        competencia: str | None,
        filtro_responsavel: str,
        filtro_busca: str,
        *,
        ano: str | None = None,
        retorno_url: str | None = None,
        rpv_query_builder: Callable[..., Any],
        dativo_query_builder: Callable[..., Any],
        rpv_has_irrf: Callable[[Any], bool],
        month_filter_checker: Callable[[Any, str], bool],
        year_filter_checker: Callable[[Any, str], bool],
        responsavel_filter_checker: Callable[[Any, str], bool],
        rpv_record_builder: Callable[..., dict[str, Any]],
        dativo_record_builder: Callable[..., dict[str, Any]],
        busca_filter_checker: Callable[[dict[str, Any], str, str], bool],
        sort_key_builder: Callable[[dict[str, Any]], Any],
        text_normalizer: Callable[[str | None], str],
    ) -> list[dict]:
        busca = text_normalizer(filtro_busca)
        busca_doc = normalizar_documento(filtro_busca)
        registros: list[dict[str, Any]] = []

        rpvs = rpv_query_builder(
            competencia=competencia,
            filtro_responsavel=filtro_responsavel,
            filtro_busca=filtro_busca,
            ano=ano,
        ).all()
        for registro in rpvs:
            if getattr(registro, "status_principal_cancelado", False):
                continue
            if not rpv_has_irrf(registro):
                continue
            if competencia and not month_filter_checker(registro.data_pagamento, competencia):
                continue
            if ano and not year_filter_checker(registro.data_pagamento, ano):
                continue
            if not responsavel_filter_checker(registro.elaborador_id, filtro_responsavel):
                continue

            linha = rpv_record_builder(registro, retorno_url=retorno_url)
            if not busca_filter_checker(linha, busca, busca_doc):
                continue
            registros.append(linha)

        itens_irrf = dativo_query_builder(
            competencia=competencia,
            filtro_responsavel=filtro_responsavel,
            filtro_busca=filtro_busca,
            ano=ano,
        ).all()
        for item in itens_irrf:
            if getattr(item, "status_principal_cancelado", False):
                continue
            if competencia and not month_filter_checker(item.data_pagamento, competencia):
                continue
            if ano and not year_filter_checker(item.data_pagamento, ano):
                continue
            if not responsavel_filter_checker(item.dativo_ci.responsavel_id if item.dativo_ci else None, filtro_responsavel):
                continue

            linha = dativo_record_builder(item, retorno_url=retorno_url)
            if not busca_filter_checker(linha, busca, busca_doc):
                continue
            registros.append(linha)

        registros.sort(key=sort_key_builder, reverse=True)
        return registros
