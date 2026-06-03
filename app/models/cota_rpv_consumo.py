from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


class CotaRPVConsumo(db.Model):
    __tablename__ = "cotas_rpv_consumos"
    __table_args__ = (
        db.Index("ix_cotas_rpv_consumos_origem", "origem_tipo", "origem_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cota_rpv_competencia_id = db.Column(
        db.Integer,
        db.ForeignKey("cotas_rpv_competencias.id"),
        nullable=False,
        index=True,
    )
    origem_tipo = db.Column(db.String(30), nullable=False, index=True)
    origem_id = db.Column(db.Integer, nullable=False, index=True)
    valor_consumido = db.Column(db.Numeric(14, 2), nullable=False)
    resumo_origem = db.Column(db.String(255), nullable=True)

    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)

    consumido_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    estornado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    consumido_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    estornado_em = db.Column(db.DateTime, nullable=True)

    competencia_ref = db.relationship("CotaRPVCompetencia", back_populates="consumos")
    consumido_por = db.relationship("User", foreign_keys=[consumido_por_id])
    estornado_por = db.relationship("User", foreign_keys=[estornado_por_id])

    def __repr__(self) -> str:
        return f"<CotaRPVConsumo {self.origem_tipo}:{self.origem_id} ativo={self.ativo}>"
