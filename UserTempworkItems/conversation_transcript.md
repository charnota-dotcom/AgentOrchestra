# Conversation Transcript

## User

Help me create a feature spec for an app which is orchestsrting ai agents.

The idea is that the staging is done on a canvas with cards. The types of cards are: fpv drones, which are effectively manually compiled batches of text to be processed by the ai. It could be a conversation with an ai to be harvested for ideas, test results, shopping receipts etc. 

Second type of card is an ai agent. These can vary from mission control to implemented, auditors, Web searchers and so on. Suggest here what other types might be useful.

Third type of card is the staging area, basically a pause in the process where all incoming actors need to arrive before proceeding further. Staging area itself may be an agent which analyses incoming info and issues further orders.

All the cards are connected by lines. Directional lines are sequential, simple lines are consecutive.

Different lineups can be saved as templates for further reuse.

All cards are given instructions to log own token use, so that the economy can be assessed.

Let's call agents Reaper drones.

All the Reapers are agents ai, with various model presets. Upon activation they launch their own cli window, but also interact via the app with other cards.

Can you draft a concise and structured description of this feature and app functionality required to support it

## Assistant

## Feature Spec: AI Agent Orchestration Canvas

### Working title

**Reaper Command Canvas**

A visual orchestration app for staging, coordinating, running, and auditing AI agent workflows. Users build agent pipelines on a canvas using connected cards, where each card represents either source material, an AI agent, or a staging/checkpoint step in the process.

The system is designed to let users create reusable “lineups” of AI agents, route information between them, pause execution at controlled points, and track the cost and efficiency of each workflow.

---

# 1. Core Concept

The app provides a **canvas-based workflow builder** where users arrange cards and connect them with lines.

Each workflow is made of:

1. **FPV Drone cards**  
   Manually curated batches of source material.

2. **Reaper Drone cards**  
   AI agents with specific roles, model presets, prompts, tools, and execution behaviour.

3. **Staging Area cards**  
   Synchronisation, review, aggregation, and decision points where multiple inputs are gathered before the workflow proceeds.

Cards are connected by lines that define how information and control move through the workflow.

Completed workflows can be saved as reusable **templates**.

---

# 2. Card Types

## 2.1 FPV Drone Card

An **FPV Drone** is a user-controlled source-input card. It contains manually assembled text or files that are sent into the workflow for AI processing.

Example uses:

- A conversation with an AI to harvest ideas from.
- User notes.
- Test results.
- Shopping receipts.
- Research snippets.
- Meeting transcripts.
- Bug reports.
- Logs.
- Emails.
- Customer feedback.
- Product requirements.
- Competitive research.

### Required functionality

Each FPV Drone card should support:

- Manual text entry.
- File attachment or import.
- Pasted conversations.
- Structured metadata fields.
- Optional tagging.
- Optional source attribution.
- A preview of the content.
- Content locking, so the source material cannot change mid-run.
- Token estimate before execution.
- Version history for edited inputs.

### Key settings

- Card name.
- Description.
- Input format: plain text, markdown, JSON, CSV, transcript, mixed.
- Intended downstream use.
- Whether content should be passed in full, summarised, chunked, or embedded.
- Maximum token budget.
- Privacy/sensitivity flag.

---

## 2.2 Reaper Drone Card

A **Reaper Drone** is an AI agent card. Each Reaper has a role, model preset, system prompt, available tools, execution mode, and output contract.

When activated, a Reaper launches its own CLI window while also communicating with the canvas app and connected cards.

### Example Reaper types

#### Mission Control Reaper

Coordinates the full workflow. Assigns tasks, interprets results, and decides which Reapers should run next.

#### Implementer Reaper

Writes code, drafts documents, generates assets, or performs concrete production work.

#### Auditor Reaper

Reviews another Reaper’s output for correctness, policy compliance, completeness, cost, and hallucination risk.

#### Web Search Reaper

Searches the web, gathers current information, compares sources, and returns cited findings.

#### Research Reaper

Explores a topic deeply, groups findings, identifies unknowns, and proposes next research steps.

#### Summariser Reaper

Condenses long inputs into structured summaries, briefs, timelines, or action lists.

#### Extractor Reaper

Pulls structured facts from messy input, such as names, prices, dates, claims, requirements, risks, or entities.

#### Planner Reaper

Turns goals into step-by-step execution plans, task breakdowns, or project roadmaps.

#### Critic Reaper

Challenges assumptions, finds weak points, and proposes alternative interpretations.

#### QA Reaper

Runs test scenarios, validates expected behaviour, and reports failures.

#### Refiner Reaper

Improves tone, formatting, logic, readability, or structure of another card’s output.

#### Router Reaper

Classifies incoming information and sends it to different downstream cards based on rules.

#### Memory Reaper

Stores, retrieves, and compares historical workflow context, reusable decisions, or prior outputs.

#### Cost Controller Reaper

Monitors token usage, model costs, tool calls, and budget limits across the workflow.

#### Security Reaper

Checks for sensitive data, unsafe instructions, secrets, private information, or policy violations.

#### Human Handoff Reaper

Prepares a clean review package for a human decision before continuing.

### Required functionality

Each Reaper card should support:

- Role selection.
- Model preset selection.
- Custom system prompt.
- Custom task instructions.
- Input mapping from connected upstream cards.
- Output schema definition.
- Tool permissions.
- Execution mode: manual, automatic, approval-required, scheduled.
- Token budget.
- Cost budget.
- Retry policy.
- Failure handling.
- Logging.
- CLI launch behaviour.
- Status display on the canvas.

### Reaper status states

- Not configured.
- Ready.
- Waiting for input.
- Running.
- Waiting for human approval.
- Completed.
- Failed.
- Cancelled.
- Skipped.
- Over budget.
- Needs review.

---

## 2.3 Staging Area Card

A **Staging Area** is a pause, checkpoint, aggregation, or synchronisation card.

It waits for all required incoming actors to arrive before the workflow continues.

A Staging Area may be passive, meaning it simply waits and passes information onward, or active, meaning it contains an agent that analyses incoming information and issues further instructions.

### Example uses

- Wait for several Reapers to finish before proceeding.
- Combine outputs from multiple branches.
- Compare competing answers.
- Let a human approve or reject progress.
- Run a consistency check.
- Decide which path should execute next.
- Produce a consolidated briefing.
- Stop the workflow if cost, quality, or safety thresholds are not met.

### Required functionality

Each Staging Area should support:

- Required incoming cards.
- Optional incoming cards.
- Wait conditions.
- Timeout rules.
- Human approval mode.
- Aggregation mode.
- Decision rules.
- Optional embedded Reaper agent.
- Output routing.
- Status tracking.
- Summary of all received inputs.
- Token and cost accounting.

### Staging modes

- **Wait for all**: Continue only once every required input has arrived.
- **Wait for any**: Continue once the first valid input arrives.
- **Threshold**: Continue when a defined number or percentage of inputs arrive.
- **Manual release**: Continue only after user approval.
- **Agent decision**: Embedded Reaper decides whether to continue, branch, retry, or stop.
- **Budget gate**: Continue only if token or cost budget remains.
- **Quality gate**: Continue only if audit criteria are passed.

---

# 3. Connection Types

Cards are connected by lines on the canvas.

## 3.1 Directional Line

A directional line defines a **sequential dependency**.

Example:

`FPV Drone → Summariser Reaper → Auditor Reaper → Staging Area`

Meaning:

- The downstream card runs after the upstream card completes.
- Output from the upstream card becomes input to the downstream card.
- Execution order is enforced.

### Required behaviour

- Must show arrow direction.
- Must define input/output mapping.
- May include conditions.
- May include transformation rules.
- May be enabled or disabled.
- Should display recent transfer status.

---

## 3.2 Simple Line

A simple line defines a **consecutive or associated relationship** between cards.

It does not necessarily enforce strict execution order. It shows that cards are related, grouped, or intended to be considered together.

Example uses:

- Group related Reapers.
- Link alternate agents for comparison.
- Indicate shared context.
- Mark cards as belonging to the same phase.
- Show that outputs should be available as context but not necessarily trigger execution.

### Required behaviour

- No arrow.
- Can optionally share context.
- Can optionally define soft dependency.
- Can be converted into a directional line.
- Should not run downstream cards automatically unless configured.

---

# 4. Workflow Execution

The app should support visual orchestration from input to final output.

### Required execution capabilities

- Start workflow from any card.
- Run a whole canvas.
- Run selected cards only.
- Pause, resume, cancel, or retry cards.
- Execute branches in parallel where dependencies allow.
- Enforce staging gates.
- Display live execution status.
- Show token use and cost in real time.
- Show error states.
- Allow manual override.
- Preserve every run as an auditable record.

### Execution lifecycle

1. User builds or loads a canvas lineup.
2. User configures FPV Drones, Reapers, Staging Areas, and connections.
3. User validates the workflow.
4. App estimates cost and token usage.
5. User starts the run.
6. Cards execute according to dependency rules.
7. Reapers launch CLI windows when activated.
8. Outputs are passed between cards.
9. Staging Areas pause or route execution.
10. Final outputs are collected and saved.
11. Token usage, costs, logs, and outputs are stored in the run history.

---

# 5. Templates and Reuse

Users can save any configured lineup as a reusable template.

### Template functionality

Templates should preserve:

- Card layout.
- Card types.
- Reaper roles.
- Model presets.
- Prompts.
- Tool permissions.
- Connection structure.
- Staging rules.
- Token budgets.
- Output schemas.
- Default labels and metadata.

Templates should not necessarily preserve:

- Sensitive FPV source content.
- Run-specific outputs.
- Temporary logs.
- CLI session state.

### Template actions

- Save as template.
- Duplicate template.
- Version template.
- Share template.
- Import/export template.
- Create new run from template.
- Compare template versions.

---

# 6. Token and Cost Economy

Every card must log its own token use so the overall economy of the workflow can be assessed.

### Required tracking

For each card:

- Input tokens.
- Output tokens.
- Total tokens.
- Model used.
- Tool calls used.
- Estimated cost.
- Actual cost, where available.
- Runtime duration.
- Retry count.
- Error count.
- Cached tokens, where supported.
- Cost per useful output.
- Cost by workflow phase.

For each full run:

- Total tokens.
- Total cost.
- Cost per card.
- Cost per Reaper type.
- Cost per branch.
- Most expensive card.
- Failed-cost waste.
- Retry-cost waste.
- Average output quality score, if auditing is enabled.
- Budget remaining.

### Budget controls

- Per-card token budget.
- Per-card cost budget.
- Per-run cost budget.
- Hard stop when budget is exceeded.
- Warning threshold.
- Budget gate staging cards.
- Cost forecast before execution.

---

# 7. CLI Window Behaviour

Each Reaper launches its own CLI window on activation.

### CLI requirements

- Display Reaper name, role, run ID, and current task.
- Stream model output.
- Show token usage and cost.
- Show incoming context.
- Show outgoing result.
- Show tool calls.
- Allow manual interruption.
- Allow manual notes or commands, if permitted.
- Sync all activity back to the canvas app.
- Preserve CLI logs in run history.

### Canvas and CLI sync

The canvas should show a simplified status view, while the CLI shows detailed execution.

The Reaper should be able to:

- Receive inputs from upstream cards.
- Report progress.
- Request clarification.
- Send output to downstream cards.
- Log costs.
- Emit warnings.
- Mark itself complete, failed, blocked, or waiting.

---

# 8. Canvas Interface Requirements

### Canvas functionality

- Drag-and-drop cards.
- Zoom and pan.
- Connect cards with lines.
- Edit card configuration in a side panel.
- Group cards visually.
- Collapse and expand card details.
- Display status badges.
- Display cost badges.
- Display token badges.
- Highlight active execution paths.
- Show blocked dependencies.
- Show failed cards.
- Show completed cards.
- Search cards by name, type, tag, or role.
- Minimap for large workflows.
- Undo and redo.
- Autosave.
- Manual layout and auto-layout options.

### Card display should show

- Card name.
- Card type.
- Status.
- Last run time.
- Token count.
- Cost estimate.
- Error indicator.
- Output preview.
- Incoming and outgoing connection count.

---

# 9. Data Model

## Card

A card should include:

- Card ID.
- Card type.
- Name.
- Description.
- Position on canvas.
- Configuration.
- Inputs.
- Outputs.
- Status.
- Logs.
- Token usage.
- Cost usage.
- Version.
- Created by.
- Created at.
- Updated at.

## Connection

A connection should include:

- Connection ID.
- Source card ID.
- Target card ID.
- Connection type: directional or simple.
- Conditions.
- Input/output mapping.
- Transformation rules.
- Enabled status.
- Created at.
- Updated at.

## Workflow

A workflow should include:

- Workflow ID.
- Name.
- Description.
- Cards.
- Connections.
- Templates used.
- Run history.
- Global budget.
- Global settings.
- Created by.
- Updated by.
- Version.

## Run

A run should include:

- Run ID.
- Workflow ID.
- Triggering user.
- Start time.
- End time.
- Status.
- Card execution records.
- Outputs.
- Logs.
- Token totals.
- Cost totals.
- Errors.
- Audit results.

---

# 10. Validation Rules

Before execution, the app should validate the lineup.

### Validation checks

- All required card fields are configured.
- All Reapers have model presets.
- All Reapers have prompts.
- FPV Drone content is present.
- Required staging inputs are defined.
- Directional connections do not create invalid loops unless loops are explicitly allowed.
- Output schemas are compatible with downstream inputs.
- Token budget is sufficient.
- Required tool permissions are granted.
- CLI launch settings are valid.
- No disconnected required cards.
- No ambiguous execution order where strict order is required.

---

# 11. Permissions and Safety

### Required controls

- Role-based access to workflows and templates.
- Permission to run workflows.
- Permission to edit templates.
- Permission to approve staging gates.
- Permission to use web search.
- Permission to access files.
- Permission to launch CLI sessions.
- Permission to use expensive models.
- Permission to export logs or outputs.

### Safety features

- Sensitive data detection.
- Secret detection.
- Prompt injection warnings.
- Tool-use approval gates.
- Web-source citation requirements.
- Audit trail.
- Human approval mode.
- Reaper output review.
- Budget enforcement.

---

# 12. Minimum Viable Product

### MVP should include

- Canvas with draggable cards.
- Three card types: FPV Drone, Reaper Drone, Staging Area.
- Directional connections.
- Basic simple connections.
- Manual text input for FPV cards.
- Reaper role, prompt, and model preset configuration.
- Basic execution engine.
- Manual run and retry.
- Basic staging gate: wait for all.
- CLI window launch per Reaper.
- Token and cost logging per card.
- Run history.
- Save workflow as template.
- Load workflow from template.
- Basic validation before run.

### Not required for MVP

- Advanced branching.
- Full marketplace of Reaper types.
- Multi-user permissions.
- Complex cost analytics.
- Automated template version comparison.
- Real-time collaborative editing.
- Advanced memory system.
- Complex human approval chains.

---

# 13. Example Workflow

### Research and Implementation Lineup

1. FPV Drone: Product idea notes.
2. FPV Drone: Customer feedback.
3. Research Reaper: Extracts themes and opportunities.
4. Web Search Reaper: Checks market and competitor context.
5. Staging Area: Waits for research and web findings.
6. Planner Reaper: Creates implementation plan.
7. Implementer Reaper: Drafts feature spec or code.
8. Auditor Reaper: Reviews quality and risks.
9. Staging Area: Human approval.
10. Refiner Reaper: Produces final output.

---

# 14. Product Summary

**Reaper Command Canvas** is a visual AI orchestration environment where users compose reusable agent workflows from source-input cards, AI-agent cards, and staging gates.

The app should make AI workflows visible, controllable, reusable, and economically measurable. Its core value is not simply running agents, but giving users a command layer for planning, sequencing, inspecting, pausing, auditing, and reusing multi-agent AI work.

## User

copy this conversation verbatim in its entirety in the .md format, produce the file
