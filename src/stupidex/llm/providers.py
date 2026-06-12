"""Provider registry and resolved provider for a request.

Each provider knows its model and how to talk to the LLM via litellm.
"""
from dataclasses import dataclass

from ..config import AppConfig, DEFAULT_BASE_URL


@dataclass
class ProviderConfig:
    id: str
    name: str
    base_url: str | None
    default_model: str
    api_key_env: str | None = None
    needs_api_key: bool = False
    description: str = ""


PROVIDERS: dict[str, ProviderConfig] = {
    "deepseek-v4-flash": ProviderConfig(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        description="DeepSeek V4 Flash — rápido, multimodal, recomendado",
    ),
    "deepseek-v4-pro": ProviderConfig(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-v4-pro",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        description="DeepSeek V4 Pro — mais capaz, mais lento",
    ),
    "deepseek-chat": ProviderConfig(
        id="deepseek-chat",
        name="DeepSeek Chat (V3)",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        description="DeepSeek V3 — chat geral",
    ),
    "deepseek-reasoner": ProviderConfig(
        id="deepseek-reasoner",
        name="DeepSeek Reasoner (R1)",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-reasoner",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        description="DeepSeek R1 — raciocínio estendido",
    ),
    "openai": ProviderConfig(
        id="openai",
        name="OpenAI",
        base_url=None,
        default_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        needs_api_key=True,
    ),
    "anthropic": ProviderConfig(
        id="anthropic",
        name="Anthropic (Claude)",
        base_url=None,
        default_model="claude-3-5-sonnet-20241022",
        api_key_env="ANTHROPIC_API_KEY",
        needs_api_key=True,
    ),
    "ollama": ProviderConfig(
        id="ollama",
        name="Ollama (local)",
        base_url="http://localhost:11434/v1",
        default_model="ollama_chat/llama3.1",
        needs_api_key=False,
        description="Modelos rodando localmente no Ollama",
    ),
}


def get_provider(provider_id: str) -> ProviderConfig:
    if provider_id in PROVIDERS:
        return PROVIDERS[provider_id]
    return PROVIDERS[DEFAULT_FALLBACK_ID]


DEFAULT_FALLBACK_ID = "deepseek-v4-flash"


def list_providers() -> list[dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "model": p.default_model,
            "needs_api_key": p.needs_api_key,
            "api_key_env": p.api_key_env,
            "description": p.description,
        }
        for p in PROVIDERS.values()
    ]


def resolve_request_model(provider_id: str, custom_model: str, cfg: AppConfig) -> tuple[ProviderConfig, str]:
    """Pick the effective provider + model for a request."""
    p = get_provider(provider_id)
    if custom_model.strip():
        return p, custom_model.strip()
    if cfg.custom_model.strip():
        return p, cfg.custom_model.strip()
    return p, p.default_model
