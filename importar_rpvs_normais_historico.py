from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analisa e importa RPVs normais historicos a partir de planilha Excel."
    )
    parser.add_argument(
        "--source-xlsx",
        required=True,
        help="Caminho da planilha Excel original.",
    )
    parser.add_argument(
        "--db-path",
        default="instance/controle_rpv.db",
        help="Banco SQLite alvo.",
    )
    parser.add_argument(
        "--output-dir",
        default="saida/rpvs_normais",
        help="Pasta base para os relatorios da execucao.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Executa a importacao dos registros aptos no banco alvo.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source_path = Path(args.source_xlsx).resolve()
    db_path = Path(args.db_path).resolve()
    output_root = Path(args.output_dir).resolve()

    if not source_path.exists():
        print(f"Planilha nao encontrada: {source_path}")
        return 1

    if not db_path.exists():
        print(f"Banco alvo nao encontrado: {db_path}")
        return 1

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    from app import create_app
    from app.services.rpv_import_service import (
        aplicar_bloqueios_banco,
        aplicar_bloqueios_duplicidade,
        carregar_planilha_rpvs_normais,
        coletar_processos_existentes,
        escrever_relatorios_saida,
        importar_linhas,
        marcar_conciliacoes_banco,
        reconciliar_registros_existentes,
    )

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    modo = "execucao" if args.execute else "analise"
    output_dir = output_root / f"importacao_{modo}_{stamp}"

    app = create_app()
    with app.app_context():
        linhas = carregar_planilha_rpvs_normais(source_path)
        aplicar_bloqueios_duplicidade(linhas)
        aplicar_bloqueios_banco(
            linhas,
            chaves_existentes=coletar_processos_existentes(),
        )
        marcar_conciliacoes_banco(linhas)

        import_stats = None
        if args.execute:
            import_stats = importar_linhas(linhas)
            conciliacao_stats = reconciliar_registros_existentes(linhas)
            import_stats = {
                "importados": import_stats.get("importados", 0),
                "conciliados": conciliacao_stats.get("conciliados", 0),
                "erros": import_stats.get("erros", 0) + conciliacao_stats.get("erros", 0),
            }

        report_path = escrever_relatorios_saida(
            linhas=linhas,
            output_dir=output_dir,
            source_path=source_path,
            db_path=db_path,
            import_stats=import_stats,
        )

    print(f"Relatorios gerados em: {output_dir}")
    print(f"Resumo: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
