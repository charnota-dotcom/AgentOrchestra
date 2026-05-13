# AgentOrchestra Quick Start

This guide explains the current product vocabulary:

- **Blueprint** = the reusable template
- **FPV Drone** = a manual browser-based source bundle
- **Reaper Drone** = an autonomous CLI-backed executor
- **Staging Area** = a first-class workflow gate or buffer

---

## 1. Create a blueprint

Go to the **Blueprints** tab and create one of these:

- **+ FPV Drone** for a browser-based workflow that needs copy/paste
- **+ Reaper Drone** for an autonomous workflow that can run on its own

When filling in the blueprint:

- Give it a clear name.
- Pick a provider and model.
- Choose a role if the workflow needs one.
- Add skills if the blueprint should carry reusable instructions.
- Set a browser URL only when you are building an FPV Drone blueprint.

---

## 2. Build graph templates

Go to the **Templates** tab to build reusable agent-team flow graphs.

- Add Start, Decision, Agent, Command, Note, and End nodes.
- Wire edges between nodes and use the inspector to edit labels and deployment mapping.
- Validate before saving if you want blocking errors and warnings separated.
- Publish a template to show it in the Canvas sidebar, then drag it onto the canvas to deploy native-looking nodes and links.

Graph templates are separate from instruction templates. The `templates` tab in older docs refers to instruction-template text rendering; the new graph builder uses `template_graphs.*`.

---

## 3. Manage skills

Go to the **Skills** tab to create, edit, or remove reusable skill templates.

Skills can be attached to blueprints so FPV Drones and Reaper Drones inherit the
same instruction snippets wherever they are deployed.

---

## 4. Deploy and run

Go to the **FPV Drones** or **Reaper Drones** tab and deploy from a blueprint.

- FPV Drones stay browser-driven and use copy/paste with the manual chat site.
- Reaper Drones run autonomously and stream their progress in the app.
- Both can be attached to a workspace so they can read and edit real code.

---

## 5. Use the canvas

The **Canvas** is where you connect FPV Drones, Reaper Drones, and Staging Areas.

- Directional arrows mean execution flow.
- Plain lines mean simple association or context sharing.
- Staging Areas let you wait for all inputs, wait for any input, apply a
  threshold, or gate release manually, by budget, or by quality.

---

## 6. How they behave

- An **FPV Drone** gives you a prompt or transcript to paste into a browser.
- A **Reaper Drone** runs the task directly and streams its output as it works.
- A **Staging Area** can pause, summarize, or release work downstream once its
  conditions are met.

Legacy labels may still appear in older saved data, but the current UI uses the
terms above.
