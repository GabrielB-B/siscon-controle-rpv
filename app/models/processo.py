from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


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


class Processo(db.Model):
    __tablename__ = "processos"

    id = db.Column(db.Integer, primary_key=True)

    # formato interno recomendado: YYYY-MM
    exercicio = db.Column(db.String(7), nullable=False, index=True)
    processo_edoc = db.Column(db.String(50), nullable=False, index=True)
    numero_processo = db.Column(db.String(50), nullable=False, index=True)

    data_ci = db.Column(db.Date, nullable=False)
    data_cadastro = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

    observacoes_gerais = db.Column(db.Text, nullable=True)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    criado_por = db.relationship("User", foreign_keys=[criado_por_id], lazy=True)
    atualizado_por = db.relationship("User", foreign_keys=[atualizado_por_id], lazy=True)

    registros = db.relationship(
        "RegistroRPV",
        back_populates="processo",
        cascade="all, delete-orphan",
        lazy=True,
    )

    @property
    def exercicio_formatado(self) -> str:
        valor = str(self.exercicio or "").strip()

        if len(valor) == 7 and "-" in valor:
            ano, mes = valor.split("-")
            mes_abrev = MESES_ABREV.get(mes, mes)
            return f"{mes_abrev}/{ano}"

        if len(valor) == 7 and "/" in valor:
            mes, ano = valor.split("/")
            mes_abrev = MESES_ABREV.get(mes.zfill(2), mes)
            return f"{mes_abrev}/{ano}"

        return valor

    @property
    def exercicio_input_value(self) -> str:
        valor = str(self.exercicio or "").strip()

        if len(valor) == 7 and "-" in valor:
            return valor

        if len(valor) == 7 and "/" in valor:
            mes, ano = valor.split("/")
            return f"{ano}-{mes.zfill(2)}"

        return ""

    def __repr__(self) -> str:
        return f"<Processo {self.numero_processo}>"
