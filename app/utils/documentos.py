from __future__ import annotations

from app.utils.formatters import detectar_tipo_documento
from app.utils.normalizers import normalizar_documento


def _cpf_valido(documento: str) -> bool:
    if len(documento) != 11 or len(set(documento)) == 1:
        return False

    soma = sum(int(documento[indice]) * (10 - indice) for indice in range(9))
    digito_1 = (soma * 10 % 11) % 10

    soma = sum(int(documento[indice]) * (11 - indice) for indice in range(10))
    digito_2 = (soma * 10 % 11) % 10

    return documento[-2:] == f"{digito_1}{digito_2}"


def _cnpj_valido(documento: str) -> bool:
    if len(documento) != 14 or len(set(documento)) == 1:
        return False

    pesos_primeiro = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos_segundo = [6] + pesos_primeiro

    soma = sum(int(digito) * peso for digito, peso in zip(documento[:12], pesos_primeiro))
    resto = soma % 11
    digito_1 = "0" if resto < 2 else str(11 - resto)

    soma = sum(
        int(digito) * peso
        for digito, peso in zip(documento[:12] + digito_1, pesos_segundo)
    )
    resto = soma % 11
    digito_2 = "0" if resto < 2 else str(11 - resto)

    return documento[-2:] == f"{digito_1}{digito_2}"


def validar_documento_brasileiro(
    documento: str | None,
    tipo_preferido: str | None = None,
) -> dict[str, str | bool]:
    documento_normalizado = normalizar_documento(documento)
    tipo_documento = detectar_tipo_documento(documento_normalizado, tipo_preferido)

    if not documento_normalizado:
        return {
            "valido": False,
            "tipo_documento": tipo_documento,
            "documento_normalizado": documento_normalizado,
            "motivo": "Documento ausente.",
        }

    if tipo_documento == "CPF":
        if len(documento_normalizado) != 11:
            return {
                "valido": False,
                "tipo_documento": tipo_documento,
                "documento_normalizado": documento_normalizado,
                "motivo": "CPF precisa ter 11 digitos.",
            }
        if len(set(documento_normalizado)) == 1:
            return {
                "valido": False,
                "tipo_documento": tipo_documento,
                "documento_normalizado": documento_normalizado,
                "motivo": "CPF com digitos repetidos nao e valido.",
            }
        if not _cpf_valido(documento_normalizado):
            return {
                "valido": False,
                "tipo_documento": tipo_documento,
                "documento_normalizado": documento_normalizado,
                "motivo": "CPF invalido pelos digitos informados.",
            }
        return {
            "valido": True,
            "tipo_documento": tipo_documento,
            "documento_normalizado": documento_normalizado,
            "motivo": "CPF validado automaticamente.",
        }

    if tipo_documento == "CNPJ":
        if len(documento_normalizado) != 14:
            return {
                "valido": False,
                "tipo_documento": tipo_documento,
                "documento_normalizado": documento_normalizado,
                "motivo": "CNPJ precisa ter 14 digitos.",
            }
        if len(set(documento_normalizado)) == 1:
            return {
                "valido": False,
                "tipo_documento": tipo_documento,
                "documento_normalizado": documento_normalizado,
                "motivo": "CNPJ com digitos repetidos nao e valido.",
            }
        if not _cnpj_valido(documento_normalizado):
            return {
                "valido": False,
                "tipo_documento": tipo_documento,
                "documento_normalizado": documento_normalizado,
                "motivo": "CNPJ invalido pelos digitos informados.",
            }
        return {
            "valido": True,
            "tipo_documento": tipo_documento,
            "documento_normalizado": documento_normalizado,
            "motivo": "CNPJ validado automaticamente.",
        }

    return {
        "valido": False,
        "tipo_documento": tipo_documento,
        "documento_normalizado": documento_normalizado,
        "motivo": "Documento nao corresponde a um CPF ou CNPJ reconhecido.",
    }
