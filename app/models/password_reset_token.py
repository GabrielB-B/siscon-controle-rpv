from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    canal = db.Column(db.String(20), nullable=False)
    destino_mascarado = db.Column(db.String(160), nullable=True)
    solicitado_ip = db.Column(db.String(64), nullable=True)
    expira_em = db.Column(db.DateTime, nullable=False)
    verificado_em = db.Column(db.DateTime, nullable=True)
    tentativas_codigo = db.Column(db.Integer, nullable=False, default=0)
    utilizado_em = db.Column(db.DateTime, nullable=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

    usuario = db.relationship("User", backref=db.backref("password_reset_tokens", lazy="dynamic"))

    @property
    def expirado(self) -> bool:
        return utc_now_naive() >= self.expira_em

    @property
    def disponivel(self) -> bool:
        return self.utilizado_em is None and not self.expirado

    @property
    def codigo_verificado(self) -> bool:
        return self.verificado_em is not None

    def __repr__(self) -> str:
        return f"<PasswordResetToken user_id={self.user_id} canal={self.canal}>"
