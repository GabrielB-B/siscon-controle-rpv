from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload
from unidecode import unidecode

from app.extensions import db
from app.models import (
    CotaRPVCompetencia,
    CotaRPVConsumo,
    CotaRPVMovimento,
    DativoItem,
    DativoLote,
    RegistroRPV,
    SituacaoEmpenho,
    TipoRPV,
)
from app.utils.cota_groups import GRUPOS_COTA_ORDEM, classificar_grupo_cota, meta_grupo_cota
from app.utils.datetime_utils import utc_now_naive
from app.utils.domain_profile import get_domain_profile


class CotasRPVService:
    TIPO_APORTE_MANUAL = "aporte_manual"
    TIPO_TRANSFERENCIA_ENTRADA = "transferencia_entrada"
    TIPO_TRANSFERENCIA_SAIDA = "transferencia_saida"
    TIPO_AJUSTE_MANUAL = "ajuste_manual"

    _STATUS_CONSUMO_NOME = get_domain_profile().situacao_empenho_name(
        "se_aprovada_gerar_ne"
    )
    _CRITICAL_REMAINING_PERCENT = Decimal("30.0")
    _ATTENTION_REMAINING_PERCENT = Decimal("40.0")
    _CRITICAL_MULTIPLIER = Decimal("0.75")
    _ATTENTION_MULTIPLIER = Decimal("1.50")

    @staticmethod
    def _normalizar_texto(valor: str | None) -> str:
        return unidecode(str(valor or "").strip()).lower()

    @classmethod
    def competencia_atual(cls) -> str:
        return date.today().strftime("%Y-%m")

    @classmethod
    def normalizar_competencia(cls, valor: str | None) -> str:
        competencia = str(valor or "").strip()
        if len(competencia) == 7 and competencia[4] == "-":
            ano, mes = competencia.split("-", 1)
            try:
                ano_int = int(ano)
                mes_int = int(mes)
            except ValueError:
                return ""
            if 1 <= mes_int <= 12 and ano_int > 2000:
                return f"{ano_int:04d}-{mes_int:02d}"
        return ""

    @classmethod
    def competencia_legivel(cls, valor: str | None) -> str:
        meses = {
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
        competencia = cls.normalizar_competencia(valor)
        if not competencia:
            return "-"
        ano, mes = competencia.split("-", 1)
        return f"{meses.get(mes, mes)}/{ano}"

    @classmethod
    def proxima_competencia(cls, valor: str | None) -> str:
        competencia = cls.normalizar_competencia(valor)
        if not competencia:
            return ""
        ano, mes = competencia.split("-", 1)
        ano_int = int(ano)
        mes_int = int(mes) + 1
        if mes_int > 12:
            return f"{ano_int + 1:04d}-01"
        return f"{ano_int:04d}-{mes_int:02d}"

    @classmethod
    def _inicio_competencia(cls, valor: str | None) -> datetime | None:
        competencia = cls.normalizar_competencia(valor)
        if not competencia:
            return None
        ano, mes = competencia.split("-", 1)
        return datetime(int(ano), int(mes), 1)

    @classmethod
    def _limite_fechamento_competencia(cls, valor: str | None) -> datetime | None:
        return cls._inicio_competencia(cls.proxima_competencia(valor))

    @staticmethod
    def _decimal(valor) -> Decimal:
        try:
            return Decimal(valor or 0)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0.00")

    @classmethod
    def _quantizar(cls, valor) -> Decimal:
        return cls._decimal(valor).quantize(Decimal("0.01"))

    @classmethod
    def _quantizar_percentual(cls, valor) -> Decimal:
        return cls._decimal(valor).quantize(Decimal("0.1"))

    @classmethod
    def _status_consume_cota(cls, nome_status: str | None) -> bool:
        return cls._normalizar_texto(nome_status) == cls._normalizar_texto(
            cls._STATUS_CONSUMO_NOME
        )

    @classmethod
    def _origem_entidade(cls, entidade) -> tuple[str, int]:
        if isinstance(entidade, RegistroRPV):
            return "registro_rpv", entidade.id
        if isinstance(entidade, DativoLote):
            return "dativo_lote", entidade.id
        if isinstance(entidade, DativoItem):
            return "dativo_item", entidade.id
        raise ValueError("Entidade sem suporte para consumo de cota.")

    @classmethod
    def _competencia_entidade(cls, entidade) -> str:
        if isinstance(entidade, RegistroRPV):
            return cls.normalizar_competencia(getattr(getattr(entidade, "processo", None), "exercicio", None))
        if isinstance(entidade, (DativoLote, DativoItem)):
            return cls.normalizar_competencia(getattr(getattr(entidade, "dativo_ci", None), "exercicio", None))
        return ""

    @classmethod
    def _grupo_entidade(cls, entidade) -> str:
        if isinstance(entidade, RegistroRPV):
            tipo_rpv = db.session.get(TipoRPV, getattr(entidade, "tipo_rpv_id", None))
            tipo_nome = getattr(tipo_rpv, "nome", None)
            return classificar_grupo_cota(tipo_nome, "rpv_normal")
        if isinstance(entidade, DativoLote):
            return classificar_grupo_cota(None, "dativo_lote_sem_irrf")
        if isinstance(entidade, DativoItem):
            origem = "dativo_com_irrf" if entidade.grupo == "com_irrf" else "dativo_sem_irrf"
            return classificar_grupo_cota(None, origem)
        return "comum"

    @classmethod
    def _valor_entidade(cls, entidade) -> Decimal:
        if isinstance(entidade, DativoLote):
            return cls._quantizar(getattr(entidade, "valor_total_bruto", 0))
        return cls._quantizar(getattr(entidade, "valor_bruto", 0))

    @classmethod
    def _nome_status_entidade(cls, entidade) -> str | None:
        if isinstance(entidade, RegistroRPV):
            situacao = db.session.get(
                SituacaoEmpenho,
                getattr(entidade, "situacao_empenho_id", None),
            )
            return getattr(situacao, "nome", None)
        situacao = db.session.get(
            SituacaoEmpenho,
            getattr(entidade, "situacao_rpv_id", None),
        )
        return getattr(situacao, "nome", None)

    @classmethod
    def _resumo_entidade(cls, entidade) -> str:
        if isinstance(entidade, RegistroRPV):
            processo = getattr(entidade, "processo", None)
            return (
                f"RPV normal | {entidade.nome_beneficiario} | "
                f"{getattr(processo, 'numero_processo', '-')}"
            )
        if isinstance(entidade, DativoLote):
            dativo_ci = getattr(entidade, "dativo_ci", None)
            return (
                f"Dativo sem IRRF | C.I. {getattr(dativo_ci, 'processo_edoc', '-')} | "
                f"{getattr(entidade, 'quantidade_itens', 0)} beneficiario(s)"
            )
        if isinstance(entidade, DativoItem):
            dativo_ci = getattr(entidade, "dativo_ci", None)
            return (
                f"Dativo com IRRF | {entidade.nome_beneficiario} | "
                f"C.I. {getattr(dativo_ci, 'processo_edoc', '-')}"
            )
        return "Origem operacional"

    @classmethod
    def obter_ou_criar_competencia(
        cls,
        competencia: str,
        grupo_cota: str,
        *,
        usuario_id: int,
    ) -> CotaRPVCompetencia:
        competencia_normalizada = cls.normalizar_competencia(competencia)
        if not competencia_normalizada:
            raise ValueError("Competencia invalida para cota.")
        grupo = grupo_cota if grupo_cota in GRUPOS_COTA_ORDEM else "comum"

        registro = CotaRPVCompetencia.query.filter_by(
            competencia=competencia_normalizada,
            grupo_cota=grupo,
        ).first()
        if registro:
            return registro

        registro = CotaRPVCompetencia(
            competencia=competencia_normalizada,
            grupo_cota=grupo,
            criado_por_id=usuario_id,
            atualizado_por_id=usuario_id,
        )
        db.session.add(registro)
        db.session.flush()
        return registro

    @classmethod
    def _consumo_ativo_origem(cls, origem_tipo: str, origem_id: int) -> CotaRPVConsumo | None:
        return (
            CotaRPVConsumo.query.options(joinedload(CotaRPVConsumo.competencia_ref))
            .filter_by(origem_tipo=origem_tipo, origem_id=origem_id, ativo=True)
            .first()
        )

    @classmethod
    def sincronizar_entidade(cls, entidade, *, usuario_id: int) -> None:
        origem_tipo, origem_id = cls._origem_entidade(entidade)
        consumo_ativo = cls._consumo_ativo_origem(origem_tipo, origem_id)
        deve_consumir = cls._status_consume_cota(cls._nome_status_entidade(entidade))

        if not deve_consumir:
            if consumo_ativo:
                consumo_ativo.ativo = False
                consumo_ativo.estornado_em = utc_now_naive()
                consumo_ativo.estornado_por_id = usuario_id
            return

        competencia = cls._competencia_entidade(entidade)
        valor = cls._valor_entidade(entidade)
        grupo_cota = cls._grupo_entidade(entidade)

        if not competencia or valor <= 0:
            if consumo_ativo:
                consumo_ativo.ativo = False
                consumo_ativo.estornado_em = utc_now_naive()
                consumo_ativo.estornado_por_id = usuario_id
            return

        bucket = cls.obter_ou_criar_competencia(
            competencia,
            grupo_cota,
            usuario_id=usuario_id,
        )
        resumo_origem = cls._resumo_entidade(entidade)

        if (
            consumo_ativo
            and consumo_ativo.cota_rpv_competencia_id == bucket.id
            and cls._quantizar(consumo_ativo.valor_consumido) == valor
        ):
            consumo_ativo.resumo_origem = resumo_origem
            return

        if consumo_ativo:
            consumo_ativo.ativo = False
            consumo_ativo.estornado_em = utc_now_naive()
            consumo_ativo.estornado_por_id = usuario_id

        db.session.add(
            CotaRPVConsumo(
                cota_rpv_competencia_id=bucket.id,
                origem_tipo=origem_tipo,
                origem_id=origem_id,
                valor_consumido=valor,
                resumo_origem=resumo_origem,
                ativo=True,
                consumido_por_id=usuario_id,
            )
        )
        bucket.atualizado_por_id = usuario_id

    @classmethod
    def registrar_aportes(
        cls,
        *,
        competencia: str,
        valores_por_grupo: dict[str, Decimal],
        usuario_id: int,
        observacoes: str | None = None,
    ) -> list[CotaRPVMovimento]:
        competencia_normalizada = cls.normalizar_competencia(competencia)
        if not competencia_normalizada:
            raise ValueError("Informe uma competencia valida para lancar a cota.")

        movimentos: list[CotaRPVMovimento] = []
        for grupo in GRUPOS_COTA_ORDEM:
            valor = cls._quantizar(valores_por_grupo.get(grupo))
            if valor < 0:
                raise ValueError("Os valores de cota nao podem ser negativos.")
            if valor == 0:
                continue

            bucket = cls.obter_ou_criar_competencia(
                competencia_normalizada,
                grupo,
                usuario_id=usuario_id,
            )
            bucket.atualizado_por_id = usuario_id
            movimento = CotaRPVMovimento(
                cota_rpv_competencia_id=bucket.id,
                tipo_movimento=cls.TIPO_APORTE_MANUAL,
                valor=valor,
                referencia_competencia=competencia_normalizada,
                observacoes=(observacoes or "").strip() or None,
                criado_por_id=usuario_id,
            )
            db.session.add(movimento)
            movimentos.append(movimento)

        if not movimentos:
            raise ValueError("Informe ao menos um valor maior que zero para lancar a cota.")

        return movimentos

    @classmethod
    def conciliar_saldo_oficial(
        cls,
        *,
        competencia: str,
        grupo_cota: str,
        saldo_oficial_atual: Decimal,
        usuario_id: int,
        observacoes: str | None = None,
    ) -> dict:
        competencia_normalizada = cls.normalizar_competencia(competencia)
        if not competencia_normalizada:
            raise ValueError("Informe uma competencia valida para conciliar a cota.")

        grupo_normalizado = str(grupo_cota or "").strip().lower()
        if grupo_normalizado not in GRUPOS_COTA_ORDEM:
            raise ValueError("Ficha invalida para conciliacao manual.")

        saldo_oficial = cls._quantizar(saldo_oficial_atual)
        if saldo_oficial < 0:
            raise ValueError("O saldo oficial nao pode ser negativo.")

        observacao_limpa = str(observacoes or "").strip()
        if not observacao_limpa:
            raise ValueError("Informe a justificativa da conciliacao manual.")

        bucket = cls.obter_ou_criar_competencia(
            competencia_normalizada,
            grupo_normalizado,
            usuario_id=usuario_id,
        )
        resumo_atual = cls._resumos_buckets([bucket]).get(bucket.id, {})
        valor_lancado_atual = cls._quantizar(resumo_atual.get("valor_lancado"))
        valor_consumido_atual = cls._quantizar(resumo_atual.get("valor_consumido"))
        saldo_atual = cls._quantizar(resumo_atual.get("saldo_disponivel"))

        delta_ajuste = cls._quantizar(saldo_oficial - saldo_atual)
        if delta_ajuste == 0:
            raise ValueError("O saldo informado ja esta sincronizado com o SISCON.")

        valor_lancado_final = cls._quantizar(valor_lancado_atual + delta_ajuste)
        if valor_lancado_final < valor_consumido_atual:
            raise ValueError(
                "O ajuste reduziria a ficha abaixo do valor ja consumido nesta competencia."
            )

        db.session.add(
            CotaRPVMovimento(
                cota_rpv_competencia_id=bucket.id,
                tipo_movimento=cls.TIPO_AJUSTE_MANUAL,
                valor=delta_ajuste,
                referencia_competencia=competencia_normalizada,
                observacoes=observacao_limpa,
                criado_por_id=usuario_id,
            )
        )
        bucket.atualizado_por_id = usuario_id

        return {
            "bucket_id": bucket.id,
            "competencia": competencia_normalizada,
            "grupo_cota": grupo_normalizado,
            "grupo_label": bucket.grupo_label,
            "saldo_anterior": saldo_atual,
            "saldo_oficial_atual": saldo_oficial,
            "delta_ajuste": delta_ajuste,
            "valor_lancado_anterior": valor_lancado_atual,
            "valor_consumido_atual": valor_consumido_atual,
            "valor_lancado_final": valor_lancado_final,
        }

    @classmethod
    def _mapa_movimentos(cls, bucket_ids: list[int]) -> dict[int, Decimal]:
        if not bucket_ids:
            return {}
        rows = (
            db.session.query(
                CotaRPVMovimento.cota_rpv_competencia_id,
                func.coalesce(func.sum(CotaRPVMovimento.valor), 0),
            )
            .filter(CotaRPVMovimento.cota_rpv_competencia_id.in_(bucket_ids))
            .group_by(CotaRPVMovimento.cota_rpv_competencia_id)
            .all()
        )
        return {bucket_id: cls._quantizar(total) for bucket_id, total in rows}

    @classmethod
    def _mapa_consumos_ativos(cls, bucket_ids: list[int]) -> dict[int, Decimal]:
        if not bucket_ids:
            return {}
        rows = (
            db.session.query(
                CotaRPVConsumo.cota_rpv_competencia_id,
                func.coalesce(func.sum(CotaRPVConsumo.valor_consumido), 0),
            )
            .filter(
                CotaRPVConsumo.cota_rpv_competencia_id.in_(bucket_ids),
                CotaRPVConsumo.ativo.is_(True),
            )
            .group_by(CotaRPVConsumo.cota_rpv_competencia_id)
            .all()
        )
        return {bucket_id: cls._quantizar(total) for bucket_id, total in rows}

    @classmethod
    def _resumos_buckets(cls, buckets: list[CotaRPVCompetencia]) -> dict[int, dict]:
        bucket_ids = [bucket.id for bucket in buckets]
        mapa_movimentos = cls._mapa_movimentos(bucket_ids)
        mapa_consumos = cls._mapa_consumos_ativos(bucket_ids)
        resumos = {}
        for bucket in buckets:
            lancado = cls._quantizar(mapa_movimentos.get(bucket.id))
            consumido = cls._quantizar(mapa_consumos.get(bucket.id))
            resumos[bucket.id] = {
                "valor_lancado": lancado,
                "valor_consumido": consumido,
                "saldo_disponivel": cls._quantizar(lancado - consumido),
            }
        return resumos

    @classmethod
    def saldo_disponivel_competencia(cls, bucket: CotaRPVCompetencia) -> Decimal:
        resumo = cls._resumos_buckets([bucket]).get(bucket.id, {})
        return cls._quantizar(resumo.get("saldo_disponivel"))

    @classmethod
    def _saldo_fechado_competencia(cls, bucket: CotaRPVCompetencia) -> Decimal:
        limite = cls._limite_fechamento_competencia(bucket.competencia)
        if limite is None:
            return cls.saldo_disponivel_competencia(bucket)

        total_movimentos = (
            db.session.query(func.coalesce(func.sum(CotaRPVMovimento.valor), 0))
            .filter(
                CotaRPVMovimento.cota_rpv_competencia_id == bucket.id,
                CotaRPVMovimento.criado_em < limite,
            )
            .scalar()
        )
        total_consumos = (
            db.session.query(func.coalesce(func.sum(CotaRPVConsumo.valor_consumido), 0))
            .filter(
                CotaRPVConsumo.cota_rpv_competencia_id == bucket.id,
                CotaRPVConsumo.consumido_em < limite,
                or_(
                    CotaRPVConsumo.estornado_em.is_(None),
                    CotaRPVConsumo.estornado_em >= limite,
                ),
            )
            .scalar()
        )
        return cls._quantizar(cls._decimal(total_movimentos) - cls._decimal(total_consumos))

    @classmethod
    def _saldo_operacional_em_instante(
        cls,
        bucket: CotaRPVCompetencia,
        *,
        instante: datetime,
        movimento_id_limite: int | None = None,
    ) -> Decimal:
        filtros_movimentos = [
            CotaRPVMovimento.cota_rpv_competencia_id == bucket.id,
        ]
        if movimento_id_limite is None:
            filtros_movimentos.append(CotaRPVMovimento.criado_em < instante)
        else:
            filtros_movimentos.append(
                or_(
                    CotaRPVMovimento.criado_em < instante,
                    and_(
                        CotaRPVMovimento.criado_em == instante,
                        CotaRPVMovimento.id < movimento_id_limite,
                    ),
                )
            )

        total_movimentos = (
            db.session.query(func.coalesce(func.sum(CotaRPVMovimento.valor), 0))
            .filter(*filtros_movimentos)
            .scalar()
        )
        total_consumos = (
            db.session.query(func.coalesce(func.sum(CotaRPVConsumo.valor_consumido), 0))
            .filter(
                CotaRPVConsumo.cota_rpv_competencia_id == bucket.id,
                CotaRPVConsumo.consumido_em < instante,
                or_(
                    CotaRPVConsumo.estornado_em.is_(None),
                    CotaRPVConsumo.estornado_em >= instante,
                ),
            )
            .scalar()
        )
        return cls._quantizar(cls._decimal(total_movimentos) - cls._decimal(total_consumos))

    @classmethod
    def saldo_transferivel_competencia(cls, bucket: CotaRPVCompetencia) -> Decimal:
        competencia_atual = cls.competencia_atual()
        if bucket.competencia >= competencia_atual:
            return cls.saldo_disponivel_competencia(bucket)

        limite = cls._limite_fechamento_competencia(bucket.competencia)
        if limite is None:
            return cls.saldo_disponivel_competencia(bucket)

        saldo_fechado = cls._saldo_fechado_competencia(bucket)
        movimentos_pos_fechamento = (
            CotaRPVMovimento.query
            .filter(
                CotaRPVMovimento.cota_rpv_competencia_id == bucket.id,
                CotaRPVMovimento.criado_em >= limite,
            )
            .order_by(CotaRPVMovimento.criado_em.asc(), CotaRPVMovimento.id.asc())
            .all()
        )
        saldo_transferivel = saldo_fechado
        for movimento in movimentos_pos_fechamento:
            if movimento.tipo_movimento == cls.TIPO_AJUSTE_MANUAL:
                saldo_operacional_antes = cls._saldo_operacional_em_instante(
                    bucket,
                    instante=movimento.criado_em,
                    movimento_id_limite=movimento.id,
                )
                saldo_transferivel = cls._quantizar(
                    saldo_operacional_antes + cls._decimal(movimento.valor)
                )
                continue
            saldo_transferivel = cls._quantizar(
                saldo_transferivel + cls._decimal(movimento.valor)
            )
        return cls._quantizar(saldo_transferivel)

    @classmethod
    def transferir_saldo_integral(
        cls,
        *,
        origem_competencia_id: int,
        competencia_destino: str,
        usuario_id: int,
        observacoes: str | None = None,
    ) -> Decimal:
        origem_bucket = db.session.get(CotaRPVCompetencia, origem_competencia_id)
        if not origem_bucket:
            raise ValueError("Saldo de origem nao encontrado.")

        destino_normalizado = cls.normalizar_competencia(competencia_destino)
        if not destino_normalizado:
            raise ValueError("Competencia de destino invalida.")
        if destino_normalizado == origem_bucket.competencia:
            raise ValueError("Selecione uma competencia diferente para transferir o saldo.")

        valor_transferencia = cls.saldo_transferivel_competencia(origem_bucket)
        if valor_transferencia <= 0:
            raise ValueError("Nao existe saldo disponivel para transferir nessa ficha.")

        destino_bucket = cls.obter_ou_criar_competencia(
            destino_normalizado,
            origem_bucket.grupo_cota,
            usuario_id=usuario_id,
        )
        origem_bucket.atualizado_por_id = usuario_id
        destino_bucket.atualizado_por_id = usuario_id

        observacao_limpa = (observacoes or "").strip() or None
        db.session.add(
            CotaRPVMovimento(
                cota_rpv_competencia_id=origem_bucket.id,
                tipo_movimento=cls.TIPO_TRANSFERENCIA_SAIDA,
                valor=cls._quantizar(valor_transferencia * Decimal("-1")),
                referencia_competencia=destino_normalizado,
                observacoes=observacao_limpa,
                criado_por_id=usuario_id,
            )
        )
        db.session.add(
            CotaRPVMovimento(
                cota_rpv_competencia_id=destino_bucket.id,
                tipo_movimento=cls.TIPO_TRANSFERENCIA_ENTRADA,
                valor=cls._quantizar(valor_transferencia),
                referencia_competencia=origem_bucket.competencia,
                observacoes=observacao_limpa,
                criado_por_id=usuario_id,
            )
        )
        return cls._quantizar(valor_transferencia)

    @classmethod
    def _medias_historicas_por_grupo(
        cls,
        *,
        competencia_referencia: str,
        janela: int = 3,
    ) -> dict[str, Decimal]:
        referencia = cls.normalizar_competencia(competencia_referencia) or cls.competencia_atual()
        totais_por_competencia = defaultdict(lambda: defaultdict(lambda: Decimal("0.00")))

        registros = (
            RegistroRPV.query.options(joinedload(RegistroRPV.tipo_rpv), joinedload(RegistroRPV.situacao_empenho))
            .filter(RegistroRPV.ativo.is_(True), RegistroRPV.data_pagamento.isnot(None))
            .all()
        )
        for registro in registros:
            if getattr(registro, "status_principal_cancelado", False):
                continue
            competencia = cls.normalizar_competencia(
                registro.data_pagamento.strftime("%Y-%m") if registro.data_pagamento else None
            )
            if not competencia or competencia > referencia:
                continue
            grupo = classificar_grupo_cota(
                getattr(getattr(registro, "tipo_rpv", None), "nome", None),
                "rpv_normal",
            )
            totais_por_competencia[competencia][grupo] += cls._quantizar(registro.valor_bruto)

        lotes = (
            DativoLote.query.options(joinedload(DativoLote.situacao_rpv))
            .filter(
                DativoLote.ativo.is_(True),
                DativoLote.tipo_lote == "sem_irrf",
                DativoLote.data_pagamento.isnot(None),
            )
            .all()
        )
        for lote in lotes:
            if getattr(lote, "status_principal_cancelado", False):
                continue
            competencia = cls.normalizar_competencia(
                lote.data_pagamento.strftime("%Y-%m") if lote.data_pagamento else None
            )
            if not competencia or competencia > referencia:
                continue
            totais_por_competencia[competencia]["comum"] += cls._quantizar(
                lote.valor_total_bruto
            )

        itens = (
            DativoItem.query.options(joinedload(DativoItem.situacao_rpv))
            .filter(
                DativoItem.ativo.is_(True),
                DativoItem.grupo == "com_irrf",
                DativoItem.data_pagamento.isnot(None),
            )
            .all()
        )
        for item in itens:
            if getattr(item, "status_principal_cancelado", False):
                continue
            competencia = cls.normalizar_competencia(
                item.data_pagamento.strftime("%Y-%m") if item.data_pagamento else None
            )
            if not competencia or competencia > referencia:
                continue
            totais_por_competencia[competencia]["comum"] += cls._quantizar(item.valor_bruto)

        competencias = sorted(totais_por_competencia.keys())[-janela:]
        if not competencias:
            return {grupo: Decimal("0.00") for grupo in GRUPOS_COTA_ORDEM}

        medias = {}
        for grupo in GRUPOS_COTA_ORDEM:
            total = sum(
                (totais_por_competencia[competencia].get(grupo, Decimal("0.00")) for competencia in competencias),
                Decimal("0.00"),
            )
            medias[grupo] = cls._quantizar(total / Decimal(len(competencias)))
        return medias

    @classmethod
    def _estado_saldo(
        cls,
        saldo: Decimal,
        media_historica: Decimal,
        valor_lancado: Decimal,
    ) -> str:
        saldo_atual = cls._quantizar(saldo)
        media = cls._quantizar(media_historica)
        if saldo_atual <= 0:
            return "critical"
        percentual_restante = cls._percentual_restante(
            saldo=saldo_atual,
            valor_lancado=valor_lancado,
        )
        if percentual_restante is not None:
            if percentual_restante <= cls._CRITICAL_REMAINING_PERCENT:
                return "critical"
            if percentual_restante <= cls._ATTENTION_REMAINING_PERCENT:
                return "attention"
            # Quando a cota do mes foi lancada e ainda esta saudavel no proprio
            # percentual restante, o historico continua apenas como leitura de contexto.
            return "ok"
        if media <= 0:
            return "ok"
        if saldo_atual < cls._quantizar(media * cls._CRITICAL_MULTIPLIER):
            return "critical"
        if saldo_atual < cls._quantizar(media * cls._ATTENTION_MULTIPLIER):
            return "attention"
        return "ok"

    @classmethod
    def _percentual_restante(
        cls,
        *,
        saldo: Decimal,
        valor_lancado: Decimal | None,
    ) -> Decimal | None:
        lancado = cls._quantizar(valor_lancado)
        if lancado <= 0:
            return None
        percentual = (cls._quantizar(saldo) / lancado) * Decimal("100")
        if percentual < 0:
            percentual = Decimal("0.0")
        return cls._quantizar_percentual(percentual)

    @classmethod
    def _percentual_restante_label(cls, percentual_restante: Decimal | None) -> str:
        if percentual_restante is None:
            return "Sem cota lancada"
        valor = f"{float(percentual_restante):.1f}".replace(".", ",")
        return f"{valor}% restante"

    @classmethod
    def _status_visual_label(cls, status: str) -> str:
        if status == "critical":
            return "Critico"
        if status == "attention":
            return "Sob alerta"
        return "Estavel"

    @classmethod
    def _cobertura_label(cls, saldo: Decimal, media_historica: Decimal) -> str:
        media = cls._quantizar(media_historica)
        if media <= 0:
            return "Sem base historica recente"
        cobertura = saldo / media
        cobertura_float = float(cobertura)
        valor = f"{cobertura_float:.1f}".replace(".", ",")
        sufixo = "mes" if cobertura_float < 2 else "meses"
        return f"Cobre {valor} {sufixo} medio(s)"

    @classmethod
    def listar_saldos_anteriores_pendentes(cls, *, competencia_destino: str) -> list[dict]:
        competencia = cls.normalizar_competencia(competencia_destino) or cls.competencia_atual()
        buckets = (
            CotaRPVCompetencia.query.filter(CotaRPVCompetencia.competencia < competencia)
            .order_by(CotaRPVCompetencia.competencia.asc(), CotaRPVCompetencia.grupo_cota.asc())
            .all()
        )
        if not buckets:
            return []

        pendentes = []
        for bucket in buckets:
            saldo = cls.saldo_transferivel_competencia(bucket)
            if saldo <= 0:
                continue
            pendentes.append(
                {
                    "bucket_id": bucket.id,
                    "competencia": bucket.competencia,
                    "competencia_legivel": cls.competencia_legivel(bucket.competencia),
                    "grupo_cota": bucket.grupo_cota,
                    "grupo_label": bucket.grupo_label,
                    "saldo_disponivel": saldo,
                }
            )
        return pendentes

    @classmethod
    def resumo_competencia(cls, *, competencia: str | None = None) -> dict:
        competencia_normalizada = cls.normalizar_competencia(competencia) or cls.competencia_atual()
        buckets = (
            CotaRPVCompetencia.query.filter_by(competencia=competencia_normalizada)
            .order_by(CotaRPVCompetencia.grupo_cota.asc())
            .all()
        )
        resumos = cls._resumos_buckets(buckets)
        buckets_por_grupo = {bucket.grupo_cota: bucket for bucket in buckets}
        saldos_pendentes = cls.listar_saldos_anteriores_pendentes(
            competencia_destino=competencia_normalizada
        )
        pendentes_por_grupo = defaultdict(lambda: Decimal("0.00"))
        for pendente in saldos_pendentes:
            pendentes_por_grupo[pendente["grupo_cota"]] += cls._quantizar(
                pendente["saldo_disponivel"]
            )

        medias = cls._medias_historicas_por_grupo(competencia_referencia=competencia_normalizada)

        grupos = []
        for grupo in GRUPOS_COTA_ORDEM:
            meta = meta_grupo_cota(grupo)
            bucket = buckets_por_grupo.get(grupo)
            resumo = resumos.get(bucket.id if bucket else None, {})
            lancado = cls._quantizar(resumo.get("valor_lancado"))
            consumido = cls._quantizar(resumo.get("valor_consumido"))
            saldo = cls._quantizar(resumo.get("saldo_disponivel"))
            media = cls._quantizar(medias.get(grupo))
            percentual_restante = cls._percentual_restante(
                saldo=saldo,
                valor_lancado=lancado,
            )
            status = cls._estado_saldo(saldo, media, lancado)
            grupos.append(
                {
                    "key": grupo,
                    "label": meta["label"],
                    "descricao": meta["descricao"],
                    "bucket_id": bucket.id if bucket else None,
                    "valor_lancado": lancado,
                    "valor_consumido": consumido,
                    "saldo_disponivel": saldo,
                    "percentual_restante": percentual_restante,
                    "percentual_restante_label": cls._percentual_restante_label(
                        percentual_restante
                    ),
                    "media_historica": media,
                    "cobertura_label": cls._cobertura_label(saldo, media),
                    "status": status,
                    "status_class": f"is-{status}",
                    "status_visual_label": cls._status_visual_label(status),
                    "saldo_anterior_pendente": cls._quantizar(pendentes_por_grupo[grupo]),
                    "tem_saldo_anterior_pendente": pendentes_por_grupo[grupo] > 0,
                }
            )

        return {
            "competencia": competencia_normalizada,
            "competencia_legivel": cls.competencia_legivel(competencia_normalizada),
            "proxima_competencia": cls.proxima_competencia(competencia_normalizada),
            "grupos": grupos,
            "saldos_anteriores_pendentes": saldos_pendentes,
            "tem_saldo_anterior_pendente": bool(saldos_pendentes),
            "total_grupos_pendentes": len({item["grupo_cota"] for item in saldos_pendentes}),
        }

    @classmethod
    def resumo_home(cls, *, competencia: str | None = None) -> dict:
        resumo = cls.resumo_competencia(competencia=competencia)
        return {
            "competencia": resumo["competencia"],
            "competencia_legivel": resumo["competencia_legivel"],
            "grupos": [
                {
                    "key": grupo["key"],
                    "label": grupo["label"],
                    "saldo_disponivel": grupo["saldo_disponivel"],
                    "status_class": grupo["status_class"],
                    "status_visual_label": grupo["status_visual_label"],
                    "percentual_restante_label": grupo["percentual_restante_label"],
                }
                for grupo in resumo["grupos"]
            ],
            "tem_saldo_anterior_pendente": resumo["tem_saldo_anterior_pendente"],
            "total_grupos_pendentes": resumo["total_grupos_pendentes"],
        }

    @classmethod
    def movimentos_recentes(cls, *, limite: int = 12) -> list[CotaRPVMovimento]:
        return (
            CotaRPVMovimento.query.options(joinedload(CotaRPVMovimento.competencia_ref))
            .order_by(CotaRPVMovimento.criado_em.desc(), CotaRPVMovimento.id.desc())
            .limit(limite)
            .all()
        )

    @classmethod
    def _origens_por_consumo(cls, consumos: list[CotaRPVConsumo]) -> dict[tuple[str, int], object]:
        ids_por_tipo: dict[str, set[int]] = defaultdict(set)
        for consumo in consumos:
            ids_por_tipo[str(consumo.origem_tipo or "")].add(int(consumo.origem_id))

        origens: dict[tuple[str, int], object] = {}
        mapas = {
            "registro_rpv": (RegistroRPV, "id"),
            "dativo_lote": (DativoLote, "id"),
            "dativo_item": (DativoItem, "id"),
        }
        for origem_tipo, ids in ids_por_tipo.items():
            if not ids or origem_tipo not in mapas:
                continue
            modelo, campo = mapas[origem_tipo]
            registros = modelo.query.filter(getattr(modelo, campo).in_(ids)).all()
            for registro in registros:
                origens[(origem_tipo, int(registro.id))] = registro
        return origens

    @classmethod
    def _entidade_esta_paga(cls, entidade) -> bool:
        if entidade is None:
            return False
        if getattr(entidade, "status_principal_cancelado", False):
            return False
        return getattr(entidade, "data_pagamento", None) is not None

    @classmethod
    def painel_consumos_ativos(
        cls,
        *,
        competencia: str | None = None,
        incluir_pagos: bool = False,
    ) -> dict:
        competencia_normalizada = cls.normalizar_competencia(competencia) or cls.competencia_atual()
        consumos = (
            CotaRPVConsumo.query.options(joinedload(CotaRPVConsumo.competencia_ref))
            .join(CotaRPVCompetencia, CotaRPVConsumo.cota_rpv_competencia_id == CotaRPVCompetencia.id)
            .filter(
                CotaRPVConsumo.ativo.is_(True),
                CotaRPVCompetencia.competencia == competencia_normalizada,
            )
            .order_by(CotaRPVConsumo.valor_consumido.desc(), CotaRPVConsumo.consumido_em.desc(), CotaRPVConsumo.id.desc())
            .all()
        )
        origens = cls._origens_por_consumo(consumos)

        itens = []
        pagos_ocultos = 0
        for consumo in consumos:
            entidade = origens.get((str(consumo.origem_tipo or ""), int(consumo.origem_id)))
            esta_pago = cls._entidade_esta_paga(entidade)
            if esta_pago and not incluir_pagos:
                pagos_ocultos += 1
                continue
            bucket = consumo.competencia_ref
            itens.append(
                {
                    "bucket": bucket,
                    "ficha_label": bucket.grupo_label if bucket else "-",
                    "competencia": getattr(bucket, "competencia", "-"),
                    "resumo_origem": consumo.resumo_origem or f"{consumo.origem_tipo} #{consumo.origem_id}",
                    "valor_consumido": consumo.valor_consumido,
                    "esta_pago": esta_pago,
                }
            )

        return {
            "itens": itens,
            "incluir_pagos": incluir_pagos,
            "competencia": competencia_normalizada,
            "competencia_legivel": cls.competencia_legivel(competencia_normalizada),
            "total_competencia": len(consumos),
            "total_exibidos": len(itens),
            "pagos_ocultos": pagos_ocultos,
        }
