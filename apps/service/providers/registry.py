"""Provider registry.

Service code calls `get_provider("anthropic").open_chat(card)` rather
than instantiating adapters directly.  This makes adding Gemini /
OpenAI / Ollama a one-line change.
"""

from __future__ import annotations

from apps.service.providers.anthropic import AnthropicProvider
from apps.service.providers.protocol import LLMProvider
from apps.service.types import ProviderError

_REGISTRY: dict[str, LLMProvider] = {}


def register(name: str, provider: LLMProvider) -> None:
    _REGISTRY[name] = provider


def get_provider(name: str) -> LLMProvider:
    if name not in _REGISTRY:
        raise ProviderError(f"unknown provider: {name!r}")
    return _REGISTRY[name]


def known_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def install_default_providers() -> None:
    register("anthropic", AnthropicProvider())
    # Gemini, OpenAI, Ollama: register here when their adapters land.


install_default_providers()
