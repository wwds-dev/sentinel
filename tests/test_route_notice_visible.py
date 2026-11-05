"""The Local only substitution notice (D3) is visible in the Current Route card."""

from __future__ import annotations

import pytest

from tests.test_routing_phase4 import qapp, win  # noqa: F401


@pytest.fixture
def local_only(win):
    import main
    # the shared fixture stubs the resolver; this test needs the real one
    win.resolve_backend_model = main.GodAI.resolve_backend_model.__get__(win)
    win.auto_recommend_checkbox.setChecked(False)   # the manual pick, not the router's
    win.execution_mode_box.setCurrentText("Local only")
    win.provider_box.setCurrentText("openai")
    assert win.provider_box.currentText() == "openai", "the cloud pick must exist in the box"
    return win


def _card_contains(win, widget) -> bool:
    return win.route_screen.isAncestorOf(widget)


def test_substitution_notice_is_shown_in_the_route_card(local_only):
    win = local_only
    provider, _model = win.resolve_backend_model()
    assert provider == "ollama"
    label = win.route_notice_label
    assert _card_contains(win, label)
    assert label.isVisibleTo(win.route_screen)
    assert label.text().startswith("Local only mode: using ollama")
    assert "instead of openai" in label.text()


def test_notice_clears_when_nothing_is_substituted(local_only):
    win = local_only
    win.resolve_backend_model()
    win.execution_mode_box.setCurrentText("Cloud only")
    try:
        win.resolve_backend_model()
    except RuntimeError:
        pass
    assert not win.route_notice_label.isVisibleTo(win.route_screen)
    assert win.route_notice_label.text() == ""
