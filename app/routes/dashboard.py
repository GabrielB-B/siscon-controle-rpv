import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import StringIO
from typing import Callable

from flask import Blueprint, Response, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload
from unidecode import unidecode

from app.models import DativoCI, DativoItem, DativoLote, Processo, RegistroRPV, RPVPendenciaDocumento
from app.services.bi_beneficiary_service import BIBeneficiaryService
from app.services.bi_context_service import BIContextService
from app.services.bi_dataset_service import BIDatasetService
from app.services.bi_export_service import BIExportService
from app.services.bi_filter_service import BIFilterService
from app.services.bi_operational_metrics_service import BIOperationalMetricsService
from app.services.cotas_rpv_service import CotasRPVService
from app.utils.cota_groups import (
    GRUPOS_COTA_META as GRUPOS_COTA_BI,
    GRUPOS_COTA_OPCOES,
    GRUPOS_COTA_ORDEM,
    classificar_grupo_cota as _classificar_grupo_cota_shared,
    meta_grupo_cota as _meta_grupo_cota_shared,
)
from app.utils.domain_profile import get_domain_profile
from app.utils.formatters import formatar_documento_br
from app.utils.normalizers import normalizar_documento
from app.utils.reinf_rules import (
    reinf_status_eh_cancelado,
    reinf_status_eh_concluido,
    reinf_status_esta_resolvido,
)

dashboard_bp = Blueprint("dashboard", __name__)

LIMITE_ALERTA_IRRF = Decimal("5040.00")
TIPOS_SENSIVEIS_IRRF = {
    "rpv honorarios",
    "honorarios",
    "rpv dativo",
    "dativo",
}
ORIGENS_BI = {
    "todos": "Todas as origens",
    "rpv_normal": "RPVs normais",
    "dativo_com_irrf": "Dativos com IRRF",
    "dativo_sem_irrf": "Dativos sem IRRF",
}
PAGAMENTO_BI = {
    "todos": "Todos",
    "pagos": "Com data de pagamento",
    "sem_data": "Sem data de pagamento",
}
VISOES_BI_LABELS = {
    "operacional": "Operacional",
    "conferencia": "Conferencia",
}
VISOES_BI_NAVEGACAO = {
    "operacional": "Operacional",
}
JANELAS_GRAFICO_BI = {
    6: "6 meses",
    12: "12 meses",
}
CORES_GRAFICOS_BI = {
    "pessoal": "#2f6da8",
    "comum": "#0f8f7c",
    "pericial": "#e68a00",
    "pago": "#1e3a5f",
    "aberto": "#d97706",
    "previsao": "#0f766e",
    "dativos": "#2ba89a",
    "outros": "#d8e7e4",
}
BENEFICIARIOS_BI_DESTAQUE = 5
BENEFICIARIOS_BI_POR_PAGINA = 20
BENEFICIARIOS_BI_FISCAL = {
    "todos": "Todos",
    "com_retencao": "Com pelo menos uma retencao",
    "sem_retencao": "Sem retencao no recorte",
}
DOMAIN_PROFILE = get_domain_profile()
LABEL_SEM_IRRF = DOMAIN_PROFILE.situacao_imposto_sem_irrf_nome
STATUSES_PRIORITARIOS_RPV_HOME = (
    {
        "nome": DOMAIN_PROFILE.situacao_empenho_name("sem_tratamento"),
        "slug": "sem-tratamento",
        "label": "Sem tratamento",
        "css_class": "priority-chip-neutral",
    },
    {
        "nome": DOMAIN_PROFILE.situacao_empenho_name("se_aguardando_aprovacao"),
        "slug": "se-aguardando-aprovacao",
        "label": "SE aguardando aprovacao",
        "css_class": "priority-chip-approval",
    },
    {
        "nome": DOMAIN_PROFILE.situacao_empenho_name("aguardando_retorno_banco"),
        "slug": "aguardando-retorno-banco",
        "label": "Aguardando retorno banco",
        "css_class": "priority-chip-bank",
    },
)


def _normalizar_texto(valor: str | None) -> str:
    return unidecode(str(valor or "").strip()).lower()


STATUSES_PRIORITARIOS_RPV_HOME_INDEX = {
    _normalizar_texto(item["nome"]): item for item in STATUSES_PRIORITARIOS_RPV_HOME
}


def _competencia_atual() -> str:
    return date.today().strftime("%Y-%m")


def _competencia_legivel(valor: str) -> str:
    meses = {
        "01": "jan",
        "02": "fev",
        "03": "mar",
        "04": "abr",
        "05": "mai",
        "06": "jun",
        "07": "jul",
        "08": "ago",
        "09": "set",
        "10": "out",
        "11": "nov",
        "12": "dez",
    }

    competencia = str(valor or "").strip()
    if len(competencia) == 7 and "-" in competencia:
        ano, mes = competencia.split("-", 1)
        return f"{meses.get(mes, mes)}/{ano}"
    return competencia or "-"


def _janela_meses_bi(valor: str | int | None) -> int:
    try:
        quantidade = int(str(valor or "").strip() or "6")
    except (TypeError, ValueError):
        return 6

    if quantidade in JANELAS_GRAFICO_BI:
        return quantidade
    return 6


def _inteiro_positivo(valor: str | int | None, padrao: int = 1) -> int:
    try:
        numero = int(str(valor or "").strip() or str(padrao))
    except (TypeError, ValueError):
        return padrao

    return numero if numero > 0 else padrao


def _visao_bi(valor: str | None) -> str:
    chave = str(valor or "").strip().lower()
    if chave in VISOES_BI_LABELS:
        return chave
    return "operacional"


def _decimal(valor) -> Decimal:
    try:
        return Decimal(valor or 0)
    except Exception:
        return Decimal("0.00")


def _decimal_csv(valor) -> str:
    numero = _decimal(valor)
    return f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data_legivel(valor) -> str:
    return valor.strftime("%d/%m/%Y") if valor else "-"


def _competencia_normalizada(valor: str | None) -> str:
    competencia = str(valor or "").strip()
    if len(competencia) == 7 and "-" in competencia:
        return competencia
    return ""


def _competencia_no_intervalo(
    competencia: str,
    competencia_inicial: str,
    competencia_final: str,
) -> bool:
    if not competencia:
        return not (competencia_inicial or competencia_final)

    if competencia_inicial and competencia < competencia_inicial:
        return False

    if competencia_final and competencia > competencia_final:
        return False

    return True


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


def _faixa_data_pagamento_bi(
    competencia_inicial: str | None,
    competencia_final: str | None,
) -> tuple[date | None, date | None]:
    inicio = _inicio_competencia(competencia_inicial)
    fim = _inicio_competencia(competencia_final)
    if fim:
        fim = _proxima_competencia_data(fim)
    return inicio, fim


def _filtro_competencia_operacional_rpv(
    competencia_inicial: str | None,
    competencia_final: str | None,
    *,
    pagamento: str,
):
    competencia_inicial = _competencia_normalizada(competencia_inicial)
    competencia_final = _competencia_normalizada(competencia_final)
    if not (competencia_inicial or competencia_final):
        return None

    inicio_pagamento, fim_pagamento = _faixa_data_pagamento_bi(
        competencia_inicial,
        competencia_final,
    )
    filtros_pagos = [RegistroRPV.data_pagamento.isnot(None)]
    if inicio_pagamento:
        filtros_pagos.append(RegistroRPV.data_pagamento >= inicio_pagamento)
    if fim_pagamento:
        filtros_pagos.append(RegistroRPV.data_pagamento < fim_pagamento)

    filtros_abertos = [RegistroRPV.data_pagamento.is_(None)]
    if competencia_inicial:
        filtros_abertos.append(RegistroRPV.processo.has(Processo.exercicio >= competencia_inicial))
    if competencia_final:
        filtros_abertos.append(RegistroRPV.processo.has(Processo.exercicio <= competencia_final))

    if pagamento == "pagos":
        return and_(*filtros_pagos)
    if pagamento == "sem_data":
        return and_(*filtros_abertos)
    return or_(and_(*filtros_pagos), and_(*filtros_abertos))


def _filtro_competencia_operacional_dativo(
    competencia_inicial: str | None,
    competencia_final: str | None,
    *,
    pagamento: str,
):
    competencia_inicial = _competencia_normalizada(competencia_inicial)
    competencia_final = _competencia_normalizada(competencia_final)
    if not (competencia_inicial or competencia_final):
        return None

    inicio_pagamento, fim_pagamento = _faixa_data_pagamento_bi(
        competencia_inicial,
        competencia_final,
    )
    filtros_pagos = [DativoItem.data_pagamento.isnot(None)]
    if inicio_pagamento:
        filtros_pagos.append(DativoItem.data_pagamento >= inicio_pagamento)
    if fim_pagamento:
        filtros_pagos.append(DativoItem.data_pagamento < fim_pagamento)

    filtros_abertos = [DativoItem.data_pagamento.is_(None)]
    if competencia_inicial:
        filtros_abertos.append(DativoItem.dativo_ci.has(DativoCI.exercicio >= competencia_inicial))
    if competencia_final:
        filtros_abertos.append(DativoItem.dativo_ci.has(DativoCI.exercicio <= competencia_final))

    if pagamento == "pagos":
        return and_(*filtros_pagos)
    if pagamento == "sem_data":
        return and_(*filtros_abertos)
    return or_(and_(*filtros_pagos), and_(*filtros_abertos))


def _situacao_indica_fluxo_irrf(nome_situacao: str | None) -> bool:
    return _normalizar_texto(nome_situacao) not in {"", _normalizar_texto(LABEL_SEM_IRRF)}


def _status_reinf_concluido(status: str | None) -> bool:
    return reinf_status_eh_concluido(status)


def _status_reinf_cancelado(status: str | None) -> bool:
    return reinf_status_eh_cancelado(status)


def _status_reinf_resolvido(status: str | None) -> bool:
    return reinf_status_esta_resolvido(status)


def _situacao_exige_continuidade(situacao) -> bool:
    if not situacao:
        return True
    return not bool(getattr(situacao, "is_final", False))


def _nome_responsavel_home(usuario, fallback: str = "Sem responsavel") -> str:
    return str(getattr(usuario, "nome", "") or "").strip() or fallback


def _status_prioritario_rpv_home(nome_situacao: str | None) -> dict | None:
    return STATUSES_PRIORITARIOS_RPV_HOME_INDEX.get(_normalizar_texto(nome_situacao))


def _resumo_setor_rpvs_home(*, rpvs: list[RegistroRPV]) -> dict:
    responsaveis: dict[int | None, dict] = {}
    totais_por_status: dict[str, dict] = {}

    for registro in rpvs:
        if getattr(registro, "status_principal_cancelado", False):
            continue

        situacao = getattr(registro, "situacao_empenho", None)
        if not _situacao_exige_continuidade(situacao):
            continue

        definicao = _status_prioritario_rpv_home(getattr(situacao, "nome", None))
        if not definicao:
            continue

        responsavel_id = getattr(registro, "elaborador_id", None)
        responsavel_nome = _nome_responsavel_home(getattr(registro, "elaborador", None))
        bloco_responsavel = responsaveis.setdefault(
            responsavel_id,
            {
                "responsavel_id": responsavel_id,
                "responsavel_nome": responsavel_nome,
                "total": 0,
                "status_map": {},
                "url": url_for(
                    "cadastros.lista_rpvs",
                    responsavel=responsavel_id if responsavel_id is not None else "todos",
                ),
            },
        )

        bloco_status = bloco_responsavel["status_map"].setdefault(
            definicao["slug"],
            {
                "slug": definicao["slug"],
                "label": definicao["label"],
                "css_class": definicao["css_class"],
                "quantidade": 0,
                "situacao_id": getattr(situacao, "id", None),
            },
        )
        bloco_status["quantidade"] += 1
        bloco_responsavel["total"] += 1

        total_status = totais_por_status.setdefault(
            definicao["slug"],
            {
                "slug": definicao["slug"],
                "label": definicao["label"],
                "css_class": definicao["css_class"],
                "quantidade": 0,
                "situacao_id": getattr(situacao, "id", None),
            },
        )
        total_status["quantidade"] += 1

    usuarios = []
    for responsavel in responsaveis.values():
        status_items = []
        for definicao in STATUSES_PRIORITARIOS_RPV_HOME:
            item = responsavel["status_map"].get(definicao["slug"])
            if not item:
                continue
            status_items.append(
                {
                    **item,
                    "url": url_for(
                        "cadastros.lista_rpvs",
                        responsavel=(
                            responsavel["responsavel_id"]
                            if responsavel["responsavel_id"] is not None
                            else "todos"
                        ),
                        situacao_empenho_id=item["situacao_id"],
                    ),
                }
            )

        usuarios.append(
            {
                "responsavel_id": responsavel["responsavel_id"],
                "responsavel_nome": responsavel["responsavel_nome"],
                "total": responsavel["total"],
                "status_items": status_items,
                "url": responsavel["url"],
            }
        )

    usuarios.sort(
        key=lambda item: (
            -item["total"],
            0 if item["responsavel_id"] == current_user.id else 1,
            _normalizar_texto(item["responsavel_nome"]),
        )
    )

    totais = []
    for definicao in STATUSES_PRIORITARIOS_RPV_HOME:
        item = totais_por_status.get(definicao["slug"])
        if not item:
            continue
        totais.append(
            {
                **item,
                "url": url_for(
                    "cadastros.lista_rpvs",
                    responsavel="todos",
                    situacao_empenho_id=item["situacao_id"],
                ),
            }
        )

    return {
        "usuarios": usuarios,
        "totais": totais,
        "total_quantidade": sum(item["quantidade"] for item in totais),
        "total_responsaveis": len(usuarios),
    }


def _resumo_setor_dativos_home(
    *,
    lotes_sem_irrf: list[DativoLote],
    dativo_items: list[DativoItem],
) -> dict:
    responsaveis: dict[int | None, dict] = {}
    totais_por_status: dict[str, dict] = {}

    def registrar(status_obj, responsavel_id, responsavel_nome, *, origem: str):
        definicao = _status_prioritario_rpv_home(getattr(status_obj, "nome", None))
        if not definicao:
            return

        bloco_responsavel = responsaveis.setdefault(
            responsavel_id,
            {
                "responsavel_id": responsavel_id,
                "responsavel_nome": responsavel_nome,
                "total": 0,
                "total_lotes": 0,
                "total_itens": 0,
                "status_map": {},
                "url": url_for(
                    "dativos.lista_cis",
                    responsavel=responsavel_id if responsavel_id is not None else "todos",
                ),
            },
        )

        bloco_status = bloco_responsavel["status_map"].setdefault(
            definicao["slug"],
            {
                "slug": definicao["slug"],
                "label": definicao["label"],
                "css_class": definicao["css_class"],
                "quantidade": 0,
                "situacao_id": getattr(status_obj, "id", None),
            },
        )
        bloco_status["quantidade"] += 1
        bloco_responsavel["total"] += 1

        if origem == "lote":
            bloco_responsavel["total_lotes"] += 1
        else:
            bloco_responsavel["total_itens"] += 1

        total_status = totais_por_status.setdefault(
            definicao["slug"],
            {
                "slug": definicao["slug"],
                "label": definicao["label"],
                "css_class": definicao["css_class"],
                "quantidade": 0,
                "situacao_id": getattr(status_obj, "id", None),
            },
        )
        total_status["quantidade"] += 1

    for lote in lotes_sem_irrf:
        if getattr(lote, "status_principal_cancelado", False):
            continue
        situacao = getattr(lote, "situacao_rpv", None)
        if not _situacao_exige_continuidade(situacao):
            continue
        registrar(
            situacao,
            getattr(lote, "responsavel_id", None),
            _nome_responsavel_home(getattr(lote, "responsavel", None)),
            origem="lote",
        )

    for item in dativo_items:
        if item.grupo != "com_irrf":
            continue
        if getattr(item, "status_principal_cancelado", False):
            continue
        situacao = getattr(item, "situacao_rpv", None)
        if not _situacao_exige_continuidade(situacao):
            continue
        registrar(
            situacao,
            getattr(item, "responsavel_id", None),
            _nome_responsavel_home(getattr(item, "responsavel", None)),
            origem="item",
        )

    usuarios = []
    for responsavel in responsaveis.values():
        status_items = []
        for definicao in STATUSES_PRIORITARIOS_RPV_HOME:
            item = responsavel["status_map"].get(definicao["slug"])
            if not item:
                continue
            status_items.append(
                {
                    **item,
                    "url": url_for(
                        "dativos.lista_cis",
                        responsavel=(
                            responsavel["responsavel_id"]
                            if responsavel["responsavel_id"] is not None
                            else "todos"
                        ),
                        situacao_rpv_id=item["situacao_id"],
                    ),
                }
            )

        usuarios.append(
            {
                "responsavel_id": responsavel["responsavel_id"],
                "responsavel_nome": responsavel["responsavel_nome"],
                "total": responsavel["total"],
                "total_lotes": responsavel["total_lotes"],
                "total_itens": responsavel["total_itens"],
                "status_items": status_items,
                "url": responsavel["url"],
            }
        )

    usuarios.sort(
        key=lambda item: (
            -item["total"],
            0 if item["responsavel_id"] == current_user.id else 1,
            _normalizar_texto(item["responsavel_nome"]),
        )
    )

    totais = []
    for definicao in STATUSES_PRIORITARIOS_RPV_HOME:
        item = totais_por_status.get(definicao["slug"])
        if not item:
            continue
        totais.append(
            {
                **item,
                "url": url_for(
                    "dativos.lista_cis",
                    responsavel="todos",
                    situacao_rpv_id=item["situacao_id"],
                ),
            }
        )

    return {
        "usuarios": usuarios,
        "totais": totais,
        "total_quantidade": sum(item["quantidade"] for item in totais),
        "total_responsaveis": len(usuarios),
        "total_lotes": sum(item["total_lotes"] for item in usuarios),
        "total_itens": sum(item["total_itens"] for item in usuarios),
    }


def _tem_irrf_rpv(registro: RegistroRPV) -> bool:
    if getattr(registro, "sem_irrf_efetivo", False):
        return False

    if getattr(registro, "possui_irrf", False):
        return True

    if _decimal(registro.valor_irrf) > 0:
        return True

    nome_situacao = getattr(getattr(registro, "situacao_imposto", None), "nome", None)
    return _situacao_indica_fluxo_irrf(nome_situacao)


def _tipo_rpv_exige_alerta(nome_tipo: str | None) -> bool:
    return _normalizar_texto(nome_tipo) in TIPOS_SENSIVEIS_IRRF


def _rpv_precisa_alerta_irrf(registro: RegistroRPV) -> bool:
    nome_tipo = getattr(getattr(registro, "tipo_rpv", None), "nome", None)

    return (
        _tipo_rpv_exige_alerta(nome_tipo)
        and not getattr(registro, "sem_irrf_efetivo", False)
        and _decimal(registro.valor_bruto) > LIMITE_ALERTA_IRRF
        and _decimal(registro.valor_irrf) <= 0
    )


def _item_dativo_sem_irrf_precisa_alerta(item: DativoItem) -> bool:
    return getattr(item, "alerta_irrf_sem_retencao_pendente", False)


def _abrir_url_dativo(item: DativoItem) -> str:
    if item.grupo == "com_irrf":
        return url_for("dativos.detalhe_item_com_irrf", item_id=item.id)

    if item.dativo_lote_id:
        return url_for(
            "dativos.editar_item_lote",
            lote_id=item.dativo_lote_id,
            item_id=item.id,
        )

    return url_for("dativos.detalhe_ci", ci_id=item.dativo_ci_id)


def _reinf_status_bi(status: str | None, tem_irrf: bool) -> str:
    return status or ("Nao enviado" if tem_irrf else "Nao se aplica")


def _match_reinf_bi(status: str, filtro: str) -> bool:
    filtro_normalizado = _normalizar_texto(filtro)
    status_normalizado = _normalizar_texto(status)

    if filtro_normalizado in {"", "todos"}:
        return True
    if filtro_normalizado == "concluido":
        return _status_reinf_concluido(status)
    if filtro_normalizado == "cancelado":
        return _status_reinf_cancelado(status)
    if filtro_normalizado == "pendente":
        return status_normalizado != "nao se aplica" and not _status_reinf_resolvido(status)
    if filtro_normalizado == "nao_aplicavel":
        return status_normalizado == "nao se aplica"

    return True


def _match_responsavel_bi(responsavel_id, filtro: str) -> bool:
    if filtro in ("", "todos"):
        return True

    if filtro == "meus":
        return responsavel_id == current_user.id

    return str(responsavel_id) == str(filtro)


def _match_pagamento_bi(data_pagamento, filtro: str) -> bool:
    filtro_normalizado = _normalizar_texto(filtro)

    if filtro_normalizado in {"", "todos"}:
        return True

    if filtro_normalizado == "pagos":
        return bool(data_pagamento)

    if filtro_normalizado == "sem_data":
        return not bool(data_pagamento)

    return True


def _meta_grupo_cota(chave: str) -> dict:
    return _meta_grupo_cota_shared(chave)


def _classificar_grupo_cota(tipo_nome: str | None, origem_chave: str | None = None) -> str:
    return _classificar_grupo_cota_shared(tipo_nome, origem_chave)


def _proxima_competencia(competencia: str | None) -> str:
    valor = _competencia_normalizada(competencia)
    if not valor:
        return ""

    try:
        ano, mes = valor.split("-", 1)
        ano_int = int(ano)
        mes_int = int(mes)
    except (TypeError, ValueError):
        return ""

    mes_int += 1
    if mes_int > 12:
        ano_int += 1
        mes_int = 1

    return f"{ano_int:04d}-{mes_int:02d}"


def _deslocar_competencia(competencia: str | None, deslocamento: int) -> str:
    valor = _competencia_normalizada(competencia)
    if not valor:
        return ""

    try:
        ano, mes = valor.split("-", 1)
        total_meses = (int(ano) * 12) + (int(mes) - 1) + deslocamento
    except (TypeError, ValueError):
        return ""

    ano_resultado = total_meses // 12
    mes_resultado = (total_meses % 12) + 1
    return f"{ano_resultado:04d}-{mes_resultado:02d}"


def _janela_competencias(competencia_referencia: str | None, quantidade: int) -> list[str]:
    referencia = _competencia_normalizada(competencia_referencia) or _competencia_atual()
    quantidade_normalizada = max(_janela_meses_bi(quantidade), 1)
    return [
        _deslocar_competencia(referencia, deslocamento)
        for deslocamento in range(-(quantidade_normalizada - 1), 1)
    ]


def _grupos_cota_visiveis(filtros: dict[str, str] | None = None) -> tuple[str, ...]:
    if not filtros:
        return GRUPOS_COTA_ORDEM

    grupo_cota = str(filtros.get("grupo_cota") or "").strip()
    if grupo_cota in GRUPOS_COTA_BI:
        return (grupo_cota,)
    return GRUPOS_COTA_ORDEM


def _competencias_disponiveis(dataset: list[dict]) -> list[str]:
    return sorted({row["competencia"] for row in dataset if row["competencia"]})


def _competencias_pagamento_disponiveis(dataset: list[dict]) -> list[str]:
    return sorted({row["competencia_pagamento"] for row in dataset if row["competencia_pagamento"]})


def _filtros_bi_tem_competencia_explicita(filtros: dict[str, str] | None) -> bool:
    if not filtros:
        return False

    return bool(
        _competencia_normalizada(filtros.get("competencia_inicial"))
        or _competencia_normalizada(filtros.get("competencia_final"))
    )


def _competencia_referencia_bi(
    dataset: list[dict],
    filtros: dict[str, str] | None = None,
) -> str:
    competencia_atual = _competencia_atual()
    competencias_disponiveis = _competencias_disponiveis(dataset)
    competencias_pagamento = _competencias_pagamento_disponiveis(dataset)
    ultima_competencia_disponivel = (
        competencias_pagamento[-1]
        if competencias_pagamento
        else (competencias_disponiveis[-1] if competencias_disponiveis else "")
    )

    if _filtros_bi_tem_competencia_explicita(filtros):
        return ultima_competencia_disponivel or competencia_atual

    if not ultima_competencia_disponivel:
        return competencia_atual

    return (
        competencia_atual
        if competencia_atual >= ultima_competencia_disponivel
        else ultima_competencia_disponivel
    )


def _linhas_bi_pagas(dataset: list[dict]) -> list[dict]:
    return [row for row in dataset if row["valor_pago"] > 0 and row["competencia_pagamento"]]


def _linhas_bi_em_aberto(dataset: list[dict]) -> list[dict]:
    return [row for row in dataset if row["valor_previsto_aberto"] > 0]


def _total_beneficiarios_pagos_bi(dataset: list[dict]) -> int:
    return len(
        {
            row["documento_normalizado"] or row["nome_normalizado"]
            for row in _linhas_bi_pagas(dataset)
            if row["documento_normalizado"] or row["nome_normalizado"]
        }
    )


def _periodo_pagamentos_bi(dataset: list[dict], filtros: dict[str, str]) -> str:
    competencia_inicial = _competencia_normalizada(filtros.get("competencia_inicial"))
    competencia_final = _competencia_normalizada(filtros.get("competencia_final"))
    if competencia_inicial and competencia_final:
        if competencia_inicial == competencia_final:
            return f"Pagamentos em {_competencia_legivel(competencia_inicial)}."
        return (
            f"Pagamentos entre {_competencia_legivel(competencia_inicial)} "
            f"e {_competencia_legivel(competencia_final)}."
        )
    if competencia_inicial:
        return f"Pagamentos a partir de {_competencia_legivel(competencia_inicial)}."
    if competencia_final:
        return f"Pagamentos ate {_competencia_legivel(competencia_final)}."

    competencias = _competencias_pagamento_disponiveis(dataset)
    if not competencias:
        return "Leitura baseada apenas nos pagamentos encontrados no recorte."
    if len(competencias) == 1:
        return f"Pagamentos concentrados em {_competencia_legivel(competencias[0])}."
    return (
        f"Pagamentos encontrados de {_competencia_legivel(competencias[0])} "
        f"a {_competencia_legivel(competencias[-1])}."
    )


def _filtros_beneficiarios_bi_da_requisicao() -> dict[str, str | int | bool]:
    return BIFilterService.normalize_beneficiary_filters(
        request.args,
        integer_normalizer=_inteiro_positivo,
        fiscal_options=BENEFICIARIOS_BI_FISCAL,
    )


def _url_bi_beneficiarios_pagina(
    filtros: dict[str, str],
    estado: dict[str, str | int | bool] | None = None,
    **updates,
) -> str:
    parametros = {
        chave: valor
        for chave, valor in filtros.items()
        if valor not in ("", None)
    }
    beneficiarios_estado = {
        "q": str((estado or {}).get("q") or "").strip(),
        "pagina": _inteiro_positivo((estado or {}).get("pagina"), 1),
        "fiscal": str((estado or {}).get("fiscal") or "todos").strip() or "todos",
    }
    if beneficiarios_estado["fiscal"] not in BENEFICIARIOS_BI_FISCAL:
        beneficiarios_estado["fiscal"] = "todos"

    for chave, valor in updates.items():
        if chave == "q":
            beneficiarios_estado["q"] = str(valor or "").strip()
        elif chave == "pagina":
            beneficiarios_estado["pagina"] = _inteiro_positivo(valor, 1)
        elif chave == "fiscal":
            fiscal = str(valor or "todos").strip() or "todos"
            beneficiarios_estado["fiscal"] = (
                fiscal if fiscal in BENEFICIARIOS_BI_FISCAL else "todos"
            )

    if beneficiarios_estado["q"]:
        parametros["beneficiario_q"] = beneficiarios_estado["q"]
    if beneficiarios_estado["pagina"] > 1:
        parametros["pagina"] = str(beneficiarios_estado["pagina"])
    if beneficiarios_estado["fiscal"] != "todos":
        parametros["fiscal"] = beneficiarios_estado["fiscal"]

    return url_for("dashboard.bi_beneficiarios", **parametros)


def _url_bi_beneficiarios_atalho(filtros: dict[str, str]) -> str:
    parametros = {
        chave: valor
        for chave, valor in filtros.items()
        if valor not in ("", None)
    }
    return url_for("dashboard.bi_beneficiarios", **parametros)


def _registro_bi_rpv(registro: RegistroRPV) -> dict:
    processo = getattr(registro, "processo", None)
    tipo_nome = getattr(getattr(registro, "tipo_rpv", None), "nome", None) or "Sem tipo"
    tipo_documento = str(getattr(registro, "tipo_documento_efetivo", "") or "Documento").upper()
    documento_original = registro.documento_original or "-"
    documento_limpo = normalizar_documento(documento_original) or "-"
    documento_formatado = formatar_documento_br(documento_original, tipo_documento) or "-"
    responsavel = registro.elaborador.nome if registro.elaborador else "-"
    competencia_cadastro = _competencia_normalizada(getattr(processo, "exercicio", ""))
    competencia_pagamento = (
        registro.data_pagamento.strftime("%Y-%m") if registro.data_pagamento else ""
    )
    competencia = competencia_pagamento or competencia_cadastro
    tem_irrf = _tem_irrf_rpv(registro)
    grupo_cota = _classificar_grupo_cota(tipo_nome, "rpv_normal")
    meta_grupo = _meta_grupo_cota(grupo_cota)
    valor_bruto = _decimal(registro.valor_bruto)
    valor_pago = valor_bruto if registro.data_pagamento else Decimal("0.00")
    valor_previsto_aberto = Decimal("0.00") if registro.data_pagamento else valor_bruto

    return {
        "origem": "RPV normal",
        "origem_chave": "rpv_normal",
        "tipo": tipo_nome,
        "grupo_cota": grupo_cota,
        "grupo_cota_label": meta_grupo["label"],
        "responsavel": responsavel,
        "responsavel_id": registro.elaborador_id,
        "competencia": competencia,
        "competencia_legivel": _competencia_legivel(competencia) if competencia else "-",
        "competencia_cadastro": competencia_cadastro,
        "competencia_cadastro_legivel": (
            _competencia_legivel(competencia_cadastro) if competencia_cadastro else "-"
        ),
        "competencia_pagamento": competencia_pagamento,
        "competencia_pagamento_legivel": (
            _competencia_legivel(competencia_pagamento) if competencia_pagamento else "-"
        ),
        "data_pagamento": registro.data_pagamento,
        "data_pagamento_legivel": _data_legivel(registro.data_pagamento),
        "pagamento_status": "Pago" if registro.data_pagamento else "Em carteira",
        "nome": registro.nome_beneficiario,
        "nome_normalizado": _normalizar_texto(registro.nome_beneficiario),
        "tipo_documento": tipo_documento,
        "documento": documento_original,
        "documento_limpo": documento_limpo,
        "documento_formatado": documento_formatado,
        "documento_normalizado": documento_limpo,
        "processo": getattr(processo, "numero_processo", "-"),
        "ci": getattr(processo, "processo_edoc", "-"),
        "valor_bruto": valor_bruto,
        "valor_irrf": _decimal(registro.valor_irrf),
        "valor_liquido": _decimal(registro.valor_liquido),
        "valor_pago": valor_pago,
        "valor_previsto_aberto": valor_previsto_aberto,
        "tem_irrf": tem_irrf,
        "fluxo_irrf_label": "Com IRRF" if tem_irrf else LABEL_SEM_IRRF,
        "reinf_status": _reinf_status_bi(registro.reinf_status_legivel, tem_irrf),
        "url": url_for("cadastros.editar_rpv", registro_id=registro.id),
    }


def _registro_bi_dativo(item: DativoItem) -> dict:
    competencia_cadastro = _competencia_normalizada(
        getattr(getattr(item, "dativo_ci", None), "exercicio", "")
    )
    competencia_pagamento = item.data_pagamento.strftime("%Y-%m") if item.data_pagamento else ""
    competencia = competencia_pagamento or competencia_cadastro
    tem_irrf = item.grupo == "com_irrf"
    tipo_nome = "Dativo com IRRF" if tem_irrf else "Dativo sem IRRF"
    tipo_documento = str(getattr(item, "tipo_documento_efetivo", "") or "Documento").upper()
    dativo_ci = getattr(item, "dativo_ci", None)
    documento_original = item.cpf_original or "-"
    documento_limpo = normalizar_documento(documento_original) or "-"
    documento_formatado = formatar_documento_br(documento_original, tipo_documento) or "-"
    responsavel = item.dativo_ci.responsavel.nome if item.dativo_ci and item.dativo_ci.responsavel else "-"
    grupo_cota = _classificar_grupo_cota(
        tipo_nome,
        "dativo_com_irrf" if tem_irrf else "dativo_sem_irrf",
    )
    meta_grupo = _meta_grupo_cota(grupo_cota)
    valor_bruto = _decimal(item.valor_bruto)
    valor_pago = valor_bruto if item.data_pagamento else Decimal("0.00")
    valor_previsto_aberto = Decimal("0.00") if item.data_pagamento else valor_bruto

    return {
        "origem": tipo_nome,
        "origem_chave": "dativo_com_irrf" if tem_irrf else "dativo_sem_irrf",
        "tipo": tipo_nome,
        "grupo_cota": grupo_cota,
        "grupo_cota_label": meta_grupo["label"],
        "responsavel": responsavel,
        "responsavel_id": item.dativo_ci.responsavel_id if item.dativo_ci else None,
        "competencia": competencia,
        "competencia_legivel": _competencia_legivel(competencia) if competencia else "-",
        "competencia_cadastro": competencia_cadastro,
        "competencia_cadastro_legivel": (
            _competencia_legivel(competencia_cadastro) if competencia_cadastro else "-"
        ),
        "competencia_pagamento": competencia_pagamento,
        "competencia_pagamento_legivel": (
            _competencia_legivel(competencia_pagamento) if competencia_pagamento else "-"
        ),
        "data_pagamento": item.data_pagamento,
        "data_pagamento_legivel": _data_legivel(item.data_pagamento),
        "pagamento_status": "Pago" if item.data_pagamento else "Em carteira",
        "nome": item.nome_beneficiario,
        "nome_normalizado": _normalizar_texto(item.nome_beneficiario),
        "tipo_documento": tipo_documento,
        "documento": documento_original,
        "documento_limpo": documento_limpo,
        "documento_formatado": documento_formatado,
        "documento_normalizado": documento_limpo,
        "processo": item.numero_processo or "-",
        "ci": getattr(dativo_ci, "processo_edoc", "-"),
        "valor_bruto": valor_bruto,
        "valor_irrf": _decimal(item.valor_irrf),
        "valor_liquido": _decimal(item.valor_liquido),
        "valor_pago": valor_pago,
        "valor_previsto_aberto": valor_previsto_aberto,
        "tem_irrf": tem_irrf,
        "fluxo_irrf_label": "Com IRRF" if tem_irrf else LABEL_SEM_IRRF,
        "reinf_status": _reinf_status_bi(item.reinf_status_legivel if tem_irrf else None, tem_irrf),
        "url": _abrir_url_dativo(item),
    }


def _query_registros_bi(
    filtros: dict[str, str] | None = None,
    *,
    visao: str = "operacional",
):
    return BIDatasetService.query_registros(
        filtros,
        visao=visao,
        current_user_id=getattr(current_user, "id", None),
        visao_normalizer=_visao_bi,
        faixa_data_pagamento_resolver=_faixa_data_pagamento_bi,
        filtro_competencia_resolver=_filtro_competencia_operacional_rpv,
        pagamento_normalizer=_normalizar_texto,
    )


def _query_dativos_bi(
    filtros: dict[str, str] | None = None,
    *,
    visao: str = "operacional",
):
    return BIDatasetService.query_dativos(
        filtros,
        visao=visao,
        current_user_id=getattr(current_user, "id", None),
        visao_normalizer=_visao_bi,
        faixa_data_pagamento_resolver=_faixa_data_pagamento_bi,
        filtro_competencia_resolver=_filtro_competencia_operacional_dativo,
        pagamento_normalizer=_normalizar_texto,
    )


def _coletar_dataset_bi(
    filtros: dict[str, str] | None = None,
    *,
    visao: str = "operacional",
    ordenar: bool = False,
) -> list[dict]:
    return BIDatasetService.collect_dataset(
        filtros,
        visao=visao,
        ordenar=ordenar,
        current_user_id=getattr(current_user, "id", None),
        visao_normalizer=_visao_bi,
        faixa_data_pagamento_resolver=_faixa_data_pagamento_bi,
        filtro_competencia_rpv_resolver=_filtro_competencia_operacional_rpv,
        filtro_competencia_dativo_resolver=_filtro_competencia_operacional_dativo,
        pagamento_normalizer=_normalizar_texto,
        map_rpv=_registro_bi_rpv,
        map_dativo=_registro_bi_dativo,
    )


def _filtros_memoria_bi(filtros: dict[str, str] | None) -> dict[str, str]:
    return BIFilterService.memory_filters(
        filtros,
        text_normalizer=_normalizar_texto,
    )


def _filtrar_dataset_bi_em_memoria(
    dataset: list[dict],
    *,
    texto: str,
    grupo_cota: str,
    tipo: str,
    reinf: str,
) -> list[dict]:
    return BIDatasetService.filter_dataset_in_memory(
        dataset,
        texto=texto,
        grupo_cota=grupo_cota,
        tipo=tipo,
        reinf=reinf,
        reinf_matcher=_match_reinf_bi,
        text_normalizer=_normalizar_texto,
    )


def _filtrar_dataset_bi(
    dataset: list[dict],
    filtros: dict[str, str],
    *,
    visao: str = "operacional",
) -> list[dict]:
    filtros_memoria = _filtros_memoria_bi(filtros)
    return BIDatasetService.filter_dataset(
        dataset,
        filtros,
        memory_filters=filtros_memoria,
        reinf_matcher=_match_reinf_bi,
        text_normalizer=_normalizar_texto,
    )


def _carregar_dataset_bi_filtrado(
    filtros: dict[str, str] | None = None,
    *,
    visao: str = "operacional",
    ordenar: bool = False,
) -> tuple[list[dict], list[dict]]:
    return BIDatasetService.load_filtered_dataset(
        filtros,
        visao=visao,
        ordenar=ordenar,
        current_user_id=getattr(current_user, "id", None),
        visao_normalizer=_visao_bi,
        faixa_data_pagamento_resolver=_faixa_data_pagamento_bi,
        filtro_competencia_rpv_resolver=_filtro_competencia_operacional_rpv,
        filtro_competencia_dativo_resolver=_filtro_competencia_operacional_dativo,
        pagamento_normalizer=_normalizar_texto,
        map_rpv=_registro_bi_rpv,
        map_dativo=_registro_bi_dativo,
        memory_filters=_filtros_memoria_bi(filtros or {}),
        reinf_matcher=_match_reinf_bi,
        text_normalizer=_normalizar_texto,
    )


def _aplicar_percentuais(items: list[dict], campo_valor: str = "valor_total") -> list[dict]:
    maior_valor = max((item[campo_valor] for item in items), default=Decimal("0.00"))

    for item in items:
        if maior_valor > 0:
            item["percentual"] = float((item[campo_valor] / maior_valor) * Decimal("100"))
        else:
            item["percentual"] = 0.0

    return items


def _agrupar_por_competencia(
    dataset: list[dict],
    campo_valor: str = "valor_bruto",
    ignorar_zerados: bool = False,
) -> list[dict]:
    agrupado = defaultdict(lambda: {"quantidade": 0, "valor_total": Decimal("0.00")})

    for row in dataset:
        if not row["competencia"]:
            continue
        valor = _decimal(row[campo_valor])
        if ignorar_zerados and valor <= 0:
            continue
        agrupado[row["competencia"]]["quantidade"] += 1
        agrupado[row["competencia"]]["valor_total"] += valor

    serie = [
        {
            "competencia": competencia,
            "label": _competencia_legivel(competencia),
            "quantidade": dados["quantidade"],
            "valor_total": dados["valor_total"],
        }
        for competencia, dados in agrupado.items()
    ]
    serie.sort(key=lambda item: item["competencia"])
    return _aplicar_percentuais(serie)


def _agrupar_por_campo(dataset: list[dict], campo: str, limite: int = 6) -> list[dict]:
    agrupado = defaultdict(lambda: {"quantidade": 0, "valor_total": Decimal("0.00")})

    for row in dataset:
        chave = row[campo] or "-"
        agrupado[chave]["quantidade"] += 1
        agrupado[chave]["valor_total"] += row["valor_bruto"]

    serie = [
        {
            "label": chave,
            "quantidade": dados["quantidade"],
            "valor_total": dados["valor_total"],
        }
        for chave, dados in agrupado.items()
    ]
    serie.sort(
        key=lambda item: (item["valor_total"], item["quantidade"], item["label"]),
        reverse=True,
    )
    return _aplicar_percentuais(serie[:limite])


def _agrupar_por_campo_quantidade(dataset: list[dict], campo: str, limite: int = 6) -> list[dict]:
    agrupado = defaultdict(lambda: {"quantidade": 0, "valor_total": Decimal("0.00")})

    for row in dataset:
        chave = row[campo] or "-"
        agrupado[chave]["quantidade"] += 1
        agrupado[chave]["valor_total"] += row["valor_bruto"]

    serie = [
        {
            "label": chave,
            "quantidade": dados["quantidade"],
            "valor_total": dados["valor_total"],
        }
        for chave, dados in agrupado.items()
    ]
    serie.sort(
        key=lambda item: (item["quantidade"], item["valor_total"], item["label"]),
        reverse=True,
    )
    maior_quantidade = max((item["quantidade"] for item in serie), default=0)
    for item in serie:
        item["percentual"] = (
            float((Decimal(item["quantidade"]) / Decimal(maior_quantidade)) * Decimal("100"))
            if maior_quantidade > 0
            else 0.0
        )
    return serie[:limite]


def _distribuicao_status_pagamento_bi(dataset: list[dict]) -> list[dict]:
    serie = _agrupar_por_campo_quantidade(dataset, "pagamento_status", limite=4)
    total = sum(item["quantidade"] for item in serie)
    for item in serie:
        item["participacao"] = (
            float((Decimal(item["quantidade"]) / Decimal(total)) * Decimal("100"))
            if total > 0
            else 0.0
        )
    return serie


def _percentual_decimal(valor: Decimal, total: Decimal) -> float:
    return float((valor / total) * Decimal("100")) if total > 0 else 0.0


def _serie_donut_bi(items: list[dict]) -> dict:
    total = sum((_decimal(item.get("valor")) for item in items), Decimal("0.00"))
    cursor = 0.0
    partes = []
    serie = []

    for item in items:
        valor = _decimal(item.get("valor"))
        percentual = _percentual_decimal(valor, total)
        inicio = cursor
        fim = min(100.0, cursor + percentual)
        cursor = fim
        cor = item.get("cor") or CORES_GRAFICOS_BI["outros"]
        if percentual > 0:
            partes.append(f"{cor} {inicio:.2f}% {fim:.2f}%")
        serie.append(
            {
                **item,
                "valor": valor,
                "percentual": percentual,
                "cor": cor,
            }
        )

    if not partes:
        partes.append("rgba(188, 201, 206, 0.42) 0% 100%")

    return {
        "total": total,
        "itens": serie,
        "gradient": ", ".join(partes),
        "tem_dados": total > 0,
    }


def _graficos_ciclo_operacional_bi(
    resumo_grupos: dict,
    dativos_competencia: dict,
) -> dict:
    ciclo = _serie_donut_bi(
        [
            {
                "label": "Pago no mes",
                "valor": resumo_grupos["total_mes_pago"],
                "cor": CORES_GRAFICOS_BI["pago"],
                "nota": resumo_grupos["competencia_legivel"],
            },
            {
                "label": "Carteira aberta",
                "valor": resumo_grupos["total_em_aberto"],
                "cor": CORES_GRAFICOS_BI["aberto"],
                "nota": "sem pagamento",
            },
            {
                "label": "Previsao seguinte",
                "valor": resumo_grupos["total_previsao"],
                "cor": CORES_GRAFICOS_BI["previsao"],
                "nota": resumo_grupos["proxima_competencia_legivel"],
            },
        ]
    )
    grupos_mes = _serie_donut_bi(
        [
            {
                "label": grupo["label"],
                "valor": grupo["valor_mes_pago"],
                "quantidade": grupo["quantidade_mes_pago"],
                "cor": CORES_GRAFICOS_BI.get(grupo["chave"], CORES_GRAFICOS_BI["outros"]),
                "nota": f"{grupo['quantidade_mes_pago']} pagamento(s)",
            }
            for grupo in resumo_grupos["grupos"]
        ]
    )
    valor_dativos = _decimal(dativos_competencia.get("total_valor"))
    valor_demais = max(resumo_grupos["total_mes_pago"] - valor_dativos, Decimal("0.00"))
    dativos = _serie_donut_bi(
        [
            {
                "label": "Dativos",
                "valor": valor_dativos,
                "cor": CORES_GRAFICOS_BI["dativos"],
                "nota": f"{dativos_competencia.get('total_quantidade', 0)} pagamento(s)",
            },
            {
                "label": "Demais pagamentos",
                "valor": valor_demais,
                "cor": CORES_GRAFICOS_BI["outros"],
                "nota": resumo_grupos["competencia_legivel"],
            },
        ]
    )
    percentual_previsao = _percentual_decimal(
        resumo_grupos["total_previsao"],
        resumo_grupos["total_mes_pago"],
    )

    return {
        "ciclo": ciclo,
        "grupos_mes": grupos_mes,
        "dativos": dativos,
        "previsao": {
            "competencia": resumo_grupos["proxima_competencia_legivel"],
            "valor": resumo_grupos["total_previsao"],
            "base_mes": resumo_grupos["total_mes_pago"],
            "percentual_vs_mes": percentual_previsao,
            "meter": min(percentual_previsao, 100.0),
        },
    }


def _agrupar_top_credores(dataset: list[dict], limite: int = 8) -> list[dict]:
    agrupado = {}

    for row in dataset:
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
            },
        )
        dados["quantidade"] += 1
        dados["valor_total"] += row["valor_bruto"]

    serie = list(agrupado.values())
    serie.sort(
        key=lambda item: (item["valor_total"], item["quantidade"], item["label"]),
        reverse=True,
    )
    return _aplicar_percentuais(serie[:limite])


def _linhas_dativos_pagos(dataset: list[dict]) -> list[dict]:
    return [
        row
        for row in dataset
        if row["origem_chave"] in {"dativo_com_irrf", "dativo_sem_irrf"} and row["data_pagamento"]
    ]


def _resumo_dativos_competencia(dataset: list[dict], competencia_referencia: str | None = None) -> dict:
    return BIOperationalMetricsService.resumo_dativos_competencia(
        dataset,
        competencia_referencia,
    )


def _resumo_dativos_competencia_projetado(
    projecao: dict,
    competencia_referencia: str | None = None,
) -> dict:
    return BIOperationalMetricsService.resumo_dativos_competencia_projetado(
        projecao,
        competencia_referencia,
    )


def _resumo_pendencias_bi(dataset: list[dict]) -> dict:
    linhas_pagas = _linhas_bi_pagas(dataset)
    linhas_em_aberto = _linhas_bi_em_aberto(dataset)
    reinf_pendentes = [
        row
        for row in linhas_pagas
        if row["tem_irrf"] and not _status_reinf_resolvido(row["reinf_status"])
    ]
    aberto_com_irrf = [row for row in linhas_em_aberto if row["tem_irrf"]]
    aberto_sem_irrf = [row for row in linhas_em_aberto if not row["tem_irrf"]]

    return {
        "itens": [
            {
                "label": "Em carteira",
                "quantidade": len(linhas_em_aberto),
                "valor_total": sum((row["valor_previsto_aberto"] for row in linhas_em_aberto), Decimal("0.00")),
                "nota": "Registros ainda sem pagamento dentro do recorte",
            },
            {
                "label": "Com IRRF em aberto",
                "quantidade": len(aberto_com_irrf),
                "valor_total": sum((row["valor_previsto_aberto"] for row in aberto_com_irrf), Decimal("0.00")),
                "nota": "Carteira que ainda depende de fluxo fiscal",
            },
            {
                "label": f"{LABEL_SEM_IRRF} em aberto",
                "quantidade": len(aberto_sem_irrf),
                "valor_total": sum((row["valor_previsto_aberto"] for row in aberto_sem_irrf), Decimal("0.00")),
                "nota": "Carteira fora da retencao no recorte",
            },
            {
                "label": "REINF pendente",
                "quantidade": len(reinf_pendentes),
                "valor_total": sum((row["valor_irrf"] for row in reinf_pendentes), Decimal("0.00")),
                "nota": "Pagamentos com IRRF que ainda aguardam envio ou decisao",
            },
        ]
    }


def _serie_dativos_ultimas_competencias(dataset: list[dict], limite: int = 6) -> list[dict]:
    return BIOperationalMetricsService.serie_dativos_ultimas_competencias(
        dataset,
        limite=limite,
    )


def _serie_dativos_ultimas_competencias_projetada(projecao: dict, limite: int = 6) -> list[dict]:
    return BIOperationalMetricsService.serie_dativos_ultimas_competencias_projetada(
        projecao,
        limite=limite,
    )


def _top_cis_dativos_pagos(dataset: list[dict], limite: int = 6) -> list[dict]:
    agrupado = defaultdict(lambda: {"quantidade": 0, "valor_total": Decimal("0.00")})

    for row in _linhas_dativos_pagos(dataset):
        chave = row["ci"] or "Sem C.I."
        agrupado[chave]["quantidade"] += 1
        agrupado[chave]["valor_total"] += row["valor_bruto"]

    serie = [
        {
            "label": ci,
            "quantidade": dados["quantidade"],
            "valor_total": dados["valor_total"],
        }
        for ci, dados in agrupado.items()
    ]
    serie.sort(
        key=lambda item: (item["valor_total"], item["quantidade"], item["label"]),
        reverse=True,
    )
    return _aplicar_percentuais(serie[:limite])


def _resumo_grupos_cota(
    dataset: list[dict],
    filtros: dict[str, str] | None = None,
) -> dict:
    return BIOperationalMetricsService.resumo_grupos_cota(dataset, filtros)


def _competencia_referencia_bi_projetada(
    projecao: dict,
    filtros: dict[str, str] | None = None,
) -> str:
    competencia_atual = _competencia_atual()
    competencias_disponiveis = sorted(projecao.get("competencias_disponiveis", []))
    competencias_pagamento = sorted(projecao.get("competencias_pagas", []))
    ultima_competencia_disponivel = (
        competencias_pagamento[-1]
        if competencias_pagamento
        else (competencias_disponiveis[-1] if competencias_disponiveis else "")
    )

    if _filtros_bi_tem_competencia_explicita(filtros):
        return ultima_competencia_disponivel or competencia_atual

    if not ultima_competencia_disponivel:
        return competencia_atual

    return (
        competencia_atual
        if competencia_atual >= ultima_competencia_disponivel
        else ultima_competencia_disponivel
    )


def _resumo_grupos_cota_projetado(
    projecao: dict,
    filtros: dict[str, str] | None = None,
) -> dict:
    return BIOperationalMetricsService.resumo_grupos_cota_projetado(
        projecao,
        filtros,
    )


def _series_grupos_cota_bi(
    dataset: list[dict],
    resumo_grupos: dict,
    *,
    janela_meses: int = 6,
    filtros: dict[str, str] | None = None,
) -> list[dict]:
    return BIOperationalMetricsService.series_grupos_cota(
        dataset,
        resumo_grupos,
        janela_meses=janela_meses,
        filtros=filtros,
    )


def _series_grupos_cota_bi_projetado(
    projecao: dict,
    resumo_grupos: dict,
    *,
    janela_meses: int = 6,
    filtros: dict[str, str] | None = None,
) -> list[dict]:
    return BIOperationalMetricsService.series_grupos_cota_projetado(
        projecao,
        resumo_grupos,
        janela_meses=janela_meses,
        filtros=filtros,
    )


def _serie_mensal_grupos_cota(dataset: list[dict], limite: int = 12) -> list[dict]:
    return BIOperationalMetricsService.serie_mensal_grupos_cota(
        dataset,
        limite=limite,
    )


def _serie_mensal_grupos_cota_projetada(projecao: dict, limite: int = 12) -> list[dict]:
    return BIOperationalMetricsService.serie_mensal_grupos_cota_projetada(
        projecao,
        limite=limite,
    )


def _acumulado_anual_por_grupo(resumo_grupos: dict) -> list[dict]:
    grupos = []
    maior_valor = max(
        (grupo["valor_ano_pago"] for grupo in resumo_grupos["grupos"]),
        default=Decimal("0.00"),
    )

    for grupo in resumo_grupos["grupos"]:
        grupos.append(
            {
                "label": grupo["label"],
                "descricao": grupo["descricao"],
                "valor_total": grupo["valor_ano_pago"],
                "quantidade": grupo["quantidade_ano_pago"],
                "percentual": (
                    float((grupo["valor_ano_pago"] / maior_valor) * Decimal("100"))
                    if maior_valor > 0
                    else 0.0
                ),
                "css_class": grupo["chart_class"],
            }
        )

    return grupos


def _resumo_irrf_bi(
    dataset: list[dict],
    *,
    competencia_referencia: str | None = None,
    janela_meses: int = 6,
) -> dict:
    return BIOperationalMetricsService.resumo_irrf(
        dataset,
        competencia_referencia=competencia_referencia,
        janela_meses=janela_meses,
    )


def _conferencia_bi(dataset: list[dict]) -> dict:
    linhas_pagas = [row for row in dataset if row["competencia_pagamento"]]
    agrupado = defaultdict(
        lambda: {
            "competencia": "",
            "label": "",
            "quantidade": 0,
            "valor_pessoal": Decimal("0.00"),
            "valor_pericial": Decimal("0.00"),
            "valor_comum": Decimal("0.00"),
            "valor_bruto": Decimal("0.00"),
            "valor_pago": Decimal("0.00"),
            "valor_irrf": Decimal("0.00"),
            "valor_liquido": Decimal("0.00"),
            "valor_em_aberto": Decimal("0.00"),
        }
    )
    totais = {
        "quantidade": 0,
        "valor_pessoal": Decimal("0.00"),
        "valor_pericial": Decimal("0.00"),
        "valor_comum": Decimal("0.00"),
        "valor_bruto": Decimal("0.00"),
        "valor_pago": Decimal("0.00"),
        "valor_irrf": Decimal("0.00"),
        "valor_liquido": Decimal("0.00"),
        "valor_em_aberto": Decimal("0.00"),
    }

    for row in linhas_pagas:
        competencia = row["competencia_pagamento"] or "sem-competencia"
        item = agrupado[competencia]
        item["competencia"] = competencia
        item["label"] = (
            _competencia_legivel(competencia)
            if competencia != "sem-competencia"
            else "Sem competencia"
        )
        item["quantidade"] += 1
        item["valor_bruto"] += row["valor_pago"]
        item["valor_pago"] += row["valor_pago"]
        item["valor_irrf"] += row["valor_irrf"]
        item["valor_liquido"] += row["valor_liquido"]

        chave_grupo = f"valor_{row['grupo_cota']}"
        if chave_grupo in item:
            item[chave_grupo] += row["valor_pago"]

        totais["quantidade"] += 1
        totais["valor_bruto"] += row["valor_pago"]
        totais["valor_pago"] += row["valor_pago"]
        totais["valor_irrf"] += row["valor_irrf"]
        totais["valor_liquido"] += row["valor_liquido"]
        if chave_grupo in totais:
            totais[chave_grupo] += row["valor_pago"]

    linhas = list(agrupado.values())
    linhas.sort(
        key=lambda item: (item["competencia"] == "sem-competencia", item["competencia"])
    )
    competencias_validas = [linha["competencia"] for linha in linhas if linha["competencia"] != "sem-competencia"]
    if not competencias_validas:
        periodo_label = "Sem pagamentos"
    elif len(competencias_validas) == 1:
        periodo_label = _competencia_legivel(competencias_validas[0])
    else:
        periodo_label = (
            f"{_competencia_legivel(competencias_validas[0])} a "
            f"{_competencia_legivel(competencias_validas[-1])}"
        )

    grupos = []
    for chave in GRUPOS_COTA_ORDEM:
        meta = _meta_grupo_cota(chave)
        valor_total = totais[f"valor_{chave}"]
        percentual = (
            float((valor_total / totais["valor_bruto"]) * Decimal("100"))
            if totais["valor_bruto"] > 0
            else 0.0
        )
        grupos.append(
            {
                "chave": chave,
                "label": meta["label"],
                "descricao": meta["descricao"],
                "valor_total": valor_total,
                "percentual": percentual,
                "css_class": meta["css_class"],
            }
        )

    return {
        "linhas": linhas,
        "totais": totais,
        "grupos": grupos,
        "total_competencias": len(linhas),
        "tem_dados": bool(linhas),
        "periodo_label": periodo_label,
        "competencia_base": "Pagamento efetivo",
    }


def _conferencia_pendencias_documentais_bi(filtros: dict[str, str]) -> dict:
    query = (
        RPVPendenciaDocumento.query.options(
            joinedload(RPVPendenciaDocumento.tipo_rpv),
            joinedload(RPVPendenciaDocumento.responsavel),
            joinedload(RPVPendenciaDocumento.criado_por),
        )
        .filter(RPVPendenciaDocumento.status == "aberta")
        .order_by(RPVPendenciaDocumento.exercicio.asc(), RPVPendenciaDocumento.criado_em.asc())
    )

    if not current_user.is_admin:
        query = query.filter(
            (RPVPendenciaDocumento.responsavel_id == current_user.id)
            | (RPVPendenciaDocumento.criado_por_id == current_user.id)
        )

    texto = _normalizar_texto(filtros.get("q"))
    documento_busca = normalizar_documento(filtros.get("q"))
    competencia_inicial = _competencia_normalizada(filtros.get("competencia_inicial"))
    competencia_final = _competencia_normalizada(filtros.get("competencia_final"))
    responsavel = str(filtros.get("responsavel") or "todos").strip()
    tipo = _normalizar_texto(filtros.get("tipo"))

    if responsavel == "meus":
        query = query.filter(
            (RPVPendenciaDocumento.responsavel_id == current_user.id)
            | (RPVPendenciaDocumento.criado_por_id == current_user.id)
        )
    elif responsavel not in ("", "todos"):
        try:
            responsavel_id = int(responsavel)
            query = query.filter(
                (RPVPendenciaDocumento.responsavel_id == responsavel_id)
                | (RPVPendenciaDocumento.criado_por_id == responsavel_id)
            )
        except ValueError:
            pass

    pendencias = query.all()
    linhas = []

    for pendencia in pendencias:
        competencia = _competencia_normalizada(pendencia.exercicio)
        if not _competencia_no_intervalo(competencia, competencia_inicial, competencia_final):
            continue

        tipo_nome = getattr(getattr(pendencia, "tipo_rpv", None), "nome", None) or "Sem tipo"
        if tipo and tipo != _normalizar_texto(tipo_nome):
            continue

        campos_busca = [
            pendencia.nome_beneficiario,
            pendencia.processo_edoc,
            pendencia.numero_processo,
            pendencia.documento_original,
            pendencia.documento_normalizado,
            getattr(getattr(pendencia, "responsavel", None), "nome", None),
            getattr(getattr(pendencia, "criado_por", None), "nome", None),
        ]
        if texto and not any(texto in _normalizar_texto(valor) for valor in campos_busca):
            if not documento_busca or documento_busca not in normalizar_documento(pendencia.documento_original):
                continue

        linhas.append(
            {
                "id": pendencia.id,
                "competencia": competencia,
                "competencia_legivel": _competencia_legivel(competencia) if competencia else "-",
                "beneficiario": pendencia.nome_beneficiario,
                "tipo": tipo_nome,
                "documento": pendencia.documento_formatado or pendencia.documento_original or "Sem documento",
                "documento_status": pendencia.documento_status_legivel,
                "motivo": pendencia.motivo_documento_exibido,
                "processo": pendencia.numero_processo,
                "ci": pendencia.processo_edoc,
                "responsavel": getattr(getattr(pendencia, "responsavel", None), "nome", None) or "-",
                "criado_por": getattr(getattr(pendencia, "criado_por", None), "nome", None) or "-",
                "valor_bruto": _decimal(pendencia.valor_bruto),
                "url": url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id),
            }
        )

    linhas.sort(
        key=lambda linha: (
            linha["competencia"] or "",
            _normalizar_texto(linha["beneficiario"]),
            linha["processo"] or "",
        )
    )
    total_valor = sum((linha["valor_bruto"] for linha in linhas), Decimal("0.00"))
    total_sem_documento = sum(1 for linha in linhas if linha["documento"] == "Sem documento")

    return {
        "linhas": linhas,
        "tem_dados": bool(linhas),
        "total_quantidade": len(linhas),
        "total_valor": total_valor,
        "total_sem_documento": total_sem_documento,
    }


def _agrupar_beneficiarios_fluxo_bi(
    dataset: list[dict],
    competencias_limite: int = 4,
) -> list[dict]:
    return BIBeneficiaryService.aggregate_paid_flow(
        dataset,
        paid_rows_loader=_linhas_bi_pagas,
        competencia_labeler=_competencia_legivel,
        percent_applicator=_aplicar_percentuais,
        competencias_limite=competencias_limite,
    )


def _top_beneficiarios_fluxo_mensal(
    dataset: list[dict],
    limite: int = BENEFICIARIOS_BI_DESTAQUE,
    competencias_limite: int = 4,
) -> list[dict]:
    return _agrupar_beneficiarios_fluxo_bi(dataset, competencias_limite=competencias_limite)[:limite]


def _filtrar_beneficiarios_fluxo_bi(serie: list[dict], busca: str | None) -> list[dict]:
    return BIBeneficiaryService.filter_flow(
        serie,
        busca,
        text_normalizer=_normalizar_texto,
        document_normalizer=normalizar_documento,
    )


def _filtrar_beneficiarios_fiscal_bi(serie: list[dict], fiscal: str | None) -> list[dict]:
    return BIBeneficiaryService.filter_fiscal(serie, fiscal)


def _resumo_beneficiarios_fiscal_bi(serie: list[dict]) -> dict:
    return BIBeneficiaryService.summary_fiscal(serie)


def _exploracao_beneficiarios_fluxo_bi(
    serie_completa: list[dict],
    *,
    busca: str | None,
    pagina: int,
    fiscal: str | None = "todos",
    destaque: int = BENEFICIARIOS_BI_DESTAQUE,
    pagina_tamanho: int = BENEFICIARIOS_BI_POR_PAGINA,
) -> dict:
    return BIBeneficiaryService.exploration(
        serie_completa,
        busca=busca,
        pagina=pagina,
        fiscal=fiscal,
        destaque=destaque,
        pagina_tamanho=pagina_tamanho,
        fiscal_options=BENEFICIARIOS_BI_FISCAL,
        integer_normalizer=_inteiro_positivo,
        text_normalizer=_normalizar_texto,
        document_normalizer=normalizar_documento,
    )


def _cards_bi(
    dataset: list[dict],
    resumo_grupos: dict | None = None,
    resumo_dativos: dict | None = None,
) -> list[dict]:
    resumo_grupos = resumo_grupos or _resumo_grupos_cota(dataset)
    resumo_dativos = resumo_dativos or _resumo_dativos_competencia(
        dataset,
        resumo_grupos.get("competencia_referencia"),
    )
    linhas_pagas = _linhas_bi_pagas(dataset)
    linhas_em_aberto = _linhas_bi_em_aberto(dataset)
    total_reinf_pendente = sum(
        1
        for row in linhas_pagas
        if row["tem_irrf"] and not _status_reinf_resolvido(row["reinf_status"])
    )
    total_beneficiarios_abertos = len(
        {
            row["documento_normalizado"] or row["nome_normalizado"]
            for row in linhas_em_aberto
            if row["documento_normalizado"] or row["nome_normalizado"]
        }
    )

    return [
        {
            "label": f"Pago em {resumo_grupos['competencia_legivel']}",
            "valor": resumo_grupos["total_mes_pago"],
            "tipo": "moeda",
            "nota": "Somente valores efetivamente pagos na competencia",
        },
        {
            "label": f"Pago em {resumo_grupos['ano_referencia']}",
            "valor": resumo_grupos["total_ano_pago"],
            "tipo": "moeda",
            "nota": "Acumulado anual de pagamentos realizados",
        },
        {
            "label": "Carteira em aberto",
            "valor": resumo_grupos["total_em_aberto"],
            "tipo": "moeda",
            "nota": "Valores cadastrados sem pagamento realizado no recorte",
        },
        {
            "label": f"Dativos pagos em {resumo_dativos['competencia_legivel']}",
            "valor": resumo_dativos["total_valor"],
            "tipo": "moeda",
            "nota": "Quanto dos pagamentos do mes veio de dativos",
        },
        {
            "label": f"Projecao {resumo_grupos['proxima_competencia_legivel']}",
            "valor": resumo_grupos["total_previsao"],
            "tipo": "moeda",
            "nota": "Media simples das 3 ultimas competencias pagas",
        },
        {
            "label": "Beneficiarios em aberto",
            "valor": total_beneficiarios_abertos,
            "tipo": "numero",
            "nota": "Beneficiarios ainda em carteira, sem data de pagamento",
        },
        {
            "label": "REINF pendente",
            "valor": total_reinf_pendente,
            "tipo": "numero",
            "nota": "Pagamentos com IRRF realizados e ainda nao resolvidos",
        },
    ]


def _montar_contexto_bi_principal(
    filtros: dict[str, str],
    *,
    visao_bi: str,
) -> dict:
    return BIContextService.build_main_context(
        filtros=filtros,
        visao_bi=visao_bi,
        current_user_id=getattr(current_user, "id", None),
        dataset_loader=_carregar_dataset_bi_filtrado,
        janela_loader=_janela_meses_bi,
        calculators=_calculadoras_contexto_bi_principal(),
        url_builders=_url_builders_contexto_bi_principal(),
        constants={
            "visao_labels": VISOES_BI_LABELS,
            "visao_navegacao": VISOES_BI_NAVEGACAO,
            "origem_opcoes": ORIGENS_BI,
            "pagamento_opcoes": PAGAMENTO_BI,
            "grupo_cota_opcoes": GRUPOS_COTA_OPCOES,
            "janela_opcoes": JANELAS_GRAFICO_BI,
            "beneficiarios_destaque": BENEFICIARIOS_BI_DESTAQUE,
        },
    )


def _calculadoras_contexto_bi_principal() -> dict[str, Callable]:
    return {
        "resumo_grupos": _resumo_grupos_cota,
        "resumo_grupos_projetado": _resumo_grupos_cota_projetado,
        "series_grupos": _series_grupos_cota_bi,
        "series_grupos_projetado": _series_grupos_cota_bi_projetado,
        "serie_mensal_grupos": _serie_mensal_grupos_cota,
        "serie_mensal_grupos_projetada": _serie_mensal_grupos_cota_projetada,
        "resumo_dativos": _resumo_dativos_competencia,
        "resumo_dativos_projetado": _resumo_dativos_competencia_projetado,
        "serie_dativos": _serie_dativos_ultimas_competencias,
        "serie_dativos_projetada": _serie_dativos_ultimas_competencias_projetada,
        "conferencia": _conferencia_bi,
        "conferencia_pendencias_documentais": _conferencia_pendencias_documentais_bi,
        "cards": _cards_bi,
        "resumo_irrf": _resumo_irrf_bi,
        "acumulado_anual": _acumulado_anual_por_grupo,
        "pendencias": _resumo_pendencias_bi,
        "graficos_ciclo": _graficos_ciclo_operacional_bi,
        "sinais_operacionais": _sinais_operacionais_bi,
        "agrupar_beneficiarios_fluxo": _agrupar_beneficiarios_fluxo_bi,
        "periodo_pagamentos": _periodo_pagamentos_bi,
        "agrupar_por_campo": _agrupar_por_campo,
        "agrupar_por_campo_quantidade": _agrupar_por_campo_quantidade,
        "linhas_bi_pagas": _linhas_bi_pagas,
        "distribuicao_pagamento": _distribuicao_status_pagamento_bi,
    }


def _url_builders_contexto_bi_principal() -> dict[str, Callable]:
    return {
        "visao": _url_bi_com_filtros,
        "janela": _url_bi_com_filtros,
        "beneficiarios": _url_bi_beneficiarios_atalho,
    }


def _calculadoras_contexto_bi_beneficiarios() -> dict[str, Callable]:
    return {
        "agrupar_beneficiarios_fluxo": _agrupar_beneficiarios_fluxo_bi,
        "resumo_beneficiarios": _resumo_beneficiarios_fiscal_bi,
        "exploracao_beneficiarios": _exploracao_beneficiarios_fluxo_bi,
        "periodo_pagamentos": _periodo_pagamentos_bi,
    }


def _url_builders_contexto_bi_beneficiarios() -> dict[str, Callable]:
    return {
        "bi": _url_bi_com_filtros,
        "beneficiarios_pagina": _url_bi_beneficiarios_pagina,
    }


def _sinais_operacionais_bi(
    dataset: list[dict],
    resumo_grupos: dict,
    dativos_competencia: dict,
) -> dict:
    linhas_pagas = _linhas_bi_pagas(dataset)
    linhas_em_aberto = _linhas_bi_em_aberto(dataset)
    total_reinf_pendente = sum(
        1
        for row in linhas_pagas
        if row["tem_irrf"] and not _status_reinf_resolvido(row["reinf_status"])
    )
    beneficiarios_abertos = len(
        {
            row["documento_normalizado"] or row["nome_normalizado"]
            for row in linhas_em_aberto
            if row["documento_normalizado"] or row["nome_normalizado"]
        }
    )
    base_carteira = resumo_grupos["total_mes_pago"] + resumo_grupos["total_em_aberto"]
    percentual_previsao = _percentual_decimal(
        resumo_grupos["total_previsao"],
        resumo_grupos["total_mes_pago"],
    )

    return {
        "previsao": {
            "label": f"Projecao {resumo_grupos['proxima_competencia_legivel']}",
            "valor": resumo_grupos["total_previsao"],
            "percentual_vs_mes": percentual_previsao,
            "meter": min(percentual_previsao, 100.0),
            "nota": "Media simples das 3 ultimas competencias pagas.",
        },
        "carteira": {
            "valor": resumo_grupos["total_em_aberto"],
            "beneficiarios": beneficiarios_abertos,
            "percentual": min(
                _percentual_decimal(resumo_grupos["total_em_aberto"], base_carteira),
                100.0,
            ),
            "nota": "Valores cadastrados sem pagamento realizado no recorte.",
        },
        "ano": {
            "valor": resumo_grupos["total_ano_pago"],
            "ano": resumo_grupos["ano_referencia"],
            "nota": "Acumulado anual de pagamentos realizados.",
        },
        "dativos": {
            "valor": dativos_competencia["total_valor"],
            "quantidade": dativos_competencia["total_quantidade"],
            "competencia": dativos_competencia["competencia_legivel"],
        },
        "reinf": {
            "pendente": total_reinf_pendente,
            "nota": "Pagamentos com IRRF ainda nao resolvidos na REINF.",
        },
    }


def _resumo_pendencias_documentais_home(
    pendencias_documentais: list[RPVPendenciaDocumento],
) -> dict:
    abertas = [
        pendencia
        for pendencia in pendencias_documentais
        if pendencia.status == "aberta"
    ]
    minhas_abertas = [
        pendencia
        for pendencia in abertas
        if (
            pendencia.responsavel_id == current_user.id
            or pendencia.criado_por_id == current_user.id
        )
    ]
    total_abertas = len(abertas)
    total_prontas = sum(1 for pendencia in abertas if pendencia.pode_continuar_fluxo_oficial)
    total_sem_documento = sum(1 for pendencia in abertas if pendencia.documento_ausente)
    total_minhas = len(minhas_abertas)
    total_valor_aberto = sum(
        (_decimal(pendencia.valor_bruto) for pendencia in abertas),
        Decimal("0.00"),
    )
    sem_pendencias = total_abertas == 0

    if sem_pendencias:
        titulo = "Nenhum documento fora do fluxo oficial"
        nota = (
            "A fila setorial esta limpa agora. Se surgir um caso com documento pendente, "
            "ele aparece aqui para todo o setor."
        )
    else:
        titulo = f"{total_abertas} documento(s) fora do fluxo oficial"
        nota = (
            "Casos que ainda nao entram em pagamento, BI ou REINF ate validar o documento. "
            f"{total_minhas} estao na sua fila atual."
        )

    metricas = [
        {
            "label": "Em revisao",
            "valor": total_abertas,
            "css_class": "is-total",
        },
        {
            "label": "Prontas para oficializar",
            "valor": total_prontas,
            "css_class": "is-success",
        },
        {
            "label": "Sem CPF/CNPJ",
            "valor": total_sem_documento,
            "css_class": "is-warning",
        },
        {
            "label": "Na sua fila",
            "valor": total_minhas,
            "css_class": "is-neutral",
        },
    ]

    return {
        "total_abertas": total_abertas,
        "total_prontas": total_prontas,
        "total_sem_documento": total_sem_documento,
        "total_minhas": total_minhas,
        "total_valor_aberto": total_valor_aberto,
        "sem_pendencias": sem_pendencias,
        "titulo": titulo,
        "nota": nota,
        "metricas": metricas,
        "url_setor": url_for(
            "cadastros.lista_pendencias_documentais",
            responsavel="todos",
            status="aberta",
        ),
    }


def _fila_operacional_home(
    *,
    rpvs: list[RegistroRPV],
    dativo_cis: list[DativoCI],
    lotes_sem_irrf: list[DativoLote],
    dativo_items: list[DativoItem],
    pendencias_documentais: list[RPVPendenciaDocumento],
) -> dict:
    meus_rpvs = [
        registro
        for registro in rpvs
        if registro.elaborador_id == current_user.id and _situacao_exige_continuidade(registro.situacao_empenho)
    ]
    meus_lotes = [
        lote
        for lote in lotes_sem_irrf
        if lote.responsavel_id == current_user.id and _situacao_exige_continuidade(lote.situacao_rpv)
    ]
    meus_itens = [
        item
        for item in dativo_items
        if item.grupo == "com_irrf"
        and item.responsavel_id == current_user.id
        and _situacao_exige_continuidade(item.situacao_rpv)
    ]
    minhas_pendencias_documentais = [
        pendencia
        for pendencia in pendencias_documentais
        if (
            pendencia.status == "aberta"
            and (
                pendencia.responsavel_id == current_user.id
                or pendencia.criado_por_id == current_user.id
            )
        )
    ]
    meus_cabecalhos_dativos = []
    ids_cabecalhos_dativos = set()
    for dativo_ci in dativo_cis:
        if dativo_ci.id in ids_cabecalhos_dativos:
            continue
        if getattr(dativo_ci, "possui_movimentacao_ativa", False):
            continue

        status_ci = str(getattr(dativo_ci, "status", "aberta") or "aberta").strip().casefold()
        relaciona_comigo = (
            dativo_ci.responsavel_id == current_user.id
            or dativo_ci.criado_por_id == current_user.id
        )
        if not relaciona_comigo:
            continue

        if status_ci in {"aberta", "descartada"}:
            meus_cabecalhos_dativos.append(dativo_ci)
            ids_cabecalhos_dativos.add(dativo_ci.id)

    itens = [
        {
            "label": "RPVs normais",
            "quantidade": len(meus_rpvs),
            "valor_total": sum((_decimal(registro.valor_bruto) for registro in meus_rpvs), Decimal("0.00")),
            "nota": "Fluxo normal que ainda depende de andamento",
            "nota_zero": "Nenhum RPV normal pendente sob sua responsabilidade agora.",
            "url": url_for("cadastros.lista_rpvs"),
            "acao": "Abrir RPVs normais",
        },
        {
            "label": "Dativos sem IRRF",
            "quantidade": len(meus_lotes),
            "valor_total": sum((_decimal(lote.valor_total_bruto) for lote in meus_lotes), Decimal("0.00")),
            "nota": "Lotes sob sua responsabilidade ainda em andamento",
            "nota_zero": "Nenhum lote sem IRRF pendente sob sua responsabilidade agora.",
            "url": url_for("dativos.lotes_sem_irrf"),
            "acao": "Abrir lotes",
        },
        {
            "label": "Dativos com IRRF",
            "quantidade": len(meus_itens),
            "valor_total": sum((_decimal(item.valor_bruto) for item in meus_itens), Decimal("0.00")),
            "nota": "Itens individuais que seguem no fluxo fiscal",
            "nota_zero": "Nenhum item com IRRF pendente sob sua responsabilidade agora.",
            "url": url_for("dativos.itens_com_irrf"),
            "acao": "Abrir itens",
        },
        {
            "label": "Dativos sem continuidade",
            "quantidade": len(meus_cabecalhos_dativos),
            "valor_total": Decimal("0.00"),
            "nota": "C.I.s abertas sem continuidade, fora da sua fila ou canceladas antes do fluxo oficial",
            "nota_zero": "Nenhum dativo sem continuidade pendente agora.",
            "url": url_for("dativos.cabecalhos_em_revisao"),
            "acao": "Abrir fila",
            "kpi_secundaria": "Fila direta",
        },
        {
            "label": "Pendencias documentais",
            "quantidade": len(minhas_pendencias_documentais),
            "valor_total": sum(
                (_decimal(pendencia.valor_bruto) for pendencia in minhas_pendencias_documentais),
                Decimal("0.00"),
            ),
            "nota": "RPVs normais fora do fluxo oficial ate corrigir ou confirmar o documento",
            "nota_zero": "Nenhuma pendencia documental sob sua responsabilidade agora.",
            "url": url_for("cadastros.lista_pendencias_documentais"),
            "acao": "Abrir pendencias",
        },
    ]

    for item in itens:
        item["sem_pendencias"] = item["quantidade"] == 0
        item["nota_exibida"] = item["nota_zero"] if item["sem_pendencias"] else item["nota"]

    return {
        "itens": itens,
        "total_quantidade": sum(item["quantidade"] for item in itens),
        "total_valor": sum((item["valor_total"] for item in itens), Decimal("0.00")),
    }


def _url_bi_com_filtros(filtros: dict[str, str], **updates) -> str:
    parametros = {
        chave: valor
        for chave, valor in filtros.items()
        if valor not in ("", None)
    }
    for chave, valor in updates.items():
        if valor in ("", None):
            parametros.pop(chave, None)
        else:
            parametros[chave] = str(valor)
    return url_for("dashboard.bi", **parametros)


def _filtros_bi_da_requisicao() -> dict[str, str]:
    return BIFilterService.normalize_main_filters(
        request.args,
        visao_normalizer=_visao_bi,
        competencia_normalizer=_competencia_normalizada,
        janela_normalizer=_janela_meses_bi,
    )


@dashboard_bp.route("/")
@login_required
def index():
    from app.routes.reinf import _coletar_base_reinf, _resolver_competencia_reinf

    competencia_atual = _competencia_atual()
    competencia_atual_legivel = _competencia_legivel(competencia_atual)
    resolucao_competencia_reinf = _resolver_competencia_reinf(None)
    competencia_reinf_fechamento = (
        resolucao_competencia_reinf["competencia_aplicada"] or competencia_atual
    )
    competencia_reinf_fechamento_legivel = _competencia_legivel(competencia_reinf_fechamento)

    lotes_sem_irrf = (
        DativoLote.query.options(
            joinedload(DativoLote.dativo_ci).joinedload(DativoCI.responsavel),
            joinedload(DativoLote.situacao_rpv),
        )
        .filter_by(tipo_lote="sem_irrf")
        .order_by(DativoLote.criado_em.desc())
        .all()
    )
    rpvs = (
        RegistroRPV.query.options(
            joinedload(RegistroRPV.tipo_rpv),
            joinedload(RegistroRPV.situacao_imposto),
            joinedload(RegistroRPV.situacao_empenho),
            joinedload(RegistroRPV.processo),
            joinedload(RegistroRPV.elaborador),
        )
        .filter_by(ativo=True)
        .order_by(RegistroRPV.criado_em.desc())
        .all()
    )
    pendencias_documentais = (
        RPVPendenciaDocumento.query.options(
            joinedload(RPVPendenciaDocumento.tipo_rpv),
            joinedload(RPVPendenciaDocumento.responsavel),
        )
        .filter_by(status="aberta")
        .order_by(RPVPendenciaDocumento.criado_em.desc())
        .all()
    )
    dativo_items = (
        DativoItem.query.options(
            joinedload(DativoItem.dativo_ci).joinedload(DativoCI.responsavel),
            joinedload(DativoItem.dativo_lote),
            joinedload(DativoItem.situacao_rpv),
        )
        .filter_by(ativo=True)
        .order_by(DativoItem.criado_em.desc())
        .all()
    )

    alertas_irrf = []

    for registro in rpvs:
        if getattr(registro, "status_principal_cancelado", False):
            continue

        nome_tipo = getattr(getattr(registro, "tipo_rpv", None), "nome", None) or "Sem tipo"
        valor_bruto = _decimal(registro.valor_bruto)

        if _rpv_precisa_alerta_irrf(registro):
            alertas_irrf.append(
                {
                    "titulo": registro.nome_beneficiario,
                    "origem": "RPV normal",
                    "descricao": nome_tipo,
                    "meta": f"Processo {getattr(getattr(registro, 'processo', None), 'numero_processo', '-')}",
                    "responsavel": _nome_responsavel_home(getattr(registro, "elaborador", None)),
                    "acao": "Revisar IRRF",
                    "valor": valor_bruto,
                    "url": url_for("cadastros.editar_rpv", registro_id=registro.id),
                }
            )

    for item in dativo_items:
        if getattr(item, "status_principal_cancelado", False):
            continue

        valor_bruto = _decimal(item.valor_bruto)

        if _item_dativo_sem_irrf_precisa_alerta(item):
            alertas_irrf.append(
                {
                    "titulo": item.nome_beneficiario,
                    "origem": "Dativo sem IRRF",
                    "descricao": f"{item.tipo_documento_efetivo} {item.documento_formatado or '-'}",
                    "meta": (
                        f"C.I. {getattr(getattr(item, 'dativo_ci', None), 'processo_edoc', '-')}"
                        f" | Processo {item.numero_processo or '-'}"
                    ),
                    "responsavel": _nome_responsavel_home(getattr(item, "responsavel", None)),
                    "acao": "Revisar beneficiário",
                    "valor": valor_bruto,
                    "url": _abrir_url_dativo(item),
                }
            )

    fila_operacional = _fila_operacional_home(
        rpvs=rpvs,
        dativo_cis=(
            DativoCI.query.options(
                joinedload(DativoCI.responsavel),
                joinedload(DativoCI.criado_por),
                joinedload(DativoCI.lotes),
                joinedload(DativoCI.itens),
            )
            .order_by(DativoCI.criado_em.desc())
            .all()
        ),
        lotes_sem_irrf=[lote for lote in lotes_sem_irrf if not getattr(lote, "status_principal_cancelado", False)],
        dativo_items=[item for item in dativo_items if not getattr(item, "status_principal_cancelado", False)],
        pendencias_documentais=pendencias_documentais,
    )
    pendencias_documentais_setor = _resumo_pendencias_documentais_home(
        pendencias_documentais
    )
    cotas_home = CotasRPVService.resumo_home(competencia=competencia_atual)
    resumo_setor_rpvs = _resumo_setor_rpvs_home(rpvs=rpvs)
    resumo_setor_dativos = _resumo_setor_dativos_home(
        lotes_sem_irrf=lotes_sem_irrf,
        dativo_items=dativo_items,
    )
    total_responsaveis_sinalizados = len(
        {
            item["responsavel_id"]
            for item in resumo_setor_rpvs["usuarios"] + resumo_setor_dativos["usuarios"]
        }
    )

    alertas_irrf.sort(key=lambda item: (item["valor"], item["titulo"]), reverse=True)
    registros_reinf_mes = _coletar_base_reinf(competencia_reinf_fechamento, "todos", "")
    total_reinf_mes = len(registros_reinf_mes)
    total_reinf_mes_concluido = sum(
        1 for registro in registros_reinf_mes if _status_reinf_concluido(registro["reinf_status"])
    )
    total_reinf_mes_cancelado = sum(
        1 for registro in registros_reinf_mes if _status_reinf_cancelado(registro["reinf_status"])
    )
    total_reinf_mes_pendente = sum(
        1 for registro in registros_reinf_mes if not _status_reinf_resolvido(registro["reinf_status"])
    )
    valor_irrf_mes = sum(
        (_decimal(registro["imposto"]) for registro in registros_reinf_mes),
        Decimal("0.00"),
    )
    total_alertas_irrf = len(alertas_irrf)
    alertas_exibidos = alertas_irrf[:6]

    prestacao_mensal = {
        "competencia_legivel": competencia_reinf_fechamento_legivel,
        "total_registros": total_reinf_mes,
        "concluidos": total_reinf_mes_concluido,
        "cancelados": total_reinf_mes_cancelado,
        "pendentes": total_reinf_mes_pendente,
        "valor_irrf": valor_irrf_mes,
        "url": url_for("reinf.index", competencia=competencia_reinf_fechamento),
    }

    return render_template(
        "dashboard/index.html",
        usuario=current_user,
        competencia_atual_legivel=competencia_atual_legivel,
        fila_operacional=fila_operacional,
        pendencias_documentais_setor=pendencias_documentais_setor,
        cotas_home=cotas_home,
        resumo_setor_rpvs=resumo_setor_rpvs,
        resumo_setor_dativos=resumo_setor_dativos,
        total_responsaveis_sinalizados=total_responsaveis_sinalizados,
        alertas_irrf=alertas_exibidos,
        total_alertas_irrf=total_alertas_irrf,
        prestacao_mensal=prestacao_mensal,
    )


@dashboard_bp.route("/bi")
@login_required
def bi():
    filtros = _filtros_bi_da_requisicao()
    visao_bi = _visao_bi(filtros.get("visao"))
    return render_template(
        "dashboard/bi.html",
        **_montar_contexto_bi_principal(filtros, visao_bi=visao_bi),
    )


@dashboard_bp.route("/bi/beneficiarios")
@login_required
def bi_beneficiarios():
    filtros = _filtros_bi_da_requisicao()
    filtros_beneficiarios = _filtros_beneficiarios_bi_da_requisicao()
    visao_bi = _visao_bi(filtros.get("visao"))
    return render_template(
        "dashboard/beneficiarios.html",
        **BIContextService.build_beneficiaries_context(
            filtros=filtros,
            filtros_beneficiarios=filtros_beneficiarios,
            visao_bi=visao_bi,
            dataset_loader=_carregar_dataset_bi_filtrado,
            calculators=_calculadoras_contexto_bi_beneficiarios(),
            url_builders=_url_builders_contexto_bi_beneficiarios(),
            constants={
                "origem_opcoes": ORIGENS_BI,
                "pagamento_opcoes": PAGAMENTO_BI,
                "grupo_cota_opcoes": GRUPOS_COTA_OPCOES,
                "fiscal_opcoes": BENEFICIARIOS_BI_FISCAL,
                "beneficiarios_por_pagina": BENEFICIARIOS_BI_POR_PAGINA,
            },
        ),
    )


@dashboard_bp.route("/bi/exportar.csv")
@login_required
def exportar_bi_csv():
    filtros = _filtros_bi_da_requisicao()
    _, dataset = _carregar_dataset_bi_filtrado(filtros, ordenar=True)
    exportacao = BIExportService.build_operational_csv(
        dataset,
        decimal_formatter=_decimal_csv,
        file_date=date.today(),
    )
    return Response(
        exportacao["content"],
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{exportacao["filename"]}"'},
    )


@dashboard_bp.route("/bi/conferencia.csv")
@login_required
def exportar_bi_conferencia_csv():
    filtros = _filtros_bi_da_requisicao()
    _, dataset = _carregar_dataset_bi_filtrado(
        filtros,
        visao="conferencia",
        ordenar=True,
    )
    conferencia = _conferencia_bi(dataset)
    exportacao = BIExportService.build_conference_csv(
        conferencia,
        decimal_formatter=_decimal_csv,
        file_date=date.today(),
    )
    return Response(
        exportacao["content"],
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{exportacao["filename"]}"'},
    )

