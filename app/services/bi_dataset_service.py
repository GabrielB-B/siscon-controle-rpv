from __future__ import annotations

from datetime import date
from typing import Any, Callable

from sqlalchemy import func, not_, or_
from sqlalchemy.orm import joinedload

from app.models import DativoCI, DativoItem, RegistroRPV, TipoRPV


class BIDatasetService:
    @staticmethod
    def _grupo_cota_filtro(filtros: dict[str, str] | None) -> str:
        return str((filtros or {}).get("grupo_cota") or "").strip()

    @staticmethod
    def _tipo_filtro(filtros: dict[str, str] | None) -> str:
        return str((filtros or {}).get("tipo") or "").strip()

    @staticmethod
    def _tipos_rpv_condicoes():
        nome_tipo = func.lower(TipoRPV.nome)
        pessoal = or_(nome_tipo.like("%pessoal%"), nome_tipo.like("%trabalhist%"))
        pericial = or_(nome_tipo.like("%pericial%"), nome_tipo.like("%periciais%"))
        comum = not_(or_(pessoal, pericial))
        return {
            "pessoal": pessoal,
            "pericial": pericial,
            "comum": comum,
        }

    @staticmethod
    def query_registros(
        filtros: dict[str, str] | None = None,
        *,
        visao: str = "operacional",
        current_user_id: int | None,
        visao_normalizer: Callable[[str | None], str],
        faixa_data_pagamento_resolver: Callable[[str | None, str | None], tuple[date | None, date | None]],
        filtro_competencia_resolver: Callable[[str | None, str | None], Any],
        pagamento_normalizer: Callable[[str | None], str],
    ):
        query = (
            RegistroRPV.query.options(
                joinedload(RegistroRPV.elaborador),
                joinedload(RegistroRPV.tipo_rpv),
                joinedload(RegistroRPV.situacao_imposto),
                joinedload(RegistroRPV.situacao_empenho),
                joinedload(RegistroRPV.processo),
            )
            .filter(RegistroRPV.ativo.is_(True))
        )

        if not filtros:
            return query

        origem = str(filtros.get("origem") or "").strip()
        if origem not in ("", "todos", "rpv_normal"):
            return query.filter(RegistroRPV.id == -1)

        tipo = BIDatasetService._tipo_filtro(filtros)
        grupo_cota = BIDatasetService._grupo_cota_filtro(filtros)
        joined_tipo = False

        if tipo in {"Dativo com IRRF", "Dativo sem IRRF"}:
            return query.filter(RegistroRPV.id == -1)
        if tipo:
            query = query.join(RegistroRPV.tipo_rpv).filter(TipoRPV.nome == tipo)
            joined_tipo = True

        condicoes_grupo = BIDatasetService._tipos_rpv_condicoes()
        if grupo_cota in condicoes_grupo:
            if not joined_tipo:
                query = query.join(RegistroRPV.tipo_rpv)
                joined_tipo = True
            query = query.filter(condicoes_grupo[grupo_cota])

        responsavel = str(filtros.get("responsavel") or "").strip()
        if responsavel == "meus":
            if current_user_id is None:
                return query.filter(RegistroRPV.id == -1)
            query = query.filter(RegistroRPV.elaborador_id == current_user_id)
        elif responsavel not in ("", "todos"):
            query = query.filter(RegistroRPV.elaborador_id == responsavel)

        pagamento = pagamento_normalizer(filtros.get("pagamento"))
        visao_bi = visao_normalizer(visao)
        if visao_bi == "conferencia" or pagamento == "pagos":
            query = query.filter(RegistroRPV.data_pagamento.isnot(None))
        elif pagamento == "sem_data":
            query = query.filter(RegistroRPV.data_pagamento.is_(None))

        if visao_bi == "conferencia":
            inicio, fim = faixa_data_pagamento_resolver(
                filtros.get("competencia_inicial"),
                filtros.get("competencia_final"),
            )
            if inicio:
                query = query.filter(RegistroRPV.data_pagamento >= inicio)
            if fim:
                query = query.filter(RegistroRPV.data_pagamento < fim)
        else:
            filtro_operacional = filtro_competencia_resolver(
                filtros.get("competencia_inicial"),
                filtros.get("competencia_final"),
                pagamento=pagamento,
            )
            if filtro_operacional is not None:
                query = query.filter(filtro_operacional)

        return query

    @staticmethod
    def query_dativos(
        filtros: dict[str, str] | None = None,
        *,
        visao: str = "operacional",
        current_user_id: int | None,
        visao_normalizer: Callable[[str | None], str],
        faixa_data_pagamento_resolver: Callable[[str | None, str | None], tuple[date | None, date | None]],
        filtro_competencia_resolver: Callable[[str | None, str | None], Any],
        pagamento_normalizer: Callable[[str | None], str],
    ):
        query = (
            DativoItem.query.options(
                joinedload(DativoItem.dativo_ci).joinedload(DativoCI.responsavel),
                joinedload(DativoItem.situacao_rpv),
            )
            .filter(DativoItem.ativo.is_(True))
        )

        if not filtros:
            return query

        origem = str(filtros.get("origem") or "").strip()
        if origem == "rpv_normal":
            return query.filter(DativoItem.id == -1)
        if origem == "dativo_com_irrf":
            query = query.filter(DativoItem.grupo == "com_irrf")
        elif origem == "dativo_sem_irrf":
            query = query.filter(DativoItem.grupo == "sem_irrf")

        tipo = BIDatasetService._tipo_filtro(filtros)
        grupo_cota = BIDatasetService._grupo_cota_filtro(filtros)

        if grupo_cota in {"pessoal", "pericial"}:
            return query.filter(DativoItem.id == -1)

        if tipo == "Dativo com IRRF":
            query = query.filter(DativoItem.grupo == "com_irrf")
        elif tipo == "Dativo sem IRRF":
            query = query.filter(DativoItem.grupo == "sem_irrf")
        elif tipo:
            return query.filter(DativoItem.id == -1)

        responsavel = str(filtros.get("responsavel") or "").strip()
        if responsavel == "meus":
            if current_user_id is None:
                return query.filter(DativoItem.id == -1)
            query = query.join(DativoItem.dativo_ci).filter(DativoCI.responsavel_id == current_user_id)
        elif responsavel not in ("", "todos"):
            query = query.join(DativoItem.dativo_ci).filter(DativoCI.responsavel_id == responsavel)

        pagamento = pagamento_normalizer(filtros.get("pagamento"))
        visao_bi = visao_normalizer(visao)
        if visao_bi == "conferencia" or pagamento == "pagos":
            query = query.filter(DativoItem.data_pagamento.isnot(None))
        elif pagamento == "sem_data":
            query = query.filter(DativoItem.data_pagamento.is_(None))

        if visao_bi == "conferencia":
            inicio, fim = faixa_data_pagamento_resolver(
                filtros.get("competencia_inicial"),
                filtros.get("competencia_final"),
            )
            if inicio:
                query = query.filter(DativoItem.data_pagamento >= inicio)
            if fim:
                query = query.filter(DativoItem.data_pagamento < fim)
        else:
            filtro_operacional = filtro_competencia_resolver(
                filtros.get("competencia_inicial"),
                filtros.get("competencia_final"),
                pagamento=pagamento,
            )
            if filtro_operacional is not None:
                query = query.filter(filtro_operacional)

        return query

    @classmethod
    def collect_dataset(
        cls,
        filtros: dict[str, str] | None = None,
        *,
        visao: str = "operacional",
        ordenar: bool = False,
        current_user_id: int | None,
        visao_normalizer: Callable[[str | None], str],
        faixa_data_pagamento_resolver: Callable[[str | None, str | None], tuple[date | None, date | None]],
        filtro_competencia_rpv_resolver: Callable[[str | None, str | None], Any],
        filtro_competencia_dativo_resolver: Callable[[str | None, str | None], Any],
        pagamento_normalizer: Callable[[str | None], str],
        map_rpv: Callable[[Any], dict],
        map_dativo: Callable[[Any], dict],
    ) -> list[dict]:
        registros = cls.query_registros(
            filtros,
            visao=visao,
            current_user_id=current_user_id,
            visao_normalizer=visao_normalizer,
            faixa_data_pagamento_resolver=faixa_data_pagamento_resolver,
            filtro_competencia_resolver=filtro_competencia_rpv_resolver,
            pagamento_normalizer=pagamento_normalizer,
        ).all()
        dativo_items = cls.query_dativos(
            filtros,
            visao=visao,
            current_user_id=current_user_id,
            visao_normalizer=visao_normalizer,
            faixa_data_pagamento_resolver=faixa_data_pagamento_resolver,
            filtro_competencia_resolver=filtro_competencia_dativo_resolver,
            pagamento_normalizer=pagamento_normalizer,
        ).all()

        dataset = [
            map_rpv(registro)
            for registro in registros
            if not getattr(registro, "status_principal_cancelado", False)
        ]
        dataset.extend(
            map_dativo(item)
            for item in dativo_items
            if not getattr(item, "status_principal_cancelado", False)
        )

        if ordenar:
            dataset.sort(
                key=lambda row: (
                    row["competencia"] or "",
                    row["data_pagamento"] or date.min,
                    row["valor_bruto"],
                    row["nome"],
                ),
                reverse=True,
            )

        return dataset

    @staticmethod
    def filter_dataset_in_memory(
        dataset: list[dict],
        *,
        texto: str,
        grupo_cota: str,
        tipo: str,
        reinf: str,
        reinf_matcher: Callable[[str, str], bool],
        text_normalizer: Callable[[str | None], str],
    ) -> list[dict]:
        filtrados = []

        for row in dataset:
            if grupo_cota not in ("", "todos") and row["grupo_cota"] != grupo_cota:
                continue

            if tipo and row["tipo"] != tipo:
                continue

            if not reinf_matcher(row["reinf_status"], reinf):
                continue

            if texto:
                valores_busca = (
                    row["nome_normalizado"],
                    row["documento_normalizado"],
                    text_normalizer(row["processo"]),
                    text_normalizer(row["ci"]),
                    text_normalizer(row["tipo"]),
                )
                if not any(texto in valor for valor in valores_busca):
                    continue

            filtrados.append(row)

        return filtrados

    @classmethod
    def filter_dataset(
        cls,
        dataset: list[dict],
        filtros: dict[str, str],
        *,
        memory_filters: dict[str, str],
        reinf_matcher: Callable[[str, str], bool],
        text_normalizer: Callable[[str | None], str],
    ) -> list[dict]:
        return cls.filter_dataset_in_memory(
            dataset,
            texto=memory_filters["texto"],
            grupo_cota=memory_filters["grupo_cota"],
            tipo=memory_filters["tipo"],
            reinf=memory_filters["reinf"],
            reinf_matcher=reinf_matcher,
            text_normalizer=text_normalizer,
        )

    @classmethod
    def load_filtered_dataset(
        cls,
        filtros: dict[str, str] | None = None,
        *,
        visao: str = "operacional",
        ordenar: bool = False,
        current_user_id: int | None,
        visao_normalizer: Callable[[str | None], str],
        faixa_data_pagamento_resolver: Callable[[str | None, str | None], tuple[date | None, date | None]],
        filtro_competencia_rpv_resolver: Callable[[str | None, str | None], Any],
        filtro_competencia_dativo_resolver: Callable[[str | None, str | None], Any],
        pagamento_normalizer: Callable[[str | None], str],
        map_rpv: Callable[[Any], dict],
        map_dativo: Callable[[Any], dict],
        memory_filters: dict[str, str],
        reinf_matcher: Callable[[str, str], bool],
        text_normalizer: Callable[[str | None], str],
    ) -> tuple[list[dict], list[dict]]:
        dataset = cls.collect_dataset(
            filtros,
            visao=visao,
            ordenar=ordenar,
            current_user_id=current_user_id,
            visao_normalizer=visao_normalizer,
            faixa_data_pagamento_resolver=faixa_data_pagamento_resolver,
            filtro_competencia_rpv_resolver=filtro_competencia_rpv_resolver,
            filtro_competencia_dativo_resolver=filtro_competencia_dativo_resolver,
            pagamento_normalizer=pagamento_normalizer,
            map_rpv=map_rpv,
            map_dativo=map_dativo,
        )
        dataset_filtrado = cls.filter_dataset(
            dataset,
            filtros or {},
            memory_filters=memory_filters,
            reinf_matcher=reinf_matcher,
            text_normalizer=text_normalizer,
        )
        return dataset, dataset_filtrado
