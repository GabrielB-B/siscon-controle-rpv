from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload
from unidecode import unidecode

from app.extensions import db
from app.models import (
    Processo,
    RegistroRPV,
    RPVPendenciaDocumento,
    TipoRPV,
    SituacaoEmpenho,
    SituacaoImposto,
    User,
)
from app.services.audit_service import registrar_evento, snapshot_entidade
from app.services.irrf_calculator import calcular_irrf_operacional
from app.services.payment_reference_service import (
    PaymentReferenceValidationError,
    validar_referencias_pagamento_principal,
)
from app.services.processo_crosscheck_service import ProcessoCrosscheckService
from app.utils.normalizers import normalizar_documento, normalizar_numero_processo
from app.utils.datetime_utils import utc_now_naive
from app.utils.documentos import validar_documento_brasileiro
from app.utils.navigation import current_internal_url, sanitize_internal_return_url
from app.utils.payment_rules import (
    competencia_pagamento_automatica,
    data_pagamento_manual_exige_confirmacao,
    resolver_data_pagamento_por_status,
    situacao_id_eh_cancelado,
    situacao_id_quita_pagamento_principal,
)
from app.utils.workbench import (
    build_page_window,
    build_pagination,
    collect_hidden_queue_status_ids,
    merge_query_params,
    parse_page,
    parse_page_size,
    resolve_next_sort_direction,
    sanitize_sort_direction,
    should_include_closed_in_queue,
)

cadastros_bp = Blueprint("cadastros", __name__, url_prefix="/rpvs")


def parse_date(valor: str):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def parse_decimal(valor: str):
    if not valor:
        return None

    valor = valor.strip().replace("R$", "").replace(" ", "")

    if "," in valor and "." in valor:
        valor = valor.replace(".", "").replace(",", ".")
    else:
        valor = valor.replace(",", ".")

    return Decimal(valor)


def _valor_bruto_alterado(valor_atual, valor_novo: Decimal) -> bool:
    return Decimal(valor_atual or 0) != Decimal(valor_novo or 0)


def _payload_calculo_irrf() -> dict:
    return request.get_json(silent=True) or {}


@cadastros_bp.route("/calcular-irrf", methods=["POST"])
@login_required
def calcular_irrf_sugerido():
    payload = _payload_calculo_irrf()
    resultado = calcular_irrf_operacional(
        competencia=payload.get("competencia"),
        valor_bruto_tributavel=payload.get("valor_bruto"),
        documento=payload.get("documento"),
        tipo_documento=payload.get("tipo_documento"),
        sem_irrf_forcado=bool(payload.get("sem_irrf")),
    )
    return jsonify(resultado.to_payload())


def resolver_exercicio_operacional(exercicio_informado: str | None, data_pagamento):
    if data_pagamento:
        return data_pagamento.strftime("%Y-%m")

    return str(exercicio_informado or "").strip()


def carregar_opcoes():
    tipos_rpv = TipoRPV.query.filter_by(ativo=True).order_by(TipoRPV.ordem_exibicao.asc()).all()
    situacoes_empenho = (
        SituacaoEmpenho.query.filter_by(ativo=True)
        .order_by(SituacaoEmpenho.ordem_fluxo.asc())
        .all()
    )
    situacoes_imposto = (
        SituacaoImposto.query.filter_by(ativo=True)
        .order_by(SituacaoImposto.ordem_fluxo.asc())
        .all()
    )
    usuarios = User.query.filter_by(ativo=True).order_by(User.nome.asc()).all()
    return tipos_rpv, situacoes_empenho, situacoes_imposto, usuarios


def _obter_usuario_responsavel(usuario_id_raw: str | int | None) -> User:
    valor = str(usuario_id_raw or "").strip()
    if not valor.isdigit():
        raise ValueError("Responsável inválido.")

    usuario = db.session.get(User, int(valor))
    if not usuario or not usuario.ativo:
        raise ValueError("Responsável inválido.")

    return usuario


def _registrar_historico_rpv(
    registro: RegistroRPV,
    *,
    usuario_id: int,
    acao: str,
    antes: dict | None = None,
    resumo: str | None = None,
    forcar_registro: bool = False,
):
    registrar_evento(
        entidade_tipo="registro_rpv",
        entidade_id=registro.id,
        usuario_id=usuario_id,
        acao=acao,
        antes=antes,
        depois=snapshot_entidade("registro_rpv", registro),
        resumo=resumo,
        forcar_registro=forcar_registro,
    )


def obter_situacao_empenho_inicial():
    situacao = SituacaoEmpenho.query.filter_by(nome="Sem Tratamento", ativo=True).first()
    if not situacao:
        raise ValueError("Situação inicial do RPV 'Sem Tratamento' não encontrada.")
    return situacao


def obter_situacao_imposto_por_nome(nome: str):
    situacao = SituacaoImposto.query.filter_by(nome=nome, ativo=True).first()
    if not situacao:
        raise ValueError(f"Situação inicial do imposto '{nome}' não encontrada.")
    return situacao


def situacao_imposto_eh_sem_irrf(situacao: SituacaoImposto | None) -> bool:
    return str(getattr(situacao, "nome", "") or "").strip().casefold() == "sem irrf"


def obter_situacao_imposto_inicial(sem_irrf: bool = False):
    nome = "Sem IRRF" if sem_irrf else "Sem Tratamento"
    return obter_situacao_imposto_por_nome(nome)


def resolver_situacao_imposto_rpv(sem_irrf: bool, situacao_imposto_id: int | None = None):
    if sem_irrf:
        return obter_situacao_imposto_por_nome("Sem IRRF")

    if situacao_imposto_id is None:
        return obter_situacao_imposto_por_nome("Sem Tratamento")

    situacao = db.session.get(SituacaoImposto, int(situacao_imposto_id))
    if not situacao or not situacao.ativo:
        raise ValueError("Situação do imposto inválida.")

    if situacao_imposto_eh_sem_irrf(situacao):
        return obter_situacao_imposto_por_nome("Sem Tratamento")

    return situacao


def situacao_imposto_oculta_na_fila(situacao: SituacaoImposto | None) -> bool:
    nome = unidecode(str(getattr(situacao, "nome", "") or "").strip()).lower()
    return nome in {"sem irrf", "pago", "concluida", "cancelado", "cancelada"}


def parse_checkbox(valor: str | None) -> bool:
    return str(valor or "").strip().lower() in {"1", "true", "on", "yes"}


LIMITE_ALERTA_IRRF = Decimal("5040.00")


def normalizar_tipo_para_alerta(nome_tipo: str) -> str:
    return unidecode((nome_tipo or "").strip()).lower()


def precisa_alerta_irrf(
    tipo_rpv_nome: str,
    valor_bruto,
    sem_irrf: bool = False,
    valor_irrf=None,
) -> bool:
    if valor_bruto is None or sem_irrf or valor_irrf is not None:
        return False

    tipo_normalizado = normalizar_tipo_para_alerta(tipo_rpv_nome)

    tipos_sensiveis = {
        "rpv honorarios",
        "honorarios",
        "rpv dativo",
        "dativo",
    }

    return tipo_normalizado in tipos_sensiveis and Decimal(valor_bruto) > LIMITE_ALERTA_IRRF


def obter_contexto_processo_existente(processo_existente: Processo):
    if not processo_existente:
        return None

    registros_mesmo_processo = listar_registros_mesmo_processo(processo_existente)
    registro_referencia = registros_mesmo_processo[0] if registros_mesmo_processo else None

    if not registro_referencia:
        return {
            "registro_id": None,
            "responsavel": "-",
            "resumo_operacional": "-",
            "situacao_empenho": "-",
            "situacao_imposto": "-",
        }

    return {
        "registro_id": registro_referencia.id,
        "responsavel": registro_referencia.elaborador.nome if registro_referencia.elaborador else "-",
        "resumo_operacional": registro_referencia.resumo_operacional,
        "situacao_empenho": (
            registro_referencia.situacao_empenho.nome
            if registro_referencia.situacao_empenho
            else "-"
        ),
        "situacao_imposto": (
            registro_referencia.situacao_imposto.nome
            if registro_referencia.situacao_imposto
            else "-"
        ),
    }


def listar_registros_mesmo_processo(processo_existente: Processo | None) -> list[RegistroRPV]:
    if not processo_existente:
        return []

    registros = (
        RegistroRPV.query
        .filter_by(processo_id=processo_existente.id, ativo=True)
        .order_by(RegistroRPV.criado_em.asc())
        .all()
    )
    return [registro for registro in registros if not registro.status_principal_cancelado]


def listar_registros_mesma_ci(
    processo_edoc: str | None,
    *,
    numero_processo_excluir: str | None = None,
) -> list[RegistroRPV]:
    processo_edoc_limpo = str(processo_edoc or "").strip()
    if not processo_edoc_limpo:
        return []

    query = (
        RegistroRPV.query.join(Processo)
        .filter(
            Processo.processo_edoc == processo_edoc_limpo,
            RegistroRPV.ativo.is_(True),
        )
        .order_by(Processo.data_ci.asc(), RegistroRPV.criado_em.asc())
    )

    numero_processo_excluir_limpo = str(numero_processo_excluir or "").strip()
    if numero_processo_excluir_limpo:
        query = query.filter(Processo.numero_processo != numero_processo_excluir_limpo)

    registros = query.all()
    return [registro for registro in registros if not registro.status_principal_cancelado]


def _processo_tem_mesma_ci(processo: Processo | None, processo_edoc: str | None) -> bool:
    if not processo:
        return False
    return str(processo.processo_edoc or "").strip() == str(processo_edoc or "").strip()


def _criar_processo_para_rpv(
    *,
    exercicio_operacional: str,
    processo_edoc: str,
    numero_processo: str,
    data_ci,
    usuario_id: int,
) -> Processo:
    processo = Processo(
        exercicio=exercicio_operacional,
        processo_edoc=processo_edoc,
        numero_processo=numero_processo,
        data_ci=data_ci,
        data_cadastro=utc_now_naive(),
        observacoes_gerais=None,
        criado_por_id=usuario_id,
        atualizado_por_id=usuario_id,
    )
    db.session.add(processo)
    db.session.flush()
    return processo


def _render_novo_rpv(
    *,
    tipos_rpv,
    situacoes_empenho,
    situacoes_imposto,
    usuarios,
    processo_existente=None,
    registro_existente_mesmo_documento=None,
    registros_mesmo_processo=None,
    registros_mesma_ci=None,
    contexto_processo_existente=None,
    alerta_irrf=False,
    ocorrencias_processo=None,
    form_data=None,
    pendencia_documental=None,
):
    return render_template(
        "cadastros/novo_rpv.html",
        tipos_rpv=tipos_rpv,
        situacoes_empenho=situacoes_empenho,
        situacoes_imposto=situacoes_imposto,
        usuarios=usuarios,
        processo_existente=processo_existente,
        registro_existente_mesmo_documento=registro_existente_mesmo_documento,
        registros_mesmo_processo=registros_mesmo_processo or [],
        registros_mesma_ci=registros_mesma_ci or [],
        contexto_processo_existente=contexto_processo_existente,
        alerta_irrf=alerta_irrf,
        ocorrencias_processo=ocorrencias_processo or [],
        form_data=form_data or {},
        pendencia_documental=pendencia_documental,
    )


def _pode_acessar_pendencia_documental(pendencia: RPVPendenciaDocumento) -> bool:
    return bool(
        getattr(current_user, "is_authenticated", False)
        and getattr(current_user, "ativo", False)
    )


def _filtro_responsavel_pendencia_documental(usuario_id: int):
    return or_(
        RPVPendenciaDocumento.responsavel_id == usuario_id,
        RPVPendenciaDocumento.criado_por_id == usuario_id,
    )


def _aplicar_filtro_responsavel_pendencia_documental(query, filtro_responsavel: str):
    filtro = str(filtro_responsavel or "").strip() or "todos"

    if filtro == "meus":
        return query.filter(
            _filtro_responsavel_pendencia_documental(current_user.id)
        ), "meus"

    if filtro in {"", "todos"}:
        return query, "todos"

    if filtro.isdigit():
        return query.filter(
            _filtro_responsavel_pendencia_documental(int(filtro))
        ), filtro

    return query, "todos"


def _carregar_pendencia_documental_da_requisicao() -> RPVPendenciaDocumento | None:
    valor = (request.form.get("pendencia_id") or request.args.get("pendencia_id") or "").strip()
    if not valor.isdigit():
        return None

    pendencia = (
        RPVPendenciaDocumento.query.options(
            joinedload(RPVPendenciaDocumento.tipo_rpv),
            joinedload(RPVPendenciaDocumento.responsavel),
        )
        .filter_by(id=int(valor))
        .first()
    )
    if not pendencia:
        raise ValueError("Pendencia documental nao encontrada.")
    if not _pode_acessar_pendencia_documental(pendencia):
        raise ValueError("Voce nao pode acessar essa pendencia documental.")
    if pendencia.status != "aberta":
        raise ValueError("Essa pendencia documental ja foi encerrada.")
    return pendencia


def _form_data_pendencia_documental(pendencia: RPVPendenciaDocumento) -> dict[str, str]:
    return {
        "pendencia_id": str(pendencia.id),
        "exercicio": pendencia.exercicio or "",
        "processo_edoc": pendencia.processo_edoc or "",
        "numero_processo": pendencia.numero_processo or "",
        "data_ci": pendencia.data_ci.strftime("%Y-%m-%d") if pendencia.data_ci else "",
        "tipo_rpv_id": str(pendencia.tipo_rpv_id or ""),
        "nome_beneficiario": pendencia.nome_beneficiario or "",
        "tipo_documento": pendencia.tipo_documento or "CPF",
        "documento_original": pendencia.documento_original or "",
        "elaborador_id": str(pendencia.responsavel_id or ""),
        "valor_bruto": str(pendencia.valor_bruto or ""),
        "valor_irrf": "" if pendencia.valor_irrf is None else str(pendencia.valor_irrf),
        "sem_irrf": "1" if pendencia.sem_irrf else "",
        "observacoes": pendencia.observacoes or "",
    }


def _coletar_dados_formulario_rpv(formulario) -> dict:
    exercicio = str(formulario.get("exercicio", "") or "").strip()
    processo_edoc = str(formulario.get("processo_edoc", "") or "").strip()
    numero_processo = str(formulario.get("numero_processo", "") or "").strip()
    data_ci = parse_date(str(formulario.get("data_ci", "") or "").strip())

    tipo_rpv_id_raw = str(formulario.get("tipo_rpv_id", "") or "").strip()
    if not tipo_rpv_id_raw.isdigit():
        raise ValueError("Tipo de RPV invalido.")
    tipo_rpv = db.session.get(TipoRPV, int(tipo_rpv_id_raw))
    if not tipo_rpv:
        raise ValueError("Tipo de RPV invalido.")

    nome_beneficiario = str(formulario.get("nome_beneficiario", "") or "").strip()
    tipo_documento = str(formulario.get("tipo_documento", "") or "").strip().upper()
    documento_original = str(formulario.get("documento_original", "") or "").strip()

    if tipo_documento not in {"CPF", "CNPJ"}:
        raise ValueError("Tipo de documento invalido.")

    valor_bruto_raw = str(formulario.get("valor_bruto", "") or "").strip()
    valor_irrf_raw = str(formulario.get("valor_irrf", "") or "").strip()

    return {
        "exercicio": exercicio,
        "processo_edoc": processo_edoc,
        "numero_processo": numero_processo,
        "data_ci": data_ci,
        "tipo_rpv_id": int(tipo_rpv_id_raw),
        "tipo_rpv": tipo_rpv,
        "nome_beneficiario": nome_beneficiario,
        "tipo_documento": tipo_documento,
        "documento_original": documento_original,
        "responsavel": _obter_usuario_responsavel(
            formulario.get("elaborador_id", str(current_user.id))
        ),
        "valor_bruto": parse_decimal(valor_bruto_raw),
        "valor_irrf": parse_decimal(valor_irrf_raw) if valor_irrf_raw else None,
        "sem_irrf": parse_checkbox(formulario.get("sem_irrf")),
        "observacoes": str(formulario.get("observacoes", "") or "").strip() or None,
    }


def _sincronizar_pendencia_documental(
    pendencia: RPVPendenciaDocumento,
    *,
    dados: dict,
    usuario_id: int,
) -> None:
    documento_anterior = normalizar_documento(pendencia.documento_original)
    tipo_anterior = str(pendencia.tipo_documento or "").strip().upper()
    validacao_nova = validar_documento_brasileiro(
        dados["documento_original"],
        dados["tipo_documento"],
    )
    documento_novo = str(validacao_nova["documento_normalizado"] or "")
    documento_alterado = (
        documento_anterior != documento_novo
        or tipo_anterior != str(dados["tipo_documento"]).strip().upper()
    )

    pendencia.exercicio = dados["exercicio"]
    pendencia.processo_edoc = dados["processo_edoc"]
    pendencia.numero_processo = dados["numero_processo"]
    pendencia.data_ci = dados["data_ci"]
    pendencia.tipo_rpv_id = dados["tipo_rpv_id"]
    pendencia.responsavel_id = dados["responsavel"].id
    pendencia.nome_beneficiario = dados["nome_beneficiario"]
    pendencia.tipo_documento = dados["tipo_documento"]
    pendencia.documento_original = dados["documento_original"]
    pendencia.valor_bruto = dados["valor_bruto"]
    pendencia.valor_irrf = dados["valor_irrf"]
    pendencia.sem_irrf = dados["sem_irrf"]
    pendencia.observacoes = dados["observacoes"]
    pendencia.atualizado_por_id = usuario_id

    if documento_alterado or bool(validacao_nova["valido"]):
        pendencia.documento_confirmado_manual = False
        pendencia.documento_confirmado_em = None
        pendencia.documento_confirmado_por_id = None

    pendencia.atualizar_campos_derivados()
    pendencia.status = "aberta"


def _criar_ou_atualizar_pendencia_documental(
    *,
    dados: dict,
    usuario_id: int,
    pendencia: RPVPendenciaDocumento | None = None,
) -> tuple[RPVPendenciaDocumento, bool]:
    nova = pendencia is None
    if nova:
        pendencia = RPVPendenciaDocumento(
            criado_por_id=usuario_id,
            atualizado_por_id=usuario_id,
            status="aberta",
        )
        db.session.add(pendencia)

    _sincronizar_pendencia_documental(
        pendencia,
        dados=dados,
        usuario_id=usuario_id,
    )
    if nova:
        db.session.flush()
    return pendencia, nova


def _resumo_confirmacao_processo(ocorrencias_processo: list[dict]) -> str:
    quantidade = len(ocorrencias_processo)
    exemplos = " | ".join(
        f"{ocorrencia['origem']}: {ocorrencia['resumo_operacional']}"
        for ocorrencia in ocorrencias_processo[:2]
    )

    resumo = f"Processo repetido confirmado pelo operador ({quantidade} ocorrência(s) prévias)."
    if exemplos:
        resumo = f"{resumo} Exemplos: {exemplos}"
    return resumo


def _resumo_confirmacao_ci(registros_mesma_ci: list[RegistroRPV]) -> str:
    quantidade = len(registros_mesma_ci)
    exemplos = " | ".join(
        f"{registro.processo.numero_processo if registro.processo else '-'}: {registro.nome_beneficiario}"
        for registro in registros_mesma_ci[:2]
    )
    resumo = f"C.I./eDOC repetida confirmada pelo operador ({quantidade} registro(s) previo(s))."
    if exemplos:
        resumo = f"{resumo} Exemplos: {exemplos}"
    return resumo


def _url_retorno_interna(padrao: str) -> str:
    return sanitize_internal_return_url(request.values.get("retorno"), padrao)


@cadastros_bp.route("/")
@login_required
def lista_rpvs():
    tipos_rpv, situacoes_empenho, situacoes_imposto, usuarios = carregar_opcoes()

    q = request.args.get("q", "").strip()
    filtro_ne = request.args.get("ne", "").strip()
    filtro_exercicio = request.args.get("exercicio", "").strip()
    filtro_responsavel = request.args.get("responsavel", "meus").strip() or "meus"
    filtro_empenho = request.args.get("situacao_empenho_id", "").strip()
    filtro_imposto = request.args.get("situacao_imposto_id", "").strip()
    mostrar_encerrados = should_include_closed_in_queue(
        request.args.get("mostrar_encerrados"),
        filtro_empenho or filtro_imposto,
    )
    situacoes_rpv_ocultas = collect_hidden_queue_status_ids(situacoes_empenho)
    situacoes_rpv_canceladas = {
        situacao.id
        for situacao in situacoes_empenho
        if str(getattr(situacao, "nome", "") or "").strip().casefold() == "cancelado"
    }
    situacoes_imposto_ocultas = {
        situacao.id
        for situacao in situacoes_imposto
        if situacao_imposto_oculta_na_fila(situacao)
    }
    ordenar = request.args.get("ordenar", "competencia").strip()
    direcao = sanitize_sort_direction(request.args.get("direcao"), padrao="desc")
    pagina = parse_page(request.args.get("pagina"), padrao=1)
    por_pagina = parse_page_size(request.args.get("por_pagina"), padrao=20)

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
        filtros = [
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
            filtros.append(Processo.numero_processo.ilike(f"%{q_processo}%"))
        if q_doc:
            filtros.append(RegistroRPV.documento_normalizado.ilike(f"%{q_doc}%"))
        query = query.filter(or_(*filtros))

    if filtro_ne:
        query = query.filter(RegistroRPV.nota_empenho.ilike(f"%{filtro_ne}%"))

    if filtro_exercicio:
        query = query.filter(Processo.exercicio == filtro_exercicio)

    if filtro_responsavel == "meus":
        query = query.filter(RegistroRPV.elaborador_id == current_user.id)
    elif filtro_responsavel not in ("", "todos"):
        query = query.filter(RegistroRPV.elaborador_id == int(filtro_responsavel))

    if filtro_empenho:
        query = query.filter(RegistroRPV.situacao_empenho_id == int(filtro_empenho))
    elif not mostrar_encerrados and situacoes_rpv_ocultas:
        filtros_fila = [~RegistroRPV.situacao_empenho_id.in_(situacoes_rpv_ocultas)]

        if situacoes_imposto_ocultas:
            fiscal_pendente = [
                ~RegistroRPV.situacao_imposto_id.in_(situacoes_imposto_ocultas),
                RegistroRPV.sem_irrf.is_(False),
            ]
            if situacoes_rpv_canceladas:
                fiscal_pendente.append(
                    ~RegistroRPV.situacao_empenho_id.in_(situacoes_rpv_canceladas)
                )
            filtros_fila.append(and_(*fiscal_pendente))

        query = query.filter(or_(*filtros_fila))

    if filtro_imposto:
        query = query.filter(RegistroRPV.situacao_imposto_id == int(filtro_imposto))

    ordenar_mapa = {
        "competencia": Processo.exercicio,
        "processo": Processo.numero_processo,
        "resumo": RegistroRPV.resumo_operacional,
        "valor": RegistroRPV.valor_bruto,
        "imposto": RegistroRPV.valor_irrf,
    }
    coluna_ordenacao = ordenar_mapa.get(ordenar, Processo.exercicio)
    clausula_ordenacao = (
        coluna_ordenacao.asc() if direcao == "asc" else coluna_ordenacao.desc()
    )

    total_registros = query.count()
    paginacao = build_pagination(total_registros, pagina, por_pagina)
    registros = (
        query.order_by(clausula_ordenacao, RegistroRPV.criado_em.desc())
        .offset((paginacao["pagina"] - 1) * paginacao["por_pagina"])
        .limit(paginacao["por_pagina"])
        .all()
    )

    filtros_dict = request.args.to_dict()
    filtros_ocultos = merge_query_params(
        filtros_dict,
        pagina=None,
        por_pagina=None,
    )
    sort_urls = {
        chave: url_for(
            "cadastros.lista_rpvs",
            **merge_query_params(
                filtros_dict,
                ordenar=chave,
                direcao=resolve_next_sort_direction(ordenar, direcao, chave),
                pagina=1,
            ),
        )
        for chave in ordenar_mapa
    }
    paginas_visiveis = build_page_window(
        paginacao["total_paginas"],
        paginacao["pagina"],
    )
    pagina_urls = {
        numero: url_for(
            "cadastros.lista_rpvs",
            **merge_query_params(filtros_dict, pagina=numero),
        )
        for numero in paginas_visiveis
    }
    pagina_anterior_url = (
        url_for(
            "cadastros.lista_rpvs",
            **merge_query_params(filtros_dict, pagina=paginacao["pagina_anterior"]),
        )
        if paginacao["tem_anterior"]
        else None
    )
    proxima_pagina_url = (
        url_for(
            "cadastros.lista_rpvs",
            **merge_query_params(filtros_dict, pagina=paginacao["proxima_pagina"]),
        )
        if paginacao["tem_proxima"]
        else None
    )
    url_retorno_atual = current_internal_url(url_for("cadastros.lista_rpvs"))
    busca_processo_contexto = ProcessoCrosscheckService.buscar_contexto_pesquisa(
        q or filtro_ne,
        retorno_url=url_retorno_atual,
    )
    total_pendencias_documentais = (
        RPVPendenciaDocumento.query.filter(
            RPVPendenciaDocumento.status == "aberta",
            or_(
                RPVPendenciaDocumento.responsavel_id == current_user.id,
                RPVPendenciaDocumento.criado_por_id == current_user.id,
            ),
        ).count()
    )
    return render_template(
        "cadastros/lista_rpvs.html",
        registros=registros,
        tipos_rpv=tipos_rpv,
        situacoes_empenho=situacoes_empenho,
        situacoes_imposto=situacoes_imposto,
        usuarios=usuarios,
        filtros=request.args,
        filtro_responsavel=filtro_responsavel,
        filtros_ocultos=filtros_ocultos,
        ordenar_atual=ordenar,
        direcao_atual=direcao,
        por_pagina=por_pagina,
        paginacao=paginacao,
        paginas_visiveis=paginas_visiveis,
        pagina_urls=pagina_urls,
        pagina_anterior_url=pagina_anterior_url,
        proxima_pagina_url=proxima_pagina_url,
        sort_urls=sort_urls,
        busca_processo_contexto=busca_processo_contexto,
        mostrar_encerrados=mostrar_encerrados,
        total_pendencias_documentais=total_pendencias_documentais,
        url_retorno_atual=url_retorno_atual,
    )


@cadastros_bp.route("/pendencias-documentais")
@login_required
def lista_pendencias_documentais():
    usuarios = User.query.filter_by(ativo=True).order_by(User.nome.asc()).all()
    filtro_responsavel = request.args.get("responsavel", "todos").strip() or "todos"
    filtro_status = request.args.get("status", "aberta").strip() or "aberta"
    filtro_q = request.args.get("q", "").strip()

    query = RPVPendenciaDocumento.query.options(
        joinedload(RPVPendenciaDocumento.tipo_rpv),
        joinedload(RPVPendenciaDocumento.responsavel),
        joinedload(RPVPendenciaDocumento.criado_por),
    ).order_by(RPVPendenciaDocumento.criado_em.desc(), RPVPendenciaDocumento.id.desc())

    if filtro_status != "todas":
        query = query.filter(RPVPendenciaDocumento.status == filtro_status)

    if filtro_q:
        q_like = f"%{filtro_q}%"
        q_doc = normalizar_documento(filtro_q)
        filtros_busca = [
            RPVPendenciaDocumento.nome_beneficiario.ilike(q_like),
            RPVPendenciaDocumento.processo_edoc.ilike(q_like),
            RPVPendenciaDocumento.numero_processo.ilike(q_like),
            RPVPendenciaDocumento.observacoes.ilike(q_like),
        ]
        if q_doc:
            filtros_busca.append(RPVPendenciaDocumento.documento_normalizado.ilike(f"%{q_doc}%"))
        query = query.filter(or_(*filtros_busca))

    query, filtro_responsavel = _aplicar_filtro_responsavel_pendencia_documental(
        query,
        filtro_responsavel,
    )

    pendencias = query.all()
    total_abertas = sum(1 for pendencia in pendencias if pendencia.status == "aberta")
    total_prontas = sum(
        1
        for pendencia in pendencias
        if pendencia.status == "aberta" and pendencia.pode_continuar_fluxo_oficial
    )
    total_sem_documento = sum(
        1
        for pendencia in pendencias
        if pendencia.status == "aberta" and pendencia.documento_ausente
    )
    total_valor_aberto = sum(
        (Decimal(pendencia.valor_bruto or 0) for pendencia in pendencias if pendencia.status == "aberta"),
        Decimal("0.00"),
    )

    return render_template(
        "cadastros/lista_pendencias_documentais.html",
        pendencias=pendencias,
        usuarios=usuarios,
        filtro_responsavel=filtro_responsavel,
        filtro_status=filtro_status,
        filtro_q=filtro_q,
        total_abertas=total_abertas,
        total_prontas=total_prontas,
        total_sem_documento=total_sem_documento,
        total_valor_aberto=total_valor_aberto,
    )


@cadastros_bp.route("/pendencias-documentais/<int:pendencia_id>", methods=["GET", "POST"])
@login_required
def detalhe_pendencia_documental(pendencia_id):
    pendencia = (
        RPVPendenciaDocumento.query.options(
            joinedload(RPVPendenciaDocumento.tipo_rpv),
            joinedload(RPVPendenciaDocumento.responsavel),
            joinedload(RPVPendenciaDocumento.criado_por),
            joinedload(RPVPendenciaDocumento.documento_confirmado_por),
        )
        .filter_by(id=pendencia_id)
        .first_or_404()
    )

    if not _pode_acessar_pendencia_documental(pendencia):
        flash("Voce nao pode acessar essa pendencia documental.", "danger")
        return redirect(url_for("cadastros.lista_pendencias_documentais"))

    tipos_rpv, _, _, usuarios = carregar_opcoes()

    if request.method == "POST":
        try:
            acao = request.form.get("acao", "salvar").strip()
            antes = snapshot_entidade("rpv_pendencia_documento", pendencia)
            dados = _coletar_dados_formulario_rpv(request.form)

            if acao == "reabrir_pendencia":
                if pendencia.status != "descartada":
                    flash("Somente cadastros cancelados podem ser reabertos.", "info")
                    return redirect(
                        url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
                    )

                _sincronizar_pendencia_documental(
                    pendencia,
                    dados=dados,
                    usuario_id=current_user.id,
                )
                pendencia.status = "aberta"
                pendencia.atualizado_por_id = current_user.id
                registrar_evento(
                    entidade_tipo="rpv_pendencia_documento",
                    entidade_id=pendencia.id,
                    usuario_id=current_user.id,
                    acao="Reabertura do cadastro em revisao",
                    antes=antes,
                    depois=snapshot_entidade("rpv_pendencia_documento", pendencia),
                    resumo="O cadastro voltou para a fila de revisao documental.",
                    forcar_registro=True,
                )
                db.session.commit()
                flash("Cadastro reaberto para revisao documental.", "success")
                return redirect(
                    url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
                )

            if pendencia.status != "aberta":
                flash(
                    "Esse cadastro ja foi encerrado. Reabra a revisao antes de editar ou seguir.",
                    "warning",
                )
                return redirect(
                    url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
                )

            _sincronizar_pendencia_documental(
                pendencia,
                dados=dados,
                usuario_id=current_user.id,
            )

            if acao == "confirmar_documento":
                if pendencia.documento_valido:
                    flash("O documento ja foi validado automaticamente.", "info")
                    return redirect(
                        url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
                    )

                pendencia.documento_confirmado_manual = True
                pendencia.documento_confirmado_em = utc_now_naive()
                pendencia.documento_confirmado_por_id = current_user.id
                pendencia.atualizar_campos_derivados()
                registrar_evento(
                    entidade_tipo="rpv_pendencia_documento",
                    entidade_id=pendencia.id,
                    usuario_id=current_user.id,
                    acao="Conferencia manual de documento",
                    antes=antes,
                    depois=snapshot_entidade("rpv_pendencia_documento", pendencia),
                    resumo=(
                        "Conferencia registrada sem liberar o cadastro para o fluxo oficial. "
                        "A pendencia continua aguardando CPF/CNPJ valido."
                    ),
                    forcar_registro=True,
                )
                db.session.commit()
                flash(
                    "Conferencia registrada. A pendencia continua fora do fluxo oficial ate receber CPF/CNPJ valido.",
                    "warning",
                )
                return redirect(
                    url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
                )

            if acao == "reabrir_validacao":
                pendencia.documento_confirmado_manual = False
                pendencia.documento_confirmado_em = None
                pendencia.documento_confirmado_por_id = None
                pendencia.atualizar_campos_derivados()
                registrar_evento(
                    entidade_tipo="rpv_pendencia_documento",
                    entidade_id=pendencia.id,
                    usuario_id=current_user.id,
                    acao="Remocao da conferencia manual",
                    antes=antes,
                    depois=snapshot_entidade("rpv_pendencia_documento", pendencia),
                    resumo="A marca de conferencia manual foi retirada da pendencia documental.",
                    forcar_registro=True,
                )
                db.session.commit()
                flash("A conferencia manual foi retirada da pendencia.", "info")
                return redirect(
                    url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
                )

            if acao == "descartar":
                pendencia.status = "descartada"
                pendencia.atualizado_por_id = current_user.id
                registrar_evento(
                    entidade_tipo="rpv_pendencia_documento",
                    entidade_id=pendencia.id,
                    usuario_id=current_user.id,
                    acao="Cancelamento do cadastro em revisao",
                    antes=antes,
                    depois=snapshot_entidade("rpv_pendencia_documento", pendencia),
                    resumo="O cadastro foi encerrado sem seguir para o fluxo oficial.",
                    forcar_registro=True,
                )
                db.session.commit()
                flash("Cadastro cancelado com seguranca. O historico foi preservado.", "info")
                return redirect(url_for("cadastros.lista_pendencias_documentais"))

            registrar_evento(
                entidade_tipo="rpv_pendencia_documento",
                entidade_id=pendencia.id,
                usuario_id=current_user.id,
                acao="Atualizacao da pendencia documental",
                antes=antes,
                depois=snapshot_entidade("rpv_pendencia_documento", pendencia),
            )
            db.session.commit()
            flash("Pendencia documental atualizada com sucesso.", "success")
            return redirect(
                url_for("cadastros.detalhe_pendencia_documental", pendencia_id=pendencia.id)
            )

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except InvalidOperation:
            db.session.rollback()
            flash("Verifique os campos numericos e datas informados.", "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao atualizar pendencia documental: {exc}", "danger")

    return render_template(
        "cadastros/detalhe_pendencia_documental.html",
        pendencia=pendencia,
        tipos_rpv=tipos_rpv,
        usuarios=usuarios,
    )


@cadastros_bp.route("/<int:registro_id>/atualizacao-rapida", methods=["POST"])
@login_required
def atualizacao_rapida_rpv(registro_id):
    registro = RegistroRPV.query.get_or_404(registro_id)
    antes = snapshot_entidade("registro_rpv", registro)

    try:
        situacao_empenho_id = request.form.get("situacao_empenho_id")
        situacao_imposto_id = request.form.get("situacao_imposto_id")
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_empenho_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_empenho_id)
        competencia_pagamento = competencia_pagamento_automatica()

        nota_empenho = request.form.get("nota_empenho", "").strip() or None
        ordem_bancaria = request.form.get("ordem_bancaria", "").strip() or None
        validar_referencias_pagamento_principal(
            registro,
            nota_empenho=nota_empenho,
            ordem_bancaria=ordem_bancaria,
            exigir_preenchimento=status_quita_pagamento and not status_cancelado,
        )

        registro.nota_empenho = nota_empenho
        registro.ordem_bancaria = ordem_bancaria
        registro.ob_imposto = request.form.get("ob_imposto", "").strip() or None
        registro.situacao_empenho_id = int(situacao_empenho_id)
        situacao_imposto = resolver_situacao_imposto_rpv(
            sem_irrf=registro.sem_irrf_efetivo,
            situacao_imposto_id=int(situacao_imposto_id) if situacao_imposto_id else None,
        )
        registro.situacao_imposto_id = situacao_imposto.id
        registro.data_pagamento = resolver_data_pagamento_por_status(
            data_atual=registro.data_pagamento,
            status_pago=status_quita_pagamento,
            status_cancelado=status_cancelado,
            competencia=competencia_pagamento,
        )
        if status_quita_pagamento and not status_cancelado:
            registro.processo.exercicio = resolver_exercicio_operacional(
                getattr(registro.processo, "exercicio", None),
                registro.data_pagamento,
            )
            registro.processo.atualizado_por_id = current_user.id
        if status_cancelado:
            registro.data_pagamento_irrf = None
            registro.reinf_status = None
        registro.atualizado_por_id = current_user.id
        _registrar_historico_rpv(
            registro,
            usuario_id=current_user.id,
            acao="Atualização rápida",
            antes=antes,
        )

        db.session.commit()
        flash("Atualização rápida salva com sucesso.", "success")
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro na atualização rápida: {exc}", "danger")

    return redirect(url_for("cadastros.lista_rpvs", **request.args.to_dict()))


@cadastros_bp.route("/atualizacao-lote", methods=["POST"])
@login_required
def atualizacao_lote_rpvs():
    selecionados = [
        int(registro_id)
        for registro_id in request.form.getlist("selecionados")
        if str(registro_id).strip().isdigit()
    ]
    situacao_empenho_id = request.form.get("situacao_empenho_id_lote", "").strip()
    situacao_imposto_id = request.form.get("situacao_imposto_id_lote", "").strip()

    if not selecionados:
        flash("Selecione pelo menos um RPV para atualizar em lote.", "warning")
        return redirect(url_for("cadastros.lista_rpvs", **request.args.to_dict()))

    if not situacao_empenho_id and not situacao_imposto_id:
        flash("Escolha ao menos uma situação para aplicar no lote.", "warning")
        return redirect(url_for("cadastros.lista_rpvs", **request.args.to_dict()))

    try:
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_empenho_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_empenho_id)
        registros = (
            RegistroRPV.query.filter(RegistroRPV.id.in_(selecionados))
            .order_by(RegistroRPV.id.asc())
            .all()
        )

        for registro in registros:
            antes = snapshot_entidade("registro_rpv", registro)
            if situacao_empenho_id:
                if status_quita_pagamento and not status_cancelado:
                    validar_referencias_pagamento_principal(
                        registro,
                        nota_empenho=registro.nota_empenho,
                        ordem_bancaria=registro.ordem_bancaria,
                        exigir_preenchimento=True,
                    )
                competencia_pagamento = competencia_pagamento_automatica()
                registro.situacao_empenho_id = int(situacao_empenho_id)
                registro.data_pagamento = resolver_data_pagamento_por_status(
                    data_atual=registro.data_pagamento,
                    status_pago=status_quita_pagamento,
                    status_cancelado=status_cancelado,
                    competencia=competencia_pagamento,
                )
                if status_quita_pagamento and not status_cancelado:
                    registro.processo.exercicio = resolver_exercicio_operacional(
                        getattr(registro.processo, "exercicio", None),
                        registro.data_pagamento,
                    )
                    registro.processo.atualizado_por_id = current_user.id
                if status_cancelado:
                    registro.data_pagamento_irrf = None
                    registro.reinf_status = None

            if situacao_imposto_id:
                situacao_imposto = resolver_situacao_imposto_rpv(
                    sem_irrf=registro.sem_irrf_efetivo,
                    situacao_imposto_id=int(situacao_imposto_id),
                )
                registro.situacao_imposto_id = situacao_imposto.id

            registro.atualizado_por_id = current_user.id
            _registrar_historico_rpv(
                registro,
                usuario_id=current_user.id,
                acao="Atualização em lote",
                antes=antes,
                resumo="Ação aplicada em lote",
            )

        db.session.commit()
        flash(f"Lote de RPVs atualizado com sucesso ({len(registros)} registro(s)).", "success")
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro na atualização em lote dos RPVs: {exc}", "danger")

    return redirect(url_for("cadastros.lista_rpvs", **request.args.to_dict()))


@cadastros_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo_rpv():
    tipos_rpv, situacoes_empenho, situacoes_imposto, usuarios = carregar_opcoes()

    processo_existente = None
    registro_existente_mesmo_documento = None
    registros_mesmo_processo = []
    registros_mesma_ci = []
    contexto_processo_existente = None
    alerta_irrf = False
    try:
        pendencia_documental = _carregar_pendencia_documental_da_requisicao()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("cadastros.lista_pendencias_documentais"))

    form_data = (
        request.form.to_dict(flat=True)
        if request.method == "POST"
        else (
            _form_data_pendencia_documental(pendencia_documental)
            if pendencia_documental
            else {}
        )
    )

    if request.method == "POST":
        try:
            dados = _coletar_dados_formulario_rpv(request.form)
            exercicio = dados["exercicio"]
            processo_edoc = dados["processo_edoc"]
            numero_processo = dados["numero_processo"]
            responsavel = dados["responsavel"]
            ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(
                numero_processo,
                excluir_pendencia_id=pendencia_documental.id if pendencia_documental else None,
            )
            confirmar_processo_existente = (
                request.form.get("confirmar_processo_existente") == "1"
            )
            confirmar_ci_existente = request.form.get("confirmar_ci_existente") == "1"
            data_ci = dados["data_ci"]

            tipo_rpv_id = dados["tipo_rpv_id"]
            tipo_rpv = dados["tipo_rpv"]

            if not tipo_rpv:
                raise ValueError("Tipo de RPV inválido.")
            nome_beneficiario = dados["nome_beneficiario"]
            tipo_documento = dados["tipo_documento"]
            documento_original = dados["documento_original"]

            data_pagamento_raw = request.form.get("data_pagamento", "").strip()
            data_pagamento = parse_date(data_pagamento_raw) if data_pagamento_raw else None
            exercicio_operacional = resolver_exercicio_operacional(exercicio, data_pagamento)

            valor_bruto = dados["valor_bruto"]
            valor_irrf = dados["valor_irrf"]
            sem_irrf = dados["sem_irrf"]
            situacao_empenho = obter_situacao_empenho_inicial()
            situacao_imposto = obter_situacao_imposto_inicial(sem_irrf=sem_irrf)

            confirmar_alerta_irrf = (
                request.form.get("confirmar_alerta_irrf") == "1"
            )

            validacao_documento = validar_documento_brasileiro(documento_original, tipo_documento)

            if not validacao_documento["valido"]:
                antes_pendencia = (
                    snapshot_entidade("rpv_pendencia_documento", pendencia_documental)
                    if pendencia_documental
                    else None
                )
                pendencia_documental, pendencia_nova = _criar_ou_atualizar_pendencia_documental(
                    dados=dados,
                    usuario_id=current_user.id,
                    pendencia=pendencia_documental,
                )
                registrar_evento(
                    entidade_tipo="rpv_pendencia_documento",
                    entidade_id=pendencia_documental.id,
                    usuario_id=current_user.id,
                    acao=(
                        "Criacao automatica de pendencia documental"
                        if pendencia_nova
                        else "Atualizacao automatica da pendencia documental"
                    ),
                    antes=antes_pendencia,
                    depois=snapshot_entidade("rpv_pendencia_documento", pendencia_documental),
                    resumo="Cadastro oficial interrompido por validacao documental automatica.",
                    forcar_registro=pendencia_nova,
                )
                db.session.commit()
                flash(
                    f"{validacao_documento['motivo']} A RPV ficou na aba de pendencias documentais ate receber CPF/CNPJ valido.",
                    "warning",
                )
                return redirect(
                    url_for(
                        "cadastros.detalhe_pendencia_documental",
                        pendencia_id=pendencia_documental.id,
                    )
                )

            if ocorrencias_processo and not confirmar_processo_existente:
                flash(
                    "Este número de processo já existe em outro ponto do sistema. Confira antes de continuar.",
                    "danger",
                )
                return _render_novo_rpv(
                    tipos_rpv=tipos_rpv,
                    situacoes_empenho=situacoes_empenho,
                    situacoes_imposto=situacoes_imposto,
                    usuarios=usuarios,
                    ocorrencias_processo=ocorrencias_processo,
                    form_data=form_data,
                    pendencia_documental=pendencia_documental,
                )

            processos_mesmo_numero = (
                Processo.query.filter_by(numero_processo=numero_processo)
                .order_by(Processo.id.asc())
                .all()
            )
            processo_mesma_ci = next(
                (
                    processo_candidato
                    for processo_candidato in processos_mesmo_numero
                    if _processo_tem_mesma_ci(processo_candidato, processo_edoc)
                ),
                None,
            )
            processo_existente = (
                processo_mesma_ci
                or (processos_mesmo_numero[0] if processos_mesmo_numero else None)
            )

            documento_normalizado = normalizar_documento(documento_original)

            if processo_existente:
                registros_mesmo_processo = listar_registros_mesmo_processo(processo_existente)
                contexto_processo_existente = obter_contexto_processo_existente(processo_existente)
                registro_existente_mesmo_documento = next(
                    (
                        registro_existente
                        for registro_existente in registros_mesmo_processo
                        if registro_existente.documento_normalizado == documento_normalizado
                    ),
                    None,
                )

                if registros_mesmo_processo and not confirmar_processo_existente:
                    flash(
                        "Este número de processo já existe. Confira o contexto abaixo antes de continuar.",
                        "danger",
                    )
                    return _render_novo_rpv(
                        tipos_rpv=tipos_rpv,
                        situacoes_empenho=situacoes_empenho,
                        situacoes_imposto=situacoes_imposto,
                        usuarios=usuarios,
                        processo_existente=processo_existente,
                        registro_existente_mesmo_documento=registro_existente_mesmo_documento,
                        registros_mesmo_processo=registros_mesmo_processo,
                        contexto_processo_existente=contexto_processo_existente,
                        ocorrencias_processo=ocorrencias_processo,
                        form_data=form_data,
                        pendencia_documental=pendencia_documental,
                    )

                if processo_mesma_ci:
                    processo = processo_mesma_ci
                    processo.exercicio = exercicio_operacional
                    processo.atualizado_por_id = current_user.id
                    processo.atualizado_em = utc_now_naive()
                else:
                    processo = _criar_processo_para_rpv(
                        exercicio_operacional=exercicio_operacional,
                        processo_edoc=processo_edoc,
                        numero_processo=numero_processo,
                        data_ci=data_ci,
                        usuario_id=current_user.id,
                    )
            else:
                processo = _criar_processo_para_rpv(
                    exercicio_operacional=exercicio_operacional,
                    processo_edoc=processo_edoc,
                    numero_processo=numero_processo,
                    data_ci=data_ci,
                    usuario_id=current_user.id,
                )

            registros_mesma_ci = listar_registros_mesma_ci(
                processo_edoc,
                numero_processo_excluir=numero_processo,
            )
            if registros_mesma_ci and not confirmar_ci_existente:
                flash(
                    "Esta C.I./eDOC ja existe em outros RPVs normais. Confira o contexto abaixo antes de continuar.",
                    "danger",
                )
                return _render_novo_rpv(
                    tipos_rpv=tipos_rpv,
                    situacoes_empenho=situacoes_empenho,
                    situacoes_imposto=situacoes_imposto,
                    usuarios=usuarios,
                    processo_existente=processo_existente,
                    registro_existente_mesmo_documento=registro_existente_mesmo_documento,
                    registros_mesmo_processo=registros_mesmo_processo,
                    registros_mesma_ci=registros_mesma_ci,
                    contexto_processo_existente=contexto_processo_existente,
                    alerta_irrf=False,
                    ocorrencias_processo=ocorrencias_processo,
                    form_data=form_data,
                    pendencia_documental=pendencia_documental,
                )

            alerta_irrf = precisa_alerta_irrf(
                tipo_rpv.nome,
                valor_bruto,
                sem_irrf=sem_irrf,
                valor_irrf=valor_irrf,
            )

            if alerta_irrf and not confirmar_alerta_irrf:
                flash(
                    "Este lançamento atende à regra operacional de incidência de IRRF. Revise antes de continuar.",
                    "info",
                )
                return _render_novo_rpv(
                    tipos_rpv=tipos_rpv,
                    situacoes_empenho=situacoes_empenho,
                    situacoes_imposto=situacoes_imposto,
                    usuarios=usuarios,
                    processo_existente=processo_existente,
                    registro_existente_mesmo_documento=registro_existente_mesmo_documento,
                    registros_mesmo_processo=registros_mesmo_processo,
                    registros_mesma_ci=registros_mesma_ci,
                    contexto_processo_existente=contexto_processo_existente,
                    alerta_irrf=True,
                    ocorrencias_processo=ocorrencias_processo,
                    form_data=form_data,
                    pendencia_documental=pendencia_documental,
                )

            registro = RegistroRPV(
                processo_id=processo.id,
                elaborador_id=responsavel.id,
                tipo_rpv_id=tipo_rpv_id,
                nome_beneficiario=nome_beneficiario,
                nome_beneficiario_normalizado="",
                tipo_documento=tipo_documento,
                documento_original=documento_original,
                documento_normalizado="",
                documento_corrigido=None,
                data_pagamento=data_pagamento,
                data_pagamento_irrf=None,
                valor_bruto=valor_bruto,
                valor_irrf=valor_irrf,
                valor_liquido=Decimal("0.00"),
                possui_irrf=False,
                sem_irrf=sem_irrf,
                imposto_texto=None,
                nota_empenho=None,
                numero_se=None,
                situacao_empenho_id=situacao_empenho.id,
                situacao_imposto_id=situacao_imposto.id,
                ordem_bancaria=None,
                reinf_status=None,
                ob_imposto=None,
                historico_auto="",
                observacoes=dados["observacoes"],
                ativo=True,
                criado_por_id=current_user.id,
                atualizado_por_id=current_user.id,
            )

            registro.atualizar_campos_derivados()
            registro.gerar_historico_auto(
                processo_edoc=processo.processo_edoc,
                numero_processo=processo.numero_processo,
                descricao=tipo_rpv.nome,
                data_ci=processo.data_ci,
            )

            db.session.add(registro)
            db.session.flush()
            _registrar_historico_rpv(
                registro,
                usuario_id=current_user.id,
                acao="Cadastro",
                resumo="Registro criado",
                forcar_registro=True,
            )
            if confirmar_processo_existente and ocorrencias_processo:
                registrar_evento(
                    entidade_tipo="registro_rpv",
                    entidade_id=registro.id,
                    usuario_id=current_user.id,
                    acao="Confirmação de repetição de processo",
                    resumo=_resumo_confirmacao_processo(ocorrencias_processo),
                )
            if confirmar_ci_existente and registros_mesma_ci:
                registrar_evento(
                    entidade_tipo="registro_rpv",
                    entidade_id=registro.id,
                    usuario_id=current_user.id,
                    acao="Confirmação de C.I./eDOC repetida",
                    resumo=_resumo_confirmacao_ci(registros_mesma_ci),
                )
            if pendencia_documental:
                _sincronizar_pendencia_documental(
                    pendencia_documental,
                    dados=dados,
                    usuario_id=current_user.id,
                )
                antes_pendencia = snapshot_entidade("rpv_pendencia_documento", pendencia_documental)
                pendencia_documental.status = "convertida"
                pendencia_documental.registro_rpv_convertido_id = registro.id
                pendencia_documental.atualizado_por_id = current_user.id
                registrar_evento(
                    entidade_tipo="rpv_pendencia_documento",
                    entidade_id=pendencia_documental.id,
                    usuario_id=current_user.id,
                    acao="Conversao em RPV oficial",
                    antes=antes_pendencia,
                    depois=snapshot_entidade("rpv_pendencia_documento", pendencia_documental),
                    resumo=(
                        "Pendencia documental convertida no fluxo oficial."
                        if validacao_documento["valido"]
                        else "Pendencia documental convertida."
                    ),
                    forcar_registro=True,
                )
            db.session.commit()

            flash("RPV cadastrada com sucesso.", "success")
            return redirect(url_for("cadastros.lista_rpvs"))

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except InvalidOperation:
            db.session.rollback()
            flash("Verifique os campos numéricos e datas informados.", "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao cadastrar RPV: {exc}", "danger")

    return _render_novo_rpv(
        tipos_rpv=tipos_rpv,
        situacoes_empenho=situacoes_empenho,
        situacoes_imposto=situacoes_imposto,
        usuarios=usuarios,
        processo_existente=processo_existente,
        registro_existente_mesmo_documento=registro_existente_mesmo_documento,
        registros_mesmo_processo=registros_mesmo_processo,
        registros_mesma_ci=registros_mesma_ci,
        contexto_processo_existente=contexto_processo_existente,
        alerta_irrf=alerta_irrf,
        ocorrencias_processo=[],
        form_data=form_data,
        pendencia_documental=pendencia_documental,
    )


@cadastros_bp.route("/<int:registro_id>/transferir-responsavel", methods=["POST"])
@login_required
def transferir_responsavel_rpv(registro_id):
    registro = RegistroRPV.query.get_or_404(registro_id)
    url_retorno = _url_retorno_interna(url_for("cadastros.lista_rpvs"))

    try:
        responsavel = _obter_usuario_responsavel(request.form.get("elaborador_id"))

        if registro.elaborador_id == responsavel.id:
            flash("Esse RPV já está com o responsável selecionado.", "info")
            return redirect(
                url_for("cadastros.editar_rpv", registro_id=registro.id, retorno=url_retorno)
            )

        antes = snapshot_entidade("registro_rpv", registro)
        registro.elaborador_id = responsavel.id
        registro.atualizado_por_id = current_user.id
        _registrar_historico_rpv(
            registro,
            usuario_id=current_user.id,
            acao="Transferência de responsabilidade",
            antes=antes,
            resumo=f"Responsabilidade transferida para {responsavel.nome}.",
        )
        db.session.commit()
        flash("Responsável do RPV atualizado com sucesso.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao transferir responsabilidade do RPV: {exc}", "danger")

    return redirect(url_for("cadastros.editar_rpv", registro_id=registro.id, retorno=url_retorno))


@cadastros_bp.route("/<int:registro_id>/editar", methods=["GET", "POST"])
@login_required
def editar_rpv(registro_id):
    registro = RegistroRPV.query.get_or_404(registro_id)
    processo = registro.processo
    url_retorno = _url_retorno_interna(url_for("cadastros.lista_rpvs"))

    tipos_rpv, situacoes_empenho, situacoes_imposto, usuarios = carregar_opcoes()

    if request.method == "POST":
        try:
            antes = snapshot_entidade("registro_rpv", registro)
            valor_bruto = parse_decimal(request.form.get("valor_bruto", "").strip())
            if valor_bruto is None:
                raise ValueError("Valor bruto e obrigatorio.")
            if (
                _valor_bruto_alterado(registro.valor_bruto, valor_bruto)
                and request.form.get("confirmar_edicao_valor_bruto") != "1"
            ):
                flash(
                    "Confirme a edicao do valor bruto antes de salvar. "
                    "A correcao recalcula valores e fica registrada no historico.",
                    "warning",
                )
                return redirect(
                    url_for("cadastros.editar_rpv", registro_id=registro.id, retorno=url_retorno)
                )

            documento_original_anterior = registro.documento_original
            tipo_documento_anterior = registro.tipo_documento
            registro.tipo_rpv_id = int(request.form.get("tipo_rpv_id"))
            registro.nome_beneficiario = request.form.get("nome_beneficiario", "").strip()
            registro.tipo_documento = request.form.get("tipo_documento", "").strip()
            registro.documento_original = request.form.get("documento_original", "").strip()
            validacao_documento = validar_documento_brasileiro(
                registro.documento_original,
                registro.tipo_documento,
            )
            documento_alterado = (
                normalizar_documento(documento_original_anterior)
                != normalizar_documento(registro.documento_original)
                or str(tipo_documento_anterior or "").strip().upper()
                != str(registro.tipo_documento or "").strip().upper()
            )
            if documento_alterado and not validacao_documento["valido"]:
                raise ValueError(
                    f"{validacao_documento['motivo']} Corrija o documento aqui ou trate o caso pela aba de pendencias documentais antes de seguir."
                )

            exercicio_informado = request.form.get("exercicio", "").strip()
            data_pagamento_raw = request.form.get("data_pagamento", "").strip()
            situacao_empenho_id = request.form.get("situacao_empenho_id")
            status_quita_pagamento = situacao_id_quita_pagamento_principal(
                SituacaoEmpenho, situacao_empenho_id
            )
            status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_empenho_id)

            if (
                data_pagamento_manual_exige_confirmacao(
                    registro.data_pagamento,
                    data_pagamento_raw,
                    parser=parse_date,
                )
                and request.form.get("confirmar_data_pagamento_manual") != "1"
            ):
                flash(
                    "Confirme a alteracao manual da data do pagamento principal. "
                    "BI, competencia operacional e a fila mensal da REINF passarao a seguir essa data.",
                    "warning",
                )
                return redirect(
                    url_for("cadastros.editar_rpv", registro_id=registro.id, retorno=url_retorno)
                )

            competencia_pagamento = (
                competencia_pagamento_automatica()
                if status_quita_pagamento
                else (exercicio_informado or getattr(processo, "exercicio", None))
            )
            registro.data_pagamento = resolver_data_pagamento_por_status(
                data_atual=registro.data_pagamento,
                status_pago=status_quita_pagamento,
                status_cancelado=status_cancelado,
                valor_informado=data_pagamento_raw,
                parser=parse_date,
                competencia=competencia_pagamento,
            )
            data_pagamento_irrf_raw = request.form.get("data_pagamento_irrf", "").strip()
            processo.exercicio = resolver_exercicio_operacional(
                competencia_pagamento or exercicio_informado,
                registro.data_pagamento,
            )

            registro.valor_bruto = valor_bruto

            valor_irrf_raw = request.form.get("valor_irrf", "").strip()
            registro.valor_irrf = parse_decimal(valor_irrf_raw) if valor_irrf_raw else None
            registro.sem_irrf = parse_checkbox(request.form.get("sem_irrf"))

            nota_empenho = request.form.get("nota_empenho", "").strip() or None
            numero_se = request.form.get("numero_se", "").strip() or None
            ordem_bancaria = request.form.get("ordem_bancaria", "").strip() or None
            validar_referencias_pagamento_principal(
                registro,
                nota_empenho=nota_empenho,
                ordem_bancaria=ordem_bancaria,
                exigir_preenchimento=status_quita_pagamento and not status_cancelado,
            )
            registro.nota_empenho = nota_empenho
            registro.numero_se = numero_se
            registro.ordem_bancaria = ordem_bancaria
            registro.observacoes = request.form.get("observacoes", "").strip() or None

            registro.situacao_empenho_id = int(situacao_empenho_id)
            situacao_imposto = resolver_situacao_imposto_rpv(
                sem_irrf=registro.sem_irrf,
                situacao_imposto_id=(
                    int(request.form.get("situacao_imposto_id"))
                    if request.form.get("situacao_imposto_id")
                    else None
                ),
            )
            registro.situacao_imposto_id = situacao_imposto.id
            if status_cancelado or registro.sem_irrf:
                registro.ob_imposto = None
                registro.data_pagamento_irrf = None
                registro.reinf_status = None
            else:
                registro.ob_imposto = request.form.get("ob_imposto", "").strip() or None
                registro.data_pagamento_irrf = (
                    parse_date(data_pagamento_irrf_raw) if data_pagamento_irrf_raw else None
                )
            registro.atualizado_por_id = current_user.id
            processo.atualizado_por_id = current_user.id

            tipo_rpv = db.session.get(TipoRPV, registro.tipo_rpv_id)

            registro.atualizar_campos_derivados()
            registro.gerar_historico_auto(
                processo_edoc=processo.processo_edoc,
                numero_processo=processo.numero_processo,
                descricao=tipo_rpv.nome,
                data_ci=processo.data_ci,
            )
            _registrar_historico_rpv(
                registro,
                usuario_id=current_user.id,
                acao="Alteração manual",
                antes=antes,
            )

            db.session.commit()
            flash("RPV atualizada com sucesso.", "success")
            return redirect(
                url_for("cadastros.editar_rpv", registro_id=registro.id, retorno=url_retorno)
            )

        except PaymentReferenceValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except (ValueError, InvalidOperation):
            db.session.rollback()
            flash("Verifique os campos numéricos e datas informados.", "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao atualizar RPV: {exc}", "danger")

    return render_template(
        "cadastros/editar_rpv.html",
        registro=registro,
        processo=processo,
        tipos_rpv=tipos_rpv,
        situacoes_empenho=situacoes_empenho,
        situacoes_imposto=situacoes_imposto,
        usuarios=usuarios,
        url_retorno=url_retorno,
    )
