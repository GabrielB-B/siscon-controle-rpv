from datetime import datetime
from pathlib import Path
from shutil import copy2

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import DativoCI, DativoItem, DativoLote, Processo, RegistroRPV, User


def _backup_sqlite(app) -> Path | None:
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).strip()
    if not database_uri.startswith("sqlite:///"):
        return None

    caminho_banco = Path(db.engine.url.database or "").resolve()
    if not caminho_banco.exists():
        return None

    pasta_backup = Path(app.root_path).parent / "backups"
    pasta_backup.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = pasta_backup / f"controle_rpv_backup_antes_limpeza_{timestamp}.db"
    copy2(caminho_banco, destino)
    return destino


def _resetar_sequencias_sqlite():
    tabelas = {
        row[0]
        for row in db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
        )
    }
    if "sqlite_sequence" not in tabelas:
        return

    nomes = [
        "processos",
        "registros_rpv",
        "dativos_ci",
        "dativos_lotes",
        "dativos_itens",
        "usuarios",
    ]
    for nome in nomes:
        db.session.execute(text("DELETE FROM sqlite_sequence WHERE name = :nome"), {"nome": nome})


def main():
    app = create_app()

    with app.app_context():
        backup = _backup_sqlite(app)

        db.session.query(DativoItem).delete(synchronize_session=False)
        db.session.query(DativoLote).delete(synchronize_session=False)
        db.session.query(DativoCI).delete(synchronize_session=False)
        db.session.query(RegistroRPV).delete(synchronize_session=False)
        db.session.query(Processo).delete(synchronize_session=False)
        db.session.query(User).filter(User.is_admin.is_(False)).delete(synchronize_session=False)

        if str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).startswith("sqlite:///"):
            _resetar_sequencias_sqlite()

        db.session.commit()

        print("Base operacional limpa com sucesso.")
        if backup:
            print(f"Backup salvo em: {backup}")
        else:
            print("Nenhum backup SQLite foi gerado.")


if __name__ == "__main__":
    main()
