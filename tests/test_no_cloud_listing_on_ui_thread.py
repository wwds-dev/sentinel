"""Readiness audit B1 (2026-11-18): no cloud model listing from the UI thread.

README promise: in Local only mode Sentinel contacts no cloud provider at all.
Before the fix, `AgentPanel.default_provider = "anthropic"` made every panel's
model box call `models_for_provider("anthropic")` at construction, which asked
the Anthropic API for its models live on the UI thread, in every mode; and
`route_for_request` did the same per keystroke for each ticked provider.

`tests/_isolation.py` already replaces every client's `list_models` with a
stub that returns KNOWN_MODELS, so a call would pass silently. These tests
wrap that stub in a spy and fail on any call made on the UI thread (and, in
Local only, on any call at all). Live listing belongs to ModelScanWorker,
which `scan_plan` gates on the mode and the allow boxes.
"""

from __future__ import annotations

import threading

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from services import routing_advisor as ra

CLIENT_PATHS = (
    ("services.anthropic_client", "AnthropicClientWrapper"),
    ("services.deepseek_client", "DeepSeekClientWrapper"),
    ("services.gemini_client", "GeminiClientWrapper"),
    ("services.kimi_client", "KimiClientWrapper"),
    ("services.openai_client", "OpenAIClientWrapper"),
    ("services.qwen_client", "QwenClientWrapper"),
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def calls(monkeypatch):
    """Spy on every cloud client's list_models: (class, on the UI thread?)."""
    import importlib

    seen: list[tuple[str, bool]] = []
    for module_name, cls_name in CLIENT_PATHS:
        cls = getattr(importlib.import_module(module_name), cls_name)
        inner = cls.list_models

        def spy(self, *a, _inner=inner, _name=cls_name, **k):
            seen.append((_name, threading.current_thread() is threading.main_thread()))
            return _inner(self, *a, **k)

        monkeypatch.setattr(cls, "list_models", spy)
    return seen


@pytest.fixture
def local_only_window(qapp, monkeypatch, tmp_path, calls):
    import main

    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(main, "SETTINGS_OVERRIDE_FILE", tmp_path / "override.json")
    # The app starts in Local only: the routing mode decides the run bar's
    # Execution mode box from the first moment of construction.
    monkeypatch.setattr(ra, "mode_for", lambda settings: ra.LOCAL_ONLY)
    window = main.GodAI()
    yield window
    window.close()


def test_window_and_panels_build_without_listing_cloud_models(local_only_window, calls):
    win = local_only_window
    assert win.execution_mode_box.currentText() == "Local only"
    # Every agent panel has built its provider/model row by now; at least
    # one defaults to a cloud provider, which is what used to trigger a call.
    assert any(getattr(p, "provider_box", None) is not None
               and p.provider_box.currentText() not in ("ollama", "lmstudio")
               for p in win.panels.values()), "no panel defaults to a cloud provider"
    assert calls == [], f"cloud list_models called while building the window: {calls}"


def test_reloading_a_cloud_model_box_lists_nothing(local_only_window, calls):
    win = local_only_window
    models = win.models_for_provider("anthropic")
    assert models, "the cached list must still offer Anthropic's models"
    for panel in win.panels.values():
        if getattr(panel, "provider_box", None) is not None:
            panel.provider_box.setCurrentText("openai")
    assert calls == [], f"cloud list_models called on a provider switch: {calls}"


def test_keystroke_routing_lists_nothing_on_the_ui_thread(local_only_window, calls):
    win = local_only_window
    QApplication.processEvents()
    calls.clear()
    # Even with every cloud provider ticked (Hybrid), routing a keystroke
    # reads cached lists only.
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    for provider in ("anthropic", "openai", "deepseek", "kimi", "gemini", "qwen"):
        box = getattr(win, f"allow_{provider}_checkbox", None)
        if box is not None:
            box.setChecked(True)
    for text in ("W", "Wr", "Write a haiku"):
        win.input_box.setPlainText(text)
        win.route_for_request(text, agent="chat")
    on_ui = [c for c in calls if c[1]]
    assert on_ui == [], f"cloud list_models called on the UI thread: {on_ui}"
