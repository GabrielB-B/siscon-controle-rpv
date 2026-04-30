import re

from flask import url_for
from sqlalchemy.orm import joinedload
from unidecode import unidecode

from app.models import DativoCI, DativoItem, Processo, RegistroRPV, RPVPendenciaDocumento
from app.utils.navigation import append_internal_return_url
from app.utils.normalizers import normalizar_documento, normalizar_numero_processo


class ProcessoCrosscheckService:
    """
    Faz a checagem transversal em todo o banco.

    A checagem de duplicidade do cadastro continua estrita por numero de processo.
    A busca das telas mostra contexto por processo, C.I., documento, beneficiario
    e marcadores operacionais, sem alterar registros.
    """

    MINIMO_BUSCA_NUMERICA = 6
    MINIMO_BUSCA_TEXTO = 3

    @staticmethod
    def busca_parece_processo(numero_processo: str | None, *, tamanho_minimo: int = 6) -> bool:
        return len(normalizar_numero_processo(numero_processo or "")) >= tamanho_minimo

    @staticmethod
    def _normalizar_texto(valor: str | None) -> str:
        texto = unidecode(str(valor or "").strip()).upper()
        return re.sub(r"\s+", " ", texto)

    @staticmethod
    def _normalizar_texto_compacto(valor: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", ProcessoCrosscheckService._normalizar_texto(valor))

    @staticmethod
    def _preparar_consulta(consulta: str | None) -> dict:
        texto_original = str(consulta or "").strip()
        texto_compacto = ProcessoCrosscheckService._normalizar_texto_compacto(texto_original)
        tem_letra = any(caractere.isalpha() for caractere in texto_compacto)
        texto_minimo = (
            ProcessoCrosscheckService.MINIMO_BUSCA_TEXTO
            if tem_letra
            else ProcessoCrosscheckService.MINIMO_BUSCA_NUMERICA
        )
        documento = normalizar_documento(texto_original)
        processo = normalizar_numero_processo(texto_original)

        return {
            "original": texto_original,
            "texto": texto_compacto if len(texto_compacto) >= texto_minimo else "",
            "documento": (
                documento
                if len(documento) >= ProcessoCrosscheckService.MINIMO_BUSCA_NUMERICA
                else ""
            ),
            "processo": (
                processo
                if len(processo) >= ProcessoCrosscheckService.MINIMO_BUSCA_NUMERICA
                else ""
            ),
        }

    @staticmethod
    def _adicionar_criterio(criterios: list[str], criterio: str):
        if criterio and criterio not in criterios:
            criterios.append(criterio)

    @staticmethod
    def _criterios_correspondentes(
        consulta: dict,
        *,
        campos_texto: list[tuple[str, str | None]],
        campos_documento: list[tuple[str, str | None]] | None = None,
        campos_processo: list[tuple[str, str | None]] | None = None,
    ) -> list[str]:
        criterios = []
        termo_texto = consulta.get("texto") or ""
        termo_documento = consulta.get("documento") or ""
        termo_processo = consulta.get("processo") or ""

        if termo_texto:
            for criterio, valor in campos_texto:
                if termo_texto in ProcessoCrosscheckService._normalizar_texto_compacto(valor):
                    ProcessoCrosscheckService._adicionar_criterio(criterios, criterio)

        if termo_documento:
            for criterio, valor in campos_documento or []:
                if termo_documento in normalizar_documento(valor):
                    ProcessoCrosscheckService._adicionar_criterio(criterios, criterio)

        if termo_processo:
            for criterio, valor in campos_processo or []:
                if termo_processo in normalizar_numero_processo(valor or ""):
                    ProcessoCrosscheckService._adicionar_criterio(criterios, criterio)

        return criterios

    @staticmethod
    def _ocorrencia_rpv_normal(registro: RegistroRPV, *, criterios: list[str] | None = None) -> dict:
        return {
            "origem": "RPV normal",
            "tipo": registro.tipo_rpv.nome if registro.tipo_rpv else "-",
            "grupo": "rpv_normal",
            "beneficiario": registro.nome_beneficiario,
            "numero_processo": registro.processo.numero_processo if registro.processo else "-",
            "processo_edoc": registro.processo.processo_edoc if registro.processo else "-",
            "documento": registro.documento_original,
            "ci_id": None,
            "ci_exercicio": getattr(registro.processo, "exercicio", None),
            "ci_url": None,
            "lote_id": None,
            "lote_url": None,
            "tipo_link": "rpv",
            "link_id": registro.id,
            "abrir_url": url_for("cadastros.editar_rpv", registro_id=registro.id),
            "responsavel": registro.elaborador.nome if registro.elaborador else "-",
            "resumo_operacional": registro.resumo_operacional,
            "situacao_rpv": registro.situacao_empenho.nome if registro.situacao_empenho else "-",
            "situacao_irrf": registro.situacao_imposto.nome if registro.situacao_imposto else "-",
            "criterios": criterios or [],
        }

    @staticmethod
    def _ocorrencia_rpv_pendente(
        pendencia: RPVPendenciaDocumento,
        *,
        criterios: list[str] | None = None,
    ) -> dict:
        return {
            "origem": "RPV pendente",
            "tipo": pendencia.tipo_rpv.nome if pendencia.tipo_rpv else "-",
            "grupo": "rpv_pendente",
            "beneficiario": pendencia.nome_beneficiario,
            "numero_processo": pendencia.numero_processo,
            "processo_edoc": pendencia.processo_edoc,
            "documento": pendencia.documento_original,
            "ci_id": None,
            "ci_exercicio": pendencia.exercicio,
            "ci_url": None,
            "lote_id": None,
            "lote_url": None,
            "tipo_link": "rpv_pendencia",
            "link_id": pendencia.id,
            "abrir_url": url_for(
                "cadastros.detalhe_pendencia_documental",
                pendencia_id=pendencia.id,
            ),
            "responsavel": pendencia.responsavel.nome if pendencia.responsavel else "-",
            "resumo_operacional": pendencia.resumo_operacional,
            "situacao_rpv": pendencia.status_legivel,
            "situacao_irrf": "Fora do fluxo oficial",
            "criterios": criterios or [],
        }

    @staticmethod
    def _ocorrencia_dativo(item: DativoItem, *, criterios: list[str] | None = None) -> dict:
        if item.grupo == "sem_irrf":
            origem = "Dativo sem IRRF"
            tipo = "Beneficiário do lote sem IRRF"
            grupo = "dativo_sem_irrf"
            tipo_link = "dativo_item_lote"
            link_id = item.id
            abrir_url = url_for(
                "dativos.editar_item_lote",
                lote_id=item.dativo_lote_id,
                item_id=item.id,
            )
        else:
            origem = "Dativo com IRRF"
            tipo = "Item com IRRF"
            grupo = "dativo_com_irrf"
            tipo_link = "dativo_item"
            link_id = item.id
            abrir_url = url_for("dativos.detalhe_item_com_irrf", item_id=item.id)

        return {
            "origem": origem,
            "tipo": tipo,
            "grupo": grupo,
            "beneficiario": item.nome_beneficiario,
            "numero_processo": item.numero_processo,
            "processo_edoc": item.dativo_ci.processo_edoc if item.dativo_ci else "-",
            "documento": item.cpf_original,
            "ci_id": item.dativo_ci_id,
            "ci_exercicio": getattr(getattr(item, "dativo_ci", None), "exercicio", None),
            "ci_url": (
                url_for("dativos.detalhe_ci", ci_id=item.dativo_ci_id)
                if item.dativo_ci_id
                else None
            ),
            "lote_id": item.dativo_lote_id,
            "lote_url": (
                url_for("dativos.detalhe_lote_sem_irrf", lote_id=item.dativo_lote_id)
                if item.dativo_lote_id
                else None
            ),
            "tipo_link": tipo_link,
            "link_id": link_id,
            "abrir_url": abrir_url,
            "responsavel": (
                item.dativo_ci.responsavel.nome
                if item.dativo_ci and item.dativo_ci.responsavel
                else "-"
            ),
            "resumo_operacional": item.resumo_operacional_atual,
            "situacao_rpv": item.situacao_rpv.nome if item.situacao_rpv else "-",
            "situacao_irrf": item.situacao_imposto.nome if item.situacao_imposto else "-",
            "criterios": criterios or [],
        }

    @staticmethod
    def _carregar_rpvs_normais():
        return (
            RegistroRPV.query.options(
                joinedload(RegistroRPV.processo),
                joinedload(RegistroRPV.tipo_rpv),
                joinedload(RegistroRPV.elaborador),
                joinedload(RegistroRPV.situacao_empenho),
                joinedload(RegistroRPV.situacao_imposto),
            )
            .join(Processo)
            .filter(RegistroRPV.ativo.is_(True))
            .order_by(RegistroRPV.criado_em.desc())
            .all()
        )

    @staticmethod
    def _carregar_itens_dativos():
        return (
            DativoItem.query.options(
                joinedload(DativoItem.dativo_ci).joinedload(DativoCI.responsavel),
                joinedload(DativoItem.dativo_lote),
                joinedload(DativoItem.situacao_rpv),
                joinedload(DativoItem.situacao_imposto),
            )
            .filter(DativoItem.ativo.is_(True))
            .order_by(DativoItem.criado_em.desc())
            .all()
        )

    @staticmethod
    def _carregar_pendencias_documentais():
        return (
            RPVPendenciaDocumento.query.options(
                joinedload(RPVPendenciaDocumento.tipo_rpv),
                joinedload(RPVPendenciaDocumento.responsavel),
            )
            .filter(RPVPendenciaDocumento.status == "aberta")
            .order_by(RPVPendenciaDocumento.criado_em.desc())
            .all()
        )

    @staticmethod
    def buscar_ocorrencias(
        numero_processo: str,
        excluir_registro_id=None,
        excluir_item_id=None,
        excluir_pendencia_id=None,
    ):
        processo_alvo = normalizar_numero_processo(numero_processo)
        if not processo_alvo:
            return []

        ocorrencias = []

        for registro in ProcessoCrosscheckService._carregar_rpvs_normais():
            if excluir_registro_id and registro.id == excluir_registro_id:
                continue
            if getattr(registro, "status_principal_cancelado", False):
                continue

            numero_existente = normalizar_numero_processo(
                registro.processo.numero_processo if registro.processo else ""
            )
            if numero_existente != processo_alvo:
                continue

            ocorrencias.append(
                ProcessoCrosscheckService._ocorrencia_rpv_normal(
                    registro,
                    criterios=["Número do processo"],
                )
            )

        for pendencia in ProcessoCrosscheckService._carregar_pendencias_documentais():
            if excluir_pendencia_id and pendencia.id == excluir_pendencia_id:
                continue

            if normalizar_numero_processo(pendencia.numero_processo) != processo_alvo:
                continue

            ocorrencias.append(
                ProcessoCrosscheckService._ocorrencia_rpv_pendente(
                    pendencia,
                    criterios=["Número do processo", "Pendência documental"],
                )
            )

        for item in ProcessoCrosscheckService._carregar_itens_dativos():
            if excluir_item_id and item.id == excluir_item_id:
                continue
            if getattr(item, "status_principal_cancelado", False):
                continue

            if normalizar_numero_processo(item.numero_processo) != processo_alvo:
                continue

            ocorrencias.append(
                ProcessoCrosscheckService._ocorrencia_dativo(
                    item,
                    criterios=["Número do processo"],
                )
            )

        return ocorrencias

    @staticmethod
    def buscar_ocorrencias_pesquisa(consulta_texto: str):
        consulta = ProcessoCrosscheckService._preparar_consulta(consulta_texto)
        if not any((consulta["texto"], consulta["documento"], consulta["processo"])):
            return []

        ocorrencias = []

        for registro in ProcessoCrosscheckService._carregar_rpvs_normais():
            if getattr(registro, "status_principal_cancelado", False):
                continue

            processo = registro.processo
            criterios = ProcessoCrosscheckService._criterios_correspondentes(
                consulta,
                campos_texto=[
                    ("C.I./eDOC", processo.processo_edoc if processo else None),
                    ("Número do processo", processo.numero_processo if processo else None),
                    ("Beneficiário", registro.nome_beneficiario),
                    ("Resumo operacional", registro.resumo_operacional),
                    ("Nota de empenho", registro.nota_empenho),
                    ("SE", registro.numero_se),
                    ("OB", registro.ordem_bancaria),
                    ("OB de imposto", registro.ob_imposto),
                ],
                campos_documento=[
                    ("CPF/CNPJ", registro.documento_original),
                    ("CPF/CNPJ", registro.documento_normalizado),
                    ("CPF/CNPJ corrigido", registro.documento_corrigido),
                ],
                campos_processo=[
                    ("Número do processo", processo.numero_processo if processo else None),
                    ("C.I./eDOC", processo.processo_edoc if processo else None),
                ],
            )
            if not criterios:
                continue

            ocorrencias.append(
                ProcessoCrosscheckService._ocorrencia_rpv_normal(
                    registro,
                    criterios=criterios,
                )
            )

        for pendencia in ProcessoCrosscheckService._carregar_pendencias_documentais():
            criterios = ProcessoCrosscheckService._criterios_correspondentes(
                consulta,
                campos_texto=[
                    ("C.I./eDOC", pendencia.processo_edoc),
                    ("Número do processo", pendencia.numero_processo),
                    ("Beneficiário", pendencia.nome_beneficiario),
                    ("Resumo operacional", pendencia.resumo_operacional),
                    ("Observações", pendencia.observacoes),
                ],
                campos_documento=[
                    ("CPF/CNPJ", pendencia.documento_original),
                    ("CPF/CNPJ", pendencia.documento_normalizado),
                ],
                campos_processo=[
                    ("Número do processo", pendencia.numero_processo),
                    ("C.I./eDOC", pendencia.processo_edoc),
                ],
            )
            if not criterios:
                continue

            ocorrencias.append(
                ProcessoCrosscheckService._ocorrencia_rpv_pendente(
                    pendencia,
                    criterios=criterios,
                )
            )

        for item in ProcessoCrosscheckService._carregar_itens_dativos():
            if getattr(item, "status_principal_cancelado", False):
                continue

            dativo_ci = item.dativo_ci
            lote = item.dativo_lote
            criterios = ProcessoCrosscheckService._criterios_correspondentes(
                consulta,
                campos_texto=[
                    ("C.I./eDOC", dativo_ci.processo_edoc if dativo_ci else None),
                    ("Número do processo", item.numero_processo),
                    ("Beneficiário", item.nome_beneficiario),
                    ("Resumo operacional", item.resumo_operacional_atual),
                    ("Nota de empenho", item.nota_empenho),
                    ("Nota de empenho do lote", lote.nota_empenho if lote else None),
                    ("SE", item.numero_se),
                    ("SE do lote", lote.numero_se if lote else None),
                    ("OB", item.ordem_bancaria),
                    ("OB do lote", lote.ordem_bancaria if lote else None),
                    ("OB de imposto", item.ob_imposto),
                ],
                campos_documento=[
                    ("CPF/CNPJ", item.cpf_original),
                    ("CPF/CNPJ", item.cpf_normalizado),
                ],
                campos_processo=[
                    ("Número do processo", item.numero_processo),
                    ("C.I./eDOC", dativo_ci.processo_edoc if dativo_ci else None),
                ],
            )
            if not criterios:
                continue

            ocorrencias.append(
                ProcessoCrosscheckService._ocorrencia_dativo(
                    item,
                    criterios=criterios,
                )
            )

        return ocorrencias

    @staticmethod
    def _aplicar_retorno_ocorrencia(ocorrencia: dict, retorno_url: str | None) -> dict:
        if not retorno_url:
            return ocorrencia

        resultado = dict(ocorrencia)
        for campo in ("abrir_url", "ci_url", "lote_url"):
            if resultado.get(campo):
                resultado[campo] = append_internal_return_url(
                    resultado.get(campo),
                    retorno_url,
                )
        return resultado

    @staticmethod
    def buscar_contexto_pesquisa(numero_processo: str, *, retorno_url: str | None = None):
        ocorrencias = ProcessoCrosscheckService.buscar_ocorrencias_pesquisa(numero_processo)
        if not ocorrencias:
            return None

        if retorno_url:
            ocorrencias = [
                ProcessoCrosscheckService._aplicar_retorno_ocorrencia(
                    ocorrencia,
                    retorno_url,
                )
                for ocorrencia in ocorrencias
            ]

        rpvs_normais = []
        pendencias_documentais = []
        dativos_por_ci = {}

        for ocorrencia in ocorrencias:
            if ocorrencia.get("grupo") == "rpv_normal":
                rpvs_normais.append(ocorrencia)
                continue
            if ocorrencia.get("grupo") == "rpv_pendente":
                pendencias_documentais.append(ocorrencia)
                continue

            chave_ci = ocorrencia.get("ci_id") or f"ci::{ocorrencia.get('processo_edoc')}"
            dativo_ci = dativos_por_ci.setdefault(
                chave_ci,
                {
                    "ci_id": ocorrencia.get("ci_id"),
                    "processo_edoc": ocorrencia.get("processo_edoc") or "-",
                    "exercicio": ocorrencia.get("ci_exercicio") or "",
                    "abrir_ci_url": ocorrencia.get("ci_url"),
                    "abrir_lote_url": ocorrencia.get("lote_url"),
                    "total_ocorrencias": 0,
                    "total_itens_sem_irrf": 0,
                    "total_itens_com_irrf": 0,
                    "exemplos": [],
                    "criterios": [],
                },
            )

            dativo_ci["total_ocorrencias"] += 1
            if ocorrencia.get("grupo") == "dativo_sem_irrf":
                dativo_ci["total_itens_sem_irrf"] += 1
                if not dativo_ci["abrir_lote_url"] and ocorrencia.get("lote_url"):
                    dativo_ci["abrir_lote_url"] = ocorrencia.get("lote_url")
            elif ocorrencia.get("grupo") == "dativo_com_irrf":
                dativo_ci["total_itens_com_irrf"] += 1

            for criterio in ocorrencia.get("criterios") or []:
                if criterio not in dativo_ci["criterios"] and len(dativo_ci["criterios"]) < 4:
                    dativo_ci["criterios"].append(criterio)

            exemplo = (
                f"{ocorrencia.get('tipo')}: {ocorrencia.get('beneficiario')}"
                if ocorrencia.get("beneficiario")
                else str(ocorrencia.get("tipo") or "").strip()
            )
            if exemplo and exemplo not in dativo_ci["exemplos"] and len(dativo_ci["exemplos"]) < 3:
                dativo_ci["exemplos"].append(exemplo)

        dativos_relacionados = sorted(
            dativos_por_ci.values(),
            key=lambda ci: (str(ci.get("processo_edoc") or ""), str(ci.get("exercicio") or "")),
        )

        busca_por_processo = any(
            "Número do processo" in (ocorrencia.get("criterios") or [])
            for ocorrencia in ocorrencias
        )

        return {
            "consulta": str(numero_processo or "").strip(),
            "processo_normalizado": normalizar_numero_processo(numero_processo),
            "busca_por_processo": busca_por_processo,
            "total_ocorrencias": len(ocorrencias),
            "total_rpvs_normais": len(rpvs_normais),
            "total_pendencias_documentais": len(pendencias_documentais),
            "total_cis_dativos": len(dativos_relacionados),
            "total_dativo_ocorrencias": sum(
                item["total_ocorrencias"] for item in dativos_relacionados
            ),
            "rpvs_normais": rpvs_normais[:5],
            "pendencias_documentais": pendencias_documentais[:5],
            "dativos_relacionados": dativos_relacionados,
        }
