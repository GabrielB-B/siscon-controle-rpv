from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from app.models import Processo, RegistroRPV
from app.utils.normalizers import normalizar_documento, normalizar_numero_processo


class CadastrosDatasetService:
    @staticmethod
    def build_rpvs_query(
        *,
        filtros: dict[str, str | int | bool],
        current_user_id: int,
        hidden_status_ids: Iterable[int],
        cancelled_status_ids: Iterable[int],
        hidden_imposto_status_ids: Iterable[int],
    ):
        q = str(filtros.get("q", "") or "").strip()
        filtro_ne = str(filtros.get("ne", "") or "").strip()
        filtro_exercicio = str(filtros.get("exercicio", "") or "").strip()
        filtro_responsavel = str(filtros.get("responsavel", "meus") or "meus")
        filtro_empenho = str(filtros.get("situacao_empenho_id", "") or "").strip()
        filtro_imposto = str(filtros.get("situacao_imposto_id", "") or "").strip()
        mostrar_encerrados = bool(filtros.get("mostrar_encerrados"))

        query = (
            RegistroRPV.query.options(
                joinedload(RegistroRPV.processo),
                joinedload(RegistroRPV.situacao_empenho),
                joinedload(RegistroRPV.situacao_imposto),
                joinedload(RegistroRPV.elaborador),
                joinedload(RegistroRPV.criado_por),
            )
            .join(Processo)
        )

        if q:
            q_doc = normalizar_documento(q)
            q_processo = normalizar_numero_processo(q)
            filtros_busca = [
                RegistroRPV.nome_beneficiario.ilike(f"%{q}%"),
                Processo.numero_processo.ilike(f"%{q}%"),
                Processo.processo_edoc.ilike(f"%{q}%"),
                RegistroRPV.historico_auto.ilike(f"%{q}%"),
                RegistroRPV.nota_empenho.ilike(f"%{q}%"),
                RegistroRPV.numero_se.ilike(f"%{q}%"),
                RegistroRPV.ordem_bancaria.ilike(f"%{q}%"),
                RegistroRPV.ob_imposto.ilike(f"%{q}%"),
            ]
            if q_processo and q_processo != q:
                filtros_busca.append(Processo.numero_processo.ilike(f"%{q_processo}%"))
            if q_doc:
                filtros_busca.append(RegistroRPV.documento_normalizado.ilike(f"%{q_doc}%"))
            query = query.filter(or_(*filtros_busca))

        if filtro_ne:
            query = query.filter(RegistroRPV.nota_empenho.ilike(f"%{filtro_ne}%"))

        if filtro_exercicio:
            query = query.filter(Processo.exercicio == filtro_exercicio)

        if filtro_responsavel == "meus":
            query = query.filter(RegistroRPV.elaborador_id == current_user_id)
        elif filtro_responsavel not in ("", "todos"):
            query = query.filter(RegistroRPV.elaborador_id == int(filtro_responsavel))

        if filtro_empenho:
            query = query.filter(RegistroRPV.situacao_empenho_id == int(filtro_empenho))
        elif not mostrar_encerrados and hidden_status_ids:
            filtros_fila = [~RegistroRPV.situacao_empenho_id.in_(set(hidden_status_ids))]

            if hidden_imposto_status_ids:
                fiscal_pendente = [
                    ~RegistroRPV.situacao_imposto_id.in_(set(hidden_imposto_status_ids)),
                    RegistroRPV.sem_irrf.is_(False),
                ]
                if cancelled_status_ids:
                    fiscal_pendente.append(
                        ~RegistroRPV.situacao_empenho_id.in_(set(cancelled_status_ids))
                    )
                filtros_fila.append(and_(*fiscal_pendente))

            query = query.filter(or_(*filtros_fila))

        if filtro_imposto:
            query = query.filter(RegistroRPV.situacao_imposto_id == int(filtro_imposto))

        return query

    @staticmethod
    def collect_rpvs_queue_page(
        *,
        filtros: dict[str, str | int | bool],
        current_user_id: int,
        hidden_status_ids: Iterable[int],
        cancelled_status_ids: Iterable[int],
        hidden_imposto_status_ids: Iterable[int],
        pagination_builder,
    ) -> dict[str, Any]:
        ordenar_mapa = {
            "competencia": Processo.exercicio,
            "processo": Processo.numero_processo,
            "resumo": RegistroRPV.resumo_operacional,
            "valor": RegistroRPV.valor_bruto,
            "imposto": RegistroRPV.valor_irrf,
        }
        ordenar = str(filtros.get("ordenar", "competencia") or "competencia")
        direcao = str(filtros.get("direcao", "desc") or "desc")
        pagina = int(filtros.get("pagina", 1) or 1)
        por_pagina = int(filtros.get("por_pagina", 20) or 20)

        query = CadastrosDatasetService.build_rpvs_query(
            filtros=filtros,
            current_user_id=current_user_id,
            hidden_status_ids=hidden_status_ids,
            cancelled_status_ids=cancelled_status_ids,
            hidden_imposto_status_ids=hidden_imposto_status_ids,
        )

        coluna_ordenacao = ordenar_mapa.get(ordenar, Processo.exercicio)
        clausula_ordenacao = (
            coluna_ordenacao.asc() if direcao == "asc" else coluna_ordenacao.desc()
        )

        total_registros = query.count()
        paginacao = pagination_builder(total_registros, pagina, por_pagina)
        registros = (
            query.order_by(clausula_ordenacao, RegistroRPV.criado_em.desc())
            .offset((paginacao["pagina"] - 1) * paginacao["por_pagina"])
            .limit(paginacao["por_pagina"])
            .all()
        )

        return {
            "registros": registros,
            "paginacao": paginacao,
            "ordenar_mapa": ordenar_mapa,
        }
