"""Tests for the attachment render pipeline (CSV / XLSX / images).

Network-free, parser-light: we use openpyxl when available to write
out a tiny .xlsx; CSV is built inline; image tests cover the
no-Pillow fall-through path so they don't require optional deps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.service.attachments import (
    classify_kind,
    render_attachment,
    sanitize_filename,
)
from apps.service.attachments.render import (
    MAX_COLS_PER_SHEET,
    MAX_ROWS_PER_SHEET,
    AttachmentRenderError,
    _to_markdown_table,
)


def test_classify_kind_image_and_spreadsheet() -> None:
    assert classify_kind(Path("a.png")) == "image"
    assert classify_kind(Path("a.JPG")) == "image"
    assert classify_kind(Path("a.gif")) == "image"
    assert classify_kind(Path("b.xlsx")) == "spreadsheet"
    assert classify_kind(Path("b.CSV")) == "spreadsheet"
    assert classify_kind(Path("b.pdf")) == ""


def test_sanitize_filename_strips_path_and_oddities() -> None:
    assert sanitize_filename("../etc/passwd") == "passwd"
    assert sanitize_filename("a b/c.txt") == "c.txt"
    assert sanitize_filename("...") == "upload"
    assert sanitize_filename("") == "upload"
    assert sanitize_filename("ok.name-1.png") == "ok.name-1.png"


def test_to_markdown_table_handles_empty_and_ragged() -> None:
    assert _to_markdown_table([]) == ""
    table = _to_markdown_table([["a", "b"], ["1"], ["2", "3", "4"]])
    # Wider rows should be padded so | counts line up.
    lines = table.splitlines()
    assert all(line.count("|") == lines[0].count("|") for line in lines)


def test_render_csv(tmp_path: Path) -> None:
    src = tmp_path / "data.csv"
    src.write_text("name,age\nAlice,30\nBob,28\n", encoding="utf-8")
    dest = tmp_path / "stored.csv"
    result = render_attachment(src, dest=dest, kind="spreadsheet")
    assert result.rendered_text is not None
    assert "Alice" in result.rendered_text
    assert "| name | age |" in result.rendered_text
    assert dest.exists()
    assert result.bytes_written == len(src.read_bytes())


def test_render_csv_caps_rows(tmp_path: Path) -> None:
    src = tmp_path / "big.csv"
    rows = ["col0,col1"] + [f"r{i},v{i}" for i in range(MAX_ROWS_PER_SHEET + 50)]
    src.write_text("\n".join(rows), encoding="utf-8")
    result = render_attachment(src, dest=tmp_path / "out.csv", kind="spreadsheet")
    assert result.rendered_text is not None
    assert "truncated" in result.rendered_text.lower()


def test_render_xlsx_when_openpyxl_available(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Numbers"
    ws.append(["x", "y"])
    ws.append([1, 2])
    ws.append([3, 4])
    src = tmp_path / "n.xlsx"
    wb.save(str(src))

    dest = tmp_path / "n_stored.xlsx"
    result = render_attachment(src, dest=dest, kind="spreadsheet")
    assert result.rendered_text is not None
    assert "### Sheet: Numbers" in result.rendered_text
    assert "| x | y |" in result.rendered_text
    assert "| 1 | 2 |" in result.rendered_text
    assert dest.exists()


def test_render_image_passthrough_when_pillow_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the "no Pillow" branch by hiding the module.
    import sys

    monkeypatch.setitem(sys.modules, "PIL", None)
    src = tmp_path / "small.png"
    # 67-byte minimal PNG header + IDAT + IEND so it's recognised as PNG
    src.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    dest = tmp_path / "small_stored.png"
    result = render_attachment(src, dest=dest, kind="image")
    assert result.rendered_text is None
    assert result.mime_type == "image/png"
    assert dest.read_bytes() == src.read_bytes()


def test_render_attachment_unknown_kind(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("hi", encoding="utf-8")
    with pytest.raises(AttachmentRenderError):
        render_attachment(src, dest=tmp_path / "x", kind="garbage")


def test_render_xlsx_without_openpyxl_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "openpyxl", None)
    src = tmp_path / "a.xlsx"
    src.write_bytes(b"PK\x03\x04not really xlsx")
    # render_attachment surfaces missing-parser as AttachmentRenderError
    # so the upload handler can preserve raw bytes + flag the operator.
    # The fallback-to-raw-text path is for *other* spreadsheet failures
    # (corrupt xlsx etc.), not for missing optional deps.
    with pytest.raises(AttachmentRenderError, match="openpyxl not installed"):
        render_attachment(src, dest=tmp_path / "out.xlsx", kind="spreadsheet")


def test_max_caps_are_sensible() -> None:
    # Sanity: cap constants exist and are positive so the markdown
    # tables we hand to the model can never blow context indefinitely.
    assert MAX_ROWS_PER_SHEET > 0
    assert MAX_COLS_PER_SHEET > 0
