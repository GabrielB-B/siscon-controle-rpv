import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

import pandas as pd
from flask import current_app, url_for
from sqlalchemy.orm import joinedload

from app.models import DativoItem
from app.services.processo_crosscheck_service import ProcessoCrosscheckService
from app.services.dativos_service import DativosService
from app.utils.datetime_utils import utc_now_naive
from app.utils.formatters import detectar_tipo_documento, formatar_documento_br
from app.utils.normalizers import normalizar_documento, normalizar_nome, normalizar_numero_processo

LIMITE_ALERTA_IRRF = Decimal("5040.00")


class DativosImportService:
    """
    Importacao assistida de planilhas ODS do modulo de dativos.

    Regras importantes:
    - aceita CPF e CNPJ na mesma coluna
    - ignora colunas de ordem/indice sem significado operacional
    - ignora rodapes e linhas de total
    - nao cria lote vazio
    """

    PREVIEW_EXPIRATION_HOURS = 12

    COLUNAS_OBRIGATORIAS = {
        "nome": {
            "nome",
            "nome beneficiario",
            "beneficiario",
            "favorecido",
        },
        "documento": {
            "cpf",
            "cnpj",
            "cpf cnpj",
            "cpf/cnpj",
            "cpf ou cnpj",
            "cpfcnpj",
            "documento",
            "documento beneficiario",
        },
        "processo": {
            "processo",
            "numero processo",
            "numero do processo",
            "n processo",
            "processo judicial",
            "numero processo judicial",
            "número processo",
            "nproc",
        },
        "valor": {
            "valor",
            "valor bruto",
            "valor rpv",
            "valor principal",
            "valor liquido",
            "valor líquido",
        },
    }

    CABECALHOS_IGNORADOS = {
        "",
        "/",
        "-",
        ".",
        "#",
        "n",
        "n.",
        "no",
        "ordem",
        "ordem interna",
        "item",
        "itens",
        "linha",
        "linhas",
        "sequencia",
        "sequência",
        "seq",
        "indice",
        "índice",
    }

    @staticmethod
    def _normalizar_cabecalho(valor: str) -> str:
        """
        Normaliza o texto do cabecalho para comparacao segura.
        """
        texto = str(valor or "")
        texto = texto.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        texto = " ".join(texto.split())
        texto = normalizar_nome(texto)
        texto = texto.replace("_", " ").strip().lower()
        texto = re.sub(r"[^a-z0-9 ]", " ", texto)
        texto = " ".join(texto.split()).strip()

        # Colunas automáticas tipo "Unnamed: 0" ou equivalentes devem ser ignoradas
        if texto.startswith("unnamed"):
            return ""

        return texto

    @staticmethod
    def _eh_cabecalho_ignorado(cabecalho_normalizado: str) -> bool:
        """
        Define se um cabecalho deve ser descartado do processo de mapeamento.
        """
        if not cabecalho_normalizado:
            return True

        return cabecalho_normalizado in DativosImportService.CABECALHOS_IGNORADOS

    @staticmethod
    def _cabecalho_combina_exato(cabecalho_normalizado: str, aliases: set[str]) -> bool:
        """
        Preferencia por combinacao exata para evitar encaixes errados.
        """
        if DativosImportService._eh_cabecalho_ignorado(cabecalho_normalizado):
            return False

        return cabecalho_normalizado in aliases

    @staticmethod
    def _cabecalho_combina_parcial(cabecalho_normalizado: str, aliases: set[str]) -> bool:
        """
        Combinacao parcial controlada, usada apenas como fallback.
        """
        if DativosImportService._eh_cabecalho_ignorado(cabecalho_normalizado):
            return False

        for alias in aliases:
            alias = str(alias or "").strip().lower()
            if not alias:
                continue

            if cabecalho_normalizado == alias:
                return True

            if cabecalho_normalizado.startswith(f"{alias} "):
                return True

            if cabecalho_normalizado.endswith(f" {alias}"):
                return True

            if f" {alias} " in f" {cabecalho_normalizado} ":
                return True

        return False

    @staticmethod
    def _mapear_colunas(colunas):
        """
        Mapeia as colunas obrigatorias da planilha.

        Estrategia:
        1. tenta casar por nome exato
        2. se nao achar, tenta parcial controlado
        3. ignora colunas vazias, simbolicas ou de indice
        """
        colunas_normalizadas = [
            (coluna_original, DativosImportService._normalizar_cabecalho(coluna_original))
            for coluna_original in colunas
        ]

        mapeamento = {}
        colunas_usadas = set()

        for campo, aliases in DativosImportService.COLUNAS_OBRIGATORIAS.items():
            coluna_encontrada = None

            for coluna_original, coluna_norm in colunas_normalizadas:
                if coluna_original in colunas_usadas:
                    continue
                if DativosImportService._cabecalho_combina_exato(coluna_norm, aliases):
                    coluna_encontrada = coluna_original
                    break

            if coluna_encontrada is None:
                for coluna_original, coluna_norm in colunas_normalizadas:
                    if coluna_original in colunas_usadas:
                        continue
                    if DativosImportService._cabecalho_combina_parcial(coluna_norm, aliases):
                        coluna_encontrada = coluna_original
                        break

            if coluna_encontrada is None:
                cabecalhos_lidos = ", ".join(
                    [f"'{valor}'" for _, valor in colunas_normalizadas]
                )
                raise ValueError(
                    f"Coluna obrigatoria nao encontrada na planilha: {campo}. "
                    f"Cabecalhos lidos: {cabecalhos_lidos}"
                )

            mapeamento[campo] = coluna_encontrada
            colunas_usadas.add(coluna_encontrada)

        return mapeamento

    @staticmethod
    def _texto_limpo(valor) -> str:
        if pd.isna(valor):
            return ""

        if isinstance(valor, (int, float)):
            if isinstance(valor, float) and valor.is_integer():
                valor = int(valor)

        return str(valor).strip()

    @staticmethod
    def _remover_sufixo_decimal_zero(texto: str) -> str:
        texto = str(texto or "").strip()
        if texto.endswith(".0"):
            texto = texto[:-2]
        return texto.strip()

    @staticmethod
    def _normalizar_processo(valor) -> str:
        texto = DativosImportService._texto_limpo(valor)
        texto = DativosImportService._remover_sufixo_decimal_zero(texto)
        return texto.strip()

    @staticmethod
    def _normalizar_valor_decimal(valor) -> Decimal:
        if pd.isna(valor) or valor == "":
            raise ValueError("Valor em branco.")

        if isinstance(valor, (int, float, Decimal)):
            return Decimal(str(valor))

        texto = str(valor).strip().replace("R$", "").replace(" ", "")

        if "," in texto and "." in texto:
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", ".")

        try:
            return Decimal(texto)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Valor invalido: {valor}") from exc

    @staticmethod
    def _normalizar_documento_generico(documento) -> str:
        texto = DativosImportService._texto_limpo(documento)
        texto = DativosImportService._remover_sufixo_decimal_zero(texto)

        doc = normalizar_documento(texto)

        if len(doc) not in (11, 14):
            raise ValueError(f"Documento invalido: {documento}")

        return doc

    @staticmethod
    def _linha_rodape_ou_total(nome: str, documento: str, processo: str, valor: str) -> bool:
        """
        Ignora linhas tipicas de rodape/somatorio.
        """
        nome_norm = normalizar_nome(nome or "").strip().lower()
        processo_norm = normalizar_nome(processo or "").strip().lower()
        documento_limpo = normalizar_documento(documento or "")
        valor_txt = str(valor or "").strip().lower()

        termos_rodape = {
            "total",
            "total geral",
            "somatorio",
            "somatório",
            "soma",
        }

        if not nome_norm and not documento_limpo and not processo_norm:
            return True

        if nome_norm in termos_rodape or processo_norm in termos_rodape:
            return True

        if (
            ("total" in nome_norm or "soma" in nome_norm or "somatorio" in nome_norm or "somatório" in nome_norm)
            and not documento_limpo
        ):
            return True

        if documento_limpo == "" and processo_norm == "" and valor_txt != "":
            return True

        return False

    @staticmethod
    def _obter_chaves_existentes(dativo_ci_id: int, grupo: str):
        itens = DativoItem.query.filter_by(dativo_ci_id=dativo_ci_id, grupo=grupo).all()
        return {
            (item.cpf_normalizado, item.numero_processo)
            for item in itens
        }

    @staticmethod
    def _validar_mapeamento_com_amostra(df: pd.DataFrame, mapeamento: dict):
        """
        Faz uma validacao defensiva do mapeamento antes de importar.

        Objetivo:
        evitar que coluna de indice/contador seja confundida com "nome".
        """
        if df.empty:
            return

        amostra = df.head(12)

        nomes = [
            DativosImportService._texto_limpo(valor)
            for valor in amostra[mapeamento["nome"]].tolist()
        ]
        nomes_preenchidos = [valor for valor in nomes if valor]

        if nomes_preenchidos:
            nomes_curto_numericos = [
                valor for valor in nomes_preenchidos
                if re.fullmatch(r"\d{1,6}", valor)
            ]

            if len(nomes_curto_numericos) >= max(3, len(nomes_preenchidos) // 2):
                raise ValueError(
                    "A coluna de NOME foi identificada incorretamente. "
                    "A planilha parece estar usando uma coluna de ordem/indice no lugar do beneficiario."
                )

        documentos = [
            DativosImportService._texto_limpo(valor)
            for valor in amostra[mapeamento["documento"]].tolist()
        ]
        documentos_preenchidos = [valor for valor in documentos if valor]

        if documentos_preenchidos:
            documentos_validos = 0
            for valor in documentos_preenchidos:
                try:
                    doc = DativosImportService._normalizar_documento_generico(valor)
                    if len(doc) in (11, 14):
                        documentos_validos += 1
                except Exception:
                    continue

            if documentos_validos == 0:
                raise ValueError(
                    "A coluna de DOCUMENTO foi identificada incorretamente. "
                    "Nao foi possivel reconhecer CPF/CNPJ na amostra lida."
                )

    @staticmethod
    def _formatar_decimal_ptbr(valor: Decimal) -> str:
        texto = f"{Decimal(valor or 0):,.2f}"
        return texto.replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def _label_grupo(grupo: str) -> str:
        return "Lote sem IRRF" if grupo == "sem_irrf" else "Item com IRRF"

    @staticmethod
    def _destino_automatico(documento: str, valor: Decimal) -> tuple[str, str, str]:
        tipo_documento = detectar_tipo_documento(documento)

        if tipo_documento == "CNPJ":
            return (
                "sem_irrf",
                "Lote sem IRRF",
                "CNPJ permanece no lote sem IRRF, mesmo acima do corte operacional.",
            )

        if Decimal(valor or 0) > LIMITE_ALERTA_IRRF:
            return (
                "com_irrf",
                "Item com IRRF",
                "CPF acima de 5040 segue automaticamente para o fluxo com IRRF.",
            )

        return (
            "sem_irrf",
            "Lote sem IRRF",
            "CPF ate 5040 permanece no fluxo sem IRRF.",
        )

    @staticmethod
    def _preview_storage_dir() -> str:
        diretorio = os.path.join(current_app.instance_path, "import_previews")
        os.makedirs(diretorio, exist_ok=True)
        return diretorio

    @staticmethod
    def _preview_storage_path(token: str) -> str:
        token_limpo = re.sub(r"[^a-zA-Z0-9_-]", "", str(token or "").strip())
        return os.path.join(
            DativosImportService._preview_storage_dir(),
            f"dativos_import_preview_{token_limpo}.json",
        )

    @staticmethod
    def _abrir_url_item_existente(item: DativoItem) -> str | None:
        if item.grupo == "sem_irrf":
            if item.dativo_lote_id:
                return url_for(
                    "dativos.editar_item_lote",
                    lote_id=item.dativo_lote_id,
                    item_id=item.id,
                )
            return url_for("dativos.detalhe_ci", ci_id=item.dativo_ci_id)

        return url_for("dativos.detalhe_item_com_irrf", item_id=item.id)

    @staticmethod
    def _resumo_item_existente(item: DativoItem) -> dict:
        return {
            "grupo": DativosImportService._label_grupo(item.grupo),
            "beneficiario": item.nome_beneficiario,
            "documento": formatar_documento_br(item.cpf_original, item.tipo_documento_efetivo),
            "processo": item.numero_processo,
            "resumo_operacional": item.resumo_operacional_atual,
            "abrir_url": DativosImportService._abrir_url_item_existente(item),
        }

    @staticmethod
    def _resumo_ocorrencias_processo(ocorrencias: list[dict], limite: int = 3) -> list[dict]:
        return [
            {
                "origem": ocorrencia.get("origem") or "-",
                "responsavel": ocorrencia.get("responsavel") or "-",
                "resumo_operacional": ocorrencia.get("resumo_operacional") or "-",
                "abrir_url": ocorrencia.get("abrir_url"),
            }
            for ocorrencia in ocorrencias[:limite]
        ]

    @staticmethod
    def _carregar_itens_existentes_ci(dativo_ci_id: int) -> list[DativoItem]:
        itens = (
            DativoItem.query.options(
                joinedload(DativoItem.situacao_rpv),
                joinedload(DativoItem.dativo_ci),
                joinedload(DativoItem.dativo_lote),
            )
            .filter(DativoItem.dativo_ci_id == dativo_ci_id, DativoItem.ativo.is_(True))
            .order_by(DativoItem.criado_em.asc(), DativoItem.id.asc())
            .all()
        )
        return [
            item for item in itens
            if not getattr(item, "status_principal_cancelado", False)
        ]

    @staticmethod
    def analisar_ods_unico(arquivo, dativo_ci) -> dict:
        df = pd.read_excel(arquivo, engine="odf", dtype=str)
        mapeamento = DativosImportService._mapear_colunas(df.columns)
        DativosImportService._validar_mapeamento_com_amostra(df, mapeamento)

        itens_existentes = DativosImportService._carregar_itens_existentes_ci(dativo_ci.id)
        itens_existentes_por_grupo = {}
        itens_existentes_por_chave = {}

        for item in itens_existentes:
            chave = (item.cpf_normalizado, item.numero_processo)
            itens_existentes_por_chave.setdefault(chave, []).append(item)
            itens_existentes_por_grupo.setdefault(
                (item.grupo, item.cpf_normalizado, item.numero_processo),
                item,
            )

        prontas_sem_irrf = []
        prontas_com_irrf = []
        pendencias = []
        erros = []
        rodapes_ignorados = 0
        processos_com_ocorrencia = 0
        cnpjs_mantidos_sem_irrf = 0
        chaves_vistas_por_grupo = {}

        for indice, row in df.iterrows():
            linha_excel = indice + 2

            try:
                nome_bruto = DativosImportService._texto_limpo(row[mapeamento["nome"]])
                documento_bruto = DativosImportService._texto_limpo(row[mapeamento["documento"]])
                processo_bruto = DativosImportService._texto_limpo(row[mapeamento["processo"]])
                valor_bruto_raw = DativosImportService._texto_limpo(row[mapeamento["valor"]])

                if DativosImportService._linha_rodape_ou_total(
                    nome=nome_bruto,
                    documento=documento_bruto,
                    processo=processo_bruto,
                    valor=valor_bruto_raw,
                ):
                    rodapes_ignorados += 1
                    continue

                nome = nome_bruto
                documento = DativosImportService._normalizar_documento_generico(documento_bruto)
                processo = DativosImportService._normalizar_processo(processo_bruto)
                valor = DativosImportService._normalizar_valor_decimal(valor_bruto_raw)

                if not nome or not processo:
                    raise ValueError("Nome ou processo em branco.")

                tipo_documento = detectar_tipo_documento(documento)
                destino_grupo, destino_label, regra_aplicada = DativosImportService._destino_automatico(
                    documento,
                    valor,
                )
                if tipo_documento == "CNPJ" and valor > LIMITE_ALERTA_IRRF:
                    cnpjs_mantidos_sem_irrf += 1

                chave = (documento, processo)
                chave_destino = (destino_grupo, documento, processo)
                primeira_linha_mesmo_destino = chaves_vistas_por_grupo.get(chave_destino)

                item_existente_mesmo_destino = itens_existentes_por_grupo.get(chave_destino)
                itens_existentes_outro_grupo = [
                    item
                    for item in itens_existentes_por_chave.get(chave, [])
                    if item.grupo != destino_grupo
                ]

                ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(processo)
                if ocorrencias_processo:
                    processos_com_ocorrencia += 1

                motivos_pendencia = []
                detalhes_pendencia = []
                referencias_existentes = []

                if ocorrencias_processo:
                    motivos_pendencia.append("Processo ja encontrado no sistema")
                    detalhes_pendencia.append(
                        "Esse numero de processo ja aparece em outro contexto do sistema e exige confirmacao explicita."
                    )

                if primeira_linha_mesmo_destino is not None:
                    motivos_pendencia.append("Linha repetida na mesma classificacao")
                    detalhes_pendencia.append(
                        f"Repete documento e processo da linha {primeira_linha_mesmo_destino} no mesmo destino."
                    )

                if item_existente_mesmo_destino is not None:
                    motivos_pendencia.append("Ja existe registro igual no mesmo destino desta C.I.")
                    detalhes_pendencia.append(
                        "O mesmo documento e processo ja existem no grupo sugerido para esta C.I."
                    )
                    referencias_existentes.append(
                        DativosImportService._resumo_item_existente(item_existente_mesmo_destino)
                    )

                if itens_existentes_outro_grupo:
                    motivos_pendencia.append("Ja existe no outro fluxo desta C.I.")
                    detalhes_pendencia.append(
                        "O mesmo documento e processo ja aparecem no outro grupo desta C.I.; confirme antes de repetir."
                    )
                    for item_existente in itens_existentes_outro_grupo[:3]:
                        referencias_existentes.append(
                            DativosImportService._resumo_item_existente(item_existente)
                        )

                linha_preview = {
                    "preview_id": uuid4().hex,
                    "line_number": linha_excel,
                    "nome": nome,
                    "documento": documento,
                    "documento_formatado": formatar_documento_br(documento, tipo_documento),
                    "tipo_documento": tipo_documento,
                    "processo": processo,
                    "valor": str(valor),
                    "valor_legivel": DativosImportService._formatar_decimal_ptbr(valor),
                    "destino_grupo": destino_grupo,
                    "destino_label": destino_label,
                    "regra_aplicada": regra_aplicada,
                    "motivo_pendencia": " | ".join(motivos_pendencia),
                    "detalhe_pendencia": " ".join(detalhes_pendencia),
                    "requer_confirmacao": bool(motivos_pendencia),
                    "ocorrencias_processo_total": len(ocorrencias_processo),
                    "ocorrencias_processo": DativosImportService._resumo_ocorrencias_processo(
                        ocorrencias_processo
                    ),
                    "referencias_existentes": referencias_existentes,
                }

                chaves_vistas_por_grupo.setdefault(chave_destino, linha_excel)

                if linha_preview["requer_confirmacao"]:
                    pendencias.append(linha_preview)
                    continue

                if destino_grupo == "sem_irrf":
                    prontas_sem_irrf.append(linha_preview)
                else:
                    prontas_com_irrf.append(linha_preview)

            except Exception as exc:
                erros.append(f"Linha {linha_excel}: {exc}")

        return {
            "resumo": {
                "total_prontas_sem_irrf": len(prontas_sem_irrf),
                "total_prontas_com_irrf": len(prontas_com_irrf),
                "total_pendencias": len(pendencias),
                "total_erros": len(erros),
                "rodapes_ignorados": rodapes_ignorados,
                "processos_com_ocorrencia": processos_com_ocorrencia,
                "cnpjs_mantidos_sem_irrf": cnpjs_mantidos_sem_irrf,
                "total_linhas_classificadas": (
                    len(prontas_sem_irrf) + len(prontas_com_irrf) + len(pendencias)
                ),
            },
            "prontas_sem_irrf": prontas_sem_irrf,
            "prontas_com_irrf": prontas_com_irrf,
            "pendencias": pendencias,
            "erros": erros,
        }

    @staticmethod
    def analisar_ods_grupo_fixo(arquivo, dativo_ci, grupo: str) -> dict:
        if grupo not in {"sem_irrf", "com_irrf"}:
            raise ValueError("Grupo de importacao invalido.")

        df = pd.read_excel(arquivo, engine="odf", dtype=str)
        mapeamento = DativosImportService._mapear_colunas(df.columns)
        DativosImportService._validar_mapeamento_com_amostra(df, mapeamento)

        itens_existentes = DativosImportService._carregar_itens_existentes_ci(dativo_ci.id)
        itens_existentes_por_grupo = {}
        itens_existentes_por_chave = {}

        for item in itens_existentes:
            chave = (item.cpf_normalizado, item.numero_processo)
            itens_existentes_por_chave.setdefault(chave, []).append(item)
            itens_existentes_por_grupo.setdefault(
                (item.grupo, item.cpf_normalizado, item.numero_processo),
                item,
            )

        prontas_sem_irrf = []
        prontas_com_irrf = []
        pendencias = []
        erros = []
        rodapes_ignorados = 0
        processos_com_ocorrencia = 0
        chaves_vistas_por_grupo = {}

        destino_label = DativosImportService._label_grupo(grupo)
        regra_aplicada = (
            "Importacao mantida manualmente no lote sem IRRF."
            if grupo == "sem_irrf"
            else "Importacao mantida manualmente no fluxo com IRRF."
        )

        for indice, row in df.iterrows():
            linha_excel = indice + 2

            try:
                nome_bruto = DativosImportService._texto_limpo(row[mapeamento["nome"]])
                documento_bruto = DativosImportService._texto_limpo(row[mapeamento["documento"]])
                processo_bruto = DativosImportService._texto_limpo(row[mapeamento["processo"]])
                valor_bruto_raw = DativosImportService._texto_limpo(row[mapeamento["valor"]])

                if DativosImportService._linha_rodape_ou_total(
                    nome=nome_bruto,
                    documento=documento_bruto,
                    processo=processo_bruto,
                    valor=valor_bruto_raw,
                ):
                    rodapes_ignorados += 1
                    continue

                nome = nome_bruto
                documento = DativosImportService._normalizar_documento_generico(documento_bruto)
                processo = DativosImportService._normalizar_processo(processo_bruto)
                valor = DativosImportService._normalizar_valor_decimal(valor_bruto_raw)

                if not nome or not processo:
                    raise ValueError("Nome ou processo em branco.")

                tipo_documento = detectar_tipo_documento(documento)
                chave = (documento, processo)
                chave_destino = (grupo, documento, processo)
                primeira_linha_mesmo_destino = chaves_vistas_por_grupo.get(chave_destino)

                item_existente_mesmo_destino = itens_existentes_por_grupo.get(chave_destino)
                itens_existentes_outro_grupo = [
                    item
                    for item in itens_existentes_por_chave.get(chave, [])
                    if item.grupo != grupo
                ]

                ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(processo)
                if ocorrencias_processo:
                    processos_com_ocorrencia += 1

                motivos_pendencia = []
                detalhes_pendencia = []
                referencias_existentes = []

                if ocorrencias_processo:
                    motivos_pendencia.append("Processo ja encontrado no sistema")
                    detalhes_pendencia.append(
                        "Esse numero de processo ja aparece em outro contexto do sistema e exige confirmacao explicita."
                    )

                if primeira_linha_mesmo_destino is not None:
                    motivos_pendencia.append("Linha repetida na mesma importacao")
                    detalhes_pendencia.append(
                        f"Repete documento e processo da linha {primeira_linha_mesmo_destino} neste mesmo fluxo."
                    )

                if item_existente_mesmo_destino is not None:
                    motivos_pendencia.append("Ja existe registro igual neste fluxo da C.I.")
                    detalhes_pendencia.append(
                        "O mesmo documento e processo ja existem nesse fluxo desta C.I."
                    )
                    referencias_existentes.append(
                        DativosImportService._resumo_item_existente(item_existente_mesmo_destino)
                    )

                if itens_existentes_outro_grupo:
                    motivos_pendencia.append("Ja existe no outro fluxo desta C.I.")
                    detalhes_pendencia.append(
                        "O mesmo documento e processo ja aparecem no outro grupo desta C.I.; confirme antes de repetir."
                    )
                    for item_existente in itens_existentes_outro_grupo[:3]:
                        referencias_existentes.append(
                            DativosImportService._resumo_item_existente(item_existente)
                        )

                linha_preview = {
                    "preview_id": uuid4().hex,
                    "line_number": linha_excel,
                    "nome": nome,
                    "documento": documento,
                    "documento_formatado": formatar_documento_br(documento, tipo_documento),
                    "tipo_documento": tipo_documento,
                    "processo": processo,
                    "valor": str(valor),
                    "valor_legivel": DativosImportService._formatar_decimal_ptbr(valor),
                    "destino_grupo": grupo,
                    "destino_label": destino_label,
                    "regra_aplicada": regra_aplicada,
                    "motivo_pendencia": " | ".join(motivos_pendencia),
                    "detalhe_pendencia": " ".join(detalhes_pendencia),
                    "requer_confirmacao": bool(motivos_pendencia),
                    "ocorrencias_processo_total": len(ocorrencias_processo),
                    "ocorrencias_processo": DativosImportService._resumo_ocorrencias_processo(
                        ocorrencias_processo
                    ),
                    "referencias_existentes": referencias_existentes,
                }

                chaves_vistas_por_grupo.setdefault(chave_destino, linha_excel)

                if linha_preview["requer_confirmacao"]:
                    pendencias.append(linha_preview)
                    continue

                if grupo == "sem_irrf":
                    prontas_sem_irrf.append(linha_preview)
                else:
                    prontas_com_irrf.append(linha_preview)

            except Exception as exc:
                erros.append(f"Linha {linha_excel}: {exc}")

        return {
            "resumo": {
                "total_prontas_sem_irrf": len(prontas_sem_irrf),
                "total_prontas_com_irrf": len(prontas_com_irrf),
                "total_pendencias": len(pendencias),
                "total_erros": len(erros),
                "rodapes_ignorados": rodapes_ignorados,
                "processos_com_ocorrencia": processos_com_ocorrencia,
                "cnpjs_mantidos_sem_irrf": 0,
                "total_linhas_classificadas": (
                    len(prontas_sem_irrf) + len(prontas_com_irrf) + len(pendencias)
                ),
            },
            "prontas_sem_irrf": prontas_sem_irrf,
            "prontas_com_irrf": prontas_com_irrf,
            "pendencias": pendencias,
            "erros": erros,
        }

    @staticmethod
    def salvar_previa_importacao_unica(
        *,
        preview: dict,
        dativo_ci_id: int,
        usuario_id: int,
        nome_arquivo: str,
    ) -> str:
        token = uuid4().hex
        payload = dict(preview)
        payload["preview_token"] = token
        payload["dativo_ci_id"] = dativo_ci_id
        payload["usuario_id"] = usuario_id
        payload["nome_arquivo"] = str(nome_arquivo or "").strip()
        payload["criado_em"] = utc_now_naive().isoformat()

        with open(
            DativosImportService._preview_storage_path(token),
            "w",
            encoding="utf-8",
        ) as arquivo_preview:
            json.dump(payload, arquivo_preview, ensure_ascii=False, indent=2)

        return token

    @staticmethod
    def carregar_previa_importacao_unica(
        token: str,
        *,
        dativo_ci_id: int,
        usuario_id: int,
    ) -> dict | None:
        caminho = DativosImportService._preview_storage_path(token)
        if not os.path.exists(caminho):
            return None

        with open(caminho, "r", encoding="utf-8") as arquivo_preview:
            payload = json.load(arquivo_preview)

        if payload.get("dativo_ci_id") != dativo_ci_id or payload.get("usuario_id") != usuario_id:
            return None

        criado_em = str(payload.get("criado_em") or "").strip()
        if criado_em:
            try:
                delta_horas = (
                    utc_now_naive() - datetime.fromisoformat(criado_em)
                ).total_seconds() / 3600
                if delta_horas > DativosImportService.PREVIEW_EXPIRATION_HOURS:
                    DativosImportService.descartar_previa_importacao_unica(token)
                    return None
            except Exception:
                pass

        return payload

    @staticmethod
    def descartar_previa_importacao_unica(token: str | None):
        caminho = DativosImportService._preview_storage_path(token or "")
        if os.path.exists(caminho):
            os.remove(caminho)

    @staticmethod
    def aplicar_previa_importacao_unica(
        *,
        dativo_ci,
        preview: dict,
        usuario_id: int,
        pendencias_confirmadas: set[str] | None = None,
    ) -> dict:
        pendencias_confirmadas = pendencias_confirmadas or set()

        linhas_prontas = list(preview.get("prontas_sem_irrf", [])) + list(preview.get("prontas_com_irrf", []))
        linhas_pendentes = [
            linha
            for linha in preview.get("pendencias", [])
            if linha.get("preview_id") in pendencias_confirmadas
        ]

        linhas_importacao = sorted(
            linhas_prontas + linhas_pendentes,
            key=lambda linha: int(linha.get("line_number") or 0),
        )

        importados_sem_irrf = 0
        importados_com_irrf = 0

        for linha in linhas_importacao:
            permitir_duplicidade = bool(linha.get("requer_confirmacao"))

            if linha.get("destino_grupo") == "sem_irrf":
                DativosService.adicionar_item_sem_irrf(
                    dativo_ci=dativo_ci,
                    nome_beneficiario=linha["nome"],
                    cpf_original=linha["documento"],
                    numero_processo=linha["processo"],
                    valor_bruto=Decimal(str(linha["valor"])),
                    usuario_id=usuario_id,
                    permitir_duplicidade_confirmada=permitir_duplicidade,
                )
                importados_sem_irrf += 1
            else:
                DativosService.adicionar_item_com_irrf(
                    dativo_ci=dativo_ci,
                    nome_beneficiario=linha["nome"],
                    cpf_original=linha["documento"],
                    numero_processo=linha["processo"],
                    valor_bruto=Decimal(str(linha["valor"])),
                    valor_irrf=None,
                    usuario_id=usuario_id,
                    permitir_duplicidade_confirmada=permitir_duplicidade,
                )
                importados_com_irrf += 1

        total_pendencias = len(preview.get("pendencias", []))
        total_confirmadas = len(linhas_pendentes)

        return {
            "importados_total": len(linhas_importacao),
            "importados_sem_irrf": importados_sem_irrf,
            "importados_com_irrf": importados_com_irrf,
            "pendencias_confirmadas": total_confirmadas,
            "pendencias_descartadas": max(total_pendencias - total_confirmadas, 0),
        }

    @staticmethod
    def importar_ods_sem_irrf(arquivo, dativo_ci, usuario_id: int):
        df = pd.read_excel(arquivo, engine="odf", dtype=str)
        mapeamento = DativosImportService._mapear_colunas(df.columns)

        chaves_existentes = DativosImportService._obter_chaves_existentes(
            dativo_ci_id=dativo_ci.id,
            grupo="sem_irrf",
        )

        linhas_validas = []
        ignorados = 0
        rodapes_ignorados = 0
        erros = []
        alertas_processo_existente = []

        for indice, row in df.iterrows():
            linha_excel = indice + 2

            try:
                nome_bruto = DativosImportService._texto_limpo(row[mapeamento["nome"]])
                documento_bruto = DativosImportService._texto_limpo(row[mapeamento["documento"]])
                processo_bruto = DativosImportService._texto_limpo(row[mapeamento["processo"]])
                valor_bruto_raw = DativosImportService._texto_limpo(row[mapeamento["valor"]])

                if DativosImportService._linha_rodape_ou_total(
                    nome=nome_bruto,
                    documento=documento_bruto,
                    processo=processo_bruto,
                    valor=valor_bruto_raw,
                ):
                    rodapes_ignorados += 1
                    continue

                nome = nome_bruto
                documento = DativosImportService._normalizar_documento_generico(documento_bruto)
                processo = DativosImportService._normalizar_processo(processo_bruto)
                ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(processo)
                if ocorrencias_processo:
                    alertas_processo_existente.append(
                        f"{nome} / processo {normalizar_numero_processo(processo)}"
                    )
                valor = DativosImportService._normalizar_valor_decimal(valor_bruto_raw)

                if not nome or not processo:
                    raise ValueError("Nome ou processo em branco.")

                chave = (documento, processo)

                if chave in chaves_existentes:
                    ignorados += 1
                    continue

                linhas_validas.append(
                    {
                        "nome": nome,
                        "documento": documento,
                        "processo": processo,
                        "valor": valor,
                    }
                )
                chaves_existentes.add(chave)

            except Exception as exc:
                erros.append(f"Linha {linha_excel}: {exc}")

        importados = 0

        for linha in linhas_validas:
            try:
                DativosService.adicionar_item_sem_irrf(
                    dativo_ci=dativo_ci,
                    nome_beneficiario=linha["nome"],
                    cpf_original=linha["documento"],
                    numero_processo=linha["processo"],
                    valor_bruto=linha["valor"],
                    usuario_id=usuario_id,
                )
                importados += 1
            except Exception as exc:
                erros.append(
                    f"Item '{linha['nome']}' / processo {linha['processo']}: {exc}"
                )

        return {
            "importados": importados,
            "ignorados": ignorados,
            "rodapes_ignorados": rodapes_ignorados,
            "erros": erros,
            "alertas_processo_existente": alertas_processo_existente,
        }

    @staticmethod
    def importar_ods_com_irrf(arquivo, dativo_ci, usuario_id: int):
        df = pd.read_excel(arquivo, engine="odf", dtype=str)
        mapeamento = DativosImportService._mapear_colunas(df.columns)
        DativosImportService._validar_mapeamento_com_amostra(df, mapeamento)

        chaves_existentes = DativosImportService._obter_chaves_existentes(
            dativo_ci_id=dativo_ci.id,
            grupo="com_irrf",
        )

        linhas_validas = []
        ignorados = 0
        rodapes_ignorados = 0
        erros = []
        alertas_processo_existente = []

        for indice, row in df.iterrows():
            linha_excel = indice + 2

            try:
                nome_bruto = DativosImportService._texto_limpo(row[mapeamento["nome"]])
                documento_bruto = DativosImportService._texto_limpo(row[mapeamento["documento"]])
                processo_bruto = DativosImportService._texto_limpo(row[mapeamento["processo"]])
                valor_bruto_raw = DativosImportService._texto_limpo(row[mapeamento["valor"]])

                if DativosImportService._linha_rodape_ou_total(
                    nome=nome_bruto,
                    documento=documento_bruto,
                    processo=processo_bruto,
                    valor=valor_bruto_raw,
                ):
                    rodapes_ignorados += 1
                    continue

                nome = nome_bruto
                documento = DativosImportService._normalizar_documento_generico(documento_bruto)
                processo = DativosImportService._normalizar_processo(processo_bruto)
                ocorrencias_processo = ProcessoCrosscheckService.buscar_ocorrencias(processo)
                if ocorrencias_processo:
                    alertas_processo_existente.append(
                        f"{nome} / processo {normalizar_numero_processo(processo)}"
                    )
                valor = DativosImportService._normalizar_valor_decimal(valor_bruto_raw)

                if not nome or not processo:
                    raise ValueError("Nome ou processo em branco.")

                chave = (documento, processo)

                if chave in chaves_existentes:
                    ignorados += 1
                    continue

                linhas_validas.append(
                    {
                        "nome": nome,
                        "documento": documento,
                        "processo": processo,
                        "valor": valor,
                    }
                )
                chaves_existentes.add(chave)

            except Exception as exc:
                erros.append(f"Linha {linha_excel}: {exc}")

        importados = 0

        for linha in linhas_validas:
            try:
                DativosService.adicionar_item_com_irrf(
                    dativo_ci=dativo_ci,
                    nome_beneficiario=linha["nome"],
                    cpf_original=linha["documento"],
                    numero_processo=linha["processo"],
                    valor_bruto=linha["valor"],
                    valor_irrf=None,
                    usuario_id=usuario_id,
                )
                importados += 1
            except Exception as exc:
                erros.append(
                    f"Item '{linha['nome']}' / processo {linha['processo']}: {exc}"
                )

        return {
            "importados": importados,
            "ignorados": ignorados,
            "rodapes_ignorados": rodapes_ignorados,
            "erros": erros,
            "alertas_processo_existente": alertas_processo_existente,
        }
