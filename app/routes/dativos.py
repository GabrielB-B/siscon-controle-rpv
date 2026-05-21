from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    DativoCI,
    DativoLote,
    DativoItem,
    SituacaoEmpenho,
    SituacaoImposto,
    User,
)
from app.services.audit_service import registrar_evento, snapshot_entidade
from app.services.irrf_calculator import calcular_irrf_operacional
from app.services.dativos_service import DativosService
from app.services.dativos_import_service import DativosImportService
from app.services.payment_reference_service import (
    PaymentReferenceValidationError,
    validar_referencias_pagamento_principal,
)
from app.services.processo_crosscheck_service import ProcessoCrosscheckService
from app.utils.navigation import current_internal_url, sanitize_internal_return_url
from app.utils.normalizers import normalizar_documento, normalizar_numero_processo
from app.utils.payment_rules import (
    competencia_pagamento_automatica,
    data_pagamento_manual_exige_confirmacao,
    resolver_data_pagamento_por_status,
    situacao_id_eh_cancelado,
    situacao_id_quita_pagamento_principal,
)
from app.utils.workbench import (
    build_page_window,
    collect_hidden_queue_status_ids,
    merge_query_params,
    paginate_items,
    parse_page,
    parse_page_size,
    resolve_next_sort_direction,
    sanitize_sort_direction,
    should_include_closed_in_queue,
)

dativos_bp = Blueprint("dativos", __name__, url_prefix="/dativos")


def _url_retorno_interna(padrao: str) -> str:
    return sanitize_internal_return_url(request.values.get("retorno"), padrao)


def parse_date(valor: str):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _match_busca_processual(texto_busca: str, processo_busca: str, *valores) -> bool:
    if not texto_busca and not processo_busca:
        return True

    for valor in valores:
        texto = str(valor or "").lower()
        if texto_busca and texto_busca in texto:
            return True
        if processo_busca and processo_busca in normalizar_numero_processo(str(valor or "")):
            return True

    return False


def _ci_sem_movimentacao(dativo_ci: DativoCI) -> bool:
    lotes_ativos = [
        lote for lote in getattr(dativo_ci, "lotes", [])
        if getattr(lote, "ativo", True)
    ]
    itens_ativos = [
        item for item in getattr(dativo_ci, "itens", [])
        if getattr(item, "ativo", True)
    ]
    return not lotes_ativos and not itens_ativos


def _flash_erro_duplicidade_item(mensagem_padrao: str, exc):
    if DativosService.eh_erro_duplicidade_item(exc):
        flash(DativosService.mensagem_duplicidade_item(), "danger")
        return

    flash(mensagem_padrao.format(erro=exc), "danger")


def _tipo_historico_entidade(entidade) -> str:
    if isinstance(entidade, DativoCI):
        return "dativo_ci"
    if isinstance(entidade, DativoLote):
        return "dativo_lote"
    if isinstance(entidade, DativoItem):
        return "dativo_item"
    raise ValueError("Entidade sem suporte de histórico.")


def _registrar_historico_entidade(
    entidade,
    *,
    usuario_id: int,
    acao: str,
    antes: dict | None = None,
    resumo: str | None = None,
    forcar_registro: bool = False,
):
    entidade_tipo = _tipo_historico_entidade(entidade)
    registrar_evento(
        entidade_tipo=entidade_tipo,
        entidade_id=entidade.id,
        usuario_id=usuario_id,
        acao=acao,
        antes=antes,
        depois=snapshot_entidade(entidade_tipo, entidade),
        resumo=resumo,
        forcar_registro=forcar_registro,
    )


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


@dativos_bp.route("/calcular-irrf", methods=["POST"])
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


def carregar_situacoes():
    situacoes_rpv = (
        SituacaoEmpenho.query.filter_by(ativo=True)
        .order_by(SituacaoEmpenho.ordem_fluxo.asc())
        .all()
    )
    situacoes_imposto = (
        SituacaoImposto.query.filter_by(ativo=True)
        .order_by(SituacaoImposto.ordem_fluxo.asc())
        .all()
    )
    return situacoes_rpv, situacoes_imposto


def _carregar_usuarios_ativos() -> list[User]:
    return User.query.filter_by(ativo=True).order_by(User.nome.asc()).all()


def _obter_usuario_responsavel(usuario_id_raw: str | int | None) -> User:
    valor = str(usuario_id_raw or "").strip()
    if not valor.isdigit():
        raise ValueError("Responsável inválido.")

    usuario = db.session.get(User, int(valor))
    if not usuario or not usuario.ativo:
        raise ValueError("Responsável inválido.")

    return usuario


def _contexto_detalhe_ci(
    dativo_ci: DativoCI,
    *,
    ocorrencias_processo: list[dict] | None = None,
    form_data: dict | None = None,
    formulario_origem: str = "",
    importacao_unica_preview: dict | None = None,
) -> dict:
    lote_sem_irrf = DativoLote.query.filter_by(
        dativo_ci_id=dativo_ci.id,
        tipo_lote="sem_irrf",
    ).first()

    itens_sem_irrf = (
        DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="sem_irrf")
        .order_by(DativoItem.nome_beneficiario.asc())
        .all()
    )

    itens_com_irrf = (
        DativoItem.query.filter_by(dativo_ci_id=dativo_ci.id, grupo="com_irrf")
        .order_by(DativoItem.nome_beneficiario.asc())
        .all()
    )

    return {
        "dativo_ci": dativo_ci,
        "lote_sem_irrf": lote_sem_irrf,
        "itens_sem_irrf": itens_sem_irrf,
        "itens_com_irrf": itens_com_irrf,
        "usuarios": _carregar_usuarios_ativos(),
        "ocorrencias_processo": ocorrencias_processo or [],
        "form_data": form_data or {},
        "formulario_origem": formulario_origem,
        "importacao_unica_preview": importacao_unica_preview,
        "url_retorno": _url_retorno_interna(url_for("dativos.lista_cis")),
    }


def _render_detalhe_ci(
    dativo_ci: DativoCI,
    *,
    ocorrencias_processo: list[dict] | None = None,
    form_data: dict | None = None,
    formulario_origem: str = "",
    importacao_unica_preview: dict | None = None,
):
    return render_template(
        "dativos/detalhe_ci.html",
        **_contexto_detalhe_ci(
            dativo_ci,
            ocorrencias_processo=ocorrencias_processo,
            form_data=form_data,
            formulario_origem=formulario_origem,
            importacao_unica_preview=importacao_unica_preview,
        ),
    )


def _render_novo_item_lote(
    lote: DativoLote,
    *,
    ocorrencias_processo: list[dict] | None = None,
    form_data: dict | None = None,
):
    url_retorno = _url_retorno_interna(
        url_for("dativos.detalhe_lote_sem_irrf", lote_id=lote.id)
    )
    return render_template(
        "dativos/novo_item_lote.html",
        lote=lote,
        ocorrencias_processo=ocorrencias_processo or [],
        form_data=form_data or {},
        url_retorno=url_retorno,
    )


def _resumo_confirmacao_processo(ocorrencias_processo: list[dict]) -> str:
    quantidade = len(ocorrencias_processo)
    exemplos = " | ".join(
        f"{ocorrencia['origem']}: {ocorrencia['resumo_operacional']}"
        for ocorrencia in ocorrencias_processo[:2]
    )

    resumo = f"Repetição confirmada pelo operador ({quantidade} ocorrência(s) prévias)."
    if exemplos:
        resumo = f"{resumo} Exemplos: {exemplos}"
    return resumo


def _metadados_preview_importacao(modo: str) -> dict:
    configuracoes = {
        "unico": {
            "modo_importacao": "unico",
            "titulo_preview": "Previa da planilha unica",
            "descricao_preview": (
                "Revise a classificacao automatica e confirme apenas o que deve entrar agora."
            ),
            "acao_historico": "Importacao unica assistida",
            "mensagem_conclusao": "Importacao automatica concluida.",
        },
        "sem_irrf": {
            "modo_importacao": "sem_irrf",
            "titulo_preview": "Previa da importacao sem IRRF",
            "descricao_preview": (
                "A planilha encontrou repeticoes ou ocorrencias de processo que exigem confirmacao explicita antes da gravacao."
            ),
            "acao_historico": "Importacao sem IRRF assistida",
            "mensagem_conclusao": "Importacao sem IRRF concluida.",
        },
        "com_irrf": {
            "modo_importacao": "com_irrf",
            "titulo_preview": "Previa da importacao com IRRF",
            "descricao_preview": (
                "A planilha encontrou repeticoes ou ocorrencias de processo que exigem confirmacao explicita antes da gravacao."
            ),
            "acao_historico": "Importacao com IRRF assistida",
            "mensagem_conclusao": "Importacao com IRRF concluida.",
        },
    }
    return dict(configuracoes.get(modo, configuracoes["unico"]))


def _preparar_preview_importacao(preview: dict, *, modo: str) -> dict:
    payload = dict(preview)
    payload.update(_metadados_preview_importacao(modo))
    return payload


@dativos_bp.route("/")
@login_required
def index():
    return redirect(url_for("dativos.lista_cis"))


@dativos_bp.route("/cis")
@login_required
def lista_cis():
    q = request.args.get("q", "").strip()
    ne = request.args.get("ne", "").strip()
    exercicio = request.args.get("exercicio", "").strip()
    ci = request.args.get("ci", "").strip()
    responsavel = request.args.get("responsavel", "meus").strip() or "meus"
    grupo = request.args.get("grupo", "todos").strip()
    situacao_rpv_id = request.args.get("situacao_rpv_id", "").strip()
    situacao_imposto_id = request.args.get("situacao_imposto_id", "").strip()
    mostrar_encerrados = should_include_closed_in_queue(
        request.args.get("mostrar_encerrados"),
        situacao_rpv_id,
    )
    ordenar = request.args.get("ordenar", "exercicio").strip()
    direcao = sanitize_sort_direction(request.args.get("direcao"), padrao="desc")
    pagina = parse_page(request.args.get("pagina"), padrao=1)
    por_pagina = parse_page_size(request.args.get("por_pagina"), padrao=20)

    situacoes_rpv, situacoes_imposto = carregar_situacoes()
    situacoes_rpv_ocultas = collect_hidden_queue_status_ids(situacoes_rpv)
    usuarios = _carregar_usuarios_ativos()

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
        query = query.filter(DativoCI.responsavel_id == current_user.id)
    elif responsavel and responsavel != "todos":
        try:
            query = query.filter(DativoCI.responsavel_id == int(responsavel))
        except ValueError:
            responsavel = "meus"
            query = query.filter(DativoCI.responsavel_id == current_user.id)

    cis = query.order_by(DativoCI.data_ci.desc(), DativoCI.criado_em.desc()).all()

    url_retorno_atual = current_internal_url(url_for("dativos.lista_cis"))
    linhas = []
    cis_incompletas = []
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

    def match_busca(*valores):
        return _match_busca_processual(q_lower, q_processo, *valores)

    def match_documento(documento_valor: str | None) -> bool:
        if not q_doc:
            return True
        return q_doc in normalizar_documento(documento_valor or "")

    for dativo_ci in cis:
        if _ci_sem_movimentacao(dativo_ci):
            if permitir_cis_incompletas and (
                not q
                or match_busca(
                    dativo_ci.processo_edoc,
                    dativo_ci.descricao,
                    getattr(getattr(dativo_ci, "responsavel", None), "nome", None),
                )
            ):
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
                and lote_sem_irrf.situacao_rpv_id in situacoes_rpv_ocultas
            ):
                lote_match = False

            if situacao_imposto_id and str(lote_sem_irrf.situacao_imposto_id) != situacao_imposto_id:
                lote_match = False

            busca_ok = match_busca(
                dativo_ci.processo_edoc,
                lote_sem_irrf.resumo_operacional,
                lote_sem_irrf.nota_empenho,
                lote_sem_irrf.numero_se,
                lote_sem_irrf.ordem_bancaria,
                *(item.numero_processo for item in itens_lote_sem_irrf),
                *(item.nome_beneficiario for item in itens_lote_sem_irrf),
            )
            documento_ok = bool(q_doc) and any(
                match_documento(item.cpf_original) for item in itens_lote_sem_irrf
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
                        retorno=url_retorno_atual,
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
            elif not mostrar_encerrados and item.situacao_rpv_id in situacoes_rpv_ocultas:
                item_match = False

            if situacao_imposto_id and str(item.situacao_imposto_id) != situacao_imposto_id:
                item_match = False

            busca_ok = match_busca(
                dativo_ci.processo_edoc,
                item.numero_processo,
                item.nome_beneficiario,
                item.resumo_operacional_atual,
                item.nota_empenho,
                item.numero_se,
                item.ordem_bancaria,
                item.ob_imposto,
            )

            documento_ok = bool(q_doc) and match_documento(item.cpf_original)

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
                        retorno=url_retorno_atual,
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

    total_ci = len(cis)
    ordenacao_mapa = {
        "exercicio": lambda linha: (
            str(linha.get("exercicio_valor") or ""),
            linha.get("data_referencia") or datetime.min.date(),
        ),
        "grupo": lambda linha: (linha.get("grupo_ordem") or 0, str(linha.get("grupo_label") or "")),
        "resumo": lambda linha: str(linha.get("resumo_operacional") or "").casefold(),
        "valor": lambda linha: Decimal(linha.get("valor") or 0),
        "imposto": lambda linha: Decimal(linha.get("imposto") or 0),
    }
    chave_ordenacao = ordenacao_mapa.get(ordenar, ordenacao_mapa["exercicio"])
    linhas.sort(key=chave_ordenacao, reverse=(direcao == "desc"))
    linhas_paginadas, paginacao = paginate_items(linhas, pagina, por_pagina)

    filtros_dict = request.args.to_dict()
    filtros_ocultos = merge_query_params(
        filtros_dict,
        pagina=None,
        por_pagina=None,
    )
    sort_urls = {
        chave: url_for(
            "dativos.lista_cis",
            **merge_query_params(
                filtros_dict,
                ordenar=chave,
                direcao=resolve_next_sort_direction(ordenar, direcao, chave),
                pagina=1,
            ),
        )
        for chave in ordenacao_mapa
    }
    paginas_visiveis = build_page_window(
        paginacao["total_paginas"],
        paginacao["pagina"],
    )
    pagina_urls = {
        numero: url_for(
            "dativos.lista_cis",
            **merge_query_params(filtros_dict, pagina=numero),
        )
        for numero in paginas_visiveis
    }
    pagina_anterior_url = (
        url_for(
            "dativos.lista_cis",
            **merge_query_params(filtros_dict, pagina=paginacao["pagina_anterior"]),
        )
        if paginacao["tem_anterior"]
        else None
    )
    proxima_pagina_url = (
        url_for(
            "dativos.lista_cis",
            **merge_query_params(filtros_dict, pagina=paginacao["proxima_pagina"]),
        )
        if paginacao["tem_proxima"]
        else None
    )
    busca_processo_contexto = ProcessoCrosscheckService.buscar_contexto_pesquisa(
        q,
        retorno_url=url_retorno_atual,
    )
    return render_template(
        "dativos/lista_cis.html",
        linhas=linhas_paginadas,
        filtros=request.args,
        filtros_ocultos=filtros_ocultos,
        usuarios=usuarios,
        filtro_responsavel=responsavel,
        total_ci=total_ci,
        total_ci_incompletas=len(cis_incompletas),
        total_lotes=total_lotes,
        total_itens_com_irrf=total_itens_com_irrf,
        cis_incompletas=cis_incompletas,
        situacoes_rpv=situacoes_rpv,
        situacoes_imposto=situacoes_imposto,
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
        url_retorno_atual=url_retorno_atual,
    )


@dativos_bp.route("/novo-ci", methods=["GET", "POST"])
@login_required
def novo_ci():
    usuarios = _carregar_usuarios_ativos()
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    if request.method == "POST":
        try:
            exercicio = request.form.get("exercicio", "").strip()
            processo_edoc = request.form.get("processo_edoc", "").strip()
            data_ci = parse_date(request.form.get("data_ci", "").strip())
            responsavel = _obter_usuario_responsavel(
                request.form.get("responsavel_id", str(current_user.id))
            )

            dativo_ci = DativosService.criar_ci_dativo(
                exercicio=exercicio,
                processo_edoc=processo_edoc,
                data_ci=data_ci,
                usuario_id=current_user.id,
                responsavel_id=responsavel.id,
            )

            _registrar_historico_entidade(
                dativo_ci,
                usuario_id=current_user.id,
                acao="Cadastro",
                resumo="C.I. criada",
                forcar_registro=True,
            )
            db.session.commit()
            flash("C.I. de dativo criada com sucesso.", "success")
            return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))

        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao criar C.I. de dativo: {exc}", "danger")

    return render_template("dativos/novo_ci.html", usuarios=usuarios, form_data=form_data)


@dativos_bp.route("/ci/<int:ci_id>/transferir-responsavel", methods=["POST"])
@login_required
def transferir_responsavel_ci(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    url_retorno = _url_retorno_interna(url_for("dativos.lista_cis"))

    try:
        responsavel = _obter_usuario_responsavel(request.form.get("responsavel_id"))

        if dativo_ci.responsavel_id == responsavel.id:
            flash("Essa C.I. já está com o responsável selecionado.", "info")
            return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id, retorno=url_retorno))

        antes = snapshot_entidade("dativo_ci", dativo_ci)
        dativo_ci.responsavel_id = responsavel.id
        dativo_ci.atualizado_por_id = current_user.id
        _registrar_historico_entidade(
            dativo_ci,
            usuario_id=current_user.id,
            acao="Transferência de responsabilidade",
            antes=antes,
            resumo=(
                f"Responsabilidade operacional da C.I. transferida para {responsavel.nome}. "
                "Lotes e itens passam a seguir esse responsável."
            ),
        )
        db.session.commit()
        flash("Responsável da C.I. atualizado com sucesso.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao transferir responsabilidade da C.I.: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id, retorno=url_retorno))


@dativos_bp.route("/atualizacao-lote", methods=["POST"])
@login_required
def atualizacao_lote_cis():
    selecionados = request.form.getlist("selecionados")
    situacao_rpv_id = request.form.get("situacao_rpv_id_lote", "").strip()
    situacao_imposto_id = request.form.get("situacao_imposto_id_lote", "").strip()

    if not selecionados:
        flash("Selecione pelo menos um registro de dativo para atualizar em lote.", "warning")
        return redirect(url_for("dativos.lista_cis", **request.args.to_dict()))

    if not situacao_rpv_id and not situacao_imposto_id:
        flash("Escolha ao menos uma situação para aplicar no lote.", "warning")
        return redirect(url_for("dativos.lista_cis", **request.args.to_dict()))

    try:
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_rpv_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_rpv_id)
        situacao_imposto_sem_irrf_id = DativosService.obter_situacao_imposto_sem_irrf().id
        total_atualizados = 0

        for selecionado in selecionados:
            tipo, _, valor_id = str(selecionado).partition(":")
            if not valor_id.isdigit():
                continue

            if tipo == "lote":
                registro = db.session.get(DativoLote, int(valor_id))
            elif tipo == "item":
                registro = db.session.get(DativoItem, int(valor_id))
            else:
                continue

            if not registro:
                continue

            entidade_tipo = _tipo_historico_entidade(registro)
            antes = snapshot_entidade(entidade_tipo, registro)

            if situacao_rpv_id:
                if status_quita_pagamento and not status_cancelado:
                    validar_referencias_pagamento_principal(
                        registro,
                        nota_empenho=getattr(registro, "nota_empenho", None),
                        ordem_bancaria=getattr(registro, "ordem_bancaria", None),
                        exigir_preenchimento=True,
                    )
                registro.situacao_rpv_id = int(situacao_rpv_id)
                registro.data_pagamento = resolver_data_pagamento_por_status(
                    data_atual=getattr(registro, "data_pagamento", None),
                    status_pago=status_quita_pagamento,
                    status_cancelado=status_cancelado,
                    competencia=competencia_pagamento_automatica(),
                )
                if status_cancelado and isinstance(registro, DativoItem):
                    registro.reinf_status = None
                if isinstance(registro, DativoLote):
                    DativosService.sincronizar_lote_sem_irrf_com_itens(
                        registro,
                        usuario_id=current_user.id,
                    )

            if situacao_imposto_id:
                if tipo == "item":
                    registro.situacao_imposto_id = int(situacao_imposto_id)
                else:
                    registro.situacao_imposto_id = situacao_imposto_sem_irrf_id
            elif tipo == "lote":
                registro.situacao_imposto_id = situacao_imposto_sem_irrf_id
                DativosService.sincronizar_lote_sem_irrf_com_itens(
                    registro,
                    usuario_id=current_user.id,
                )

            registro.atualizado_por_id = current_user.id
            registrar_evento(
                entidade_tipo=entidade_tipo,
                entidade_id=registro.id,
                usuario_id=current_user.id,
                acao="Atualização em lote",
                antes=antes,
                depois=snapshot_entidade(entidade_tipo, registro),
                resumo="Ação aplicada em lote",
            )
            total_atualizados += 1

        db.session.commit()
        flash(
            f"Lote de dativos atualizado com sucesso ({total_atualizados} registro(s)).",
            "success",
        )
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro na atualização em lote dos dativos: {exc}", "danger")

    return redirect(url_for("dativos.lista_cis", **request.args.to_dict()))


@dativos_bp.route("/ci/<int:ci_id>", methods=["GET"])
@login_required
def detalhe_ci(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    preview_token = request.args.get("preview_token", "").strip()
    importacao_unica_preview = None

    if preview_token:
        importacao_unica_preview = DativosImportService.carregar_previa_importacao_unica(
            preview_token,
            dativo_ci_id=dativo_ci.id,
            usuario_id=current_user.id,
        )
        if importacao_unica_preview is None:
            flash("A prévia da planilha única não foi encontrada ou expirou.", "warning")

    if importacao_unica_preview is not None:
        importacao_unica_preview = _preparar_preview_importacao(
            importacao_unica_preview,
            modo=str(importacao_unica_preview.get("modo_importacao") or "unico"),
        )

    return _render_detalhe_ci(
        dativo_ci,
        importacao_unica_preview=importacao_unica_preview,
    )


@dativos_bp.route("/ci/<int:ci_id>/importar-sem-irrf", methods=["POST"])
@login_required
def importar_sem_irrf(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    antes_ci = snapshot_entidade("dativo_ci", dativo_ci)

    try:
        arquivo = request.files.get("arquivo_sem_irrf")

        if not arquivo or not arquivo.filename:
            raise ValueError("Selecione um arquivo ODS para importar.")

        if not arquivo.filename.lower().endswith(".ods"):
            raise ValueError("O arquivo deve estar no formato .ods")

        preview = DativosImportService.analisar_ods_grupo_fixo(
            arquivo=arquivo,
            dativo_ci=dativo_ci,
            grupo="sem_irrf",
        )
        preview = _preparar_preview_importacao(preview, modo="sem_irrf")

        if preview["resumo"]["total_pendencias"]:
            preview_token = DativosImportService.salvar_previa_importacao_unica(
                preview=preview,
                dativo_ci_id=dativo_ci.id,
                usuario_id=current_user.id,
                nome_arquivo=arquivo.filename,
            )
            flash(
                (
                    "A importacao sem IRRF encontrou casos com possibilidade de repeticao. "
                    "Revise a previa e confirme explicitamente apenas o que deve entrar."
                ),
                "warning",
            )
            if preview["erros"]:
                flash(
                    "Algumas linhas tiveram erro: " + " | ".join(preview["erros"][:5]),
                    "info",
                )
            return redirect(
                url_for(
                    "dativos.detalhe_ci",
                    ci_id=dativo_ci.id,
                    preview_token=preview_token,
                )
            )

        resultado = DativosImportService.aplicar_previa_importacao_unica(
            dativo_ci=dativo_ci,
            preview=preview,
            usuario_id=current_user.id,
        )
        resultado = {
            "importados": resultado["importados_total"],
            "ignorados": resultado["pendencias_descartadas"],
            "rodapes_ignorados": preview["resumo"]["rodapes_ignorados"],
            "alertas_processo_existente": [],
            "erros": preview["erros"],
        }

        registrar_evento(
            entidade_tipo="dativo_ci",
            entidade_id=dativo_ci.id,
            usuario_id=current_user.id,
            acao="Importação sem IRRF",
            antes=antes_ci,
            depois=snapshot_entidade("dativo_ci", dativo_ci),
            resumo=(
                f"Importados {resultado['importados']} | "
                f"Ignorados {resultado['ignorados']} | "
                f"Rodapés {resultado['rodapes_ignorados']}"
            ),
            forcar_registro=True,
        )
        db.session.commit()

        flash(
            f"Importação sem IRRF concluída. Importados: {resultado['importados']} | "
            f"Duplicados ignorados: {resultado['ignorados']} | "
            f"Rodapés ignorados: {resultado['rodapes_ignorados']}",
            "success",
        )

        if resultado.get("alertas_processo_existente"):
            exemplos = " | ".join(resultado["alertas_processo_existente"][:5])
            flash(
                f"Atenção: alguns processos importados já existem no sistema. Exemplos: {exemplos}",
                "info",
            )

        if resultado["erros"]:
            flash(
                "Algumas linhas tiveram erro: " + " | ".join(resultado["erros"][:5]),
                "info",
            )

    except IntegrityError as exc:
        db.session.rollback()
        _flash_erro_duplicidade_item("Erro na importacao sem IRRF: {erro}", exc)
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro na importação sem IRRF: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))


@dativos_bp.route("/ci/<int:ci_id>/importar-com-irrf", methods=["POST"])
@login_required
def importar_com_irrf(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    antes_ci = snapshot_entidade("dativo_ci", dativo_ci)

    try:
        arquivo = request.files.get("arquivo_com_irrf")

        if not arquivo or not arquivo.filename:
            raise ValueError("Selecione um arquivo ODS para importar.")

        if not arquivo.filename.lower().endswith(".ods"):
            raise ValueError("O arquivo deve estar no formato .ods")

        preview = DativosImportService.analisar_ods_grupo_fixo(
            arquivo=arquivo,
            dativo_ci=dativo_ci,
            grupo="com_irrf",
        )
        preview = _preparar_preview_importacao(preview, modo="com_irrf")

        if preview["resumo"]["total_pendencias"]:
            preview_token = DativosImportService.salvar_previa_importacao_unica(
                preview=preview,
                dativo_ci_id=dativo_ci.id,
                usuario_id=current_user.id,
                nome_arquivo=arquivo.filename,
            )
            flash(
                (
                    "A importacao com IRRF encontrou casos com possibilidade de repeticao. "
                    "Revise a previa e confirme explicitamente apenas o que deve entrar."
                ),
                "warning",
            )
            if preview["erros"]:
                flash(
                    "Algumas linhas tiveram erro: " + " | ".join(preview["erros"][:5]),
                    "info",
                )
            return redirect(
                url_for(
                    "dativos.detalhe_ci",
                    ci_id=dativo_ci.id,
                    preview_token=preview_token,
                )
            )

        resultado = DativosImportService.aplicar_previa_importacao_unica(
            dativo_ci=dativo_ci,
            preview=preview,
            usuario_id=current_user.id,
        )
        resultado = {
            "importados": resultado["importados_total"],
            "ignorados": resultado["pendencias_descartadas"],
            "rodapes_ignorados": preview["resumo"]["rodapes_ignorados"],
            "alertas_processo_existente": [],
            "erros": preview["erros"],
        }

        registrar_evento(
            entidade_tipo="dativo_ci",
            entidade_id=dativo_ci.id,
            usuario_id=current_user.id,
            acao="Importação com IRRF",
            antes=antes_ci,
            depois=snapshot_entidade("dativo_ci", dativo_ci),
            resumo=(
                f"Importados {resultado['importados']} | "
                f"Ignorados {resultado['ignorados']} | "
                f"Rodapés {resultado['rodapes_ignorados']}"
            ),
            forcar_registro=True,
        )
        db.session.commit()

        flash(
            f"Importação com IRRF concluída. Importados: {resultado['importados']} | "
            f"Duplicados ignorados: {resultado['ignorados']} | "
            f"Rodapés ignorados: {resultado['rodapes_ignorados']}",
            "success",
        )

        if resultado.get("alertas_processo_existente"):
            exemplos = " | ".join(resultado["alertas_processo_existente"][:5])
            flash(
                f"Atenção: alguns processos importados já existem no sistema. Exemplos: {exemplos}",
                "info",
            )

        if resultado["erros"]:
            flash(
                "Algumas linhas tiveram erro: " + " | ".join(resultado["erros"][:5]),
                "info",
            )

    except IntegrityError as exc:
        db.session.rollback()
        _flash_erro_duplicidade_item("Erro na importacao com IRRF: {erro}", exc)
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro na importação com IRRF: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))


@dativos_bp.route("/ci/<int:ci_id>/importar-unico/analisar", methods=["POST"])
@login_required
def analisar_importacao_unica(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)

    try:
        arquivo = request.files.get("arquivo_unico")

        if not arquivo or not arquivo.filename:
            raise ValueError("Selecione um arquivo ODS para analisar.")

        if not arquivo.filename.lower().endswith(".ods"):
            raise ValueError("O arquivo deve estar no formato .ods")

        preview = DativosImportService.analisar_ods_unico(
            arquivo=arquivo,
            dativo_ci=dativo_ci,
        )
        preview = _preparar_preview_importacao(preview, modo="unico")
        preview_token = DativosImportService.salvar_previa_importacao_unica(
            preview=preview,
            dativo_ci_id=dativo_ci.id,
            usuario_id=current_user.id,
            nome_arquivo=arquivo.filename,
        )
        resumo = preview["resumo"]

        flash(
            (
                "Analise da planilha unica concluida. "
                f"Prontas sem IRRF: {resumo['total_prontas_sem_irrf']} | "
                f"Prontas com IRRF: {resumo['total_prontas_com_irrf']} | "
                f"Pendencias: {resumo['total_pendencias']} | "
                f"Erros: {resumo['total_erros']}"
            ),
            "success",
        )
        if resumo["cnpjs_mantidos_sem_irrf"]:
            flash(
                (
                    f"{resumo['cnpjs_mantidos_sem_irrf']} linha(s) com CNPJ "
                    "foram mantidas em sem IRRF mesmo acima do corte."
                ),
                "info",
            )

        return redirect(
            url_for(
                "dativos.detalhe_ci",
                ci_id=dativo_ci.id,
                preview_token=preview_token,
            )
        )

    except Exception as exc:
        flash(f"Erro na analise da planilha unica: {exc}", "danger")
        return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))


@dativos_bp.route("/ci/<int:ci_id>/importar-unico/confirmar", methods=["POST"])
@login_required
def confirmar_importacao_unica(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    antes_ci = snapshot_entidade("dativo_ci", dativo_ci)
    preview_token = request.form.get("preview_token", "").strip()

    try:
        preview = DativosImportService.carregar_previa_importacao_unica(
            preview_token,
            dativo_ci_id=dativo_ci.id,
            usuario_id=current_user.id,
        )
        if preview is None:
            raise ValueError("A previa dessa importacao nao esta mais disponivel.")
        preview = _preparar_preview_importacao(
            preview,
            modo=str(preview.get("modo_importacao") or "unico"),
        )

        pendencias_confirmadas = set(request.form.getlist("pendencias_confirmadas"))
        resultado = DativosImportService.aplicar_previa_importacao_unica(
            dativo_ci=dativo_ci,
            preview=preview,
            usuario_id=current_user.id,
            pendencias_confirmadas=pendencias_confirmadas,
        )

        registrar_evento(
            entidade_tipo="dativo_ci",
            entidade_id=dativo_ci.id,
            usuario_id=current_user.id,
            acao=preview["acao_historico"],
            antes=antes_ci,
            depois=snapshot_entidade("dativo_ci", dativo_ci),
            resumo=(
                f"Importados {resultado['importados_total']} | "
                f"Sem IRRF {resultado['importados_sem_irrf']} | "
                f"Com IRRF {resultado['importados_com_irrf']} | "
                f"Pendencias confirmadas {resultado['pendencias_confirmadas']} | "
                f"Pendencias ignoradas {resultado['pendencias_descartadas']}"
            ),
            forcar_registro=True,
        )
        db.session.commit()
        DativosImportService.descartar_previa_importacao_unica(preview_token)

        flash(
            (
                f"{preview['mensagem_conclusao']} "
                f"Sem IRRF: {resultado['importados_sem_irrf']} | "
                f"Com IRRF: {resultado['importados_com_irrf']} | "
                f"Pendencias confirmadas: {resultado['pendencias_confirmadas']}"
            ),
            "success",
        )
        if resultado["pendencias_descartadas"]:
            flash(
                f"{resultado['pendencias_descartadas']} pendencia(s) ficaram de fora nesta rodada.",
                "info",
            )

        return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))

    except IntegrityError as exc:
        db.session.rollback()
        _flash_erro_duplicidade_item("Erro na importacao automatica: {erro}", exc)
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro na confirmacao da importacao automatica: {exc}", "danger")

    return redirect(
        url_for(
            "dativos.detalhe_ci",
            ci_id=dativo_ci.id,
            preview_token=preview_token,
        )
    )


@dativos_bp.route("/ci/<int:ci_id>/adicionar-sem-irrf", methods=["POST"])
@login_required
def adicionar_item_sem_irrf(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    form_data = request.form.to_dict(flat=True)

    try:
        nome_beneficiario = request.form.get("nome_beneficiario", "").strip()
        cpf_original = request.form.get("cpf_original", "").strip()
        numero_processo = request.form.get("numero_processo", "").strip()
        observacoes = request.form.get("observacoes", "").strip() or None
        ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(numero_processo)
        confirmar_processo_existente = (
            request.form.get("confirmar_processo_existente") == "1"
        )
        duplicidade_existente = DativosService.buscar_duplicidade_item(
            dativo_ci_id=dativo_ci.id,
            grupo="sem_irrf",
            documento=cpf_original,
            numero_processo=numero_processo,
        )
        if (ocorrencias_processo or duplicidade_existente) and not confirmar_processo_existente:
            flash(
                "Este processo já aparece no sistema. Confira o contexto abaixo antes de continuar.",
                "danger",
            )
            return _render_detalhe_ci(
                dativo_ci,
                ocorrencias_processo=ocorrencias_processo,
                form_data=form_data,
                formulario_origem="sem_irrf",
            )

        valor_bruto = parse_decimal(request.form.get("valor_bruto", "").strip())
        lote_existente = DativoLote.query.filter_by(
            dativo_ci_id=dativo_ci.id,
            tipo_lote="sem_irrf",
        ).first()
        antes_lote = snapshot_entidade("dativo_lote", lote_existente) if lote_existente else None

        item = DativosService.adicionar_item_sem_irrf(
            dativo_ci=dativo_ci,
            nome_beneficiario=nome_beneficiario,
            cpf_original=cpf_original,
            numero_processo=numero_processo,
            valor_bruto=valor_bruto,
            usuario_id=current_user.id,
            observacoes=observacoes,
            permitir_duplicidade_confirmada=confirmar_processo_existente,
        )

        _registrar_historico_entidade(
            item,
            usuario_id=current_user.id,
            acao="Cadastro manual",
            resumo="Beneficiário incluído na C.I.",
            forcar_registro=True,
        )
        if item.dativo_lote:
            _registrar_historico_entidade(
                item.dativo_lote,
                usuario_id=current_user.id,
                acao="Totais recalculados",
                antes=antes_lote,
                resumo="Inclusão manual de beneficiário",
                forcar_registro=antes_lote is None,
            )
        if confirmar_processo_existente and ocorrencias_processo:
            _registrar_historico_entidade(
                item,
                usuario_id=current_user.id,
                acao="Confirmação de repetição de processo",
                resumo=_resumo_confirmacao_processo(ocorrencias_processo),
            )
        db.session.commit()
        flash("Item sem IRRF adicionado com sucesso.", "success")

    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError as exc:
        db.session.rollback()
        _flash_erro_duplicidade_item("Erro ao adicionar item sem IRRF: {erro}", exc)
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao adicionar item sem IRRF: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))


@dativos_bp.route("/ci/<int:ci_id>/adicionar-com-irrf", methods=["POST"])
@login_required
def adicionar_item_com_irrf(ci_id):
    dativo_ci = DativoCI.query.get_or_404(ci_id)
    form_data = request.form.to_dict(flat=True)

    try:
        nome_beneficiario = request.form.get("nome_beneficiario", "").strip()
        cpf_original = request.form.get("cpf_original", "").strip()
        numero_processo = request.form.get("numero_processo", "").strip()
        observacoes = request.form.get("observacoes", "").strip() or None
        ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(numero_processo)
        confirmar_processo_existente = (
            request.form.get("confirmar_processo_existente") == "1"
        )
        duplicidade_existente = DativosService.buscar_duplicidade_item(
            dativo_ci_id=dativo_ci.id,
            grupo="com_irrf",
            documento=cpf_original,
            numero_processo=numero_processo,
        )
        if (ocorrencias_processo or duplicidade_existente) and not confirmar_processo_existente:
            flash(
                "Este processo já aparece no sistema. Confira o contexto abaixo antes de continuar.",
                "danger",
            )
            return _render_detalhe_ci(
                dativo_ci,
                ocorrencias_processo=ocorrencias_processo,
                form_data=form_data,
                formulario_origem="com_irrf",
            )

        valor_bruto = parse_decimal(request.form.get("valor_bruto", "").strip())

        valor_irrf_raw = request.form.get("valor_irrf", "").strip()
        valor_irrf = parse_decimal(valor_irrf_raw) if valor_irrf_raw else None

        item = DativosService.adicionar_item_com_irrf(
            dativo_ci=dativo_ci,
            nome_beneficiario=nome_beneficiario,
            cpf_original=cpf_original,
            numero_processo=numero_processo,
            valor_bruto=valor_bruto,
            valor_irrf=valor_irrf,
            usuario_id=current_user.id,
            observacoes=observacoes,
            permitir_duplicidade_confirmada=confirmar_processo_existente,
        )

        _registrar_historico_entidade(
            item,
            usuario_id=current_user.id,
            acao="Cadastro manual",
            resumo="Item com IRRF incluído na C.I.",
            forcar_registro=True,
        )
        if confirmar_processo_existente and ocorrencias_processo:
            _registrar_historico_entidade(
                item,
                usuario_id=current_user.id,
                acao="Confirmação de repetição de processo",
                resumo=_resumo_confirmacao_processo(ocorrencias_processo),
            )
        db.session.commit()
        flash("Item com IRRF adicionado com sucesso.", "success")

    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError as exc:
        db.session.rollback()
        _flash_erro_duplicidade_item("Erro ao adicionar item com IRRF: {erro}", exc)
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao adicionar item com IRRF: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_ci", ci_id=dativo_ci.id))


@dativos_bp.route("/lotes-sem-irrf")
@login_required
def lotes_sem_irrf():
    situacoes_rpv, situacoes_imposto = carregar_situacoes()
    situacoes_rpv_ocultas = collect_hidden_queue_status_ids(situacoes_rpv)
    usuarios = _carregar_usuarios_ativos()
    url_retorno_atual = current_internal_url(url_for("dativos.lista_cis"))

    q = request.args.get("q", "").strip()
    exercicio = request.args.get("exercicio", "").strip()
    ci = request.args.get("ci", "").strip()
    ne = request.args.get("ne", "").strip()
    responsavel = request.args.get("responsavel", "meus").strip() or "meus"
    situacao_rpv_id = request.args.get("situacao_rpv_id", "").strip()
    mostrar_encerrados = should_include_closed_in_queue(
        request.args.get("mostrar_encerrados"),
        situacao_rpv_id,
    )

    busca = q or ci or ne

    query = (
        DativoLote.query.options(
            selectinload(DativoLote.dativo_ci).selectinload(DativoCI.responsavel),
            selectinload(DativoLote.itens),
            selectinload(DativoLote.situacao_rpv),
        )
        .join(DativoCI)
        .filter(DativoLote.tipo_lote == "sem_irrf")
    )

    if exercicio:
        query = query.filter(DativoCI.exercicio == exercicio)

    if ne:
        query = query.filter(DativoLote.nota_empenho.ilike(f"%{ne}%"))

    if responsavel == "meus":
        query = query.filter(DativoCI.responsavel_id == current_user.id)
    elif responsavel not in ("", "todos"):
        try:
            query = query.filter(DativoCI.responsavel_id == int(responsavel))
        except ValueError:
            responsavel = "meus"
            query = query.filter(DativoCI.responsavel_id == current_user.id)

    if situacao_rpv_id:
        query = query.filter(DativoLote.situacao_rpv_id == int(situacao_rpv_id))
    elif not mostrar_encerrados and situacoes_rpv_ocultas:
        query = query.filter(~DativoLote.situacao_rpv_id.in_(situacoes_rpv_ocultas))

    lotes = query.order_by(DativoCI.data_ci.desc()).all()

    if busca:
        busca_lower = busca.lower()
        busca_processo = normalizar_numero_processo(busca)
        busca_doc = normalizar_documento(busca)

        lotes_filtrados = []
        for lote in lotes:
            busca_ok = _match_busca_processual(
                busca_lower,
                busca_processo,
                getattr(getattr(lote, "dativo_ci", None), "processo_edoc", None),
                lote.resumo_operacional,
                lote.nota_empenho,
                lote.numero_se,
                lote.ordem_bancaria,
                *(item.numero_processo for item in lote.itens),
                *(item.nome_beneficiario for item in lote.itens),
            )
            documento_ok = bool(busca_doc) and any(
                busca_doc in normalizar_documento(item.cpf_original or "")
                for item in lote.itens
            )

            if busca_ok or documento_ok:
                lotes_filtrados.append(lote)

        lotes = lotes_filtrados

    busca_processo_contexto = ProcessoCrosscheckService.buscar_contexto_pesquisa(
        busca,
        retorno_url=url_retorno_atual,
    )

    return render_template(
        "dativos/lotes.html",
        lotes=lotes,
        filtros=request.args,
        usuarios=usuarios,
        filtro_responsavel=responsavel,
        situacoes_rpv=situacoes_rpv,
        situacoes_imposto=situacoes_imposto,
        busca_processo_contexto=busca_processo_contexto,
        mostrar_encerrados=mostrar_encerrados,
        url_retorno_atual=url_retorno_atual,
    )


@dativos_bp.route("/lotes-sem-irrf/<int:lote_id>/item/novo", methods=["GET", "POST"])
@login_required
def novo_item_lote(lote_id):
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    lote = DativoLote.query.get_or_404(lote_id)
    url_retorno = _url_retorno_interna(
        url_for("dativos.detalhe_lote_sem_irrf", lote_id=lote.id)
    )

    if not lote.dativo_ci:
        flash("Lote sem vínculo de C.I. não pode receber itens manualmente.", "danger")
        return redirect(url_retorno)

    if request.method == "POST":
        try:
            antes_lote = snapshot_entidade("dativo_lote", lote)
            nome_beneficiario = request.form.get("nome_beneficiario", "").strip()
            cpf_original = request.form.get("cpf_original", "").strip()
            numero_processo = request.form.get("numero_processo", "").strip()
            valor_bruto_raw = request.form.get("valor_bruto", "").strip()
            observacoes = request.form.get("observacoes", "").strip() or None
            confirmar_processo_existente = request.form.get("confirmar_processo_existente") == "1"
            ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(numero_processo)
            duplicidade_existente = DativosService.buscar_duplicidade_item(
                dativo_ci_id=lote.dativo_ci.id,
                grupo="sem_irrf",
                documento=cpf_original,
                numero_processo=numero_processo,
            )
            if (ocorrencias_processo or duplicidade_existente) and not confirmar_processo_existente:
                flash(
                    "Este processo já aparece no sistema. Confira o contexto abaixo antes de continuar.",
                    "danger",
                )
                return _render_novo_item_lote(
                    lote,
                    ocorrencias_processo=ocorrencias_processo,
                    form_data=form_data,
                )

            if not nome_beneficiario or not cpf_original or not numero_processo:
                raise ValueError("Nome, documento e processo são obrigatórios.")

            valor_bruto = parse_decimal(valor_bruto_raw)
            if valor_bruto is None:
                raise ValueError("Valor bruto é obrigatório.")

            antes_lote = snapshot_entidade("dativo_lote", lote)
            item = DativosService.adicionar_item_sem_irrf(
                dativo_ci=lote.dativo_ci,
                nome_beneficiario=nome_beneficiario,
                cpf_original=cpf_original,
                numero_processo=numero_processo,
                valor_bruto=valor_bruto,
                usuario_id=current_user.id,
                observacoes=observacoes,
                permitir_duplicidade_confirmada=confirmar_processo_existente,
            )

            _registrar_historico_entidade(
                item,
                usuario_id=current_user.id,
                acao="Cadastro manual",
                resumo="Beneficiário incluído no lote",
                forcar_registro=True,
            )
            _registrar_historico_entidade(
                lote,
                usuario_id=current_user.id,
                acao="Totais recalculados",
                antes=antes_lote,
                resumo="Inclusão de beneficiário no lote",
            )
            if confirmar_processo_existente and ocorrencias_processo:
                _registrar_historico_entidade(
                    item,
                    usuario_id=current_user.id,
                    acao="Confirmação de repetição de processo",
                    resumo=_resumo_confirmacao_processo(ocorrencias_processo),
                )
            db.session.commit()
            flash("Beneficiário adicionado ao lote com sucesso.", "success")
            return redirect(url_retorno)

        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError as exc:
            db.session.rollback()
            _flash_erro_duplicidade_item("Erro ao adicionar beneficiario: {erro}", exc)
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao adicionar beneficiário: {exc}", "danger")

    return _render_novo_item_lote(lote, form_data=form_data)


@dativos_bp.route(
    "/lotes-sem-irrf/<int:lote_id>/item/<int:item_id>/editar",
    methods=["GET", "POST"],
)
@login_required
def editar_item_lote(lote_id, item_id):
    lote = DativoLote.query.get_or_404(lote_id)
    url_retorno = _url_retorno_interna(
        url_for("dativos.detalhe_lote_sem_irrf", lote_id=lote.id)
    )

    item = DativoItem.query.filter_by(
        id=item_id,
        dativo_lote_id=lote.id,
        grupo="sem_irrf",
    ).first_or_404()

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    if request.method == "POST":
        try:
            antes_item = snapshot_entidade("dativo_item", item)
            antes_lote = snapshot_entidade("dativo_lote", lote)
            nome_beneficiario = request.form.get("nome_beneficiario", "").strip()
            cpf_original = request.form.get("cpf_original", "").strip()
            numero_processo = request.form.get("numero_processo", "").strip()
            valor_bruto = parse_decimal(request.form.get("valor_bruto", "").strip())
            observacoes = request.form.get("observacoes", "").strip() or None
            dispensa_irrf_confirmada = request.form.get("dispensa_irrf_confirmada") == "1"
            confirmar_processo_existente = (
                request.form.get("confirmar_processo_existente") == "1"
            )
            ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(
                numero_processo,
                excluir_item_id=item.id,
            )
            duplicidade_existente = DativosService.buscar_duplicidade_item(
                dativo_ci_id=item.dativo_ci_id,
                grupo="sem_irrf",
                documento=cpf_original,
                numero_processo=numero_processo,
                item_id_excluir=item.id,
            )
            if (ocorrencias_processo or duplicidade_existente) and not confirmar_processo_existente:
                flash(
                    "Este processo já aparece no sistema. Confira o contexto abaixo antes de continuar.",
                    "danger",
                )
                return render_template(
                    "dativos/editar_item_lote.html",
                    lote=lote,
                    item=item,
                    ocorrencias_processo=ocorrencias_processo,
                    form_data=form_data,
                    url_retorno=url_retorno,
                )

            if not nome_beneficiario or not cpf_original or not numero_processo:
                raise ValueError("Nome, documento e processo são obrigatórios.")

            if valor_bruto is None:
                raise ValueError("Valor bruto é obrigatório.")

            if (
                _valor_bruto_alterado(item.valor_bruto, valor_bruto)
                and request.form.get("confirmar_edicao_valor_bruto") != "1"
            ):
                flash(
                    "Confirme a edicao do valor bruto antes de salvar. "
                    "A correcao recalcula os totais do lote e fica registrada no historico.",
                    "warning",
                )
                return redirect(
                    url_for(
                        "dativos.editar_item_lote",
                        lote_id=lote.id,
                        item_id=item.id,
                        retorno=url_retorno,
                    )
                )

            DativosService.editar_item_sem_irrf(
                item=item,
                nome_beneficiario=nome_beneficiario,
                cpf_original=cpf_original,
                numero_processo=numero_processo,
                valor_bruto=valor_bruto,
                usuario_id=current_user.id,
                data_pagamento=item.data_pagamento,
                observacoes=observacoes,
                dispensa_irrf_confirmada=dispensa_irrf_confirmada,
                permitir_duplicidade_confirmada=confirmar_processo_existente,
            )

            DativosService.atualizar_totais_lote(lote, usuario_id=current_user.id)
            _registrar_historico_entidade(
                item,
                usuario_id=current_user.id,
                acao="Alteração manual",
                antes=antes_item,
            )
            _registrar_historico_entidade(
                lote,
                usuario_id=current_user.id,
                acao="Totais recalculados",
                antes=antes_lote,
                resumo="Ajuste manual de beneficiário",
            )
            if confirmar_processo_existente and ocorrencias_processo:
                _registrar_historico_entidade(
                    item,
                    usuario_id=current_user.id,
                    acao="Confirmação de repetição de processo",
                    resumo=_resumo_confirmacao_processo(ocorrencias_processo),
                )
            db.session.commit()

            flash("Beneficiário do lote atualizado com sucesso.", "success")
            return redirect(url_retorno)

        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError as exc:
            db.session.rollback()
            _flash_erro_duplicidade_item("Erro ao atualizar beneficiario do lote: {erro}", exc)
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao atualizar beneficiário do lote: {exc}", "danger")

    return render_template(
        "dativos/editar_item_lote.html",
        lote=lote,
        item=item,
        ocorrencias_processo=[],
        form_data=form_data,
        url_retorno=url_retorno,
    )


@dativos_bp.route(
    "/lotes-sem-irrf/<int:lote_id>/item/<int:item_id>/excluir",
    methods=["POST"],
)
@login_required
def excluir_item_lote(lote_id, item_id):
    lote = DativoLote.query.get_or_404(lote_id)
    url_retorno = _url_retorno_interna(
        url_for("dativos.detalhe_lote_sem_irrf", lote_id=lote.id)
    )

    item = DativoItem.query.filter_by(
        id=item_id,
        dativo_lote_id=lote.id,
        grupo="sem_irrf",
    ).first_or_404()

    try:
        antes_lote = snapshot_entidade("dativo_lote", lote)
        db.session.delete(item)
        db.session.flush()

        db.session.expire(lote, ["itens"])
        DativosService.atualizar_totais_lote(lote, usuario_id=current_user.id)
        _registrar_historico_entidade(
            lote,
            usuario_id=current_user.id,
            acao="Totais recalculados",
            antes=antes_lote,
            resumo="Beneficiário removido do lote",
            forcar_registro=True,
        )

        db.session.commit()
        flash("Beneficiário removido do lote com sucesso.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao excluir beneficiário: {exc}", "danger")

    return redirect(url_retorno)

@dativos_bp.route("/lotes-sem-irrf/<int:lote_id>/atualizacao-rapida", methods=["POST"])
@login_required
def atualizacao_rapida_lote(lote_id):
    lote = DativoLote.query.get_or_404(lote_id)
    antes = snapshot_entidade("dativo_lote", lote)

    try:
        situacao_rpv_id = request.form.get("situacao_rpv_id")
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_rpv_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_rpv_id)

        nota_empenho = request.form.get("nota_empenho", "").strip() or None
        numero_se_informado = "numero_se" in request.form
        numero_se = request.form.get("numero_se", "").strip() or None
        ordem_bancaria = request.form.get("ordem_bancaria", "").strip() or None
        validar_referencias_pagamento_principal(
            lote,
            nota_empenho=nota_empenho,
            ordem_bancaria=ordem_bancaria,
            exigir_preenchimento=status_quita_pagamento and not status_cancelado,
        )

        lote.nota_empenho = nota_empenho
        if numero_se_informado:
            lote.numero_se = numero_se
        lote.ordem_bancaria = ordem_bancaria
        lote.situacao_rpv_id = int(situacao_rpv_id)
        lote.situacao_imposto_id = DativosService.obter_situacao_imposto_sem_irrf().id
        lote.data_pagamento = resolver_data_pagamento_por_status(
            data_atual=lote.data_pagamento,
            status_pago=status_quita_pagamento,
            status_cancelado=status_cancelado,
            valor_informado=(
                request.form.get("data_pagamento", "").strip()
                if "data_pagamento" in request.form
                else None
            ),
            parser=parse_date,
            competencia=competencia_pagamento_automatica(),
        )
        lote.atualizado_por_id = current_user.id
        DativosService.sincronizar_lote_sem_irrf_com_itens(
            lote,
            usuario_id=current_user.id,
        )
        _registrar_historico_entidade(
            lote,
            usuario_id=current_user.id,
            acao="Atualização rápida",
            antes=antes,
        )

        db.session.commit()
        flash("Lote atualizado com sucesso.", "success")
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao atualizar lote: {exc}", "danger")

    origem = request.form.get("origem", "").strip()
    filtros = request.args.to_dict()

    if origem == "lista_cis":
        return redirect(url_for("dativos.lista_cis", **filtros))

    return redirect(url_for("dativos.lotes_sem_irrf", **filtros))


@dativos_bp.route("/lotes-sem-irrf/<int:lote_id>")
@login_required
def detalhe_lote_sem_irrf(lote_id):
    lote = DativoLote.query.get_or_404(lote_id)
    situacoes_rpv, situacoes_imposto = carregar_situacoes()
    url_retorno = _url_retorno_interna(url_for("dativos.lista_cis"))

    itens = (
        DativoItem.query.filter_by(dativo_lote_id=lote.id)
        .order_by(DativoItem.nome_beneficiario.asc())
        .all()
    )

    return render_template(
        "dativos/detalhe_lote.html",
        lote=lote,
        itens=itens,
        situacoes_rpv=situacoes_rpv,
        situacoes_imposto=situacoes_imposto,
        url_retorno=url_retorno,
    )


@dativos_bp.route("/lotes-sem-irrf/<int:lote_id>/salvar", methods=["POST"])
@login_required
def salvar_lote_sem_irrf(lote_id):
    lote = DativoLote.query.get_or_404(lote_id)
    url_retorno = _url_retorno_interna(url_for("dativos.lista_cis"))

    try:
        antes = snapshot_entidade("dativo_lote", lote)
        situacao_rpv_id = request.form.get("situacao_rpv_id")
        data_pagamento_raw = request.form.get("data_pagamento", "").strip()
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_rpv_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_rpv_id)

        if (
            data_pagamento_manual_exige_confirmacao(
                lote.data_pagamento,
                data_pagamento_raw,
                parser=parse_date,
            )
            and request.form.get("confirmar_data_pagamento_manual") != "1"
        ):
            flash(
                "Confirme a alteracao manual da data do pagamento. "
                "BI, competencia operacional e controles mensais passarao a seguir essa data.",
                "warning",
            )
            return redirect(
                url_for("dativos.detalhe_lote_sem_irrf", lote_id=lote.id, retorno=url_retorno)
            )

        nota_empenho = request.form.get("nota_empenho", "").strip() or None
        numero_se = request.form.get("numero_se", "").strip() or None
        ordem_bancaria = request.form.get("ordem_bancaria", "").strip() or None
        validar_referencias_pagamento_principal(
            lote,
            nota_empenho=nota_empenho,
            ordem_bancaria=ordem_bancaria,
            exigir_preenchimento=status_quita_pagamento and not status_cancelado,
        )

        lote.nota_empenho = nota_empenho
        lote.numero_se = numero_se
        lote.ordem_bancaria = ordem_bancaria
        lote.data_pagamento = resolver_data_pagamento_por_status(
            data_atual=lote.data_pagamento,
            status_pago=status_quita_pagamento,
            status_cancelado=status_cancelado,
            valor_informado=data_pagamento_raw,
            parser=parse_date,
            competencia=competencia_pagamento_automatica(),
        )
        lote.situacao_rpv_id = int(situacao_rpv_id)
        lote.situacao_imposto_id = DativosService.obter_situacao_imposto_sem_irrf().id
        lote.observacoes = request.form.get("observacoes", "").strip() or None
        lote.atualizado_por_id = current_user.id
        DativosService.sincronizar_lote_sem_irrf_com_itens(
            lote,
            usuario_id=current_user.id,
        )
        _registrar_historico_entidade(
            lote,
            usuario_id=current_user.id,
            acao="Alteração manual",
            antes=antes,
        )

        db.session.commit()
        flash("Detalhe do lote salvo com sucesso.", "success")
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao salvar detalhe do lote: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_lote_sem_irrf", lote_id=lote.id, retorno=url_retorno))


@dativos_bp.route("/itens-com-irrf/<int:item_id>/atualizacao-rapida", methods=["POST"])
@login_required
def atualizacao_rapida_item(item_id):
    item = DativoItem.query.get_or_404(item_id)
    antes = snapshot_entidade("dativo_item", item)

    try:
        situacao_rpv_id = request.form.get("situacao_rpv_id")
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_rpv_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_rpv_id)

        nota_empenho = request.form.get("nota_empenho", "").strip() or None
        numero_se_informado = "numero_se" in request.form
        numero_se = request.form.get("numero_se", "").strip() or None
        ordem_bancaria = request.form.get("ordem_bancaria", "").strip() or None
        validar_referencias_pagamento_principal(
            item,
            nota_empenho=nota_empenho,
            ordem_bancaria=ordem_bancaria,
            exigir_preenchimento=status_quita_pagamento and not status_cancelado,
        )

        item.nota_empenho = nota_empenho
        if numero_se_informado:
            item.numero_se = numero_se
        item.ordem_bancaria = ordem_bancaria
        item.ob_imposto = request.form.get("ob_imposto", "").strip() or None
        item.situacao_rpv_id = int(situacao_rpv_id)
        item.situacao_imposto_id = int(request.form.get("situacao_imposto_id"))
        item.data_pagamento = resolver_data_pagamento_por_status(
            data_atual=item.data_pagamento,
            status_pago=status_quita_pagamento,
            status_cancelado=status_cancelado,
            valor_informado=(
                request.form.get("data_pagamento", "").strip()
                if "data_pagamento" in request.form
                else None
            ),
            parser=parse_date,
            competencia=competencia_pagamento_automatica(),
        )

        if "valor_irrf" in request.form:
            valor_irrf_raw = request.form.get("valor_irrf", "").strip()
            item.valor_irrf = parse_decimal(valor_irrf_raw) if valor_irrf_raw else None

        if status_cancelado:
            item.reinf_status = None
        item.atualizado_por_id = current_user.id

        item.atualizar_campos_derivados()
        item.gerar_resumo_operacional(
            processo_edoc=item.dativo_ci.processo_edoc if item.dativo_ci else None,
            data_ci=item.dativo_ci.data_ci if item.dativo_ci else None,
        )
        _registrar_historico_entidade(
            item,
            usuario_id=current_user.id,
            acao="Atualização rápida",
            antes=antes,
        )

        db.session.commit()
        flash("Item atualizado com sucesso.", "success")
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao atualizar item: {exc}", "danger")

    origem = request.form.get("origem", "").strip()
    filtros = request.args.to_dict()

    if origem == "itens_com_irrf":
        return redirect(url_for("dativos.itens_com_irrf", **filtros))

    return redirect(url_for("dativos.lista_cis", **filtros))


@dativos_bp.route("/itens-com-irrf")
@login_required
def itens_com_irrf():
    q = request.args.get("q", "").strip()
    ne = request.args.get("ne", "").strip()
    exercicio = request.args.get("exercicio", "").strip()
    responsavel = request.args.get("responsavel", "meus").strip() or "meus"
    url_retorno_atual = current_internal_url(url_for("dativos.itens_com_irrf"))
    situacao_rpv_id = request.args.get("situacao_rpv_id", "").strip()
    situacao_imposto_id = request.args.get("situacao_imposto_id", "").strip()
    mostrar_encerrados = should_include_closed_in_queue(
        request.args.get("mostrar_encerrados"),
        situacao_rpv_id,
    )

    situacoes_rpv, situacoes_imposto = carregar_situacoes()
    situacoes_rpv_ocultas = collect_hidden_queue_status_ids(situacoes_rpv)
    usuarios = _carregar_usuarios_ativos()

    query = (
        DativoItem.query.options(
            selectinload(DativoItem.dativo_ci).selectinload(DativoCI.responsavel),
            selectinload(DativoItem.situacao_rpv),
            selectinload(DativoItem.situacao_imposto),
        )
        .join(DativoCI)
        .filter(DativoItem.grupo == "com_irrf")
    )

    if exercicio:
        query = query.filter(DativoCI.exercicio == exercicio)

    if responsavel == "meus":
        query = query.filter(DativoCI.responsavel_id == current_user.id)
    elif responsavel not in ("", "todos"):
        try:
            query = query.filter(DativoCI.responsavel_id == int(responsavel))
        except ValueError:
            responsavel = "meus"
            query = query.filter(DativoCI.responsavel_id == current_user.id)

    if situacao_rpv_id:
        query = query.filter(DativoItem.situacao_rpv_id == int(situacao_rpv_id))
    elif not mostrar_encerrados and situacoes_rpv_ocultas:
        query = query.filter(~DativoItem.situacao_rpv_id.in_(situacoes_rpv_ocultas))

    if situacao_imposto_id:
        query = query.filter(DativoItem.situacao_imposto_id == int(situacao_imposto_id))

    if ne:
        query = query.filter(DativoItem.nota_empenho.ilike(f"%{ne}%"))

    if q:
        q_like = f"%{q}%"
        q_doc = normalizar_documento(q)
        q_processo = normalizar_numero_processo(q)

        filtros = [
            DativoCI.processo_edoc.ilike(q_like),
            DativoItem.numero_processo.ilike(q_like),
            DativoItem.nome_beneficiario.ilike(q_like),
            DativoItem.resumo_operacional.ilike(q_like),
            DativoItem.nota_empenho.ilike(q_like),
            DativoItem.numero_se.ilike(q_like),
            DativoItem.ordem_bancaria.ilike(q_like),
            DativoItem.ob_imposto.ilike(q_like),
        ]
        if q_processo and q_processo != q:
            filtros.append(DativoItem.numero_processo.ilike(f"%{q_processo}%"))

        if q_doc:
            filtros.append(DativoItem.cpf_normalizado.ilike(f"%{q_doc}%"))

        query = query.filter(or_(*filtros))

    itens = query.order_by(DativoCI.data_ci.desc(), DativoItem.nome_beneficiario.asc()).all()
    busca_processo_contexto = ProcessoCrosscheckService.buscar_contexto_pesquisa(
        q or ne,
        retorno_url=url_retorno_atual,
    )

    return render_template(
        "dativos/itens.html",
        itens=itens,
        filtros=request.args,
        usuarios=usuarios,
        filtro_responsavel=responsavel,
        situacoes_rpv=situacoes_rpv,
        situacoes_imposto=situacoes_imposto,
        busca_processo_contexto=busca_processo_contexto,
        mostrar_encerrados=mostrar_encerrados,
        url_retorno_atual=url_retorno_atual,
    )


@dativos_bp.route("/itens-com-irrf/<int:item_id>", methods=["GET"])
@login_required
def detalhe_item_com_irrf(item_id):
    item = DativoItem.query.get_or_404(item_id)
    situacoes_rpv, situacoes_imposto = carregar_situacoes()
    url_retorno = _url_retorno_interna(url_for("dativos.itens_com_irrf"))

    return render_template(
        "dativos/detalhe_item.html",
        item=item,
        situacoes_rpv=situacoes_rpv,
        situacoes_imposto=situacoes_imposto,
        url_retorno=url_retorno,
    )


@dativos_bp.route("/itens-com-irrf/<int:item_id>/salvar", methods=["POST"])
@login_required
def salvar_item_com_irrf(item_id):
    item = DativoItem.query.get_or_404(item_id)
    url_retorno = _url_retorno_interna(url_for("dativos.itens_com_irrf"))

    try:
        antes = snapshot_entidade("dativo_item", item)
        valor_bruto_raw = request.form.get("valor_bruto")
        if valor_bruto_raw is not None:
            valor_bruto = parse_decimal(valor_bruto_raw.strip())
            if valor_bruto is None:
                raise ValueError("Valor bruto e obrigatorio.")
            if (
                _valor_bruto_alterado(item.valor_bruto, valor_bruto)
                and request.form.get("confirmar_edicao_valor_bruto") != "1"
            ):
                flash(
                    "Confirme a edicao do valor bruto antes de salvar. "
                    "A correcao recalcula o valor liquido e fica registrada no historico.",
                    "warning",
                )
                return redirect(
                    url_for("dativos.detalhe_item_com_irrf", item_id=item.id, retorno=url_retorno)
                )
            item.valor_bruto = valor_bruto

        valor_irrf_raw = request.form.get("valor_irrf", "").strip()
        item.valor_irrf = parse_decimal(valor_irrf_raw) if valor_irrf_raw else None

        data_pagamento_raw = request.form.get("data_pagamento", "").strip()
        nota_empenho = request.form.get("nota_empenho", "").strip() or None
        numero_se = request.form.get("numero_se", "").strip() or None
        ordem_bancaria = request.form.get("ordem_bancaria", "").strip() or None
        item.ob_imposto = request.form.get("ob_imposto", "").strip() or None
        situacao_rpv_id = request.form.get("situacao_rpv_id")
        status_quita_pagamento = situacao_id_quita_pagamento_principal(
            SituacaoEmpenho, situacao_rpv_id
        )
        status_cancelado = situacao_id_eh_cancelado(SituacaoEmpenho, situacao_rpv_id)

        if (
            data_pagamento_manual_exige_confirmacao(
                item.data_pagamento,
                data_pagamento_raw,
                parser=parse_date,
            )
            and request.form.get("confirmar_data_pagamento_manual") != "1"
        ):
            flash(
                "Confirme a alteracao manual da data do pagamento. "
                "BI, competencia operacional e a fila mensal da REINF passarao a seguir essa data.",
                "warning",
            )
            return redirect(
                url_for("dativos.detalhe_item_com_irrf", item_id=item.id, retorno=url_retorno)
            )

        validar_referencias_pagamento_principal(
            item,
            nota_empenho=nota_empenho,
            ordem_bancaria=ordem_bancaria,
            exigir_preenchimento=status_quita_pagamento and not status_cancelado,
        )

        item.nota_empenho = nota_empenho
        item.numero_se = numero_se
        item.ordem_bancaria = ordem_bancaria
        item.situacao_rpv_id = int(situacao_rpv_id)
        item.situacao_imposto_id = int(request.form.get("situacao_imposto_id"))
        item.data_pagamento = resolver_data_pagamento_por_status(
            data_atual=item.data_pagamento,
            status_pago=status_quita_pagamento,
            status_cancelado=status_cancelado,
            valor_informado=data_pagamento_raw,
            parser=parse_date,
            competencia=competencia_pagamento_automatica(),
        )
        item.observacoes = request.form.get("observacoes", "").strip() or None
        item.atualizado_por_id = current_user.id

        if status_cancelado:
            item.reinf_status = None
        item.atualizar_campos_derivados()
        item.gerar_resumo_operacional(
            processo_edoc=item.dativo_ci.processo_edoc if item.dativo_ci else None,
            data_ci=item.dativo_ci.data_ci if item.dativo_ci else None,
        )
        _registrar_historico_entidade(
            item,
            usuario_id=current_user.id,
            acao="Alteração manual",
            antes=antes,
        )

        db.session.commit()
        flash("Item com IRRF atualizado com sucesso.", "success")
    except PaymentReferenceValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao atualizar item com IRRF: {exc}", "danger")

    return redirect(url_for("dativos.detalhe_item_com_irrf", item_id=item.id, retorno=url_retorno))


