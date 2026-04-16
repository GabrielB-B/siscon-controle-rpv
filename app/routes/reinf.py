import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
from unidecode import unidecode

from app.extensions import db
from app.models import DativoCI, DativoItem, RegistroRPV, User
from app.services.audit_service import registrar_evento, snapshot_entidade
from app.utils.formatters import formatar_documento_br
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


def _registro_pago_no_mes(data_pagamento, competencia: str) -> bool:
    return bool(data_pagamento and data_pagamento.strftime("%Y-%m") == competencia)


def _registro_pago_no_ano(data_pagamento, ano: str) -> bool:
    return bool(data_pagamento and data_pagamento.strftime("%Y") == ano)


def _competencias_reinf_pendentes() -> list[str]:
    competencias = set()

    rpvs = (
        RegistroRPV.query.options(joinedload(RegistroRPV.situacao_imposto))
        .filter_by(ativo=True)
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

    itens_irrf = DativoItem.query.filter_by(grupo="com_irrf", ativo=True).all()
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


def _montar_registro_rpv(registro: RegistroRPV) -> dict:
    processo = getattr(registro, "processo", None)
    documento_original = registro.documento_original or "-"
    tipo_documento = str(getattr(registro, "tipo_documento_efetivo", "") or "CPF").upper()
    competencia_pagamento_valor = (
        registro.data_pagamento.strftime("%Y-%m")
        if registro.data_pagamento
        else ""
    )
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
        "abrir_url": url_for("cadastros.editar_rpv", registro_id=registro.id),
        "registro_id": registro.id,
        "criado_em": registro.criado_em,
    }


def _montar_registro_dativo(item: DativoItem) -> dict:
    dativo_ci = getattr(item, "dativo_ci", None)
    documento_original = item.cpf_original or "-"
    tipo_documento = str(getattr(item, "tipo_documento_efetivo", "") or "CPF").upper()
    competencia_pagamento_valor = (
        item.data_pagamento.strftime("%Y-%m")
        if item.data_pagamento
        else ""
    )
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
        "abrir_url": url_for("dativos.detalhe_item_com_irrf", item_id=item.id),
        "registro_id": item.id,
        "criado_em": item.criado_em,
    }


def _coletar_base_reinf(
    competencia: str | None,
    filtro_responsavel: str,
    filtro_busca: str,
    *,
    ano: str | None = None,
) -> list[dict]:
    busca = _normalizar_texto(filtro_busca)
    busca_doc = normalizar_documento(filtro_busca)
    registros = []

    rpvs = (
        RegistroRPV.query.options(
            joinedload(RegistroRPV.elaborador),
            joinedload(RegistroRPV.processo),
            joinedload(RegistroRPV.situacao_imposto),
        )
        .filter_by(ativo=True)
        .order_by(RegistroRPV.criado_em.desc())
        .all()
    )
    for registro in rpvs:
        if getattr(registro, "status_principal_cancelado", False):
            continue
        if not _tem_irrf_rpv(registro):
            continue

        if competencia and not _registro_pago_no_mes(registro.data_pagamento, competencia):
            continue
        if ano and not _registro_pago_no_ano(registro.data_pagamento, ano):
            continue

        if not _filtro_responsavel_ok(registro.elaborador_id, filtro_responsavel):
            continue

        linha = _montar_registro_rpv(registro)
        if not _filtro_busca_ok(linha, busca, busca_doc):
            continue

        registros.append(linha)

    itens_irrf = (
        DativoItem.query.options(
            joinedload(DativoItem.dativo_ci).joinedload(DativoCI.responsavel),
        )
        .filter_by(grupo="com_irrf", ativo=True)
        .order_by(DativoItem.criado_em.desc())
        .all()
    )
    for item in itens_irrf:
        if getattr(item, "status_principal_cancelado", False):
            continue
        if competencia and not _registro_pago_no_mes(item.data_pagamento, competencia):
            continue
        if ano and not _registro_pago_no_ano(item.data_pagamento, ano):
            continue

        if not _filtro_responsavel_ok(item.dativo_ci.responsavel_id if item.dativo_ci else None, filtro_responsavel):
            continue

        linha = _montar_registro_dativo(item)
        if not _filtro_busca_ok(linha, busca, busca_doc):
            continue

        registros.append(linha)

    registros.sort(key=_chave_ordenacao_registro, reverse=True)
    return registros


def _chave_beneficiario_reinf(registro: dict) -> tuple[str, str]:
    documento = str(registro.get("documento_limpo") or "").strip()
    if documento and documento != "-":
        return (str(registro.get("tipo_documento") or "CPF"), documento)
    return (str(registro.get("tipo_documento") or "CPF"), _normalizar_texto(registro.get("beneficiario")))


def _linhas_conferencia_reinf_mensal(registros: list[dict], competencia: str) -> dict:
    agrupado = {}

    for registro in registros:
        chave = _chave_beneficiario_reinf(registro)
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
        linha["pagamentos"].sort(
            key=lambda item: (
                item["data_pagamento"] or date.min,
                item["valor_bruto"],
                item["processo"],
            ),
            reverse=True,
        )

    linhas.sort(
        key=lambda item: (
            str(item.get("tipo_documento") or ""),
            normalizar_documento(item.get("documento") or ""),
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
    visao = _visao_reinf_valida(request.args.get("visao"))
    resolucao_competencia = (
        _resolver_competencia_reinf(request.args.get("competencia", "").strip())
        if visao == "operacional"
        else _resolver_competencia_reinf_livre(request.args.get("competencia", "").strip())
    )
    ano_padrao = (
        resolucao_competencia["competencia_aplicada"][:4]
        if resolucao_competencia["competencia_aplicada"]
        else _competencia_mes_atual()[:4]
    )
    return {
        "visao": visao,
        "competencia": resolucao_competencia["competencia_aplicada"],
        "ano": _ano_valido(request.args.get("ano"), ano_padrao),
        "responsavel": request.args.get("responsavel", "todos").strip() or "todos",
        "reinf_status": request.args.get("reinf_status", "").strip() or REINF_STATUS_NAO_ENVIADO,
        "q": request.args.get("q", "").strip(),
        "ordenar": _ordenacao_reinf_valida(request.args.get("ordenar")),
        "direcao": sanitize_sort_direction(request.args.get("direcao"), padrao=REINF_DIRECAO_PADRAO),
        "pagina": parse_page(request.args.get("pagina"), padrao=1),
        "por_pagina": parse_page_size(request.args.get("por_pagina"), padrao=20),
        "competencia_padrao": resolucao_competencia["competencia_padrao"],
        "competencias_pendentes": resolucao_competencia["competencias_pendentes"],
        "competencia_bloqueada": resolucao_competencia["competencia_bloqueada"],
    }


@reinf_bp.route("/")
@login_required
def index():
    usuarios = User.query.filter_by(ativo=True).order_by(User.nome.asc()).all()
    filtros = _filtros_reinf()
    visao = filtros["visao"]
    registros_gerais = _coletar_base_reinf(
        competencia=None,
        filtro_responsavel=filtros["responsavel"],
        filtro_busca=filtros["q"],
    )
    anos_disponiveis = sorted(
        {
            registro["data_pagamento"].strftime("%Y")
            for registro in registros_gerais
            if registro.get("data_pagamento")
        },
        reverse=True,
    )
    if not anos_disponiveis:
        anos_disponiveis = [filtros["ano"]]
    if filtros["ano"] not in anos_disponiveis:
        filtros["ano"] = anos_disponiveis[0]

    filtros_dict = request.args.to_dict()
    view_urls = {
        chave: url_for(
            "reinf.index",
            **merge_query_params(
                filtros_dict,
                visao=chave,
                pagina=None,
            ),
        )
        for chave in REINF_VISOES
    }

    registros_paginados = []
    paginacao = {
        "total_itens": 0,
        "inicio": 0,
        "fim": 0,
        "pagina": 1,
        "total_paginas": 1,
    }
    filtros_ocultos = {}
    sort_urls = {}
    paginas_visiveis = []
    pagina_urls = {}
    pagina_anterior_url = None
    proxima_pagina_url = None
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

    if visao == "operacional":
        registros = [
            registro
            for registro in registros_gerais
            if _registro_pago_no_mes(registro["data_pagamento"], filtros["competencia"])
            and _filtro_status_ok(registro["reinf_status"], filtros["reinf_status"])
        ]
        registros = _ordenar_registros_reinf(registros, filtros["ordenar"], filtros["direcao"])
        registros_paginados, paginacao = paginate_items(
            registros,
            filtros["pagina"],
            filtros["por_pagina"],
        )
        filtros_ocultos = merge_query_params(
            filtros_dict,
            pagina=None,
            por_pagina=None,
        )
        sort_keys = [
            "origem",
            "competencia",
            "data_pagamento",
            "beneficiario",
            "imposto",
            "status_reinf",
        ]
        sort_urls = {
            chave: url_for(
                "reinf.index",
                **merge_query_params(
                    filtros_dict,
                    ordenar=chave,
                    direcao=resolve_next_sort_direction(
                        filtros["ordenar"],
                        filtros["direcao"],
                        chave,
                    ),
                    pagina=1,
                ),
            )
            for chave in sort_keys
        }
        paginas_visiveis = build_page_window(
            paginacao["total_paginas"],
            paginacao["pagina"],
        )
        pagina_urls = {
            numero: url_for(
                "reinf.index",
                **merge_query_params(filtros_dict, pagina=numero),
            )
            for numero in paginas_visiveis
        }
        pagina_anterior_url = (
            url_for(
                "reinf.index",
                **merge_query_params(filtros_dict, pagina=paginacao["pagina_anterior"]),
            )
            if paginacao["tem_anterior"]
            else None
        )
        proxima_pagina_url = (
            url_for(
                "reinf.index",
                **merge_query_params(filtros_dict, pagina=paginacao["proxima_pagina"]),
            )
            if paginacao["tem_proxima"]
            else None
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
        registros_mes = [
            registro
            for registro in registros_gerais
            if _registro_pago_no_mes(registro["data_pagamento"], filtros["competencia"])
        ]
        conferencia_mensal = _linhas_conferencia_reinf_mensal(
            registros_mes,
            filtros["competencia"],
        )
    else:
        registros_ano = [
            registro
            for registro in registros_gerais
            if _registro_pago_no_ano(registro["data_pagamento"], filtros["ano"])
        ]
        conferencia_anual = _linhas_conferencia_reinf_anual(
            registros_ano,
            filtros["ano"],
        )

    return render_template(
        "reinf/index.html",
        visao_reinf=visao,
        view_urls=view_urls,
        visao_opcoes=REINF_VISOES,
        registros=registros_paginados,
        conferencia_mensal=conferencia_mensal,
        conferencia_anual=conferencia_anual,
        anos_disponiveis=anos_disponiveis,
        usuarios=usuarios,
        reinf_status_opcoes=REINF_STATUS_OPCOES,
        reinf_status_filtros=REINF_STATUS_FILTROS,
        reinf_ordenacao_opcoes=REINF_ORDENACAO_OPCOES,
        reinf_direcao_opcoes=REINF_DIRECAO_OPCOES,
        reinf_ordenacao_labels=dict(REINF_ORDENACAO_OPCOES),
        export_url=export_url,
        filtros_ocultos=filtros_ocultos,
        filtro_competencia=filtros["competencia"],
        filtro_ano=filtros["ano"],
        filtro_responsavel=filtros["responsavel"],
        filtro_reinf_status=filtros["reinf_status"],
        filtro_busca=filtros["q"],
        ordenar_atual=filtros["ordenar"],
        direcao_atual=filtros["direcao"],
        por_pagina=filtros["por_pagina"],
        paginacao=paginacao,
        paginas_visiveis=paginas_visiveis,
        pagina_urls=pagina_urls,
        pagina_anterior_url=pagina_anterior_url,
        proxima_pagina_url=proxima_pagina_url,
        sort_urls=sort_urls,
        competencia_padrao=filtros["competencia_padrao"],
        competencia_bloqueada=filtros["competencia_bloqueada"],
        competencias_pendentes=filtros["competencias_pendentes"],
        competencia_legivel=_competencia_legivel,
    )


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

    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Origem",
            "Competência",
            "Data pagamento",
            "Beneficiário",
            "Documento",
            "Processo",
            "C.I.",
            "Resumo operacional",
            "Valor bruto",
            "IRRF",
            "Status REINF",
        ]
    )

    for registro in registros:
        writer.writerow(
            [
                registro["tipo_origem"],
                registro["competencia"],
                registro["data_pagamento"].strftime("%d/%m/%Y") if registro["data_pagamento"] else "-",
                registro["beneficiario"],
                registro["documento_limpo"],
                registro["processo"],
                registro["ci"],
                registro["resumo_operacional"],
                _decimal_csv(registro["valor"]),
                _decimal_csv(registro["imposto"]),
                registro["reinf_status"],
            ]
        )

    nome_arquivo = f"reinf_{filtros['competencia']}.csv"
    conteudo = "\ufeff" + buffer.getvalue()
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
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao atualizar status da REINF: {exc}", "danger")

    return redirect(request.referrer or url_for("reinf.index"))


@reinf_bp.route("/limpar-status", methods=["POST"])
@login_required
def limpar_status():
    if not getattr(current_user, "is_admin", False):
        flash("Acesso restrito a administradores.", "danger")
        return redirect(request.referrer or url_for("reinf.index"))

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
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao limpar status da REINF: {exc}", "danger")

    return redirect(request.referrer or url_for("reinf.index"))


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
        return redirect(request.referrer or url_for("reinf.index"))

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
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao atualizar a REINF em lote: {exc}", "danger")

    return redirect(request.referrer or url_for("reinf.index"))
