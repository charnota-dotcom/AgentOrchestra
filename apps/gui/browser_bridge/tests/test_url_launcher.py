"""Tests for url_launcher's URL normalisation.

The actual browser-launch is a side effect on the real OS that
we don't exercise from CI; we only test the input-normalisation
logic that runs before ``webbrowser.open`` is called.  The actual
``open_url`` function is exercised manually by the operator.
"""

from __future__ import annotations

from unittest.mock import patch

from apps.gui.browser_bridge import open_url


def test_empty_input_returns_false() -> None:
    assert open_url("") is False
    assert open_url(None) is False
    assert open_url("   ") is False


def test_url_with_scheme_passes_through() -> None:
    with patch("apps.gui.browser_bridge.url_launcher.webbrowser.open") as mock:
        assert open_url("https://claude.ai/new") is True
    mock.assert_called_once_with("https://claude.ai/new", new=2)


def test_url_without_scheme_gets_https_prefix() -> None:
    with patch("apps.gui.browser_bridge.url_launcher.webbrowser.open") as mock:
        assert open_url("claude.ai/new") is True
    mock.assert_called_once_with("https://claude.ai/new", new=2)


def test_leading_whitespace_stripped() -> None:
    with patch("apps.gui.browser_bridge.url_launcher.webbrowser.open") as mock:
        assert open_url("  https://chatgpt.com/  ") is True
    mock.assert_called_once_with("https://chatgpt.com/", new=2)


def test_open_url_swallows_webbrowser_errors() -> None:
    # webbrowser.open occasionally raises on weird platforms; we
    # should return False rather than crashing the caller.
    with patch(
        "apps.gui.browser_bridge.url_launcher.webbrowser.open",
        side_effect=RuntimeError("no browser configured"),
    ):
        assert open_url("https://claude.ai/new") is False
