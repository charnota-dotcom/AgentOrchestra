"""Tests for the shared GUI preset registry.

These are pure-Python and don't import PySide6, so they run on any
CI image regardless of GUI availability.
"""

from __future__ import annotations

from apps.gui.presets import (
    DEFAULT_MODEL_INDEX,
    DEFAULT_THINKING_INDEX,
    MODE_CODING,
    MODE_FILE,
    MODE_GENERAL,
    MODE_IMAGE,
    MODEL_PRESETS,
    THINKING_PRESETS,
    ModelPreset,
    ThinkingPreset,
    compose_system,
    model_label_for,
    skills_to_system,
)


def test_default_indices_are_valid() -> None:
    assert 0 <= DEFAULT_MODEL_INDEX < len(MODEL_PRESETS)
    assert 0 <= DEFAULT_THINKING_INDEX < len(THINKING_PRESETS)


def test_every_preset_has_a_known_mode() -> None:
    valid_modes = {MODE_CODING, MODE_GENERAL, MODE_FILE, MODE_IMAGE}
    for p in MODEL_PRESETS:
        assert p.mode in valid_modes, f"{p.label}: unknown mode {p.mode!r}"


def test_display_label_contains_mode() -> None:
    for p in MODEL_PRESETS:
        assert p.mode in p.display()


def test_provider_set_is_subscription_only() -> None:
    """Default presets use the CLI providers (subscription-only path).

    The orchestrator is deliberately subscription-only by default —
    no API-key-only providers should sneak into the default picker.
    """
    seen = {p.provider for p in MODEL_PRESETS}
    assert seen <= {"claude-cli", "gemini-cli"}, f"unexpected providers: {seen}"


def test_skills_to_system_empty_returns_empty() -> None:
    assert skills_to_system("") == ""
    assert skills_to_system("   ") == ""


def test_skills_to_system_includes_skills() -> None:
    out = skills_to_system("/research-deep /cite-sources")
    assert "/research-deep" in out
    assert "/cite-sources" in out
    assert "activation" in out.lower()


def test_compose_system_drops_empty_parts() -> None:
    plain = next(p for p in MODEL_PRESETS if p.mode == MODE_CODING and p.system == "")
    no_thinking = THINKING_PRESETS[0]  # ("Off", "")
    assert no_thinking.system == ""
    composed = compose_system(plain, no_thinking, "")
    # All three sources empty → empty result.
    assert composed == ""


def test_compose_system_joins_in_order() -> None:
    general = next(p for p in MODEL_PRESETS if p.mode == MODE_GENERAL)
    hard = next(t for t in THINKING_PRESETS if t.label == "Hard")
    composed = compose_system(general, hard, "/cite-sources")
    # Mode prompt comes first, then thinking, then skills.
    assert composed.index(general.system) < composed.index(hard.system)
    assert composed.index(hard.system) < composed.index("cite-sources")
    # Joined with double newlines so each is its own paragraph.
    assert "\n\n" in composed


def test_model_label_for_known_pair() -> None:
    p = MODEL_PRESETS[DEFAULT_MODEL_INDEX]
    assert model_label_for(p.provider, p.model) == p.label


def test_model_label_for_unknown_pair() -> None:
    label = model_label_for("ollama", "llama3.2")
    assert "ollama" in label and "llama3.2" in label


def test_dataclasses_are_hashable_and_immutable() -> None:
    # @dataclass(frozen=True) buys us immutability and hashability,
    # which lets the GUI store presets in sets / dicts safely.
    p = ModelPreset("X", "claude-cli", "x", MODE_CODING, "")
    assert hash(p) == hash(p)
    t = ThinkingPreset("Off", "")
    assert hash(t) == hash(t)
