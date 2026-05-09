"""Banks-style template engine.

Templates are Jinja2 with optional YAML-ish front-matter.  We don't
pull in PyYAML to keep the dependency surface small; we hand-parse a
small front-matter format:

    ---
    name: Broad Research
    archetype: broad-research
    variables:
      - name: goal
        kind: text
        required: true
      - name: scope_hours
        kind: number
        default: 4
    ---
    <jinja body>

That's enough for V1.  The full Banks library is a drop-in upgrade
when we want richer metadata + evaluator hooks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, select_autoescape

from apps.service.types import InstructionTemplate, TemplateVariable, utc_now

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass
class _ParsedTemplate:
    metadata: dict[str, Any]
    body: str


def _parse_frontmatter(text: str) -> _ParsedTemplate:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return _ParsedTemplate(metadata={}, body=text)
    raw = m.group(1)
    body = text[m.end() :]
    return _ParsedTemplate(metadata=_parse_yaml_subset(raw), body=body)


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """Tiny indent-based parser for our front-matter subset.

    Supports: top-level scalars, top-level lists, list items that are
    one-level mappings.  Strings, ints, bools, lists.  No flow style.
    """
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.startswith(" ") and ":" in ln:
            key, _, val = ln.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # block: either list or mapping
                items: list[Any] = []
                i += 1
                while i < len(lines) and lines[i].startswith("  "):
                    if lines[i].lstrip().startswith("- "):
                        # start a new item
                        item: dict[str, Any] = {}
                        first = lines[i].lstrip()[2:]
                        if ":" in first:
                            k, _, v = first.partition(":")
                            item[k.strip()] = _scalar(v.strip())
                        i += 1
                        while (
                            i < len(lines)
                            and lines[i].startswith("    ")
                            and not lines[i].lstrip().startswith("- ")
                        ):
                            sub = lines[i].lstrip()
                            if ":" in sub:
                                k, _, v = sub.partition(":")
                                item[k.strip()] = _scalar(v.strip())
                            i += 1
                        items.append(item)
                    else:
                        # scalar list element or mapping key under root key
                        sub = lines[i].lstrip()
                        if ":" in sub:
                            k, _, v = sub.partition(":")
                            if not isinstance(items, dict):
                                items = {}  # type: ignore[assignment]
                            items[k.strip()] = _scalar(v.strip())  # type: ignore[index]
                        i += 1
                result[key] = items
            else:
                result[key] = _scalar(val)
                i += 1
        else:
            i += 1
    return result


def _scalar(s: str) -> Any:
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lstrip("-").isdigit():
        return int(s)
    try:
        return float(s)
    except ValueError:
        pass
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------


_jinja_env = Environment(
    autoescape=select_autoescape(default_for_string=False, default=False),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)


def parse_template(text: str) -> InstructionTemplate:
    parsed = _parse_frontmatter(text)
    md = parsed.metadata
    if not md.get("name"):
        raise ValueError("template missing 'name'")
    if not md.get("archetype"):
        raise ValueError("template missing 'archetype'")
    raw_vars = md.get("variables") or []
    if not isinstance(raw_vars, list):
        raise ValueError("'variables' must be a list")
    vars_ = [
        TemplateVariable.model_validate({**v, "label": v.get("label", v.get("name", ""))})
        for v in raw_vars
    ]
    h = hashlib.sha256(parsed.body.encode("utf-8")).hexdigest()
    return InstructionTemplate(
        name=md["name"],
        archetype=md["archetype"],
        body=parsed.body,
        variables=vars_,
        version=int(md.get("version", 1)),
        content_hash=h,
        created_at=utc_now(),
    )


def load_template(path: Path) -> InstructionTemplate:
    return parse_template(path.read_text(encoding="utf-8"))


def render(template: InstructionTemplate, variables: dict[str, Any]) -> str:
    """Render `template.body` against `variables`.  Required vars must be
    present; optional vars fall back to their declared defaults.
    """
    vals: dict[str, Any] = {}
    for v in template.variables:
        if v.name in variables:
            vals[v.name] = variables[v.name]
        elif v.default is not None:
            vals[v.name] = v.default
        elif v.required:
            raise ValueError(f"missing required variable: {v.name}")
        else:
            vals[v.name] = ""
    tmpl = _jinja_env.from_string(template.body)
    return tmpl.render(**vals)
