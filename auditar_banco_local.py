from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import create_app
from app.services.operational_health_service import collect_operational_audit_report


SEVERITY_ORDER = {
    "ok": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa auditoria preventiva local do banco e do BI.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="Caminho opcional para salvar o relatorio completo em JSON.",
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "warning", "error"),
        default="warning",
        help="Define a severidade minima que faz o processo terminar com codigo 1.",
    )
    return parser.parse_args()


def _write_json_output(path_str: str, payload: dict) -> Path:
    path = Path(path_str).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return path


def _print_auditoria(payload: dict) -> None:
    print("=== AUDITORIA LOCAL DO BANCO E BI ===")
    print(f"Gerado em: {payload['generated_at']}")
    print(f"Status geral: {payload['status'].upper()}")
    print(f"Banco atual: {payload['database_path'] or 'N/A'}")
    print(f"Banco existe: {'SIM' if payload['database_exists'] else 'NAO'}")
    shadow_db = payload.get("shadow_database") or {}
    if shadow_db:
        print(
            "Banco sombra na raiz: "
            + ("SIM" if shadow_db.get("shadow_database_present") else "NAO")
        )
        print(f"Arquivo raiz monitorado: {shadow_db.get('root_database_path') or 'N/A'}")
    print(f"Pasta de backups: {payload['backup_dir']}")
    print(f"Pasta de backups existe: {'SIM' if payload['backup_dir_exists'] else 'NAO'}")
    print()

    resumo = payload["summary"]
    print("Resumo operacional")
    print(f"- Usuarios cadastrados: {resumo['usuarios_cadastrados']}")
    print(f"- RPVs ativos: {resumo['rpvs_ativos']}")
    print(f"- Itens de dativo ativos: {resumo['itens_dativo_ativos']}")
    print(f"- Lotes de dativo ativos: {resumo['lotes_dativo_ativos']}")
    print(f"- Linhas do dataset do BI: {resumo['linhas_dataset_bi']}")
    print(f"- Origens do BI: {resumo['origens_bi']}")
    print(f"- Achados por severidade: {resumo['issues_por_severidade']}")
    print(f"- Banco sombra presente: {'SIM' if resumo.get('shadow_database_present') else 'NAO'}")
    print()

    totais = payload["financial_totals"]
    print("Totais financeiros")
    print(f"- Bruto no banco: {totais['banco']['bruto_formatado']}")
    print(f"- IRRF no banco: {totais['banco']['irrf_formatado']}")
    print(f"- Liquido no banco: {totais['banco']['liquido_formatado']}")
    print(f"- Bruto no BI: {totais['bi']['bruto_formatado']}")
    print(f"- IRRF no BI: {totais['bi']['irrf_formatado']}")
    print(f"- Liquido no BI: {totais['bi']['liquido_formatado']}")
    print()

    print("Cards atuais do BI")
    for card in payload["cards_bi"]:
        print(f"- {card['label']}: {card['valor']}")
    print()

    issues = payload["issues"]
    if not issues:
        print("Status")
        print("- Auditoria concluida sem divergencias criticas.")
        print("- Totais do BI conferem com a soma direta do banco.")
        print("- Lotes sem IRRF conferem com os itens associados.")
        print("- Nao foram encontradas duplicidades operacionais nas chaves auditadas.")
        return

    print("Achados")
    for severity in ("error", "warning", "info"):
        issues_group = [issue for issue in issues if issue["severity"] == severity]
        if not issues_group:
            continue
        print(f"- {severity.upper()}: {len(issues_group)} item(ns)")
        for issue in issues_group:
            contexto = issue.get("context") or {}
            if contexto:
                print(f"  - [{issue['code']}] {issue['message']} | contexto={contexto}")
            else:
                print(f"  - [{issue['code']}] {issue['message']}")


def _should_fail(payload: dict, fail_on: str) -> bool:
    if fail_on == "none":
        return False

    threshold = SEVERITY_ORDER[fail_on]
    highest = SEVERITY_ORDER.get(payload["status"], 0)
    return highest >= threshold


def main() -> int:
    args = _parse_args()
    app = create_app()

    with app.app_context():
        payload = collect_operational_audit_report()

    _print_auditoria(payload)

    if args.json_output:
        output_path = _write_json_output(args.json_output, payload)
        print()
        print(f"Relatorio JSON salvo em: {output_path}")

    return 1 if _should_fail(payload, args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
