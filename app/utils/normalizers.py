import re
from unidecode import unidecode


def normalizar_nome(nome: str | None) -> str:
    if not nome:
        return ""
    nome = unidecode(nome)
    nome = nome.upper().strip()
    nome = re.sub(r"\s+", " ", nome)
    return nome


def normalizar_documento(documento: str | None) -> str:
    if not documento:
        return ""
    return re.sub(r"\D", "", documento)


def normalizar_telefone(telefone: str | None) -> str:
    if not telefone:
        return ""

    telefone_limpo = re.sub(r"\D", "", str(telefone))

    if telefone_limpo.startswith("55") and len(telefone_limpo) in {12, 13}:
        telefone_limpo = telefone_limpo[2:]

    return telefone_limpo


def telefone_brasileiro_valido(telefone: str | None) -> bool:
    telefone_limpo = normalizar_telefone(telefone)
    return len(telefone_limpo) in {10, 11}


def formatar_telefone_br(telefone: str | None) -> str:
    telefone_limpo = normalizar_telefone(telefone)

    if len(telefone_limpo) == 11:
        return f"({telefone_limpo[:2]}) {telefone_limpo[2:7]}-{telefone_limpo[7:]}"

    if len(telefone_limpo) == 10:
        return f"({telefone_limpo[:2]}) {telefone_limpo[2:6]}-{telefone_limpo[6:]}"

    return str(telefone or "").strip()


def normalizar_numero_processo(valor: str) -> str:
    """
    Normaliza número de processo para comparação transversal.

    Estratégia:
    - remove espaços
    - remove caracteres não numéricos
    - mantém só os dígitos para comparação

    Isso evita falhas de detecção entre módulos diferentes.
    """
    texto = unidecode(str(valor or "").strip()).upper()
    apenas_digitos = re.sub(r"\D", "", texto)

    if apenas_digitos:
        return apenas_digitos

    return re.sub(r"[^A-Z0-9]", "", texto)
