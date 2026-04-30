from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import request


def sanitize_internal_return_url(raw_url: str | None, default: str) -> str:
    retorno = str(raw_url or "").strip()
    if not retorno:
        return default

    partes = urlsplit(retorno)
    if partes.scheme or partes.netloc or not retorno.startswith("/") or retorno.startswith("//"):
        return default

    return retorno


def current_internal_url(default: str) -> str:
    full_path = request.full_path.rstrip("?")
    return sanitize_internal_return_url(full_path, default)


def append_internal_return_url(target_url: str | None, return_url: str | None) -> str:
    destino = sanitize_internal_return_url(target_url, "")
    if not destino:
        return str(target_url or "").strip()

    retorno = sanitize_internal_return_url(return_url, "")
    if not retorno:
        return destino

    partes = urlsplit(destino)
    query_params = [
        (chave, valor)
        for chave, valor in parse_qsl(partes.query, keep_blank_values=True)
        if chave != "retorno"
    ]
    query_params.append(("retorno", retorno))

    return urlunsplit(
        (
            "",
            "",
            partes.path,
            urlencode(query_params, doseq=True),
            partes.fragment,
        )
    )
