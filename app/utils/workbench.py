from math import ceil

from unidecode import unidecode


QUEUE_HIDDEN_STATUS_NAMES = {"concluida", "cancelado", "cancelada"}


def parse_page(valor, padrao: int = 1) -> int:
    try:
        pagina = int(valor)
    except (TypeError, ValueError):
        return padrao

    return max(1, pagina)


def parse_page_size(valor, padrao: int = 20, minimo: int = 1, maximo: int = 100) -> int:
    try:
        quantidade = int(valor)
    except (TypeError, ValueError):
        return padrao

    if quantidade < minimo or quantidade > maximo:
        return padrao

    return quantidade


def sanitize_sort_direction(valor: str | None, padrao: str = "desc") -> str:
    direcao = str(valor or "").strip().lower()
    return direcao if direcao in {"asc", "desc"} else padrao


def normalize_queue_status_name(valor: str | None) -> str:
    return unidecode(str(valor or "").strip().lower())


def should_include_closed_in_queue(
    flag_value: str | None,
    explicit_status_id: str | int | None = None,
) -> bool:
    if str(explicit_status_id or "").strip():
        return True

    return normalize_queue_status_name(flag_value) in {"1", "true", "on", "sim", "yes"}


def collect_hidden_queue_status_ids(statuses: list) -> set[int]:
    return {
        status.id
        for status in statuses
        if normalize_queue_status_name(getattr(status, "nome", None))
        in QUEUE_HIDDEN_STATUS_NAMES
    }


def resolve_next_sort_direction(
    chave_atual: str,
    direcao_atual: str,
    chave_destino: str,
    padrao: str = "asc",
) -> str:
    if chave_atual == chave_destino:
        return "desc" if direcao_atual == "asc" else "asc"
    return padrao


def merge_query_params(parametros_atuais: dict | None = None, **updates) -> dict:
    merged = dict(parametros_atuais or {})

    for chave, valor in updates.items():
        if valor is None:
            merged.pop(chave, None)
            continue
        merged[chave] = valor

    return {chave: valor for chave, valor in merged.items() if valor not in (None, "")}


def build_pagination(total_itens: int, pagina: int, por_pagina: int) -> dict:
    total_itens = max(0, int(total_itens or 0))
    por_pagina = max(1, int(por_pagina or 1))
    total_paginas = max(1, ceil(total_itens / por_pagina)) if total_itens else 1
    pagina = min(max(1, int(pagina or 1)), total_paginas)

    if total_itens:
        inicio = ((pagina - 1) * por_pagina) + 1
        fim = min(total_itens, pagina * por_pagina)
    else:
        inicio = 0
        fim = 0

    return {
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total_itens": total_itens,
        "total_paginas": total_paginas,
        "inicio": inicio,
        "fim": fim,
        "tem_anterior": pagina > 1,
        "tem_proxima": pagina < total_paginas,
        "pagina_anterior": pagina - 1 if pagina > 1 else None,
        "proxima_pagina": pagina + 1 if pagina < total_paginas else None,
    }


def paginate_items(items: list, pagina: int, por_pagina: int) -> tuple[list, dict]:
    paginacao = build_pagination(len(items), pagina, por_pagina)
    inicio_indice = (paginacao["pagina"] - 1) * paginacao["por_pagina"]
    fim_indice = inicio_indice + paginacao["por_pagina"]
    return items[inicio_indice:fim_indice], paginacao


def build_page_window(total_paginas: int, pagina_atual: int, raio: int = 2) -> list[int]:
    total_paginas = max(1, int(total_paginas or 1))
    pagina_atual = min(max(1, int(pagina_atual or 1)), total_paginas)
    inicio = max(1, pagina_atual - raio)
    fim = min(total_paginas, pagina_atual + raio)
    return list(range(inicio, fim + 1))
