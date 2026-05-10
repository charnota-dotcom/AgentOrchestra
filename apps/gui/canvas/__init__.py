"""Flow Canvas — visual orchestration of agent runs.

See ``docs/FLOW_CANVAS_PLAN.md`` for the design.  Public surface
re-exports the page widget that the main window installs as a tab.
"""

from apps.gui.canvas.page import CanvasPage

__all__ = ["CanvasPage"]
