from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


class SituacaoImposto(db.Model):
    __tablename__ = "situacoes_imposto"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False, unique=True, index=True)
    cor_badge = db.Column(db.String(20), nullable=True)
    ordem_fluxo = db.Column(db.Integer, nullable=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    is_final = db.Column(db.Boolean, nullable=False, default=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    def __repr__(self) -> str:
        return f"<SituacaoImposto {self.nome}>"
