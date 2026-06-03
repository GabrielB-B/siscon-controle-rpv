from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.services.cotas_rpv_service import CotasRPVService
from app.utils.cota_groups import GRUPOS_COTA_META, GRUPOS_COTA_OPCOES, GRUPOS_COTA_ORDEM
from app.utils.domain_profile import get_domain_profile


LABEL_SEM_IRRF = get_domain_profile().situacao_imposto_sem_irrf_nome


class BIOperationalMetricsService:
    @staticmethod
    def _decimal(valor) -> Decimal:
        try:
            return Decimal(valor or 0)
        except Exception:
            return Decimal("0.00")

    @staticmethod
    def _aplicar_percentuais(items: list[dict], campo_valor: str = "valor_total") -> list[dict]:
        maior_valor = max((item[campo_valor] for item in items), default=Decimal("0.00"))

        for item in items:
            if maior_valor > 0:
                item["percentual"] = float((item[campo_valor] / maior_valor) * Decimal("100"))
            else:
                item["percentual"] = 0.0

        return items

    @staticmethod
    def _meta_grupo_cota(chave: str) -> dict:
        return GRUPOS_COTA_META.get(chave, GRUPOS_COTA_META["comum"])

    @staticmethod
    def _grupos_cota_visiveis(filtros: dict[str, str] | None = None) -> tuple[str, ...]:
        if not filtros:
            return GRUPOS_COTA_ORDEM

        grupo_cota = str(filtros.get("grupo_cota") or "").strip()
        if grupo_cota in GRUPOS_COTA_OPCOES and grupo_cota != "todos":
            return (grupo_cota,)
        return GRUPOS_COTA_ORDEM

    @staticmethod
    def _filtros_bi_tem_competencia_explicita(filtros: dict[str, str] | None) -> bool:
        if not filtros:
            return False

        return bool(
            CotasRPVService.normalizar_competencia(filtros.get("competencia_inicial"))
            or CotasRPVService.normalizar_competencia(filtros.get("competencia_final"))
        )

    @classmethod
    def _deslocar_competencia(cls, competencia: str | None, deslocamento: int) -> str:
        valor = CotasRPVService.normalizar_competencia(competencia)
        if not valor:
            return ""

        ano, mes = valor.split("-", 1)
        indice = (int(ano) * 12) + (int(mes) - 1) + deslocamento
        if indice < 0:
            return ""

        novo_ano = indice // 12
        novo_mes = (indice % 12) + 1
        return f"{novo_ano:04d}-{novo_mes:02d}"

    @classmethod
    def _janela_competencias(cls, competencia_referencia: str | None, quantidade: int) -> list[str]:
        referencia = CotasRPVService.normalizar_competencia(competencia_referencia) or CotasRPVService.competencia_atual()
        quantidade_normalizada = max(int(quantidade or 6), 1)
        return [
            cls._deslocar_competencia(referencia, deslocamento)
            for deslocamento in range(-(quantidade_normalizada - 1), 1)
        ]

    @staticmethod
    def _competencias_disponiveis(dataset: list[dict]) -> list[str]:
        return sorted({row["competencia"] for row in dataset if row["competencia"]})

    @staticmethod
    def _competencias_pagamento_disponiveis(dataset: list[dict]) -> list[str]:
        return sorted({row["competencia_pagamento"] for row in dataset if row["competencia_pagamento"]})

    @staticmethod
    def _linhas_bi_pagas(dataset: list[dict]) -> list[dict]:
        return [row for row in dataset if row["valor_pago"] > 0 and row["competencia_pagamento"]]

    @staticmethod
    def _linhas_bi_em_aberto(dataset: list[dict]) -> list[dict]:
        return [row for row in dataset if row["valor_previsto_aberto"] > 0]

    @staticmethod
    def _linhas_dativos_pagos(dataset: list[dict]) -> list[dict]:
        return [
            row
            for row in dataset
            if row["origem_chave"] in {"dativo_com_irrf", "dativo_sem_irrf"} and row["data_pagamento"]
        ]

    @classmethod
    def competencia_referencia_bi(
        cls,
        dataset: list[dict],
        filtros: dict[str, str] | None = None,
    ) -> str:
        competencia_atual = CotasRPVService.competencia_atual()
        competencias_disponiveis = cls._competencias_disponiveis(dataset)
        competencias_pagamento = cls._competencias_pagamento_disponiveis(dataset)
        ultima_competencia_disponivel = (
            competencias_pagamento[-1]
            if competencias_pagamento
            else (competencias_disponiveis[-1] if competencias_disponiveis else "")
        )

        if cls._filtros_bi_tem_competencia_explicita(filtros):
            return ultima_competencia_disponivel or competencia_atual

        if not ultima_competencia_disponivel:
            return competencia_atual

        return (
            competencia_atual
            if competencia_atual >= ultima_competencia_disponivel
            else ultima_competencia_disponivel
        )

    @classmethod
    def competencia_referencia_bi_projetada(
        cls,
        projecao: dict,
        filtros: dict[str, str] | None = None,
    ) -> str:
        competencia_atual = CotasRPVService.competencia_atual()
        competencias_disponiveis = sorted(projecao.get("competencias_disponiveis", []))
        competencias_pagamento = sorted(projecao.get("competencias_pagas", []))
        ultima_competencia_disponivel = (
            competencias_pagamento[-1]
            if competencias_pagamento
            else (competencias_disponiveis[-1] if competencias_disponiveis else "")
        )

        if cls._filtros_bi_tem_competencia_explicita(filtros):
            return ultima_competencia_disponivel or competencia_atual

        if not ultima_competencia_disponivel:
            return competencia_atual

        return (
            competencia_atual
            if competencia_atual >= ultima_competencia_disponivel
            else ultima_competencia_disponivel
        )

    @classmethod
    def resumo_dativos_competencia(
        cls,
        dataset: list[dict],
        competencia_referencia: str | None = None,
    ) -> dict:
        competencia_referencia = (
            CotasRPVService.normalizar_competencia(competencia_referencia)
            or CotasRPVService.competencia_atual()
        )
        linhas = [
            row for row in cls._linhas_dativos_pagos(dataset) if row["competencia"] == competencia_referencia
        ]

        grupos_base = [
            ("dativo_sem_irrf", "Dativos sem IRRF", "stack-segment-soft"),
            ("dativo_com_irrf", "Dativos com IRRF", "stack-segment-strong"),
        ]
        total_valor = sum((row["valor_bruto"] for row in linhas), Decimal("0.00"))
        total_quantidade = len(linhas)
        grupos = []

        for chave, label, css_class in grupos_base:
            linhas_grupo = [row for row in linhas if row["origem_chave"] == chave]
            valor_total = sum((row["valor_bruto"] for row in linhas_grupo), Decimal("0.00"))
            quantidade = len(linhas_grupo)
            percentual = float((valor_total / total_valor) * Decimal("100")) if total_valor > 0 else 0.0
            grupos.append(
                {
                    "label": label,
                    "css_class": css_class,
                    "valor_total": valor_total,
                    "quantidade": quantidade,
                    "percentual": percentual,
                }
            )

        return {
            "competencia": competencia_referencia,
            "competencia_legivel": CotasRPVService.competencia_legivel(competencia_referencia),
            "total_valor": total_valor,
            "total_quantidade": total_quantidade,
            "grupos": grupos,
        }

    @classmethod
    def resumo_dativos_competencia_projetado(
        cls,
        projecao: dict,
        competencia_referencia: str | None = None,
    ) -> dict:
        competencia_referencia = (
            CotasRPVService.normalizar_competencia(competencia_referencia)
            or CotasRPVService.competencia_atual()
        )
        dados_competencia = projecao.get("historico_dativos_pagos", {}).get(
            competencia_referencia,
            {
                "dativo_sem_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
                "dativo_com_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
            },
        )

        grupos_base = [
            ("dativo_sem_irrf", "Dativos sem IRRF", "stack-segment-soft"),
            ("dativo_com_irrf", "Dativos com IRRF", "stack-segment-strong"),
        ]
        total_valor = sum(
            (dados_competencia[chave]["valor_total"] for chave, _, _ in grupos_base),
            Decimal("0.00"),
        )
        total_quantidade = sum(
            (dados_competencia[chave]["quantidade"] for chave, _, _ in grupos_base),
            0,
        )
        grupos = []

        for chave, label, css_class in grupos_base:
            valor_total = dados_competencia[chave]["valor_total"]
            quantidade = dados_competencia[chave]["quantidade"]
            percentual = float((valor_total / total_valor) * Decimal("100")) if total_valor > 0 else 0.0
            grupos.append(
                {
                    "label": label,
                    "css_class": css_class,
                    "valor_total": valor_total,
                    "quantidade": quantidade,
                    "percentual": percentual,
                }
            )

        return {
            "competencia": competencia_referencia,
            "competencia_legivel": CotasRPVService.competencia_legivel(competencia_referencia),
            "total_valor": total_valor,
            "total_quantidade": total_quantidade,
            "grupos": grupos,
        }

    @classmethod
    def serie_dativos_ultimas_competencias(cls, dataset: list[dict], limite: int = 6) -> list[dict]:
        agrupado = defaultdict(
            lambda: {
                "dativo_sem_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
                "dativo_com_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
            }
        )

        for row in cls._linhas_dativos_pagos(dataset):
            competencia = row["competencia"] or (
                row["data_pagamento"].strftime("%Y-%m") if row["data_pagamento"] else ""
            )
            if not competencia:
                continue
            grupo = agrupado[competencia][row["origem_chave"]]
            grupo["valor_total"] += row["valor_bruto"]
            grupo["quantidade"] += 1

        competencias = sorted(agrupado.keys())[-limite:]
        totais = []

        for competencia in competencias:
            dados_competencia = agrupado[competencia]
            valor_sem_irrf = dados_competencia["dativo_sem_irrf"]["valor_total"]
            valor_com_irrf = dados_competencia["dativo_com_irrf"]["valor_total"]
            quantidade_sem_irrf = dados_competencia["dativo_sem_irrf"]["quantidade"]
            quantidade_com_irrf = dados_competencia["dativo_com_irrf"]["quantidade"]
            valor_total = valor_sem_irrf + valor_com_irrf
            quantidade_total = quantidade_sem_irrf + quantidade_com_irrf

            totais.append(
                {
                    "competencia": competencia,
                    "label": CotasRPVService.competencia_legivel(competencia),
                    "valor_total": valor_total,
                    "quantidade_total": quantidade_total,
                    "segmentos": [
                        {
                            "label": LABEL_SEM_IRRF,
                            "css_class": "stack-segment-soft",
                            "valor_total": valor_sem_irrf,
                            "quantidade": quantidade_sem_irrf,
                        },
                        {
                            "label": "Com IRRF",
                            "css_class": "stack-segment-strong",
                            "valor_total": valor_com_irrf,
                            "quantidade": quantidade_com_irrf,
                        },
                    ],
                }
            )

        maior_total = max((item["valor_total"] for item in totais), default=Decimal("0.00"))

        for item in totais:
            item["altura_percentual"] = (
                float((item["valor_total"] / maior_total) * Decimal("100")) if maior_total > 0 else 0.0
            )
            for segmento in item["segmentos"]:
                segmento["percentual_interno"] = (
                    float((segmento["valor_total"] / item["valor_total"]) * Decimal("100"))
                    if item["valor_total"] > 0
                    else 0.0
                )

        return totais

    @classmethod
    def serie_dativos_ultimas_competencias_projetada(cls, projecao: dict, limite: int = 6) -> list[dict]:
        historico_dativos = projecao.get("historico_dativos_pagos", {})
        competencias = sorted(historico_dativos.keys())[-limite:]
        totais = []

        for competencia in competencias:
            dados_competencia = historico_dativos.get(
                competencia,
                {
                    "dativo_sem_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
                    "dativo_com_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
                },
            )
            valor_sem_irrf = dados_competencia["dativo_sem_irrf"]["valor_total"]
            valor_com_irrf = dados_competencia["dativo_com_irrf"]["valor_total"]
            quantidade_sem_irrf = dados_competencia["dativo_sem_irrf"]["quantidade"]
            quantidade_com_irrf = dados_competencia["dativo_com_irrf"]["quantidade"]
            valor_total = valor_sem_irrf + valor_com_irrf
            quantidade_total = quantidade_sem_irrf + quantidade_com_irrf

            totais.append(
                {
                    "competencia": competencia,
                    "label": CotasRPVService.competencia_legivel(competencia),
                    "valor_total": valor_total,
                    "quantidade_total": quantidade_total,
                    "segmentos": [
                        {
                            "label": LABEL_SEM_IRRF,
                            "css_class": "stack-segment-soft",
                            "valor_total": valor_sem_irrf,
                            "quantidade": quantidade_sem_irrf,
                        },
                        {
                            "label": "Com IRRF",
                            "css_class": "stack-segment-strong",
                            "valor_total": valor_com_irrf,
                            "quantidade": quantidade_com_irrf,
                        },
                    ],
                }
            )

        maior_total = max((item["valor_total"] for item in totais), default=Decimal("0.00"))

        for item in totais:
            item["altura_percentual"] = (
                float((item["valor_total"] / maior_total) * Decimal("100")) if maior_total > 0 else 0.0
            )
            for segmento in item["segmentos"]:
                segmento["percentual_interno"] = (
                    float((segmento["valor_total"] / item["valor_total"]) * Decimal("100"))
                    if item["valor_total"] > 0
                    else 0.0
                )

        return totais

    @classmethod
    def resumo_grupos_cota(
        cls,
        dataset: list[dict],
        filtros: dict[str, str] | None = None,
    ) -> dict:
        linhas_pagas = cls._linhas_bi_pagas(dataset)
        linhas_em_aberto = cls._linhas_bi_em_aberto(dataset)
        competencias = cls._competencias_pagamento_disponiveis(dataset)
        competencia_referencia = cls.competencia_referencia_bi(dataset, filtros)
        proxima_competencia = CotasRPVService.proxima_competencia(competencia_referencia)
        ano_referencia = (competencia_referencia or CotasRPVService.competencia_atual())[:4]
        historico_pago = defaultdict(
            lambda: {
                chave: {"valor_total": Decimal("0.00"), "quantidade": 0}
                for chave in GRUPOS_COTA_ORDEM
            }
        )
        historico_aberto = defaultdict(
            lambda: {
                chave: {"valor_total": Decimal("0.00"), "quantidade": 0}
                for chave in GRUPOS_COTA_ORDEM
            }
        )

        for row in linhas_pagas:
            competencia = row["competencia_pagamento"]
            if not competencia:
                continue
            grupo = historico_pago[competencia][row["grupo_cota"]]
            grupo["valor_total"] += row["valor_pago"]
            grupo["quantidade"] += 1

        for row in linhas_em_aberto:
            competencia = row["competencia_cadastro"]
            if not competencia:
                continue
            grupo = historico_aberto[competencia][row["grupo_cota"]]
            grupo["valor_total"] += row["valor_previsto_aberto"]
            grupo["quantidade"] += 1

        janela_previsao = competencias[-3:] if competencias else []
        grupos = []
        total_mes_pago = Decimal("0.00")
        total_ano_pago = Decimal("0.00")
        total_em_aberto = Decimal("0.00")
        total_previsao = Decimal("0.00")

        for chave in GRUPOS_COTA_ORDEM:
            meta = cls._meta_grupo_cota(chave)
            valor_mes_pago = historico_pago[competencia_referencia][chave]["valor_total"]
            quantidade_mes_pago = historico_pago[competencia_referencia][chave]["quantidade"]
            valor_ano_pago = sum(
                dados[chave]["valor_total"]
                for competencia, dados in historico_pago.items()
                if competencia.startswith(ano_referencia)
            )
            quantidade_ano_pago = sum(
                dados[chave]["quantidade"]
                for competencia, dados in historico_pago.items()
                if competencia.startswith(ano_referencia)
            )
            valor_em_aberto = sum(
                dados[chave]["valor_total"] for dados in historico_aberto.values()
            )
            quantidade_em_aberto = sum(
                dados[chave]["quantidade"] for dados in historico_aberto.values()
            )

            if janela_previsao:
                valor_previsao = sum(
                    (
                        historico_pago[competencia][chave]["valor_total"]
                        for competencia in janela_previsao
                    ),
                    Decimal("0.00"),
                ) / Decimal(len(janela_previsao))
                valor_previsao = valor_previsao.quantize(Decimal("0.01"))
            else:
                valor_previsao = Decimal("0.00")

            total_mes_pago += valor_mes_pago
            total_ano_pago += valor_ano_pago
            total_em_aberto += valor_em_aberto
            total_previsao += valor_previsao

            grupos.append(
                {
                    "chave": chave,
                    "label": meta["label"],
                    "descricao": meta["descricao"],
                    "css_class": meta["css_class"],
                    "chart_class": meta["chart_class"],
                    "progress_class": meta["progress_class"],
                    "valor_mes_pago": valor_mes_pago,
                    "quantidade_mes_pago": quantidade_mes_pago,
                    "valor_ano_pago": valor_ano_pago,
                    "quantidade_ano_pago": quantidade_ano_pago,
                    "valor_em_aberto": valor_em_aberto,
                    "quantidade_em_aberto": quantidade_em_aberto,
                    "valor_previsao": valor_previsao,
                    "percentual_pago_mes": 0.0,
                }
            )

        for grupo in grupos:
            grupo["percentual_pago_mes"] = (
                float((grupo["valor_mes_pago"] / total_mes_pago) * Decimal("100"))
                if total_mes_pago > 0
                else 0.0
            )

        return {
            "competencia_referencia": competencia_referencia,
            "competencia_legivel": CotasRPVService.competencia_legivel(competencia_referencia),
            "proxima_competencia": proxima_competencia,
            "proxima_competencia_legivel": (
                CotasRPVService.competencia_legivel(proxima_competencia) if proxima_competencia else "proximo mes"
            ),
            "ano_referencia": ano_referencia,
            "total_mes_pago": total_mes_pago,
            "total_ano_pago": total_ano_pago,
            "total_em_aberto": total_em_aberto,
            "total_previsao": total_previsao,
            "grupos": grupos,
        }

    @classmethod
    def resumo_grupos_cota_projetado(
        cls,
        projecao: dict,
        filtros: dict[str, str] | None = None,
    ) -> dict:
        historico_pago = projecao.get("historico_pago", {})
        historico_aberto = projecao.get("historico_aberto", {})
        competencias = sorted(projecao.get("competencias_pagas", []))
        competencia_referencia = cls.competencia_referencia_bi_projetada(projecao, filtros)
        proxima_competencia = CotasRPVService.proxima_competencia(competencia_referencia)
        ano_referencia = (competencia_referencia or CotasRPVService.competencia_atual())[:4]
        janela_previsao = competencias[-3:] if competencias else []
        grupos = []
        total_mes_pago = Decimal("0.00")
        total_ano_pago = Decimal("0.00")
        total_em_aberto = Decimal("0.00")
        total_previsao = Decimal("0.00")

        for chave in GRUPOS_COTA_ORDEM:
            meta = cls._meta_grupo_cota(chave)
            valor_mes_pago = historico_pago.get(competencia_referencia, {}).get(
                chave,
                {"valor_total": Decimal("0.00")},
            )["valor_total"]
            quantidade_mes_pago = historico_pago.get(competencia_referencia, {}).get(
                chave,
                {"quantidade": 0},
            )["quantidade"]
            valor_ano_pago = sum(
                dados[chave]["valor_total"]
                for competencia, dados in historico_pago.items()
                if competencia.startswith(ano_referencia)
            )
            quantidade_ano_pago = sum(
                dados[chave]["quantidade"]
                for competencia, dados in historico_pago.items()
                if competencia.startswith(ano_referencia)
            )
            valor_em_aberto = sum(
                dados[chave]["valor_total"] for dados in historico_aberto.values()
            )
            quantidade_em_aberto = sum(
                dados[chave]["quantidade"] for dados in historico_aberto.values()
            )

            if janela_previsao:
                valor_previsao = sum(
                    (historico_pago[competencia][chave]["valor_total"] for competencia in janela_previsao),
                    Decimal("0.00"),
                ) / Decimal(len(janela_previsao))
                valor_previsao = valor_previsao.quantize(Decimal("0.01"))
            else:
                valor_previsao = Decimal("0.00")

            total_mes_pago += valor_mes_pago
            total_ano_pago += valor_ano_pago
            total_em_aberto += valor_em_aberto
            total_previsao += valor_previsao

            grupos.append(
                {
                    "chave": chave,
                    "label": meta["label"],
                    "descricao": meta["descricao"],
                    "css_class": meta["css_class"],
                    "chart_class": meta["chart_class"],
                    "progress_class": meta["progress_class"],
                    "valor_mes_pago": valor_mes_pago,
                    "quantidade_mes_pago": quantidade_mes_pago,
                    "valor_ano_pago": valor_ano_pago,
                    "quantidade_ano_pago": quantidade_ano_pago,
                    "valor_em_aberto": valor_em_aberto,
                    "quantidade_em_aberto": quantidade_em_aberto,
                    "valor_previsao": valor_previsao,
                    "percentual_pago_mes": 0.0,
                }
            )

        for grupo in grupos:
            grupo["percentual_pago_mes"] = (
                float((grupo["valor_mes_pago"] / total_mes_pago) * Decimal("100"))
                if total_mes_pago > 0
                else 0.0
            )

        return {
            "competencia_referencia": competencia_referencia,
            "competencia_legivel": CotasRPVService.competencia_legivel(competencia_referencia),
            "proxima_competencia": proxima_competencia,
            "proxima_competencia_legivel": (
                CotasRPVService.competencia_legivel(proxima_competencia) if proxima_competencia else "proximo mes"
            ),
            "ano_referencia": ano_referencia,
            "total_mes_pago": total_mes_pago,
            "total_ano_pago": total_ano_pago,
            "total_em_aberto": total_em_aberto,
            "total_previsao": total_previsao,
            "grupos": grupos,
        }

    @classmethod
    def series_grupos_cota(
        cls,
        dataset: list[dict],
        resumo_grupos: dict,
        *,
        janela_meses: int = 6,
        filtros: dict[str, str] | None = None,
    ) -> list[dict]:
        competencias = cls._janela_competencias(
            resumo_grupos.get("competencia_referencia"),
            janela_meses,
        )
        linhas_pagas = cls._linhas_bi_pagas(dataset)
        grupos_resumo = {grupo["chave"]: grupo for grupo in resumo_grupos.get("grupos", [])}
        grupos = []

        for chave in cls._grupos_cota_visiveis(filtros):
            meta = cls._meta_grupo_cota(chave)
            resumo_grupo = grupos_resumo.get(chave, {})
            serie = []
            maior_valor = Decimal("0.00")
            valor_total_janela = Decimal("0.00")
            quantidade_total_janela = 0

            for competencia in competencias:
                linhas_competencia = [
                    row
                    for row in linhas_pagas
                    if row["grupo_cota"] == chave and row["competencia_pagamento"] == competencia
                ]
                valor_total = sum((row["valor_pago"] for row in linhas_competencia), Decimal("0.00"))
                quantidade = len(linhas_competencia)
                valor_total_janela += valor_total
                quantidade_total_janela += quantidade
                maior_valor = max(maior_valor, valor_total)
                serie.append(
                    {
                        "competencia": competencia,
                        "label": CotasRPVService.competencia_legivel(competencia),
                        "valor_total": valor_total,
                        "quantidade": quantidade,
                        "percentual": 0.0,
                    }
                )

            for item in serie:
                item["percentual"] = (
                    float((item["valor_total"] / maior_valor) * Decimal("100"))
                    if maior_valor > 0
                    else 0.0
                )

            media_mensal = (
                (valor_total_janela / Decimal(len(competencias))).quantize(Decimal("0.01"))
                if competencias
                else Decimal("0.00")
            )
            melhor_mes = (
                max(serie, key=lambda item: item["valor_total"], default=None)
                if valor_total_janela > 0
                else None
            )

            grupos.append(
                {
                    "chave": chave,
                    "label": meta["label"],
                    "descricao": meta["descricao"],
                    "css_class": meta["css_class"],
                    "chart_class": meta["chart_class"],
                    "progress_class": meta["progress_class"],
                    "serie": serie,
                    "valor_total_janela": valor_total_janela,
                    "quantidade_total_janela": quantidade_total_janela,
                    "media_mensal": media_mensal,
                    "valor_mes_pago": resumo_grupo.get("valor_mes_pago", Decimal("0.00")),
                    "valor_ano_pago": resumo_grupo.get("valor_ano_pago", Decimal("0.00")),
                    "valor_em_aberto": resumo_grupo.get("valor_em_aberto", Decimal("0.00")),
                    "percentual_pago_mes": resumo_grupo.get("percentual_pago_mes", 0.0),
                    "valor_previsao": resumo_grupo.get("valor_previsao", Decimal("0.00")),
                    "tem_dados": valor_total_janela > 0,
                    "melhor_mes_label": melhor_mes["label"] if melhor_mes else "-",
                    "melhor_mes_valor": melhor_mes["valor_total"] if melhor_mes else Decimal("0.00"),
                }
            )

        return grupos

    @classmethod
    def series_grupos_cota_projetado(
        cls,
        projecao: dict,
        resumo_grupos: dict,
        *,
        janela_meses: int = 6,
        filtros: dict[str, str] | None = None,
    ) -> list[dict]:
        competencias = cls._janela_competencias(
            resumo_grupos.get("competencia_referencia"),
            janela_meses,
        )
        historico_pago = projecao.get("historico_pago", {})
        grupos_resumo = {grupo["chave"]: grupo for grupo in resumo_grupos.get("grupos", [])}
        grupos = []

        for chave in cls._grupos_cota_visiveis(filtros):
            meta = cls._meta_grupo_cota(chave)
            resumo_grupo = grupos_resumo.get(chave, {})
            serie = []
            maior_valor = Decimal("0.00")
            valor_total_janela = Decimal("0.00")
            quantidade_total_janela = 0

            for competencia in competencias:
                dados = historico_pago.get(competencia, {}).get(
                    chave,
                    {"valor_total": Decimal("0.00"), "quantidade": 0},
                )
                valor_total = dados["valor_total"]
                quantidade = dados["quantidade"]
                valor_total_janela += valor_total
                quantidade_total_janela += quantidade
                maior_valor = max(maior_valor, valor_total)
                serie.append(
                    {
                        "competencia": competencia,
                        "label": CotasRPVService.competencia_legivel(competencia),
                        "valor_total": valor_total,
                        "quantidade": quantidade,
                        "percentual": 0.0,
                    }
                )
            for item in serie:
                item["percentual"] = (
                    float((item["valor_total"] / maior_valor) * Decimal("100"))
                    if maior_valor > 0
                    else 0.0
                )

            media_mensal = (
                (valor_total_janela / Decimal(len(competencias))).quantize(Decimal("0.01"))
                if competencias
                else Decimal("0.00")
            )
            melhor_mes = (
                max(serie, key=lambda item: item["valor_total"], default=None)
                if valor_total_janela > 0
                else None
            )

            grupos.append(
                {
                    "chave": chave,
                    "label": meta["label"],
                    "descricao": meta["descricao"],
                    "css_class": meta["css_class"],
                    "chart_class": meta["chart_class"],
                    "progress_class": meta["progress_class"],
                    "serie": serie,
                    "valor_total_janela": valor_total_janela,
                    "quantidade_total_janela": quantidade_total_janela,
                    "media_mensal": media_mensal,
                    "valor_mes_pago": resumo_grupo.get("valor_mes_pago", Decimal("0.00")),
                    "valor_ano_pago": resumo_grupo.get("valor_ano_pago", Decimal("0.00")),
                    "valor_em_aberto": resumo_grupo.get("valor_em_aberto", Decimal("0.00")),
                    "percentual_pago_mes": resumo_grupo.get("percentual_pago_mes", 0.0),
                    "valor_previsao": resumo_grupo.get("valor_previsao", Decimal("0.00")),
                    "tem_dados": valor_total_janela > 0,
                    "melhor_mes_label": melhor_mes["label"] if melhor_mes else "-",
                    "melhor_mes_valor": melhor_mes["valor_total"] if melhor_mes else Decimal("0.00"),
                }
            )

        return grupos

    @classmethod
    def serie_mensal_grupos_cota(cls, dataset: list[dict], limite: int = 12) -> list[dict]:
        agrupado = defaultdict(
            lambda: {
                chave: {"valor_total": Decimal("0.00"), "quantidade": 0}
                for chave in GRUPOS_COTA_ORDEM
            }
        )

        for row in cls._linhas_bi_pagas(dataset):
            competencia = row["competencia_pagamento"]
            if not competencia:
                continue
            grupo = agrupado[competencia][row["grupo_cota"]]
            grupo["valor_total"] += row["valor_pago"]
            grupo["quantidade"] += 1

        competencias = sorted(agrupado.keys())[-limite:]
        totais = []

        for competencia in competencias:
            dados_competencia = agrupado[competencia]
            segmentos = []
            valor_total = Decimal("0.00")
            quantidade_total = 0

            for chave in GRUPOS_COTA_ORDEM:
                meta = cls._meta_grupo_cota(chave)
                valor_grupo = dados_competencia[chave]["valor_total"]
                quantidade_grupo = dados_competencia[chave]["quantidade"]
                valor_total += valor_grupo
                quantidade_total += quantidade_grupo
                segmentos.append(
                    {
                        "label": meta["label"],
                        "css_class": meta["css_class"],
                        "valor_total": valor_grupo,
                        "quantidade": quantidade_grupo,
                        "percentual_interno": 0.0,
                    }
                )

            totais.append(
                {
                    "competencia": competencia,
                    "label": CotasRPVService.competencia_legivel(competencia),
                    "valor_total": valor_total,
                    "quantidade_total": quantidade_total,
                    "segmentos": segmentos,
                    "altura_percentual": 0.0,
                }
            )

        maior_total = max((item["valor_total"] for item in totais), default=Decimal("0.00"))

        for item in totais:
            item["altura_percentual"] = (
                float((item["valor_total"] / maior_total) * Decimal("100")) if maior_total > 0 else 0.0
            )
            for segmento in item["segmentos"]:
                segmento["percentual_interno"] = (
                    float((segmento["valor_total"] / item["valor_total"]) * Decimal("100"))
                    if item["valor_total"] > 0
                    else 0.0
                )

        return totais

    @classmethod
    def serie_mensal_grupos_cota_projetada(cls, projecao: dict, limite: int = 12) -> list[dict]:
        historico_pago = projecao.get("historico_pago", {})
        competencias = sorted(historico_pago.keys())[-limite:]
        totais = []

        for competencia in competencias:
            dados_competencia = historico_pago.get(competencia, {})
            segmentos = []
            valor_total = Decimal("0.00")
            quantidade_total = 0

            for chave in GRUPOS_COTA_ORDEM:
                meta = cls._meta_grupo_cota(chave)
                valor_grupo = dados_competencia.get(chave, {}).get("valor_total", Decimal("0.00"))
                quantidade_grupo = dados_competencia.get(chave, {}).get("quantidade", 0)
                valor_total += valor_grupo
                quantidade_total += quantidade_grupo
                segmentos.append(
                    {
                        "label": meta["label"],
                        "css_class": meta["css_class"],
                        "valor_total": valor_grupo,
                        "quantidade": quantidade_grupo,
                        "percentual_interno": 0.0,
                    }
                )

            totais.append(
                {
                    "competencia": competencia,
                    "label": CotasRPVService.competencia_legivel(competencia),
                    "valor_total": valor_total,
                    "quantidade_total": quantidade_total,
                    "segmentos": segmentos,
                    "altura_percentual": 0.0,
                }
            )

        maior_total = max((item["valor_total"] for item in totais), default=Decimal("0.00"))

        for item in totais:
            item["altura_percentual"] = (
                float((item["valor_total"] / maior_total) * Decimal("100")) if maior_total > 0 else 0.0
            )
            for segmento in item["segmentos"]:
                segmento["percentual_interno"] = (
                    float((segmento["valor_total"] / item["valor_total"]) * Decimal("100"))
                    if item["valor_total"] > 0
                    else 0.0
                )

        return totais

    @classmethod
    def resumo_irrf(
        cls,
        dataset: list[dict],
        *,
        competencia_referencia: str | None = None,
        janela_meses: int = 6,
    ) -> dict:
        referencia = (
            CotasRPVService.normalizar_competencia(competencia_referencia)
            or cls.competencia_referencia_bi(dataset)
        )
        competencias = cls._janela_competencias(referencia, janela_meses)
        linhas_irrf = [
            row
            for row in cls._linhas_bi_pagas(dataset)
            if row["competencia_pagamento"] and row["valor_irrf"] > 0
        ]
        agrupado = defaultdict(
            lambda: {
                "valor_total": Decimal("0.00"),
                "quantidade": 0,
                "beneficiarios": set(),
            }
        )

        for row in linhas_irrf:
            competencia = row["competencia_pagamento"]
            dados = agrupado[competencia]
            dados["valor_total"] += row["valor_irrf"]
            dados["quantidade"] += 1
            chave_beneficiario = row["documento_normalizado"] or row["nome_normalizado"]
            if chave_beneficiario:
                dados["beneficiarios"].add(chave_beneficiario)

        serie = []
        maior_valor = Decimal("0.00")

        for competencia in competencias:
            dados = agrupado[competencia]
            maior_valor = max(maior_valor, dados["valor_total"])
            serie.append(
                {
                    "competencia": competencia,
                    "label": CotasRPVService.competencia_legivel(competencia),
                    "valor_total": dados["valor_total"],
                    "quantidade": dados["quantidade"],
                    "beneficiarios": len(dados["beneficiarios"]),
                    "percentual": 0.0,
                }
            )

        for item in serie:
            item["percentual"] = (
                float((item["valor_total"] / maior_valor) * Decimal("100"))
                if maior_valor > 0
                else 0.0
            )

        acumulado_recorte = sum((row["valor_irrf"] for row in linhas_irrf), Decimal("0.00"))
        acumulado_ano = sum(
            (
                row["valor_irrf"]
                for row in linhas_irrf
                if row["competencia_pagamento"].startswith(referencia[:4])
            ),
            Decimal("0.00"),
        )
        irrf_mes = agrupado[referencia]["valor_total"]
        pagamentos_com_irrf = len(linhas_irrf)
        beneficiarios_unicos = len(
            {
                row["documento_normalizado"] or row["nome_normalizado"]
                for row in linhas_irrf
                if row["documento_normalizado"] or row["nome_normalizado"]
            }
        )
        media_mensal = (
            (sum((item["valor_total"] for item in serie), Decimal("0.00")) / Decimal(len(competencias))).quantize(Decimal("0.01"))
            if competencias
            else Decimal("0.00")
        )

        return {
            "competencia_referencia": referencia,
            "competencia_legivel": CotasRPVService.competencia_legivel(referencia),
            "serie": serie,
            "irrf_mes": irrf_mes,
            "acumulado_recorte": acumulado_recorte,
            "acumulado_ano": acumulado_ano,
            "pagamentos_com_irrf": pagamentos_com_irrf,
            "beneficiarios_unicos": beneficiarios_unicos,
            "media_mensal": media_mensal,
            "janela_meses": int(janela_meses or 6),
            "tem_dados": acumulado_recorte > 0,
        }
