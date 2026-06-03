from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from flask import url_for
from sqlalchemy.orm import selectinload

from app.models import DativoCI
from app.services.dativos_service import DativosService
from app.utils.normalizers import normalizar_documento, normalizar_numero_processo


class DativosDatasetService:
    @staticmethod
    def collect_ci_queue_dataset(
        *,
        filtros: dict[str, str | int | bool],
        current_user_id: int,
        hidden_status_ids: Iterable[int],
        retorno_url: str,
    ) -> dict[str, Any]:
        responsavel = str(filtros.get("responsavel", "meus") or "meus")
        cis, responsavel = DativosDatasetService._load_visible_cis(
            filtros=filtros,
            current_user_id=current_user_id,
            responsavel=responsavel,
        )
        payload = DativosDatasetService._build_ci_queue_payload(
            cis,
            filtros=filtros,
            hidden_status_ids=set(hidden_status_ids),
            retorno_url=retorno_url,
        )

        payload["cis_incompletas_ocultas"] = DativosDatasetService._load_hidden_incomplete_cis(
            filtros=filtros,
            current_user_id=current_user_id,
            responsavel=responsavel,
            visible_incomplete_ids={dativo_ci.id for dativo_ci in payload["cis_incompletas"]},
        )
        payload["responsavel"] = responsavel
        payload["total_ci"] = len(cis)
        return payload

    @staticmethod
    def _load_visible_cis(
        *,
        filtros: dict[str, str | int | bool],
        current_user_id: int,
        responsavel: str,
    ) -> tuple[list[DativoCI], str]:
        exercicio = str(filtros.get("exercicio", "") or "").strip()
        ci = str(filtros.get("ci", "") or "").strip()
        mostrar_encerrados = bool(filtros.get("mostrar_encerrados"))

        query = DativoCI.query.options(
            selectinload(DativoCI.responsavel),
            selectinload(DativoCI.lotes),
            selectinload(DativoCI.itens),
        )

        if exercicio:
            query = query.filter(DativoCI.exercicio == exercicio)

        if ci:
            query = query.filter(DativoCI.processo_edoc.ilike(f"%{ci}%"))

        if responsavel == "meus":
            query = query.filter(DativoCI.responsavel_id == current_user_id)
        elif responsavel and responsavel != "todos":
            try:
                query = query.filter(DativoCI.responsavel_id == int(responsavel))
            except ValueError:
                responsavel = "meus"
                query = query.filter(DativoCI.responsavel_id == current_user_id)

        if not mostrar_encerrados:
            query = query.filter(DativoCI.status == DativosService.STATUS_CI_ABERTA)

        cis = query.order_by(DativoCI.data_ci.desc(), DativoCI.criado_em.desc()).all()
        return cis, responsavel

    @staticmethod
    def _build_ci_queue_payload(
        cis: list[DativoCI],
        *,
        filtros: dict[str, str | int | bool],
        hidden_status_ids: set[int],
        retorno_url: str,
    ) -> dict[str, Any]:
        q = str(filtros.get("q", "") or "").strip()
        ne = str(filtros.get("ne", "") or "").strip()
        grupo = str(filtros.get("grupo", "todos") or "todos").strip() or "todos"
        situacao_rpv_id = str(filtros.get("situacao_rpv_id", "") or "").strip()
        situacao_imposto_id = str(filtros.get("situacao_imposto_id", "") or "").strip()
        mostrar_encerrados = bool(filtros.get("mostrar_encerrados"))

        linhas: list[dict[str, Any]] = []
        cis_incompletas: list[DativoCI] = []
        cis_descartadas: list[DativoCI] = []
        total_lotes = 0
        total_itens_com_irrf = 0

        q_lower = q.lower()
        q_processo = normalizar_numero_processo(q)
        q_doc = normalizar_documento(q)
        ne_lower = ne.lower()
        permitir_cis_incompletas = (
            not ne
            and grupo in ("", "todos")
            and not situacao_rpv_id
            and not situacao_imposto_id
        )

        for dativo_ci in cis:
            if DativosDatasetService._ci_without_movement(dativo_ci):
                combina_cabecalho = (
                    not q
                    or DativosDatasetService._match_search(
                        q_lower,
                        q_processo,
                        dativo_ci.processo_edoc,
                        dativo_ci.descricao,
                        getattr(getattr(dativo_ci, "responsavel", None), "nome", None),
                    )
                )
                if permitir_cis_incompletas and combina_cabecalho:
                    if DativosDatasetService._ci_discarded(dativo_ci):
                        cis_descartadas.append(dativo_ci)
                    else:
                        cis_incompletas.append(dativo_ci)
                continue

            itens_lote_sem_irrf = [
                item for item in dativo_ci.itens
                if item.grupo == "sem_irrf"
            ]
            lote_sem_irrf = next(
                (lote for lote in dativo_ci.lotes if lote.tipo_lote == "sem_irrf"),
                None,
            )
            itens_com_irrf = [
                item for item in dativo_ci.itens
                if item.grupo == "com_irrf"
            ]

            if lote_sem_irrf:
                lote_match = True

                if grupo not in ("", "todos", "lote_sem_irrf"):
                    lote_match = False

                if situacao_rpv_id and str(lote_sem_irrf.situacao_rpv_id) != situacao_rpv_id:
                    lote_match = False
                elif (
                    not mostrar_encerrados
                    and lote_sem_irrf.situacao_rpv_id in hidden_status_ids
                ):
                    lote_match = False

                if situacao_imposto_id and str(lote_sem_irrf.situacao_imposto_id) != situacao_imposto_id:
                    lote_match = False

                busca_ok = DativosDatasetService._match_search(
                    q_lower,
                    q_processo,
                    dativo_ci.processo_edoc,
                    lote_sem_irrf.resumo_operacional,
                    lote_sem_irrf.nota_empenho,
                    lote_sem_irrf.numero_se,
                    lote_sem_irrf.ordem_bancaria,
                    *(item.numero_processo for item in itens_lote_sem_irrf),
                    *(item.nome_beneficiario for item in itens_lote_sem_irrf),
                )
                documento_ok = bool(q_doc) and any(
                    DativosDatasetService._matches_document(q_doc, item.cpf_original)
                    for item in itens_lote_sem_irrf
                )

                if q and not (busca_ok or documento_ok):
                    lote_match = False

                if ne and ne_lower not in str(lote_sem_irrf.nota_empenho or "").lower():
                    lote_match = False

                if lote_match:
                    linhas.append({
                        "tipo": "lote_sem_irrf",
                        "id": lote_sem_irrf.id,
                        "exercicio": dativo_ci.exercicio_formatado,
                        "exercicio_valor": dativo_ci.exercicio,
                        "grupo_label": "Lote sem IRRF",
                        "grupo_ordem": 1,
                        "resumo_operacional": lote_sem_irrf.resumo_operacional,
                        "valor": lote_sem_irrf.valor_total_bruto,
                        "imposto": lote_sem_irrf.valor_total_irrf,
                        "ne": lote_sem_irrf.nota_empenho,
                        "numero_se": getattr(lote_sem_irrf, "numero_se", None),
                        "ob": lote_sem_irrf.ordem_bancaria,
                        "ob_irrf": None,
                        "situacao_rpv": lote_sem_irrf.situacao_rpv,
                        "situacao_imposto": lote_sem_irrf.situacao_imposto,
                        "situacao_rpv_id": lote_sem_irrf.situacao_rpv_id,
                        "situacao_imposto_id": lote_sem_irrf.situacao_imposto_id,
                        "abrir_url": url_for(
                            "dativos.detalhe_lote_sem_irrf",
                            lote_id=lote_sem_irrf.id,
                            retorno=retorno_url,
                        ),
                        "documento_label": "C.I.",
                        "documento_valor": dativo_ci.processo_edoc,
                        "responsavel_nome": (
                            dativo_ci.responsavel.nome
                            if dativo_ci.responsavel
                            else "Nao informado"
                        ),
                        "data_referencia": dativo_ci.data_ci,
                    })
                    total_lotes += 1

            for item in itens_com_irrf:
                item_match = True

                if grupo not in ("", "todos", "item_com_irrf"):
                    item_match = False

                if situacao_rpv_id and str(item.situacao_rpv_id) != situacao_rpv_id:
                    item_match = False
                elif not mostrar_encerrados and item.situacao_rpv_id in hidden_status_ids:
                    item_match = False

                if situacao_imposto_id and str(item.situacao_imposto_id) != situacao_imposto_id:
                    item_match = False

                busca_ok = DativosDatasetService._match_search(
                    q_lower,
                    q_processo,
                    dativo_ci.processo_edoc,
                    item.numero_processo,
                    item.nome_beneficiario,
                    item.resumo_operacional_atual,
                    item.nota_empenho,
                    item.numero_se,
                    item.ordem_bancaria,
                    item.ob_imposto,
                )
                documento_ok = bool(q_doc) and DativosDatasetService._matches_document(
                    q_doc,
                    item.cpf_original,
                )

                if q and not (busca_ok or documento_ok):
                    item_match = False

                if ne and ne_lower not in str(item.nota_empenho or "").lower():
                    item_match = False

                if item_match:
                    linhas.append({
                        "tipo": "item_com_irrf",
                        "id": item.id,
                        "exercicio": dativo_ci.exercicio_formatado,
                        "exercicio_valor": dativo_ci.exercicio,
                        "grupo_label": "Item com IRRF",
                        "grupo_ordem": 2,
                        "resumo_operacional": item.resumo_operacional_atual,
                        "valor": item.valor_bruto,
                        "imposto": item.valor_irrf or Decimal("0.00"),
                        "ne": item.nota_empenho,
                        "numero_se": getattr(item, "numero_se", None),
                        "ob": item.ordem_bancaria,
                        "ob_irrf": item.ob_imposto,
                        "situacao_rpv": item.situacao_rpv,
                        "situacao_imposto": item.situacao_imposto,
                        "situacao_rpv_id": item.situacao_rpv_id,
                        "situacao_imposto_id": item.situacao_imposto_id,
                        "abrir_url": url_for(
                            "dativos.detalhe_item_com_irrf",
                            item_id=item.id,
                            retorno=retorno_url,
                        ),
                        "documento_label": item.tipo_documento_efetivo,
                        "documento_valor": item.documento_formatado,
                        "responsavel_nome": (
                            dativo_ci.responsavel.nome
                            if dativo_ci.responsavel
                            else "Nao informado"
                        ),
                        "data_referencia": dativo_ci.data_ci,
                    })
                    total_itens_com_irrf += 1

        return {
            "linhas": linhas,
            "cis_incompletas": cis_incompletas,
            "cis_descartadas": cis_descartadas,
            "cis_incompletas_ocultas": [],
            "total_lotes": total_lotes,
            "total_itens_com_irrf": total_itens_com_irrf,
        }

    @staticmethod
    def _load_hidden_incomplete_cis(
        *,
        filtros: dict[str, str | int | bool],
        current_user_id: int,
        responsavel: str,
        visible_incomplete_ids: set[int],
    ) -> list[DativoCI]:
        if responsavel == "todos":
            return []

        q = str(filtros.get("q", "") or "").strip()
        exercicio = str(filtros.get("exercicio", "") or "").strip()
        ci = str(filtros.get("ci", "") or "").strip()
        q_lower = q.lower()
        q_processo = normalizar_numero_processo(q)

        query = DativoCI.query.options(
            selectinload(DativoCI.responsavel),
        ).filter(
            DativoCI.criado_por_id == current_user_id,
            DativoCI.status == DativosService.STATUS_CI_ABERTA,
        )

        if exercicio:
            query = query.filter(DativoCI.exercicio == exercicio)

        if ci:
            query = query.filter(DativoCI.processo_edoc.ilike(f"%{ci}%"))

        cis_ocultas: list[DativoCI] = []
        for dativo_ci in query.order_by(DativoCI.data_ci.desc(), DativoCI.criado_em.desc()).all():
            if dativo_ci.id in visible_incomplete_ids:
                continue
            if not DativosDatasetService._ci_without_movement(dativo_ci):
                continue
            if DativosDatasetService._ci_discarded(dativo_ci):
                continue

            combina_cabecalho = (
                not q
                or DativosDatasetService._match_search(
                    q_lower,
                    q_processo,
                    dativo_ci.processo_edoc,
                    dativo_ci.descricao,
                    getattr(getattr(dativo_ci, "responsavel", None), "nome", None),
                )
            )
            if not combina_cabecalho:
                continue

            cis_ocultas.append(dativo_ci)

        return cis_ocultas

    @staticmethod
    def _match_search(texto_busca: str, processo_busca: str, *valores: Any) -> bool:
        if not texto_busca and not processo_busca:
            return True

        for valor in valores:
            texto = str(valor or "").lower()
            if texto_busca and texto_busca in texto:
                return True
            if processo_busca and processo_busca in normalizar_numero_processo(str(valor or "")):
                return True

        return False

    @staticmethod
    def _matches_document(documento_busca: str, documento_valor: str | None) -> bool:
        return documento_busca in normalizar_documento(documento_valor or "")

    @staticmethod
    def _ci_without_movement(dativo_ci: DativoCI) -> bool:
        return not getattr(dativo_ci, "possui_movimentacao_ativa", False)

    @staticmethod
    def _ci_discarded(dativo_ci: DativoCI) -> bool:
        return getattr(dativo_ci, "status_normalizado", "aberta") == DativosService.STATUS_CI_DESCARTADA
