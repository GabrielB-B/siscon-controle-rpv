from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.models import DativoItem, DativoLote, RegistroRPV


class PaymentReferenceValidationError(ValueError):
    """Erro de validacao para NE/OB do pagamento principal."""


@dataclass(slots=True)
class _ReferenceContext:
    entidade_tipo: str
    entidade_id: int | None
    lote_sem_irrf_id: int | None


def _normalizar_referencia(valor: str | None) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    return re.sub(r"\s+", "", texto).upper()


def _contexto_entidade(entidade) -> _ReferenceContext:
    if isinstance(entidade, RegistroRPV):
        return _ReferenceContext("registro_rpv", entidade.id, None)

    if isinstance(entidade, DativoLote):
        lote_id = entidade.id if getattr(entidade, "tipo_lote", None) == "sem_irrf" else None
        return _ReferenceContext("dativo_lote", entidade.id, lote_id)

    if isinstance(entidade, DativoItem):
        lote_id = entidade.dativo_lote_id if getattr(entidade, "grupo", None) == "sem_irrf" else None
        return _ReferenceContext("dativo_item", entidade.id, lote_id)

    raise TypeError("Entidade sem suporte para validacao de NE/OB.")


def _descricao_entidade(entidade) -> str:
    if isinstance(entidade, RegistroRPV):
        processo = getattr(entidade, "processo", None)
        processo_edoc = getattr(processo, "processo_edoc", None) or "C.I. não informada"
        numero_processo = getattr(processo, "numero_processo", None) or "processo não informado"
        return (
            f'RPV "{entidade.nome_beneficiario}" '
            f"(C.I. {processo_edoc} / processo {numero_processo})"
        )

    if isinstance(entidade, DativoLote):
        dativo_ci = getattr(entidade, "dativo_ci", None)
        processo_edoc = getattr(dativo_ci, "processo_edoc", None) or "C.I. não informada"
        return f"lote sem IRRF da C.I. {processo_edoc}"

    if isinstance(entidade, DativoItem):
        dativo_ci = getattr(entidade, "dativo_ci", None)
        processo_edoc = getattr(dativo_ci, "processo_edoc", None) or "C.I. não informada"
        processo = getattr(entidade, "numero_processo", None) or "processo não informado"
        if getattr(entidade, "grupo", None) == "sem_irrf":
            return (
                f'beneficiário do lote sem IRRF "{entidade.nome_beneficiario}" '
                f"(C.I. {processo_edoc} / processo {processo})"
            )
        return (
            f'item com IRRF "{entidade.nome_beneficiario}" '
            f"(C.I. {processo_edoc} / processo {processo})"
        )

    return "registro operacional"


def _descricao_curta_entidade(entidade) -> str:
    if isinstance(entidade, RegistroRPV):
        return f'RPV "{entidade.nome_beneficiario}"'
    if isinstance(entidade, DativoLote):
        processo_edoc = getattr(getattr(entidade, "dativo_ci", None), "processo_edoc", None)
        return f"lote sem IRRF da C.I. {processo_edoc or 'não informada'}"
    if isinstance(entidade, DativoItem):
        if getattr(entidade, "grupo", None) == "sem_irrf":
            return f'beneficiário do lote sem IRRF "{entidade.nome_beneficiario}"'
        return f'item com IRRF "{entidade.nome_beneficiario}"'
    return "registro"


def _referencias_iguais(valor_a: str | None, valor_b: str | None) -> bool:
    return _normalizar_referencia(valor_a) == _normalizar_referencia(valor_b)


def _contexto_ignora_conflito(contexto: _ReferenceContext, candidato) -> bool:
    if isinstance(candidato, RegistroRPV):
        return contexto.entidade_tipo == "registro_rpv" and candidato.id == contexto.entidade_id

    if isinstance(candidato, DativoLote):
        if contexto.entidade_tipo == "dativo_lote" and candidato.id == contexto.entidade_id:
            return True
        return (
            contexto.lote_sem_irrf_id is not None
            and getattr(candidato, "tipo_lote", None) == "sem_irrf"
            and candidato.id == contexto.lote_sem_irrf_id
        )

    if isinstance(candidato, DativoItem):
        if contexto.entidade_tipo == "dativo_item" and candidato.id == contexto.entidade_id:
            return True
        return (
            contexto.lote_sem_irrf_id is not None
            and getattr(candidato, "grupo", None) == "sem_irrf"
            and candidato.dativo_lote_id == contexto.lote_sem_irrf_id
        )

    return False


def _consulta_normalizada(modelo, campo: str, valor_normalizado: str):
    coluna = getattr(modelo, campo)
    coluna_normalizada = func.upper(func.replace(func.trim(coluna), " ", ""))
    query = modelo.query.filter(coluna.isnot(None), coluna_normalizada == valor_normalizado)

    if hasattr(modelo, "ativo"):
        query = query.filter(getattr(modelo, "ativo").is_(True))

    if modelo is RegistroRPV:
        query = query.options(joinedload(RegistroRPV.processo)).order_by(RegistroRPV.id.asc())
    elif modelo is DativoLote:
        query = query.options(joinedload(DativoLote.dativo_ci)).order_by(DativoLote.id.asc())
    elif modelo is DativoItem:
        query = query.options(joinedload(DativoItem.dativo_ci)).order_by(DativoItem.id.asc())

    return query.all()


def _buscar_conflito(entidade, campo: str, valor: str | None):
    valor_normalizado = _normalizar_referencia(valor)
    if not valor_normalizado:
        return None

    contexto = _contexto_entidade(entidade)

    for modelo in (RegistroRPV, DativoLote, DativoItem):
        for candidato in _consulta_normalizada(modelo, campo, valor_normalizado):
            if _contexto_ignora_conflito(contexto, candidato):
                continue
            return candidato

    return None


def _rotulo_campos(campos: list[str]) -> str:
    if not campos:
        return ""
    if len(campos) == 1:
        return campos[0]
    return ", ".join(campos[:-1]) + f" e {campos[-1]}"


def validar_referencias_pagamento_principal(
    entidade,
    *,
    nota_empenho: str | None,
    ordem_bancaria: str | None,
    exigir_preenchimento: bool = False,
) -> None:
    mensagens: list[str] = []
    campos_obrigatorios: list[str] = []

    referencias = (
        ("nota_empenho", "NE", nota_empenho, getattr(entidade, "nota_empenho", None)),
        (
            "ordem_bancaria",
            "OB",
            ordem_bancaria,
            getattr(entidade, "ordem_bancaria", None),
        ),
    )

    for campo, rotulo, valor_novo, valor_atual in referencias:
        if not str(valor_novo or "").strip():
            if exigir_preenchimento:
                campos_obrigatorios.append(rotulo)
            continue

        if not exigir_preenchimento and _referencias_iguais(valor_novo, valor_atual):
            continue

        conflito = _buscar_conflito(entidade, campo, valor_novo)
        if conflito is None:
            continue

        mensagens.append(
            f'{rotulo} "{str(valor_novo).strip()}" já está em uso em {_descricao_entidade(conflito)}.'
        )

    if campos_obrigatorios:
        mensagens.insert(
            0,
            "Para marcar o pagamento principal como pago ou concluído, "
            f"informe {_rotulo_campos(campos_obrigatorios)}. "
            f"Registro: {_descricao_curta_entidade(entidade)}.",
        )

    if mensagens:
        raise PaymentReferenceValidationError(" ".join(mensagens))
