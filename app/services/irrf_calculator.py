from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.utils.formatters import detectar_tipo_documento, moeda_br
from app.utils.irrf_rules import IRRFProgressiveBracket, get_irrf_rules


DECIMAL_TWO_PLACES = Decimal("0.01")
DECIMAL_HUNDRED = Decimal("100")
ZERO = Decimal("0.00")


def _round_money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(value).quantize(DECIMAL_TWO_PLACES, rounding=ROUND_HALF_UP)


def _coerce_decimal(value) -> Decimal | None:
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    texto = str(value).strip()
    if not texto:
        return None

    texto = texto.replace("R$", "").replace(" ", "")
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _resolve_calendar_year(competencia: str | int | None) -> int | None:
    if competencia is None:
        return None

    texto = str(competencia).strip()
    if not texto:
        return None

    if len(texto) >= 4 and texto[:4].isdigit():
        return int(texto[:4])

    return None


def _format_money(value: Decimal) -> str:
    return f"R$ {moeda_br(value)}"


def _select_bracket(base_calculo: Decimal, tabela: tuple[IRRFProgressiveBracket, ...]) -> IRRFProgressiveBracket:
    for faixa in tabela:
        if faixa.limite_superior is None or base_calculo <= faixa.limite_superior:
            return faixa
    return tabela[-1]


@dataclass(frozen=True)
class IRRFCalculationResult:
    aplicavel: bool
    motivo_nao_aplicavel: str | None
    resumo: str
    valor_irrf: Decimal
    valor_irrf_input: str
    valor_irrf_formatado: str
    deducao_utilizada: Decimal
    base_calculo: Decimal
    imposto_tabela: Decimal
    reducao: Decimal
    aliquota_efetiva: Decimal
    versao_regra: str
    memoria_calculo: list[str]
    detalhes: list[dict[str, str]]
    sugerir_sem_irrf: bool
    desconsiderado_limite_minimo: bool
    competencia_considerada: str
    tipo_documento_considerado: str
    faixa_aplicada: str | None = None

    def to_payload(self) -> dict:
        return {
            "aplicavel": self.aplicavel,
            "motivo_nao_aplicavel": self.motivo_nao_aplicavel,
            "resumo": self.resumo,
            "valor_irrf": str(self.valor_irrf),
            "valor_irrf_input": self.valor_irrf_input,
            "valor_irrf_formatado": self.valor_irrf_formatado,
            "deducao_utilizada": str(self.deducao_utilizada),
            "base_calculo": str(self.base_calculo),
            "imposto_tabela": str(self.imposto_tabela),
            "reducao": str(self.reducao),
            "aliquota_efetiva": str(self.aliquota_efetiva),
            "versao_regra": self.versao_regra,
            "memoria_calculo": self.memoria_calculo,
            "detalhes": self.detalhes,
            "sugerir_sem_irrf": self.sugerir_sem_irrf,
            "desconsiderado_limite_minimo": self.desconsiderado_limite_minimo,
            "competencia_considerada": self.competencia_considerada,
            "tipo_documento_considerado": self.tipo_documento_considerado,
            "faixa_aplicada": self.faixa_aplicada,
        }


def _build_non_applicable_result(
    *,
    motivo: str,
    competencia_considerada: str,
    tipo_documento_considerado: str,
) -> IRRFCalculationResult:
    return IRRFCalculationResult(
        aplicavel=False,
        motivo_nao_aplicavel=motivo,
        resumo=motivo,
        valor_irrf=ZERO,
        valor_irrf_input="0,00",
        valor_irrf_formatado=_format_money(ZERO),
        deducao_utilizada=ZERO,
        base_calculo=ZERO,
        imposto_tabela=ZERO,
        reducao=ZERO,
        aliquota_efetiva=ZERO,
        versao_regra="",
        memoria_calculo=[],
        detalhes=[],
        sugerir_sem_irrf=False,
        desconsiderado_limite_minimo=False,
        competencia_considerada=competencia_considerada,
        tipo_documento_considerado=tipo_documento_considerado,
        faixa_aplicada=None,
    )


def calcular_irrf_operacional(
    *,
    competencia: str | int | None,
    valor_bruto_tributavel,
    documento: str | None,
    tipo_documento: str | None = None,
    sem_irrf_forcado: bool = False,
) -> IRRFCalculationResult:
    competencia_considerada = str(competencia or "").strip()
    ano_calendario = _resolve_calendar_year(competencia_considerada)
    tipo_documento_considerado = detectar_tipo_documento(documento, tipo_documento)

    if sem_irrf_forcado:
        return _build_non_applicable_result(
            motivo="O registro esta marcado como sem IRRF. Desmarque essa opcao se quiser calcular a sugestao.",
            competencia_considerada=competencia_considerada,
            tipo_documento_considerado=tipo_documento_considerado,
        )

    if ano_calendario is None:
        return _build_non_applicable_result(
            motivo="Informe a competencia do calculo no formato mes/ano para gerar a sugestao de IRRF.",
            competencia_considerada=competencia_considerada,
            tipo_documento_considerado=tipo_documento_considerado,
        )

    try:
        regras = get_irrf_rules(ano_calendario)
    except ValueError as exc:
        return _build_non_applicable_result(
            motivo=str(exc),
            competencia_considerada=competencia_considerada,
            tipo_documento_considerado=tipo_documento_considerado,
        )

    if tipo_documento_considerado != "CPF":
        return _build_non_applicable_result(
            motivo="O calculo assistido desta fase esta disponivel apenas para CPF. CNPJ continua fora do calculo automatico.",
            competencia_considerada=competencia_considerada,
            tipo_documento_considerado=tipo_documento_considerado,
        )

    valor_bruto = _coerce_decimal(valor_bruto_tributavel)
    if valor_bruto is None:
        return _build_non_applicable_result(
            motivo="Informe um valor bruto valido para calcular o IRRF sugerido.",
            competencia_considerada=competencia_considerada,
            tipo_documento_considerado=tipo_documento_considerado,
        )

    valor_bruto = _round_money(valor_bruto)
    if valor_bruto <= ZERO:
        resumo = "O valor bruto informado nao gera base tributavel. A sugestao operacional de IRRF ficou em R$ 0,00."
        return IRRFCalculationResult(
            aplicavel=True,
            motivo_nao_aplicavel=None,
            resumo=resumo,
            valor_irrf=ZERO,
            valor_irrf_input="0,00",
            valor_irrf_formatado=_format_money(ZERO),
            deducao_utilizada=regras.desconto_simplificado_mensal,
            base_calculo=ZERO,
            imposto_tabela=ZERO,
            reducao=ZERO,
            aliquota_efetiva=ZERO,
            versao_regra=regras.versao_regra,
            memoria_calculo=[
                f"Competencia considerada: {competencia_considerada or ano_calendario}.",
                f"Valor bruto sem base tributavel: {_format_money(valor_bruto)}.",
            ],
            detalhes=[
                {"label": "Valor bruto", "valor": _format_money(valor_bruto)},
                {"label": "IRRF sugerido", "valor": _format_money(ZERO)},
            ],
            sugerir_sem_irrf=True,
            desconsiderado_limite_minimo=False,
            competencia_considerada=competencia_considerada,
            tipo_documento_considerado=tipo_documento_considerado,
            faixa_aplicada=None,
        )

    deducao_utilizada = _round_money(regras.desconto_simplificado_mensal)
    base_calculo = _round_money(max(ZERO, valor_bruto - deducao_utilizada))
    faixa = _select_bracket(base_calculo, regras.tabela_progressiva)

    if faixa.aliquota == ZERO:
        imposto_tabela = ZERO
    else:
        imposto_tabela = _round_money((base_calculo * faixa.aliquota) - faixa.deducao)
        if imposto_tabela < ZERO:
            imposto_tabela = ZERO

    if valor_bruto <= regras.reducao_faixa_1_limite:
        reducao_bruta = regras.reducao_faixa_1_valor
    elif valor_bruto <= regras.reducao_faixa_2_limite:
        reducao_bruta = regras.reducao_faixa_2_constante - (
            regras.reducao_faixa_2_coeficiente * valor_bruto
        )
    else:
        reducao_bruta = ZERO

    reducao = _round_money(max(ZERO, min(imposto_tabela, reducao_bruta)))
    valor_irrf = _round_money(max(ZERO, imposto_tabela - reducao))

    desconsiderado_limite_minimo = ZERO < valor_irrf <= regras.limite_retencao_minima
    valor_irrf_considerado = ZERO if desconsiderado_limite_minimo else valor_irrf
    aliquota_efetiva = _round_money(
        (valor_irrf_considerado / valor_bruto) * DECIMAL_HUNDRED
    ) if valor_bruto > ZERO else ZERO

    memoria_calculo = [
        f"Competencia considerada: {competencia_considerada or ano_calendario}.",
        f"Documento considerado: {tipo_documento_considerado}.",
        f"Desconto simplificado mensal fixo: {_format_money(deducao_utilizada)}.",
        f"Base de calculo: {_format_money(valor_bruto)} - {_format_money(deducao_utilizada)} = {_format_money(base_calculo)}.",
        f"Faixa aplicada na tabela progressiva: {faixa.descricao}.",
        f"Imposto pela tabela mensal: {_format_money(imposto_tabela)}.",
        f"Reducao legal mensal aplicada sobre o rendimento bruto: {_format_money(reducao)}.",
    ]

    if desconsiderado_limite_minimo:
        memoria_calculo.append(
            "O resultado ficou em ate R$ 10,00, entao a retencao foi desconsiderada pela regra operacional do setor."
        )

    detalhes = [
        {"label": "Valor bruto", "valor": _format_money(valor_bruto)},
        {"label": "Base de calculo", "valor": _format_money(base_calculo)},
        {"label": "Imposto tabela", "valor": _format_money(imposto_tabela)},
        {"label": "Reducao", "valor": _format_money(reducao)},
        {"label": "IRRF sugerido", "valor": _format_money(valor_irrf_considerado)},
    ]

    if valor_irrf_considerado == ZERO:
        resumo = (
            "O calculo operacional nao indicou retencao efetiva de IRRF. "
            "Se essa leitura estiver correta, confirme o registro como sem IRRF antes de salvar."
        )
    else:
        resumo = (
            f"IRRF sugerido em {_format_money(valor_irrf_considerado)} pelo modelo operacional "
            f"simplificado de {ano_calendario}."
        )

    return IRRFCalculationResult(
        aplicavel=True,
        motivo_nao_aplicavel=None,
        resumo=resumo,
        valor_irrf=valor_irrf_considerado,
        valor_irrf_input=moeda_br(valor_irrf_considerado),
        valor_irrf_formatado=_format_money(valor_irrf_considerado),
        deducao_utilizada=deducao_utilizada,
        base_calculo=base_calculo,
        imposto_tabela=imposto_tabela,
        reducao=reducao,
        aliquota_efetiva=aliquota_efetiva,
        versao_regra=regras.versao_regra,
        memoria_calculo=memoria_calculo,
        detalhes=detalhes,
        sugerir_sem_irrf=valor_irrf_considerado == ZERO,
        desconsiderado_limite_minimo=desconsiderado_limite_minimo,
        competencia_considerada=competencia_considerada,
        tipo_documento_considerado=tipo_documento_considerado,
        faixa_aplicada=faixa.descricao,
    )
