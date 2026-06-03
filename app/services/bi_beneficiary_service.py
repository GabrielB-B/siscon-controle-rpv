from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Callable


class BIBeneficiaryService:
    @staticmethod
    def aggregate_paid_flow(
        dataset: list[dict],
        *,
        paid_rows_loader: Callable[[list[dict]], list[dict]],
        competencia_labeler: Callable[[str], str],
        percent_applicator: Callable[[list[dict]], list[dict]],
        competencias_limite: int = 4,
    ) -> list[dict]:
        agrupado = {}

        for row in paid_rows_loader(dataset):
            chave = row["documento_normalizado"] or row["nome_normalizado"]
            if not chave:
                continue

            dados = agrupado.setdefault(
                chave,
                {
                    "label": row["nome"],
                    "documento": row["documento_limpo"],
                    "quantidade": 0,
                    "valor_total": Decimal("0.00"),
                    "valor_com_irrf": Decimal("0.00"),
                    "valor_sem_irrf": Decimal("0.00"),
                    "valor_irrf_total": Decimal("0.00"),
                    "competencias": defaultdict(
                        lambda: {
                            "valor_total": Decimal("0.00"),
                            "valor_com_irrf": Decimal("0.00"),
                            "valor_sem_irrf": Decimal("0.00"),
                            "valor_irrf": Decimal("0.00"),
                            "quantidade": 0,
                        }
                    ),
                },
            )

            dados["quantidade"] += 1
            dados["valor_total"] += row["valor_pago"]
            if row["tem_irrf"]:
                dados["valor_com_irrf"] += row["valor_pago"]
            else:
                dados["valor_sem_irrf"] += row["valor_pago"]
            dados["valor_irrf_total"] += row["valor_irrf"]

            if row["competencia_pagamento"]:
                competencia = dados["competencias"][row["competencia_pagamento"]]
                competencia["valor_total"] += row["valor_pago"]
                competencia["quantidade"] += 1
                competencia["valor_irrf"] += row["valor_irrf"]
                if row["tem_irrf"]:
                    competencia["valor_com_irrf"] += row["valor_pago"]
                else:
                    competencia["valor_sem_irrf"] += row["valor_pago"]

        serie = list(agrupado.values())
        serie.sort(
            key=lambda item: (item["valor_total"], item["quantidade"], item["label"]),
            reverse=True,
        )

        for item in serie:
            competencias = sorted(item["competencias"].keys(), reverse=True)[:competencias_limite]
            item["competencias"] = [
                {
                    "competencia": competencia,
                    "label": competencia_labeler(competencia),
                    "valor_total": item["competencias"][competencia]["valor_total"],
                    "valor_com_irrf": item["competencias"][competencia]["valor_com_irrf"],
                    "valor_sem_irrf": item["competencias"][competencia]["valor_sem_irrf"],
                    "valor_irrf": item["competencias"][competencia]["valor_irrf"],
                    "quantidade": item["competencias"][competencia]["quantidade"],
                }
                for competencia in competencias
            ]
            item["tem_retencao"] = item["valor_irrf_total"] > 0
            item["observacao_fiscal"] = (
                "Teve imposto retido no recorte e deve aparecer na observacao fiscal."
                if item["tem_retencao"]
                else "Sem imposto retido no recorte atual."
            )

        return percent_applicator(serie)

    @staticmethod
    def filter_flow(
        serie: list[dict],
        busca: str | None,
        *,
        text_normalizer: Callable[[str | None], str],
        document_normalizer: Callable[[str | None], str],
    ) -> list[dict]:
        texto = text_normalizer(busca)
        documento = document_normalizer(busca)
        if not (texto or documento):
            return serie

        filtrados = []
        for item in serie:
            nome = text_normalizer(item.get("label"))
            documento_item = document_normalizer(item.get("documento"))
            if texto and (texto in nome or texto in text_normalizer(item.get("documento"))):
                filtrados.append(item)
                continue
            if documento and documento in documento_item:
                filtrados.append(item)

        return filtrados

    @staticmethod
    def filter_fiscal(serie: list[dict], fiscal: str | None) -> list[dict]:
        if fiscal == "com_retencao":
            return [item for item in serie if item.get("tem_retencao")]
        if fiscal == "sem_retencao":
            return [item for item in serie if not item.get("tem_retencao")]
        return serie

    @staticmethod
    def summary_fiscal(serie: list[dict]) -> dict:
        com_retencao = [item for item in serie if item.get("tem_retencao")]
        sem_retencao = [item for item in serie if not item.get("tem_retencao")]
        return {
            "total": len(serie),
            "com_retencao": len(com_retencao),
            "sem_retencao": len(sem_retencao),
            "valor_total": sum((item["valor_total"] for item in serie), Decimal("0.00")),
            "valor_irrf_total": sum((item["valor_irrf_total"] for item in serie), Decimal("0.00")),
        }

    @classmethod
    def exploration(
        cls,
        serie_completa: list[dict],
        *,
        busca: str | None,
        pagina: int,
        fiscal: str | None = "todos",
        destaque: int,
        pagina_tamanho: int,
        fiscal_options: dict[str, str],
        integer_normalizer: Callable[[Any, int], int],
        text_normalizer: Callable[[str | None], str],
        document_normalizer: Callable[[str | None], str],
    ) -> dict:
        busca_texto = str(busca or "").strip()
        busca_ativa = bool(busca_texto)
        fiscal = fiscal if fiscal in fiscal_options else "todos"
        serie_fiscal = cls.filter_fiscal(serie_completa, fiscal)
        deslocamento_base = 0 if busca_ativa else min(destaque, len(serie_fiscal))
        serie_base = (
            cls.filter_flow(
                serie_fiscal,
                busca_texto,
                text_normalizer=text_normalizer,
                document_normalizer=document_normalizer,
            )
            if busca_ativa
            else serie_fiscal[deslocamento_base:]
        )

        total_resultados = len(serie_base)
        total_paginas = (((total_resultados - 1) // pagina_tamanho) + 1) if total_resultados > 0 else 1
        pagina_atual = min(max(integer_normalizer(pagina, 1), 1), total_paginas)
        inicio_indice = (pagina_atual - 1) * pagina_tamanho
        itens = serie_base[inicio_indice : inicio_indice + pagina_tamanho]

        if itens:
            inicio_ordem = deslocamento_base + inicio_indice + 1
            fim_ordem = inicio_ordem + len(itens) - 1
        else:
            inicio_ordem = 0
            fim_ordem = 0

        return {
            "q": busca_texto,
            "busca_ativa": busca_ativa,
            "fiscal": fiscal,
            "pagina": pagina_atual,
            "pagina_tamanho": pagina_tamanho,
            "total_resultados": total_resultados,
            "total_paginas": total_paginas if total_resultados else 0,
            "itens": itens,
            "tem_itens": bool(itens),
            "tem_anterior": pagina_atual > 1,
            "tem_proxima": (inicio_indice + len(itens)) < total_resultados,
            "inicio_ordem": inicio_ordem,
            "fim_ordem": fim_ordem,
            "restante_apos_destaque": max(len(serie_fiscal) - destaque, 0),
            "total_geral": len(serie_fiscal),
        }
