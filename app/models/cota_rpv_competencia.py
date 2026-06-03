from app.extensions import db
from app.utils.cota_groups import meta_grupo_cota
from app.utils.datetime_utils import utc_now_naive


class CotaRPVCompetencia(db.Model):
    __tablename__ = "cotas_rpv_competencias"
    __table_args__ = (
        db.UniqueConstraint(
            "competencia",
            "grupo_cota",
            name="uq_cotas_rpv_competencia_grupo",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    competencia = db.Column(db.String(7), nullable=False, index=True)
    grupo_cota = db.Column(db.String(20), nullable=False, index=True)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    criado_por = db.relationship("User", foreign_keys=[criado_por_id])
    atualizado_por = db.relationship("User", foreign_keys=[atualizado_por_id])

    movimentos = db.relationship(
        "CotaRPVMovimento",
        back_populates="competencia_ref",
        cascade="all, delete-orphan",
        lazy=True,
    )
    consumos = db.relationship(
        "CotaRPVConsumo",
        back_populates="competencia_ref",
        cascade="all, delete-orphan",
        lazy=True,
    )

    @property
    def grupo_meta(self) -> dict:
        return meta_grupo_cota(self.grupo_cota)

    @property
    def grupo_label(self) -> str:
        return self.grupo_meta["label"]

    def __repr__(self) -> str:
        return f"<CotaRPVCompetencia {self.competencia} {self.grupo_cota}>"
