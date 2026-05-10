"""Flow Canvas — visual orchestration of agent runs.

See ``docs/FLOW_CANVAS_PLAN.md`` for the design.

We deliberately don't re-export the GUI pieces from here.  The CI
environment has only the dev extras installed (no PySide6), and
re-exporting ``CanvasPage`` at package import time would force
``PySide6`` resolution as soon as anyone imported any submodule
(e.g. ``apps.gui.canvas.layout`` for an auto-layout unit test).
Submodules import what they need directly.
"""
