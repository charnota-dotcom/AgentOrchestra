"""Provider registry.

Service code calls `get_provider("claude-cli").open_chat(card)` rather
than instantiating adapters directly.  Adding new providers is a
one-line change.

Default install (this module) registers ONLY subscription-backed
routes:

* claude-cli — Claude Code on the user's Max / Pro plan
* gemini-cli — Google Gemini CLI on the user's existing auth
* codex-cli  - OpenAI Codex CLI on the user's existing auth
* ollama    — local models, no network calls

The API-keyed adapters (`anthropic`, `google`, eventually `openai`)
are imported but deliberately **not** registered.  An operator who
wants metered API calls can opt in via
``register_api_providers()``; the GUI doesn't surface that path so
the no-usage-fees default is the only thing reachable from the UI.
"""

from __future__ import annotations

from apps.service.providers.anthropic import AnthropicProvider
from apps.service.providers.claude_cli import ClaudeCLIProvider
from apps.service.providers.codex_cli import CodexCLIProvider
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
    # Subscription / local only — no API-keyed paths in the default
    # set so accidental dispatch never bills against an API account.
    register("claude-cli", ClaudeCLIProvider())
    register("gemini-cli", GeminiCLIProvider())
    register("codex-cli", CodexCLIProvider())
    register("ollama", OllamaProvider())


def register_api_providers() -> None:
    """Opt-in: install the metered API adapters.

    Not called from any code path today.  Reserved for a future
    "advanced mode" toggle so operators who explicitly want to spend
    on the API can flip it on without editing the registry.
    """
    register("anthropic", AnthropicProvider())
    register("google", GoogleProvider())


install_default_providers()
