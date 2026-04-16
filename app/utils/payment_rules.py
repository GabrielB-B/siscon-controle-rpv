from datetime import date

from unidecode import unidecode

from app.extensions import db


def normalizar_nome_status(nome_status: str | None) -> str:
    return unidecode(str(nome_status or "").strip()).upper()


def nome_status_eh_pago(nome_status: str | None) -> bool:
    return normalizar_nome_status(nome_status) == "PAGO"


def nome_status_quita_pagamento_principal(nome_status: str | None) -> bool:
    return normalizar_nome_status(nome_status) in {"PAGO", "CONCLUIDA"}


def nome_status_eh_cancelado(nome_status: str | None) -> bool:
    return normalizar_nome_status(nome_status) == "CANCELADO"


def situacao_id_eh_pago(modelo, situacao_id) -> bool:
    if not situacao_id:
        return False

    try:
        situacao = db.session.get(modelo, int(situacao_id))
    except (TypeError, ValueError):
        return False

    return nome_status_eh_pago(getattr(situacao, "nome", None))


def situacao_id_quita_pagamento_principal(modelo, situacao_id) -> bool:
    if not situacao_id:
        return False

    try:
        situacao = db.session.get(modelo, int(situacao_id))
    except (TypeError, ValueError):
        return False

    return nome_status_quita_pagamento_principal(getattr(situacao, "nome", None))


def situacao_id_eh_cancelado(modelo, situacao_id) -> bool:
    if not situacao_id:
        return False

    try:
        situacao = db.session.get(modelo, int(situacao_id))
    except (TypeError, ValueError):
        return False

    return nome_status_eh_cancelado(getattr(situacao, "nome", None))


def competencia_para_data_base(competencia: str | None):
    valor = str(competencia or "").strip()
    if len(valor) != 7 or "-" not in valor:
        return None

    ano, mes = valor.split("-", 1)

    if not (ano.isdigit() and mes.isdigit()):
        return None

    try:
        return date(int(ano), int(mes), 1)
    except ValueError:
        return None


def competencia_pagamento_automatica(referencia: date | None = None) -> str:
    data_referencia = referencia or date.today()
    return data_referencia.strftime("%Y-%m")


def data_pagamento_manual_exige_confirmacao(
    data_atual,
    valor_informado: str | None,
    *,
    parser=None,
) -> bool:
    valor_limpo = str(valor_informado or "").strip()
    if not valor_limpo:
        return False

    try:
        nova_data = parser(valor_limpo) if parser else valor_limpo
    except (TypeError, ValueError):
        return False

    return nova_data != data_atual


def resolver_data_pagamento_por_status(
    *,
    data_atual,
    status_pago: bool,
    status_cancelado: bool = False,
    valor_informado: str | None = None,
    parser=None,
    competencia: str | None = None,
):
    data_competencia = competencia_para_data_base(competencia)

    if status_cancelado:
        return None

    if valor_informado is not None:
        valor_limpo = str(valor_informado or "").strip()

        if valor_limpo:
            return parser(valor_limpo) if parser else valor_limpo

        if status_pago:
            return data_atual or data_competencia or date.today()

        return None

    if status_pago and not data_atual:
        return data_competencia or date.today()

    return data_atual
