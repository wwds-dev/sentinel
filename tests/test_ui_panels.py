"""The shared panel base — `ui/panels/base.py` — and the host seam it uses.

Phase 3 of the `main.py` split (`docs/refactor_plan.md`). Six panels used to
build their own provider/model row and their own model loader; they now share
one of each. Two things are worth pinning:

1. Every panel still ends up with a populated model box wired to its provider —
   this is the behaviour the duplication used to provide, and losing it would
   not raise anything, it would just leave a combo empty.
2. `AgentPanel` works against a stand-in host, with no `GodAI` in sight. That is
   the whole point of composition over mixins, and it is what phase 4 relies on.

The window is built offscreen once per module: constructing `GodAI` is slow, and
every test here only reads widget state or calls one method on it.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QAbstractItemView, QLabel, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.host import AgentHost
from ui.panels.base import PROVIDERS, AgentPanel, build_provider_row


# Agents with a panel of their own. Their provider/model boxes are reached the
# way the application reaches them — `setup_widgets_for` — because a panel that
# has moved to `ui/panels/` owns its combos and one that has not still hangs
# them off `GodAI` (phase 4 moves them one at a time).
PANEL_AGENTS = [
    "osint", "osint_heavy", "wifi", "bug_bounty", "manager", "vpn",
]


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    """One offscreen `GodAI`. Modal dialogs are stubbed — nothing can click them."""
    from PySide6.QtWidgets import QMessageBox
    import main

    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        yield main.GodAI()
    finally:
        QMessageBox.warning, QMessageBox.question = saved


# ─────────────────────────────────────────────────────────────────────────────
# 1. The rows the panels no longer build by hand
# ─────────────────────────────────────────────────────────────────────────────

class TestPanelProviderRows:

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_panel_owns_its_provider_and_model_boxes(self, win, agent):
        # A panel with no boxes cannot be recommended to, marked, or run.
        provider_box, model_box = win.setup_widgets_for(agent)
        assert provider_box is not None
        assert model_box is not None


    def test_sidebar_and_window_show_the_canonical_version(self, win):
        from services.app_version import DISPLAY_VERSION

        assert win.version_label.objectName() == "AppVersion"
        assert win.version_label.text() == DISPLAY_VERSION
        assert DISPLAY_VERSION in win.windowTitle()

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_provider_box_offers_every_provider(self, win, agent):
        box, _ = win.setup_widgets_for(agent)
        assert [box.itemText(i) for i in range(box.count())] == list(PROVIDERS)

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_model_box_is_populated_by_the_time_the_panel_is_built(
            self, win, agent):
        # Every client falls back to a hand-written list when its API is
        # unreachable, so this holds offline and with no API keys set.
        assert win.setup_widgets_for(agent)[1].count() > 0

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_switching_provider_reloads_the_model_list(self, win, agent):
        provider_box, model_box = win.setup_widgets_for(agent)
        before = provider_box.currentText()
        other = next(p for p in PROVIDERS if p != before)
        try:
            provider_box.setCurrentText(other)
            expected = win.models_for_provider(other)
            actual = [model_box.itemText(i) for i in range(model_box.count())]
            assert actual == expected or (
                not expected
                and actual in ([], ["(unavailable)"], ["(no local models)"])
            )
        finally:
            provider_box.setCurrentText(before)

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_specialists_use_the_same_compact_run_bar_as_chat(self, win, agent):
        from PySide6.QtWidgets import QLabel

        panel = win.panels[agent]
        run_bar = panel.findChild(QObject, "RunBar")
        assert run_bar is not None
        labels = {
            w.text() for w in run_bar.findChildren(QLabel)
            if w.text() in ("Provider:", "Model:")
        }
        assert labels == set()
        provider_box, model_box = win.setup_widgets_for(agent)
        assert provider_box.objectName() == "MachinePick"
        assert model_box.objectName() == "MachinePick"

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_every_specialist_exposes_auto_route_in_its_run_bar(self, win, agent):
        panel = win.panels[agent]
        run_bar = panel.findChild(QObject, "RunBar")
        assert panel.auto_route_btn.text() == "Auto-route"
        assert panel.auto_route_btn.objectName() == "AutoRouteAction"
        assert not panel.auto_route_btn.isHidden()
        assert run_bar.layout().indexOf(panel.auto_route_btn) >= 0

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_every_specialist_marks_cloud_routes_as_paid(self, win, agent):
        from ui.widgets import MenuComboBox

        provider, model = win.setup_widgets_for(agent)
        cloud_index = provider.findText("deepseek")
        local_index = provider.findText("ollama")
        assert provider.itemData(cloud_index, MenuComboBox.COST_ROLE) is True
        assert provider.itemData(local_index, MenuComboBox.COST_ROLE) is False
        assert provider.property("paidSelection") is True
        assert model.property("paidSelection") is True

    def test_specialist_auto_route_uses_current_agent_input(self, win, monkeypatch):
        from types import SimpleNamespace

        trace = win.panels["osint"]
        trace.target_input.setText("@sapio1337")
        calls = []
        decision = SimpleNamespace(
            provider="ollama", model="deepseek-r1:8b", mode="Local only",
            reason="Private local route.", fallbacks=[],
        )

        def prepare(agent, prompt, provider_box, model_box, **kwargs):
            calls.append((agent, prompt, kwargs))
            return decision

        monkeypatch.setattr(win, "prepare_agent_route", prepare)
        assert trace.auto_route("Query") is decision
        assert calls[0][0] == "osint"
        assert "@sapio1337" in calls[0][1]
        assert calls[0][2]["force"] is True


class TestRecommendationsStillReachThePanels:
    """The registry replaced a map of loader method *names*.

    Selecting a recommended model only works if the model box was repopulated
    first, so a panel sitting on its recommended model is end-to-end proof that
    the recommendation system found this panel's loader.
    """

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_routing_tile_shows_catalog_cost_for_every_agent(self, win, agent):
        import main
        from services.model_recommendations import pricing_metadata

        win.select_agent(agent)
        rec = main.AGENT_RECOMMENDATIONS[agent]
        expected = pricing_metadata(rec["provider"], rec["model"]).compact
        assert win.routing_rows["Cost"].value.text() == expected

    def test_chat_startup_accepts_serialized_route_pricing(self, win):
        """The capability router returns pricing as a dict from as_dict()."""
        rec = win.get_recommended_setup()
        assert isinstance(rec["pricing"], dict)
        win.update_recommendation_label()
        assert win.routing_rows["Cost"].value.text() == rec["cost_label"]

    def test_chat_exposes_auto_route_in_the_primary_run_bar(self, win):
        run_bar = win.findChild(QObject, "RunBar")
        assert win.auto_route_btn.text() == "Auto-route"
        assert not win.auto_route_btn.isHidden()
        assert run_bar.layout().indexOf(win.auto_route_btn) >= 0

    def test_cloud_provider_and_models_are_marked_as_paid(self, win):
        from ui.widgets import MenuComboBox

        provider = win.provider_box
        model = win.model_box
        provider.setCurrentText("deepseek")
        win.load_provider_models()

        cloud_index = provider.findText("deepseek")
        local_index = provider.findText("ollama")
        assert provider.itemData(cloud_index, MenuComboBox.COST_ROLE) is True
        assert provider.itemData(local_index, MenuComboBox.COST_ROLE) is False
        assert provider.property("paidSelection") is True
        assert model.property("paidSelection") is True
        assert all(
            model.itemData(index, MenuComboBox.COST_ROLE) is True
            for index in range(model.count())
        )

        provider.setCurrentText("ollama")
        win.load_provider_models()
        assert provider.property("paidSelection") is False
        assert model.property("paidSelection") is False

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_panel_starts_on_its_recommended_provider_and_model(
            self, win, agent):
        import main
        rec = main.AGENT_RECOMMENDATIONS[agent]
        provider_box, model_box = win.setup_widgets_for(agent)
        assert provider_box.currentText() == rec["provider"]
        assert model_box.currentIndex() == win._find_model_index(
            model_box, rec["model"])

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_the_recommended_model_is_still_marked_after_a_reload(
            self, win, agent):
        # Clearing a combo drops every item's colour, so the marking has to be
        # re-applied after each load — the reason the loader and the marker are
        # both wired to the provider box.
        from PySide6.QtCore import Qt
        import main
        model_box = win.setup_widgets_for(agent)[1]
        win.load_models_for(agent)
        win.refresh_recommendation_marks(agent)
        idx = win._find_model_index(model_box, main.AGENT_RECOMMENDATIONS[agent]["model"])
        assert idx >= 0
        assert model_box.itemData(idx, Qt.ForegroundRole) is not None

    def test_trace_models_come_from_the_client_not_the_live_api(self, win):
        # conftest serves KNOWN_MODELS; the panels reach it through
        # models_for_provider -> list_models.
        from services.deepseek_client import DeepSeekClientWrapper

        trace_box = win.setup_widgets_for("osint")[1]
        assert [trace_box.itemText(i) for i in range(trace_box.count())] == (
            DeepSeekClientWrapper.KNOWN_MODELS)

    @pytest.mark.parametrize("provider", [
        "ollama", "openai", "deepseek", "kimi", "gemini", "anthropic", "qwen",
    ])
    def test_chat_models_come_from_the_client_not_the_live_api(
            self, win, provider):
        # Chat used to call three providers' APIs itself, around list_models
        # and so around conftest's offline lists. OpenAI keeps Chat's
        # text-only filter; everything else is the client's list unchanged.
        known = list(win.models_for_provider(provider))
        if provider == "openai":
            known = [m for m in known if not m.startswith("dall-e")]

        original = win.provider_box.currentText()
        try:
            win.provider_box.setCurrentText(provider)
            win.load_provider_models()
            chat_box = win.model_box
            assert [chat_box.itemText(i) for i in range(chat_box.count())] == known
            assert known
        finally:
            win.provider_box.setCurrentText(original)
            win.load_provider_models()

    def test_trace_falls_back_inline_when_deepseek_permission_is_off(
            self, win, monkeypatch):
        from types import SimpleNamespace

        trace = win.panels["osint"]
        monkeypatch.setattr(
            win, "models_for_provider", lambda provider, *args: [f"{provider}-test"]
        )
        trace.provider_box.setCurrentText("deepseek")
        win.allow_deepseek_checkbox.setChecked(False)
        win.auto_recommend_checkbox.setChecked(False)
        monkeypatch.setattr(win, "provider_key_available", lambda provider: False)
        local_model = "ollama-test"
        decision = SimpleNamespace(
            provider="ollama", model=local_model, mode="Local only",
            reason="Use local fallback.", fallbacks=[],
        )
        monkeypatch.setattr(win, "route_for_request", lambda *a, **k: decision)

        win.prepare_agent_route(
            "osint", "@sapio1337", trace.provider_box, trace.model_box,
            tool="Auto-detect",
        )

        assert trace.provider_box.currentText() == "ollama"
        assert trace.model_box.currentText() == local_model
        assert "deepseek permission is off" in trace._route_notice
        assert "using ollama" in trace._route_notice

    def test_trace_shows_inline_guidance_when_no_fallback_exists(
            self, win, monkeypatch):
        trace = win.panels["osint"]
        monkeypatch.setattr(
            win, "models_for_provider", lambda provider, *args: [f"{provider}-test"]
        )
        trace.provider_box.setCurrentText("deepseek")
        win.allow_deepseek_checkbox.setChecked(False)
        win.auto_recommend_checkbox.setChecked(False)
        monkeypatch.setattr(win, "provider_key_available", lambda provider: False)
        trace.target_input.setText("@sapio1337")
        monkeypatch.setattr(
            win, "route_for_request",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("No fallback available")),
        )
        authorised = []
        monkeypatch.setattr(
            win, "authorize_request", lambda *a, **k: authorised.append(True) or True
        )

        trace.analyse()

        assert authorised == []
        assert "deepseek permission is off" in trace.status_label.text()
        assert "Inspector" in trace.status_label.text()
        assert trace.analyse_btn.isEnabled()

    def test_trace_local_balance_retry_bypasses_auto_routing_once(
            self, win, monkeypatch):
        trace = win.panels["osint"]
        previous_provider = trace.provider
        previous_auto = win.auto_recommend_checkbox.isChecked()
        try:
            trace.provider_box.setCurrentText("ollama")
            trace._prefer_local_retry_once = True
            win.auto_recommend_checkbox.setChecked(True)
            rerouted = []
            monkeypatch.setattr(
                win, "route_for_request", lambda *a, **k: rerouted.append(True)
            )

            result = win.prepare_agent_route(
                "osint", "@sapio1337", trace.provider_box, trace.model_box,
                tool="Auto-detect",
            )

            assert result is None
            assert rerouted == []
            assert trace._prefer_local_retry_once is False
        finally:
            trace.provider_box.setCurrentText(previous_provider)
            win.auto_recommend_checkbox.setChecked(previous_auto)


class TestModelLoaderRegistry:

    @pytest.mark.parametrize("agent", PANEL_AGENTS + ["chat"])
    def test_every_agent_registers_a_loader(self, win, agent):
        assert callable(win._model_loaders.get(agent))

    @pytest.mark.parametrize("agent", PANEL_AGENTS)
    def test_load_models_for_refills_an_emptied_box(self, win, agent):
        model_box = win.setup_widgets_for(agent)[1]
        model_box.clear()
        win.load_models_for(agent)
        assert model_box.count() > 0

    def test_unknown_agent_key_is_a_no_op(self, win):
        win.load_models_for("no_such_agent")  # must not raise

    def test_a_failing_loader_is_recorded_not_raised(self, win, monkeypatch):
        noted = []
        monkeypatch.setattr(win, "_note_failure",
                            lambda ctx, exc, widget=None: noted.append(ctx))
        win._model_loaders["boom"] = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            win.load_models_for("boom")
        finally:
            del win._model_loaders["boom"]
        assert noted and "boom" in noted[0]


class TestLoadModelsInto:

    def test_unknown_provider_leaves_the_box_empty(self, win, qapp):
        from PySide6.QtWidgets import QComboBox
        provider_box, model_box = QComboBox(), QComboBox()
        provider_box.addItem("nonesuch")
        win.load_models_into(provider_box, model_box, "test")
        assert model_box.count() == 0

    def test_placeholder_only_where_a_panel_asked_for_one(self, win, qapp):
        from PySide6.QtWidgets import QComboBox
        provider_box, model_box = QComboBox(), QComboBox()
        provider_box.addItem("ollama")
        win.load_models_into(provider_box, model_box, "test",
                             empty_placeholder=True)
        # Either real models, or Forge's hint — never a silently empty box.
        assert model_box.count() > 0

    def test_a_failing_client_is_noted_rather_than_swallowed(self, win, qapp,
                                                             monkeypatch):
        """Two of the six panels used to discard this exception entirely."""
        from PySide6.QtWidgets import QComboBox

        class Exploding:
            def list_models(self):
                raise RuntimeError("provider down")

        noted = []
        monkeypatch.setattr(win, "qwen", Exploding())
        monkeypatch.setattr(win, "_note_failure",
                            lambda ctx, exc, widget=None: noted.append(ctx))
        provider_box, model_box = QComboBox(), QComboBox()
        provider_box.addItem("qwen")
        win.load_models_into(provider_box, model_box, "trace")
        assert noted == ["trace: load models"]
        assert model_box.count() == 0

    def test_no_context_keeps_the_old_silent_behaviour(self, win, monkeypatch):
        class Exploding:
            def list_models(self):
                raise RuntimeError("provider down")

        noted = []
        monkeypatch.setattr(win, "qwen", Exploding())
        monkeypatch.setattr(win, "_note_failure",
                            lambda *a, **k: noted.append(a))
        assert win.models_for_provider("qwen") == []
        assert noted == []


class TestAgentHostProtocol:

    def test_godai_satisfies_the_host_protocol(self, win):
        # The protocol is the contract phase 4 panels are written against; a
        # member renamed on GodAI must fail here, not in a panel months later.
        assert isinstance(win, AgentHost)


class TestWorkspaceLayoutRegressions:
    """Pin the shell failures that made the live GUI look empty or cramped."""

    def test_chat_keeps_empty_output_compact_then_reveals_real_results(self, win):
        win.select_agent("chat")
        win.output_box.clear()
        win.update_agent_ui("chat")
        assert win.output_label.isHidden()
        assert win.output_box.isHidden()
        assert "results" in win.output_box.placeholderText().lower()

        win.output_box.setPlainText("Completed result")
        win.update_agent_ui("chat")
        assert not win.output_label.isHidden()
        assert not win.output_box.isHidden()

        win.output_label.click()
        assert win.output_box.isHidden()
        assert "Previous turn" in win.output_label.text()

        win.output_box.clear()
        win.output_label.setChecked(True)
        win.update_agent_ui("chat")

    def test_run_bar_cannot_absorb_the_workspace_height(self, win):
        from PySide6.QtWidgets import QSizePolicy
        run_bar = win.findChild(QObject, "RunBar")
        assert run_bar is not None
        assert run_bar.sizePolicy().verticalPolicy() == QSizePolicy.Fixed

    def test_inspector_toggle_and_preference_stay_in_sync(self, win):
        original = win.inspector_visible
        try:
            win.toggle_inspector(True)
            assert not win.right_panel.isHidden()
            assert win.inspector_btn.isChecked()
            assert win.inspector_visible
            win.toggle_inspector(False)
            assert win.right_panel.isHidden()
            assert not win.inspector_btn.isChecked()
            assert not win.inspector_visible
        finally:
            win.toggle_inspector(original)

    def test_minimum_width_keeps_the_requested_three_column_shell(
            self, win, qapp):
        original_size = win.size()
        original = win.inspector_visible
        try:
            win.toggle_inspector(True)
            win.show()
            win.resize(1200, 700)
            qapp.processEvents()
            assert not win.left_panel.isHidden()
            assert not win.right_panel.isHidden()
        finally:
            win.resize(original_size)
            win.toggle_inspector(original)

    def test_narrow_inspector_content_stays_inside_its_viewport(self, win, qapp):
        from PySide6.QtWidgets import QScrollArea
        from ui.widgets import KeyValue, Meter

        original_size = win.size()
        original = win.inspector_visible
        try:
            win.showNormal()
            win.resize(1200, 700)
            win.toggle_inspector(True)
            qapp.processEvents()

            scroll = win.right_panel.findChild(QScrollArea, "InspectorScroll")
            container = scroll.widget()
            assert container.width() <= scroll.viewport().width()

            for item in [
                *container.findChildren(KeyValue),
                *container.findChildren(Meter),
            ]:
                value = item.value
                assert value.width() > 0
                assert value.geometry().right() <= item.contentsRect().right()
        finally:
            win.resize(original_size)
            win.toggle_inspector(original)

    def test_chat_run_bar_and_prompt_form_one_compact_stack(self, win):
        win.select_agent("chat")
        run_bar = win.findChild(QObject, "RunBar")
        assert run_bar is not None
        layout = win.normal_panel.layout()
        assert layout.indexOf(run_bar) + 1 == layout.indexOf(win.input_box)
        assert layout.spacing() <= 12
        assert win.input_box.height() >= 126

    def test_inspector_exposes_complete_operational_information(self, win):
        from PySide6.QtWidgets import QGroupBox

        visible_sections = {
            group.title() for group in win.findChildren(QGroupBox)
            if not group.isHidden()
        }
        assert {
            "SYSTEM", "CURRENT ROUTE", "COST", "BUDGET", "ACTIONS", "API KEYS"
        } <= visible_sections
        assert not win.routing_rows["Mode"].isHidden()
        assert not win.routing_rows["Cost"].isHidden()
        assert "estimate" in win.cost_rows
        assert "Qwen" in win.key_rows

    def test_sidebars_balance_global_and_live_information(self, win):
        from PySide6.QtWidgets import QGroupBox

        assert win.right_panel.maximumWidth() <= 270
        assert win.left_panel.maximumWidth() == win.right_panel.maximumWidth()

        right_container = win.right_panel.findChild(QObject, "RightCardsContainer")
        right_order = []
        for index in range(right_container.layout().count()):
            widget = right_container.layout().itemAt(index).widget()
            if isinstance(widget, QGroupBox):
                right_order.append(widget.title())
        assert right_order == ["CURRENT ROUTE", "COST", "BUDGET", "SYSTEM"]

        left_order = []
        for index in range(win.left_utility_layout.count()):
            widget = win.left_utility_layout.itemAt(index).widget()
            if isinstance(widget, QGroupBox):
                left_order.append(widget.title())
        assert left_order == ["API KEYS", "ACTIONS"]

    def test_all_sidebar_sections_remain_visible_after_redistribution(self, win):
        from PySide6.QtWidgets import QGroupBox

        titles = set()
        for sidebar in (win.left_panel, win.right_panel):
            for group in sidebar.findChildren(QGroupBox):
                if not group.isHidden():
                    titles.add(group.title())
        assert titles == {
            "CURRENT ROUTE", "COST", "BUDGET", "SYSTEM", "API KEYS", "ACTIONS"
        }

    def test_right_inspector_order_tracks_request_lifecycle(self, win):
        from PySide6.QtWidgets import QGroupBox

        container = win.right_panel.findChild(QObject, "RightCardsContainer")
        ordered = []
        for index in range(container.layout().count()):
            widget = container.layout().itemAt(index).widget()
            if isinstance(widget, QGroupBox):
                ordered.append(widget.title())
        assert ordered == [
            "CURRENT ROUTE", "COST", "BUDGET", "SYSTEM"
        ]

    def test_inspector_keeps_quick_actions_available(self, win):
        from PySide6.QtWidgets import QGroupBox

        actions = next(
            group for group in win.left_panel.findChildren(QGroupBox)
            if group.title() == "ACTIONS"
        )
        assert not actions.isHidden()
        assert not win.realtime_monitor_btn.isHidden()
        more_actions = {action.text() for action in win.header_more_btn.menu().actions()}
        assert {"Cost history", "Run log", "Settings"} <= more_actions

    def test_live_cost_row_tracks_the_chat_prompt(self, win):
        win.select_agent("chat")
        original = win.input_box.toPlainText()
        try:
            win.input_box.setPlainText("Summarise the current investigation.")
            win.update_live_cost_estimate()
            value = win.cost_rows["estimate"].value.text()
            assert "tok" in value
            assert value != "€0.00 · 0 tok"
        finally:
            win.input_box.setPlainText(original)
            win.update_live_cost_estimate()

    def test_chat_options_is_a_compact_request_menu(self, win):
        top_level = {action.text() for action in win.runbar_menu.actions()}
        assert {
            "Command", "Execution mode", "Paid provider access",
            "Auto route now", "Use recommended model",
            "Auto-apply recommendations", "Estimate this request",
            "Export current report", "Models",
        } <= top_level
        assert {"Cost history", "Run log", "Settings", "App docs"}.isdisjoint(
            top_level
        )

        providers = next(
            action.menu() for action in win.runbar_menu.actions()
            if action.text() == "Paid provider access"
        )
        assert {action.text() for action in providers.actions()} == {
            "OpenAI", "DeepSeek", "Kimi", "Gemini", "Anthropic", "Qwen"
        }

    def test_every_application_combo_uses_the_shared_popup_contract(self, win):
        from PySide6.QtWidgets import QComboBox
        from ui.widgets import MenuComboBox

        combos = win.findChildren(QComboBox)
        assert combos
        for combo in combos:
            assert isinstance(combo, MenuComboBox)
            assert combo.property("sentinelMenuCombo") is True
            assert combo.maxVisibleItems() == 12

    def test_provider_and_model_selectors_use_meaningful_nested_menus(self, win):
        from ui.widgets import MenuComboBox

        provider = win.provider_box
        model = win.model_box
        assert provider.menuMode() == MenuComboBox.PROVIDER
        assert model.menuMode() == MenuComboBox.MODEL

        provider_menu = provider.buildMenu()
        provider_groups = {
            action.text(): action.menu() for action in provider_menu.actions()
            if action.menu() is not None
        }
        assert set(provider_groups) == {"Local providers", "Cloud providers"}
        assert [a.text() for a in provider_groups["Local providers"].actions()] == [
            "ollama"
        ]
        assert "openai" in {
            a.text() for a in provider_groups["Cloud providers"].actions()
        }

        model_menu = model.buildMenu()
        assert not [
            action for action in model_menu.actions() if action.menu() is not None
        ]
        assert [a.text() for a in model_menu.actions()] == [
            model.itemText(index) for index in range(model.count())
        ]

    def test_closed_model_tooltip_describes_the_selected_route(self, win):
        provider = win.provider_box
        model = win.model_box
        win.refresh_recommendation_marks("chat")
        assert model.toolTip() == (
            f"Selected model: {provider.currentText()} · {model.currentText()}."
        )

    def test_simple_selectors_keep_the_menu_style_without_fake_hierarchy(self, win):
        combo = win.osint_panel.type_box
        menu = combo.buildMenu()
        assert not [action for action in menu.actions() if action.menu() is not None]
        assert [action.text() for action in menu.actions()] == [
            combo.itemText(index) for index in range(combo.count())
        ]

        person = next(action for action in menu.actions() if action.text() == "Person")
        person.trigger()
        assert combo.currentText() == "Person"

    @pytest.mark.parametrize("panel_name", ["wifi_panel", "vpn_panel"])
    def test_specialists_do_not_duplicate_the_global_help_button(
            self, win, panel_name):
        from PySide6.QtWidgets import QPushButton
        panel = getattr(win, panel_name)
        assert not [b for b in panel.findChildren(QPushButton) if b.text() == "Help"]

    @pytest.mark.parametrize("panel_name,primary,stop", [
        ("osint_panel", "analyse_btn", "stop_btn"),
        ("osint_heavy_panel", "investigate_btn", "stop_btn"),
        ("wifi_panel", "run_btn", "stop_btn"),
        ("bug_bounty_panel", "analyse_btn", "stop_btn"),
        ("bug_bounty_panel", "nmap_run_btn", "nmap_stop_btn"),
        ("vpn_panel", "run_btn", "stop_btn"),
    ])
    def test_idle_workspaces_show_the_primary_action_not_stop(
            self, win, panel_name, primary, stop):
        panel = getattr(win, panel_name)
        assert not getattr(panel, primary).isHidden()
        assert getattr(panel, stop).isHidden()

    def test_saved_chats_only_occupy_the_chat_workflow(self, win):
        win.saved_chats_toggle.setChecked(False)
        win._toggle_saved_chats(False)
        win.saved_searches_toggle.setChecked(False)
        win._toggle_saved_searches(False)

        win.select_agent("osint")
        assert win.saved_chats_panel.isHidden()
        assert not win.saved_searches_toggle.isHidden()
        assert win.saved_searches_panel.isHidden()
        win.saved_searches_toggle.click()
        assert not win.saved_searches_panel.isHidden()

        win.select_agent("chat")
        assert not win.saved_chats_toggle.isHidden()
        assert win.saved_chats_panel.isHidden()
        assert win.saved_searches_panel.isHidden()
        win.saved_chats_toggle.click()
        assert not win.saved_chats_panel.isHidden()

        win.select_agent("vpn")
        assert win.saved_chats_panel.isHidden()
        assert win.saved_searches_panel.isHidden()

        win.saved_chats_toggle.setChecked(False)
        win._toggle_saved_chats(False)
        win.saved_searches_toggle.setChecked(False)
        win._toggle_saved_searches(False)

    def test_chat_projects_can_be_created_and_selected(self, win, monkeypatch):
        import main

        existing = win.registry.list_projects()
        projects = []
        monkeypatch.setattr(win.registry, "list_projects", lambda: list(projects))

        def create(project):
            saved = {
                "id": "moonlight",
                "name": project["name"],
                "instructions": "",
                "default_agent": "chat",
                "default_provider": "ollama",
                "default_model": "",
                "budget_eur": None,
                "archived": False,
            }
            projects.append(saved)
            return saved

        monkeypatch.setattr(win.registry, "upsert_project", create)
        monkeypatch.setattr(
            main.QInputDialog, "getText",
            staticmethod(lambda *args, **kwargs: ("Moonlight", True)),
        )

        win.refresh_project_controls()
        win.create_chat_project()

        assert win.active_project_id == "moonlight"
        assert win.active_project_box.currentText() == "Moonlight"
        assert win.history_project_filter.findText("Moonlight") >= 0

        # Leave the module-scoped window showing the real registry afterwards.
        monkeypatch.setattr(win.registry, "list_projects", lambda: existing)
        win.active_project_id = None
        win.refresh_project_controls()

    def test_saved_chat_project_filter_intersects_agent_and_search(
            self, win, monkeypatch, tmp_path):
        import json
        import main

        records = (
            ("one.json", "chat", "moonlight", "alpha draft"),
            ("two.json", "vpn", "moonlight", "alpha tunnel"),
            ("three.json", "chat", None, "alpha loose"),
            ("four.json", "chat", "moonlight", "beta draft"),
        )
        for filename, agent, project, prompt in records:
            payload = {
                "agent": agent,
                "messages": [{"role": "user", "content": prompt}],
                "response": "done",
            }
            if project:
                payload["project"] = project
            (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")

        existing = win.registry.list_projects()
        monkeypatch.setattr(
            win.registry, "list_projects",
            lambda: [{"id": "moonlight", "name": "Moonlight"}],
        )
        monkeypatch.setattr(main, "CHATS_DIR", tmp_path)
        win.refresh_project_controls()
        win.history_project_filter.setCurrentIndex(
            win._combo_index_for_data(win.history_project_filter, "moonlight")
        )
        win.history_agent_filter.setCurrentText("chat")
        win.history_search.setText("alpha")
        win.load_history_list()

        assert win.history_list.count() == 1
        assert "alpha draft" in win.history_list.item(0).text()

        win.history_search.clear()
        win.history_agent_filter.setCurrentText("All agents")
        monkeypatch.setattr(win.registry, "list_projects", lambda: existing)
        win.active_project_id = None
        win.refresh_project_controls()

    def test_saved_chat_can_be_assigned_and_returned_to_unfiled(
            self, win, monkeypatch, tmp_path):
        import json

        path = tmp_path / "chat.json"
        path.write_text(json.dumps({
            "agent": "chat",
            "messages": [{"role": "user", "content": "hello"}],
            "response": "hi",
        }), encoding="utf-8")
        monkeypatch.setattr(win, "load_history_list", lambda: None)
        monkeypatch.setattr(
            win, "_chat_projects",
            {"moonlight": {"id": "moonlight", "name": "Moonlight"}},
        )

        win._assign_chat_to_project(str(path), "moonlight")
        assert json.loads(path.read_text(encoding="utf-8"))["project"] == "moonlight"

        win._assign_chat_to_project(str(path), None)
        assert "project" not in json.loads(path.read_text(encoding="utf-8"))

    def test_completed_chat_is_saved_and_accounted_to_pending_project(
            self, win, monkeypatch):
        usage_calls = []
        saved_calls = []
        monkeypatch.setattr(
            win.usage_tracker, "log_request",
            lambda **kwargs: usage_calls.append(kwargs) or {
                "cost_eur": 0.0,
                "estimated_cost": 0.0,
                "input_tokens": 1,
                "output_tokens": 1,
            },
        )
        monkeypatch.setattr(
            win.history, "save_chat",
            lambda **kwargs: saved_calls.append(kwargs),
        )
        monkeypatch.setattr(win, "load_history_list", lambda: None)
        monkeypatch.setattr(win, "update_usage_labels", lambda: None)

        win.pending_agent = "chat"
        win.pending_backend = "ollama"
        win.pending_model = "test-model"
        win.pending_command = "General Chat"
        win.pending_prompt = "hello"
        win.pending_messages = [win._timestamped_message("user", "hello")]
        win.pending_usage = None
        win.pending_project = "moonlight"
        win.current_messages = list(win.pending_messages)
        win.active_run_id = None

        win.handle_chat_finished("hi")

        assert usage_calls[0]["project"] == "moonlight"
        assert saved_calls[0]["project"] == "moonlight"

    def test_saved_trace_search_can_be_filtered_and_reopened(
            self, win, monkeypatch, tmp_path):
        path = tmp_path / "trace.json"
        data = {
            "agent": "osint",
            "backend": "deepseek",
            "model": "deepseek-v4-flash",
            "command": "Username",
            "messages": [{"role": "user", "content": "@sapio1337"}],
            "response": (
                "## QUERY STRUCTURE\nusername variants\n"
                "## GOOGLE DORKS\n\"@sapio1337\"\n"
                "## PUBLIC SOURCES\nGitHub\n"
                "## SUMMARY & NEXT STEPS\nReview manually"
            ),
        }
        monkeypatch.setattr(win.history, "list_chats", lambda: [path])
        monkeypatch.setattr(win.history, "load_chat", lambda _path: data)

        win.saved_search_search.setText("sapio")
        win.load_saved_searches()
        assert win.saved_search_list.count() == 1
        assert "@sapio1337" in win.saved_search_list.item(0).text()

        win.open_selected_search(win.saved_search_list.item(0))
        trace = win.panels["osint"]
        assert trace.target_input.text() == "@sapio1337"
        assert trace.type_box.currentText() == "Username"
        assert "username variants" in trace.sections._raw
        assert trace.status_label.text() == "Saved search loaded."
        assert "no external sources were queried" in trace.activity_box.toPlainText()

    def test_chat_width_is_bounded_only_when_the_workspace_is_ultrawide(self, win):
        win.center_widget.resize(2200, 1000)
        win.select_agent("chat")
        wide_inset = win.center_layout.contentsMargins().left()
        assert wide_inset == 480

        win.center_widget.resize(1000, 700)
        win._update_workspace_margins()
        assert win.center_layout.contentsMargins().left() == 18

        win.center_widget.resize(2200, 1000)
        win.select_agent("osint")
        assert win.center_layout.contentsMargins().left() == 18

    def test_chat_composer_is_multiline_sized_and_enter_sends(self, win):
        from main import ChatInput

        win.select_agent("chat")
        assert win.input_box.minimumHeight() >= 120
        composer = ChatInput()
        sent = QSignalSpy(composer.sendRequested)
        composer.setPlainText("hello")
        QTest.keyClick(composer, Qt.Key_Return)
        assert sent.count() == 1

    def test_shift_enter_adds_a_line_without_sending(self, win):
        from main import ChatInput

        win.select_agent("chat")
        composer = ChatInput()
        sent = QSignalSpy(composer.sendRequested)
        composer.setPlainText("first")
        cursor = composer.textCursor()
        cursor.movePosition(QTextCursor.End)
        composer.setTextCursor(cursor)
        QTest.keyClick(composer, Qt.Key_Return, Qt.ShiftModifier)
        assert "\n" in composer.toPlainText()
        assert sent.count() == 0

    def test_chat_transcript_renders_every_message_with_timestamps(self, win):
        win.current_messages = [
            win._timestamped_message("user", "Hello", "2026-08-31T10:15:00+01:00"),
            win._timestamped_message("assistant", "Hi", "2026-08-31T10:15:03+01:00"),
            win._timestamped_message("system", "Saved", "2026-08-31T10:15:04+01:00"),
        ]
        win._render_chat_conversation(force_tail=True)
        transcript = win.output_box.toPlainText()
        assert "YOU · 31 Aug 2026 · 10:15" in transcript
        assert "ASSISTANT · 31 Aug 2026 · 10:15" in transcript
        assert "SYSTEM · 31 Aug 2026 · 10:15" in transcript
        assert win.output_box.verticalScrollBar() is not None

    def test_shared_document_search_counts_and_clears_matches(self, win):
        from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QTextBrowser

        search = QLineEdit()
        previous = QPushButton()
        next_button = QPushButton()
        count = QLabel()
        browser = QTextBrowser()
        browser.setPlainText("Model selection\nAnother model\nNo match here")

        win._wire_document_search(
            search, previous, next_button, count, browser
        )
        search.setText("model")
        assert count.text() == "2 matches"

        search.clear()
        assert count.text() == ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. AgentPanel, with no GodAI anywhere
# ─────────────────────────────────────────────────────────────────────────────

class FakeAgentFactory:
    """Stands in for the thing that writes real files into agents/ and config/."""

    def __init__(self, success=True):
        self.created = []
        self.success = success

    def create_agent(self, spec):
        self.created.append(spec)
        if self.success:
            return {"success": True, "files_created": [f"agents/{spec['name']}_agent.py"]}
        return {"success": False, "errors": ["disk on fire"]}


class FakeHost:
    """The whole surface a panel is allowed to touch, and nothing else."""

    def __init__(self, models=("m1", "m2")):
        self.models = list(models)
        self.loaders = {}
        self.calls = []
        self.request_ids = []
        self.agent_instances = {"demo": object()}
        self.authorized = True
        self.agent_factory = FakeAgentFactory()

    def load_models_into(self, provider_box, model_box, context,
                         empty_placeholder=False):
        self.calls.append(("load", provider_box.currentText(), context))
        model_box.clear()
        model_box.addItems([f"{provider_box.currentText()}-{m}" for m in self.models])

    def register_model_loader(self, agent_key, loader):
        self.loaders[agent_key] = loader

    def authorize_request(self, agent, provider, model, prompt,
                          tool=None, label=None, request_id=None):
        self.calls.append(("authorize", agent, provider, model, prompt, tool, label))
        self.request_ids.append(("authorize", request_id))
        return self.authorized

    def record_request(self, agent, response, messages=None, request_id=None):
        self.calls.append(("record", agent, response, messages))
        self.request_ids.append(("record", request_id))

    def abandon_request(self, agent, reason="error", request_id=None):
        self.calls.append(("abandon", agent, reason))
        self.request_ids.append(("abandon", request_id))

    def note_request_usage(self, agent, usage, request_id=None):
        self.calls.append(("usage", agent, usage))
        self.request_ids.append(("usage", request_id))

    def record_external_research(self, **details):
        self.calls.append(("external", details))

    def run_backend(self, backend, model, messages, prompt):
        self.calls.append(("run", backend, model, messages, prompt))
        return "done"

    def _note_failure(self, context, exc, widget=None):
        self.calls.append(("failure", context))

    def show_agent_docs(self):
        self.calls.append(("docs",))


class FakeScanWorker(QObject):
    """A SubprocessWorker with the process taken out."""

    finished_signal = Signal(str)
    error_signal = Signal(str)
    instances = []

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
        self.running = True
        FakeScanWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.running = False


class FakeWorker(QObject):
    """A ChatWorker with the threads taken out."""

    token_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)
    status_signal = Signal(str)

    instances = []

    def __init__(self, run_backend, provider, model, messages, prompt):
        super().__init__()
        self.args = (provider, model, messages, prompt)
        self.started = False
        self.cancelled = False
        self.running = True
        FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True


class FakeVpnConnectionWorker(QObject):
    """A Tunnel connection worker that never runs wg-quick/openvpn/sudo."""

    finished_signal = Signal(dict)
    error_signal = Signal(str)
    instances = []

    def __init__(self, action, profile):
        super().__init__()
        self.action = action
        self.profile = profile
        self.started = False
        FakeVpnConnectionWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return False


class FakeVpnDiagnosticsWorker(QObject):
    """A Tunnel diagnostics worker with all system and network reads removed."""

    finished_signal = Signal(object)
    error_signal = Signal(str)
    instances = []

    def __init__(self, include_external=False, selected_profile=None):
        super().__init__()
        self.include_external = include_external
        self.selected_profile = selected_profile
        self.started = False
        self.cancelled = False
        self.running = True
        self.waited = False
        self.terminated = False
        FakeVpnDiagnosticsWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def wait(self, _timeout_ms):
        self.waited = True
        self.running = False
        return True

    def terminate(self):
        self.terminated = True
        self.running = False


class DemoPanel(AgentPanel):
    agent_key = "demo"
    default_provider = "deepseek"
    worker_class = FakeWorker


@pytest.fixture
def panel(qapp):
    host = FakeHost()
    p = DemoPanel(host)
    _container, layout = p.flow_row()
    p.build_provider_row(layout)
    return p


class TestAgentPanel:

    def test_it_builds_without_a_window(self, panel):
        assert panel.provider_box is not None and panel.model_box is not None

    def test_it_starts_on_its_default_provider(self, panel):
        assert panel.provider == "deepseek"

    def test_it_asks_the_host_for_models_once_while_building(self, panel):
        assert [c for c in panel.host.calls if c[0] == "load"] == [
            ("load", "deepseek", "demo")
        ]

    def test_changing_provider_reloads_through_the_host(self, panel):
        panel.provider_box.setCurrentText("openai")
        assert ("load", "openai", "demo") in panel.host.calls
        assert panel.model == "openai-m1"

    def test_it_registers_its_loader_with_the_host(self, panel):
        assert callable(panel.host.loaders["demo"])

    def test_authorize_passes_the_panels_own_key_and_selection(self, panel):
        assert panel.authorize("prompt text", label="Person") is True
        assert panel.host.calls[-1] == (
            "authorize", "demo", "deepseek", "deepseek-m1", "prompt text",
            None, "Person",
        )

    def test_a_blocked_request_is_reported_as_false(self, panel):
        panel.host.authorized = False
        assert panel.authorize("prompt text") is False

    def test_record_abandon_and_usage_all_carry_the_agent_key(self, panel):
        panel.authorize("prompt")
        request_id = panel._request_id
        panel.note_usage({"input_tokens": 5})
        panel.record("response", [{"role": "user"}])
        panel.authorize("another prompt")
        abandon_id = panel._request_id
        panel.abandon("stopped")
        assert ("usage", "demo", {"input_tokens": 5}) in panel.host.calls
        assert ("record", "demo", "response", [{"role": "user"}]) in panel.host.calls
        assert ("abandon", "demo", "stopped") in panel.host.calls
        assert panel.host.request_ids[-5:] == [
            ("authorize", request_id),
            ("usage", request_id),
            ("record", request_id),
            ("authorize", abandon_id),
            ("abandon", abandon_id),
        ]
        assert request_id != abandon_id
        assert panel._request_id is None

    def test_run_backend_uses_the_current_selection(self, panel):
        panel.run_backend([{"role": "user"}], "hello")
        assert panel.host.calls[-1] == (
            "run", "deepseek", "deepseek-m1", [{"role": "user"}], "hello",
        )

    def test_agent_comes_from_the_host_registry(self, panel):
        assert panel.agent() is panel.host.agent_instances["demo"]

    def test_note_failure_is_prefixed_with_the_agent_key(self, panel):
        panel.note_failure("load models", RuntimeError("x"))
        assert panel.host.calls[-1] == ("failure", "demo: load models")

    def test_load_models_is_a_no_op_before_the_row_is_built(self, qapp):
        DemoPanel(FakeHost()).load_models()  # must not raise

    def test_a_bare_panel_reports_no_selection(self, qapp):
        bare = DemoPanel(FakeHost())
        assert (bare.provider, bare.model) == ("", "")


class TestAgentPanelRuns:

    @pytest.fixture(autouse=True)
    def _fresh_workers(self):
        FakeWorker.instances.clear()

    def test_start_worker_passes_the_current_selection(self, panel):
        worker = panel.start_worker([{"role": "user"}], "hello")
        assert worker.args == ("deepseek", "deepseek-m1", [{"role": "user"}], "hello")
        assert worker.started is True

    def test_the_panel_keeps_a_handle_on_its_worker(self, panel):
        worker = panel.start_worker([], "hello")
        assert panel.worker is worker

    def test_usage_is_reported_without_the_caller_wiring_it(self, panel):
        # The signal every panel had to remember to connect, and one didn't.
        worker = panel.start_worker([], "hello")
        worker.usage_signal.emit({"input_tokens": 7})
        assert ("usage", "demo", {"input_tokens": 7}) in panel.host.calls

    def test_optional_callbacks_are_connected_when_given(self, panel):
        seen = []
        worker = panel.start_worker(
            [], "hello",
            on_token=lambda t: seen.append(("token", t)),
            on_finished=lambda r: seen.append(("finished", r)),
            on_error=lambda e: seen.append(("error", e)),
        )
        worker.token_signal.emit("tok")
        worker.finished_signal.emit("done")
        worker.error_signal.emit("boom")
        assert seen == [("token", "tok"), ("finished", "done"), ("error", "boom")]

    def test_omitted_callbacks_are_not_required(self, panel):
        worker = panel.start_worker([], "hello")
        worker.token_signal.emit("tok")  # nothing connected; must not raise

    def test_stop_cancels_a_running_worker(self, panel):
        worker = panel.start_worker([], "hello")
        assert panel.stop_worker() is True
        assert worker.cancelled is True

    def test_stop_is_harmless_when_nothing_is_running(self, panel):
        assert panel.stop_worker() is False
        worker = panel.start_worker([], "hello")
        worker.running = False
        assert panel.stop_worker() is False
        assert worker.cancelled is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. The first vertical to move: Trace
# ─────────────────────────────────────────────────────────────────────────────

class FakeOsintAgent:
    def __init__(self):
        self.calls = []

    def build_messages(self, target, query_type):
        self.calls.append((target, query_type))
        return [{"role": "user", "content": f"{query_type}:{target}"}]

    def validate_target(self, target, query_type):
        from agents.osint_agent import OSINTAgent
        return OSINTAgent.validate_target(target, query_type)


class FakeLookupWorker(QObject):
    progress_signal = Signal(str, str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)
    instances = []

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.running = True
        self.cancelled = False
        self.__class__.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        self.running = False


class FakeIdentityLookupWorker(FakeLookupWorker):
    instances = []

    def __init__(self, target, query_type, sources=()):
        super().__init__(target)
        self.query_type = query_type
        self.sources = tuple(sources)


@pytest.fixture
def trace(qapp, monkeypatch):
    """Trace built against a fake host — no `GodAI`, no window, no thread."""
    from ui.panels.osint import OsintPanel
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(OsintPanel, "worker_class", FakeWorker)
    monkeypatch.setattr(OsintPanel, "lookup_worker_class", FakeLookupWorker)
    monkeypatch.setattr(
        OsintPanel, "identity_lookup_worker_class", FakeIdentityLookupWorker
    )
    FakeWorker.instances.clear()
    FakeLookupWorker.instances.clear()
    FakeIdentityLookupWorker.instances.clear()

    host = FakeHost()
    host.agent_instances["osint"] = FakeOsintAgent()
    panel = OsintPanel(host)
    panel.target_input.setText("acme.com")
    return panel


class TestTracePanel:

    def test_it_builds_without_a_window(self, trace):
        assert trace.provider_box is not None
        assert trace.status_label.text() == "Idle"
        assert trace.stop_btn.isEnabled() is False

    def test_it_starts_hidden(self, trace):
        # The centre pane stacks every panel and shows one; a panel that forgets
        # to hide itself appears behind whichever agent is selected.
        assert trace.isHidden() is True

    def test_an_empty_target_never_reaches_the_guard(self, trace):
        trace.target_input.clear()
        trace.analyse()
        assert [c for c in trace.host.calls if c[0] == "authorize"] == []

    def test_an_invalid_typed_target_never_reaches_the_guard(self, trace):
        trace.type_box.setCurrentText("Email")
        trace.target_input.setText("not-an-email")
        trace.analyse()
        assert [c for c in trace.host.calls if c[0] == "authorize"] == []
        assert trace.status_label.text() == "Check the target and try again."
        assert "Target validation stopped" in trace.activity_box.toPlainText()

    def test_auto_detected_type_reaches_agent_guard_and_tracker(self, trace):
        trace.target_input.setText("analyst@example.com")
        trace.analyse()
        authorize = [c for c in trace.host.calls if c[0] == "authorize"][-1]
        assert authorize[6] == "Email"
        assert "Email (auto-detected)" in trace.activity_box.toPlainText()

    def test_no_model_never_reaches_the_guard(self, trace):
        trace.model_box.clear()
        trace.analyse()
        assert [c for c in trace.host.calls if c[0] == "authorize"] == []

    def test_a_blocked_request_starts_no_worker(self, trace):
        trace.host.authorized = False
        trace.analyse()
        assert FakeWorker.instances == []

    def test_the_query_type_is_passed_to_the_agent_and_the_guard(self, trace):
        trace.type_box.setCurrentText("Domain")
        trace.analyse()
        assert trace.host.agent_instances["osint"].calls == [("acme.com", "Domain")]
        authorize = [c for c in trace.host.calls if c[0] == "authorize"][-1]
        assert authorize[1] == "osint" and authorize[4] == "acme.com"
        assert authorize[6] == "Domain"

    def test_running_disables_analyse_and_enables_stop(self, trace):
        trace.analyse()
        assert trace.analyse_btn.isEnabled() is False
        assert trace.stop_btn.isEnabled() is True

    def test_activity_tracker_explains_local_scope_while_running(self, trace):
        trace.provider_box.setCurrentText("ollama")
        trace.analyse()
        activity = trace.activity_box.toPlainText()
        assert "target stays on this Mac" in activity
        assert "No websites or public databases are contacted" in activity
        assert "Google dorks" in activity

    def test_live_domain_research_requires_explicit_confirmation(
            self, trace, monkeypatch):
        trace.target_input.setText("example.com")
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
        )
        trace.live_research()
        assert FakeLookupWorker.instances == []
        assert trace.status_label.text() == "Live Research cancelled before any lookup."

    def test_ip_consent_names_every_key_gated_source(self, trace, monkeypatch):
        """The IP consent text must disclose the key-gated IP sources that
        domain_lookup.lookup() actually contacts. Regression: Shodan InternetDB
        (always) and IPinfo / Criminal IP (when keyed) were contacted without
        being named in the dialog the user approves."""
        seen = {}
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: seen.update(text=a[2]) or QMessageBox.No),
        )
        monkeypatch.setenv("IPINFO_API_KEY", "tok")
        monkeypatch.setenv("CRIMINALIP_API_KEY", "cip")
        trace.type_box.setCurrentText("IP Address")
        trace.target_input.setText("203.0.113.5")
        trace.live_research()
        assert "Shodan InternetDB" in seen["text"]
        assert "IPinfo" in seen["text"]
        assert "Criminal IP" in seen["text"]

    def test_company_consent_names_courtlistener(self, trace, monkeypatch):
        """Company Live Research always contacts CourtListener, so the consent
        text (and the audit line built from it) must name it. Regression: the
        target was POSTed to CourtListener without disclosure."""
        seen = {}
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: seen.update(text=a[2]) or QMessageBox.No),
        )
        trace.type_box.setCurrentText("Company")
        trace.target_input.setText("Acme Ltd")
        trace.live_research()
        assert "CourtListener" in seen["text"]

    def test_live_domain_research_tracks_sources_and_saves_results(
            self, trace, monkeypatch):
        trace.target_input.setText("example.com")
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
        )
        trace.live_research()
        worker = FakeLookupWorker.instances[-1]
        assert worker.target == "example.com"
        assert [call for call in trace.host.calls if call[0] == "authorize"] == []

        worker.progress_signal.emit("WHOIS", "checking")
        worker.progress_signal.emit("WHOIS", "checked")
        worker.progress_signal.emit("DNS", "error")
        worker.finished_signal.emit({
            "type": "domain",
            "query": "example.com",
            "whois": {"registrar": "Example Registrar"},
            "dns": {"error": "no records resolved"},
            "certificates": {"total_unique": 2, "sample": ["www.example.com"]},
            "sources_contacted": [
                {"source": "WHOIS", "status": "checked"},
                {"source": "DNS", "status": "error"},
                {"source": "Certificate transparency (crt.sh)", "status": "checked"},
            ],
        })

        assert trace.status_label.text() == "Live Research complete."
        assert "Sources actually contacted" in trace.activity_box.toPlainText()
        assert "Example Registrar" in trace.sections._raw
        saved = [call for call in trace.host.calls if call[0] == "external"]
        assert saved and saved[-1][1]["target"] == "example.com"

    def test_live_research_stop_retains_partial_results(
            self, trace, monkeypatch):
        trace.target_input.setText("192.0.2.10")
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
        )
        trace.live_research()
        worker = FakeLookupWorker.instances[-1]
        trace.stop()
        assert worker.cancelled
        worker.finished_signal.emit({
            "type": "ip", "query": "192.0.2.10", "cancelled": True,
            "whois": {"country": "ZZ"},
            "sources_contacted": [{"source": "WHOIS", "status": "checked"}],
        })
        assert trace.status_label.text() == "Stopped — partial results retained."
        assert "partial results retained" in trace.status_label.text()
        assert '"cancelled": true' in trace.sections._raw

    def test_live_username_research_requires_consent_and_uses_urlscan(
            self, trace, monkeypatch):
        trace.target_input.setText("@researcher_1")
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
        )
        trace.live_research()
        worker = FakeIdentityLookupWorker.instances[-1]
        assert worker.query_type == "Username"
        assert worker.sources == ()
        assert "URLScan" in trace.activity_box.toPlainText()

        worker.finished_signal.emit({
            "type": "username", "query": "researcher_1",
            "urlscan": {"unique_domains_found": 1, "hits": []},
            "sources_contacted": [{"source": "URLScan", "status": "checked"}],
        })
        assert "unique_domains_found" in trace.sections._raw

    def test_live_email_research_passes_only_explicitly_selected_sources(
            self, trace, monkeypatch):
        trace.target_input.setText("analyst@example.com")
        monkeypatch.setattr(
            trace, "_choose_email_sources", lambda target: ("emailrep",)
        )
        trace.live_research()
        worker = FakeIdentityLookupWorker.instances[-1]
        assert worker.query_type == "Email"
        assert worker.sources == ("emailrep",)
        assert "BreachDirectory" not in trace.activity_box.toPlainText()
        assert "Have I Been Pwned" not in trace.activity_box.toPlainText()

        worker.finished_signal.emit({
            "type": "email", "query": "analyst@example.com",
            "reputation": {"score": "high"},
            "sources_contacted": [{"source": "EmailRep", "status": "checked"}],
        })
        assert "\"score\": \"high\"" in trace.sections._raw

    def test_live_email_research_can_include_gravatar(self, trace, monkeypatch):
        trace.target_input.setText("analyst@example.com")
        monkeypatch.setattr(
            trace, "_choose_email_sources", lambda target: ("emailrep", "gravatar")
        )
        trace.live_research()
        worker = FakeIdentityLookupWorker.instances[-1]
        assert worker.sources == ("emailrep", "gravatar")
        assert "EmailRep, Gravatar" in trace.activity_box.toPlainText()

        worker.finished_signal.emit({
            "type": "email", "query": "analyst@example.com",
            "gravatar": {"status": "ok", "found": True, "display_name": "Analyst"},
            "sources_contacted": [{"source": "Gravatar", "status": "checked"}],
        })
        labels = " ".join(label.text() for label in trace.sections.findChildren(QLabel))
        assert "Gravatar profile" in labels

    def test_live_domain_research_names_and_shows_the_web_archive(
            self, trace, monkeypatch):
        prompts = []
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: prompts.append(a[2]) or QMessageBox.Yes),
        )
        trace.target_input.setText("example.com")
        trace.live_research()
        assert "Wayback Machine" in prompts[-1]
        FakeLookupWorker.instances[-1].finished_signal.emit({
            "type": "domain", "query": "example.com",
            "archive": {"archived": True, "first_snapshot": {"date": "1999-01-02"}},
            "sources_contacted": [{"source": "Wayback Machine", "status": "checked"}],
        })
        labels = " ".join(label.text() for label in trace.sections.findChildren(QLabel))
        assert "Web archive" in labels
        assert "1999-01-02" in trace.sections._raw

    def test_live_ip_research_names_and_shows_network_and_attack_reports(
            self, trace, monkeypatch):
        prompts = []
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: prompts.append(a[2]) or QMessageBox.Yes),
        )
        trace.target_input.setText("192.0.2.10")
        trace.live_research()
        assert "Team Cymru" in prompts[-1] and "DShield" in prompts[-1]
        FakeLookupWorker.instances[-1].finished_signal.emit({
            "type": "ip", "query": "192.0.2.10",
            "network": {"asn": "AS64500"}, "attack_reports": {"reports": 4},
            "passive_dns": {"records": []},
            "sources_contacted": [{"source": "SANS DShield", "status": "checked"}],
        })
        labels = " ".join(label.text() for label in trace.sections.findChildren(QLabel))
        for card in ("Network owner (ASN)", "Passive DNS history", "Attack reports (DShield)"):
            assert card in labels
        assert "AS64500" in trace.sections._raw

    def test_live_username_research_shows_github_and_keybase(self, trace, monkeypatch):
        prompts = []
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: prompts.append(a[2]) or QMessageBox.Yes),
        )
        trace.target_input.setText("@researcher_1")
        trace.live_research()
        assert "GitHub, and Keybase" in prompts[-1]
        FakeIdentityLookupWorker.instances[-1].finished_signal.emit({
            "type": "username", "query": "researcher_1",
            "github": {"found": True}, "keybase": {"found": False},
            "sources_contacted": [{"source": "GitHub", "status": "checked"}],
        })
        labels = " ".join(label.text() for label in trace.sections.findChildren(QLabel))
        assert "GitHub profile" in labels and "Keybase profile and proofs" in labels

    @pytest.mark.parametrize("key,named,sources", [
        ("", False, ()),
        ("test-key", True, ("opensanctions",)),
    ])
    def test_company_research_adds_opensanctions_only_with_a_key(
            self, trace, monkeypatch, key, named, sources):
        from providers import company_lookup

        monkeypatch.setattr(company_lookup, "OPENSANCTIONS_KEY", key)
        prompts = []
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: prompts.append(a[2]) or QMessageBox.Yes),
        )
        trace.type_box.setCurrentText("Company")
        trace.target_input.setText("Example Limited")
        trace.live_research()
        assert ("OpenSanctions" in prompts[-1]) is named
        worker = FakeIdentityLookupWorker.instances[-1]
        assert worker.sources == sources
        worker.finished_signal.emit({
            "type": "company", "query": "Example Limited",
            "legal_entities": {"records": []},
            **({"sanctions": {"matches": [{"name": "Example Limited", "listed": True}]}}
               if named else {}),
            "sources_contacted": [{"source": "GLEIF Legal Entity Index", "status": "checked"}],
        })
        labels = " ".join(label.text() for label in trace.sections.findChildren(QLabel))
        assert ("Sanctions and watchlists (OpenSanctions)" in labels) is named

    def test_the_company_worker_passes_the_sanctions_choice_through(self, monkeypatch):
        from providers import company_lookup
        from ui.workers import IdentityLookupWorker

        seen = []
        monkeypatch.setattr(company_lookup, "lookup",
                            lambda target, **kwargs: seen.append(kwargs) or {"type": "company"})
        IdentityLookupWorker("Example", "Company", ("opensanctions",)).run()
        IdentityLookupWorker("Example", "Company", ()).run()
        assert [kwargs["sanctions"] for kwargs in seen] == [True, False]

    def test_live_company_research_requires_consent_and_uses_gleif(
            self, trace, monkeypatch):
        trace.type_box.setCurrentText("Company")
        trace.target_input.setText("Example Limited")
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
        )
        trace.live_research()
        worker = FakeIdentityLookupWorker.instances[-1]
        assert worker.query_type == "Company"
        assert worker.sources == ()
        assert "GLEIF Legal Entity Index" in trace.activity_box.toPlainText()

        worker.finished_signal.emit({
            "type": "company", "query": "Example Limited",
            "legal_entities": {
                "total_matches": 1,
                "records": [{"legal_name": "Example Limited", "lei": "LEI123"}],
            },
            "sources_contacted": [
                {"source": "GLEIF Legal Entity Index", "status": "checked"}
            ],
        })
        assert "LEI123" in trace.sections._raw
        saved = [call for call in trace.host.calls if call[0] == "external"]
        assert saved[-1][1]["query_type"] == "Company"

    @pytest.mark.parametrize("query_type,target", [
        ("Person", "Example Person"),
        ("Phone", "+353 1 234 5678"),
    ])
    def test_person_and_phone_live_research_stays_local(
            self, trace, monkeypatch, query_type, target):
        notices = []
        monkeypatch.setattr(
            QMessageBox, "information",
            staticmethod(lambda *args, **kwargs: notices.append(args[2])),
        )
        trace.type_box.setCurrentText(query_type)
        trace.target_input.setText(target)
        trace.live_research()
        assert FakeLookupWorker.instances == []
        assert FakeIdentityLookupWorker.instances == []
        assert "will not send personal identifiers" in notices[-1]

    def test_tokens_stream_into_the_raw_box(self, trace):
        trace.analyse()
        trace.worker.token_signal.emit("## QUERY ")
        trace.worker.token_signal.emit("STRUCTURE")
        assert trace.stream_box.toPlainText() == "## QUERY STRUCTURE"
        assert trace.stream_box.isVisibleTo(trace) is True
        assert trace.sections.isVisibleTo(trace) is False

    def test_a_finished_answer_is_recorded_and_rendered_as_cards(self, trace):
        trace.analyse()
        trace.worker.finished_signal.emit(
            "## QUERY STRUCTURE\nS\n## GOOGLE DORKS\nd\n"
            "## PUBLIC SOURCES\np\n## SUMMARY\nx"
        )
        assert ("record", "osint",
                "## QUERY STRUCTURE\nS\n## GOOGLE DORKS\nd\n"
                "## PUBLIC SOURCES\np\n## SUMMARY\nx", None) in trace.host.calls
        assert trace.sections.isVisibleTo(trace) is True
        assert trace.stream_box.isVisibleTo(trace) is False
        assert trace.status_label.text() == "Done."
        assert trace.analyse_btn.isEnabled() is True
        activity = trace.activity_box.toPlainText()
        assert "External sources queried: none" in activity
        assert "not verified findings" in activity

    def test_activity_tracker_stays_visible_after_results_return(self, trace):
        trace.analyse()
        trace.worker.token_signal.emit("## QUERY STRUCTURE")
        trace.worker.finished_signal.emit("## SUMMARY\nDone")
        assert trace.activity_box.isVisibleTo(trace) is True
        assert "Model response received" in trace.activity_box.toPlainText()
        assert "Completed" in trace.activity_box.toPlainText()

    def test_an_error_abandons_the_request_so_it_is_not_billed(self, trace):
        trace.analyse()
        trace.worker.error_signal.emit("provider down")
        assert ("abandon", "osint", "error") in trace.host.calls
        assert "provider down" in trace.stream_box.toPlainText()
        assert trace.status_label.text() == "Error."
        assert trace.analyse_btn.isEnabled() is True
        assert trace.stop_btn.isEnabled() is False

    def test_deepseek_balance_error_prepares_local_retry_without_resending(self, trace):
        trace.provider_box.setCurrentText("deepseek")
        trace.analyse()
        run_calls_before = [call for call in trace.host.calls if call[0] == "run"]

        trace.worker.error_signal.emit(
            "The DeepSeek cloud API could not run this request because the cloud "
            "account has no API credit. Local DeepSeek models in Ollama remain free to use."
        )

        assert trace.provider == "ollama"
        assert trace.model == "ollama-m1"
        assert trace.status_label.text() == "Ready to retry locally."
        assert "did not resend the target" in trace.stream_box.toPlainText()
        assert "choose it yourself" in trace.stream_box.toPlainText()
        assert [call for call in trace.host.calls if call[0] == "run"] == run_calls_before

    def test_usage_reaches_the_host_without_the_panel_wiring_it(self, trace):
        trace.analyse()
        trace.worker.usage_signal.emit({"input_tokens": 11})
        assert ("usage", "osint", {"input_tokens": 11}) in trace.host.calls

    def test_stop_cancels_and_restores_the_controls(self, trace):
        trace.analyse()
        worker = trace.worker
        trace.stop()
        assert worker.cancelled is True
        assert trace.status_label.text() == "Stopped."
        assert trace.analyse_btn.isEnabled() is True
        assert trace.stop_btn.isEnabled() is False

    def test_clear_empties_the_form_and_the_output(self, trace):
        trace.analyse()
        trace.worker.finished_signal.emit("## SUMMARY\nx")
        trace.clear()
        assert trace.target_input.text() == ""
        assert trace.stream_box.toPlainText() == ""
        assert trace.status_label.text() == "Idle"


class TestTraceSectionParsing:
    """Pure text handling — no widgets, no host."""

    ANSWER = (
        "## QUERY STRUCTURE\nstructured\n"
        "## GOOGLE DORKS\nsite:acme.com\n"
        "## PUBLIC SOURCES\nwhois\n"
        "## SUMMARY\nnext steps"
    )

    def test_each_heading_becomes_its_own_section(self):
        from ui.panels.osint import OsintPanel
        parsed = OsintPanel.parse_sections(self.ANSWER)
        assert parsed == {
            "structure": "structured", "dorks": "site:acme.com",
            "sources": "whois", "summary": "next steps",
        }

    def test_a_missing_heading_comes_back_empty_rather_than_absent(self):
        from ui.panels.osint import OsintPanel
        parsed = OsintPanel.parse_sections("## GOOGLE DORKS\nsite:acme.com")
        assert parsed["dorks"] == "site:acme.com"
        assert parsed["structure"] == "" and parsed["summary"] == ""

    def test_headings_are_matched_case_insensitively(self):
        from ui.panels.osint import OsintPanel
        assert OsintPanel.parse_sections("## google dorks\nx")["dorks"] == "x"


class TestWindowStopButton:
    """`stop_current_task` — the Stop button in the run bar.

    The 2026-08-19 cull left it calling `self.author_worker` and five other
    attributes that no longer exist, so Stop raised `AttributeError` for every
    agent before reaching any of them.
    """

    def test_stopping_with_nothing_running_does_not_raise(self, win):
        win.stop_current_task()

    def test_it_stops_a_running_panel(self, win, monkeypatch):
        from ui.panels.osint import OsintPanel
        monkeypatch.setattr(OsintPanel, "worker_class", FakeWorker)
        monkeypatch.setattr(win, "authorize_request", lambda *a, **k: True)
        panel = win.panels["osint"]
        permission = getattr(win, f"allow_{panel.provider}_checkbox", None)
        if permission is not None:
            permission.setChecked(True)
        panel.target_input.setText("acme.com")
        panel.analyse()
        worker = panel.worker
        try:
            win.stop_current_task()
            assert worker.cancelled is True
            assert panel.status_label.text() == "Stopped."
        finally:
            panel.worker = None
            panel.clear()

    def test_it_touches_no_attribute_that_no_longer_exists(self, win):
        # Every `self.x` the method reaches for directly must be one the window
        # has. Names looked up as strings with a default (`getattr(self, name,
        # None)`) are deliberately exempt — that is the safe form.
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(type(win).stop_current_task)))
        touched = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "self"
        }
        missing = sorted(a for a in touched if not hasattr(win, a))
        assert missing == [], f"stop_current_task reaches for {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Forge
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def forge(qapp, monkeypatch):
    """Forge against a fake host — including a factory that writes nothing."""
    from ui.panels.manager import ManagerPanel
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(ManagerPanel, "worker_class", FakeWorker)
    FakeWorker.instances.clear()

    panel = ManagerPanel(FakeHost())
    panel.idea_input.setPlainText("An agent that reviews firewall logs")
    return panel


SPEC = '{"name": "fwreview", "label": "Firewall Review"}'


class TestForgePanel:

    def test_it_builds_without_a_window_and_starts_hidden(self, forge):
        assert forge.isHidden() is True
        assert forge.approve_btn.isEnabled() is False
        assert forge.reject_btn.isEnabled() is False

    def test_it_defaults_to_deepseek(self, forge):
        # Forge is the one panel that does not default to a cloud flagship.
        assert forge.provider == "deepseek"

    def test_an_empty_idea_never_reaches_the_guard(self, forge):
        forge.idea_input.setPlainText("   ")
        forge.analyze_idea()
        assert [c for c in forge.host.calls if c[0] == "authorize"] == []

    def test_a_second_run_while_busy_is_refused(self, forge):
        forge.analyze_idea()
        forge.analyze_idea()
        assert len(FakeWorker.instances) == 1

    def test_a_blocked_request_starts_no_worker(self, forge):
        forge.host.authorized = False
        forge.analyze_idea()
        assert FakeWorker.instances == []

    def test_a_blocked_request_leaves_the_panel_usable(self, forge):
        # The controls are disabled before the guard runs, so a refusal that
        # returns early used to leave Analyze dead until the app restarted.
        forge.host.authorized = False
        forge.analyze_idea()
        assert forge.analyze_btn.isEnabled() is True
        assert "Analyzing" not in forge.stream_box.toPlainText()

    def test_tokens_stream_before_the_spec_is_structured(self, forge):
        forge.analyze_idea()
        forge.worker.token_signal.emit('{"name": "fw')
        assert forge.stream_box.toPlainText() == '{"name": "fw'
        assert forge.sections.isHidden() is True

    def test_a_parsed_spec_enables_approval(self, forge):
        forge.analyze_idea()
        forge.worker.finished_signal.emit(SPEC)
        assert forge.pending_spec == {"name": "fwreview", "label": "Firewall Review"}
        assert forge.approve_btn.isEnabled() is True
        assert forge.reject_btn.isEnabled() is True
        assert "[Ready]" in forge.log.toPlainText()
        assert forge.stream_box.isHidden() is True
        assert forge.sections._raw == SPEC
        assert "Firewall Review" in " ".join(
            label.text() for label in forge.sections.findChildren(QLabel)
        )

    def test_an_unparseable_answer_leaves_approval_disabled(self, forge):
        # The response is still shown — the raw text is the only clue to why.
        forge.analyze_idea()
        forge.worker.finished_signal.emit("sorry, I could not do that")
        assert forge.pending_spec is None
        assert forge.approve_btn.isEnabled() is False
        assert "sorry, I could not do that" in forge.sections._raw

    def test_an_error_abandons_the_request(self, forge):
        forge.analyze_idea()
        forge.worker.error_signal.emit("provider down")
        assert ("abandon", "manager", "error") in forge.host.calls
        assert forge.analyze_btn.isEnabled() is True
        assert "[Error]" in forge.log.toPlainText()

    def test_approving_asks_the_host_to_write_the_agent(self, forge):
        forge.analyze_idea()
        forge.worker.finished_signal.emit(SPEC)
        forge.approve_spec()
        assert forge.host.agent_factory.created == [
            {"name": "fwreview", "label": "Firewall Review"}
        ]
        assert "[Created]" in forge.log.toPlainText()
        assert forge.approve_btn.isEnabled() is False
        assert forge.pending_spec is None

    def test_a_failed_creation_is_reported_and_keeps_the_spec(self, forge):
        forge.host.agent_factory.success = False
        forge.analyze_idea()
        forge.worker.finished_signal.emit(SPEC)
        forge.approve_spec()
        assert "[Failed]" in forge.log.toPlainText()
        assert "disk on fire" in forge.log.toPlainText()
        assert forge.pending_spec is not None

    def test_approving_nothing_does_nothing(self, forge):
        forge.approve_spec()
        assert forge.host.agent_factory.created == []

    def test_rejecting_clears_the_spec(self, forge):
        forge.analyze_idea()
        forge.worker.finished_signal.emit(SPEC)
        forge.reject_spec()
        assert forge.pending_spec is None
        assert forge.sections._raw == ""
        assert forge.stream_box.toPlainText() == ""
        assert forge.approve_btn.isEnabled() is False

    def test_stop_cancels_and_re_enables_analyse(self, forge):
        # Forge has no Stop button of its own; the window's Stop reaches it, and
        # before phase 4 it did not reach it at all.
        forge.analyze_idea()
        worker = forge.worker
        forge.stop()
        assert worker.cancelled is True
        assert forge.analyze_btn.isEnabled() is True
        assert "[Stopped]" in forge.log.toPlainText()

    def test_clear_resets_the_form(self, forge):
        forge.analyze_idea()
        forge.worker.finished_signal.emit(SPEC)
        forge.clear()
        assert forge.idea_input.toPlainText() == ""
        assert forge.sections._raw == ""
        assert forge.stream_box.toPlainText() == ""
        assert forge.pending_spec is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Bug Spray
# ─────────────────────────────────────────────────────────────────────────────

class FakeBugBountyAgent:
    def __init__(self):
        self.calls = []

    def build_messages(self, target, program, scope_type, findings, nmap_output):
        self.calls.append((target, program, scope_type, findings, nmap_output))
        return [{"role": "user", "content": target}]


REPORT = (
    "## VULNERABILITY REPORT\n**Severity** High\nCVSS 8.1\n"
    "A bounty of $1,500 is typical.\n"
    "## Proof of Concept\ncurl …\n"
    "## Remediation\npatch it\n"
    "## SUBMISSION DRAFT\ndraft text"
)


@pytest.fixture
def spray(qapp, monkeypatch):
    from ui.panels.bug_bounty import BugBountyPanel

    monkeypatch.setattr(BugBountyPanel, "worker_class", FakeWorker)
    FakeWorker.instances.clear()

    host = FakeHost()
    host.agent_instances["bug_bounty"] = FakeBugBountyAgent()
    panel = BugBountyPanel(host)
    panel.target_input.setText("https://target.example.com/app")
    return panel


class TestBugSprayPanel:

    def test_it_builds_hidden_with_saving_disabled(self, spray):
        from PySide6.QtWidgets import QTabWidget
        assert spray.isHidden() is True
        assert spray.save_btn.isEnabled() is False
        # The report is one sectioned view, not the five tabs it replaced in
        # V2. The program radar above it is a separate feed whose Recent
        # changes / Programs tabs are not report sections, so it is exempt.
        report_tabs = [
            tabs for tabs in spray.findChildren(QTabWidget)
            if not spray.program_feed.isAncestorOf(tabs)
        ]
        assert report_tabs == []
        assert spray.sections is not None

    def test_nothing_to_analyse_says_so_without_spending(self, spray):
        spray.target_input.clear()
        spray.analyse()
        assert "Enter a target" in spray.status_label.text()
        assert [c for c in spray.host.calls if c[0] == "authorize"] == []

    def test_no_model_says_so_without_spending(self, spray):
        spray.model_box.clear()
        spray.analyse()
        assert spray.status_label.text() == "Select a model first."
        assert [c for c in spray.host.calls if c[0] == "authorize"] == []

    def test_a_blocked_request_leaves_the_panel_usable(self, spray):
        spray.host.authorized = False
        spray.analyse()
        assert FakeWorker.instances == []
        assert spray.analyse_btn.isEnabled() is True
        assert spray.stop_btn.isEnabled() is False

    def test_every_input_reaches_the_agent(self, spray):
        spray.program_input.setText("HackerOne — Acme")
        spray.scope_box.setCurrentText("API / REST")
        spray.findings_input.setPlainText("401 on /admin")
        spray.analyse()
        assert spray.host.agent_instances["bug_bounty"].calls == [
            ("https://target.example.com/app", "HackerOne — Acme",
             "API / REST", "401 on /admin", ""),
        ]

    def test_tokens_stream_until_the_report_can_be_sectioned(self, spray):
        spray.analyse()
        spray.worker.token_signal.emit("## VULN")
        spray.worker.token_signal.emit("ERABILITY")
        assert spray.stream_box.toPlainText() == "## VULNERABILITY"
        assert spray.sections.isHidden() is True

    def test_a_finished_report_fills_the_section_cards(self, spray):
        spray.analyse()
        spray.worker.finished_signal.emit(REPORT)
        parsed = spray.parse_sections(REPORT)
        assert parsed["remediation"] == "patch it"
        assert parsed["submission"] == "draft text"
        assert parsed["poc"].startswith("curl")
        assert spray.sections._raw == REPORT
        assert spray.stream_box.isHidden() is True
        assert spray.save_btn.isEnabled() is True
        assert spray.status_label.text() == "Analysis complete."

    def test_a_finished_report_fills_the_indicators(self, spray):
        spray.analyse()
        spray.worker.finished_signal.emit(REPORT)
        assert spray.severity_label.text() == "High"
        assert spray.cvss_label.text() == "8.1"
        assert spray.bounty_label.text() == "$1,500"

    def test_indicators_stay_blank_when_the_answer_has_no_figures(self, spray):
        spray.analyse()
        spray.worker.finished_signal.emit("## VULNERABILITY REPORT\nno numbers here")
        assert spray.severity_label.text() == "—"
        assert spray.cvss_label.text() == "—"
        assert spray.bounty_label.text() == "—"

    def test_an_unsectioned_answer_still_shows_something(self, spray):
        # SectionView falls back to one Response card when the model ignores
        # the requested heading format.
        spray.analyse()
        spray.worker.finished_signal.emit("just prose, no headings")
        assert spray.sections._raw == "just prose, no headings"
        assert "just prose, no headings" in " ".join(
            label.text() for label in spray.sections.findChildren(QLabel)
        )

    def test_an_error_abandons_the_request(self, spray):
        spray.analyse()
        spray.worker.error_signal.emit("provider down")
        assert ("abandon", "bug_bounty", "error") in spray.host.calls
        assert spray.status_label.text() == "Error."
        assert spray.analyse_btn.isEnabled() is True

    def test_stop_cancels_and_restores_the_controls(self, spray):
        spray.analyse()
        worker = spray.worker
        spray.stop()
        assert worker.cancelled is True
        assert spray.status_label.text() == "Stopped."
        assert spray.analyse_btn.isEnabled() is True
        assert spray.stop_btn.isEnabled() is False

    def test_clear_resets_inputs_outputs_and_indicators(self, spray):
        spray.analyse()
        spray.worker.finished_signal.emit(REPORT)
        spray.clear()
        assert spray.target_input.text() == ""
        assert spray.stream_box.toPlainText() == ""
        assert spray.sections._raw == ""
        assert spray.severity_label.text() == "—"
        assert spray.save_btn.isEnabled() is False

    def test_nmap_needs_a_target_or_a_command(self, spray):
        # Guard before any process is spawned — the test suite must never fork
        # a real scanner.
        spray.target_input.clear()
        spray.nmap_cmd_input.clear()
        spray.run_nmap()
        assert "[Error]" in spray.nmap_output.toPlainText()
        assert spray._nmap_process is None
        assert spray.nmap_run_btn.isEnabled() is True

    def test_the_scan_is_not_a_paid_request(self, spray):
        spray.target_input.clear()
        spray.run_nmap()
        assert [c for c in spray.host.calls if c[0] == "authorize"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. Bloodhound
# ─────────────────────────────────────────────────────────────────────────────

class FakeOsintHeavyAgent:
    def __init__(self):
        self.calls = []

    def build_messages(self, target, target_type, scope, objective,
                       image_metadata, live_results=None):
        self.calls.append((target, target_type, scope, objective, image_metadata))
        return [{"role": "user", "content": target}]


DOSSIER = (
    "## 1. OVERVIEW\nwho they are\n"
    "## 2. DIGITAL FOOTPRINT\naccounts\n"
    "## 3. INFRASTRUCTURE / SOCIAL\nhosts\n"
    "## 4. RISK & RED FLAGS\nTHREAT LEVEL: 7/10  CONFIDENCE: 82%  SOURCES REFERENCED: 14\n"
    "## 5. METHODOLOGY\nhow it was found"
)


class FakeCollectionWorker(QObject):
    """Bloodhound's live-collection worker with the thread taken out."""

    progress_signal = Signal(str)
    finished_signal = Signal(list)
    error_signal = Signal(str)
    instances = []

    def __init__(self, collect, target, target_type, scope):
        super().__init__()
        self.args = (collect, target, target_type, scope)
        self.cancelled = False
        self.running = True
        FakeCollectionWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def hound(qapp, monkeypatch):
    from ui.panels.osint_heavy import OsintHeavyPanel
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(OsintHeavyPanel, "worker_class", FakeWorker)
    FakeWorker.instances.clear()

    host = FakeHost()
    host.agent_instances["osint_heavy"] = FakeOsintHeavyAgent()
    panel = OsintHeavyPanel(host)
    panel.target_input.setText("acme.com")
    return panel


class TestBloodhoundPanel:

    def test_local_file_discovery_starts_collapsed_and_read_only(self, hound):
        assert hound.file_discovery_body.isHidden() is True
        assert hound.file_results.editTriggers() == QAbstractItemView.NoEditTriggers
        assert hound.file_search_btn.text() == "Search Selected Folders"

    def test_file_filters_convert_megabytes_and_extensions(self, hound):
        hound.file_name_filter.setText("invoice")
        hound.file_name_mode.setCurrentText("Exact")
        hound.file_extension_filter.setText("PDF, docx")
        hound.file_min_size.setText("1.5")
        hound.file_max_size.setText("3")
        filters = hound._file_filters()
        assert filters.name == "invoice"
        assert filters.exact_name is True
        assert filters.extensions == (".pdf", ".docx")
        assert filters.min_size == int(1.5 * 1024 * 1024)
        assert filters.max_size == 3 * 1024 * 1024

    def test_file_results_show_path_type_size_and_modified_date(self, hound):
        from services.local_file_search import FileMatch, FileSearchReport

        report = FileSearchReport(matches=[FileMatch(
            name="evidence.pdf",
            path="/tmp/evidence.pdf",
            extension=".pdf",
            size=2048,
            modified=datetime(2026, 9, 7, 12, 30),
        )])
        hound._populate_file_results(report)
        assert hound.file_results.item(0, 0).text() == "evidence.pdf"
        assert hound.file_results.item(0, 1).text() == "/tmp/evidence.pdf"
        assert hound.file_results.item(0, 2).text() == ".pdf"
        assert hound.file_results.item(0, 3).text() == "2.0 KB"
        assert hound.file_results.item(0, 4).text() == "2026-09-07 12:30"

    def test_remote_mode_shows_ssh_fields_and_disables_local_folder_picker(self, hound):
        hound.file_discovery_body.show()
        hound.file_source_box.setCurrentText("Remote SSH machine")
        assert hound.remote_file_widget.isHidden() is False
        assert hound.file_folders.isEnabled() is False
        assert hound.add_folder_btn.isEnabled() is False

    def test_it_builds_hidden_with_section_results(self, hound):
        from PySide6.QtWidgets import QTabWidget

        assert hound.isHidden() is True
        assert hound.findChildren(QTabWidget) == []
        assert hound.sections is not None
        assert hound.save_btn.isEnabled() is False

    def test_an_empty_target_never_reaches_the_guard(self, hound):
        hound.target_input.clear()
        hound.investigate()
        assert [c for c in hound.host.calls if c[0] == "authorize"] == []

    def test_a_blocked_request_leaves_the_panel_usable(self, hound):
        hound.host.authorized = False
        hound.investigate()
        assert FakeWorker.instances == []
        assert hound.investigate_btn.isEnabled() is True
        assert hound.stop_btn.isEnabled() is False

    def test_the_whole_brief_reaches_the_agent(self, hound):
        hound.type_box.setCurrentText("Organisation")
        hound.scope_box.setCurrentText("Deep Dive")
        hound.objective_input.setPlainText("map infrastructure")
        hound.investigate()
        assert hound.host.agent_instances["osint_heavy"].calls == [
            ("acme.com", "Organisation", "Deep Dive", "map infrastructure", ""),
        ]

    def test_the_scope_is_shown_as_the_depth_indicator(self, hound):
        hound.scope_box.setCurrentText("Quick Scan")
        hound.investigate()
        assert hound.depth_label.text() == "Quick Scan"

    def test_a_finished_dossier_fills_section_cards(self, hound):
        hound.investigate()
        hound.worker.finished_signal.emit(DOSSIER)
        parsed = hound.parse_sections(DOSSIER)
        assert parsed["overview"] == "who they are"
        assert parsed["footprint"] == "accounts"
        assert parsed["methodology"] == "how it was found"
        assert hound.sections._raw == DOSSIER
        labels = " ".join(
            label.text() for label in hound.sections.findChildren(QLabel)
        )
        assert "Digital footprint" in labels
        assert "Methodology" in labels
        assert hound.save_btn.isEnabled() is True

    def test_a_finished_dossier_fills_the_indicators(self, hound):
        hound.investigate()
        hound.worker.finished_signal.emit(DOSSIER)
        assert hound.threat_label.text() == "7/10"
        assert hound.threat_bar.value() == 7
        assert hound.conf_label.text() == "82%"
        assert hound.sources_label.text() == "14"

    def test_an_error_abandons_the_request(self, hound):
        hound.investigate()
        hound.worker.error_signal.emit("provider down")
        assert ("abandon", "osint_heavy", "error") in hound.host.calls
        assert hound.status_label.text() == "Error."

    def test_stop_restores_the_controls(self, hound):
        hound.investigate()
        hound.stop()
        assert hound.investigate_btn.isEnabled() is True
        assert hound.status_label.text() == "Stopped."

    def test_clear_resets_the_brief_the_sections_and_the_image(self, hound):
        hound.investigate()
        hound.worker.finished_signal.emit(DOSSIER)
        hound.clear()
        assert hound.target_input.text() == ""
        assert hound.sections._raw == ""
        assert hound.threat_bar.value() == 0
        assert hound.image_label.text() == "No image selected"

    def test_an_attached_image_puts_its_metadata_in_the_prompt(self, hound, tmp_path):
        # No EXIF in a text file — the point is that the panel sends *something*
        # about the image rather than silently dropping it.
        fake = tmp_path / "target.jpg"
        fake.write_text("not really a jpeg")
        hound.set_image(str(fake))
        hound.investigate()
        metadata = hound.host.agent_instances["osint_heavy"].calls[-1][4]
        assert "No EXIF metadata could be extracted" in metadata

    def test_no_image_means_no_metadata_in_the_prompt(self, hound):
        hound.investigate()
        assert hound.host.agent_instances["osint_heavy"].calls[-1][4] == ""

    # ── Live collection on its own thread ────────────────────────────────
    @pytest.fixture
    def collecting(self, hound, monkeypatch):
        """Give the agent a collect_live and swap in a thread-free worker."""
        agent = hound.host.agent_instances["osint_heavy"]
        agent.collected = []

        def collect_live(target, target_type, scope, on_progress=None,
                         should_stop=None):
            agent.collected.append((target, target_type, scope))
            return []

        agent.collect_live = collect_live
        agent.last_source_count = 3
        monkeypatch.setattr(type(hound), "collection_worker_class", FakeCollectionWorker)
        FakeCollectionWorker.instances.clear()
        return hound

    def test_the_guard_runs_before_any_public_source_is_contacted(self, collecting):
        collecting.host.authorized = False
        collecting.investigate()
        assert FakeCollectionWorker.instances == []
        assert collecting.status_label.text() == "Blocked before sending."

    def test_collection_runs_off_the_ui_thread_before_the_model_call(self, collecting):
        collecting.scope_box.setCurrentText("Deep Dive")
        collecting.investigate()
        collector = FakeCollectionWorker.instances[-1]
        assert collector.args[1:] == ("acme.com", "Person", "Deep Dive")
        assert FakeWorker.instances == []           # no model call yet
        assert "WhatsMyName" in collecting.status_label.text()

        collector.progress_signal.emit("WhatsMyName: 10 of 600 sites checked")
        assert "10 of 600" in collecting.status_label.text()

        live = [{"type": "username", "sources_contacted": []}]
        collector.finished_signal.emit(live)
        assert len(FakeWorker.instances) == 1
        assert collecting._live_source_count == 3
        assert collecting.status_label.text() == "Investigating…"

    def test_a_failed_collection_still_produces_a_dossier(self, collecting):
        collecting.investigate()
        FakeCollectionWorker.instances[-1].error_signal.emit("boom")
        assert len(FakeWorker.instances) == 1

    def test_stop_during_collection_cancels_and_never_calls_the_model(self, collecting):
        collecting.investigate()
        collector = FakeCollectionWorker.instances[-1]
        collecting.stop()
        assert collector.cancelled is True
        assert ("abandon", "osint_heavy", "cancelled") in collecting.host.calls
        collector.finished_signal.emit([])          # the sweep winds down late
        assert FakeWorker.instances == []
        assert collecting.investigate_btn.isEnabled() is True

    def test_a_stale_sweep_cannot_answer_for_a_newer_investigation(self, collecting):
        collecting.investigate()
        first = FakeCollectionWorker.instances[-1]
        collecting.stop()
        collecting.investigate()
        first.finished_signal.emit([])
        assert FakeWorker.instances == []
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        assert len(FakeWorker.instances) == 1

    def test_attaching_an_image_shows_local_image_details(self, hound, tmp_path):
        fake = tmp_path / "target.jpg"
        fake.write_text("not really a jpeg")
        hound.set_image(str(fake))
        assert hound.image_label.text() == "target.jpg"
        assert hound.image_details.isHidden() is False
        assert "Image OSINT" in hound.image_details.toPlainText()
        assert "No EXIF data found" in hound.image_details.toPlainText()


class TestBloodhoundParsing:
    """Dossier sections and EXIF — no widgets, no host."""

    def test_each_numbered_heading_becomes_a_section(self):
        from ui.panels.osint_heavy import OsintHeavyPanel
        parsed = OsintHeavyPanel.parse_sections(DOSSIER)
        assert parsed["overview"] == "who they are"
        assert parsed["footprint"] == "accounts"
        assert parsed["methodology"] == "how it was found"

    def test_missing_headings_come_back_empty(self):
        from ui.panels.osint_heavy import OsintHeavyPanel
        parsed = OsintHeavyPanel.parse_sections("## 1. OVERVIEW\nonly this")
        assert parsed["overview"] == "only this"
        assert parsed["risk"] == ""

    def test_gps_south_and_west_are_negative(self):
        from ui.panels.osint_heavy import gps_to_decimal
        assert gps_to_decimal((51, 30, 0), "N") == 51.5
        assert gps_to_decimal((51, 30, 0), "S") == -51.5
        assert gps_to_decimal((0, 7, 30), "W") == -0.125

    def test_unreadable_coordinates_are_zero_rather_than_an_exception(self):
        from ui.panels.osint_heavy import gps_to_decimal
        assert gps_to_decimal(("x", "y", "z"), "N") == 0.0
        assert gps_to_decimal((), "N") == 0.0

    def test_a_file_with_no_exif_is_not_an_error(self, tmp_path):
        from ui.panels.osint_heavy import exif_summary, extract_exif
        f = tmp_path / "plain.jpg"
        f.write_text("not really a jpeg")
        assert extract_exif(str(f)) == {}
        assert exif_summary(str(f)) == "No EXIF data found in this image."

    def test_a_missing_file_is_not_an_error(self):
        from ui.panels.osint_heavy import extract_exif
        assert extract_exif("/no/such/image.jpg") == {}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Tunnel
# ─────────────────────────────────────────────────────────────────────────────

class FakeVpnAgent:
    def __init__(self):
        self.prompts = []

    def build_messages(self, prompt):
        self.prompts.append(prompt)
        return [{"role": "user", "content": prompt}]


@pytest.fixture
def tunnel(qapp, monkeypatch):
    import ui.panels.vpn as vpn_mod
    from ui.panels.vpn import VpnPanel
    from services.vpn_diagnostics import VpnProfileCatalog
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(VpnPanel, "worker_class", FakeWorker)
    monkeypatch.setattr(VpnPanel, "diagnostics_worker_class", FakeVpnDiagnosticsWorker)
    monkeypatch.setattr(VpnPanel, "connection_worker_class", FakeVpnConnectionWorker)
    FakeVpnConnectionWorker.instances.clear()
    monkeypatch.setattr(
        vpn_mod,
        "load_vpn_profile_catalog",
        lambda: VpnProfileCatalog(
            profiles=({
                "name": "Travel VPS", "endpoint": "vpn.example.test",
                "port": 51820, "interface": "wg3",
            },),
            active_profile="Travel VPS",
            source="test profiles",
        ),
    )
    FakeWorker.instances.clear()
    FakeVpnDiagnosticsWorker.instances.clear()
    # The Connect/Disconnect review reads local state; keep it off this Mac.
    from services import vpn_connection, vpn_execution
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    monkeypatch.setattr(vpn_execution, "_killswitch_armed", lambda: False)
    monkeypatch.setattr(vpn_execution, "_default_probe", lambda: {
        "wireguard": lambda name: {"up": False, "device": None},
        "openvpn_running": lambda: False,
        "route_interface": lambda: None,
    })

    host = FakeHost()
    host.agent_instances["vpn"] = FakeVpnAgent()
    panel = VpnPanel(host)
    panel.question_input.setText("hotel wifi blocks UDP")
    return panel


class TestTunnelPanel:

    def test_it_builds_hidden_with_six_result_tabs(self, tunnel):
        assert tunnel.isHidden() is True
        assert tunnel.tabs.count() == 6
        assert tunnel.tabs.tabText(0) == "Diagnostics"
        assert tunnel.tabs.tabText(3) == "Action Preview"
        assert tunnel.tabs.tabText(5) == "Execution"

    def test_connecting_a_real_profile_starts_the_connection_worker(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("VPS", {
            "name": "VPS", "protocol": "WireGuard",
            "endpoint": "203.0.113.7", "interface": "wg0"})
        tunnel.connect_vpn()
        assert len(FakeVpnConnectionWorker.instances) == 1
        assert FakeVpnConnectionWorker.instances[0].action == "connect"
        assert FakeVpnConnectionWorker.instances[0].profile["interface"] == "wg0"

    def test_the_confirmation_shows_the_review_and_defaults_to_no(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        asked = []

        def question(parent, title, text, buttons, default=None):
            asked.append((title, text, default))
            return QMessageBox.No
        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("VPS", {
            "name": "VPS", "protocol": "WireGuard",
            "endpoint": "203.0.113.7", "interface": "wg0"})
        tunnel.connect_vpn()
        assert FakeVpnConnectionWorker.instances == []
        (title, text, default), = asked
        assert "Target: wg0" in text and "wg-quick" in text and "To undo" in text
        assert default == QMessageBox.No
        assert tunnel.tabs.currentWidget() is tunnel.execution_view

    def test_a_blocked_review_refuses_without_asking(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from services import vpn_connection
        monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available",
                            lambda: False)
        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Yes))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("VPS", {
            "name": "VPS", "protocol": "WireGuard",
            "endpoint": "203.0.113.7", "interface": "wg0"})
        tunnel.connect_vpn()
        assert asked == []
        assert FakeVpnConnectionWorker.instances == []
        assert "Refused" in tunnel.connection_status_label.text()

    def test_disconnect_now_needs_confirmation(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.No))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("VPS", {
            "name": "VPS", "protocol": "WireGuard",
            "endpoint": "203.0.113.7", "interface": "wg0"})
        tunnel.disconnect_vpn()
        assert FakeVpnConnectionWorker.instances == []
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        tunnel.disconnect_vpn()
        assert [w.action for w in FakeVpnConnectionWorker.instances] == ["disconnect"]

    def test_a_gated_result_shows_its_verdict_and_record(self, tunnel):
        tunnel._on_connection_finished({
            "success": True, "protocol": "WireGuard",
            "status_line": "WireGuard connect: command completed but NOT verified — x",
            "sections": [("Result", "The command completed.", False)],
        })
        assert "NOT verified" in tunnel.connection_status_label.text()
        assert tunnel.tabs.currentWidget() is tunnel.execution_view

    def test_connecting_a_template_profile_does_nothing(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        # Even if the user clicks through, a placeholder must not start a tunnel.
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("Example — Japan", {
            "name": "Example — Japan", "protocol": "WireGuard",
            "endpoint": "<SERVER_IP>", "interface": "wgjp", "placeholder": True})
        tunnel.connect_vpn()
        assert FakeVpnConnectionWorker.instances == []

    def test_a_successful_connect_updates_status(self, tunnel):
        tunnel._on_connection_finished({"success": True, "protocol": "WireGuard", "output": "up"})
        assert "WireGuard" in tunnel.connection_status_label.text()
        assert "not verified" in tunnel.connection_status_label.text()
        assert tunnel.connect_btn.isEnabled() is True
        assert tunnel.tabs.tabText(4) == "Config Inspection"
        assert tunnel.external_checks_box.isChecked() is False
        assert tunnel.profile_box.currentText() == "Travel VPS"

    def test_arming_the_kill_switch_dispatches_an_arm_worker(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from services import vpn_connection
        monkeypatch.setattr(vpn_connection, "killswitch_supported", lambda: True)
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("VPS", {
            "name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
            "interface": "wg0"})
        tunnel.arm_kill_switch()
        assert len(FakeVpnConnectionWorker.instances) == 1
        assert FakeVpnConnectionWorker.instances[0].action == "arm"

    def test_arming_refuses_a_template_profile(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from services import vpn_connection
        monkeypatch.setattr(vpn_connection, "killswitch_supported", lambda: True)
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
        tunnel.connect_profile_box.clear()
        tunnel.connect_profile_box.addItem("Example — Japan", {
            "name": "Example — Japan", "protocol": "WireGuard",
            "endpoint": "<SERVER_IP>", "interface": "wgjp", "placeholder": True})
        tunnel.arm_kill_switch()
        assert FakeVpnConnectionWorker.instances == []

    def test_kill_switch_result_updates_its_own_status(self, tunnel):
        tunnel._on_killswitch_finished({"success": True, "output": "Kill switch ARMED"})
        assert "ARMED" in tunnel.kill_switch_status_label.text()
        assert tunnel.arm_ks_btn.isEnabled() is True

    def test_an_empty_question_never_reaches_the_guard(self, tunnel):
        tunnel.question_input.clear()
        tunnel.run()
        assert [c for c in tunnel.host.calls if c[0] == "authorize"] == []

    def test_the_deployment_setup_is_prepended_as_context(self, tunnel):
        tunnel.mode_box.setCurrentText("Native (home LAN)")
        tunnel.host_input.setText("203.0.113.9")
        tunnel.run()
        prompt = tunnel.host.agent_instances["vpn"].prompts[-1]
        assert prompt.startswith("Context —")
        assert "Native (home LAN)" in prompt
        assert "203.0.113.9" in prompt
        assert prompt.rstrip().endswith("hotel wifi blocks UDP")

    def test_the_mode_is_passed_as_the_guard_label(self, tunnel):
        tunnel.mode_box.setCurrentText("Remote (VPS)")
        tunnel.run()
        authorize = [c for c in tunnel.host.calls if c[0] == "authorize"][-1]
        assert authorize[6] == "Remote (VPS)"

    def test_an_answer_is_recorded_and_shown(self, tunnel):
        tunnel.run()
        tunnel.worker.finished_signal.emit("Use OpenVPN on 443.")
        assert ("record", "vpn", "Use OpenVPN on 443.", None) in tunnel.host.calls
        assert tunnel.advisor_box.toPlainText() == "Use OpenVPN on 443."
        assert tunnel.status_label.text() == "Done."

    def test_an_error_abandons_the_request(self, tunnel):
        tunnel.run()
        tunnel.worker.error_signal.emit("no route")
        assert ("abandon", "vpn", "error") in tunnel.host.calls
        assert "ERROR" in tunnel.advisor_box.toPlainText()

    def test_build_config_is_offline_and_costs_nothing(self, tunnel):
        tunnel.mode_box.setCurrentText("Remote (VPS)")
        tunnel.host_input.setText("203.0.113.9")
        tunnel.build_config()
        text = tunnel.config_box.toPlainText()
        assert "203.0.113.9" in text
        assert tunnel.tabs.currentWidget() is tunnel.config_box
        assert [c for c in tunnel.host.calls if c[0] == "authorize"] == []

    def test_local_connection_check_uses_no_model_or_external_permission(self, tunnel):
        tunnel.run_diagnostics()
        worker = FakeVpnDiagnosticsWorker.instances[-1]
        assert worker.started is True
        assert worker.include_external is False
        assert worker.selected_profile["name"] == "Travel VPS"
        assert [c for c in tunnel.host.calls if c[0] == "authorize"] == []

    def test_action_preview_is_offline_and_never_executes(self, tunnel):
        tunnel.action_box.setCurrentText("Disconnect")
        tunnel.preview_action()

        assert tunnel.tabs.currentWidget() is tunnel.action_view
        assert "sudo wg-quick down wg3" in tunnel.action_view._raw
        assert "nothing executed" in tunnel.status_label.text().lower()
        assert [c for c in tunnel.host.calls if c[0] == "authorize"] == []

    def test_config_inspection_discards_keys_and_uses_no_model(
        self, tunnel, monkeypatch, tmp_path
    ):
        import ui.panels.vpn as vpn_mod

        secret = "PRIVATE-MUST-NOT-BE-RENDERED"
        path = tmp_path / "travel.conf"
        path.write_text(f"""
[Interface]
PrivateKey = {secret}
DNS = 10.0.0.53
[Peer]
Endpoint = vpn.example.test:51820
AllowedIPs = 0.0.0.0/0
""", encoding="utf-8")
        monkeypatch.setattr(
            vpn_mod.QFileDialog,
            "getOpenFileName",
            staticmethod(lambda *a, **k: (str(path), "WireGuard configurations (*.conf)")),
        )

        tunnel.inspect_config()

        assert tunnel.tabs.currentWidget() is tunnel.config_inspection_view
        assert secret not in tunnel.config_inspection_view._raw
        assert "key material discarded" in tunnel.status_label.text().lower()
        assert [c for c in tunnel.host.calls if c[0] == "authorize"] == []

    def test_connection_result_is_rendered_as_sections(self, tunnel):
        from services.vpn_diagnostics import VpnDiagnosticsReport

        tunnel.run_diagnostics()
        worker = FakeVpnDiagnosticsWorker.instances[-1]
        worker.running = False
        worker.finished_signal.emit(VpnDiagnosticsReport(
            checked_at="2026-09-09 12:00:00 IST",
            tools={"wg status tool": "installed"},
            active_tunnels=["utun7"],
            tunnel_stats={
                "utun7": {
                    "peers": 1,
                    "latest_handshake": 0,
                    "received": 2048,
                    "sent": 4096,
                }
            },
            route={"interface": "utun7", "gateway": "10.0.0.1", "error": ""},
            dns_servers=["10.0.0.53"],
            openvpn_running=False,
        ))
        assert tunnel.tabs.currentWidget() is tunnel.diagnostics_view
        assert "utun7" in tunnel.diagnostics_view._raw
        assert tunnel.status_label.text() == "Connection check complete."

    def test_external_checks_need_a_second_confirmation(self, tunnel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        tunnel.external_checks_box.setChecked(True)
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
        )
        tunnel.run_diagnostics()
        assert FakeVpnDiagnosticsWorker.instances == []

    def test_stop_cancels_a_connection_check(self, tunnel):
        tunnel.run_diagnostics()
        worker = FakeVpnDiagnosticsWorker.instances[-1]
        tunnel.stop()
        assert worker.cancelled is True
        assert tunnel.status_label.text() == "Stopped."

    def test_shutdown_joins_a_connection_check(self, tunnel):
        tunnel.run_diagnostics()
        worker = FakeVpnDiagnosticsWorker.instances[-1]

        tunnel.shutdown()

        assert worker.cancelled is True
        assert worker.waited is True
        assert worker.terminated is False

    def test_stop_restores_the_controls(self, tunnel):
        tunnel.run()
        tunnel.stop()
        assert tunnel.run_btn.isEnabled() is True
        assert tunnel.status_label.text() == "Stopped."


# ─────────────────────────────────────────────────────────────────────────────
# 8. Beacon
# ─────────────────────────────────────────────────────────────────────────────

class FakeWifiAgent:
    def __init__(self):
        self.prompts = []

    def build_messages(self, prompt):
        self.prompts.append(prompt)
        return [{"role": "user", "content": prompt}]


@pytest.fixture
def beacon(qapp, monkeypatch):
    import ui.panels.wifi as wifi_mod
    from ui.panels.wifi import WifiPanel
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(wifi_mod, "SubprocessWorker", FakeScanWorker)
    monkeypatch.setattr(WifiPanel, "worker_class", FakeWorker)
    FakeWorker.instances.clear()
    FakeScanWorker.instances.clear()

    host = FakeHost()
    host.agent_instances["wifi"] = FakeWifiAgent()
    return WifiPanel(host)


class TestBeaconPanel:

    def test_it_builds_hidden_with_the_kali_form_collapsed(self, beacon):
        from PySide6.QtWidgets import QTabWidget
        assert beacon.isHidden() is True
        assert beacon.kali_group.isHidden() is True
        assert beacon.preflight_btn.text() == "Run Preflight"
        assert beacon.preflight_box.objectName() == "BeaconPreflight"
        assert beacon.findChildren(QTabWidget) == []
        assert beacon.sections is not None

    def test_switching_to_kali_reveals_the_form_and_disables_ai(self, beacon):
        beacon.mode_box.setCurrentText("Kali Command Builder")
        assert beacon.kali_group.isHidden() is False
        assert beacon.ai_checkbox.isEnabled() is False

    def test_a_scan_runs_a_subprocess_not_a_paid_request(self, beacon):
        beacon.mode_box.setCurrentText("Scan Networks")
        beacon.run()
        assert FakeScanWorker.instances  # a scan was started
        assert [c for c in beacon.host.calls if c[0] == "authorize"] == []

    def test_ping_needs_a_target(self, beacon):
        beacon.mode_box.setCurrentText("Ping Test")
        beacon.target_input.clear()
        beacon.run()
        assert FakeScanWorker.instances == []
        assert beacon.run_btn.isEnabled() is True

    def test_a_scan_feeds_the_ai_pass_when_the_box_is_ticked(self, beacon):
        beacon.mode_box.setCurrentText("Scan Networks")
        beacon.ai_checkbox.setChecked(True)
        beacon.run()
        beacon.scan_worker.finished_signal.emit("agrCtlRSSI: -55\nlink auth: wpa2-psk")
        # raw indicators from the scan, then a paid analysis of it
        assert beacon.security_label.text() == "WPA2-PSK"
        assert beacon.signal_bar.value() == 90
        assert [c for c in beacon.host.calls if c[0] == "authorize"]
        assert beacon.mode_box.currentText() in beacon.host.agent_instances["wifi"].prompts[-1]

    def test_ai_tokens_stream_then_become_section_cards(self, beacon):
        response = (
            "1. SUMMARY\nConnected on en0\n"
            "2. NETWORK FINDINGS\nOne WPA2 network\n"
            "3. SECURITY OBSERVATIONS\nNo open networks\n"
            "4. RECOMMENDATIONS\nKeep WPA2 enabled"
        )
        beacon.mode_box.setCurrentText("Scan Networks")
        beacon.run()
        beacon.scan_worker.finished_signal.emit("agrCtlRSSI: -55")
        beacon.worker.token_signal.emit("1. SUMMARY\nConnected")
        assert beacon.stream_box.toPlainText().endswith("Connected")
        assert beacon.sections.isHidden() is True
        beacon.worker.finished_signal.emit(response)
        assert beacon.stream_box.isHidden() is True
        assert beacon.sections._raw == response
        labels = " ".join(
            label.text() for label in beacon.sections.findChildren(QLabel)
        )
        assert "Network findings" in labels
        assert "Keep WPA2 enabled" in labels
        assert all(
            label.isHidden()
            for label in beacon.sections.findChildren(QLabel)
            if label.text() == "No results yet."
        )

    def test_a_scan_without_ai_just_shows_the_raw_output(self, beacon):
        beacon.mode_box.setCurrentText("Scan Networks")
        beacon.ai_checkbox.setChecked(False)
        beacon.run()
        beacon.scan_worker.finished_signal.emit("agrCtlRSSI: -80")
        assert [c for c in beacon.host.calls if c[0] == "authorize"] == []
        assert beacon.save_btn.isEnabled() is True

    def test_scan_parses_system_profiler_json_into_a_readable_report(self, beacon):
        import json
        data = {"SPAirPortDataType": [{"spairport_airport_interfaces": [{
            "_name": "en0",
            "spairport_status_information": "spairport_status_connected",
            "spairport_current_network_information": {
                "_name": "HomeNet",
                "spairport_network_channel": "6",
                "spairport_security_mode": "spairport_security_mode_wpa2_personal",
                "spairport_signal_noise": "-42 dBm / -95 dBm",
                "spairport_network_phymode": "802.11ax",
            },
            "spairport_airport_other_local_wireless_networks": [],
        }]}]}
        beacon.mode_box.setCurrentText("Scan Networks")
        beacon.ai_checkbox.setChecked(False)
        beacon.run()
        beacon.scan_worker.finished_signal.emit(json.dumps(data))
        assert "SSID: HomeNet" in beacon.sections._raw   # JSON was formatted, not shown raw
        assert beacon.signal_val_label.text() == "-42 dBm"
        assert beacon.security_label.text() == "WPA2"

    def test_the_kali_builder_is_offline(self, beacon):
        beacon.mode_box.setCurrentText("Kali Command Builder")
        beacon.ai_checkbox.setChecked(False)
        beacon.kali_bssid_input.setText("AA:BB:CC:DD:EE:FF")
        beacon.run()
        assert beacon.sections._raw != ""
        assert "Generated Kali commands" in " ".join(
            label.text() for label in beacon.sections.findChildren(QLabel)
        )
        assert [c for c in beacon.host.calls if c[0] == "authorize"] == []
        assert beacon.save_btn.isEnabled() is True

    def test_kali_mode_disables_the_ai_pass(self, beacon):
        # Switching to Kali mode disables the AI checkbox, and the builder only
        # runs the explanation when the box is *enabled* and ticked — so in Kali
        # mode no paid request fires even with the box checked. This is the
        # behaviour moved verbatim from GodAI; a redesign is TODO, not this move.
        beacon.mode_box.setCurrentText("Kali Command Builder")
        beacon.ai_checkbox.setChecked(True)
        beacon.run()
        assert beacon.ai_checkbox.isEnabled() is False
        assert [c for c in beacon.host.calls if c[0] == "authorize"] == []

    def test_stop_cancels_a_scan_that_has_no_ai_worker_yet(self, beacon):
        # A scan can be running with no ChatWorker in existence; stop must reach it.
        beacon.mode_box.setCurrentText("Scan Networks")
        beacon.ai_checkbox.setChecked(False)
        beacon.run()
        scan = beacon.scan_worker
        assert beacon.is_running() is True
        beacon.stop()
        assert scan.cancelled if hasattr(scan, "cancelled") else not scan.isRunning()
        assert beacon.status_label.text() == "Stopped."

    def test_signal_indicator_reads_rssi(self, beacon):
        beacon._update_indicators("agrCtlRSSI: -40")
        assert beacon.signal_bar.value() == 100
        assert "-40 dBm" in beacon.signal_val_label.text()

    def test_clear_resets_everything(self, beacon):
        beacon._show_sections([("Raw wireless output", "stuff", True)], "stuff")
        beacon.target_input.setText("192.168.1.1")
        beacon.clear()
        assert beacon.stream_box.toPlainText() == ""
        assert beacon.sections._raw == ""
        assert beacon.target_input.text() == ""
        assert beacon.signal_bar.value() == 0


class TestBeaconSectionParsing:
    def test_numbered_headings_become_four_sections(self):
        from ui.panels.wifi import WifiPanel

        parsed = WifiPanel.parse_analysis_sections(
            "1. SUMMARY\nsummary\n"
            "2. NETWORK FINDINGS\nnetworks\n"
            "3. SECURITY OBSERVATIONS\nsecurity\n"
            "4. RECOMMENDATIONS\nnext"
        )
        assert parsed == {
            "summary": "summary",
            "findings": "networks",
            "security": "security",
            "recommendations": "next",
        }

    def test_missing_headings_stay_empty(self):
        from ui.panels.wifi import WifiPanel

        parsed = WifiPanel.parse_analysis_sections("## SUMMARY\nOnly a summary")
        assert parsed["summary"] == "Only a summary"
        assert parsed["security"] == ""


# ── Sentry (network anomaly watch) ────────────────────────────────────────────

class FakeSentryWatchWorker(QObject):
    """A SentryWatchWorker that emits a canned summary instead of touching the
    network. The summary to emit is set on the class before each run."""

    finished_signal = Signal(dict)
    error_signal = Signal(str)
    instances = []
    next_summary = {"baseline_established": False, "findings": [],
                    "device_count": 0, "listener_count": 0, "connection_count": 0}

    def __init__(self, persist=True):
        super().__init__()
        self.persist = persist
        self.running = True
        FakeSentryWatchWorker.instances.append(self)

    def start(self):
        self.running = False
        self.finished_signal.emit(dict(FakeSentryWatchWorker.next_summary))

    def isRunning(self):
        return self.running

    def cancel(self):
        self.running = False


class FakeSentryAgent:
    def build_messages(self, prompt):
        return [{"role": "system", "content": "s"}, {"role": "user", "content": prompt}]


@pytest.fixture
def sentry_panel(qapp, monkeypatch, tmp_path):
    import ui.panels.sentry as sentry_module
    from agents.sentry.sentry import baseline as baseline_module
    from ui.panels.sentry import SentryPanel

    # No network worker, no launchctl, no writes outside tmp.
    monkeypatch.setattr(sentry_module, "SentryWatchWorker", FakeSentryWatchWorker)
    monkeypatch.setattr(SentryPanel, "worker_class", FakeWorker)
    monkeypatch.setattr(sentry_module.watchd, "status",
                        lambda: {"installed": False, "loaded": False, "interval": None})
    monkeypatch.setattr(baseline_module, "default_state_dir", lambda: tmp_path)
    FakeSentryWatchWorker.instances.clear()
    FakeWorker.instances.clear()

    host = FakeHost()
    host.agent_instances["sentry"] = FakeSentryAgent()
    return SentryPanel(host)


class TestSentryPanel:

    def test_it_builds_hidden_and_read_only_by_default(self, sentry_panel):
        assert sentry_panel.isHidden() is True
        assert sentry_panel.stop_btn.isEnabled() is False

    def test_a_watch_pass_with_findings_feeds_the_ai_when_ticked(self, sentry_panel):
        FakeSentryWatchWorker.next_summary = {
            "baseline_established": False,
            "findings": [{"severity": "alert", "kind": "gateway_mac_change",
                          "title": "Gateway hardware changed", "detail": "d", "evidence": {}}],
            "device_count": 4, "listener_count": 10, "connection_count": 12,
            "taken_at": "2026-09-28T00:00:00+00:00",
        }
        sentry_panel.ai_checkbox.setChecked(True)
        sentry_panel.run_pass(persist=True)
        # persisted pass, findings rendered, and the paid read authorised
        assert FakeSentryWatchWorker.instances[-1].persist is True
        assert "ALERT" in sentry_panel.findings_box.toPlainText()
        assert [c for c in sentry_panel.host.calls if c[0] == "authorize"]

    def test_a_finding_read_is_recorded(self, sentry_panel):
        FakeSentryWatchWorker.next_summary = {
            "baseline_established": False,
            "findings": [{"severity": "notice", "kind": "new_device",
                          "title": "New device 10.0.0.5", "detail": "", "evidence": {}}],
            "device_count": 1, "listener_count": 0, "connection_count": 0,
        }
        sentry_panel.run_pass(persist=True)
        sentry_panel.worker.finished_signal.emit("Looks benign.")
        assert [c for c in sentry_panel.host.calls if c[0] == "record"]
        assert sentry_panel.status_label.text() == "Analysis complete."

    def test_no_findings_never_spends(self, sentry_panel):
        FakeSentryWatchWorker.next_summary = {
            "baseline_established": False, "findings": [],
            "device_count": 3, "listener_count": 5, "connection_count": 7,
        }
        sentry_panel.run_pass(persist=True)
        assert [c for c in sentry_panel.host.calls if c[0] == "authorize"] == []
        assert sentry_panel.status_label.text() == "No new anomalies."

    def test_first_pass_baseline_never_spends(self, sentry_panel):
        FakeSentryWatchWorker.next_summary = {
            "baseline_established": True, "findings": [],
            "device_count": 3, "listener_count": 5, "connection_count": 7,
        }
        sentry_panel.run_pass(persist=True)
        assert [c for c in sentry_panel.host.calls if c[0] == "authorize"] == []
        assert "Baseline recorded" in sentry_panel.status_label.text()

    def test_dry_run_uses_a_non_persisting_worker(self, sentry_panel):
        FakeSentryWatchWorker.next_summary = {
            "baseline_established": False, "findings": [],
            "device_count": 0, "listener_count": 0, "connection_count": 0,
        }
        sentry_panel.run_pass(persist=False)
        assert FakeSentryWatchWorker.instances[-1].persist is False

    def test_a_blocked_request_leaves_the_panel_usable(self, sentry_panel):
        FakeSentryWatchWorker.next_summary = {
            "baseline_established": False,
            "findings": [{"severity": "warning", "kind": "new_listener",
                          "title": "New listener", "detail": "", "evidence": {}}],
            "device_count": 0, "listener_count": 1, "connection_count": 0,
        }
        sentry_panel.host.authorized = False
        sentry_panel.run_pass(persist=True)
        assert FakeWorker.instances == []
        assert sentry_panel.run_btn.isEnabled() is True
        assert sentry_panel.stop_btn.isEnabled() is False

    def test_install_and_remove_background_call_launchd(self, sentry_panel, monkeypatch):
        import ui.panels.sentry as sentry_module
        calls = []
        monkeypatch.setattr(sentry_module.watchd, "install",
                            lambda interval: calls.append(("install", interval)) or {"message": "ok"})
        monkeypatch.setattr(sentry_module.watchd, "remove",
                            lambda: calls.append(("remove",)) or {"message": "gone"})
        sentry_panel.interval_box.setValue(5)
        sentry_panel.install_background()
        sentry_panel.remove_background()
        assert calls == [("install", 300), ("remove",)]
