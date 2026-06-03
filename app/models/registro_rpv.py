from decimal import Decimal

from app.extensions import db
from app.utils.datetime_utils import utc_now_naive
from app.utils.formatters import detectar_tipo_documento, formatar_documento_br
from app.utils.normalizers import normalizar_nome, normalizar_documento
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


class RegistroRPV(db.Model):
    __tablename__ = "registros_rpv"
    __table_args__ = (
        db.Index("ix_registros_rpv_data_pagamento", "data_pagamento"),
        db.Index("ix_registros_rpv_elaborador_data_pagamento", "elaborador_id", "data_pagamento"),
    )

    id = db.Column(db.Integer, primary_key=True)

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    elaborador_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    tipo_rpv_id = db.Column(db.Integer, db.ForeignKey("tipos_rpv.id"), nullable=False)

    nome_beneficiario = db.Column(db.String(200), nullable=False)
    nome_beneficiario_normalizado = db.Column(db.String(200), nullable=False, index=True)

    tipo_documento = db.Column(db.String(10), nullable=False)
    documento_original = db.Column(db.String(30), nullable=False)
    documento_normalizado = db.Column(db.String(20), nullable=False, index=True)
    documento_corrigido = db.Column(db.String(20), nullable=True)

    data_pagamento = db.Column(db.Date, nullable=True)
    data_pagamento_irrf = db.Column(db.Date, nullable=True)

    valor_bruto = db.Column(db.Numeric(14, 2), nullable=False)
    valor_irrf = db.Column(db.Numeric(14, 2), nullable=True)
    valor_liquido = db.Column(db.Numeric(14, 2), nullable=False, default=0)

    possui_irrf = db.Column(db.Boolean, nullable=False, default=False)
    sem_irrf = db.Column(db.Boolean, nullable=False, default=False)
    imposto_texto = db.Column(db.String(30), nullable=True)

    nota_empenho = db.Column(db.String(50), nullable=True)
    numero_se = db.Column(db.String(50), nullable=True)

    situacao_empenho_id = db.Column(
        db.Integer,
        db.ForeignKey("situacoes_empenho.id"),
        nullable=False,
    )
    situacao_imposto_id = db.Column(
        db.Integer,
        db.ForeignKey("situacoes_imposto.id"),
        nullable=False,
    )

    ordem_bancaria = db.Column(db.String(50), nullable=True)
    reinf_status = db.Column(db.String(30), nullable=True)
    ob_imposto = db.Column(db.String(50), nullable=True)

    historico_auto = db.Column(db.String(500), nullable=False, default="")
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

    processo = db.relationship("Processo", back_populates="registros")
    tipo_rpv = db.relationship("TipoRPV")
    situacao_empenho = db.relationship("SituacaoEmpenho")
    situacao_imposto = db.relationship("SituacaoImposto")
    elaborador = db.relationship("User", foreign_keys=[elaborador_id])
    criado_por = db.relationship("User", foreign_keys=[criado_por_id])
    atualizado_por = db.relationship("User", foreign_keys=[atualizado_por_id])

    def _formatar_decimal_ptbr(self, valor: Decimal) -> str:
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @property
    def sem_irrf_efetivo(self) -> bool:
        if self.sem_irrf:
            return True

        nome_situacao = getattr(getattr(self, "situacao_imposto", None), "nome", None)
        return str(nome_situacao or "").strip().casefold() == "sem irrf"

    @property
    def status_principal_cancelado(self) -> bool:
        nome_situacao = getattr(getattr(self, "situacao_empenho", None), "nome", None)
        return nome_status_eh_cancelado(nome_situacao)

    def _irrf_info_resumo(self) -> str:
        if self.sem_irrf_efetivo:
            return "SEM IRRF"

        if self.possui_irrf and self.valor_irrf:
            return f"IRRF {self._formatar_decimal_ptbr(Decimal(self.valor_irrf))}"

        return "IRRF PENDENTE"

    @property
    def tipo_documento_efetivo(self) -> str:
        return detectar_tipo_documento(self.documento_original, self.tipo_documento)

    @property
    def documento_formatado(self) -> str:
        return formatar_documento_br(self.documento_original, self.tipo_documento_efetivo)

    @property
    def competencia_operacional_valor(self) -> str:
        if self.data_pagamento:
            return self.data_pagamento.strftime("%Y-%m")

        if self.processo and self.processo.exercicio:
            return self.processo.exercicio

        return ""

    def _data_ci_resumo(self, data_ci=None) -> str:
        if data_ci:
            return data_ci.strftime("%d/%m/%Y")
        if self.processo and self.processo.data_ci:
            return self.processo.data_ci.strftime("%d/%m/%Y")
        return "SEM_DATA"

    @property
    def resumo_operacional(self) -> str:
        processo_edoc = self.processo.processo_edoc if self.processo else ""
        numero_processo = self.processo.numero_processo if self.processo else ""
        descricao = self.tipo_rpv.nome if self.tipo_rpv else ""
        data_ci = self._data_ci_resumo()

        return (
            f"C.I. {processo_edoc}_"
            f"{numero_processo}_"
            f"{descricao}_"
            f"{self.nome_beneficiario}_"
            f"{data_ci}_"
            f"{self._irrf_info_resumo()}"
        )

    def atualizar_campos_derivados(self):
        self.nome_beneficiario_normalizado = normalizar_nome(self.nome_beneficiario)
        self.documento_normalizado = normalizar_documento(self.documento_original)
        self.tipo_documento = detectar_tipo_documento(self.documento_normalizado, self.tipo_documento)

        bruto = Decimal(self.valor_bruto or 0)

        if self.sem_irrf:
            self.valor_irrf = None
            self.valor_liquido = bruto
            self.possui_irrf = False
            self.imposto_texto = None
            return

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
        self.possui_irrf = irrf > 0

        if self.possui_irrf:
            self.imposto_texto = self._formatar_decimal_ptbr(irrf)
        else:
            self.imposto_texto = None

    def gerar_historico_auto(self, processo_edoc=None, numero_processo=None, descricao=None, data_ci=None):
        if not processo_edoc and self.processo:
            processo_edoc = self.processo.processo_edoc

        if not numero_processo and self.processo:
            numero_processo = self.processo.numero_processo

        if not descricao and self.tipo_rpv:
            descricao = self.tipo_rpv.nome

        data_ci = self._data_ci_resumo(data_ci=data_ci)

        self.historico_auto = (
            f"C.I. {processo_edoc}_"
            f"{numero_processo}_"
            f"{descricao}_"
            f"{self.nome_beneficiario}_"
            f"{data_ci}_"
            f"{self._irrf_info_resumo()}"
        )

    @property
    def data_pagamento_input_value(self) -> str:
        return self.data_pagamento.strftime("%Y-%m-%d") if self.data_pagamento else ""

    @property
    def data_pagamento_irrf_input_value(self) -> str:
        return self.data_pagamento_irrf.strftime("%Y-%m-%d") if self.data_pagamento_irrf else ""

    @property
    def competencia_operacional(self) -> str:
        if self.data_pagamento:
            return self.data_pagamento.strftime("%Y-%m")
        if self.processo and self.processo.exercicio:
            return self.processo.exercicio
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
        return self.reinf_status or "Não enviado"

    def __repr__(self) -> str:
        return f"<RegistroRPV {self.nome_beneficiario}>"
