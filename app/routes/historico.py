from datetime import date, timedelta

from flask import Blueprint, abort, render_template
from flask_login import login_required

from app.extensions import db
from app.models import DativoCI, DativoItem, DativoLote, RegistroRPV, RPVPendenciaDocumento
from app.services.audit_service import carregar_historico, contexto_historico


historico_bp = Blueprint("historico", __name__, url_prefix="/historico")


ENTITY_LOADERS = {
    "registro_rpv": lambda entity_id: db.session.get(RegistroRPV, entity_id),
    "dativo_ci": lambda entity_id: db.session.get(DativoCI, entity_id),
    "dativo_lote": lambda entity_id: db.session.get(DativoLote, entity_id),
    "dativo_item": lambda entity_id: db.session.get(DativoItem, entity_id),
    "rpv_pendencia_documento": lambda entity_id: db.session.get(RPVPendenciaDocumento, entity_id),
}

CRITICAL_FIELDS = {
    "valor_bruto",
    "valor_irrf",
    "valor_liquido",
    "valor_total_bruto",
    "valor_total_irrf",
    "valor_total_liquido",
    "data_pagamento",
    "data_pagamento_irrf",
    "situacao_empenho",
    "situacao_rpv",
    "situacao_imposto",
    "reinf_status",
    "responsavel",
    "documento_original",
    "cpf_original",
    "status",
    "documento_confirmado_manual",
}

CRITICAL_ACTION_KEYWORDS = (
    "cancel",
    "convers",
    "reabert",
    "valor bruto",
    "pagamento",
    "responsabilidade",
    "documento",
)


def _date_group_label(value) -> str:
    if not value:
        return "Sem data registrada"

    event_date = value.date() if hasattr(value, "date") else value
    today = date.today()
    if event_date == today:
        return f"Hoje, {event_date.strftime('%d/%m/%Y')}"
    if event_date == today - timedelta(days=1):
        return f"Ontem, {event_date.strftime('%d/%m/%Y')}"
    return event_date.strftime("%d/%m/%Y")


def _event_variant(evento, alteracoes: list[dict]) -> str:
    action = str(evento.acao or "").lower()
    if any(keyword in action for keyword in ("cancel", "descart")):
        return "danger"
    if any(keyword in action for keyword in ("cadastro", "convers", "reabert")):
        return "success"
    if any(change.get("critica") for change in alteracoes):
        return "warning"
    if any(keyword in action for keyword in CRITICAL_ACTION_KEYWORDS):
        return "warning"
    return "neutral"


def _event_summary(evento, alteracoes: list[dict]) -> str:
    actor = getattr(getattr(evento, "alterado_por", None), "nome", None) or "Sistema"
    action = str(evento.acao or "Evento registrado").strip()

    if evento.resumo:
        return f"{actor} registrou: {evento.resumo}"

    if len(alteracoes) == 1:
        return f"{actor} alterou {alteracoes[0]['rotulo']}."

    if alteracoes:
        critical = [change for change in alteracoes if change.get("critica")]
        if critical:
            first = critical[0]["rotulo"]
            extra = len(critical) - 1
            if extra > 0:
                return f"{actor} alterou {first} e mais {extra} campo(s) sensivel(is)."
            return f"{actor} alterou {first}."
        return f"{actor} registrou {len(alteracoes)} mudanca(s)."

    return f"{actor} registrou {action.lower()}."


def _prepare_history_groups(historico):
    groups = []
    group_index = {}

    for evento in historico:
        changes = []
        for change in evento.alteracoes:
            field_name = str(change.get("campo") or "")
            change = dict(change)
            change["critica"] = field_name in CRITICAL_FIELDS
            changes.append(change)

        variant = _event_variant(evento, changes)
        prepared = {
            "evento": evento,
            "alteracoes": changes,
            "variant": variant,
            "is_critical": variant in {"danger", "warning"},
            "summary": _event_summary(evento, changes),
            "actor": getattr(getattr(evento, "alterado_por", None), "nome", None) or "-",
            "date_label": evento.criado_em.strftime("%d/%m/%Y") if evento.criado_em else "-",
            "time_label": evento.criado_em.strftime("%H:%M") if evento.criado_em else "-",
            "weekday_label": evento.criado_em.strftime("%A") if evento.criado_em else "",
        }

        label = _date_group_label(evento.criado_em)
        if label not in group_index:
            group_index[label] = {"label": label, "events": []}
            groups.append(group_index[label])

        group_index[label]["events"].append(prepared)

    return groups


@historico_bp.route("/<entidade_tipo>/<int:entidade_id>")
@login_required
def detalhe(entidade_tipo: str, entidade_id: int):
    loader = ENTITY_LOADERS.get(entidade_tipo)
    if not loader:
        abort(404)

    entidade = loader(entidade_id)
    if not entidade:
        abort(404)

    historico = carregar_historico(entidade_tipo, entidade_id)
    contexto = contexto_historico(entidade_tipo, entidade)
    history_groups = _prepare_history_groups(historico)
    return render_template(
        "historico/detalhe.html",
        historico=historico,
        history_groups=history_groups,
        contexto=contexto,
    )
