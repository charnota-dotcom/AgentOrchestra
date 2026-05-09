"""Pre-flight instruction linter.

Runs against rendered instructions before dispatch.  Catches the most
common ways a non-developer's prompt goes wrong: vagueness, missing
acceptance criteria, leaked secrets, oversized scope, conflicting
constraints.

Each check returns zero or more LintIssue records.  The composer surfaces
ERRORs as blocking and WARNINGs as soft hints with a "Send anyway" button.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class LintIssue:
    rule: str
    severity: Severity
    message: str
    field: str | None = None  # which form field, if any
    suggestion: str | None = None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


_VAGUE_TERMS = {
    "improve",
    "make it better",
    "tweak",
    "clean up",
    "fix things",
    "various",
    "a bit",
    "somehow",
    "etc.",
    "things",
    "stuff",
}

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style keys
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),  # Anthropic
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),  # Google API
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),  # GitHub PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
]


_DESTRUCTIVE_TERMS = re.compile(
    r"\b(rm\s+-rf|sudo\s+rm|drop\s+table|truncate\s+table|delete\s+\*|"
    r"force[-\s]push|--force|reset\s+--hard)\b",
    re.IGNORECASE,
)


_CONFLICTING_PAIRS = [
    (re.compile(r"\bquick\b", re.I), re.compile(r"\bcomprehensive|exhaustive\b", re.I)),
    (re.compile(r"\bdo not\s+touch\b", re.I), re.compile(r"\bedit|modify\b", re.I)),
]


def lint(
    text: str, *, archetype: str | None = None, variables: dict[str, Any] | None = None
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    issues.extend(_check_length(text))
    issues.extend(_check_vagueness(text))
    issues.extend(_check_secrets(text))
    issues.extend(_check_destructive(text))
    issues.extend(_check_conflicts(text))
    if archetype:
        issues.extend(_check_archetype_requirements(archetype, text, variables or {}))
    return issues


def _check_length(text: str) -> list[LintIssue]:
    n = len(text.strip())
    out: list[LintIssue] = []
    if n < 30:
        out.append(
            LintIssue(
                rule="too-short",
                severity=Severity.ERROR,
                message="Instruction is too short to be actionable.",
                suggestion="Add at least: a goal, one constraint, and a success criterion.",
            )
        )
    elif n < 80:
        out.append(
            LintIssue(
                rule="brief",
                severity=Severity.WARNING,
                message="Instruction is very brief — agents do better with explicit success criteria.",
            )
        )
    return out


def _check_vagueness(text: str) -> list[LintIssue]:
    lc = text.lower()
    hits = sorted({term for term in _VAGUE_TERMS if term in lc})
    if hits:
        return [
            LintIssue(
                rule="vague-language",
                severity=Severity.WARNING,
                message=f"Vague phrases found: {', '.join(hits)}.",
                suggestion="Replace with concrete behavior or measurable outcome.",
            )
        ]
    return []


def _check_secrets(text: str) -> list[LintIssue]:
    for p in _SECRET_PATTERNS:
        if p.search(text):
            return [
                LintIssue(
                    rule="leaked-secret",
                    severity=Severity.ERROR,
                    message="What looks like a secret/API key is in the instruction.",
                    suggestion="Remove the secret. Keys belong in the OS keyring, not in prompts.",
                )
            ]
    return []


def _check_destructive(text: str) -> list[LintIssue]:
    if _DESTRUCTIVE_TERMS.search(text):
        return [
            LintIssue(
                rule="destructive-language",
                severity=Severity.WARNING,
                message="Instruction mentions a destructive operation.",
                suggestion="Confirm the agent has approval gates for destructive actions enabled on this card.",
            )
        ]
    return []


def _check_conflicts(text: str) -> list[LintIssue]:
    for a, b in _CONFLICTING_PAIRS:
        if a.search(text) and b.search(text):
            return [
                LintIssue(
                    rule="conflicting-constraints",
                    severity=Severity.WARNING,
                    message="The instruction contains potentially conflicting constraints.",
                    suggestion="Reread; if both are intended, make the trade-off explicit.",
                )
            ]
    return []


_ARCHETYPE_RULES = {
    "qa-on-fix": {
        "must_mention": [r"\brun\b", r"\bdiff\b|\bchanges?\b"],
        "explanation": "QA archetype must reference a target run or its diff.",
    },
    "broad-research": {
        "must_mention": [r"\bgoal\b|\bquestion\b|\btopic\b"],
        "explanation": "Research archetype must state a goal or topic.",
    },
}


def _check_archetype_requirements(
    archetype: str, text: str, variables: dict[str, Any]
) -> list[LintIssue]:
    rules = _ARCHETYPE_RULES.get(archetype)
    if not rules:
        return []
    out: list[LintIssue] = []
    lc = text.lower()
    missing = []
    for pattern in rules["must_mention"]:
        if not re.search(pattern, lc):
            missing.append(pattern)
    if missing:
        out.append(
            LintIssue(
                rule=f"archetype:{archetype}:missing-context",
                severity=Severity.WARNING,
                message=str(rules["explanation"]),
                suggestion="Add the relevant fields in the wizard before dispatching.",
            )
        )
    return out


def has_blocking(issues: list[LintIssue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)
