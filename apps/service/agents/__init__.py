"""Named-agent runtime: persistent conversations + follow-up presets.

See ``apps/service/types.py:Agent`` for the data model.

The "follow-up" verbs (summarise, annotate, deep dive, critique,
verify) are first-class here: each one is an instruction prefix that
gets prepended to the parent agent's transcript when a follow-up
agent is spawned.  The new agent receives the full parent transcript
as conversation context, plus an explicit instruction telling it how
to relate to that context.

Why a registry rather than free-form prompts: operators wanted
"summarise Agent Smith" to be one click.  Free-form follow-ups still
work via the ``custom`` preset, but the named verbs guarantee
consistent phrasing across runs.
"""

from __future__ import annotations

# Maps preset key → (label, instruction).  Add new presets here; the
# GUI dropdown reads this dict directly so adding a verb is a
# one-line change.
FOLLOWUP_PRESETS: dict[str, tuple[str, str]] = {
    "summarise": (
        "Summarise",
        "Summarise the prior agent's findings in 5-7 bullets.  "
        "Keep claims that were well-supported; flag claims that were "
        "asserted without evidence.",
    ),
    "annotate": (
        "Annotate",
        "Annotate each substantive claim in the prior agent's response "
        "with your confidence (high / medium / low) and a one-line "
        "justification.  Don't restate the response — just the "
        "annotations, keyed to the original sentence.",
    ),
    "deep_dive": (
        "Deep dive",
        "Pick the single most interesting or contested thread from the "
        "prior agent's response and dig deeper.  Cite primary sources "
        "where you can.  End with the strongest unanswered question.",
    ),
    "critique": (
        "Critique",
        "Find weaknesses, oversights, hidden assumptions, and "
        "counterarguments in the prior agent's response.  Be specific "
        "and direct; do not hedge.",
    ),
    "verify": (
        "Verify",
        "Independently check the prior agent's claims using your own "
        "reasoning and any sources you can recall.  For each claim, "
        "label it confirmed / unconfirmed / contradicted with a "
        "one-line note.",
    ),
    "custom": (
        "Custom",
        "",  # the user supplies the instruction directly
    ),
}


def followup_instruction(preset: str, custom: str = "") -> str:
    if preset == "custom":
        return custom.strip()
    _label, body = FOLLOWUP_PRESETS.get(preset, FOLLOWUP_PRESETS["custom"])
    return body
