from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote

from flask import current_app
from sqlalchemy import text
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import DativoItem, DativoLote, RegistroRPV, User
from app.routes.dashboard import _cards_bi, _coletar_dataset_bi
from app.utils.normalizers import telefone_brasileiro_valido


SEVERITY_ORDER = {
    "ok": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
}


def _decimal(valor) -> Decimal:
    try:
        return Decimal(valor or 0)
    except Exception:
        return Decimal("0.00")


def _decimal_texto(valor: Decimal) -> str:
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _registro_ativo_operacional(registro) -> bool:
    return bool(getattr(registro, "ativo", False)) and not bool(
        getattr(registro, "status_principal_cancelado", False)
    )


def _sqlite_database_path() -> Path | None:
    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if not uri.startswith("sqlite:///"):
        return None

    db_value = unquote(uri.replace("sqlite:///", "", 1))
    db_path = Path(db_value)
    if db_path.is_absolute():
        return db_path
    return (Path(current_app.instance_path) / db_path.name).resolve()


def _sqlite_root_shadow_info(active_database_path: Path | None) -> dict | None:
    if not active_database_path:
        return None

    project_root = Path(current_app.root_path).parent
    root_database_path = (project_root / "controle_rpv.db").resolve()
    active_database_path = active_database_path.resolve()
    root_exists = root_database_path.exists()

    try:
        root_size = root_database_path.stat().st_size if root_exists else 0
    except OSError:
        root_size = None

    try:
        active_size = active_database_path.stat().st_size if active_database_path.exists() else 0
    except OSError:
        active_size = None

    return {
        "root_database_path": str(root_database_path),
        "root_database_exists": root_exists,
        "root_database_size_bytes": root_size,
        "active_database_path": str(active_database_path),
        "active_database_size_bytes": active_size,
        "root_database_is_active": root_database_path == active_database_path,
        "shadow_database_present": root_exists and root_database_path != active_database_path,
    }


def _backup_dir() -> Path:
    return Path(current_app.root_path).parent / "backups"


def _serialize_cards(cards: list[dict]) -> list[dict]:
    serializados = []
    for card in cards:
        valor = card.get("valor")
        if isinstance(valor, Decimal):
            valor = f"R$ {_decimal_texto(valor)}"
        serializados.append(
            {
                "label": card.get("label"),
                "valor": valor,
            }
        )
    return serializados


def _issue(severity: str, code: str, message: str, **context) -> dict:
    payload = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if context:
        payload["context"] = context
    return payload


def _status_from_issues(issues: list[dict]) -> str:
    if not issues:
        return "ok"
    severidade_maxima = max(issues, key=lambda issue: SEVERITY_ORDER.get(issue["severity"], 0))
    return severidade_maxima["severity"]


def collect_health_snapshot(*, public: bool = True) -> dict:
    checks = []

    try:
        db.session.execute(text("SELECT 1"))
        checks.append({"component": "database", "status": "ok"})
    except Exception as exc:
        database_check = {"component": "database", "status": "error"}
        if not public:
            database_check["detail"] = str(exc)
        checks.append(database_check)

    instance_path = Path(current_app.instance_path)
    checks.append(
        {
            "component": "instance_path",
            "status": "ok" if instance_path.exists() else "warning",
        }
    )

    notification_dir = Path(
        str(
            current_app.config.get("NOTIFICATION_OUTBOX_DIR")
            or (instance_path / "notifications")
        )
    )
    try:
        notification_dir.mkdir(parents=True, exist_ok=True)
        checks.append({"component": "notifications", "status": "ok"})
    except Exception as exc:
        notification_check = {"component": "notifications", "status": "error"}
        if not public:
            notification_check["detail"] = str(exc)
        checks.append(notification_check)

    throttle_backend = str(current_app.config.get("REQUEST_THROTTLE_BACKEND") or "sqlite").strip().lower()
    throttle_check = {
        "component": "request_throttle",
        "status": "ok",
        "backend": throttle_backend,
    }
    if throttle_backend == "sqlite" and not public:
        throttle_path = Path(
            str(
                current_app.config.get("REQUEST_THROTTLE_STORAGE_PATH")
                or (instance_path / "request_throttle.sqlite3")
            )
        )
        throttle_check["storage_path"] = str(throttle_path)
        throttle_check["exists"] = throttle_path.exists()
        if throttle_path.exists():
            try:
                throttle_check["size_bytes"] = throttle_path.stat().st_size
            except OSError:
                throttle_check["size_bytes"] = None
        try:
            throttle_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            throttle_check["status"] = "error"
            throttle_check["detail"] = str(exc)
    checks.append(throttle_check)

    logging_enabled = bool(current_app.config.get("OBSERVABILITY_ENABLE_FILE_LOGGING", True))
    if logging_enabled:
        log_dir = Path(
            str(current_app.config.get("APP_LOG_DIR") or (instance_path / "logs"))
        )
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            checks.append({"component": "file_logging", "status": "ok"})
        except Exception as exc:
            logging_check = {"component": "file_logging", "status": "error"}
            if not public:
                logging_check["detail"] = str(exc)
            checks.append(logging_check)
    else:
        logging_check = {"component": "file_logging", "status": "info"}
        if not public:
            logging_check["detail"] = "disabled"
        checks.append(logging_check)

    overall_status = "ok"
    if any(check["status"] == "error" for check in checks):
        overall_status = "error"
    elif any(check["status"] == "warning" for check in checks):
        overall_status = "warning"

    payload = {
        "status": overall_status,
        "generated_at": _utc_iso_now(),
        "checks": checks,
    }
    if not public:
        payload["release_label"] = current_app.config.get("APP_RELEASE_LABEL", "")
    return payload


def _comparar_lotes_sem_irrf() -> list[dict]:
    itens_sem_irrf = [
        item
        for item in DativoItem.query.filter_by(ativo=True, grupo="sem_irrf").all()
    ]
    lotes = [lote for lote in DativoLote.query.filter_by(ativo=True).all()]
    soma_por_lote = defaultdict(
        lambda: {
            "quantidade_itens": 0,
            "valor_total_bruto": Decimal("0.00"),
            "valor_total_irrf": Decimal("0.00"),
            "valor_total_liquido": Decimal("0.00"),
        }
    )
    problemas = []

    for item in itens_sem_irrf:
        if not item.dativo_lote_id:
            problemas.append(
                _issue(
                    "error",
                    "dativo_item_sem_lote",
                    f"Item sem IRRF {item.id} esta ativo, mas sem vinculo com lote.",
                    item_id=item.id,
                    dativo_ci_id=item.dativo_ci_id,
                )
            )
            continue

        bucket = soma_por_lote[item.dativo_lote_id]
        bucket["quantidade_itens"] += 1
        bucket["valor_total_bruto"] += _decimal(item.valor_bruto)
        bucket["valor_total_irrf"] += _decimal(item.valor_irrf)
        bucket["valor_total_liquido"] += _decimal(item.valor_liquido)

    for lote in lotes:
        esperado = soma_por_lote.get(
            lote.id,
            {
                "quantidade_itens": 0,
                "valor_total_bruto": Decimal("0.00"),
                "valor_total_irrf": Decimal("0.00"),
                "valor_total_liquido": Decimal("0.00"),
            },
        )
        atual = {
            "quantidade_itens": lote.quantidade_itens,
            "valor_total_bruto": _decimal(lote.valor_total_bruto),
            "valor_total_irrf": _decimal(lote.valor_total_irrf),
            "valor_total_liquido": _decimal(lote.valor_total_liquido),
        }
        if atual != esperado:
            problemas.append(
                _issue(
                    "error",
                    "dativo_lote_total_divergente",
                    f"Lote sem IRRF {lote.id} divergente em totais.",
                    lote_id=lote.id,
                    atual={k: str(v) for k, v in atual.items()},
                    esperado={k: str(v) for k, v in esperado.items()},
                )
            )

        for item in [item for item in itens_sem_irrf if item.dativo_lote_id == lote.id]:
            if item.situacao_rpv_id != lote.situacao_rpv_id:
                problemas.append(
                    _issue(
                        "error",
                        "dativo_lote_status_principal_divergente",
                        f"Lote sem IRRF {lote.id} com item {item.id} em situacao principal divergente.",
                        lote_id=lote.id,
                        item_id=item.id,
                    )
                )
            if item.situacao_imposto_id != lote.situacao_imposto_id:
                problemas.append(
                    _issue(
                        "error",
                        "dativo_lote_status_fiscal_divergente",
                        f"Lote sem IRRF {lote.id} com item {item.id} em situacao fiscal divergente.",
                        lote_id=lote.id,
                        item_id=item.id,
                    )
                )
            if item.data_pagamento != lote.data_pagamento:
                problemas.append(
                    _issue(
                        "error",
                        "dativo_lote_data_pagamento_divergente",
                        f"Lote sem IRRF {lote.id} com item {item.id} em data de pagamento divergente.",
                        lote_id=lote.id,
                        item_id=item.id,
                    )
                )

    return problemas


def _coletar_duplicidades_dativos() -> list[dict]:
    chaves = Counter(
        (
            item.dativo_ci_id,
            item.grupo,
            item.cpf_normalizado,
            str(item.numero_processo or "").strip(),
        )
        for item in DativoItem.query.options(joinedload(DativoItem.situacao_rpv)).filter_by(ativo=True).all()
        if _registro_ativo_operacional(item)
    )
    problemas = []
    for chave, total in chaves.items():
        if total > 1:
            problemas.append(
                _issue(
                    "error",
                    "duplicidade_dativos",
                    "Duplicidade em dativos para a chave operacional auditada.",
                    dativo_ci_id=chave[0],
                    grupo=chave[1],
                    documento=chave[2],
                    numero_processo=chave[3],
                    quantidade=total,
                )
            )
    return problemas


def _coletar_duplicidades_rpvs() -> list[dict]:
    chaves = Counter(
        (
            registro.processo_id,
            registro.documento_normalizado,
            str(registro.nome_beneficiario_normalizado or "").strip(),
        )
        for registro in RegistroRPV.query.options(joinedload(RegistroRPV.situacao_empenho)).filter_by(ativo=True).all()
        if _registro_ativo_operacional(registro)
    )
    problemas = []
    for chave, total in chaves.items():
        if total > 1:
            problemas.append(
                _issue(
                    "warning",
                    "possivel_duplicidade_rpv",
                    "Possivel duplicidade em RPVs para a chave operacional auditada.",
                    processo_id=chave[0],
                    documento=chave[1],
                    nome=chave[2],
                    quantidade=total,
                )
            )
    return problemas


def _telefones_fora_do_padrao() -> list[dict]:
    problemas = []
    for usuario in User.query.order_by(User.id.asc()).all():
        if usuario.telefone and not telefone_brasileiro_valido(usuario.telefone):
            problemas.append(
                _issue(
                    "warning",
                    "telefone_usuario_invalido",
                    f"Usuario {usuario.login} possui telefone fora do padrao esperado.",
                    usuario_id=usuario.id,
                    login=usuario.login,
                    telefone=usuario.telefone,
                )
            )
    return problemas


def collect_operational_audit_report() -> dict:
    with current_app.test_request_context("/bi"):
        dataset = _coletar_dataset_bi()
        cards = _cards_bi(dataset)

    active_database_path = _sqlite_database_path()
    shadow_database_info = _sqlite_root_shadow_info(active_database_path)

    rpvs_ativos = [
        registro
        for registro in RegistroRPV.query.options(joinedload(RegistroRPV.situacao_empenho)).filter_by(ativo=True).all()
        if _registro_ativo_operacional(registro)
    ]
    itens_ativos = [
        item
        for item in DativoItem.query.options(joinedload(DativoItem.situacao_rpv)).filter_by(ativo=True).all()
        if _registro_ativo_operacional(item)
    ]
    lotes_ativos = [
        lote
        for lote in DativoLote.query.options(joinedload(DativoLote.situacao_rpv)).filter_by(ativo=True).all()
        if _registro_ativo_operacional(lote)
    ]

    total_bruto_db = sum(
        (_decimal(registro.valor_bruto) for registro in rpvs_ativos),
        Decimal("0.00"),
    ) + sum((_decimal(item.valor_bruto) for item in itens_ativos), Decimal("0.00"))
    total_irrf_db = sum(
        (_decimal(registro.valor_irrf) for registro in rpvs_ativos),
        Decimal("0.00"),
    ) + sum((_decimal(item.valor_irrf) for item in itens_ativos), Decimal("0.00"))
    total_liquido_db = sum(
        (_decimal(registro.valor_liquido) for registro in rpvs_ativos),
        Decimal("0.00"),
    ) + sum((_decimal(item.valor_liquido) for item in itens_ativos), Decimal("0.00"))

    total_bruto_bi = sum((row["valor_bruto"] for row in dataset), Decimal("0.00"))
    total_irrf_bi = sum((row["valor_irrf"] for row in dataset), Decimal("0.00"))
    total_liquido_bi = sum((row["valor_liquido"] for row in dataset), Decimal("0.00"))

    issues = []
    if len(dataset) != (len(rpvs_ativos) + len(itens_ativos)):
        issues.append(
            _issue(
                "error",
                "dataset_bi_quantidade_divergente",
                "Quantidade do dataset do BI diverge do banco.",
                dataset=len(dataset),
                esperado=len(rpvs_ativos) + len(itens_ativos),
            )
        )
    if total_bruto_bi != total_bruto_db:
        issues.append(
            _issue(
                "error",
                "dataset_bi_total_bruto_divergente",
                "Total bruto do BI diverge do banco.",
                bi=str(total_bruto_bi),
                banco=str(total_bruto_db),
            )
        )
    if total_irrf_bi != total_irrf_db:
        issues.append(
            _issue(
                "error",
                "dataset_bi_total_irrf_divergente",
                "Total de IRRF do BI diverge do banco.",
                bi=str(total_irrf_bi),
                banco=str(total_irrf_db),
            )
        )
    if total_liquido_bi != total_liquido_db:
        issues.append(
            _issue(
                "error",
                "dataset_bi_total_liquido_divergente",
                "Total liquido do BI diverge do banco.",
                bi=str(total_liquido_bi),
                banco=str(total_liquido_db),
            )
        )

    issues.extend(_comparar_lotes_sem_irrf())
    issues.extend(_coletar_duplicidades_dativos())
    issues.extend(_coletar_duplicidades_rpvs())
    issues.extend(_telefones_fora_do_padrao())
    if shadow_database_info and shadow_database_info["shadow_database_present"]:
        issues.append(
            _issue(
                "warning",
                "sqlite_root_shadow_database_present",
                "Arquivo SQLite na raiz do projeto detectado fora do banco ativo configurado.",
                root_database_path=shadow_database_info["root_database_path"],
                active_database_path=shadow_database_info["active_database_path"],
                root_database_size_bytes=shadow_database_info["root_database_size_bytes"],
                active_database_size_bytes=shadow_database_info["active_database_size_bytes"],
            )
        )

    issues.sort(key=lambda issue: (-SEVERITY_ORDER.get(issue["severity"], 0), issue["code"]))

    status = _status_from_issues(issues)

    return {
        "status": status,
        "generated_at": _utc_iso_now(),
        "database_path": str(active_database_path or ""),
        "database_exists": bool((active_database_path or Path()).exists()) if active_database_path else True,
        "shadow_database": shadow_database_info,
        "backup_dir": str(_backup_dir()),
        "backup_dir_exists": _backup_dir().exists(),
        "summary": {
            "usuarios_cadastrados": User.query.count(),
            "rpvs_ativos": len(rpvs_ativos),
            "itens_dativo_ativos": len(itens_ativos),
            "lotes_dativo_ativos": len(lotes_ativos),
            "linhas_dataset_bi": len(dataset),
            "origens_bi": dict(Counter(row["origem_chave"] for row in dataset)),
            "issues_por_severidade": dict(Counter(issue["severity"] for issue in issues)),
            "shadow_database_present": bool(shadow_database_info and shadow_database_info["shadow_database_present"]),
        },
        "financial_totals": {
            "banco": {
                "bruto": str(total_bruto_db),
                "irrf": str(total_irrf_db),
                "liquido": str(total_liquido_db),
                "bruto_formatado": f"R$ {_decimal_texto(total_bruto_db)}",
                "irrf_formatado": f"R$ {_decimal_texto(total_irrf_db)}",
                "liquido_formatado": f"R$ {_decimal_texto(total_liquido_db)}",
            },
            "bi": {
                "bruto": str(total_bruto_bi),
                "irrf": str(total_irrf_bi),
                "liquido": str(total_liquido_bi),
                "bruto_formatado": f"R$ {_decimal_texto(total_bruto_bi)}",
                "irrf_formatado": f"R$ {_decimal_texto(total_irrf_bi)}",
                "liquido_formatado": f"R$ {_decimal_texto(total_liquido_bi)}",
            },
        },
        "cards_bi": _serialize_cards(cards),
        "issues": issues,
    }
