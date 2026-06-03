from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
from unidecode import unidecode

from app.extensions import db
from app.models import DativoCI, DativoItem, HistoricoAlteracao, RegistroRPV, User
from app.services.audit_service import registrar_evento, snapshot_entidade
from app.services.reinf_context_service import ReinfContextService
from app.services.reinf_dataset_service import ReinfDatasetService
from app.services.reinf_export_service import ReinfExportService
from app.services.reinf_filter_service import ReinfFilterService
from app.utils.formatters import formatar_documento_br
from app.utils.navigation import current_internal_url, sanitize_internal_return_url
from app.utils.normalizers import normalizar_documento
from app.utils.reinf_rules import (
    REINF_STATUS_NAO_ENVIADO,
    reinf_status_eh_concluido,
    reinf_status_esta_resolvido,
    resolver_reinf_status,
)
from app.utils.workbench import (
    build_page_window,
    merge_query_params,
    paginate_items,
    parse_page,
    parse_page_size,
    resolve_next_sort_direction,
    sanitize_sort_direction,
)

reinf_bp = Blueprint("reinf", __name__, url_prefix="/reinf")

REINF_STATUS_OPCOES = ["Não enviado", "Concluído", "Cancelado"]
REINF_STATUS_FILTROS = [
    ("todos", "Todos"),
    ("Não enviado", "Não enviado"),
    ("Concluído", "Concluído"),
    ("Cancelado", "Cancelado"),
]
REINF_ORDENACAO_OPCOES = [
    ("data_pagamento", "Data de pagamento"),
    ("beneficiario", "Beneficiário"),
    ("origem", "Origem"),
    ("competencia", "Competência"),
    ("imposto", "IRRF"),
    ("status_reinf", "Status REINF"),
]
REINF_DIRECAO_OPCOES = [
    ("asc", "Crescente"),
    ("desc", "Decrescente"),
]
REINF_VISOES = {
    "operacional": "Operacao mensal",
    "conferencia_mensal": "Conferencia mensal",
    "conferencia_anual": "Conferencia anual",
}
REINF_ORDENACAO_PADRAO = "beneficiario"
REINF_DIRECAO_PADRAO = "asc"
REINF_CONFERENCIA_MENSAL_ACAO = "Conferencia REINF mensal"
REINF_CONFERENCIA_MENSAL_RESUMO = "Processo conferido na leitura mensal da REINF."
REINF_CONFERENCIA_MENSAL_RESUMO_REMOVIDO = "Processo desmarcado na leitura mensal da REINF."


def _url_retorno_interna(padrao: str) -> str:
    return sanitize_internal_return_url(request.values.get("retorno"), padrao)


def _competencia_mes_atual() -> str:
    return date.today().strftime("%Y-%m")


def _competencia_legivel(valor: str | None) -> str:
    competencia = str(valor or "").strip()
    if len(competencia) == 7 and "-" in competencia:
        ano, mes = competencia.split("-", 1)
        meses = {
            "01": "janeiro",
            "02": "fevereiro",
            "03": "março",
            "04": "abril",
            "05": "maio",
            "06": "junho",
            "07": "julho",
            "08": "agosto",
            "09": "setembro",
            "10": "outubro",
            "11": "novembro",
            "12": "dezembro",
        }
        return f"{meses.get(mes, mes)}/{ano}"
    return competencia or "-"


def _data_hora_legivel(valor) -> str:
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y as %H:%M")
    return "-"


def _normalizar_texto(valor: str | None) -> str:
    return unidecode(str(valor or "").strip()).lower()


def _visao_reinf_valida(valor: str | None) -> str:
    chave = str(valor or "").strip().lower()
    if chave in REINF_VISOES:
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


def _situacao_indica_fluxo_irrf(nome_situacao: str | None) -> bool:
    return _normalizar_texto(nome_situacao) not in {"", "sem irrf"}


def _status_reinf_concluido(status: str | None) -> bool:
    return reinf_status_eh_concluido(status)


def _status_reinf_resolvido(status: str | None) -> bool:
    return reinf_status_esta_resolvido(status)


def _entidade_tipo_reinf_por_origem(origem: str) -> str:
    valor = str(origem or "").strip()
    if valor == "rpv":
        return "registro_rpv"
    if valor == "dativo_item":
        return "dativo_item"
    raise ValueError("Origem invalida para a conferencia da REINF.")


def _carregar_entidade_conferencia_reinf(origem: str, registro_id: int):
    valor = str(origem or "").strip()
    if valor == "rpv":
        entidade = db.session.get(RegistroRPV, registro_id)
    elif valor == "dativo_item":
        entidade = db.session.get(DativoItem, registro_id)
    else:
        raise ValueError("Origem invalida para a conferencia da REINF.")

    if not entidade or not getattr(entidade, "ativo", True):
        raise ValueError("Processo da conferencia REINF nao encontrado.")

    return entidade


def _evento_conferencia_reinf_mensal(entidade_tipo: str, entidade_id: int):
    return (
        HistoricoAlteracao.query.options(joinedload(HistoricoAlteracao.alterado_por))
        .filter_by(
            entidade_tipo=entidade_tipo,
            entidade_id=entidade_id,
            acao=REINF_CONFERENCIA_MENSAL_ACAO,
        )
        .order_by(HistoricoAlteracao.criado_em.desc(), HistoricoAlteracao.id.desc())
        .first()
    )


def _estado_evento_conferencia_reinf_mensal(evento: HistoricoAlteracao | None) -> bool:
    if not evento:
        return False

    for alteracao in evento.alteracoes:
        if str(alteracao.get("campo") or "").strip() != "conferencia_reinf_mensal":
            continue

        valor_para = _normalizar_texto(alteracao.get("para"))
        if valor_para in {"conferido", "marcado", "sim", "true", "1"}:
            return True
        if valor_para in {"pendente", "desmarcado", "nao", "false", "0"}:
            return False

    resumo = _normalizar_texto(getattr(evento, "resumo", None))
    if "desmarcado" in resumo or "removido" in resumo:
        return False
    return True


def _detalhes_conferencia_reinf_mensal(antes: bool, depois: bool) -> list[dict]:
    return [
        {
            "campo": "conferencia_reinf_mensal",
            "label": "Conferencia REINF mensal",
            "de": "Conferido" if antes else "Pendente",
            "para": "Conferido" if depois else "Pendente",
        }
    ]


def _estado_revisado_formulario(valor: str | None) -> bool:
    texto = _normalizar_texto(valor)
    if texto in {"0", "false", "nao", "off"}:
        return False
    return True


def _meta_evento_conferencia_reinf_mensal(evento: HistoricoAlteracao | None) -> dict:
    revisado = _estado_evento_conferencia_reinf_mensal(evento)
    if not evento or not revisado:
        return {
            "revisado": False,
            "revisado_por": "",
            "revisado_em": None,
            "revisado_em_legivel": "",
            "revisado_meta": "",
        }

    revisado_por = getattr(getattr(evento, "alterado_por", None), "nome", None) or "Nao informado"
    revisado_em_legivel = _data_hora_legivel(evento.criado_em)
    return {
        "revisado": revisado,
        "revisado_por": revisado_por,
        "revisado_em": evento.criado_em,
        "revisado_em_legivel": revisado_em_legivel,
        "revisado_meta": f"Conferido por {revisado_por} em {revisado_em_legivel}",
    }


def _rotulo_progresso_conferencia(revisados: int, total: int) -> str:
    if revisados <= 0:
        return "Por conferir"
    if total > 0 and revisados == total:
        return "beneficiario fechado"
    return "processos conferidos"


def _resumo_conferencia_beneficiarios(revisados: int, total: int) -> str:
    if revisados <= 0 or total <= 0:
        return "Nenhum conferido"
    return f"{revisados} de {total} conferido(s)"


def _resumo_conferencia_processos(revisados: int, total: int) -> str:
    if revisados <= 0 or total <= 0:
        return "Nenhum processo conferido"
    return f"{revisados} de {total} processo(s) conferido(s)"


def _mapa_conferencia_reinf_mensal(registros: list[dict]) -> dict[tuple[str, int], dict]:
    ids_rpv = sorted(
        {
            int(registro["registro_id"])
            for registro in registros
            if str(registro.get("origem") or "").strip() == "rpv" and registro.get("registro_id")
        }
    )
    ids_dativos = sorted(
        {
            int(registro["registro_id"])
            for registro in registros
            if str(registro.get("origem") or "").strip() == "dativo_item" and registro.get("registro_id")
        }
    )

    mapa: dict[tuple[str, int], dict] = {}

    def carregar(entidade_tipo: str, origem_chave: str, ids: list[int]) -> None:
        if not ids:
            return

        eventos = (
            HistoricoAlteracao.query.options(joinedload(HistoricoAlteracao.alterado_por))
            .filter(
                HistoricoAlteracao.entidade_tipo == entidade_tipo,
                HistoricoAlteracao.entidade_id.in_(ids),
                HistoricoAlteracao.acao == REINF_CONFERENCIA_MENSAL_ACAO,
            )
            .order_by(HistoricoAlteracao.criado_em.desc(), HistoricoAlteracao.id.desc())
            .all()
        )

        for evento in eventos:
            chave = (origem_chave, int(evento.entidade_id))
            if chave in mapa:
                continue
            mapa[chave] = _meta_evento_conferencia_reinf_mensal(evento)

    carregar("registro_rpv", "rpv", ids_rpv)
    carregar("dativo_item", "dativo_item", ids_dativos)
    return mapa


def _tem_irrf_rpv(registro: RegistroRPV) -> bool:
    if getattr(registro, "sem_irrf_efetivo", False):
        return False

    if getattr(registro, "possui_irrf", False):
        return True

    if _decimal(registro.valor_irrf) > 0:
        return True

    situacao_imposto = getattr(getattr(registro, "situacao_imposto", None), "nome", None)
    return _situacao_indica_fluxo_irrf(situacao_imposto)


def _competencia_para_data_base(competencia: str | None) -> date:
    valor = str(competencia or "").strip()

    if len(valor) == 7 and "-" in valor:
        ano, mes = valor.split("-", 1)
        try:
            return date(int(ano), int(mes), 1)
        except ValueError:
            pass

    return date.min


def _competencia_normalizada(valor: str | None) -> str:
    competencia = str(valor or "").strip()
    if len(competencia) == 7 and "-" in competencia:
        return competencia
    return ""


def _ano_valido(valor: str | None, padrao: str) -> str:
    ano = str(valor or "").strip()
    if len(ano) == 4 and ano.isdigit():
        return ano
    return padrao


def _inicio_competencia(competencia: str | None) -> date | None:
    valor = _competencia_normalizada(competencia)
    if not valor:
        return None

    ano, mes = valor.split("-", 1)
    try:
        return date(int(ano), int(mes), 1)
    except ValueError:
        return None


def _proximo_mes(valor: date) -> date:
    if valor.month == 12:
        return date(valor.year + 1, 1, 1)
    return date(valor.year, valor.month + 1, 1)


def _faixa_competencia(competencia: str | None) -> tuple[date | None, date | None]:
    inicio = _inicio_competencia(competencia)
    if not inicio:
        return None, None
    return inicio, _proximo_mes(inicio)


def _faixa_ano(ano: str | None) -> tuple[date | None, date | None]:
    valor = str(ano or "").strip()
    if len(valor) != 4 or not valor.isdigit():
        return None, None

    inicio = date(int(valor), 1, 1)
    return inicio, date(int(valor) + 1, 1, 1)


def _chave_ordenacao_registro(registro: dict) -> tuple[date, Decimal, datetime]:
    return (
        registro.get("data_pagamento") or _competencia_para_data_base(registro.get("competencia_valor")),
        _decimal(registro.get("imposto")),
        registro.get("criado_em") or datetime.min,
    )


def _filtro_responsavel_ok(responsavel_id, filtro_responsavel: str) -> bool:
    if filtro_responsavel in ("", "todos", "meus"):
        return True

    return str(responsavel_id) == str(filtro_responsavel)


def _filtro_status_ok(status_reinf: str, filtro_status: str) -> bool:
    if filtro_status in ("", "todos"):
        return True

    return status_reinf == filtro_status


def _resolver_status_reinf_obrigatorio(nome_campo: str, mensagem_vazio: str) -> str:
    valor_informado = request.form.get(nome_campo, "").strip()
    if not valor_informado:
        raise ValueError(mensagem_vazio)

    status = resolver_reinf_status(valor_informado, default=None)
    if status is None:
        raise ValueError(mensagem_vazio)

    return status


def _ordenacao_reinf_valida(valor: str | None) -> str:
    valor_normalizado = str(valor or "").strip()
    opcoes_validas = {chave for chave, _ in REINF_ORDENACAO_OPCOES}
    if valor_normalizado in opcoes_validas:
        return valor_normalizado
    return REINF_ORDENACAO_PADRAO


def _ordenar_registros_reinf(registros: list[dict], ordenar: str, direcao: str) -> list[dict]:
    def chave_beneficiario(registro: dict) -> tuple:
        return (
            _normalizar_texto(registro.get("beneficiario")),
            normalizar_documento(registro.get("documento") or ""),
            registro.get("data_pagamento") or date.min,
            _decimal(registro.get("imposto")),
            _normalizar_texto(registro.get("resumo_operacional")),
        )

    ordenacao_mapa = {
        "origem": lambda registro: (
            _normalizar_texto(registro.get("tipo_origem")),
            chave_beneficiario(registro),
        ),
        "competencia": lambda registro: (
            str(registro.get("competencia_valor") or ""),
            chave_beneficiario(registro),
        ),
        "data_pagamento": lambda registro: (
            registro.get("data_pagamento") or date.min,
            chave_beneficiario(registro),
        ),
        "beneficiario": chave_beneficiario,
        "imposto": lambda registro: (
            _decimal(registro.get("imposto")),
            chave_beneficiario(registro),
        ),
        "status_reinf": lambda registro: (
            _normalizar_texto(registro.get("reinf_status")),
            chave_beneficiario(registro),
        ),
    }
    chave_ordenacao = ordenacao_mapa.get(ordenar, ordenacao_mapa[REINF_ORDENACAO_PADRAO])
    return sorted(registros, key=chave_ordenacao, reverse=(direcao == "desc"))


def _filtro_busca_ok(registro: dict, busca: str, busca_doc: str) -> bool:
    if not busca and not busca_doc:
        return True

    campos_textuais = [
        _normalizar_texto(registro.get("beneficiario")),
        _normalizar_texto(registro.get("documento")),
        _normalizar_texto(registro.get("documento_limpo")),
        _normalizar_texto(registro.get("processo")),
        _normalizar_texto(registro.get("ci")),
        _normalizar_texto(registro.get("resumo_operacional")),
    ]

    if busca and any(busca in campo for campo in campos_textuais):
        return True

    if busca_doc:
        documento_normalizado = normalizar_documento(registro.get("documento") or "")
        return busca_doc in documento_normalizado

    return False


def _aplicar_filtro_data_pagamento(query, coluna_data, *, competencia: str | None, ano: str | None):
    inicio_competencia, fim_competencia = _faixa_competencia(competencia)
    if inicio_competencia and fim_competencia:
        query = query.filter(coluna_data >= inicio_competencia, coluna_data < fim_competencia)

    inicio_ano, fim_ano = _faixa_ano(ano)
    if inicio_ano and fim_ano:
        query = query.filter(coluna_data >= inicio_ano, coluna_data < fim_ano)

    return query


def _query_rpvs_reinf(
    *,
    competencia: str | None,
    filtro_responsavel: str,
    filtro_busca: str,
    ano: str | None,
):
    return ReinfDatasetService.build_rpvs_query(
        competencia=competencia,
        filtro_responsavel=filtro_responsavel,
        filtro_busca=filtro_busca,
        ano=ano,
        text_normalizer=_normalizar_texto,
        payment_filter_applier=_aplicar_filtro_data_pagamento,
    )


def _query_dativos_reinf(
    *,
    competencia: str | None,
    filtro_responsavel: str,
    filtro_busca: str,
    ano: str | None,
):
    return ReinfDatasetService.build_dativos_query(
        competencia=competencia,
        filtro_responsavel=filtro_responsavel,
        filtro_busca=filtro_busca,
        ano=ano,
        text_normalizer=_normalizar_texto,
        payment_filter_applier=_aplicar_filtro_data_pagamento,
    )


def _anos_disponiveis_reinf(filtro_responsavel: str, filtro_busca: str) -> list[str]:
    anos = set()

    rpvs = (
        _query_rpvs_reinf(
            competencia=None,
            filtro_responsavel=filtro_responsavel,
            filtro_busca=filtro_busca,
            ano=None,
        )
        .with_entities(RegistroRPV.data_pagamento)
        .all()
    )
    for (data_pagamento,) in rpvs:
        if data_pagamento:
            anos.add(data_pagamento.strftime("%Y"))

    itens = (
        _query_dativos_reinf(
            competencia=None,
            filtro_responsavel=filtro_responsavel,
            filtro_busca=filtro_busca,
            ano=None,
        )
        .with_entities(DativoItem.data_pagamento)
        .all()
    )
    for (data_pagamento,) in itens:
        if data_pagamento:
            anos.add(data_pagamento.strftime("%Y"))

    return sorted(anos, reverse=True)


def _registro_pago_no_mes(data_pagamento, competencia: str) -> bool:
    return bool(data_pagamento and data_pagamento.strftime("%Y-%m") == competencia)


def _registro_pago_no_ano(data_pagamento, ano: str) -> bool:
    return bool(data_pagamento and data_pagamento.strftime("%Y") == ano)


def _competencias_reinf_pendentes() -> list[str]:
    competencias = set()

    rpvs = (
        RegistroRPV.query.options(
            joinedload(RegistroRPV.situacao_imposto),
            joinedload(RegistroRPV.situacao_empenho),
        )
        .filter(RegistroRPV.ativo.is_(True), RegistroRPV.data_pagamento.isnot(None))
        .all()
    )
    for registro in rpvs:
        if getattr(registro, "status_principal_cancelado", False):
            continue
        if not _tem_irrf_rpv(registro):
            continue
        if not registro.data_pagamento:
            continue
        if _status_reinf_resolvido(registro.reinf_status):
            continue
        competencias.add(registro.data_pagamento.strftime("%Y-%m"))

    itens_irrf = (
        DativoItem.query.options(joinedload(DativoItem.situacao_rpv))
        .filter(
            DativoItem.grupo == "com_irrf",
            DativoItem.ativo.is_(True),
            DativoItem.data_pagamento.isnot(None),
        )
        .all()
    )
    for item in itens_irrf:
        if getattr(item, "status_principal_cancelado", False):
            continue
        if not item.data_pagamento:
            continue
        if _status_reinf_resolvido(item.reinf_status):
            continue
        competencias.add(item.data_pagamento.strftime("%Y-%m"))

    return sorted(competencias)


def _resolver_competencia_reinf(valor_solicitado: str | None) -> dict:
    pendentes = _competencias_reinf_pendentes()
    primeira_pendente = pendentes[0] if pendentes else ""
    competencia_padrao = primeira_pendente or _competencia_mes_atual()
    competencia_aplicada = str(valor_solicitado or "").strip() or competencia_padrao
    competencia_bloqueada = ""

    if primeira_pendente and competencia_aplicada > primeira_pendente:
        competencia_bloqueada = primeira_pendente
        competencia_aplicada = primeira_pendente

    return {
        "competencia_aplicada": competencia_aplicada,
        "competencia_padrao": competencia_padrao,
        "competencias_pendentes": pendentes,
        "competencia_bloqueada": competencia_bloqueada,
    }


def _resolver_competencia_reinf_livre(valor_solicitado: str | None) -> dict:
    competencia_aplicada = _competencia_normalizada(valor_solicitado) or _competencia_mes_atual()
    return {
        "competencia_aplicada": competencia_aplicada,
        "competencia_padrao": _competencia_mes_atual(),
        "competencias_pendentes": _competencias_reinf_pendentes(),
        "competencia_bloqueada": "",
    }


def _montar_registro_rpv(registro: RegistroRPV, *, retorno_url: str | None = None) -> dict:
    processo = getattr(registro, "processo", None)
    documento_original = registro.documento_original or "-"
    tipo_documento = str(getattr(registro, "tipo_documento_efetivo", "") or "CPF").upper()
    competencia_pagamento_valor = (
        registro.data_pagamento.strftime("%Y-%m")
        if registro.data_pagamento
        else ""
    )
    abrir_url = url_for("cadastros.editar_rpv", registro_id=registro.id)
    if retorno_url:
        abrir_url = url_for("cadastros.editar_rpv", registro_id=registro.id, retorno=retorno_url)
    return {
        "origem": "rpv",
        "tipo_origem": "RPV normal",
        "responsavel": registro.elaborador.nome if registro.elaborador else "-",
        "competencia_valor": registro.competencia_operacional,
        "competencia": registro.competencia_operacional_formatada,
        "competencia_pagamento_valor": competencia_pagamento_valor,
        "competencia_pagamento": _competencia_legivel(competencia_pagamento_valor),
        "data_pagamento": registro.data_pagamento,
        "beneficiario": registro.nome_beneficiario,
        "documento": documento_original,
        "documento_limpo": normalizar_documento(documento_original) or "-",
        "documento_formatado": formatar_documento_br(documento_original, tipo_documento) or (normalizar_documento(documento_original) or "-"),
        "tipo_documento": tipo_documento,
        "processo": getattr(processo, "numero_processo", "-"),
        "ci": getattr(processo, "processo_edoc", "-"),
        "resumo_operacional": registro.resumo_operacional,
        "valor": registro.valor_bruto,
        "imposto": registro.valor_irrf or 0,
        "valor_liquido": _decimal(registro.valor_bruto) - _decimal(registro.valor_irrf),
        "reinf_status": registro.reinf_status_legivel,
        "abrir_url": abrir_url,
        "registro_id": registro.id,
        "criado_em": registro.criado_em,
    }


def _montar_registro_dativo(item: DativoItem, *, retorno_url: str | None = None) -> dict:
    dativo_ci = getattr(item, "dativo_ci", None)
    documento_original = item.cpf_original or "-"
    tipo_documento = str(getattr(item, "tipo_documento_efetivo", "") or "CPF").upper()
    competencia_pagamento_valor = (
        item.data_pagamento.strftime("%Y-%m")
        if item.data_pagamento
        else ""
    )
    abrir_url = url_for("dativos.detalhe_item_com_irrf", item_id=item.id)
    if retorno_url:
        abrir_url = url_for("dativos.detalhe_item_com_irrf", item_id=item.id, retorno=retorno_url)
    return {
        "origem": "dativo_item",
        "tipo_origem": "Dativo com IRRF",
        "responsavel": (
            item.dativo_ci.responsavel.nome
            if item.dativo_ci and item.dativo_ci.responsavel
            else "-"
        ),
        "competencia_valor": item.competencia_operacional,
        "competencia": item.competencia_operacional_formatada,
        "competencia_pagamento_valor": competencia_pagamento_valor,
        "competencia_pagamento": _competencia_legivel(competencia_pagamento_valor),
        "data_pagamento": item.data_pagamento,
        "beneficiario": item.nome_beneficiario,
        "documento": documento_original,
        "documento_limpo": normalizar_documento(documento_original) or "-",
        "documento_formatado": formatar_documento_br(documento_original, tipo_documento) or (normalizar_documento(documento_original) or "-"),
        "tipo_documento": tipo_documento,
        "processo": item.numero_processo or "-",
        "ci": getattr(dativo_ci, "processo_edoc", "-"),
        "resumo_operacional": item.resumo_operacional_atual,
        "valor": item.valor_bruto,
        "imposto": item.valor_irrf or 0,
        "valor_liquido": _decimal(item.valor_bruto) - _decimal(item.valor_irrf),
        "reinf_status": item.reinf_status_legivel,
        "abrir_url": abrir_url,
        "registro_id": item.id,
        "criado_em": item.criado_em,
    }


def _coletar_base_reinf(
    competencia: str | None,
    filtro_responsavel: str,
    filtro_busca: str,
    *,
    ano: str | None = None,
    retorno_url: str | None = None,
) -> list[dict]:
    return ReinfDatasetService.collect_base(
        competencia=competencia,
        filtro_responsavel=filtro_responsavel,
        filtro_busca=filtro_busca,
        ano=ano,
        retorno_url=retorno_url,
        rpv_query_builder=_query_rpvs_reinf,
        dativo_query_builder=_query_dativos_reinf,
        rpv_has_irrf=_tem_irrf_rpv,
        month_filter_checker=_registro_pago_no_mes,
        year_filter_checker=_registro_pago_no_ano,
        responsavel_filter_checker=_filtro_responsavel_ok,
        rpv_record_builder=_montar_registro_rpv,
        dativo_record_builder=_montar_registro_dativo,
        busca_filter_checker=_filtro_busca_ok,
        sort_key_builder=_chave_ordenacao_registro,
        text_normalizer=_normalizar_texto,
    )


def _chave_beneficiario_reinf(registro: dict) -> tuple[str, str]:
    documento = str(registro.get("documento_limpo") or "").strip()
    if documento and documento != "-":
        return (str(registro.get("tipo_documento") or "CPF"), documento)
    return (str(registro.get("tipo_documento") or "CPF"), _normalizar_texto(registro.get("beneficiario")))


def _linhas_conferencia_reinf_mensal(registros: list[dict], competencia: str) -> dict:
    agrupado = {}
    mapa_conferencia = _mapa_conferencia_reinf_mensal(registros)

    for registro in registros:
        chave = _chave_beneficiario_reinf(registro)
        conferencia_meta = mapa_conferencia.get(
            (str(registro.get("origem") or "").strip(), int(registro["registro_id"]))
        ) or _meta_evento_conferencia_reinf_mensal(None)
        linha = agrupado.setdefault(
            chave,
            {
                "competencia": competencia,
                "competencia_label": _competencia_legivel(competencia),
                "beneficiario": registro["beneficiario"],
                "documento": registro["documento_formatado"] or registro["documento_limpo"],
                "tipo_documento": registro["tipo_documento"],
                "valor_base": Decimal("0.00"),
                "valor_irrf": Decimal("0.00"),
                "pagamentos": [],
            },
        )
        linha["valor_base"] += _decimal(registro["valor"])
        linha["valor_irrf"] += _decimal(registro["imposto"])
        linha["pagamentos"].append(
            {
                "registro_id": registro["registro_id"],
                "origem_chave": registro["origem"],
                "origem": registro["tipo_origem"],
                "processo": registro["processo"],
                "ci": registro["ci"],
                "data_pagamento": registro["data_pagamento"],
                "data_pagamento_legivel": (
                    registro["data_pagamento"].strftime("%d/%m/%Y")
                    if registro["data_pagamento"]
                    else "-"
                ),
                "valor_bruto": _decimal(registro["valor"]),
                "valor_irrf": _decimal(registro["imposto"]),
                "valor_liquido": _decimal(registro["valor_liquido"]),
                "abrir_url": registro["abrir_url"],
                "conferencia_revisada": bool(conferencia_meta["revisado"]),
                "conferencia_revisada_por": conferencia_meta["revisado_por"],
                "conferencia_revisada_em": conferencia_meta["revisado_em"],
                "conferencia_revisada_em_legivel": conferencia_meta["revisado_em_legivel"],
                "conferencia_revisada_meta": conferencia_meta["revisado_meta"],
            }
        )

    linhas = list(agrupado.values())
    for linha in linhas:
        linha["pagamentos"].sort(
            key=lambda item: (
                item["data_pagamento"] or date.min,
                item["valor_bruto"],
                item["processo"],
            ),
            reverse=True,
        )
        linha["pagamentos_conferidos"] = sum(
            1 for pagamento in linha["pagamentos"] if pagamento["conferencia_revisada"]
        )
        linha["total_pagamentos"] = len(linha["pagamentos"])
        linha["rotulo_progresso_conferencia"] = _rotulo_progresso_conferencia(
            linha["pagamentos_conferidos"],
            linha["total_pagamentos"],
        )
        linha["todos_processos_conferidos"] = (
            linha["total_pagamentos"] > 0
            and linha["pagamentos_conferidos"] == linha["total_pagamentos"]
        )
        linha["processos_conferidos_parcialmente"] = (
            linha["pagamentos_conferidos"] > 0
            and not linha["todos_processos_conferidos"]
        )

    linhas.sort(
        key=lambda item: (
            str(item.get("tipo_documento") or ""),
            normalizar_documento(item.get("documento") or ""),
            _normalizar_texto(item["beneficiario"]),
        )
    )
    total_pagamentos = sum(len(linha["pagamentos"]) for linha in linhas)
    total_processos_conferidos = sum(linha["pagamentos_conferidos"] for linha in linhas)
    total_beneficiarios_conferidos = sum(
        1 for linha in linhas if linha["todos_processos_conferidos"]
    )
    return {
        "linhas": linhas,
        "tem_dados": bool(linhas),
        "resumo_conferencia": _resumo_conferencia_processos(
            total_processos_conferidos,
            total_pagamentos,
        ),
        "totais": {
            "beneficiarios": len(linhas),
            "beneficiarios_conferidos": total_beneficiarios_conferidos,
            "pagamentos": total_pagamentos,
            "pagamentos_conferidos": total_processos_conferidos,
            "valor_base": sum((linha["valor_base"] for linha in linhas), Decimal("0.00")),
            "valor_irrf": sum((linha["valor_irrf"] for linha in linhas), Decimal("0.00")),
        },
    }


def _linhas_conferencia_reinf_anual(registros: list[dict], ano: str) -> dict:
    agrupado = {}

    for registro in registros:
        chave = _chave_beneficiario_reinf(registro)
        linha = agrupado.setdefault(
            chave,
            {
                "ano": ano,
                "beneficiario": registro["beneficiario"],
                "documento": registro["documento_formatado"] or registro["documento_limpo"],
                "tipo_documento": registro["tipo_documento"],
                "valor_base": Decimal("0.00"),
                "valor_irrf": Decimal("0.00"),
                "pagamentos": [],
                "meses": {},
            },
        )
        linha["valor_base"] += _decimal(registro["valor"])
        linha["valor_irrf"] += _decimal(registro["imposto"])
        competencia = registro["competencia_pagamento_valor"]
        mes = linha["meses"].setdefault(
            competencia,
            {
                "competencia": competencia,
                "competencia_label": registro["competencia_pagamento"],
                "valor_bruto": Decimal("0.00"),
                "valor_irrf": Decimal("0.00"),
                "valor_liquido": Decimal("0.00"),
                "pagamentos": 0,
            },
        )
        mes["valor_bruto"] += _decimal(registro["valor"])
        mes["valor_irrf"] += _decimal(registro["imposto"])
        mes["valor_liquido"] += _decimal(registro["valor_liquido"])
        mes["pagamentos"] += 1
        linha["pagamentos"].append(
            {
                "competencia": registro["competencia_pagamento_valor"],
                "competencia_label": registro["competencia_pagamento"],
                "origem": registro["tipo_origem"],
                "processo": registro["processo"],
                "ci": registro["ci"],
                "data_pagamento": registro["data_pagamento"],
                "data_pagamento_legivel": (
                    registro["data_pagamento"].strftime("%d/%m/%Y")
                    if registro["data_pagamento"]
                    else "-"
                ),
                "valor_bruto": _decimal(registro["valor"]),
                "valor_irrf": _decimal(registro["imposto"]),
                "valor_liquido": _decimal(registro["valor_liquido"]),
                "abrir_url": registro["abrir_url"],
            }
        )

    linhas = list(agrupado.values())
    for linha in linhas:
        linha["meses"] = sorted(
            linha["meses"].values(),
            key=lambda item: item["competencia"],
            reverse=True,
        )
        linha["pagamentos"].sort(
            key=lambda item: (
                item["competencia"],
                item["data_pagamento"] or date.min,
                item["valor_bruto"],
            ),
            reverse=True,
        )

    linhas.sort(
        key=lambda item: (
            -item["valor_irrf"],
            -item["valor_base"],
            _normalizar_texto(item["beneficiario"]),
        )
    )
    return {
        "linhas": linhas,
        "tem_dados": bool(linhas),
        "totais": {
            "beneficiarios": len(linhas),
            "pagamentos": sum(len(linha["pagamentos"]) for linha in linhas),
            "valor_base": sum((linha["valor_base"] for linha in linhas), Decimal("0.00")),
            "valor_irrf": sum((linha["valor_irrf"] for linha in linhas), Decimal("0.00")),
        },
    }


def _atualizar_status_reinf(
    origem: str,
    registro_id: int,
    reinf_status: str | None,
    *,
    acao: str = "Atualização REINF",
    resumo: str | None = None,
):
    reinf_status = resolver_reinf_status(reinf_status, default=None)

    if origem == "rpv":
        registro = RegistroRPV.query.get_or_404(registro_id)
        antes = snapshot_entidade("registro_rpv", registro)
        registro.reinf_status = reinf_status
        registro.atualizado_por_id = current_user.id
        registrar_evento(
            entidade_tipo="registro_rpv",
            entidade_id=registro.id,
            usuario_id=current_user.id,
            acao=acao,
            antes=antes,
            depois=snapshot_entidade("registro_rpv", registro),
            resumo=resumo,
        )
        return

    if origem == "dativo_item":
        registro = DativoItem.query.get_or_404(registro_id)
        antes = snapshot_entidade("dativo_item", registro)
        registro.reinf_status = reinf_status
        registro.atualizado_por_id = current_user.id
        registrar_evento(
            entidade_tipo="dativo_item",
            entidade_id=registro.id,
            usuario_id=current_user.id,
            acao=acao,
            antes=antes,
            depois=snapshot_entidade("dativo_item", registro),
            resumo=resumo,
        )
        return

    raise ValueError("Origem inválida para atualização da REINF.")


def _filtros_reinf():
    filtros = ReinfFilterService.normalize_filters(
        request.args,
        visao_normalizer=_visao_reinf_valida,
        competencia_operacional_resolver=_resolver_competencia_reinf,
        competencia_livre_resolver=_resolver_competencia_reinf_livre,
        competencia_mes_atual_loader=_competencia_mes_atual,
        ano_normalizer=_ano_valido,
        ordenacao_normalizer=_ordenacao_reinf_valida,
        direcao_normalizer=sanitize_sort_direction,
        pagina_normalizer=parse_page,
        page_size_normalizer=parse_page_size,
        status_padrao=REINF_STATUS_NAO_ENVIADO,
    )
    filtros["direcao"] = str(filtros["direcao"] or REINF_DIRECAO_PADRAO)
    return filtros


@reinf_bp.route("/")
@login_required
def index():
    usuarios = User.query.filter_by(ativo=True).order_by(User.nome.asc()).all()
    filtros = _filtros_reinf()
    visao = str(filtros["visao"])
    url_retorno_atual = current_internal_url(url_for("reinf.index"))
    anos_disponiveis = _anos_disponiveis_reinf(filtros["responsavel"], filtros["q"])
    if not anos_disponiveis:
        anos_disponiveis = [filtros["ano"]]
    if filtros["ano"] not in anos_disponiveis:
        filtros["ano"] = anos_disponiveis[0]

    registros_paginados = []
    paginacao = {
        "total_itens": 0,
        "inicio": 0,
        "fim": 0,
        "pagina": 1,
        "total_paginas": 1,
    }
    export_url = None
    conferencia_mensal = {
        "linhas": [],
        "tem_dados": False,
        "totais": {
            "beneficiarios": 0,
            "pagamentos": 0,
            "valor_base": Decimal("0.00"),
            "valor_irrf": Decimal("0.00"),
        },
    }
    conferencia_anual = {
        "linhas": [],
        "tem_dados": False,
        "totais": {
            "beneficiarios": 0,
            "pagamentos": 0,
            "valor_base": Decimal("0.00"),
            "valor_irrf": Decimal("0.00"),
        },
    }
    export_url = None

    if visao == "operacional":
        registros_base = _coletar_base_reinf(
            competencia=filtros["competencia"],
            filtro_responsavel=filtros["responsavel"],
            filtro_busca=filtros["q"],
            retorno_url=url_retorno_atual,
        )
        registros = [
            registro
            for registro in registros_base
            if _filtro_status_ok(registro["reinf_status"], filtros["reinf_status"])
        ]
        registros = _ordenar_registros_reinf(registros, filtros["ordenar"], filtros["direcao"])
        registros_paginados, paginacao = paginate_items(
            registros,
            filtros["pagina"],
            filtros["por_pagina"],
        )
        export_url = url_for(
            "reinf.exportar_csv",
            competencia=filtros["competencia"],
            responsavel=filtros["responsavel"],
            reinf_status=filtros["reinf_status"],
            q=filtros["q"],
            ordenar=filtros["ordenar"],
            direcao=filtros["direcao"],
        )
    elif visao == "conferencia_mensal":
        registros_mes = _coletar_base_reinf(
            competencia=filtros["competencia"],
            filtro_responsavel=filtros["responsavel"],
            filtro_busca=filtros["q"],
            retorno_url=url_retorno_atual,
        )
        conferencia_mensal = _linhas_conferencia_reinf_mensal(
            registros_mes,
            filtros["competencia"],
        )
    else:
        registros_ano = _coletar_base_reinf(
            competencia=None,
            filtro_responsavel=filtros["responsavel"],
            filtro_busca=filtros["q"],
            ano=filtros["ano"],
            retorno_url=url_retorno_atual,
        )
        conferencia_anual = _linhas_conferencia_reinf_anual(
            registros_ano,
            filtros["ano"],
        )

    contexto = ReinfContextService.build_index_context(
        filtros_request=request.args,
        filtros=filtros,
        usuarios=usuarios,
        anos_disponiveis=anos_disponiveis,
        registros=registros_paginados,
        paginacao=paginacao,
        conferencia_mensal=conferencia_mensal,
        conferencia_anual=conferencia_anual,
        export_url=export_url,
        url_retorno_atual=url_retorno_atual,
        view_options=REINF_VISOES,
        status_opcoes=REINF_STATUS_OPCOES,
        status_filtros=REINF_STATUS_FILTROS,
        ordenacao_opcoes=REINF_ORDENACAO_OPCOES,
        direcao_opcoes=REINF_DIRECAO_OPCOES,
        competencia_legivel=_competencia_legivel,
        query_params_merger=merge_query_params,
        page_window_builder=build_page_window,
        sort_direction_resolver=resolve_next_sort_direction,
    )
    return render_template("reinf/index.html", **contexto)


@reinf_bp.route("/exportar.csv")
@login_required
def exportar_csv():
    filtros = _filtros_reinf()
    registros_base = _coletar_base_reinf(
        competencia=filtros["competencia"],
        filtro_responsavel=filtros["responsavel"],
        filtro_busca=filtros["q"],
    )
    registros = [
        registro
        for registro in registros_base
        if _filtro_status_ok(registro["reinf_status"], filtros["reinf_status"])
    ]
    registros = _ordenar_registros_reinf(registros, filtros["ordenar"], filtros["direcao"])
    nome_arquivo = f"reinf_{filtros['competencia']}.csv"
    conteudo = ReinfExportService.build_csv_content(
        registros,
        decimal_formatter=_decimal_csv,
    )
    return Response(
        conteudo,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )


@reinf_bp.route("/atualizar-status", methods=["POST"])
@login_required
def atualizar_status():
    origem = request.form.get("origem", "").strip()
    registro_id = request.form.get("registro_id", "").strip()
    try:
        reinf_status = _resolver_status_reinf_obrigatorio(
            "reinf_status",
            "Selecione um status REINF antes de salvar.",
        )
        _atualizar_status_reinf(
            origem,
            int(registro_id),
            reinf_status,
            acao="Atualização REINF",
        )
        db.session.commit()
        flash("Status da REINF atualizado com sucesso.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao atualizar status da REINF. origem=%s registro_id=%s",
            origem,
            registro_id,
        )
        flash("Nao foi possivel atualizar o status da REINF agora. Tente novamente.", "danger")

    return redirect(_url_retorno_interna(url_for("reinf.index")))


@reinf_bp.route("/limpar-status", methods=["POST"])
@login_required
def limpar_status():
    if not getattr(current_user, "is_admin", False):
        flash("Acesso restrito a administradores.", "danger")
        return redirect(_url_retorno_interna(url_for("reinf.index")))

    origem = request.form.get("origem", "").strip()
    registro_id = request.form.get("registro_id", "").strip()

    try:
        _atualizar_status_reinf(
            origem,
            int(registro_id),
            None,
            acao="Limpeza administrativa REINF",
            resumo="Status REINF limpo para correção operacional/teste",
        )
        db.session.commit()
        flash("Status da REINF limpo com sucesso.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao limpar status da REINF. origem=%s registro_id=%s usuario_id=%s",
            origem,
            registro_id,
            getattr(current_user, "id", None),
        )
        flash("Nao foi possivel limpar o status da REINF agora. Tente novamente.", "danger")

    return redirect(_url_retorno_interna(url_for("reinf.index")))


@reinf_bp.route("/atualizar-status-lote", methods=["POST"])
@login_required
def atualizar_status_lote():
    reinf_status = resolver_reinf_status(
        request.form.get("reinf_status_lote", ""),
        default="Concluído",
    )
    selecionados = request.form.getlist("selecionados")

    if not selecionados:
        flash("Selecione ao menos um registro para atualizar em lote.", "info")
        return redirect(_url_retorno_interna(url_for("reinf.index")))

    try:
        reinf_status = _resolver_status_reinf_obrigatorio(
            "reinf_status_lote",
            "Selecione um status REINF válido antes de atualizar em lote.",
        )
        total = 0
        for item in selecionados:
            origem, registro_id = item.split(":", 1)
            _atualizar_status_reinf(
                origem,
                int(registro_id),
                reinf_status,
                acao="Atualização REINF em lote",
                resumo="Ação aplicada em lote",
            )
            total += 1

        db.session.commit()
        flash(f"{total} registro(s) da REINF atualizados em lote.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao atualizar a REINF em lote. total_selecionados=%s usuario_id=%s",
            len(selecionados),
            getattr(current_user, "id", None),
        )
        flash("Nao foi possivel atualizar a REINF em lote agora. Tente novamente.", "danger")

    return redirect(_url_retorno_interna(url_for("reinf.index")))


@reinf_bp.route("/marcar-conferencia-processo", methods=["POST"])
@login_required
def marcar_conferencia_processo():
    wants_json = (
        request.headers.get("X-Requested-With") == "fetch"
        or "application/json" in str(request.headers.get("Accept") or "")
    )
    origem = request.form.get("origem", "").strip()
    registro_id_raw = request.form.get("registro_id", "").strip()
    revisado_desejado = _estado_revisado_formulario(request.form.get("revisado"))

    try:
        if not registro_id_raw.isdigit():
            raise ValueError("Processo da conferencia REINF invalido.")

        entidade_tipo = _entidade_tipo_reinf_por_origem(origem)
        entidade = _carregar_entidade_conferencia_reinf(origem, int(registro_id_raw))
        evento_existente = _evento_conferencia_reinf_mensal(entidade_tipo, entidade.id)
        meta_atual = _meta_evento_conferencia_reinf_mensal(evento_existente)
        criado_agora = False

        if meta_atual["revisado"] != revisado_desejado:
            historico = registrar_evento(
                entidade_tipo=entidade_tipo,
                entidade_id=entidade.id,
                usuario_id=current_user.id,
                acao=REINF_CONFERENCIA_MENSAL_ACAO,
                resumo=(
                    REINF_CONFERENCIA_MENSAL_RESUMO
                    if revisado_desejado
                    else REINF_CONFERENCIA_MENSAL_RESUMO_REMOVIDO
                ),
                forcar_registro=True,
            )
            if historico is not None:
                historico.definir_alteracoes(
                    _detalhes_conferencia_reinf_mensal(
                        meta_atual["revisado"],
                        revisado_desejado,
                    )
                )
            db.session.commit()
            criado_agora = True
            evento_existente = _evento_conferencia_reinf_mensal(entidade_tipo, entidade.id)

        meta_evento = _meta_evento_conferencia_reinf_mensal(evento_existente)
        mensagem = (
            (
                "Processo marcado como conferido na conferencia mensal da REINF."
                if criado_agora
                else "Esse processo ja estava conferido na conferencia mensal da REINF."
            )
            if revisado_desejado
            else (
                "Conferencia removida deste processo na leitura mensal da REINF."
                if criado_agora
                else "Esse processo ja estava sem conferencia marcada na leitura mensal da REINF."
            )
        )

        if wants_json:
            return jsonify(
                {
                    "ok": True,
                    "created": criado_agora,
                    "reviewed": meta_evento["revisado"],
                    "message": mensagem,
                    "reviewed_meta": meta_evento["revisado_meta"],
                    "reviewed_by": meta_evento["revisado_por"],
                    "reviewed_at": meta_evento["revisado_em_legivel"],
                }
            )

        flash(mensagem, "success" if criado_agora else "info")
    except ValueError as exc:
        db.session.rollback()
        if wants_json:
            return jsonify({"ok": False, "message": str(exc)}), 400
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao marcar processo como conferido na REINF mensal. origem=%s registro_id=%s usuario_id=%s",
            origem,
            registro_id_raw,
            getattr(current_user, "id", None),
        )
        mensagem = (
            "Nao foi possivel salvar a conferencia deste processo agora. Tente novamente."
        )
        if wants_json:
            return jsonify({"ok": False, "message": mensagem}), 500
        flash(mensagem, "danger")

    return redirect(_url_retorno_interna(url_for("reinf.index")))

