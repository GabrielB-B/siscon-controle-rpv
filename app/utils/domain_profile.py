from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TipoRPVDefinition:
    key: str
    nome: str
    ordem_exibicao: int


@dataclass(frozen=True)
class SituacaoDefinition:
    key: str
    nome: str
    cor_badge: str | None
    ordem_fluxo: int
    ativo: bool
    is_final: bool


@dataclass(frozen=True)
class DomainProfile:
    tipos_rpv: tuple[TipoRPVDefinition, ...]
    situacoes_empenho: tuple[SituacaoDefinition, ...]
    situacoes_imposto: tuple[SituacaoDefinition, ...]

    def tipo_rpv_name(self, key: str) -> str:
        return _buscar_por_key(self.tipos_rpv, key).nome

    def situacao_empenho_name(self, key: str) -> str:
        return _buscar_por_key(self.situacoes_empenho, key).nome

    def situacao_imposto_name(self, key: str) -> str:
        return _buscar_por_key(self.situacoes_imposto, key).nome

    @property
    def situacao_empenho_inicial_nome(self) -> str:
        return self.situacao_empenho_name("sem_tratamento")

    @property
    def situacao_imposto_inicial_nome(self) -> str:
        return self.situacao_imposto_name("sem_tratamento")

    @property
    def situacao_imposto_sem_irrf_nome(self) -> str:
        return self.situacao_imposto_name("sem_irrf")


DEFAULT_TIPOS_RPV = (
    TipoRPVDefinition("rpv_pessoal", "RPV pessoal", 1),
    TipoRPVDefinition("rpv_custeio", "RPV custeio", 2),
    TipoRPVDefinition("rpv_honorarios", "RPV honorários", 3),
    TipoRPVDefinition("rpv_periciais", "RPV periciais", 4),
    TipoRPVDefinition("rpv_trabalhista", "RPV trabalhista", 5),
    TipoRPVDefinition("rpv_federal", "RPV federal", 6),
    TipoRPVDefinition("guia_custas", "Guia de custas", 7),
    TipoRPVDefinition("indenizacao", "Indenização", 8),
    TipoRPVDefinition("danos_morais", "Danos Morais", 9),
    TipoRPVDefinition("rpv_dativo", "RPV dativo", 10),
)

DEFAULT_SITUACOES_EMPENHO = (
    SituacaoDefinition("sem_tratamento", "Sem Tratamento", "badge-slate", 1, True, False),
    SituacaoDefinition("guias_geradas", "Guias Geradas", "badge-sky", 2, True, False),
    SituacaoDefinition("se_aguardando_aprovacao", "SE Aguardando Aprovação", "badge-amber", 3, True, False),
    SituacaoDefinition("se_aprovada_gerar_ne", "SE Aprovada - Gerar NE", "badge-blue", 4, True, False),
    SituacaoDefinition("ne_aguardando_assinatura", "NE Aguardando Assinatura", "badge-indigo", 5, True, False),
    SituacaoDefinition("vd_a_liquidar", "VD à Liquidar", "badge-violet", 6, True, False),
    SituacaoDefinition("vd_liquidada", "VD Liquidada", "badge-purple", 7, True, False),
    SituacaoDefinition("pd_lote_carregada", "PD em Lote Carregada", "badge-cyan", 8, True, False),
    SituacaoDefinition("pd_gerada_sefaz", "PD Gerada - SEFAZ", "badge-teal", 9, True, False),
    SituacaoDefinition("pagamento_ob_gerada", "Pagamento - OB Gerada", "badge-purple", 10, True, False),
    SituacaoDefinition("aguardando_assinatura_ob", "Aguardando Assinatura da OB", "badge-amber", 11, True, False),
    SituacaoDefinition("assinado_levar_banco", "Assinado - Levar ao Banco", "badge-purple", 12, True, False),
    SituacaoDefinition("aguardando_retorno_banco", "Aguardando Retorno Banco", "badge-fuchsia", 13, True, False),
    SituacaoDefinition("pago", "Pago", "badge-emerald", 14, True, False),
    SituacaoDefinition("concluida", "Concluída", "badge-green", 15, True, True),
    SituacaoDefinition("devolvido", "Devolvido", "badge-red", 16, True, True),
    SituacaoDefinition("cancelado", "Cancelado", "badge-zinc", 17, True, True),
)

DEFAULT_SITUACOES_IMPOSTO = (
    SituacaoDefinition("sem_tratamento", "Sem Tratamento", "badge-slate", 1, True, False),
    SituacaoDefinition("sem_irrf", "Sem IRRF", "badge-slate", 2, True, True),
    SituacaoDefinition("aguardando_pgto_ob_principal", "Aguardando PGTO OB Principal", "badge-amber", 3, True, False),
    SituacaoDefinition("pd_irrf_aguardando_sefaz", "PD IRRF - Aguardando SEFAZ", "badge-cyan", 4, True, False),
    SituacaoDefinition("pgto_irrf_ob_gerada", "PGTO IRRF - OB Gerada", "badge-purple", 5, True, False),
    SituacaoDefinition("concluida", "Concluída", "badge-green", 6, True, True),
    SituacaoDefinition("devolvido", "Devolvido", "badge-red", 7, True, True),
    SituacaoDefinition("cancelado", "Cancelado", "badge-zinc", 8, True, True),
)


def _buscar_por_key(items, key: str):
    for item in items:
        if item.key == key:
            return item
    raise KeyError(f"Definicao de dominio nao encontrada: {key}")


def _resolve_profile_path(profile_path: str) -> Path:
    path = Path(profile_path)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


def _load_profile_overrides() -> dict:
    profile_path = str(os.getenv("DOMAIN_PROFILE_FILE") or "").strip()
    if not profile_path:
        return {}

    resolved_path = _resolve_profile_path(profile_path)
    if not resolved_path.exists():
        raise ValueError(f"Arquivo de perfil de dominio nao encontrado: {resolved_path}")

    raw_payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError("O arquivo de perfil de dominio deve conter um objeto JSON na raiz.")
    return raw_payload


def _replace_names(definitions, overrides: dict[str, object]) -> tuple:
    atualizados = []
    for item in definitions:
        override = overrides.get(item.key)
        if override is None:
            atualizados.append(item)
            continue
        if isinstance(override, str):
            nome = override.strip() or item.nome
        elif isinstance(override, dict):
            nome = str(override.get("nome") or "").strip() or item.nome
        else:
            raise ValueError(f"Override invalido para a chave de dominio '{item.key}'.")
        atualizados.append(replace(item, nome=nome))
    return tuple(atualizados)


@lru_cache(maxsize=1)
def get_domain_profile() -> DomainProfile:
    overrides = _load_profile_overrides()
    return DomainProfile(
        tipos_rpv=_replace_names(DEFAULT_TIPOS_RPV, overrides.get("tipos_rpv", {})),
        situacoes_empenho=_replace_names(
            DEFAULT_SITUACOES_EMPENHO,
            overrides.get("situacoes_empenho", {}),
        ),
        situacoes_imposto=_replace_names(
            DEFAULT_SITUACOES_IMPOSTO,
            overrides.get("situacoes_imposto", {}),
        ),
    )


def clear_domain_profile_cache() -> None:
    get_domain_profile.cache_clear()
