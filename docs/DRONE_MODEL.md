# Drone model — design

> *"An agent is the template for the window (skills, preset instructions, tone, etc).  That's the reserve agent.  The active chat is the deployed agent.  Lets call them drone blueprint and drone action to avoid all the agent confusion."*
> — operator, 2026-05-10

This document is the contract for the drone-model rename.  It locks the
schema, RPC surface, GUI surfaces, role-based authority, and migration
path.  Implementation lands in PRs #20+, gated on this document being
read and signed off.

---

## 1. Vocabulary

| Term | What it is |
|---|---|
| **Operator** | The human (you).  Sole creator of blueprints. |
| **Drone blueprint** | A frozen template — model, provider, persona, default skills, default references, role.  Operator-set, repo-agnostic, reusable across many deployments. |
| **Drone action** | An instance of a drone, *deployed* from a blueprint.  Carries the runtime state: workspace binding (optional), transcript, attachments, additional skills layered on top of the blueprint, additional one-off references. |
| **General chat** | A free-form chat in the Chat tab, no blueprint involved.  Same as a normal web AI UI.  Today's `Agent` rows fall here by default; new "general chats" continue to live alongside drones. |
| **External endpoint** | The thing on the other side of the wire — Claude Code CLI, Gemini CLI, an MCP-tool surface, etc.  Whether it considers itself an "agent" is irrelevant; we treat all uniformly. |
| **App authority** | The orchestrator service's logic.  Enforces role-based access on action mutations.  Roles are operator-set on the blueprint, frozen on deploy, never self-modified. |

The word *agent* exits operator-facing copy entirely.  We keep it only
in references to *external* concepts ("Claude Code's agent loop") to
preserve technical accuracy.

---

## 2. Schema

### 2.1 Roles

```python
class DroneRole(StrEnum):
    """Roles dictating cross-action mutation rights.  Operator picks
    one when creating the blueprint; frozen on deploy.  The orchestrator
    enforces the matrix below at every cross-action RPC entry point.
    """
    WORKER     = "worker"      # default; touches its own state only
    SUPERVISOR = "supervisor"  # can append refs / attachments / skills
                               # to a subordinate action
    COURIER    = "courier"     # can hand off context to a peer; used
                               # by spawn-followup
    AUDITOR    = "auditor"     # read-only across actions
```

Authority matrix (rows = caller's role, columns = capability):

| | Self-state | Append refs to peer | Append attachments to peer | Append skills to peer | Read-only peer |
|---|---|---|---|---|---|
| Worker | ✅ | ❌ | ❌ | ❌ | ✅ |
| Supervisor | ✅ | ✅ | ✅ | ✅ | ✅ |
| Courier | ✅ | ✅ | ❌ | ❌ | ✅ |
| Auditor | ❌ | ❌ | ❌ | ❌ | ✅ |

### 2.2 Tables

```sql
CREATE TABLE drone_blueprints (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'worker',
    provider     TEXT NOT NULL,                  -- 'claude-cli' / 'gemini-cli' / etc.
    model        TEXT NOT NULL,
    system_persona  TEXT NOT NULL DEFAULT '',    -- operator-typed tone / role
    skills       TEXT NOT NULL DEFAULT '[]',     -- JSON list of /tokens
    reference_blueprint_ids TEXT NOT NULL DEFAULT '[]',  -- JSON; defaults applied at deploy
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE drone_actions (
    id                     TEXT PRIMARY KEY,
    blueprint_id           TEXT NOT NULL REFERENCES drone_blueprints(id),
    blueprint_snapshot     TEXT NOT NULL,        -- JSON, frozen at deploy
    workspace_id           TEXT REFERENCES workspaces(id),  -- optional
    additional_skills      TEXT NOT NULL DEFAULT '[]',
    additional_reference_action_ids TEXT NOT NULL DEFAULT '[]',
    transcript             TEXT NOT NULL DEFAULT '[]',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE drone_action_attachments (
    id              TEXT PRIMARY KEY,
    action_id       TEXT NOT NULL REFERENCES drone_actions(id) ON DELETE CASCADE,
    -- columns mirror the existing `attachments` table.
    ...
);
```

The existing `agents` table is **renamed conceptually** to *general chats*
in the GUI but **kept as the same table** on disk so today's data
survives.  See §6.

### 2.3 Pydantic types (`apps/service/types.py`)

```python
class DroneBlueprint(BaseModel):
    id: str = Field(default_factory=long_id)
    name: str
    description: str = ""
    role: DroneRole = DroneRole.WORKER
    provider: str
    model: str
    system_persona: str = ""
    skills: list[str] = []
    reference_blueprint_ids: list[str] = []
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

class DroneAction(BaseModel):
    id: str = Field(default_factory=long_id)
    blueprint_id: str
    blueprint_snapshot: dict           # frozen copy of blueprint at deploy
    workspace_id: str | None = None
    additional_skills: list[str] = []
    additional_reference_action_ids: list[str] = []
    transcript: list[dict[str, str]] = []
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def effective_skills(self) -> list[str]:
        return list(self.blueprint_snapshot.get("skills", [])) + list(self.additional_skills)
```

---

## 3. RPC surface

### 3.1 Blueprints (operator-only writes)

| Method | Params | Returns | Auth |
|---|---|---|---|
| `blueprints.list` | `{}` | `[DroneBlueprint]` | operator |
| `blueprints.get` | `{id}` | `DroneBlueprint` | operator |
| `blueprints.create` | `{name, description, role, provider, model, system_persona, skills, reference_blueprint_ids}` | `DroneBlueprint` | operator |
| `blueprints.update` | `{id, ...fields}` | `DroneBlueprint` (with `version+1`) | operator |
| `blueprints.delete` | `{id}` | `{deleted: bool, in_flight_actions: int}` (refuses if any actions linked unless `force: true`) | operator |

### 3.2 Drone actions

| Method | Params | Returns | Auth |
|---|---|---|---|
| `drones.deploy` | `{blueprint_id, workspace_id?, additional_skills?, additional_reference_action_ids?}` | `DroneAction` | operator |
| `drones.list` | `{blueprint_id?}` filter | `[DroneAction]` | operator |
| `drones.get` | `{id}` | `DroneAction` (enriched with workspace_name + path + blueprint name) | operator |
| `drones.send` | `{action_id, message, attachment_ids?}` | `{reply, action}` | operator |
| `drones.delete` | `{id}` | `{deleted: bool}` | operator |
| `drones.append_reference` | `{action_id, ref_action_id}` | `DroneAction` | role-gated (Supervisor / Courier on a peer; self always) |
| `drones.append_skill` | `{action_id, token}` | `DroneAction` | role-gated (Supervisor on a peer; self always) |
| `drones.append_attachment` | `{action_id, attachment_id}` | `DroneAction` | role-gated (Supervisor on a peer; self always) |

### 3.3 General chats (today's `agents.*`, kept verbatim)

`agents.list / get / create / send / set_workspace / set_references / spawn_followup / delete` — unchanged.  GUI label changes to "Chat" / "General chat" / "Free chat".  Migration §6.

---

## 4. GUI plan

### 4.1 New rail tab — **Blueprints**

```
┌─ Rail ──────────┐
│  Home           │
│  Chat           │
│  Drones         │  ← new (formerly "Agents")
│  Blueprints     │  ← new
│  Compose        │
│  Canvas         │
│  History        │
│  Limits         │
│  Settings       │
└─────────────────┘
```

The **Blueprints** tab:
- Sidebar list of blueprints (name + role chip).
- Centre: editable form (name, role dropdown, provider, model, persona, skills picker (reuses `apps/gui/widgets/skills_picker.py`), default references picker).
- "+ New blueprint" button.
- "Delete" button refuses if any in-flight actions reference the blueprint, unless operator confirms force.

The **Drones** tab (today's "Agents" tab):
- Sidebar list of deployed actions, sorted by recency.  Each row shows action's blueprint name + role chip.
- Centre: same chat-style transcript view as today, with the workspace banner, git-status banner, references editor, attachment paperclip.
- Right: "Spawn follow-up" replaced with "Re-deploy from same blueprint" + "Append reference / attachment" (role-gated, hidden when not allowed).
- "+ New drone" button opens a deploy dialog: pick blueprint, pick workspace (optional), pick additional skills (one-off), confirm.

### 4.2 Canvas tab

- **Conversations palette → Drones palette.**
- "+ New drone" replaces "+ New conversation".  Opens the same deploy dialog as above.
- ConversationNode renamed `DroneNode`; subtitle still shows model + workspace.
- Lineage edges still draw between drones spawned from the same blueprint via Courier / Supervisor follow-ups.

### 4.3 Chat tab

- Unchanged.  Still mints a "general chat" `Agent` row on first send.
- Optional later: "Save as blueprint" button on the Chat header to promote settings into a blueprint.

---

## 5. Authority enforcement

A `_check_authority(caller_action_id, target_action_id, capability)` helper sits in `apps/service/main.py`:

1. Look up caller action → role from blueprint snapshot.
2. Look up target action → owner / parent.
3. Check the matrix (§2.1).
4. Raise `PermissionError` (HTTP 403, GUI surfaces a friendly toast) on denial.

The orchestrator service is the *only* enforcer.  GUI never gates by role — it asks, gets a 403, surfaces the message.  This keeps the authority model honest.

---

## 6. Migration

**Path B chosen** (per operator decision in design discussion): existing `Agent` rows stay as **general chats**, not auto-promoted to blueprints / actions.

Concretely:
- Today's `agents` table keeps its current schema and data.  Renamed to **General chats** in the GUI; columns unchanged.
- New tables `drone_blueprints` + `drone_actions` are added by `EventStore._migrate` (additive).
- The GUI's old "Agents" tab is renamed to "Drones" but **shows only `drone_actions`**, not `agents`.
- General chats are accessible only via the Chat tab (existing surface).
- Optional later: a "Save settings as blueprint" button on each Chat row that mints a blueprint from the chat's model/system/skills, and a "Convert to drone action" follow-up that links the chat as the new action's transcript.  Marked **Later** in the roadmap.

No data lost.  Operator's existing chats keep working the same way.  Drones are net-new.

---

## 7. Where the rename lands in commits

Suggested PR sequence:

| PR | Scope |
|---|---|
| #20 | Schema: types.py + store/events.py + schema.sql for drone_blueprints / drone_actions / drone_action_attachments + DroneRole enum.  No GUI yet.  Tests for the store CRUD round-trip. |
| #21 | RPC layer: `blueprints.*` + `drones.*` handlers + `_check_authority` helper + role-gating tests.  No GUI yet. |
| #22 | New **Blueprints** tab.  Operator can create / edit / delete blueprints via the GUI. |
| #23 | "Drones" tab (rename of today's Agents tab) + deploy dialog + role chips + role-gated buttons. |
| #24 | Canvas rename: ConversationNode → DroneNode; "Conversations" palette → "Drones".  "+ New drone" deploy dialog. |
| #25 | Sweep-and-burn: rename `Agent` references in user-facing copy across README, ROADMAP, CHANGELOG.  Delete obsolete tooltips.  Tests. |

Each PR is independently reviewable.  Each adds new code without
deleting existing flows, so `main` stays usable throughout.  PR #25 is
the one that flips the operator-facing copy; before it, drones and
"general chats" coexist quietly.

---

## 8. Risks + open questions

- **Spawn follow-up** today mints a child `Agent` with the parent's transcript inlined.  In drone-world the child should be a new drone-action deployed from the parent's blueprint, with the transcript folded in as a reference.  Confirm.
- **References across the boundary**: can a drone-action reference a general-chat `agent`?  The honest answer is yes (any text transcript works as context), but it muddles the two pools.  Current proposal: yes, allowed; the dialog shows two sections "Drone actions" and "General chats" so the operator picks intentionally.
- **Role escalation / downgrade after deploy**: an action's role is frozen with the blueprint snapshot.  If the operator later edits the blueprint to change role, in-flight actions keep their old role (snapshot).  New deploys from the edited blueprint pick up the new role.  Standard immutable-snapshot semantics.
- **Roles beyond the four**: the `DroneRole` enum is closed.  If "moderator" or "fact-checker" comes up later we'd add it; not worth designing custom-role plumbing in v1.
