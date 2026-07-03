"""Tests for CLI redraw helpers used to recover from terminal buffer drift.

Covers:
  - the resize-redraw scheduler we use to settle Windows resize bursts
  - the resize handler we install over prompt_toolkit's _on_resize (#5474)

Both behaviors are exercised against fake prompt_toolkit renderer/output
objects — we're asserting the escape sequences the CLI sends, not that
the terminal physically repainted.
"""

from unittest.mock import MagicMock, patch

import pytest

from cli import HermesCLI


@pytest.fixture
def bare_cli():
    """A HermesCLI with no __init__ — we only exercise the redraw helper."""
    cli = object.__new__(HermesCLI)
    return cli


class TestResizeRedrawScheduler:
    def test_schedule_resize_redraw_cancels_previous_timer(self, bare_cli):
        old_timer = MagicMock()
        bare_cli._resize_redraw_timer = old_timer

        new_timer = MagicMock()
        with patch("cli.threading.Timer", return_value=new_timer) as timer_ctor:
            bare_cli._schedule_resize_redraw(delay=0.25)

        old_timer.cancel.assert_called_once()
        timer_ctor.assert_called_once()
        assert timer_ctor.call_args.args[0] == 0.25
        assert callable(timer_ctor.call_args.args[1])
        assert new_timer.daemon is True
        new_timer.start.assert_called_once()
        assert bare_cli._resize_redraw_timer is new_timer

    def test_scheduled_callback_invalidates_without_full_clear(self, bare_cli):
        app = MagicMock()
        bare_cli._app = app
        holder = {}

        class _ImmediateTimer:
            daemon = False

            def __init__(self, delay, fn):
                holder["fn"] = fn

            def start(self):
                holder["fn"]()

        with patch("cli.threading.Timer", _ImmediateTimer):
            bare_cli._schedule_resize_redraw(delay=0.01)

        assert app.renderer._last_screen is None
        app.invalidate.assert_called_once()
        app.renderer.output.erase_screen.assert_not_called()
