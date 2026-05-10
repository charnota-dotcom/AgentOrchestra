"""Smoke + helper tests for ``apps.gui.windows.blueprints``.

The repo's test infra is Qt-free (no pytest-qt), so we don't try to
instantiate widgets here.  These tests cover:

1. ``_split_csv`` — the comma/newline parser used by the skills +
   reference-blueprints inputs.
2. The role / provider / model-hint constants — pin the closed sets
   that show up in the dropdowns so an accidental rename gets caught.
3. Module-level import smoke (PySide6 may not be available in the
   minimal test image; we skip if so).
"""

from __future__ import annotations

import pytest


def test_split_csv_strips_blanks_and_dedupes() -> None:
    pytest.importorskip("PySide6")
    from apps.gui.windows.blueprints import _split_csv

    assert _split_csv("") == []
    assert _split_csv("  ,  ,") == []
    assert _split_csv("/a,/b,/a") == ["/a", "/b"]
    # Newline acts the same as comma so an operator can paste a list.
    assert _split_csv("/a\n/b\n  /c  ") == ["/a", "/b", "/c"]
    # Whitespace inside a token is preserved (skill names don't contain
    # commas anyway), trimmed at the edges.
    assert _split_csv("  /research-deep , /cite-sources  ") == [
        "/research-deep",
        "/cite-sources",
    ]


def test_role_labels_match_drone_role_enum() -> None:
    pytest.importorskip("PySide6")
    from apps.gui.windows.blueprints import _ROLE_LABELS
    from apps.service.types import DroneRole

    enum_values = {r.value for r in DroneRole}
    label_values = {value for value, _label in _ROLE_LABELS}
    assert enum_values == label_values, (
        "blueprints.py role dropdown must mirror the DroneRole enum exactly — "
        "if you added a role to the enum, add a label here too (and vice versa)."
    )


def test_provider_constants_have_model_hints() -> None:
    pytest.importorskip("PySide6")
    from apps.gui.windows.blueprints import _MODEL_HINTS, _PROVIDERS

    for p in _PROVIDERS:
        # Every provider in the dropdown must have at least one model
        # hint, otherwise the model combo opens empty and the operator
        # has to guess what to type.
        assert _MODEL_HINTS.get(p), f"provider {p!r} has no model hints"
