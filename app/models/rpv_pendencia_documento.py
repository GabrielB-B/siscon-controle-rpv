from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.utils.datetime_utils import utc_now_naive
from app.utils.documentos import validar_documento_brasileiro
from app.utils.formatters import detectar_tipo_documento, formatar_documento_br
from app.utils.normalizers import normalizar_documento, normalizar_nome


class RPVPendenciaDocumento(db.Model):
    __tablename__ = "rpv_pendencias_documentais"

    id = db.Column(db.Integer, primary_key=True)

    exercicio = db.Column(db.String(7), nullable=False, index=True)
    processo_edoc = db.Column(db.String(50), nullable=False, index=True)
    numero_processo = db.Column(db.String(50), nullable=False, index=True)
    data_ci = db.Column(db.Date, nullable=False)

    tipo_rpv_id = db.Column(db.Integer, db.ForeignKey("tipos_rpv.id"), nullable=False)
    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)

    nome_beneficiario = db.Column(db.String(200), nullable=False)
    nome_beneficiario_normalizado = db.Column(db.String(200), nullable=False, index=True)

    tipo_documento = db.Column(db.String(10), nullable=False)
    documento_original = db.Column(db.String(30), nullable=True)
    documento_normalizado = db.Column(db.String(20), nullable=False, default="", index=True)

    valor_bruto = db.Column(db.Numeric(14, 2), nullable=False)
    valor_irrf = db.Column(db.Numeric(14, 2), nullable=True)
    sem_irrf = db.Column(db.Boolean, nullable=False, default=False)
    observacoes = db.Column(db.Text, nullable=True)

    motivo_pendencia = db.Column(
        db.String(255),
        nullable=False,
        default="Documento pendente de validacao.",
    )
    status = db.Column(db.String(20), nullable=False, default="aberta", index=True)

    documento_confirmado_manual = db.Column(db.Boolean, nullable=False, default=False)
    documento_confirmado_em = db.Column(db.DateTime, nullable=True)
    documento_confirmado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    registro_rpv_convertido_id = db.Column(
        db.Integer,
        db.ForeignKey("registros_rpv.id"),
        nullable=True,
    )

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    tipo_rpv = db.relationship("TipoRPV")
    responsavel = db.relationship("User", foreign_keys=[responsavel_id])
    criado_por = db.relationship("User", foreign_keys=[criado_por_id])
    atualizado_por = db.relationship("User", foreign_keys=[atualizado_por_id])
    documento_confirmado_por = db.relationship("User", foreign_keys=[documento_confirmado_por_id])
    registro_rpv_convertido = db.relationship("RegistroRPV", foreign_keys=[registro_rpv_convertido_id])

    def atualizar_campos_derivados(self) -> None:
        self.nome_beneficiario_normalizado = normalizar_nome(self.nome_beneficiario)
        self.documento_normalizado = normalizar_documento(self.documento_original)
        self.tipo_documento = detectar_tipo_documento(
            self.documento_normalizado,
            self.tipo_documento,
        )
        self.motivo_pendencia = self.motivo_documento_exibido

    @property
    def tipo_documento_efetivo(self) -> str:
        return detectar_tipo_documento(self.documento_original, self.tipo_documento)

    @property
    def documento_formatado(self) -> str:
        return formatar_documento_br(self.documento_original, self.tipo_documento_efetivo)

    @property
    def validacao_documento(self) -> dict[str, str | bool]:
        return validar_documento_brasileiro(self.documento_original, self.tipo_documento)

    @property
    def documento_valido(self) -> bool:
        return bool(self.validacao_documento["valido"])

    @property
    def documento_ausente(self) -> bool:
        return not bool(self.documento_normalizado)

    @property
    def pode_continuar_fluxo_oficial(self) -> bool:
        return self.documento_valido

    @property
    def status_legivel(self) -> str:
        if str(self.status or "").strip().lower() == "aberta" and self.documento_valido:
            return "Pronto para oficializar"

        mapa = {
            "aberta": "Em revisao",
            "convertida": "Convertida",
            "descartada": "Cancelada",
        }
        return mapa.get(str(self.status or "").strip().lower(), self.status or "-")

    @property
    def documento_status_legivel(self) -> str:
        if self.documento_valido:
            return "Documento validado"
        if self.documento_confirmado_manual:
            return "Conferencia registrada"
        if self.documento_ausente:
            return "Sem documento"
        return "Documento invalido"

    @property
    def motivo_documento_exibido(self) -> str:
        if self.documento_valido:
            return str(self.validacao_documento["motivo"])
        if self.documento_confirmado_manual:
            return (
                "A conferencia manual foi registrada, mas o cadastro so sai desta fila "
                "quando CPF/CNPJ valido for informado."
            )
        return str(self.validacao_documento["motivo"])

    @property
    def valor_liquido_estimado(self) -> Decimal:
        bruto = Decimal(self.valor_bruto or 0)
        if self.sem_irrf:
            return bruto
        irrf = Decimal(self.valor_irrf or 0)
        if irrf < 0:
            irrf = Decimal("0.00")
        if irrf > bruto:
            irrf = bruto
        return bruto - irrf

    @property
    def resumo_operacional(self) -> str:
        descricao_tipo = getattr(getattr(self, "tipo_rpv", None), "nome", None) or "Sem tipo"
        return (
            f"C.I. {self.processo_edoc}_"
            f"{self.numero_processo}_"
            f"{descricao_tipo}_"
            f"{self.nome_beneficiario}_"
            f"{self.motivo_documento_exibido}"
        )
