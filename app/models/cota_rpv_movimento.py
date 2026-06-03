from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


class CotaRPVMovimento(db.Model):
    __tablename__ = "cotas_rpv_movimentos"

    id = db.Column(db.Integer, primary_key=True)
    cota_rpv_competencia_id = db.Column(
        db.Integer,
        db.ForeignKey("cotas_rpv_competencias.id"),
        nullable=False,
        index=True,
    )
    tipo_movimento = db.Column(db.String(30), nullable=False, index=True)
    valor = db.Column(db.Numeric(14, 2), nullable=False)
    referencia_competencia = db.Column(db.String(7), nullable=True)
    observacoes = db.Column(db.Text, nullable=True)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    competencia_ref = db.relationship("CotaRPVCompetencia", back_populates="movimentos")
    criado_por = db.relationship("User", foreign_keys=[criado_por_id])

    def __repr__(self) -> str:
        return f"<CotaRPVMovimento {self.tipo_movimento} {self.valor}>"
