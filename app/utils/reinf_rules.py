from unidecode import unidecode


REINF_STATUS_NAO_ENVIADO = "Não enviado"
REINF_STATUS_CONCLUIDO = "Concluído"
REINF_STATUS_CANCELADO = "Cancelado"

REINF_STATUS_OPCOES = [
    REINF_STATUS_NAO_ENVIADO,
    REINF_STATUS_CONCLUIDO,
    REINF_STATUS_CANCELADO,
]

REINF_STATUS_FILTROS = [
    ("todos", "Todos"),
    (REINF_STATUS_NAO_ENVIADO, REINF_STATUS_NAO_ENVIADO),
    (REINF_STATUS_CONCLUIDO, REINF_STATUS_CONCLUIDO),
    (REINF_STATUS_CANCELADO, REINF_STATUS_CANCELADO),
]


def normalizar_reinf_status(valor: str | None) -> str:
    return unidecode(str(valor or "").strip()).lower()


def resolver_reinf_status(valor: str | None, *, default: str | None = None) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        return default

    for opcao in REINF_STATUS_OPCOES:
        if normalizar_reinf_status(texto) == normalizar_reinf_status(opcao):
            return opcao

    raise ValueError("Status REINF inválido.")


def reinf_status_eh_concluido(valor: str | None) -> bool:
    return normalizar_reinf_status(valor) == normalizar_reinf_status(REINF_STATUS_CONCLUIDO)


def reinf_status_eh_cancelado(valor: str | None) -> bool:
    return normalizar_reinf_status(valor) == normalizar_reinf_status(REINF_STATUS_CANCELADO)


def reinf_status_esta_resolvido(valor: str | None) -> bool:
    return reinf_status_eh_concluido(valor) or reinf_status_eh_cancelado(valor)
