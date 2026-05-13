# FPV Drone / Reaper Drone model â€” design

> *"An agent is the template for the window (skills, preset instructions, tone, etc).  That's the reserve agent.  The active chat is the deployed agent.  Lets call them drone blueprint and drone action to avoid all the agent confusion."*
> â€” operator, 2026-05-10

This document is the contract for the FPV Drone / Reaper Drone vocabulary alignment and the separate Staging Area node.  It locks the
schema, RPC surface, GUI surfaces, role-based authority, and migration
path.  Implementation lands in PRs #20+, gated on this document being
read and signed off.

---

## 1. Vocabulary

| Term | What it is |
|---|---|
| **Operator** | The human (you).  Sole creator of blueprints and skills. |
| **FPV Drone blueprint** | A frozen template - model, provider, persona, default skills, default references, role. Operator-set, repo-agnostic, reusable across many deployments. |
| **FPV Drone action** | An instance of an FPV Drone, *deployed* from a blueprint. Carries the runtime state: workspace binding (optional), transcript, attachments, additional skills layered on top of the blueprint, additional one-off references. |
| **FPV Drone** | A manual, browser-based robot friend (`provider="browser"`). Requires operator copy/paste. |
| **Reaper Drone** | An autonomous, CLI-based robot friend (`provider="claude-cli"`, `"gemini-cli"`, or `"codex-cli"`). Runs independently on the host. |
| **Skill** | A reusable instruction template (Superpower) stored in the database and selectable during blueprint creation or deployment. |
| **Staging Area** | A first-class gating and aggregation node that can wait, summarize, or release work downstream. |
| **App authority** | The orchestrator service's logic.  Enforces role-based access on action mutations.  Roles are operator-set on the blueprint, frozen on deploy, never self-modified. |

The product now uses **Reaper Drone** for autonomous CLI-based units and **FPV Drone** for manual browser-based units. Legacy storage and RPC names may still use older identifiers for compatibility.

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
| Worker | âœ… | âŒ | âŒ | âŒ | âœ… |
| Supervisor | âœ… | âœ… | âœ… | âœ… | âœ… |
| Courier | âœ… | âœ… | âŒ | âŒ | âœ… |
| Auditor | âŒ | âŒ | âŒ | âŒ | âœ… |

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
survives.  See Â§6.

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

`agents.list / get / create / send / set_workspace / set_references / spawn_followup / delete` â€” unchanged.  GUI label changes to "Chat" / "General chat" / "Free chat".  Migration Â§6.

---

## 4. GUI plan

### 4.1 Rail navigation

```
â”Œâ”€ Rail â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Home           â”‚
â”‚  Drones         â”‚  â† Manual (browser-based)
â”‚  Agents         â”‚  â† Autonomous (CLI-based)
â”‚  Blueprints     â”‚  â† Plan Workshop
â”‚  Skills         â”‚  â† Superpower Library
â”‚  Compose        â”‚
â”‚  Canvas         â”‚
â”‚  History        â”‚
â”‚  Limits         â”‚
â”‚  Settings       â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

- **Drones tab**: Dedicated to manual, browser-bridged units (`provider="browser"`).
- **Agents tab**: Dedicated to autonomous CLI-bridged units (`claude-cli`, `gemini-cli`).
- **Skills tab**: Standing CRUD management for reusable superpower templates (database-backed).

### 4.2 Blueprint & Action creation

Blueprints and deployments now follow context-aware paths:
- **+ Drone**: Locks to `browser` provider, shows Chat URL field.
- **+ Agent**: Locks to CLI providers, replaces manual skill entry with a mandatory selection popup.

### 4.3 Canvas tab

- **Drones palette** contains both Blueprints (templates) and Actions (deployed).
- Double-clicking an action on the canvas opens its **Edit** dialog instead of chat, allowing runtime configuration (name, workspace, skills).
- Right-clicking a manual drone action offers **"Convert to autonomous Agent..."**, promoting it to a CLI-based unit while preserving its transcript.

### 4.4 Stability & Integration (Annotator)

When auto-minting drones from UI annotations (e.g. the "Annotator" blueprint), the integration implements two critical safety guards:
1. **Mass-Minting Guard**: A readiness flag and startup delay prevent the app from spawning new drones for every existing annotation during the initial load burst.
2. **Self-Healing Deduplication**: The action log is automatically deduplicated on launch to prevent identical AI attempts from overflowing the conversation threads.

---

## 5. Authority enforcement

A `_check_authority(caller_action_id, target_action_id, capability)` helper sits in `apps/service/main.py`:

1. Look up caller action â†’ role from blueprint snapshot.
2. Look up target action â†’ owner / parent.
3. Check the matrix (Â§2.1).
4. Raise `PermissionError` (HTTP 403, GUI surfaces a friendly toast) on denial.

The orchestrator service is the *only* enforcer.  GUI never gates by role â€” it asks, gets a 403, surfaces the message.  This keeps the authority model honest.

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
| #24 | Canvas rename: ConversationNode â†’ DroneNode; "Conversations" palette â†’ "Drones".  "+ New drone" deploy dialog. |
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

---

## 9. Cross-Agent Communication ("Talk")

Agents from independent contexts can "talk" to each other when enabled by the user. This works across different models (e.g., Claude CLI and Gemini CLI) as well as within one.

### 9.1 How it works
1.  **Reference Linkage**: The operator links two drone actions (either via the "References" editor in the Drones/Agents tab or by drawing a non-directional edge on the Flow Canvas).
2.  **Transcript Injection**: When an agent is invoked, the orchestrator fetches the full conversation history of all linked peers.
3.  **Context Assembly**: These peer transcripts are formatted and injected into the agent's system prompt under a `### PEER CONTEXT` header.
4.  **Implicit Communication**: By seeing the peer's history, the agent can understand and build upon the work done in independent windows, enabling collaborative problem-solving across isolated contexts.

---

## 10. Agent Lifecycle & Skills

### 10.1 Standing Skills Management
The **Skills** tab provides a dedicated surface for managing reusable instruction templates.
- **Persistence**: Custom skills are stored in the `skills` database table.
- **Seeding**: The system auto-seeds with 20 popular archetypes (e.g., `research-deep`, `code-review`) on first launch.
- **Contextual Selection**: The selection interface (popup) only displays templates when configuring autonomous Agents, preventing bloat in manual Drone setups.

### 10.2 Lifecycle Upgrades (Conversion)
Manual Drones can be "promoted" to autonomous Agents at any time:
1.  **Canvas Promotion**: Right-click a `DroneActionNode` â†’ "Convert to autonomous Agent...".
2.  **Blueprint Promotion**: Blueprint editor â†’ "Convert to Agent...".
3.  **Result**: The manual `browser` provider is swapped for a CLI-based provider. The entire conversation transcript and all references are preserved, allowing autonomous agents to continue work initiated in the browser.