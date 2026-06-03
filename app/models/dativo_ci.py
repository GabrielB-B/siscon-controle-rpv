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


class DativoCI(db.Model):
    """
    Cabeçalho principal da C.I. de dativo.

    Uma mesma C.I. pode conter:
    - um lote geral sem IRRF
    - vários itens individuais com IRRF
    """

    __tablename__ = "dativos_ci"

    id = db.Column(db.Integer, primary_key=True)

    # Formato interno: YYYY-MM
    exercicio = db.Column(db.String(7), nullable=False, index=True)

    # Número da C.I. / e-Doc
    processo_edoc = db.Column(db.String(50), nullable=False, unique=True, index=True)
    data_ci = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="aberta", index=True)

    descricao = db.Column(db.String(120), nullable=False, default="Dativo Geral")

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    criado_por = db.relationship("User", foreign_keys=[criado_por_id], lazy=True)
    responsavel = db.relationship("User", foreign_keys=[responsavel_id], lazy=True)
    atualizado_por = db.relationship("User", foreign_keys=[atualizado_por_id], lazy=True)

    lotes = db.relationship(
        "DativoLote",
        back_populates="dativo_ci",
        cascade="all, delete-orphan",
        lazy=True,
    )

    itens = db.relationship(
        "DativoItem",
        back_populates="dativo_ci",
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

        return valor

    @property
    def exercicio_input_value(self) -> str:
        valor = str(self.exercicio or "").strip()

        if len(valor) == 7 and "-" in valor:
            return valor

        return ""

    @property
    def quantidade_total_itens(self) -> int:
        return len(self.itens)

    @property
    def quantidade_itens_sem_irrf(self) -> int:
        return sum(1 for item in self.itens if item.grupo == "sem_irrf")

    @property
    def quantidade_itens_com_irrf(self) -> int:
        return sum(1 for item in self.itens if item.grupo == "com_irrf")

    @property
    def possui_lote_sem_irrf(self) -> bool:
        return any(lote.tipo_lote == "sem_irrf" for lote in self.lotes)

    @property
    def possui_movimentacao_ativa(self) -> bool:
        lotes_ativos = any(getattr(lote, "ativo", True) for lote in self.lotes)
        itens_ativos = any(getattr(item, "ativo", True) for item in self.itens)
        return lotes_ativos or itens_ativos

    @property
    def pode_editar_cabecalho(self) -> bool:
        return not self.possui_movimentacao_ativa

    @property
    def pode_descartar(self) -> bool:
        return self.pode_editar_cabecalho and self.status_normalizado == "aberta"

    @property
    def pode_reabrir(self) -> bool:
        return self.pode_editar_cabecalho and self.status_normalizado == "descartada"

    @property
    def status_normalizado(self) -> str:
        return str(self.status or "aberta").strip().casefold()

    @property
    def status_legivel(self) -> str:
        mapa = {
            "aberta": "Em preparacao",
            "descartada": "Cancelada",
        }
        return mapa.get(self.status_normalizado, self.status or "-")

    def __repr__(self) -> str:
        return f"<DativoCI {self.processo_edoc}>"
