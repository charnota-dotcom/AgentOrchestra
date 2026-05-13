"""Shared presets for model + thinking-depth + skills.

Single source of truth for the Chat tab and the Canvas's
"+ New conversation" dialog.  Without this both screens used to drift
— the canvas had a narrower model list and no thinking / skills
fields, so a flow drafted on the canvas behaved subtly differently
from the same prompt typed into the Chat tab.

Conventions:

* ``MODEL_PRESETS`` is grouped by ``mode`` so the picker can render
  a clean ``Provider — Model — Mode`` layout instead of one long flat
  list.  Modes (``Coding``, ``General Chat``, ``File / artifact``,
  ``Image prompt``) are deliberately limited to keep the dropdown
  scannable.
* ``THINKING_PRESETS`` mirrors the four-step ladder the chat tab has
  always exposed (Off / Normal / Hard / Very hard).
* ``compose_system(...)`` stitches together a model preset's mode
  prompt, the thinking directive, and the operator's free-form
  ``/foo /bar baz`` skills line into one system prompt, exactly the
  way the chat tab has always done it.  Both screens use it so the
  result is identical for identical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPreset:
    """One (provider, model, mode) cell of the picker."""

    label: str  # "Claude Sonnet 4.6" — provider-agnostic friendly name
    provider: str  # "claude-cli" / "gemini-cli" / etc.
    model: str  # provider-specific model id
    mode: str  # "Coding" / "General Chat" / "File / artifact" / "Image prompt"
    system: str  # the mode's default system prompt (may be empty)

    def display(self) -> str:
        """One-line label for a flat combobox: ``<label> — <mode>``."""
        return f"{self.label}  ·  {self.mode}"


@dataclass(frozen=True)
class ThinkingPreset:
    label: str
    system: str  # appended to the assembled system prompt; empty = no-op


# ---------------------------------------------------------------------------
# Mode prompts — defined once so each model row can reuse the same text.
# ---------------------------------------------------------------------------

_MODE_GENERAL = (
    "You are a friendly general-purpose assistant.  Help with "
    "writing, research, brainstorming, planning, and everyday "
    "questions.  Do not assume the user is asking about code; "
    "if they are, treat code as one option among many.  Be "
    "concise unless asked for depth."
)

_MODE_FILE = (
    "Produce a self-contained artifact the user can save to disk.  "
    "Format your reply as the file's literal contents — no surrounding "
    "chatter, no Markdown fencing unless the file format itself uses "
    "Markdown.  If the user didn't specify a format, pick the most "
    "appropriate one (plain text, JSON, CSV, Markdown, etc.) and start "
    "your reply with a single header line `# filename.ext` so the file "
    "can be saved with a sensible name."
)

_MODE_IMAGE = (
    "The user wants an image.  Don't try to render one — instead "
    "produce a precise, vivid prompt suitable for a text-to-image "
    "generator (Midjourney / DALL-E / Stable Diffusion / Imagen).  "
    "Include subject, composition, style, lighting, mood, and any "
    "reference points.  Keep the prompt under 200 words.  Output "
    "only the prompt; no preamble."
)


MODE_CODING = "Coding"
MODE_GENERAL = "General Chat"
MODE_FILE = "File / artifact"
MODE_IMAGE = "Image prompt"


# --- Provider/Model Source of Truth ----------------------------------------

# Subset of providers we currently route to.
PROVIDERS: tuple[str, ...] = ("claude-cli", "gemini-cli", "codex-cli", "browser")

# Canonical model IDs for each provider.  Used by the Blueprints editor
# and creation dialogs to ensure only valid models are selectable.
PROVIDER_MODELS: dict[str, tuple[str, ...]] = {
    "claude-cli": ("claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"),
    "gemini-cli": (
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ),
    "codex-cli": ("gpt-5.3-codex", "gpt-5.2-codex", "gpt-5-codex", "codex-mini-latest"),
    "browser": (
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-haiku-4-5",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5-codex",
        "codex-mini-latest",
    ),
}


# --- Agent Skill Templates (OBSOLETE: Moved to service.types) -------------


# Frozen registries — exported as tuples so a buggy consumer can't
# .append() / setitem one of these and corrupt every other importer.
# Indexing semantics are unchanged (canvas + chat already iterate or
# index by position).
MODEL_PRESETS: tuple[ModelPreset, ...] = (
    # --- Coding -------------------------------------------------------
    ModelPreset("Claude Sonnet 4.6", "claude-cli", "claude-sonnet-4-6", MODE_CODING, ""),
    ModelPreset("Claude Opus 4.7", "claude-cli", "claude-opus-4-7", MODE_CODING, ""),
    ModelPreset("Claude Haiku 4.5", "claude-cli", "claude-haiku-4-5", MODE_CODING, ""),
    ModelPreset("Gemini 2.5 Pro", "gemini-cli", "gemini-2.5-pro", MODE_CODING, ""),
    ModelPreset("Gemini 2.5 Flash", "gemini-cli", "gemini-2.5-flash", MODE_CODING, ""),
    ModelPreset("Gemini 3 Pro (Preview)", "gemini-cli", "gemini-3-pro-preview", MODE_CODING, ""),
    ModelPreset(
        "Gemini 3 Flash (Preview)", "gemini-cli", "gemini-3-flash-preview", MODE_CODING, ""
    ),
    ModelPreset("GPT-5.3 Codex", "codex-cli", "gpt-5.3-codex", MODE_CODING, ""),
    ModelPreset("GPT-5.2 Codex", "codex-cli", "gpt-5.2-codex", MODE_CODING, ""),
    ModelPreset("GPT-5 Codex", "codex-cli", "gpt-5-codex", MODE_CODING, ""),
    ModelPreset("Codex Mini Latest", "codex-cli", "codex-mini-latest", MODE_CODING, ""),
    # --- General chat ------------------------------------------------
    ModelPreset(
        "Claude Sonnet 4.6", "claude-cli", "claude-sonnet-4-6", MODE_GENERAL, _MODE_GENERAL
    ),
    ModelPreset("Claude Opus 4.7", "claude-cli", "claude-opus-4-7", MODE_GENERAL, _MODE_GENERAL),
    ModelPreset("Gemini 2.5 Pro", "gemini-cli", "gemini-2.5-pro", MODE_GENERAL, _MODE_GENERAL),
    ModelPreset("GPT-5.3 Codex", "codex-cli", "gpt-5.3-codex", MODE_GENERAL, _MODE_GENERAL),
    # --- File / artifact ---------------------------------------------
    ModelPreset("Claude Sonnet 4.6", "claude-cli", "claude-sonnet-4-6", MODE_FILE, _MODE_FILE),
    ModelPreset("Gemini 2.5 Pro", "gemini-cli", "gemini-2.5-pro", MODE_FILE, _MODE_FILE),
    ModelPreset("GPT-5.3 Codex", "codex-cli", "gpt-5.3-codex", MODE_FILE, _MODE_FILE),
    # --- Image prompt ------------------------------------------------
    ModelPreset("Claude Sonnet 4.6", "claude-cli", "claude-sonnet-4-6", MODE_IMAGE, _MODE_IMAGE),
    ModelPreset("Gemini 2.5 Pro", "gemini-cli", "gemini-2.5-pro", MODE_IMAGE, _MODE_IMAGE),
    ModelPreset("GPT-5.3 Codex", "codex-cli", "gpt-5.3-codex", MODE_IMAGE, _MODE_IMAGE),
)


THINKING_PRESETS: tuple[ThinkingPreset, ...] = (
    ThinkingPreset("Off", ""),
    ThinkingPreset("Normal", "Think briefly before answering."),
    ThinkingPreset(
        "Hard",
        "Think carefully and step by step before answering. Show your reasoning.",
    ),
    ThinkingPreset(
        "Very hard",
        "Think exhaustively step by step before answering. "
        "Consider edge cases, alternative interpretations, and potential pitfalls. "
        "Show your reasoning explicitly.",
    ),
)


# Default selections — consistent across both screens.  Coding-Sonnet
# at Normal thinking is the most useful "blank-slate" entry point.
DEFAULT_MODEL_INDEX = 0
DEFAULT_THINKING_INDEX = 1


def skills_to_system(skills: str) -> str:
    """Turn the free-form ``/foo /bar baz`` skills field into a system
    directive.  We don't invoke Claude Code's first-class Skills feature
    here (it's interactive-only) but we tell the model to treat each
    ``/name`` token as an activation instruction.  Empty input → empty
    output (so the caller can drop it from the assembled prompt).
    """
    if not skills.strip():
        return ""
    return (
        "Skill directives (treat each `/name` token as an activation "
        "instruction; respond as if those skills are active for this "
        f"conversation): {skills.strip()}"
    )


def compose_system(
    preset: ModelPreset,
    thinking: ThinkingPreset,
    skills: str,
) -> str:
    """Stitch the mode, thinking-depth, and skills into one system prompt.

    Joined with blank lines so each is its own paragraph for the model.
    Empty parts are dropped.  This is the canonical assembler — both
    Chat and Canvas call it so identical inputs produce identical
    behaviour.
    """
    parts = [p for p in (preset.system, thinking.system, skills_to_system(skills)) if p]
    return "\n\n".join(parts)


def model_label_for(provider: str, model: str) -> str:
    """Human-readable label for an arbitrary (provider, model) pair.

    Used by Chat / Canvas to render the assistant-side speaker name in
    transcripts.  Falls back to ``"<provider> · <model>"`` for any pair
    that isn't in the preset table.
    """
    for p in MODEL_PRESETS:
        if p.provider == provider and p.model == model:
            return p.label
    return f"{provider} · {model}"
