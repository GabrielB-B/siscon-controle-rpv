from __future__ import annotations

from unidecode import unidecode


GRUPOS_COTA_META = {
    "pessoal": {
        "label": "Pessoal",
        "descricao": "RPV pessoal e trabalhista",
        "css_class": "stack-segment-strong",
        "chart_class": "chart-column-bar-secondary",
        "progress_class": "progress-bar-secondary",
    },
    "comum": {
        "label": "Comum",
        "descricao": "RPVs comuns e dativos",
        "css_class": "stack-segment-soft",
        "chart_class": "chart-column-bar",
        "progress_class": "progress-bar",
    },
    "pericial": {
        "label": "Pericial",
        "descricao": "RPVs periciais",
        "css_class": "stack-segment-amber",
        "chart_class": "chart-column-bar-amber",
        "progress_class": "progress-bar-amber",
    },
}

GRUPOS_COTA_ORDEM = ("pessoal", "comum", "pericial")

GRUPOS_COTA_OPCOES = {
    "todos": "Todos os grupos",
    "pessoal": "Pessoal",
    "comum": "Comum",
    "pericial": "Pericial",
}


def _normalizar_texto(valor: str | None) -> str:
    return unidecode(str(valor or "").strip()).lower()


def meta_grupo_cota(chave: str) -> dict:
    return GRUPOS_COTA_META.get(chave, GRUPOS_COTA_META["comum"])


def classificar_grupo_cota(tipo_nome: str | None, origem_chave: str | None = None) -> str:
    if origem_chave in {"dativo_com_irrf", "dativo_sem_irrf", "dativo_lote_sem_irrf"}:
        return "comum"

    tipo_normalizado = _normalizar_texto(tipo_nome)

    if "pessoal" in tipo_normalizado or "trabalhist" in tipo_normalizado:
        return "pessoal"

    if "pericial" in tipo_normalizado or "periciais" in tipo_normalizado:
        return "pericial"

    return "comum"
