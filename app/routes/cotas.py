from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.services.cotas_rpv_service import CotasRPVService


cotas_bp = Blueprint("cotas", __name__, url_prefix="/cotas-rpv")


def _parse_decimal_br(valor: str | None) -> Decimal:
    texto = str(valor or "").strip()
    if not texto:
        return Decimal("0.00")

    texto = texto.replace("R$", "").replace(" ", "")
    if texto.startswith("-"):
        raise ValueError("Os valores de cota devem ser positivos.")
    if any(caractere not in "0123456789,." for caractere in texto):
        raise ValueError("Use apenas números nos valores de cota.")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    try:
        return Decimal(texto)
    except InvalidOperation as exc:
        raise ValueError("Use apenas números nos valores de cota.") from exc


def _parse_flag(valor: str | None) -> bool:
    return str(valor or "").strip().lower() in {"1", "true", "sim", "on", "yes"}


def _format_decimal_br(valor: Decimal) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@cotas_bp.route("", methods=["GET"])
@login_required
def index():
    competencia = CotasRPVService.normalizar_competencia(request.args.get("competencia"))
    incluir_pagos = _parse_flag(request.args.get("incluir_pagos"))
    resumo = CotasRPVService.resumo_competencia(competencia=competencia)
    movimentos = CotasRPVService.movimentos_recentes()
    consumos_view = CotasRPVService.painel_consumos_ativos(
        competencia=resumo["competencia"],
        incluir_pagos=incluir_pagos,
    )
    return render_template(
        "cotas/index.html",
        resumo_cotas=resumo,
        movimentos_cotas=movimentos,
        consumos_cotas=consumos_view["itens"],
        consumos_view=consumos_view,
    )


@cotas_bp.route("/lancar", methods=["POST"])
@login_required
def lancar():
    competencia = request.form.get("competencia", "").strip()
    try:
        valores_por_grupo = {
            "pessoal": _parse_decimal_br(request.form.get("valor_pessoal")),
            "comum": _parse_decimal_br(request.form.get("valor_comum")),
            "pericial": _parse_decimal_br(request.form.get("valor_pericial")),
        }
        observacoes = request.form.get("observacoes", "").strip() or None
        CotasRPVService.registrar_aportes(
            competencia=competencia,
            valores_por_grupo=valores_por_grupo,
            usuario_id=current_user.id,
            observacoes=observacoes,
        )
        db.session.commit()
        flash("Cota mensal registrada com sucesso.", "success")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao registrar a cota mensal: {exc}", "danger")
    return redirect(url_for("cotas.index", competencia=competencia))


@cotas_bp.route("/transferir", methods=["POST"])
@login_required
def transferir():
    competencia_destino = request.form.get("competencia_destino", "").strip()
    bucket_id_raw = request.form.get("bucket_id", "").strip()
    try:
        if not bucket_id_raw.isdigit():
            raise ValueError("Saldo de origem invalido para transferencia.")
        valor_transferido = CotasRPVService.transferir_saldo_integral(
            origem_competencia_id=int(bucket_id_raw),
            competencia_destino=competencia_destino,
            usuario_id=current_user.id,
            observacoes=request.form.get("observacoes", "").strip() or None,
        )
        db.session.commit()
        flash(
            f"Saldo transferido com sucesso: {_format_decimal_br(valor_transferido)}",
            "success",
        )
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao transferir saldo de cota: {exc}", "danger")
    return redirect(url_for("cotas.index", competencia=competencia_destino))


@cotas_bp.route("/conciliar", methods=["POST"])
@login_required
def conciliar():
    competencia = request.form.get("competencia", "").strip()
    grupo_cota = request.form.get("grupo_cota", "").strip()
    try:
        resultado = CotasRPVService.conciliar_saldo_oficial(
            competencia=competencia,
            grupo_cota=grupo_cota,
            saldo_oficial_atual=_parse_decimal_br(request.form.get("saldo_oficial_atual")),
            usuario_id=current_user.id,
            observacoes=request.form.get("observacoes", "").strip() or None,
        )
        db.session.commit()
        flash(
            (
                f"Saldo da ficha {resultado['grupo_label']} conciliado com sucesso. "
                f"Ajuste aplicado: {_format_decimal_br(resultado['delta_ajuste'])}. "
                f"Saldo atual: {_format_decimal_br(resultado['saldo_oficial_atual'])}."
            ),
            "success",
        )
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        flash(f"Erro ao conciliar saldo de cota: {exc}", "danger")
    return redirect(url_for("cotas.index", competencia=competencia))
