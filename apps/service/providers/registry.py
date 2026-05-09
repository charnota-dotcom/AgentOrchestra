"""Provider registry.

Service code calls `get_provider("anthropic").open_chat(card)` rather
than instantiating adapters directly.  This makes adding Gemini /
OpenAI / Ollama a one-line change.
"""

from __future__ import annotations

from apps.service.providers.anthropic import AnthropicProvider
from apps.service.providers.claude_cli import ClaudeCLIProvider
from apps.service.providers.gemini_cli import GeminiCLIProvider
from apps.service.providers.google import GoogleProvider
from apps.service.providers.ollama import OllamaProvider
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
    # CLI-backed providers first so subscription-using operators get
    # zero-friction defaults.
    register("claude-cli", ClaudeCLIProvider())
    register("gemini-cli", GeminiCLIProvider())
    register("anthropic", AnthropicProvider())
    register("google", GoogleProvider())
    register("ollama", OllamaProvider())
    # OpenAI: register when its adapter lands.


install_default_providers()
