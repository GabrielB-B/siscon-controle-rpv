from decimal import Decimal, InvalidOperation

from app.utils.normalizers import normalizar_documento


def moeda_br(valor) -> str:
    if valor is None or valor == "":
        valor = Decimal("0.00")

    try:
        valor = Decimal(valor)
    except (InvalidOperation, ValueError, TypeError):
        return str(valor)

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return texto


def detectar_tipo_documento(documento: str | None, tipo_preferido: str | None = None) -> str:
    documento_limpo = normalizar_documento(documento)

    if len(documento_limpo) == 11:
        return "CPF"

    if len(documento_limpo) == 14:
        return "CNPJ"

    tipo = str(tipo_preferido or "").strip().upper()
    if tipo in {"CPF", "CNPJ"}:
        return tipo

    return "Documento"


def formatar_documento_br(documento: str | None, tipo_documento: str | None = None) -> str:
    documento_limpo = normalizar_documento(documento)
    tipo_efetivo = detectar_tipo_documento(documento_limpo, tipo_documento)

    if tipo_efetivo == "CPF" and len(documento_limpo) == 11:
        return (
            f"{documento_limpo[:3]}.{documento_limpo[3:6]}."
            f"{documento_limpo[6:9]}-{documento_limpo[9:]}"
        )

    if tipo_efetivo == "CNPJ" and len(documento_limpo) == 14:
        return (
            f"{documento_limpo[:2]}.{documento_limpo[2:5]}."
            f"{documento_limpo[5:8]}/{documento_limpo[8:12]}-"
            f"{documento_limpo[12:]}"
        )

    if documento_limpo:
        return documento_limpo

    return str(documento or "").strip()
