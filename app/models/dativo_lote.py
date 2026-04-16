from decimal import Decimal

from app.extensions import db
from app.utils.datetime_utils import utc_now_naive
from app.utils.payment_rules import nome_status_eh_cancelado


MESES_ABREV = {
    "01": "jan",
    "02": "fev",
    "03": "mar",
    "04": "abr",
    "05": "mai",
    "06": "jun",
    "07": "jul",
    "08": "ago",
    "09": "set",
    "10": "out",
    "11": "nov",
    "12": "dez",
}


class DativoLote(db.Model):
    """
    Lote resumido do dativo.

    Neste momento ele representa o grupo SEM IRRF da C.I.
    O andamento operacional principal ocorre no nível do lote.
    """

    __tablename__ = "dativos_lotes"

    id = db.Column(db.Integer, primary_key=True)

    dativo_ci_id = db.Column(
        db.Integer,
        db.ForeignKey("dativos_ci.id"),
        nullable=False,
        index=True,
    )

    tipo_lote = db.Column(db.String(20), nullable=False, default="sem_irrf")

    quantidade_itens = db.Column(db.Integer, nullable=False, default=0)

    valor_total_bruto = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    valor_total_irrf = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    valor_total_liquido = db.Column(db.Numeric(14, 2), nullable=False, default=0)

    data_pagamento = db.Column(db.Date, nullable=True)
    nota_empenho = db.Column(db.String(50), nullable=True)
    numero_se = db.Column(db.String(50), nullable=True)
    ordem_bancaria = db.Column(db.String(50), nullable=True)

    situacao_rpv_id = db.Column(
        db.Integer,
        db.ForeignKey("situacoes_empenho.id"),
        nullable=False,
    )
    situacao_imposto_id = db.Column(
        db.Integer,
        db.ForeignKey("situacoes_imposto.id"),
        nullable=False,
    )

    resumo_operacional = db.Column(db.String(500), nullable=False, default="")
    observacoes = db.Column(db.Text, nullable=True)

    ativo = db.Column(db.Boolean, nullable=False, default=True)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    dativo_ci = db.relationship("DativoCI", back_populates="lotes")
    itens = db.relationship("DativoItem", back_populates="dativo_lote", lazy=True)

    situacao_rpv = db.relationship("SituacaoEmpenho", foreign_keys=[situacao_rpv_id])
    situacao_imposto = db.relationship("SituacaoImposto", foreign_keys=[situacao_imposto_id])

    criado_por = db.relationship("User", foreign_keys=[criado_por_id])
    atualizado_por = db.relationship("User", foreign_keys=[atualizado_por_id])

    @property
    def responsavel_id(self) -> int | None:
        return getattr(getattr(self, "dativo_ci", None), "responsavel_id", None)

    @property
    def responsavel(self):
        return getattr(getattr(self, "dativo_ci", None), "responsavel", None)

    @property
    def data_pagamento_input_value(self) -> str:
        data_pagamento = getattr(self, "data_pagamento", None)
        return data_pagamento.strftime("%Y-%m-%d") if data_pagamento else ""

    @property
    def competencia_operacional(self) -> str:
        data_pagamento = getattr(self, "data_pagamento", None)

        if data_pagamento:
            return data_pagamento.strftime("%Y-%m")
        if self.dativo_ci and self.dativo_ci.exercicio:
            return self.dativo_ci.exercicio
        return ""

    @property
    def competencia_operacional_formatada(self) -> str:
        valor = str(self.competencia_operacional or "").strip()

        if len(valor) == 7 and "-" in valor:
            ano, mes = valor.split("-")
            mes_abrev = MESES_ABREV.get(mes, mes)
            return f"{mes_abrev}/{ano}"

        return valor

    @property
    def reinf_status_legivel(self) -> str:
        return getattr(self, "reinf_status", None) or "Não enviado"

    @property
    def status_principal_cancelado(self) -> bool:
        nome_situacao = getattr(getattr(self, "situacao_rpv", None), "nome", None)
        return nome_status_eh_cancelado(nome_situacao)

    def atualizar_totais(self):
        """
        Recalcula os totais do lote com base nos itens associados.
        """
        bruto = Decimal("0.00")
        irrf = Decimal("0.00")
        liquido = Decimal("0.00")

        for item in self.itens:
            bruto += Decimal(item.valor_bruto or 0)
            irrf += Decimal(item.valor_irrf or 0)
            liquido += Decimal(item.valor_liquido or 0)

        self.quantidade_itens = len(self.itens)
        self.valor_total_bruto = bruto
        self.valor_total_irrf = irrf
        self.valor_total_liquido = liquido

    def gerar_resumo_operacional(self):
        """
        Resumo operacional do lote geral sem IRRF.
        """
        data_ci_str = (
            self.dativo_ci.data_ci.strftime("%d/%m/%Y")
            if self.dativo_ci and self.dativo_ci.data_ci
            else "SEM_DATA"
        )

        self.resumo_operacional = (
            f"C.I. {self.dativo_ci.processo_edoc}_"
            f"{self.dativo_ci.descricao}_"
            f"{data_ci_str}_"
            f"SEM IRRF"
        )

    def __repr__(self) -> str:
        return f"<DativoLote {self.tipo_lote} CI={self.dativo_ci_id}>"
