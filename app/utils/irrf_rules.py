from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IRRFProgressiveBracket:
    limite_superior: Decimal | None
    aliquota: Decimal
    deducao: Decimal
    descricao: str


@dataclass(frozen=True)
class IRRFRuleSet:
    ano_calendario: int
    versao_regra: str
    desconto_simplificado_mensal: Decimal
    limite_retencao_minima: Decimal
    reducao_faixa_1_limite: Decimal
    reducao_faixa_1_valor: Decimal
    reducao_faixa_2_limite: Decimal
    reducao_faixa_2_constante: Decimal
    reducao_faixa_2_coeficiente: Decimal
    tabela_progressiva: tuple[IRRFProgressiveBracket, ...]


IRRF_RULES_2026 = IRRFRuleSet(
    ano_calendario=2026,
    versao_regra="operacional_simplificado_2026_v1",
    desconto_simplificado_mensal=Decimal("607.20"),
    limite_retencao_minima=Decimal("10.00"),
    reducao_faixa_1_limite=Decimal("5000.00"),
    reducao_faixa_1_valor=Decimal("312.89"),
    reducao_faixa_2_limite=Decimal("7350.00"),
    reducao_faixa_2_constante=Decimal("978.62"),
    reducao_faixa_2_coeficiente=Decimal("0.133145"),
    tabela_progressiva=(
        IRRFProgressiveBracket(
            limite_superior=Decimal("2428.80"),
            aliquota=Decimal("0.00"),
            deducao=Decimal("0.00"),
            descricao="Faixa isenta ate R$ 2.428,80",
        ),
        IRRFProgressiveBracket(
            limite_superior=Decimal("2826.65"),
            aliquota=Decimal("0.075"),
            deducao=Decimal("182.16"),
            descricao="Faixa de 7,5% com deducao de R$ 182,16",
        ),
        IRRFProgressiveBracket(
            limite_superior=Decimal("3751.05"),
            aliquota=Decimal("0.15"),
            deducao=Decimal("394.16"),
            descricao="Faixa de 15,0% com deducao de R$ 394,16",
        ),
        IRRFProgressiveBracket(
            limite_superior=Decimal("4664.68"),
            aliquota=Decimal("0.225"),
            deducao=Decimal("675.49"),
            descricao="Faixa de 22,5% com deducao de R$ 675,49",
        ),
        IRRFProgressiveBracket(
            limite_superior=None,
            aliquota=Decimal("0.275"),
            deducao=Decimal("908.73"),
            descricao="Faixa de 27,5% com deducao de R$ 908,73",
        ),
    ),
)


IRRF_RULES_BY_YEAR = {
    2026: IRRF_RULES_2026,
}


def get_available_irrf_years() -> tuple[int, ...]:
    return tuple(sorted(IRRF_RULES_BY_YEAR))


def get_irrf_rules(ano_calendario: int) -> IRRFRuleSet:
    regras = IRRF_RULES_BY_YEAR.get(int(ano_calendario))
    if regras is not None:
        return regras

    anos_disponiveis = ", ".join(str(ano) for ano in get_available_irrf_years())

    raise ValueError(
        "Nao existe tabela operacional cadastrada para esse ano. "
        f"Anos disponiveis hoje: {anos_disponiveis}."
    )
