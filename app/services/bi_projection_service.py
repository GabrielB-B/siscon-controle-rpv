from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import DativoCI, DativoItem, Processo, RegistroRPV, SituacaoEmpenho, TipoRPV
from app.utils.cota_groups import GRUPOS_COTA_ORDEM, classificar_grupo_cota


def _decimal(valor) -> Decimal:
    try:
        return Decimal(valor or 0)
    except Exception:
        return Decimal("0.00")


def _competencia_normalizada(valor: str | None) -> str:
    competencia = str(valor or "").strip()
    if len(competencia) == 7 and "-" in competencia:
        return competencia
    return ""


def _inicio_competencia(valor: str | None) -> date | None:
    competencia = _competencia_normalizada(valor)
    if not competencia:
        return None

    ano, mes = competencia.split("-", 1)
    try:
        return date(int(ano), int(mes), 1)
    except ValueError:
        return None


def _proxima_competencia_data(valor: date) -> date:
    if valor.month == 12:
        return date(valor.year + 1, 1, 1)
    return date(valor.year, valor.month + 1, 1)


def _faixa_data_pagamento(
    competencia_inicial: str | None,
    competencia_final: str | None,
) -> tuple[date | None, date | None]:
    inicio = _inicio_competencia(competencia_inicial)
    fim = _inicio_competencia(competencia_final)
    if fim:
        fim = _proxima_competencia_data(fim)
    return inicio, fim


def _month_bucket(column):
    dialect_name = db.session.get_bind().dialect.name
    if dialect_name.startswith("mysql"):
        return func.date_format(column, "%Y-%m")
    return func.strftime("%Y-%m", column)


class BIProjectionService:
    @staticmethod
    def supports_operational_projection(
        filtros: dict[str, str] | None = None,
        *,
        visao: str = "operacional",
    ) -> bool:
        filtros = filtros or {}
        if str(visao or "").strip().lower() != "operacional":
            return False

        if str(filtros.get("q") or "").strip():
            return False

        if str(filtros.get("reinf") or "todos").strip() not in ("", "todos"):
            return False

        if str(filtros.get("tipo") or "").strip():
            return False

        return True

    @staticmethod
    def build_operational_projection(
        filtros: dict[str, str] | None = None,
        *,
        current_user_id: int | None = None,
    ) -> dict:
        filtros = filtros or {}
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
        historico_dativos_pagos = defaultdict(
            lambda: {
                "dativo_sem_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
                "dativo_com_irrf": {"valor_total": Decimal("0.00"), "quantidade": 0},
            }
        )

        for linha in BIProjectionService._aggregate_rpvs_paid(filtros, current_user_id=current_user_id):
            grupo_cota = classificar_grupo_cota(linha["tipo_nome"], "rpv_normal")
            if not BIProjectionService._grupo_permitido(grupo_cota, filtros):
                continue
            destino = historico_pago[linha["competencia"]][grupo_cota]
            destino["valor_total"] += linha["valor_total"]
            destino["quantidade"] += linha["quantidade"]

        for linha in BIProjectionService._aggregate_rpvs_open(filtros, current_user_id=current_user_id):
            grupo_cota = classificar_grupo_cota(linha["tipo_nome"], "rpv_normal")
            if not BIProjectionService._grupo_permitido(grupo_cota, filtros):
                continue
            destino = historico_aberto[linha["competencia"]][grupo_cota]
            destino["valor_total"] += linha["valor_total"]
            destino["quantidade"] += linha["quantidade"]

        for linha in BIProjectionService._aggregate_dativos_paid(filtros, current_user_id=current_user_id):
            grupo_cota = classificar_grupo_cota(None, linha["origem_chave"])
            if not BIProjectionService._grupo_permitido(grupo_cota, filtros):
                continue
            destino_grupo = historico_pago[linha["competencia"]][grupo_cota]
            destino_grupo["valor_total"] += linha["valor_total"]
            destino_grupo["quantidade"] += linha["quantidade"]

            destino_dativo = historico_dativos_pagos[linha["competencia"]][linha["origem_chave"]]
            destino_dativo["valor_total"] += linha["valor_total"]
            destino_dativo["quantidade"] += linha["quantidade"]

        for linha in BIProjectionService._aggregate_dativos_open(filtros, current_user_id=current_user_id):
            grupo_cota = classificar_grupo_cota(None, linha["origem_chave"])
            if not BIProjectionService._grupo_permitido(grupo_cota, filtros):
                continue
            destino = historico_aberto[linha["competencia"]][grupo_cota]
            destino["valor_total"] += linha["valor_total"]
            destino["quantidade"] += linha["quantidade"]

        competencias_pagas = sorted(historico_pago.keys())
        competencias_abertas = sorted(historico_aberto.keys())
        competencias_disponiveis = sorted(set(competencias_pagas) | set(competencias_abertas))

        return {
            "historico_pago": {competencia: dict(dados) for competencia, dados in historico_pago.items()},
            "historico_aberto": {competencia: dict(dados) for competencia, dados in historico_aberto.items()},
            "historico_dativos_pagos": {
                competencia: dict(dados)
                for competencia, dados in historico_dativos_pagos.items()
            },
            "competencias_pagas": competencias_pagas,
            "competencias_abertas": competencias_abertas,
            "competencias_disponiveis": competencias_disponiveis,
        }

    @staticmethod
    def _grupo_permitido(grupo_cota: str, filtros: dict[str, str]) -> bool:
        grupo_filtro = str(filtros.get("grupo_cota") or "todos").strip()
        if grupo_filtro in ("", "todos"):
            return True
        return grupo_cota == grupo_filtro

    @staticmethod
    def _origem_rpv_permitida(filtros: dict[str, str]) -> bool:
        origem = str(filtros.get("origem") or "todos").strip()
        return origem in ("", "todos", "rpv_normal")

    @staticmethod
    def _origem_dativo_permitida(filtros: dict[str, str]) -> tuple[bool, str | None]:
        origem = str(filtros.get("origem") or "todos").strip()
        if origem in ("", "todos"):
            return True, None
        if origem == "dativo_com_irrf":
            return True, "com_irrf"
        if origem == "dativo_sem_irrf":
            return True, "sem_irrf"
        return False, None

    @staticmethod
    def _aggregate_rpvs_paid(
        filtros: dict[str, str],
        *,
        current_user_id: int | None,
    ) -> list[dict]:
        pagamento = str(filtros.get("pagamento") or "todos").strip()
        if pagamento == "sem_data" or not BIProjectionService._origem_rpv_permitida(filtros):
            return []

        competencia_expr = _month_bucket(RegistroRPV.data_pagamento)
        query = (
            db.session.query(
                competencia_expr.label("competencia"),
                TipoRPV.nome.label("tipo_nome"),
                func.count(RegistroRPV.id).label("quantidade"),
                func.coalesce(func.sum(RegistroRPV.valor_bruto), 0).label("valor_total"),
            )
            .join(TipoRPV, TipoRPV.id == RegistroRPV.tipo_rpv_id)
            .join(SituacaoEmpenho, SituacaoEmpenho.id == RegistroRPV.situacao_empenho_id)
            .filter(
                RegistroRPV.ativo.is_(True),
                RegistroRPV.data_pagamento.isnot(None),
                func.lower(SituacaoEmpenho.nome) != "cancelado",
            )
        )

        responsavel = str(filtros.get("responsavel") or "todos").strip()
        if responsavel == "meus":
            if current_user_id is None:
                return []
            query = query.filter(RegistroRPV.elaborador_id == current_user_id)
        elif responsavel not in ("", "todos"):
            query = query.filter(RegistroRPV.elaborador_id == responsavel)

        inicio, fim = _faixa_data_pagamento(
            filtros.get("competencia_inicial"),
            filtros.get("competencia_final"),
        )
        if inicio:
            query = query.filter(RegistroRPV.data_pagamento >= inicio)
        if fim:
            query = query.filter(RegistroRPV.data_pagamento < fim)

        query = query.group_by(competencia_expr, TipoRPV.nome)
        return [
            {
                "competencia": str(competencia or ""),
                "tipo_nome": tipo_nome,
                "quantidade": int(quantidade or 0),
                "valor_total": _decimal(valor_total),
            }
            for competencia, tipo_nome, quantidade, valor_total in query.all()
            if competencia
        ]

    @staticmethod
    def _aggregate_rpvs_open(
        filtros: dict[str, str],
        *,
        current_user_id: int | None,
    ) -> list[dict]:
        pagamento = str(filtros.get("pagamento") or "todos").strip()
        if pagamento == "pagos" or not BIProjectionService._origem_rpv_permitida(filtros):
            return []

        query = (
            db.session.query(
                Processo.exercicio.label("competencia"),
                TipoRPV.nome.label("tipo_nome"),
                func.count(RegistroRPV.id).label("quantidade"),
                func.coalesce(func.sum(RegistroRPV.valor_bruto), 0).label("valor_total"),
            )
            .join(Processo, Processo.id == RegistroRPV.processo_id)
            .join(TipoRPV, TipoRPV.id == RegistroRPV.tipo_rpv_id)
            .join(SituacaoEmpenho, SituacaoEmpenho.id == RegistroRPV.situacao_empenho_id)
            .filter(
                RegistroRPV.ativo.is_(True),
                RegistroRPV.data_pagamento.is_(None),
                func.lower(SituacaoEmpenho.nome) != "cancelado",
            )
        )

        responsavel = str(filtros.get("responsavel") or "todos").strip()
        if responsavel == "meus":
            if current_user_id is None:
                return []
            query = query.filter(RegistroRPV.elaborador_id == current_user_id)
        elif responsavel not in ("", "todos"):
            query = query.filter(RegistroRPV.elaborador_id == responsavel)

        competencia_inicial = _competencia_normalizada(filtros.get("competencia_inicial"))
        competencia_final = _competencia_normalizada(filtros.get("competencia_final"))
        if competencia_inicial:
            query = query.filter(Processo.exercicio >= competencia_inicial)
        if competencia_final:
            query = query.filter(Processo.exercicio <= competencia_final)

        query = query.group_by(Processo.exercicio, TipoRPV.nome)
        return [
            {
                "competencia": str(competencia or ""),
                "tipo_nome": tipo_nome,
                "quantidade": int(quantidade or 0),
                "valor_total": _decimal(valor_total),
            }
            for competencia, tipo_nome, quantidade, valor_total in query.all()
            if competencia
        ]

    @staticmethod
    def _aggregate_dativos_paid(
        filtros: dict[str, str],
        *,
        current_user_id: int | None,
    ) -> list[dict]:
        pagamento = str(filtros.get("pagamento") or "todos").strip()
        origem_permitida, grupo_origem = BIProjectionService._origem_dativo_permitida(filtros)
        if pagamento == "sem_data" or not origem_permitida:
            return []

        competencia_expr = _month_bucket(DativoItem.data_pagamento)
        query = (
            db.session.query(
                competencia_expr.label("competencia"),
                DativoItem.grupo.label("grupo"),
                func.count(DativoItem.id).label("quantidade"),
                func.coalesce(func.sum(DativoItem.valor_bruto), 0).label("valor_total"),
            )
            .join(DativoCI, DativoCI.id == DativoItem.dativo_ci_id)
            .join(SituacaoEmpenho, SituacaoEmpenho.id == DativoItem.situacao_rpv_id)
            .filter(
                DativoItem.ativo.is_(True),
                DativoItem.data_pagamento.isnot(None),
                func.lower(SituacaoEmpenho.nome) != "cancelado",
            )
        )

        if grupo_origem:
            query = query.filter(DativoItem.grupo == grupo_origem)

        responsavel = str(filtros.get("responsavel") or "todos").strip()
        if responsavel == "meus":
            if current_user_id is None:
                return []
            query = query.filter(DativoCI.responsavel_id == current_user_id)
        elif responsavel not in ("", "todos"):
            query = query.filter(DativoCI.responsavel_id == responsavel)

        inicio, fim = _faixa_data_pagamento(
            filtros.get("competencia_inicial"),
            filtros.get("competencia_final"),
        )
        if inicio:
            query = query.filter(DativoItem.data_pagamento >= inicio)
        if fim:
            query = query.filter(DativoItem.data_pagamento < fim)

        query = query.group_by(competencia_expr, DativoItem.grupo)
        return [
            {
                "competencia": str(competencia or ""),
                "origem_chave": "dativo_com_irrf" if grupo == "com_irrf" else "dativo_sem_irrf",
                "quantidade": int(quantidade or 0),
                "valor_total": _decimal(valor_total),
            }
            for competencia, grupo, quantidade, valor_total in query.all()
            if competencia
        ]

    @staticmethod
    def _aggregate_dativos_open(
        filtros: dict[str, str],
        *,
        current_user_id: int | None,
    ) -> list[dict]:
        pagamento = str(filtros.get("pagamento") or "todos").strip()
        origem_permitida, grupo_origem = BIProjectionService._origem_dativo_permitida(filtros)
        if pagamento == "pagos" or not origem_permitida:
            return []

        query = (
            db.session.query(
                DativoCI.exercicio.label("competencia"),
                DativoItem.grupo.label("grupo"),
                func.count(DativoItem.id).label("quantidade"),
                func.coalesce(func.sum(DativoItem.valor_bruto), 0).label("valor_total"),
            )
            .join(DativoCI, DativoCI.id == DativoItem.dativo_ci_id)
            .join(SituacaoEmpenho, SituacaoEmpenho.id == DativoItem.situacao_rpv_id)
            .filter(
                DativoItem.ativo.is_(True),
                DativoItem.data_pagamento.is_(None),
                func.lower(SituacaoEmpenho.nome) != "cancelado",
            )
        )

        if grupo_origem:
            query = query.filter(DativoItem.grupo == grupo_origem)

        responsavel = str(filtros.get("responsavel") or "todos").strip()
        if responsavel == "meus":
            if current_user_id is None:
                return []
            query = query.filter(DativoCI.responsavel_id == current_user_id)
        elif responsavel not in ("", "todos"):
            query = query.filter(DativoCI.responsavel_id == responsavel)

        competencia_inicial = _competencia_normalizada(filtros.get("competencia_inicial"))
        competencia_final = _competencia_normalizada(filtros.get("competencia_final"))
        if competencia_inicial:
            query = query.filter(DativoCI.exercicio >= competencia_inicial)
        if competencia_final:
            query = query.filter(DativoCI.exercicio <= competencia_final)

        query = query.group_by(DativoCI.exercicio, DativoItem.grupo)
        return [
            {
                "competencia": str(competencia or ""),
                "origem_chave": "dativo_com_irrf" if grupo == "com_irrf" else "dativo_sem_irrf",
                "quantidade": int(quantidade or 0),
                "valor_total": _decimal(valor_total),
            }
            for competencia, grupo, quantidade, valor_total in query.all()
            if competencia
        ]
