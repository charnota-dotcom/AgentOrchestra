"""Attachment subsystem: spreadsheet rendering, image normalisation, and
the on-disk layout helpers.

Operators drop ``.xlsx``, ``.xls``, ``.csv``, ``.png``, ``.jpg``,
``.gif``, ``.webp`` files into a chat.  We store the bytes under
``<data_dir>/attachments/<agent_id>/<id>__<sanitized_name>`` and persist
a row in the ``attachments`` table.

Spreadsheets are rendered to markdown once at upload time so we don't
re-parse on every send.  Images are kept as-is (CLIs accept the path
verbatim) but optionally resized to keep token cost sane.
"""

from apps.service.attachments.render import (
    AttachmentRenderError,
    classify_kind,
    render_attachment,
    sanitize_filename,
)

__all__ = [
    "AttachmentRenderError",
    "classify_kind",
    "render_attachment",
    "sanitize_filename",
]
