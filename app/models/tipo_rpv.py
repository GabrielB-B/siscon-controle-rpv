from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


class TipoRPV(db.Model):
    __tablename__ = "tipos_rpv"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False, unique=True, index=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    ordem_exibicao = db.Column(db.Integer, nullable=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    def __repr__(self) -> str:
        return f"<TipoRPV {self.nome}>"
