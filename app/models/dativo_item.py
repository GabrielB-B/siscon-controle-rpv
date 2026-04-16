from decimal import Decimal

from app.extensions import db
from app.utils.datetime_utils import utc_now_naive
from app.utils.formatters import detectar_tipo_documento, formatar_documento_br
from app.utils.normalizers import normalizar_nome, normalizar_documento
from app.utils.payment_rules import nome_status_eh_cancelado

LIMITE_ALERTA_IRRF_DATIVO_SEM_IRRF = Decimal("5040.00")


class DativoItem(db.Model):
    """
    Item individual do dativo.

    Pode pertencer:
    - ao lote sem IRRF (grupo = sem_irrf)
    - ou ao grupo individual com IRRF (grupo = com_irrf)
    """

    __tablename__ = "dativos_itens"
    __table_args__ = (
        db.Index(
            "ix_dativos_itens_ci_grupo_doc_processo",
            "dativo_ci_id",
            "grupo",
            "cpf_normalizado",
            "numero_processo",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    dativo_ci_id = db.Column(db.Integer, db.ForeignKey("dativos_ci.id"), nullable=False, index=True)
    dativo_lote_id = db.Column(db.Integer, db.ForeignKey("dativos_lotes.id"), nullable=True, index=True)

    grupo = db.Column(db.String(20), nullable=False, index=True)

    nome_beneficiario = db.Column(db.String(200), nullable=False)
    nome_beneficiario_normalizado = db.Column(db.String(200), nullable=False, index=True)

    # Mantido por compatibilidade do banco, mas na interface tratamos como CPF/CNPJ
    cpf_original = db.Column(db.String(20), nullable=False)
    cpf_normalizado = db.Column(db.String(20), nullable=False, index=True)

    numero_processo = db.Column(db.String(50), nullable=False, index=True)

    data_pagamento = db.Column(db.Date, nullable=True)
    reinf_status = db.Column(db.String(30), nullable=True)
    dispensa_irrf_confirmada = db.Column(db.Boolean, nullable=False, default=False)

    valor_bruto = db.Column(db.Numeric(14, 2), nullable=False)
    valor_irrf = db.Column(db.Numeric(14, 2), nullable=True)
    valor_liquido = db.Column(db.Numeric(14, 2), nullable=False, default=0)

    nota_empenho = db.Column(db.String(50), nullable=True)
    numero_se = db.Column(db.String(50), nullable=True)
    ordem_bancaria = db.Column(db.String(50), nullable=True)
    ob_imposto = db.Column(db.String(50), nullable=True)

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

    dativo_ci = db.relationship("DativoCI", back_populates="itens")
    dativo_lote = db.relationship("DativoLote", back_populates="itens")

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

    def _formatar_decimal_ptbr(self, valor: Decimal) -> str:
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def _irrf_info_resumo(self) -> str:
        if self.grupo == "com_irrf":
            if self.valor_irrf and Decimal(self.valor_irrf) > 0:
                return f"IRRF {self._formatar_decimal_ptbr(Decimal(self.valor_irrf))}"
            return "IRRF PENDENTE"
        return "SEM IRRF"

    def _montar_resumo_operacional(self, processo_edoc: str | None = None, data_ci=None) -> str:
        if not processo_edoc:
            processo_edoc = self.dativo_ci.processo_edoc if self.dativo_ci else "SEM_CI"

        if data_ci:
            data_ci_str = data_ci.strftime("%d/%m/%Y")
        elif self.dativo_ci and self.dativo_ci.data_ci:
            data_ci_str = self.dativo_ci.data_ci.strftime("%d/%m/%Y")
        else:
            data_ci_str = "SEM_DATA"

        return (
            f"C.I. {processo_edoc}_"
            f"{self.numero_processo}_"
            f"Dativo_"
            f"{self.nome_beneficiario}_"
            f"{data_ci_str}_"
            f"{self._irrf_info_resumo()}"
        )

    @property
    def tipo_documento_efetivo(self) -> str:
        return detectar_tipo_documento(self.cpf_original)

    @property
    def documento_formatado(self) -> str:
        return formatar_documento_br(self.cpf_original, self.tipo_documento_efetivo)

    @property
    def requer_conferencia_irrf_sem_retencao(self) -> bool:
        return (
            self.grupo == "sem_irrf"
            and Decimal(self.valor_bruto or 0) >= LIMITE_ALERTA_IRRF_DATIVO_SEM_IRRF
        )

    @property
    def alerta_irrf_sem_retencao_pendente(self) -> bool:
        return self.requer_conferencia_irrf_sem_retencao and not self.dispensa_irrf_confirmada

    def atualizar_campos_derivados(self):
        """
        Normaliza nome/documento e recalcula o valor líquido.
        """
        self.nome_beneficiario_normalizado = normalizar_nome(self.nome_beneficiario)
        self.cpf_normalizado = normalizar_documento(self.cpf_original)

        bruto = Decimal(self.valor_bruto or 0)
        irrf_informado = self.valor_irrf is not None
        irrf = Decimal(self.valor_irrf or 0)

        if irrf < 0:
            irrf = Decimal("0.00")

        if irrf > bruto:
            irrf = bruto

        if irrf_informado:
            self.valor_irrf = irrf
        else:
            self.valor_irrf = None

        self.valor_liquido = bruto - irrf

    def gerar_resumo_operacional(self, processo_edoc: str | None = None, data_ci=None):
        """
        Gera o resumo operacional do item.

        Regras:
        - grupo sem_irrf -> SEM IRRF
        - grupo com_irrf sem valor ainda -> IRRF PENDENTE
        - grupo com_irrf com valor -> IRRF xx,xx
        """
        self.resumo_operacional = self._montar_resumo_operacional(
            processo_edoc=processo_edoc,
            data_ci=data_ci,
        )

    @property
    def resumo_operacional_atual(self) -> str:
        if self.dativo_ci and self.dativo_ci.data_ci:
            return self._montar_resumo_operacional()
        return self.resumo_operacional

    @property
    def status_principal_cancelado(self) -> bool:
        nome_situacao = getattr(getattr(self, "situacao_rpv", None), "nome", None)
        return nome_status_eh_cancelado(nome_situacao)

    @property
    def data_pagamento_input_value(self) -> str:
        return self.data_pagamento.strftime("%Y-%m-%d") if self.data_pagamento else ""

    @property
    def competencia_operacional(self) -> str:
        if self.data_pagamento:
            return self.data_pagamento.strftime("%Y-%m")
        if self.dativo_ci and self.dativo_ci.exercicio:
            return self.dativo_ci.exercicio
        return ""

    @property
    def competencia_operacional_formatada(self) -> str:
        valor = str(self.competencia_operacional or "").strip()

        if len(valor) == 7 and "-" in valor:
            ano, mes = valor.split("-")
            meses = {
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
            return f"{meses.get(mes, mes)}/{ano}"

        return valor

    @property
    def reinf_status_legivel(self) -> str:
        return self.reinf_status or "Não enviado"

    def __repr__(self) -> str:
        return f"<DativoItem {self.nome_beneficiario} grupo={self.grupo}>"
