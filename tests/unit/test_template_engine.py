"""Template engine: front-matter parsing + Jinja rendering."""

from __future__ import annotations

import pytest

from apps.service.templates.engine import load_template, parse_template, render


def test_parse_minimal_template() -> None:
    text = (
        "---\n"
        "name: Demo\n"
        "archetype: demo\n"
        "version: 1\n"
        "variables:\n"
        "  - name: goal\n"
        "    label: Goal\n"
        "    kind: text\n"
        "    required: true\n"
        "---\n"
        "Hello {{ goal }}.\n"
    )
    t = parse_template(text)
    assert t.name == "Demo"
    assert t.archetype == "demo"
    assert len(t.variables) == 1
    assert t.variables[0].name == "goal"
    assert t.content_hash


def test_render_substitutes_variables() -> None:
    t = parse_template(
        "---\nname: X\narchetype: x\nvariables:\n"
        "  - name: greeting\n    label: g\n    kind: string\n    required: true\n"
        "---\n{{ greeting }}, world.\n"
    )
    assert render(t, {"greeting": "Hi"}).strip() == "Hi, world."


def test_render_missing_required_raises() -> None:
    t = parse_template(
        "---\nname: X\narchetype: x\nvariables:\n"
        "  - name: name\n    label: N\n    kind: string\n    required: true\n"
        "---\n{{ name }}\n"
    )
    with pytest.raises(ValueError, match="missing required"):
        render(t, {})


def test_render_uses_default() -> None:
    t = parse_template(
        "---\nname: X\narchetype: x\nvariables:\n"
        "  - name: who\n    label: w\n    kind: string\n    required: false\n    default: world\n"
        "---\nHi {{ who }}.\n"
    )
    assert render(t, {}).strip() == "Hi world."


def test_load_seed_templates(tmp_path) -> None:
    from pathlib import Path

    pack = Path("packs/archetypes")
    if not pack.exists():
        pytest.skip("packs not present in this checkout")
    for f in pack.glob("*.md"):
        t = load_template(f)
        assert t.archetype
        assert t.name
        assert t.variables


def test_phase2_mapper_templates_include_required_contracts() -> None:
    from pathlib import Path

    ui_path = Path("packs/archetypes/ui_architect.md")
    logic_path = Path("packs/archetypes/logic_liaison.md")
    if not ui_path.exists() or not logic_path.exists():
        pytest.skip("phase2 mapper templates not present in this checkout")

    ui = ui_path.read_text(encoding="utf-8").lower()
    logic = logic_path.read_text(encoding="utf-8").lower()

    assert "read-only analysis only" in ui
    assert "read-only analysis only" in logic
    assert "mermaid.js diagram" in ui
    assert "mermaid.js diagram" in logic
    assert r"\.addlayout\(|\.addwidget\(" in ui
    assert r"qthread|qtcore\.qthread|movetothread\(" in logic
