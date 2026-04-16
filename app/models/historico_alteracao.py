import json

from app.extensions import db
from app.utils.datetime_utils import utc_now_naive


class HistoricoAlteracao(db.Model):
    __tablename__ = "historico_alteracoes"

    id = db.Column(db.Integer, primary_key=True)
    entidade_tipo = db.Column(db.String(50), nullable=False, index=True)
    entidade_id = db.Column(db.Integer, nullable=False, index=True)
    acao = db.Column(db.String(80), nullable=False)
    resumo = db.Column(db.String(255), nullable=True)
    detalhes_json = db.Column(db.Text, nullable=False, default="[]")
    alterado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    alterado_por = db.relationship("User", foreign_keys=[alterado_por_id], lazy=True)

    @property
    def alteracoes(self) -> list[dict]:
        try:
            valor = json.loads(self.detalhes_json or "[]")
        except json.JSONDecodeError:
            return []
        return valor if isinstance(valor, list) else []

    def definir_alteracoes(self, alteracoes: list[dict]) -> None:
        self.detalhes_json = json.dumps(alteracoes or [], ensure_ascii=False)

    def __repr__(self) -> str:
        return f"<HistoricoAlteracao {self.entidade_tipo}:{self.entidade_id} {self.acao}>"
