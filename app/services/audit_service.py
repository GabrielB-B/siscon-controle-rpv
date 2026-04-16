from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import url_for

from app.extensions import db
from app.models import DativoCI, DativoItem, DativoLote, HistoricoAlteracao, RegistroRPV, RPVPendenciaDocumento


ENTITY_LABELS = {
    "registro_rpv": "RPV",
    "dativo_ci": "C.I. de dativo",
    "dativo_lote": "Lote sem IRRF",
    "dativo_item": "Item de dativo",
    "rpv_pendencia_documento": "Pendencia documental de RPV",
}

FIELD_SPECS = {
    "registro_rpv": {
        "processo_edoc": ("C.I.", "text"),
        "numero_processo": ("Processo", "text"),
        "data_ci": ("Data da C.I.", "date"),
        "exercicio": ("Exercício", "month"),
        "tipo_rpv": ("Tipo de RPV", "text"),
        "nome_beneficiario": ("Beneficiário", "text"),
        "tipo_documento": ("Tipo de documento", "text"),
        "documento_original": ("Documento", "document"),
        "valor_bruto": ("Valor bruto", "money"),
        "valor_irrf": ("Valor IRRF", "money"),
        "sem_irrf": ("Sem IRRF", "bool"),
        "nota_empenho": ("NE", "text"),
        "numero_se": ("SE", "text"),
        "ordem_bancaria": ("OB principal", "text"),
        "ob_imposto": ("OB IRRF", "text"),
        "data_pagamento": ("Data do pagamento principal", "date"),
        "data_pagamento_irrf": ("Data do pagamento do IRRF", "date"),
        "situacao_empenho": ("Situação do RPV", "text"),
        "situacao_imposto": ("Situação do IRRF", "text"),
        "reinf_status": ("Situação REINF", "text"),
        "observacoes": ("Observações", "text"),
        "responsavel": ("Responsável", "text"),
    },
    "dativo_ci": {
        "exercicio": ("Exercício", "month"),
        "processo_edoc": ("C.I.", "text"),
        "data_ci": ("Data da C.I.", "date"),
        "descricao": ("Descrição", "text"),
        "responsavel": ("Responsável", "text"),
    },
    "dativo_lote": {
        "ci": ("C.I.", "text"),
        "responsavel": ("Responsável", "text"),
        "data_pagamento": ("Data de pagamento", "date"),
        "nota_empenho": ("NE", "text"),
        "numero_se": ("SE", "text"),
        "ordem_bancaria": ("OB", "text"),
        "situacao_rpv": ("Situação do RPV", "text"),
        "situacao_imposto": ("Situação do IRRF", "text"),
        "observacoes": ("Observações", "text"),
        "quantidade_itens": ("Quantidade de itens", "int"),
        "valor_total_bruto": ("Valor bruto total", "money"),
        "valor_total_irrf": ("IRRF total", "money"),
        "valor_total_liquido": ("Valor líquido total", "money"),
    },
    "dativo_item": {
        "ci": ("C.I.", "text"),
        "responsavel": ("Responsável", "text"),
        "grupo": ("Grupo", "text"),
        "nome_beneficiario": ("Beneficiário", "text"),
        "cpf_original": ("Documento", "document"),
        "numero_processo": ("Processo", "text"),
        "valor_bruto": ("Valor bruto", "money"),
        "valor_irrf": ("Valor IRRF", "money"),
        "data_pagamento": ("Data de pagamento", "date"),
        "nota_empenho": ("NE", "text"),
        "numero_se": ("SE", "text"),
        "ordem_bancaria": ("OB principal", "text"),
        "ob_imposto": ("OB IRRF", "text"),
        "situacao_rpv": ("Situação do RPV", "text"),
        "situacao_imposto": ("Situação do IRRF", "text"),
        "reinf_status": ("Situação REINF", "text"),
        "dispensa_irrf_confirmada": ("Dispensa de IRRF confirmada", "bool"),
        "observacoes": ("Observações", "text"),
        "dativo_lote_id": ("Lote vinculado", "text"),
    },
    "rpv_pendencia_documento": {
        "processo_edoc": ("C.I.", "text"),
        "numero_processo": ("Processo", "text"),
        "data_ci": ("Data da C.I.", "date"),
        "exercicio": ("Exercicio", "month"),
        "tipo_rpv": ("Tipo de RPV", "text"),
        "nome_beneficiario": ("Beneficiario", "text"),
        "tipo_documento": ("Tipo de documento", "text"),
        "documento_original": ("Documento", "document"),
        "valor_bruto": ("Valor bruto", "money"),
        "valor_irrf": ("Valor IRRF", "money"),
        "sem_irrf": ("Sem IRRF", "bool"),
        "motivo_pendencia": ("Motivo documental", "text"),
        "status": ("Status", "text"),
        "documento_confirmado_manual": ("Documento confirmado manualmente", "bool"),
        "responsavel": ("Responsavel", "text"),
        "observacoes": ("Observacoes", "text"),
    },
}


def _money_text(value) -> str:
    valor = Decimal(value or 0).quantize(Decimal("0.01"))
    texto = f"{valor:,.2f}"
    return f"R$ {texto}".replace(",", "X").replace(".", ",").replace("X", ".")


def _month_text(value) -> str:
    valor = str(value or "").strip()
    if len(valor) == 7 and "-" in valor:
        ano, mes = valor.split("-")
        return f"{mes}/{ano}"
    return valor or "Não informado"


def _date_text(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "Não informado"


def _document_text(value) -> str:
    return str(value or "").strip() or "Não informado"


def _bool_text(value) -> str:
    if value is None:
        return "Não informado"
    return "Sim" if bool(value) else "Não"


def _int_text(value) -> str:
    if value is None:
        return "0"
    return str(int(value))


def _text_text(value) -> str:
    texto = str(value or "").strip()
    return texto or "Não informado"


def _format_value(value, kind: str) -> str:
    if kind == "money":
        return _money_text(value)
    if kind == "date":
        return _date_text(value)
    if kind == "month":
        return _month_text(value)
    if kind == "document":
        return _document_text(value)
    if kind == "bool":
        return _bool_text(value)
    if kind == "int":
        return _int_text(value)
    return _text_text(value)


def snapshot_registro_rpv(registro: RegistroRPV) -> dict:
    processo = registro.processo
    return {
        "processo_edoc": getattr(processo, "processo_edoc", None),
        "numero_processo": getattr(processo, "numero_processo", None),
        "data_ci": getattr(processo, "data_ci", None),
        "exercicio": getattr(processo, "exercicio", None),
        "tipo_rpv": getattr(getattr(registro, "tipo_rpv", None), "nome", None),
        "nome_beneficiario": registro.nome_beneficiario,
        "tipo_documento": registro.tipo_documento,
        "documento_original": registro.documento_original,
        "valor_bruto": registro.valor_bruto,
        "valor_irrf": registro.valor_irrf,
        "sem_irrf": registro.sem_irrf,
        "nota_empenho": registro.nota_empenho,
        "numero_se": getattr(registro, "numero_se", None),
        "ordem_bancaria": registro.ordem_bancaria,
        "ob_imposto": registro.ob_imposto,
        "data_pagamento": registro.data_pagamento,
        "data_pagamento_irrf": getattr(registro, "data_pagamento_irrf", None),
        "situacao_empenho": getattr(getattr(registro, "situacao_empenho", None), "nome", None),
        "situacao_imposto": getattr(getattr(registro, "situacao_imposto", None), "nome", None),
        "reinf_status": registro.reinf_status,
        "observacoes": registro.observacoes,
        "responsavel": getattr(getattr(registro, "elaborador", None), "nome", None),
    }


def snapshot_dativo_ci(dativo_ci: DativoCI) -> dict:
    return {
        "exercicio": dativo_ci.exercicio,
        "processo_edoc": dativo_ci.processo_edoc,
        "data_ci": dativo_ci.data_ci,
        "descricao": dativo_ci.descricao,
        "responsavel": getattr(getattr(dativo_ci, "responsavel", None), "nome", None),
    }


def snapshot_dativo_lote(lote: DativoLote) -> dict:
    return {
        "ci": getattr(getattr(lote, "dativo_ci", None), "processo_edoc", None),
        "responsavel": getattr(getattr(lote, "responsavel", None), "nome", None),
        "data_pagamento": lote.data_pagamento,
        "nota_empenho": lote.nota_empenho,
        "numero_se": getattr(lote, "numero_se", None),
        "ordem_bancaria": lote.ordem_bancaria,
        "situacao_rpv": getattr(getattr(lote, "situacao_rpv", None), "nome", None),
        "situacao_imposto": getattr(getattr(lote, "situacao_imposto", None), "nome", None),
        "observacoes": lote.observacoes,
        "quantidade_itens": lote.quantidade_itens,
        "valor_total_bruto": lote.valor_total_bruto,
        "valor_total_irrf": lote.valor_total_irrf,
        "valor_total_liquido": lote.valor_total_liquido,
    }


def snapshot_dativo_item(item: DativoItem) -> dict:
    return {
        "ci": getattr(getattr(item, "dativo_ci", None), "processo_edoc", None),
        "responsavel": getattr(getattr(item, "responsavel", None), "nome", None),
        "grupo": "Com IRRF" if item.grupo == "com_irrf" else "Sem IRRF",
        "nome_beneficiario": item.nome_beneficiario,
        "cpf_original": item.cpf_original,
        "numero_processo": item.numero_processo,
        "valor_bruto": item.valor_bruto,
        "valor_irrf": item.valor_irrf,
        "data_pagamento": item.data_pagamento,
        "nota_empenho": item.nota_empenho,
        "numero_se": getattr(item, "numero_se", None),
        "ordem_bancaria": item.ordem_bancaria,
        "ob_imposto": item.ob_imposto,
        "situacao_rpv": getattr(getattr(item, "situacao_rpv", None), "nome", None),
        "situacao_imposto": getattr(getattr(item, "situacao_imposto", None), "nome", None),
        "reinf_status": item.reinf_status,
        "dispensa_irrf_confirmada": item.dispensa_irrf_confirmada,
        "observacoes": item.observacoes,
        "dativo_lote_id": item.dativo_lote_id,
    }


def snapshot_rpv_pendencia_documento(pendencia: RPVPendenciaDocumento) -> dict:
    return {
        "processo_edoc": pendencia.processo_edoc,
        "numero_processo": pendencia.numero_processo,
        "data_ci": pendencia.data_ci,
        "exercicio": pendencia.exercicio,
        "tipo_rpv": getattr(getattr(pendencia, "tipo_rpv", None), "nome", None),
        "nome_beneficiario": pendencia.nome_beneficiario,
        "tipo_documento": pendencia.tipo_documento,
        "documento_original": pendencia.documento_original,
        "valor_bruto": pendencia.valor_bruto,
        "valor_irrf": pendencia.valor_irrf,
        "sem_irrf": pendencia.sem_irrf,
        "motivo_pendencia": pendencia.motivo_pendencia,
        "status": pendencia.status_legivel,
        "documento_confirmado_manual": pendencia.documento_confirmado_manual,
        "responsavel": getattr(getattr(pendencia, "responsavel", None), "nome", None),
        "observacoes": pendencia.observacoes,
    }


def snapshot_entidade(entidade_tipo: str, entidade) -> dict:
    if entidade_tipo == "registro_rpv":
        return snapshot_registro_rpv(entidade)
    if entidade_tipo == "dativo_ci":
        return snapshot_dativo_ci(entidade)
    if entidade_tipo == "dativo_lote":
        return snapshot_dativo_lote(entidade)
    if entidade_tipo == "dativo_item":
        return snapshot_dativo_item(entidade)
    if entidade_tipo == "rpv_pendencia_documento":
        return snapshot_rpv_pendencia_documento(entidade)
    raise ValueError(f"Tipo de entidade não suportado: {entidade_tipo}")


def _montar_alteracoes(entidade_tipo: str, antes: dict | None, depois: dict | None) -> list[dict]:
    specs = FIELD_SPECS.get(entidade_tipo, {})
    antes = antes or {}
    depois = depois or {}
    alteracoes = []

    for campo, (rotulo, kind) in specs.items():
        valor_antes = antes.get(campo)
        valor_depois = depois.get(campo)
        if valor_antes == valor_depois:
            continue
        alteracoes.append(
            {
                "campo": campo,
                "rotulo": rotulo,
                "antes": _format_value(valor_antes, kind),
                "depois": _format_value(valor_depois, kind),
            }
        )

    return alteracoes


def registrar_evento(
    *,
    entidade_tipo: str,
    entidade_id: int,
    usuario_id: int,
    acao: str,
    antes: dict | None = None,
    depois: dict | None = None,
    resumo: str | None = None,
    forcar_registro: bool = False,
) -> HistoricoAlteracao | None:
    alteracoes = _montar_alteracoes(entidade_tipo, antes, depois)
    if not alteracoes and not resumo and not forcar_registro:
        return None

    historico = HistoricoAlteracao(
        entidade_tipo=entidade_tipo,
        entidade_id=entidade_id,
        alterado_por_id=usuario_id,
        acao=acao,
        resumo=resumo,
    )
    historico.definir_alteracoes(alteracoes)
    db.session.add(historico)
    return historico


def carregar_historico(entidade_tipo: str, entidade_id: int) -> list[HistoricoAlteracao]:
    return (
        HistoricoAlteracao.query.filter_by(entidade_tipo=entidade_tipo, entidade_id=entidade_id)
        .order_by(HistoricoAlteracao.criado_em.desc(), HistoricoAlteracao.id.desc())
        .all()
    )


def _rotulo_entidade(entidade_tipo: str) -> str:
    return ENTITY_LABELS.get(entidade_tipo, entidade_tipo)


def contexto_historico(entidade_tipo: str, entidade) -> dict:
    if entidade_tipo == "registro_rpv":
        return {
            "titulo": entidade.nome_beneficiario,
            "subtitulo": entidade.historico_auto,
            "label": _rotulo_entidade(entidade_tipo),
            "retorno_url": url_for("cadastros.editar_rpv", registro_id=entidade.id),
            "retorno_label": "Voltar para o RPV",
        }

    if entidade_tipo == "dativo_ci":
        return {
            "titulo": entidade.processo_edoc,
            "subtitulo": entidade.descricao,
            "label": _rotulo_entidade(entidade_tipo),
            "retorno_url": url_for("dativos.detalhe_ci", ci_id=entidade.id),
            "retorno_label": "Voltar para a C.I.",
        }

    if entidade_tipo == "dativo_lote":
        return {
            "titulo": f"Lote {getattr(getattr(entidade, 'dativo_ci', None), 'processo_edoc', entidade.id)}",
            "subtitulo": entidade.resumo_operacional,
            "label": _rotulo_entidade(entidade_tipo),
            "retorno_url": url_for("dativos.detalhe_lote_sem_irrf", lote_id=entidade.id),
            "retorno_label": "Voltar para o lote",
        }

    if entidade_tipo == "dativo_item":
        if entidade.grupo == "sem_irrf" and entidade.dativo_lote_id:
            retorno_url = url_for(
                "dativos.editar_item_lote",
                lote_id=entidade.dativo_lote_id,
                item_id=entidade.id,
            )
            retorno_label = "Voltar para o beneficiario"
        else:
            retorno_url = url_for("dativos.detalhe_item_com_irrf", item_id=entidade.id)
            retorno_label = "Voltar para o item"

        return {
            "titulo": entidade.nome_beneficiario,
            "subtitulo": entidade.resumo_operacional_atual,
            "label": _rotulo_entidade(entidade_tipo),
            "retorno_url": retorno_url,
            "retorno_label": retorno_label,
        }

    if entidade_tipo == "rpv_pendencia_documento":
        return {
            "titulo": entidade.nome_beneficiario,
            "subtitulo": entidade.resumo_operacional,
            "label": _rotulo_entidade(entidade_tipo),
            "retorno_url": url_for(
                "cadastros.detalhe_pendencia_documental",
                pendencia_id=entidade.id,
            ),
            "retorno_label": "Voltar para a pendencia",
        }

    raise ValueError(f"Tipo de entidade nao suportado: {entidade_tipo}")
