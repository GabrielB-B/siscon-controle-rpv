from decimal import Decimal

from app.extensions import db
from app.models import DativoCI, DativoItem, DativoLote, SituacaoEmpenho, SituacaoImposto
from app.utils.domain_profile import get_domain_profile
from app.utils.normalizers import normalizar_documento


class DativosService:
    """
    Servico responsavel pelas regras de negocio do modulo de dativos.
    """

    STATUS_CI_ABERTA = "aberta"
    STATUS_CI_DESCARTADA = "descartada"

    @staticmethod
    def obter_situacao_rpv_inicial() -> SituacaoEmpenho:
        nome = get_domain_profile().situacao_empenho_inicial_nome
        situacao = SituacaoEmpenho.query.filter_by(nome=nome).first()
        if not situacao:
            raise ValueError(f"Situacao inicial do RPV '{nome}' nao encontrada.")
        return situacao

    @staticmethod
    def obter_situacao_imposto_sem_tratamento() -> SituacaoImposto:
        nome = get_domain_profile().situacao_imposto_inicial_nome
        situacao = SituacaoImposto.query.filter_by(nome=nome).first()
        if not situacao:
            raise ValueError(f"Situacao inicial do imposto '{nome}' nao encontrada.")
        return situacao

    @staticmethod
    def obter_situacao_imposto_sem_irrf() -> SituacaoImposto:
        nome = get_domain_profile().situacao_imposto_sem_irrf_nome
        situacao = SituacaoImposto.query.filter_by(nome=nome).first()
        if not situacao:
            raise ValueError(f"Situacao do imposto '{nome}' nao encontrada.")
        return situacao

    @staticmethod
    def criar_ci_dativo(
        exercicio: str,
        processo_edoc: str,
        data_ci,
        usuario_id: int,
        responsavel_id: int | None = None,
        descricao: str = "Dativo Geral",
    ) -> DativoCI:
        existente = DativoCI.query.filter_by(processo_edoc=processo_edoc).first()
        if existente:
            raise ValueError("Ja existe uma C.I. de dativo cadastrada com esse numero.")

        dativo_ci = DativoCI(
            exercicio=exercicio,
            processo_edoc=processo_edoc,
            data_ci=data_ci,
            status=DativosService.STATUS_CI_ABERTA,
            descricao=descricao,
            criado_por_id=usuario_id,
            responsavel_id=responsavel_id or usuario_id,
            atualizado_por_id=usuario_id,
        )
        db.session.add(dativo_ci)
        db.session.flush()

        return dativo_ci

    @staticmethod
    def validar_ci_sem_movimentacao(dativo_ci: DativoCI) -> None:
        if dativo_ci.possui_movimentacao_ativa:
            raise ValueError(
                "Esta C.I. ja possui lote ou itens cadastrados. "
                "O cabecalho nao pode ser corrigido ou cancelado depois da movimentacao."
            )

    @staticmethod
    def atualizar_ci_dativo(
        dativo_ci: DativoCI,
        *,
        exercicio: str,
        processo_edoc: str,
        data_ci,
        responsavel_id: int,
        usuario_id: int,
        descricao: str | None = None,
    ) -> DativoCI:
        DativosService.validar_ci_sem_movimentacao(dativo_ci)

        processo_edoc_limpo = str(processo_edoc or "").strip()
        existente = (
            DativoCI.query.filter(
                DativoCI.processo_edoc == processo_edoc_limpo,
                DativoCI.id != dativo_ci.id,
            )
            .order_by(DativoCI.id.asc())
            .first()
        )
        if existente:
            raise ValueError(
                "Ja existe outra C.I. de dativo cadastrada com esse numero. "
                "Abra a C.I. existente para revisar, corrigir ou reabrir o cabecalho certo."
            )

        dativo_ci.exercicio = exercicio
        dativo_ci.processo_edoc = processo_edoc_limpo
        dativo_ci.data_ci = data_ci
        dativo_ci.responsavel_id = responsavel_id
        if descricao is not None:
            dativo_ci.descricao = str(descricao).strip() or "Dativo Geral"
        dativo_ci.atualizado_por_id = usuario_id
        db.session.flush()
        return dativo_ci

    @staticmethod
    def descartar_ci_dativo(dativo_ci: DativoCI, *, usuario_id: int) -> DativoCI:
        DativosService.validar_ci_sem_movimentacao(dativo_ci)
        if dativo_ci.status_normalizado == DativosService.STATUS_CI_DESCARTADA:
            raise ValueError("Esta C.I. ja esta cancelada.")

        dativo_ci.status = DativosService.STATUS_CI_DESCARTADA
        dativo_ci.atualizado_por_id = usuario_id
        db.session.flush()
        return dativo_ci

    @staticmethod
    def reabrir_ci_dativo(dativo_ci: DativoCI, *, usuario_id: int) -> DativoCI:
        DativosService.validar_ci_sem_movimentacao(dativo_ci)
        if dativo_ci.status_normalizado == DativosService.STATUS_CI_ABERTA:
            raise ValueError("Esta C.I. ja esta aberta.")

        dativo_ci.status = DativosService.STATUS_CI_ABERTA
        dativo_ci.atualizado_por_id = usuario_id
        db.session.flush()
        return dativo_ci

    @staticmethod
    def obter_ou_criar_lote_sem_irrf(dativo_ci: DativoCI, usuario_id: int) -> DativoLote:
        lote = DativoLote.query.filter_by(
            dativo_ci_id=dativo_ci.id,
            tipo_lote="sem_irrf",
        ).first()

        if lote:
            return lote

        lote = DativoLote(
            dativo_ci_id=dativo_ci.id,
            tipo_lote="sem_irrf",
            quantidade_itens=0,
            valor_total_bruto=Decimal("0.00"),
            valor_total_irrf=Decimal("0.00"),
            valor_total_liquido=Decimal("0.00"),
            nota_empenho=None,
            numero_se=None,
            ordem_bancaria=None,
            situacao_rpv_id=DativosService.obter_situacao_rpv_inicial().id,
            situacao_imposto_id=DativosService.obter_situacao_imposto_sem_irrf().id,
            resumo_operacional="",
            observacoes=None,
            ativo=True,
            criado_por_id=usuario_id,
            atualizado_por_id=usuario_id,
        )

        db.session.add(lote)
        db.session.flush()

        lote.gerar_resumo_operacional()
        return lote

    @staticmethod
    def mensagem_duplicidade_item() -> str:
        return "Ja existe um item com esse documento e processo nesse grupo do dativo."

    @staticmethod
    def eh_erro_duplicidade_item(exc) -> bool:
        texto = str(getattr(exc, "orig", exc) or "").lower()
        return (
            "uq_dativos_itens_ci_grupo_doc_processo" in texto
            or (
                "dativos_itens.dativo_ci_id, dativos_itens.grupo, "
                "dativos_itens.cpf_normalizado, dativos_itens.numero_processo"
            )
            in texto
        )

    @staticmethod
    def buscar_duplicidade_item(
        dativo_ci_id: int,
        grupo: str,
        documento: str,
        numero_processo: str,
        item_id_excluir: int | None = None,
    ) -> DativoItem | None:
        documento_normalizado = normalizar_documento(documento)
        processo_limpo = str(numero_processo or "").strip()

        query = DativoItem.query.filter_by(
            dativo_ci_id=dativo_ci_id,
            grupo=grupo,
            cpf_normalizado=documento_normalizado,
            numero_processo=processo_limpo,
            ativo=True,
        )

        if item_id_excluir is not None:
            query = query.filter(DativoItem.id != item_id_excluir)

        itens = query.order_by(DativoItem.criado_em.asc(), DativoItem.id.asc()).all()
        return next(
            (item for item in itens if not getattr(item, "status_principal_cancelado", False)),
            None,
        )

    @staticmethod
    def _verificar_duplicidade_item(
        dativo_ci_id: int,
        grupo: str,
        documento: str,
        numero_processo: str,
        item_id_excluir: int | None = None,
        permitir_duplicidade_confirmada: bool = False,
    ):
        item_existente = DativosService.buscar_duplicidade_item(
            dativo_ci_id=dativo_ci_id,
            grupo=grupo,
            documento=documento,
            numero_processo=numero_processo,
            item_id_excluir=item_id_excluir,
        )

        if item_existente and not permitir_duplicidade_confirmada:
            raise ValueError(DativosService.mensagem_duplicidade_item())

        return item_existente

    @staticmethod
    def adicionar_item_sem_irrf(
        dativo_ci: DativoCI,
        nome_beneficiario: str,
        cpf_original: str,
        numero_processo: str,
        valor_bruto,
        usuario_id: int,
        observacoes: str | None = None,
        permitir_duplicidade_confirmada: bool = False,
    ) -> DativoItem:
        DativosService._verificar_duplicidade_item(
            dativo_ci_id=dativo_ci.id,
            grupo="sem_irrf",
            documento=cpf_original,
            numero_processo=numero_processo,
            permitir_duplicidade_confirmada=permitir_duplicidade_confirmada,
        )

        lote = DativosService.obter_ou_criar_lote_sem_irrf(dativo_ci, usuario_id)

        item = DativoItem(
            dativo_ci_id=dativo_ci.id,
            dativo_lote_id=lote.id,
            grupo="sem_irrf",
            nome_beneficiario=nome_beneficiario,
            nome_beneficiario_normalizado="",
            cpf_original=cpf_original,
            cpf_normalizado="",
            numero_processo=str(numero_processo).strip(),
            data_pagamento=lote.data_pagamento,
            reinf_status=None,
            dispensa_irrf_confirmada=False,
            valor_bruto=valor_bruto,
            valor_irrf=Decimal("0.00"),
            valor_liquido=Decimal("0.00"),
            nota_empenho=lote.nota_empenho,
            numero_se=lote.numero_se,
            ordem_bancaria=lote.ordem_bancaria,
            ob_imposto=None,
            situacao_rpv_id=lote.situacao_rpv_id,
            situacao_imposto_id=lote.situacao_imposto_id,
            resumo_operacional="",
            observacoes=observacoes or None,
            ativo=True,
            criado_por_id=usuario_id,
            atualizado_por_id=usuario_id,
        )

        item.dativo_ci = dativo_ci
        item.dativo_lote = lote
        item.atualizar_campos_derivados()
        item.gerar_resumo_operacional(
            processo_edoc=dativo_ci.processo_edoc,
            data_ci=dativo_ci.data_ci,
        )

        db.session.add(item)
        db.session.flush()

        DativosService.atualizar_totais_lote(lote, usuario_id=usuario_id)

        return item

    @staticmethod
    def adicionar_item_com_irrf(
        dativo_ci: DativoCI,
        nome_beneficiario: str,
        cpf_original: str,
        numero_processo: str,
        valor_bruto,
        valor_irrf,
        usuario_id: int,
        observacoes: str | None = None,
        permitir_duplicidade_confirmada: bool = False,
    ) -> DativoItem:
        DativosService._verificar_duplicidade_item(
            dativo_ci_id=dativo_ci.id,
            grupo="com_irrf",
            documento=cpf_original,
            numero_processo=numero_processo,
            permitir_duplicidade_confirmada=permitir_duplicidade_confirmada,
        )

        item = DativoItem(
            dativo_ci_id=dativo_ci.id,
            dativo_lote_id=None,
            grupo="com_irrf",
            nome_beneficiario=nome_beneficiario,
            nome_beneficiario_normalizado="",
            cpf_original=cpf_original,
            cpf_normalizado="",
            numero_processo=str(numero_processo).strip(),
            data_pagamento=None,
            reinf_status=None,
            valor_bruto=valor_bruto,
            valor_irrf=valor_irrf,
            valor_liquido=Decimal("0.00"),
            nota_empenho=None,
            numero_se=None,
            ordem_bancaria=None,
            ob_imposto=None,
            situacao_rpv_id=DativosService.obter_situacao_rpv_inicial().id,
            situacao_imposto_id=DativosService.obter_situacao_imposto_sem_tratamento().id,
            resumo_operacional="",
            observacoes=observacoes or None,
            ativo=True,
            criado_por_id=usuario_id,
            atualizado_por_id=usuario_id,
        )

        item.dativo_ci = dativo_ci
        item.atualizar_campos_derivados()
        item.gerar_resumo_operacional(
            processo_edoc=dativo_ci.processo_edoc,
            data_ci=dativo_ci.data_ci,
        )

        db.session.add(item)
        db.session.flush()

        return item

    @staticmethod
    def sincronizar_lote_sem_irrf_com_itens(
        lote: DativoLote,
        *,
        usuario_id: int | None = None,
    ) -> DativoLote:
        if lote.tipo_lote != "sem_irrf":
            return lote

        situacao_imposto_sem_irrf_id = DativosService.obter_situacao_imposto_sem_irrf().id

        for item in lote.itens:
            if item.grupo != "sem_irrf" or not item.ativo:
                continue

            item.dativo_lote_id = lote.id
            item.data_pagamento = lote.data_pagamento
            item.nota_empenho = lote.nota_empenho or None
            item.numero_se = lote.numero_se or None
            item.ordem_bancaria = lote.ordem_bancaria or None
            item.situacao_rpv_id = lote.situacao_rpv_id
            item.situacao_imposto_id = situacao_imposto_sem_irrf_id
            if usuario_id is not None:
                item.atualizado_por_id = usuario_id

        return lote

    @staticmethod
    def editar_item_sem_irrf(
        item: DativoItem,
        nome_beneficiario: str,
        cpf_original: str,
        numero_processo: str,
        valor_bruto,
        usuario_id: int,
        data_pagamento=None,
        nota_empenho: str | None = None,
        ordem_bancaria: str | None = None,
        ob_imposto: str | None = None,
        observacoes: str | None = None,
        situacao_rpv_id: int | None = None,
        situacao_imposto_id: int | None = None,
        dispensa_irrf_confirmada: bool | None = None,
        permitir_duplicidade_confirmada: bool = False,
    ) -> DativoItem:
        if item.grupo != "sem_irrf":
            raise ValueError("Item informado nao pertence ao lote sem IRRF.")

        if not nome_beneficiario or not cpf_original or not numero_processo:
            raise ValueError("Nome, documento e processo sao obrigatorios.")

        if valor_bruto is None:
            raise ValueError("Valor bruto e obrigatorio.")

        DativosService._verificar_duplicidade_item(
            dativo_ci_id=item.dativo_ci_id,
            grupo="sem_irrf",
            documento=cpf_original,
            numero_processo=numero_processo,
            item_id_excluir=item.id,
            permitir_duplicidade_confirmada=permitir_duplicidade_confirmada,
        )

        item.nome_beneficiario = nome_beneficiario
        item.cpf_original = cpf_original
        item.numero_processo = str(numero_processo).strip()
        item.valor_bruto = valor_bruto
        item.data_pagamento = data_pagamento
        item.nota_empenho = (
            nota_empenho
            if nota_empenho is not None
            else getattr(getattr(item, "dativo_lote", None), "nota_empenho", None)
        ) or None
        item.ordem_bancaria = (
            ordem_bancaria
            if ordem_bancaria is not None
            else getattr(getattr(item, "dativo_lote", None), "ordem_bancaria", None)
        ) or None
        item.ob_imposto = ob_imposto or None
        item.observacoes = observacoes or None
        item.atualizado_por_id = usuario_id
        if dispensa_irrf_confirmada is not None:
            item.dispensa_irrf_confirmada = bool(dispensa_irrf_confirmada)

        if situacao_rpv_id is not None:
            item.situacao_rpv_id = int(situacao_rpv_id)

        if situacao_imposto_id is not None:
            item.situacao_imposto_id = int(situacao_imposto_id)

        item.atualizar_campos_derivados()
        item.gerar_resumo_operacional(
            processo_edoc=item.dativo_ci.processo_edoc if item.dativo_ci else None,
            data_ci=item.dativo_ci.data_ci if item.dativo_ci else None,
        )

        return item

    @staticmethod
    def atualizar_totais_lote(lote: DativoLote, usuario_id: int | None = None) -> DativoLote:
        lote.atualizar_totais()
        lote.gerar_resumo_operacional()
        if usuario_id is not None:
            lote.atualizado_por_id = usuario_id

        return lote
