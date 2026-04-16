from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db, login_manager
from app.utils.datetime_utils import utc_now_naive
from app.utils.normalizers import formatar_telefone_br, normalizar_telefone


class User(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    login = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(160), unique=True, nullable=True, index=True)
    telefone = db.Column(db.String(30), nullable=True)
    cargo = db.Column(db.String(80), nullable=True)
    setor = db.Column(db.String(80), nullable=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    forcar_troca_senha = db.Column(db.Boolean, nullable=False, default=False)
    senha_alterada_em = db.Column(db.DateTime, nullable=True)
    ultimo_login_em = db.Column(db.DateTime, nullable=True)
    ultimo_login_ip = db.Column(db.String(64), nullable=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    def set_password(self, senha: str) -> None:
        self.senha_hash = generate_password_hash(senha)
        self.senha_alterada_em = utc_now_naive()
        self.forcar_troca_senha = False

    def check_password(self, senha: str) -> bool:
        return check_password_hash(self.senha_hash, senha)

    @property
    def perfil_pendente(self) -> bool:
        if self.is_admin:
            return False

        campos_obrigatorios = (
            self.email,
            self.telefone,
            self.cargo,
            self.setor,
        )
        return not all(str(campo or "").strip() for campo in campos_obrigatorios)

    @property
    def ultimo_login_legivel(self) -> str:
        if not self.ultimo_login_em:
            return "Nunca acessou"
        return self.ultimo_login_em.strftime("%d/%m/%Y %H:%M")

    @property
    def telefone_normalizado(self) -> str:
        return normalizar_telefone(self.telefone)

    @property
    def telefone_formatado(self) -> str:
        return formatar_telefone_br(self.telefone)

    def __repr__(self) -> str:
        return f"<User {self.login}>"


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
