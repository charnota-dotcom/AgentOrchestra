# Agent Orchestra Template Builder Instructions

You are working on an existing Python desktop application called Agent Orchestra.

## Goal

Implement a new Template Builder feature that lets users create reusable decision-tree templates representing groups or teams of AI agents. These templates should be editable in a separate Templates tab, then deployable onto the main canvas by dragging them from a sidebar or template library.

## Important product concept

A template is not just a diagram. It is a reusable agent team blueprint.

The user should be able to:

1. Create a decision-tree template in a separate Templates tab.
2. Save that template as a reusable agent team.
3. See saved templates in a sidebar or library.
4. Drag a template from the sidebar onto the main canvas.
5. Have the template deploy neatly as a grouped set of agent cards on the main canvas.
6. Continue editing or operating those deployed agents using the existing main canvas features.

## Preserve existing behaviour

- Do not rewrite the existing canvas.
- Do not replace the existing card design.
- Do not break existing canvas operations.
- Reuse the existing agent card components and styling wherever possible.
- The deployed template should look native to the existing app, not like a separate visual system pasted onto the canvas.

## Architecture

Keep three layers separate:

### 1. Template Builder

- Used to create and edit reusable templates.
- Lives in a separate Templates tab.
- Uses a decision-tree or Mermaid-style visual layout.

### 2. Template Definition

- The source of truth.
- Stored as JSON.
- Represents nodes, edges, agent roles, commands, branches, and layout metadata.

### 3. Template Deployment

- Takes a saved template and instantiates it onto the main canvas.
- Converts template nodes into the existing main-canvas agent cards.
- Converts template edges into whatever relationship, link, dependency, or connection model the main canvas already uses.

Do not make Mermaid the source of truth.

The source of truth must be the JSON template graph.

## Required data model

Create or update the template model so that a template represents both:

- a decision workflow
- a reusable group of agents

Recommended fields:

### AgentTemplate

- `id`
- `name`
- `description`
- `version`
- `category`
- `nodes`
- `edges`
- `metadata`
- `deployment_settings`

### TemplateNode

- `id`
- `type`
- `label`
- `layout`
- `position`
- `properties`
- `card_mapping`

### TemplateEdge

- `id`
- `from_node`
- `to_node`
- `label`
- `properties`

### deployment_settings

- `default_group_name`
- `preserve_template_layout`
- `spacing`
- `create_group_boundary`
- `deployment_mode`

### card_mapping

- `target_card_type`
- `agent_role`
- `title`
- `subtitle`
- `description`
- `command`
- `instruction`
- `expected_output`
- `icon`
- `color`
- `tags`
- `metadata`

## Node types

Support these template node types:

1. `start`
2. `decision`
3. `agent_action`
4. `command`
5. `merge`
6. `end`
7. `note`

## Deployment behaviour

When a user drags a saved template onto the main canvas:

1. Create a new canvas group or logical cluster.
2. Convert each deployable template node into a main-canvas card.
3. Preserve the existing app’s card design.
4. Place the cards neatly using the template layout.
5. Preserve decision-tree structure as links, edges, dependencies, or visual relationships.
6. Avoid duplicating IDs. Generate new runtime IDs for deployed canvas items.
7. Store a reference back to the source template ID and version.
8. Allow the deployed cards to be edited independently after deployment.

## Deployment metadata

Suggested deployment metadata on each deployed card:

- `source_template_id`
- `source_template_version`
- `source_template_node_id`
- `deployed_instance_id`
- `deployed_group_id`

Template deployment should be copy-on-write:

- The template remains reusable and unchanged.
- The deployed canvas group becomes its own editable instance.

## Main canvas integration

Inspect the current main canvas implementation before coding.

Identify:

1. How existing cards are represented in the data model.
2. How existing cards are rendered.
3. How cards are positioned.
4. How links or relationships between cards are represented.
5. How drag-and-drop onto the canvas currently works, if it exists.
6. How sidebars or palettes are implemented.
7. How groups, clusters, or selections are represented, if they already exist.

Then implement the least invasive integration.

If a card component already exists:

- Reuse it.
- Add a factory or adapter that converts `TemplateNode` into the existing card model.
- Do not create a separate card style for deployed templates unless absolutely necessary.

If links already exist:

- Convert `TemplateEdge` into the existing link model.

If links do not exist yet:

- Store the relationship metadata during deployment.
- Render simple connector lines only if this fits the existing canvas architecture.

If groups already exist:

- Deploy the template as a group.

If groups do not exist:

- Create a lightweight group metadata model first.
- Do not build a complex grouping system in the first pass.
- At minimum, assign all deployed cards a shared `deployed_group_id`.

## Template Builder UI

Add a separate Templates tab with:

### Left panel

- Template list
- Search or filter
- New template
- Duplicate template
- Delete template

### Center panel

- Decision-tree editor
- Nodes and edges
- Auto layout

### Right panel

- Properties inspector
- Node properties
- Edge properties
- Deployment mapping properties

### Toolbar

- Save
- Validate
- Auto Layout
- Export Mermaid
- Test Deployment, optional
- Add to Sidebar or Publish Template, optional

## Template Library / Sidebar

Add saved templates to a reusable template library.

The main canvas should expose templates in a sidebar or palette where users can drag them onto the canvas.

Sidebar item should show:

- template name
- description
- category
- number of deployable agent nodes
- optional icon
- optional tags

## Drag-and-drop

Implement drag-and-drop from the template sidebar to the main canvas.

Drag payload should include:

- `template_id`
- `template_version`, if available
- display name
- optional preview metadata

On drop:

- Load template by ID.
- Validate template.
- Convert template to canvas items.
- Position the deployed group around the drop location.
- Use the existing card rendering style.
- Select the new group after deployment if the canvas supports selection.

## Layout during deployment

Use the template’s level-based layout where possible.

Default formula:

```text
x = drop_x + level * horizontal_spacing
y = drop_y + order * vertical_spacing
```

Recommended spacing:

- `horizontal_spacing = 280`
- `vertical_spacing = 140`

If the existing canvas has a grid system:

- Snap deployed cards to the existing grid.

If the existing card dimensions are known:

- Use actual card width and height when calculating spacing.

## Decision-tree editor layout

Use level-based layout in the Templates tab.

Each node may have:

- `layout.level`
- `layout.order`

Do not rely on manually dragged absolute positions only.

## Preserving card format

The Template Builder should visually reuse or approximate the existing card format.

Instruction:

Inspect the existing card component and extract reusable rendering logic if possible.

Ideal approach:

- Existing main canvas card component remains the canonical card UI.
- Template Builder uses a lightweight preview of that same card.
- Deployment uses the real existing card component.

Avoid:

- Creating one design for template nodes and a different design for deployed cards.
- Hardcoding card colours, fonts, padding, or icon rules in the template code if these already exist elsewhere.
- Forking the card component unnecessarily.

## Validation

Extend template validation with deployment checks.

Validation rules:

- Exactly one start node.
- Every edge references valid nodes.
- Decision nodes have at least two labelled outgoing branches.
- Agent action nodes must define an `agent_role`.
- Agent action nodes must define an instruction or command.
- Command nodes must define a command.
- Deployable nodes must have enough `card_mapping` information to create a main-canvas card.
- Warn about nodes that are documentation-only and will not deploy.
- Warn if a template has no deployable agent nodes.
- Warn if template card mappings refer to unknown card types.
- Warn if the template uses properties unsupported by the current main canvas.

## Deployment adapter

Create a dedicated adapter module.

Suggested file:

```text
template_deployment.py
```

Responsibilities:

- Load template.
- Validate template.
- Generate deployed instance IDs.
- Convert `TemplateNode` to existing canvas card model.
- Convert `TemplateEdge` to existing canvas link model.
- Apply layout around drop position.
- Create group metadata.
- Return a deployment result.

Suggested API:

```python
deploy_template_to_canvas(
    template: AgentTemplate,
    canvas_model,
    drop_position: QPointF | tuple,
    options: dict | None = None
) -> DeploymentResult
```

### DeploymentResult

- `success`
- `created_card_ids`
- `created_link_ids`
- `deployed_group_id`
- `warnings`
- `errors`

Do not put deployment logic directly inside the UI event handler.

The UI should call the deployment adapter.

## Mermaid export

Keep Mermaid export as documentation and preview only.

Template JSON remains the editable source of truth.

## Testing

Add tests for:

1. Template JSON save and load.
2. Template validation.
3. Mermaid export.
4. Template-to-card mapping.
5. Template deployment ID generation.
6. Deployment layout around a drop point.
7. Copy-on-write behaviour.
8. Deployment does not mutate the source template.
9. Unsupported node types produce warnings, not crashes.

## Implementation sequence

Follow this order.

### Step 1

Inspect current project structure.

Identify GUI framework, tab system, canvas model, card model, sidebar implementation, drag-and-drop support, and storage location.

### Step 2

Add or update template model.

### Step 3

Add repository for template save, load, list, duplicate, delete.

### Step 4

Add validator with both graph validation and deployment validation.

### Step 5

Add Mermaid exporter.

### Step 6

Add Template Builder tab.

### Step 7

Render template nodes using a visual style consistent with existing cards.

### Step 8

Add properties inspector, including card mapping fields.

### Step 9

Add template library/sidebar integration.

### Step 10

Add drag-and-drop from template sidebar onto main canvas.

### Step 11

Add template deployment adapter that converts template nodes into existing canvas cards.

### Step 12

Add tests.

### Step 13

Polish UI and error handling.

## Acceptance criteria

1. User can create templates in a separate Templates tab.
2. User can define decision-tree branches visually.
3. User can map template nodes to existing card types and agent roles.
4. User can save and reload templates.
5. Saved templates appear in a sidebar or library.
6. User can drag a template from the sidebar onto the main canvas.
7. The template deploys as a neat group of existing-style cards.
8. The deployed cards preserve the app’s existing card design.
9. The deployed group preserves the decision-tree structure as links or relationship metadata.
10. Deployed instances do not mutate the original template.
11. The existing main canvas continues to work as before.
12. The feature is modular, testable, and does not require a rewrite of the app.

## Extra instruction before coding

Do not begin by implementing visuals.

First inspect how the existing card and canvas models work.

The most important part of this feature is the adapter that maps `TemplateNode` objects into the existing canvas card model without breaking existing behaviour.
