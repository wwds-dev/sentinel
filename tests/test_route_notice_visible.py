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
    # Ollama running: the D3 substitution goes to Ollama. The tests of spec
    # local-only-any-runtime below store their own inventory.
    from services import local_models
    win.local_inventory = local_models.LocalInventory()
    win.local_inventory.store(local_models.Snapshot(ollama_up=True, taken_at=1.0))
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


def test_notice_clears_when_auto_route_is_turned_on(local_only):
    """P2-1 of the PR #31 review: Auto-route returned before clearing the notice."""
    win = local_only
    win.resolve_backend_model()
    assert win.route_notice_label.isVisibleTo(win.route_screen)   # a substitution happened
    win.execution_mode_box.setCurrentText("Cloud only")
    win.auto_recommend_checkbox.setChecked(True)
    win.route_for_request = lambda *a, **k: _Decision()
    win._apply_route_to_widgets = lambda *a, **k: None   # only the notice is under test
    win._show_route = lambda decision: None
    win.resolve_backend_model()
    assert not win.route_notice_label.isVisibleTo(win.route_screen)
    assert win.route_notice_label.text() == ""
    assert win._route_resolution_notice == ""


class _Decision:
    provider = "anthropic"
    model = "claude-sonnet-5"


# ── spec local-only-any-runtime: the substitution uses whichever runtime runs ──

def _inventory(win, *, ollama_up, lmstudio_up, models=()):
    from services import local_models
    win.local_inventory = local_models.LocalInventory()
    win.local_inventory.store(local_models.Snapshot(
        models=tuple(models), ollama_up=ollama_up, lmstudio_up=lmstudio_up,
        taken_at=1.0))


def _lm(model_id, *, loaded=False, fit=""):
    from services import local_models
    return local_models.LocalModel(provider="lmstudio", id=model_id, name=model_id,
                                   loaded=loaded, fit=fit)


def test_lmstudio_only_mac_gets_a_loaded_lmstudio_model(local_only):
    win = local_only
    _inventory(win, ollama_up=False, lmstudio_up=True,
               models=[_lm("a-on-disk"), _lm("b-loaded", loaded=True)])
    assert win.resolve_backend_model() == ("lmstudio", "b-loaded")
    assert win.route_notice_label.text().startswith("Local only mode: using lmstudio · b-loaded")
    assert "instead of openai" in win.route_notice_label.text()


def test_saved_lmstudio_default_wins_and_too_big_models_are_skipped(local_only):
    win = local_only
    win.settings["default_model_lmstudio"] = "saved"
    _inventory(win, ollama_up=False, lmstudio_up=True,
               models=[_lm("huge", loaded=True, fit="too_big"), _lm("saved"),
                       _lm("other", loaded=True)])
    assert win.resolve_backend_model() == ("lmstudio", "saved")
    win.settings["default_model_lmstudio"] = "gone"
    assert win.resolve_backend_model() == ("lmstudio", "other")


def test_only_too_big_lmstudio_models_keeps_the_ollama_route(local_only):
    win = local_only
    _inventory(win, ollama_up=False, lmstudio_up=True,
               models=[_lm("huge", fit="too_big"), _lm("tight", fit="tight")])
    assert win.resolve_backend_model()[0] == "ollama"


@pytest.mark.parametrize("ollama_up,lmstudio_up", [(True, True), (True, False), (False, False)])
def test_ollama_route_when_ollama_runs_or_nothing_does(local_only, ollama_up, lmstudio_up):
    win = local_only
    win.settings["default_model_ollama"] = "my-ollama"
    _inventory(win, ollama_up=ollama_up, lmstudio_up=lmstudio_up,
               models=[_lm("x", loaded=True)])
    assert win.resolve_backend_model() == ("ollama", "my-ollama")


def test_no_inventory_data_keeps_the_old_ollama_route(local_only):
    from services import local_models
    win = local_only
    win.local_inventory = local_models.LocalInventory()   # never stored
    assert win.resolve_backend_model()[0] == "ollama"
