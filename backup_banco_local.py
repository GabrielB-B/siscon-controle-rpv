from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def _slug(texto: str | None) -> str:
    if not texto:
        return ""
    permitido = []
    for caractere in str(texto).strip().lower():
        if caractere.isalnum():
            permitido.append(caractere)
        elif caractere in {" ", "-", "_"}:
            permitido.append("_")
    return "".join(permitido).strip("_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera backup consistente do banco SQLite local usando a API de backup do sqlite3."
    )
    parser.add_argument(
        "--db-path",
        default="instance/controle_rpv.db",
        help="Caminho do banco SQLite atual.",
    )
    parser.add_argument(
        "--backup-dir",
        default="backups",
        help="Pasta onde o backup sera salvo.",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Rotulo opcional para acrescentar ao nome do arquivo.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path).resolve()
    backup_dir = Path(args.backup_dir).resolve()

    if not db_path.exists():
        print(f"Banco nao encontrado: {db_path}")
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    label = _slug(args.label)
    suffix = f"_{label}" if label else ""
    backup_path = backup_dir / f"controle_rpv_{timestamp}{suffix}.db"

    uri = f"file:{quote(str(db_path).replace(chr(92), '/'))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as origem:
        with sqlite3.connect(str(backup_path)) as destino:
            origem.backup(destino)

    checksum = _sha256(backup_path)
    checksum_path = backup_path.with_suffix(f"{backup_path.suffix}.sha256")
    checksum_path.write_text(f"{checksum}  {backup_path.name}\n", encoding="utf-8")

    print("Backup concluido com sucesso.")
    print(f"Origem: {db_path}")
    print(f"Destino: {backup_path}")
    print(f"Checksum SHA256: {checksum}")
    print(f"Arquivo checksum: {checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
