"""Provider registry — NVIDIA NIM (server) + Anthropic & OpenAI via Puter.js (browser).

Providers com runtime="server" usam litellm no backend.
Providers com runtime="puter" são executados inteiramente no browser via window.puter.ai.chat()
— nenhuma chave de API é necessária no servidor, a autenticação é gerida pelo Puter.

NOTA IMPORTANTE sobre model IDs Puter:
  Puter.js usa os nomes exatos da API da Anthropic/OpenAI (com versão).
  Se o modelo não for reconhecido, o Puter cai no padrão dele (claude-sonnet-4-20250514).
  Por isso TODOS os default_model de providers Puter devem usar os IDs versionados.
"""

import os
from dataclasses import dataclass

from ..config import AppConfig

# ──────────────────────────────────────────────────────────────────────────────
# NVIDIA NIM — DeepSeek-V4-Pro
# Ref: https://docs.api.nvidia.com/nim/reference/deepseek-ai-deepseek-v4-pro-infer
# ──────────────────────────────────────────────────────────────────────────────
_NVIDIA_BASE_URL     = "https://integrate.api.nvidia.com/v1"
_NVIDIA_DEEPSEEK_KEY = os.environ.get("NVIDIA_DEEPSEEK_KEY", "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")
_NVIDIA_MINIMAX_KEY  = os.environ.get("NVIDIA_MINIMAX_KEY", "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")


@dataclass
class ProviderConfig:
    id: str
    name: str
    base_url: str | None
    default_model: str
    api_key_env: str | None = None
    needs_api_key: bool = False
    supports_vision: bool = False
    supports_tools: bool = True
    supports_agent_bridge: bool = False
    runtime: str = "server"
    description: str = ""


PROVIDERS: dict[str, ProviderConfig] = {

    # ── Server-side — NVIDIA NIM: DeepSeek V4 Pro ─────────────────────────────
    "nvidia-deepseek": ProviderConfig(
        id="nvidia-deepseek",
        name="DeepSeek V4 Pro (NVIDIA NIM)",
        base_url=_NVIDIA_BASE_URL,
        default_model="deepseek-ai/deepseek-v4-pro",
        api_key_env="NVIDIA_DEEPSEEK_KEY",
        needs_api_key=False,
        supports_vision=False,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="server",
        description="DeepSeek V4 Pro via NVIDIA NIM — máxima capacidade de raciocínio e coding",
    ),

    # ── Server-side — NVIDIA NIM: MiniMax M3 ──────────────────────────────────
    "nvidia-minimax": ProviderConfig(
        id="nvidia-minimax",
        name="MiniMax M3 (NVIDIA NIM)",
        base_url=_NVIDIA_BASE_URL,
        default_model="minimaxai/minimax-m3",
        api_key_env="NVIDIA_MINIMAX_KEY",
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="server",
        description="MiniMax M3 via NVIDIA NIM — modelo multimodal com suporte a visão e raciocínio",
    ),

    # ── Browser-side via Puter.js — Anthropic ─────────────────────────────────
    # IDs versionados exatos da API Anthropic (Puter rejeita aliases sem versão)
    "claude-sonnet-4": ProviderConfig(
        id="claude-sonnet-4",
        name="Claude Sonnet 4 (Anthropic · Puter)",
        base_url=None,
        default_model="claude-sonnet-4-20250514",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="Claude Sonnet 4 — modelo mais recente da Anthropic disponível no Puter",
    ),

    "claude-3-7-sonnet": ProviderConfig(
        id="claude-3-7-sonnet",
        name="Claude 3.7 Sonnet (Anthropic · Puter)",
        base_url=None,
        default_model="claude-3-7-sonnet-20250219",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="Claude 3.7 Sonnet — raciocínio estendido (extended thinking) da Anthropic",
    ),

    "claude-3-5-sonnet": ProviderConfig(
        id="claude-3-5-sonnet",
        name="Claude 3.5 Sonnet (Anthropic · Puter)",
        base_url=None,
        default_model="claude-3-5-sonnet-20241022",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="Claude 3.5 Sonnet — equilíbrio perfeito entre velocidade e inteligência",
    ),

    "claude-3-5-haiku": ProviderConfig(
        id="claude-3-5-haiku",
        name="Claude 3.5 Haiku (Anthropic · Puter)",
        base_url=None,
        default_model="claude-3-5-haiku-20241022",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="Claude 3.5 Haiku — o mais rápido e leve da geração 3.5 da Anthropic",
    ),

    # ── Browser-side via Puter.js — OpenAI ────────────────────────────────────
    "gpt-4o": ProviderConfig(
        id="gpt-4o",
        name="GPT-4o (OpenAI · Puter)",
        base_url=None,
        default_model="gpt-4o",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="GPT-4o — multimodal da OpenAI com suporte a texto, imagem e código",
    ),

    "gpt-4o-mini": ProviderConfig(
        id="gpt-4o-mini",
        name="GPT-4o mini (OpenAI · Puter)",
        base_url=None,
        default_model="gpt-4o-mini",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="GPT-4o mini — rápido e econômico, ótimo para tarefas cotidianas",
    ),

    "gpt-4-1": ProviderConfig(
        id="gpt-4-1",
        name="GPT-4.1 (OpenAI · Puter)",
        base_url=None,
        default_model="gpt-4.1",
        api_key_env=None,
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        supports_agent_bridge=True,
        runtime="puter",
        description="GPT-4.1 — modelo de última geração da OpenAI com raciocínio avançado",
    ),
}

DEFAULT_FALLBACK_ID = "nvidia-deepseek"


def get_provider(provider_id: str) -> ProviderConfig:
    if provider_id in PROVIDERS:
        return PROVIDERS[provider_id]
    return PROVIDERS[DEFAULT_FALLBACK_ID]


def list_providers() -> list[dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "model": p.default_model,
            "needs_api_key": p.needs_api_key,
            "supports_vision": p.supports_vision,
            "supports_tools": p.supports_tools,
            "supports_agent_bridge": p.supports_agent_bridge,
            "runtime": p.runtime,
            "api_key_env": p.api_key_env,
            "description": p.description,
        }
        for p in PROVIDERS.values()
    ]


def resolve_request_model(
    provider_id: str, custom_model: str, cfg: AppConfig
) -> tuple[ProviderConfig, str]:
    """Retorna (ProviderConfig, model_string) para o provider solicitado."""
    p = get_provider(provider_id)
    if p.runtime == "server":
        return p, p.default_model
    model = custom_model.strip() if custom_model and custom_model.strip() else p.default_model
    return p, model
