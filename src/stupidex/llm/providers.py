"""Provider registry and resolved provider for a request.

Each provider knows its model and how to talk to the LLM via litellm.
"""

from dataclasses import dataclass

from ..config import DEFAULT_BASE_URL, AppConfig


@dataclass
class ProviderConfig:
    id: str
    name: str
    base_url: str | None
    default_model: str
    api_key_env: str | None = None
    needs_api_key: bool = False
    supports_vision: bool = False
    supports_tools: bool = True  # Whether the model supports function/tool calling
    runtime: str = "server"
    description: str = ""


PROVIDERS: dict[str, ProviderConfig] = {
    "deepseek-v4-flash": ProviderConfig(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        supports_tools=False,  # V4 Flash has limited/no tool calling support
        description="DeepSeek V4 Flash — rápido, mas SEM suporte a ferramentas",
    ),
    "deepseek-v4-pro": ProviderConfig(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-v4-pro",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        supports_tools=False,  # V4 Pro also has limited tool support
        description="DeepSeek V4 Pro — mais capaz, mas SEM suporte a ferramentas",
    ),
    "deepseek-chat": ProviderConfig(
        id="deepseek-chat",
        name="DeepSeek Chat (V3)",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        supports_tools=True,  # V3 fully supports tool calling
        description="DeepSeek V3 — suporta ferramentas, recomendado para coding",
    ),
    "deepseek-reasoner": ProviderConfig(
        id="deepseek-reasoner",
        name="DeepSeek Reasoner (R1)",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-reasoner",
        api_key_env="DEEPSEEK_API_KEY",
        needs_api_key=True,
        supports_tools=True,  # R1 supports tool calling
        description="DeepSeek R1 — raciocínio estendido com ferramentas",
    ),
    "openai": ProviderConfig(
        id="openai",
        name="OpenAI",
        base_url=None,
        default_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        needs_api_key=True,
        supports_vision=True,
        supports_tools=True,
    ),
    "anthropic": ProviderConfig(
        id="anthropic",
        name="Anthropic (Claude)",
        base_url=None,
        default_model="claude-3-5-sonnet-20241022",
        api_key_env="ANTHROPIC_API_KEY",
        needs_api_key=True,
        supports_vision=True,
        supports_tools=True,
    ),
    "ollama": ProviderConfig(
        id="ollama",
        name="Ollama (local)",
        base_url="http://localhost:11434/v1",
        default_model="ollama_chat/llama3.1",
        needs_api_key=False,
        supports_tools=True,
        description="Modelos rodando localmente no Ollama",
    ),
    "puter-mistral": ProviderConfig(
        id="puter-mistral",
        name="Puter Mistral (Grátis)",
        base_url="https://api.puter.com/v2",
        default_model="mistralai/mistral-large-2512",
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        runtime="puter",
        description="Mistral via Puter.com — API gratuita e ilimitada",
    ),
    "puter-gpt-5.4-nano": ProviderConfig(
        id="puter-gpt-5.4-nano",
        name="GPT-5.4 Nano (Puter)",
        base_url=None,
        default_model="gpt-5.4-nano",
        needs_api_key=False,
        supports_vision=True,
        supports_tools=True,
        runtime="puter",
        description="GPT-5.4 Nano com visão via Puter.js no navegador",
    ),
}


def get_provider(provider_id: str) -> ProviderConfig:
    if provider_id in PROVIDERS:
        return PROVIDERS[provider_id]
    return PROVIDERS[DEFAULT_FALLBACK_ID]


# Changed from deepseek-v4-flash to deepseek-chat (V3) which supports tools
DEFAULT_FALLBACK_ID = "deepseek-chat"


def list_providers() -> list[dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "model": p.default_model,
            "needs_api_key": p.needs_api_key,
            "supports_vision": p.supports_vision,
            "supports_tools": p.supports_tools,
            "runtime": p.runtime,
            "api_key_env": p.api_key_env,
            "description": p.description,
        }
        for p in PROVIDERS.values()
    ]


def resolve_request_model(
    provider_id: str, custom_model: str, cfg: AppConfig
) -> tuple[ProviderConfig, str]:
    """Pick the effective provider + model for a request."""
    p = get_provider(provider_id)
    if custom_model.strip():
        return p, custom_model.strip()
    if cfg.custom_model.strip():
        return p, cfg.custom_model.strip()
    return p, p.default_model
