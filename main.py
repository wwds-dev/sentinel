import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from services.runtime_paths import (
    RuntimeDataError, ensure_seeded, is_frozen, is_portable,
    resource_base, user_data_base,
)
try:
    ensure_seeded()
except RuntimeDataError as exc:
    message = f"Sentinel cannot use its data storage.\n\n{exc}"
    print(message, file=sys.stderr)
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e", 'display alert "Sentinel cannot start" message '
             + json.dumps(message) + " as critical"],
            check=False,
        )
    raise SystemExit(2) from exc

from dotenv import load_dotenv
# API keys: user-data .env when frozen, project .env in dev. Real env vars still win.
load_dotenv(user_data_base() / ".env")

import markdown

from PySide6.QtCore import Qt, QTimer, QProcess, QUrl, QThread, Signal, QEvent, QRect, QPoint, QSize, QSettings
from PySide6.QtGui import (
    QTextCursor, QDesktopServices, QColor, QFont, QTextDocument,
    QKeySequence, QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QSizePolicy, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QTextEdit, QPushButton, QComboBox, QListWidget, QListWidgetItem,
    QMessageBox, QCheckBox, QTextBrowser, QSplitter, QLineEdit, QFileDialog,
    QProgressBar, QDialog, QTabWidget, QFrame, QScrollArea, QStackedWidget, QLayout,
    QInputDialog, QMenu, QToolTip,
)

from services.ollama_client import OllamaClient, MUSE_GLIMMER_VARIANTS, muse_glimmer_default
from services.openai_client import OpenAIClientWrapper, is_image_model
from services.deepseek_client import DeepSeekClientWrapper
from services.kimi_client import KimiClientWrapper
from services.gemini_client import GeminiClientWrapper
from services.anthropic_client import AnthropicClientWrapper
from services.qwen_client import QwenClientWrapper
from services.resource_monitor import ResourceMonitor
from services.history_store import HistoryStore
from services.report_exporter import ReportExporter
from services.usage_tracker import UsageTracker
from services.database import init_db, get_setting, save_setting, get_connection
from services.registry import Registry
from services.validator import Validator
from services.run_logger import RunLogger

from agents.chat_agent import ChatAgent
from agents.osint_agent import OSINTAgent
from agents.bug_spray.sentinel_chat_agent import BugBountyAgent
from agents.wifi_agent import WiFiAgent
from agents.sentry.sentinel_chat_agent import SentryAgent
from agents.osint_heavy_agent import OsintHeavyAgent
from agents.vpn_agent.sentinel_chat_agent import VpnAgent
from services.agent_factory import AgentFactory
from services.agent_catalog import BUILTIN_AGENTS, BUILTIN_AGENT_ORDER
from services.provider_catalog import CLOUD_PROVIDERS
from services.tool_catalog import runtime_tool_prompts
from services.app_version import DISPLAY_VERSION, build_description
from services.model_recommendations import (
    AGENT_RECOMMENDATIONS as RECOMMENDATION_OBJECTS,
    TASK_RECOMMENDATIONS, as_dict, find_model, resolve_available_model,
    RoutingPreferences, route_request, pricing_metadata,
)


# Writable base = project root in dev, ~/Library/Application Support/Sentinel when frozen.
BASE_DIR = user_data_base()
# Read-only bundled resources (README, config defaults) = project root in dev, bundle when frozen.
RESOURCE_DIR = resource_base()
CONFIG_DIR = BASE_DIR / "config"


def load_budget_setting(settings: dict, key: str, default: float) -> float:
    """Load the persisted SQLite budget, falling back to the original JSON default."""
    fallback = settings.get(key, default)
    try:
        return float(get_setting(key, str(fallback)))
    except (TypeError, ValueError):
        return float(fallback)
DATA_DIR = BASE_DIR / "data"
CHATS_DIR = DATA_DIR / "chats"

# Sentinel value for the Saved Chats agent filter — not a real agent name.
ALL_AGENTS_FILTER = "All agents"
ALL_PROJECTS_FILTER = "All projects"
UNFILED_PROJECT_FILTER = "Unfiled"
NO_ACTIVE_PROJECT = "No project"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
COMMANDS_FILE = CONFIG_DIR / "commands.json"
TOOL_PROMPTS_FILE = CONFIG_DIR / "tool_prompts.json"
README_FILE = RESOURCE_DIR / "README.md"

# ── Per-agent recommended setup ──────────────────────────────────────────────
# Single source of truth for "which provider + model is right for THIS agent".
# Each panel pre-selects its entry on startup, and the recommended provider and
# model wear a BEST FIT badge in their dropdowns so the user can always see what
# the recommendation was, even after switching to something else mid-session.
#
# `provider` must match an item in that panel's provider box. `model` is matched
# leniently (exact -> prefix -> substring) so a dated API id such as
# "claude-sonnet-4-6-20260112" still resolves from "claude-sonnet-4-6".

AGENT_RECOMMENDATIONS = {
    key: as_dict(value) for key, value in RECOMMENDATION_OBJECTS.items()
}

# agent key -> (provider box attribute, model box attribute)
AGENT_SETUP_WIDGETS = {
    "chat":        ("provider_box",             "model_box"),
    "osint":       None,  # OsintPanel owns its boxes (phase 4)
    "osint_heavy": None,  # OsintHeavyPanel owns its boxes (phase 4)
    "wifi":        None,  # WifiPanel owns its boxes (phase 4)
    "sentry":      None,  # SentryPanel owns its boxes
    "bug_bounty":  None,  # BugBountyPanel owns its boxes (phase 4)
    "manager":     None,  # ManagerPanel owns its boxes (phase 4)
    "vpn":         None,  # VpnPanel owns its boxes (phase 4)
}

from ui import app_identity, appkit_guard, tray
from ui.workers import (
    ChatWorker, SubprocessWorker, ModelPullWorker,
)
from ui.widgets import (
    BEST_FIT_BADGE, KeyValue, MenuComboBox, Meter, RECOMMENDED_ROLE,
    RECOMMENDATION_BADGE_ROLE, RECOMMENDATION_REASON_ROLE, SectionView,
    ThemeDots,
)
from ui import vibe
from ui.style import global_stylesheet, polish_combo_box, polish_combo_boxes
from ui.theme import accent, recolour
from ui.tooltips import seed_tooltips
from ui.panels.base import PROVIDERS, build_provider_row, configure_model_controls
from ui.panels.bug_bounty import BugBountyPanel
from ui.panels.manager import ManagerPanel
from ui.panels.osint_heavy import OsintHeavyPanel
from ui.panels.vpn import VpnPanel
from ui.panels.wifi import WifiPanel
from ui.panels.sentry import SentryPanel
from ui.panels.osint import OsintPanel

class ChatInput(QTextEdit):
    """Multiline composer with the familiar Enter/Shift+Enter contract."""

    sendRequested = Signal()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and not (event.modifiers() & Qt.ShiftModifier)):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class GodAI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(f"Sentinel {DISPLAY_VERSION}")
        self.resize(1400, 900)
        # The run bar is a single row so the cost can sit right-aligned as the
        # design has it; a wrapping bar cannot right-align. That costs width, so
        # the minimum is set where the three panes still fit rather than letting
        # the splitter crush them.
        self.setMinimumSize(1200, 700)
        self.showMaximized()

        CONFIG_DIR.mkdir(exist_ok=True)
        DATA_DIR.mkdir(exist_ok=True)
        CHATS_DIR.mkdir(parents=True, exist_ok=True)

        init_db()

        self.commands = self.load_json(COMMANDS_FILE, {"General Chat": ""})
        with get_connection() as conn:
            registry_tools = [dict(row) for row in conn.execute(
                "SELECT * FROM tools ORDER BY name"
            ).fetchall()]
        self.tool_prompts = runtime_tool_prompts(
            self.load_json(TOOL_PROMPTS_FILE, {}), registry_tools
        )
        self.settings = self.load_json(SETTINGS_FILE, {})
        # (widget, css) for the inline sheets that predate ui/style.py.
        # apply_global_style repaints them with everything else.
        self._inline_sheets: list[tuple[QWidget, str]] = []

        self.ollama = OllamaClient()
        self.openai = OpenAIClientWrapper()
        self.deepseek = DeepSeekClientWrapper()
        self.kimi = KimiClientWrapper()
        self.gemini = GeminiClientWrapper()
        self.anthropic = AnthropicClientWrapper()
        self.qwen = QwenClientWrapper()
        self.monitor = ResourceMonitor()
        self.history = HistoryStore()
        self.report_exporter = ReportExporter()
        self.usage_tracker = UsageTracker()

        self.registry = Registry()
        self.validator = Validator(self.registry)
        self.active_project_id: str | None = None
        self.run_logger = RunLogger()
        # request id -> context for an in-flight request (see authorize_request)
        self._pending_requests = {}
        # agent key -> the panel's own "reload the model list" callable, filled
        # in as each panel builds (see register_model_loader)
        self._model_loaders = {}
        # agent key -> panel, for the verticals that have moved to ui/panels/.
        # Everything that has to find a panel's widgets goes through this rather
        # than through `GodAI` attributes (see setup_widgets_for).
        self.panels: dict = {}

        self.agent_factory = AgentFactory(BASE_DIR)

        self.agent_instances = {
            "chat": ChatAgent(),
            "osint": OSINTAgent(),
            "bug_bounty": BugBountyAgent(),
            "wifi": WiFiAgent(),
            "sentry": SentryAgent(),
            "osint_heavy": OsintHeavyAgent(),
            "vpn": VpnAgent(),
        }

        self.current_messages = []

        self.session_cost_total = 0.0
        self.session_request_count = 0
        self.last_request_cost = 0.0
        
        self.session_budget_eur = load_budget_setting(
            self.settings, "session_budget_eur", 1.00
        )
        self.daily_budget_eur = load_budget_setting(
            self.settings, "daily_budget_eur", 5.00
        )

        self.chat_worker: Optional[ChatWorker] = None
        self.active_run_id: Optional[str] = None
        self.chat_started_at: Optional[float] = None
        self.chat_elapsed_seconds = 0
        self.chat_estimated_seconds = 30

        self.pending_agent = ""
        self.pending_backend = ""
        self.pending_model = ""
        self.pending_command = ""
        self.pending_prompt = ""
        self.pending_messages = []
        self.pending_project: str | None = None

        # Tooltip state — toggled via the chip in the centre header bar
        self.tooltips_enabled = str(
            get_setting("tooltips_enabled", "true")
        ).lower() not in ("0", "false", "off", "no")
        self.inspector_visible = str(
            get_setting("inspector_visible", "true")
        ).lower() not in ("0", "false", "off", "no")

        self.build_ui()
        self.refresh_project_controls()
        self._install_workspace_shortcuts()
        self._polish_tab_widgets()
        self._seed_tooltips()
        # Install global event filter so we can suppress ToolTip events when disabled
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.instance().installEventFilter(self)
        self.load_models()
        # Pre-select each agent's recommended provider/model and paint those
        # entries red in their dropdowns. Runs after every panel is built.
        self.install_agent_recommendations()
        self.muse_pull_worker: Optional[ModelPullWorker] = None
        self.refresh_muse_button()
        self.load_history_list()
        self.load_saved_searches()
        self.update_resource_label()
        self.update_usage_labels()
        self.start_resource_timer()
        self.select_agent("chat")
        polish_combo_boxes(self)

    def _polish_tab_widgets(self):
        """Disable text elision and enable scroll buttons on every QTabWidget
        in the app so long tab titles never get cut off with ellipses."""
        for tabs in self.findChildren(QTabWidget):
            tabs.setElideMode(Qt.ElideNone)
            tabs.setUsesScrollButtons(True)
            tabs.setDocumentMode(False)
            # Let the tab bar expand and request its preferred (full) text size
            tab_bar = tabs.tabBar()
            if tab_bar is not None:
                tab_bar.setExpanding(False)
                tab_bar.setUsesScrollButtons(True)

    def _install_workspace_shortcuts(self) -> None:
        """Keep the same essential shortcuts in every agent workspace."""
        self.run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.run_shortcut.setContext(Qt.ApplicationShortcut)
        self.run_shortcut.activated.connect(self.run_current_action)

        self.stop_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.stop_shortcut.setContext(Qt.ApplicationShortcut)
        self.stop_shortcut.activated.connect(self.stop_current_task)

        self.help_shortcut = QShortcut(QKeySequence(Qt.Key_F1), self)
        self.help_shortcut.setContext(Qt.ApplicationShortcut)
        self.help_shortcut.activated.connect(self.show_agent_docs)

    def run_current_action(self) -> None:
        """Run the visible primary action without special-casing each agent."""
        agent_name = getattr(self, "_current_agent", "chat")
        if agent_name == "chat":
            if not self.send_btn.isHidden() and self.send_btn.isEnabled():
                self.send_btn.click()
            return

        panel = self.panels.get(agent_name)
        if panel is None:
            return
        for button in panel.findChildren(QPushButton):
            if (button.objectName() == "PrimaryAction"
                    and button.isVisible() and button.isEnabled()):
                button.click()
                return

    # ── Tooltips ────────────────────────────────────────────────────────────
    def _toggle_tooltips(self):
        """Enable or disable hover tooltips application-wide."""
        self.tooltips_enabled = self.tooltips_toggle_btn.isChecked()
        self.tooltips_toggle_btn.setText(
            "Tips on" if self.tooltips_enabled else "Tips off"
        )
        save_setting("tooltips_enabled", "true" if self.tooltips_enabled else "false")
        if not self.tooltips_enabled:
            QToolTip.hideText()

    def eventFilter(self, obj, event):
        """Swallow QEvent.ToolTip when tooltips are toggled off."""
        if event.type() == QEvent.ToolTip and not self.tooltips_enabled:
            return True
        return super().eventFilter(obj, event)

    def _toggle_saved_chats(self, expanded: bool) -> None:
        if not hasattr(self, "saved_chats_panel"):
            return
        active = getattr(self, "_current_agent", "chat") == "chat"
        self.saved_chats_panel.setVisible(active and bool(expanded))
        self.saved_chats_toggle.setText(
            "▾  History" if expanded else "▸  History"
        )

    def _toggle_saved_searches(self, expanded: bool) -> None:
        if not hasattr(self, "saved_searches_panel"):
            return
        active = getattr(self, "_current_agent", "chat") == "osint"
        self.saved_searches_panel.setVisible(active and bool(expanded))
        self.saved_searches_toggle.setText(
            "▾  Saved searches" if expanded else "▸  Saved searches"
        )

    def resizeEvent(self, event):
        """Adapt secondary Chat metadata without hiding core controls."""
        super().resizeEvent(event)
        if hasattr(self, "runbar_cost"):
            self.runbar_cost.setVisible(True)
        if hasattr(self, "agent_subtitle_label"):
            self.agent_subtitle_label.setVisible(self.width() >= 1150)
        if hasattr(self, "right_panel") and hasattr(self, "inspector_btn"):
            # The three-column shell is part of the requested interaction
            # model, including at the app's 1200 px minimum width. The user may
            # still collapse it explicitly with the Inspector chip.
            self.right_panel.setVisible(self.inspector_visible)
            self.inspector_btn.setChecked(self.inspector_visible)
        self._update_workspace_margins()

    def _update_workspace_margins(self):
        """Keep Chat readable on ultrawide windows without reducing compact space."""
        if not hasattr(self, "center_layout") or not hasattr(self, "center_widget"):
            return
        inset = 18
        if getattr(self, "_current_agent", "chat") == "chat":
            inset = max(inset, (self.center_widget.width() - 1240) // 2)
        self.center_layout.setContentsMargins(inset, 16, inset, 16)

    def _set_tooltips(self, mapping: dict):
        """Helper: apply a {widget_attr_name: text} mapping in one call.

        A dotted name — "osint.provider_box" — addresses a widget inside a panel
        that has moved to `ui/panels/`; a bare one is still an attribute of the
        window. Both are skipped silently when absent, which is why a moved
        panel has to bring its tooltip names with it: nothing would report the
        loss.
        """
        for attr, text in mapping.items():
            if "." in attr:
                agent_key, _, widget_name = attr.partition(".")
                panel = self.panels.get(agent_key)
                widget = getattr(panel, widget_name, None) if panel else None
            else:
                widget = getattr(self, attr, None)
            if widget is not None:
                widget.setToolTip(text)

    def _seed_tooltips(self):
        seed_tooltips(self)

    def load_json(self, path: Path, default):
        if not path.exists():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=2)
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def safe_key_status(self, cls):
        try:
            return "✅ available" if cls.key_available() else "❌ not set"
        except Exception:
            return "unknown"

    def estimate_chat_seconds(self, backend: str, model: str, prompt: str) -> int:
        words = max(1, len(prompt.split()))
        base = 10
        if backend == "ollama":
            if "8b" in model.lower():
                base = 35
            elif "1.5b" in model.lower():
                base = 12
            else:
                base = 25
        elif backend in CLOUD_PROVIDERS:
            base = 15
        return min(180, max(10, base + words // 20))

    def estimate_chat_cost(self, backend, model, prompt):
        """Pre-flight cost estimate in EUR, plus the approximate token count.

        Prices come from the pricing table via UsageTracker — the same source
        that bills the request afterwards. This used to be a second hardcoded
        per-backend dict which had no entry for anthropic or qwen, so both were
        estimated at zero and sailed straight past the budget gate in
        Validator.validate() no matter how expensive the model was.
        """
        approx_input_tokens = max(1, int(len(prompt) / 4))
        approx_output_tokens = max(250, int(approx_input_tokens * 1.2))
        approx_total_tokens = approx_input_tokens + approx_output_tokens

        if backend == "ollama":
            return 0.0, approx_total_tokens

        estimated_cost = self.usage_tracker.calculate_cost_eur(
            backend, model, approx_input_tokens, approx_output_tokens
        )

        return round(estimated_cost, 5), approx_total_tokens

    def get_current_cost_estimate(self):
        raw_text = self.input_box.toPlainText().strip()

        if not raw_text:
            return 0.0, 0, None, None

        _, full_prompt = self.build_user_prompt(raw_text)
        backend, model = self.resolve_backend_model()

        estimated_cost, approx_tokens = self.estimate_chat_cost(
            backend,
            model,
            full_prompt
        )

        return estimated_cost, approx_tokens, backend, model
    
    def show_cost_history(self):
        from ui.dialogs import show_cost_history as _show_cost_history
        return _show_cost_history(self)
    def show_run_log(self):
        from ui.dialogs import show_run_log as _show_run_log
        return _show_run_log(self)
    def show_settings(self):
        from ui.dialogs import show_settings as _show_settings
        return _show_settings(self)
    def update_live_cost_estimate(self):
        if not hasattr(self, "live_estimate_label"):
            return


        estimated_cost, approx_tokens, backend, model = self.get_current_cost_estimate()

        if hasattr(self, "runbar_cost"):
            tokens = f"{approx_tokens/1000:.1f}k" if approx_tokens >= 1000 else str(approx_tokens)
            if backend == "ollama":
                self.runbar_cost.setText(f"free · {tokens} tok")
            else:
                self.runbar_cost.setText(f"~€{estimated_cost:.2f} · {tokens} tok")

        if hasattr(self, "cost_rows"):
            tokens = f"{approx_tokens/1000:.1f}k" if approx_tokens >= 1000 else str(approx_tokens)
            if backend == "ollama":
                estimate = f"free · {tokens} tok"
            elif backend:
                estimate = f"~€{estimated_cost:.2f} · {tokens} tok"
            else:
                estimate = "€0.00 · 0 tok"
            self.cost_rows["estimate"].set(estimate)

        if backend == "ollama":
            self.live_estimate_label.setText("")
        elif backend in CLOUD_PROVIDERS:
            self.live_estimate_label.setText(
                f"⚠ Paid API"
            )
        else:
            self.live_estimate_label.setText("")

    def show_cost_estimate_popup(self):
        estimated_cost, approx_tokens, backend, model = self.get_current_cost_estimate()

        if backend == "ollama":
            msg = (
                f"Agent: {self.agent_box.currentText()}\n"
                f"Backend: {backend}\n"
                f"Model: {model}\n"
                f"Approx tokens: {approx_tokens}\n\n"
                f"Estimated cost: €0.0000\n"
                f"This is local execution."
            )
        else:
            msg = (
                f"Agent: {self.agent_box.currentText()}\n"
                f"Backend: {backend}\n"
                f"Model: {model}\n"
                f"Approx tokens: {approx_tokens}\n\n"
                f"Estimated cost: ~€{estimated_cost:.2f}\n"
                f"⚠ This may use a paid API."
            )

        QMessageBox.information(self, "Cost Estimate", msg)

    def format_seconds(self, total: int) -> str:
        total = max(0, int(total))
        return f"{total // 60:02d}:{total % 60:02d}"

    # ── Local-model memory guard ─────────────────────────────────────────────
    # Cloud models cost money and are gated by budget. Local models are free, so
    # they bypassed every check — yet they are the only ones that can wedge the
    # machine. This sizes the model against real memory before it is loaded.

    # A loaded model needs roughly its file size resident, plus KV cache and
    # runtime overhead. 1.15 is a deliberately mild allowance: the aim is to
    # catch "this will thrash", not to model llama.cpp allocation exactly.
    MEMORY_OVERHEAD_FACTOR = 1.15
    # Leave room for the OS and Sentinel itself rather than letting a model take
    # every last byte of physical RAM.
    MEMORY_HEADROOM_GB = 3.0

    def assess_local_model(self, model: str) -> dict | None:
        """Weigh a local model against this machine's memory.

        Returns None when the check does not apply (not a known local model, or
        the daemon is unreachable and the size is unknowable). Otherwise a dict
        with level "ok" | "tight" | "too_big", the numbers behind it, and a
        human-readable message.
        """
        import psutil

        size_bytes = self.ollama.model_size_bytes(model)
        if size_bytes is None:
            # Not pulled yet — fall back to the published download size for the
            # builds we ship a figure for, so the dropdown can grey them out.
            published_gb = MUSE_GLIMMER_VARIANTS.get(model)
            if published_gb is None:
                return None
            size_gb = float(published_gb)
        else:
            size_gb = size_bytes / 1e9

        needed_gb = size_gb * self.MEMORY_OVERHEAD_FACTOR
        vm = psutil.virtual_memory()
        total_gb = vm.total / 1e9
        available_gb = vm.available / 1e9

        if needed_gb > total_gb - self.MEMORY_HEADROOM_GB:
            level = "too_big"
            message = (
                f"{model} needs about {needed_gb:.1f} GB of memory, but this machine "
                f"only has {total_gb:.0f} GB in total. It cannot run here without "
                "swapping so hard the system becomes unresponsive."
            )
        elif needed_gb > available_gb:
            level = "tight"
            message = (
                f"{model} needs about {needed_gb:.1f} GB, but only {available_gb:.1f} GB "
                f"is free right now (of {total_gb:.0f} GB). It will fit, but expect "
                "heavy swapping and slow replies — closing other apps will help."
            )
        else:
            level = "ok"
            message = f"{model} needs ~{needed_gb:.1f} GB; {available_gb:.1f} GB free."

        return {
            "level": level,
            "model": model,
            "needed_gb": needed_gb,
            "available_gb": available_gb,
            "total_gb": total_gb,
            "message": message,
        }

    def check_memory_before_request(self, backend: str, model: str) -> bool:
        """Interactive pre-flight for local models. False means "do not run".

        Must be called from the GUI thread, before the worker is constructed.
        """
        if backend != "ollama":
            return True

        verdict = self.assess_local_model(model)
        if verdict is None or verdict["level"] == "ok":
            return True

        if verdict["level"] == "too_big":
            QMessageBox.critical(self, "Model Too Large For This Machine", verdict["message"])
            return False

        choice = QMessageBox.warning(
            self,
            "Low Memory",
            verdict["message"] + "\n\nRun it anyway?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return choice == QMessageBox.Yes

    def check_budget_before_request(self, estimated_cost: float, backend: str) -> bool:
        if backend == "ollama":
            return True

        today_total = self.usage_tracker.get_today_total()

        session_remaining = self.session_budget_eur - self.session_cost_total
        daily_remaining = self.daily_budget_eur - today_total

        if estimated_cost > session_remaining:
            QMessageBox.warning(
                self,
                "Session Budget Exceeded",
                f"This request is estimated at €{estimated_cost:.2f}, "
                f"but your remaining session budget is only €{session_remaining:.2f}."
            )
            return False

        if estimated_cost > daily_remaining:
            QMessageBox.warning(
                self,
                "Daily Budget Exceeded",
                f"This request is estimated at €{estimated_cost:.2f}, "
                f"but your remaining daily budget is only €{daily_remaining:.2f}."
            )
            return False

        return True

    def save_budget_limits(self):
        try:
            self.session_budget_eur = float(self.session_budget_input.text().strip())
            self.daily_budget_eur = float(self.daily_budget_input.text().strip())

            save_setting("session_budget_eur", str(self.session_budget_eur))
            save_setting("daily_budget_eur", str(self.daily_budget_eur))

            self.update_usage_labels()
            QMessageBox.information(self, "Budget Saved", "Budget limits saved.")

        except ValueError:
            QMessageBox.warning(self, "Invalid Budget", "Please enter valid numbers.")

    def reset_session_spend(self):
        self.session_cost_total = 0.0
        self.session_request_count = 0
        self.update_usage_labels()
        QMessageBox.information(self, "Session Reset", "Session spend has been reset.")

    def get_recommended_setup(self):
        agent = self.agent_box.currentText() if hasattr(self, "agent_box") else "chat"
        tool = self.tool_box.currentText() if hasattr(self, "tool_box") else "General Chat"
        command = self.command_box.currentText() if hasattr(self, "command_box") else "General Chat"
        prompt = self.input_box.toPlainText().strip() if hasattr(self, "input_box") else ""
        tool_config = self.tool_prompts.get(tool, {})
        task_key = {"General Chat": "general", "Writing": "writing",
                    "Rewrite": "writing", "Coding": "coding",
                    "Summarize": "summarize"}.get(tool)
        policy = TASK_RECOMMENDATIONS.get(task_key) if task_key else None
        tool_provider = policy.provider if policy else tool_config.get("recommended_provider")
        tool_model = policy.model if policy else tool_config.get("recommended_model")
        
        if tool_provider:
            available = self.models_for_provider(tool_provider)
            model = resolve_available_model(tool_model, available) if tool_model else (
                available[0] if available else self.model_box.currentText())

            # ===== CHECK API PERMISSION =====
            if tool_provider == "openai" and not self.allow_openai_checkbox.isChecked():
                return {
                    "mode": "Local only",
                    "provider": "ollama",
                    "model": self.model_box.currentText(),
                    "reason": f"{tool} recommends OpenAI, but API is disabled. Using local model."
                }

            if tool_provider == "deepseek" and not self.allow_deepseek_checkbox.isChecked():
                return {
                    "mode": "Local only",
                    "provider": "ollama",
                    "model": self.model_box.currentText(),
                    "reason": f"{tool} recommends DeepSeek, but API is disabled. Using local model."
                }

            if tool_provider == "kimi" and not self.allow_kimi_checkbox.isChecked():
                return {
                    "mode": "Local only",
                    "provider": "ollama",
                    "model": self.model_box.currentText(),
                    "reason": f"{tool} recommends Kimi, but API is disabled. Using local model."
                }

            if tool_provider == "gemini" and not self.allow_gemini_checkbox.isChecked():
                return {
                    "mode": "Local only",
                    "provider": "ollama",
                    "model": self.model_box.currentText(),
                    "reason": f"{tool} recommends Gemini, but API is disabled. Using local model."
                }

            if tool_provider == "anthropic" and not self.allow_anthropic_checkbox.isChecked():
                return {
                    "mode": "Local only",
                    "provider": "ollama",
                    "model": self.model_box.currentText(),
                    "reason": f"{tool} recommends Anthropic, but API is disabled. Using local model."
                }

            # ===== VALID CASE =====
            mode = "Local only" if tool_provider == "ollama" else "Hybrid allowed"

            return {
                "mode": mode,
                "provider": tool_provider,
                "model": model,
                "reason": policy.reason if policy else f"{tool} recommends {tool_provider}."
            }

        text = f"{agent} {tool} {command} {prompt}".lower()



        if any(k in text for k in ["debug", "error", "traceback", "python", "code", "refactor", "function", "class"]):
            if self.allow_anthropic_checkbox.isChecked():
                return {"mode": "Hybrid allowed", "provider": "anthropic", "model": "claude-sonnet-4-6", "reason": "Coding/debugging task; Claude Sonnet is excellent for code analysis and generation."}
            if self.allow_kimi_checkbox.isChecked():
                return {"mode": "Hybrid allowed", "provider": "kimi", "model": "kimi-k2.7-code", "reason": "Coding/debugging task; Kimi K2.7 Code is purpose-built for coding and long-context tool use."}
            if self.allow_deepseek_checkbox.isChecked():
                return as_dict(TASK_RECOMMENDATIONS["coding"])
            if self.allow_openai_checkbox.isChecked():
                return as_dict(TASK_RECOMMENDATIONS["writing"])
            return {"mode": "Local only", "provider": "ollama", "model": self.model_box.currentText(), "reason": "Coding task detected, but APIs are not enabled. Using local model."}

        if any(k in text for k in ["write", "rewrite", "email", "cv", "cover letter", "professional", "polish"]):
            if self.allow_anthropic_checkbox.isChecked():
                return {"mode": "Hybrid allowed", "provider": "anthropic", "model": "claude-sonnet-4-6", "reason": "Writing task; Claude is highly recommended for polished professional text."}
            if self.allow_openai_checkbox.isChecked():
                return as_dict(TASK_RECOMMENDATIONS["writing"])
            if self.allow_gemini_checkbox.isChecked():
                return {"mode": "Hybrid allowed", "provider": "gemini", "model": "gemini-2.5-flash", "reason": "Gemini is a good writing fallback."}
            return {"mode": "Local only", "provider": "ollama", "model": self.model_box.currentText(), "reason": "Writing task detected, but APIs are not enabled. Using local model."}

        if any(k in text for k in ["osint", "investigate", "research", "summarize sources", "analysis", "report"]):
            if self.allow_kimi_checkbox.isChecked():
                return as_dict(TASK_RECOMMENDATIONS["research"])
            if self.allow_deepseek_checkbox.isChecked():
                return {"mode": "Hybrid allowed", "provider": "deepseek", "model": "deepseek-flash", "reason": "DeepSeek is efficient for structured analysis."}
            if self.allow_gemini_checkbox.isChecked():
                return as_dict(TASK_RECOMMENDATIONS["summarize"])
            return {"mode": "Local only", "provider": "ollama", "model": self.model_box.currentText(), "reason": "Analysis task detected, but APIs are not enabled. Using local model."}

        return {
            "mode": "Local only",
            "provider": "ollama",
            "model": self.model_box.currentText(),
            "reason": "General/simple task. Local Ollama is free and private."
        }

    def get_recommended_setup(self):
        """Use the same capability router as request execution and agent panels."""
        agent = self.agent_box.currentText() if hasattr(self, "agent_box") else "chat"
        tool = self.tool_box.currentText() if hasattr(self, "tool_box") else "General Chat"
        prompt = self.input_box.toPlainText().strip() if hasattr(self, "input_box") else ""
        try:
            return self.route_for_request(prompt, agent=agent, tool=tool, keep_manual=False).as_dict()
        except RuntimeError as exc:
            provider = self.provider_box.currentText() if hasattr(self, "provider_box") else "ollama"
            model = self.model_box.currentText() if hasattr(self, "model_box") else ""
            pricing = pricing_metadata(provider, model)
            return {"mode": "Local only" if provider == "ollama" else "Hybrid allowed",
                    "provider": provider, "model": model, "reason": str(exc),
                    "fallbacks": [], "pricing": vars(pricing),
                    "cost_label": pricing.compact}

    def apply_recommended_setup(self):
        rec = self.get_recommended_setup()

        if hasattr(self, "execution_mode_box"):
            index = self.execution_mode_box.findText(rec["mode"])
            if index >= 0:
                self.execution_mode_box.setCurrentIndex(index)

        if hasattr(self, "provider_box"):
            index = self.provider_box.findText(rec["provider"])
            if index >= 0:
                self.provider_box.setCurrentIndex(index)

        self.load_provider_models()

        if hasattr(self, "model_box") and rec["model"] != "tts":
            index = self.model_box.findText(rec["model"])
            if index >= 0:
                self.model_box.setCurrentIndex(index)

        if hasattr(self, "recommendation_label"):
            self.recommendation_label.setText(
                f"Recommendation:\n"
                f"{rec['provider']} · {rec['model']}\n"
                f"{rec['reason']}"
            )

        self.update_live_cost_estimate()

    def update_recommendation_label(self):
        if not hasattr(self, "recommendation_label"):
            return

        rec = self.get_recommended_setup()

        self.recommendation_label.setText(f"{rec['provider']} · {rec['model']}")
        if hasattr(self, "routing_rows"):
            self.routing_rows["Suggested"].set(rec["provider"], rec["reason"])
            self.routing_rows["Model"].set(rec["model"], rec["reason"])
            self.routing_rows["Mode"].set(rec.get("mode", "—"), rec["reason"])
            # RouteDecision.as_dict() deliberately serialises nested metadata,
            # so its pricing value is a plain dict here.  cost_label is the
            # display-safe string produced from that same metadata.
            cost_label = rec.get("cost_label")
            if not cost_label:
                cost_label = pricing_metadata(
                    rec["provider"], rec["model"]
                ).compact
            self.routing_rows["Cost"].set(cost_label, rec["reason"])
        # Chat's recommendation moves with the tool/command/prompt, so repaint
        # the red dropdown markings whenever the label is refreshed.
        self.refresh_recommendation_marks("chat")

    def maybe_auto_apply_recommendation(self):
        if not hasattr(self, "auto_recommend_checkbox"):
            return

        if not self.auto_recommend_checkbox.isChecked():
            return

        self.apply_recommended_setup()

    # ── Muse Glimmer (local, via Ollama) ─────────────────────────────────────

    def _set_chat_status(self, text: str) -> None:
        """Write to the chat status line, revealing it if it is still hidden."""
        label = getattr(self, "chat_status_label", None)
        if label is None:
            return
        label.setText(text)
        label.setVisible(bool(text))

    @staticmethod
    def _total_ram_gb() -> int:
        import psutil
        return round(psutil.virtual_memory().total / 1e9)

    def _muse_choice(self) -> tuple[str, int]:
        """The Muse Glimmer build best suited to this machine: (tag, size_gb)."""
        return muse_glimmer_default(self._total_ram_gb())

    def mark_oversized_models(self, combo) -> None:
        """Grey out local models this machine cannot physically run.

        Advisory only — the entry stays selectable, and run_backend() is the
        real gate. Colouring here just makes the limit visible before clicking.
        """
        if combo is None:
            return

        for i in range(combo.count()):
            # No longer has to skip the recommended entry: that marking is a
            # role of its own now, so grey-for-too-big and BEST FIT can land on
            # the same model without either one erasing the other.
            verdict = self.assess_local_model(combo.itemText(i))
            if verdict is None:
                continue
            if verdict["level"] == "too_big":
                combo.setItemData(i, QColor("#666666"), Qt.ForegroundRole)
                combo.setItemData(i, f"⚠ {verdict['message']}", Qt.ToolTipRole)
            elif verdict["level"] == "tight":
                combo.setItemData(i, f"⚠ {verdict['message']}", Qt.ToolTipRole)

    def refresh_muse_button(self) -> None:
        """Show the pull button only while Muse Glimmer is not installed."""
        btn = getattr(self, "get_muse_btn", None)
        if btn is None:
            return

        # Installed at all? Any of the published builds counts.
        installed = any(
            self.ollama.is_model_installed(tag) for tag in MUSE_GLIMMER_VARIANTS
        )
        tag, size_gb = self._muse_choice()

        btn.setVisible(not installed)
        btn.setToolTip(
            f"Download Meta's Muse Glimmer ({tag}) into Ollama — ~{size_gb} GB. "
            "A 30B open-weights agentic model tuned for tool use, long tasks and "
            "failure recovery. Runs locally, so it is free and nothing leaves "
            "this machine."
        )
        menu_action = getattr(self, "get_muse_menu_action", None)
        if menu_action is not None:
            menu_action.setVisible(not installed)
            menu_action.setToolTip(btn.toolTip())

    def pull_muse_glimmer(self) -> None:
        if getattr(self, "muse_pull_worker", None) is not None and self.muse_pull_worker.isRunning():
            QMessageBox.information(self, "Already Downloading", "Muse Glimmer is already downloading.")
            return

        tag, size_gb = self._muse_choice()
        total_ram_gb = self._total_ram_gb()

        ram_note = ""
        if total_ram_gb and total_ram_gb < size_gb + 8:
            ram_note = (
                f"\n\n⚠ This machine has {total_ram_gb} GB of memory and the model needs "
                f"about {size_gb} GB resident. Expect heavy swapping and slow responses — "
                "Meta targets a 24–32 GB envelope. Close other apps before running it."
            )

        confirm = QMessageBox.question(
            self,
            "Download Muse Glimmer?",
            f"This downloads {tag} (~{size_gb} GB) into Ollama.\n\n"
            "Meta's 30B open-weights agentic model (Apache 2.0), tuned for tool use, "
            "long-running tasks and failure recovery. It runs locally — free, and no "
            f"data leaves this machine.{ram_note}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.get_muse_btn.setEnabled(False)
        self.muse_pull_worker = ModelPullWorker(self.ollama, tag)
        self.muse_pull_worker.progress_signal.connect(self._on_muse_pull_progress)
        self.muse_pull_worker.finished_signal.connect(self._on_muse_pull_finished)
        self.muse_pull_worker.error_signal.connect(self._on_muse_pull_error)
        self.muse_pull_worker.start()

    def _on_muse_pull_progress(self, status: str, done: int, total: int) -> None:
        if total > 0:
            pct = int(done / total * 100)
            self._set_chat_status(
                f"Muse Glimmer: {status} — {done / 1e9:.1f} / {total / 1e9:.1f} GB ({pct}%)"
            )
            self.tool_progress.setValue(pct)
        else:
            self._set_chat_status(f"Muse Glimmer: {status}")

    def _on_muse_pull_finished(self, model: str) -> None:
        self.tool_progress.setValue(100)
        self._set_chat_status(f"Muse Glimmer installed ({model}).")
        self.get_muse_btn.setEnabled(True)
        self.refresh_muse_button()
        # Bring it into the dropdown straight away if Ollama is the live provider.
        if self.provider_box.currentText() == "ollama":
            self.load_provider_models()
        QMessageBox.information(
            self,
            "Muse Glimmer Ready",
            f"{model} is installed.\n\nSelect provider 'ollama' and pick it from the "
            "Model list. It runs locally at no cost.",
        )

    def _on_muse_pull_error(self, message: str) -> None:
        self.tool_progress.setValue(0)
        self._set_chat_status("Muse Glimmer download failed.")
        self.get_muse_btn.setEnabled(True)
        QMessageBox.warning(self, "Download Failed", message)

    def models_for_provider(self, provider: str, context: str = "",
                            widget=None) -> list[str]:
        """Model ids offered by one provider, or [] for an unknown provider.

        Every client falls back to its own KNOWN_MODELS list when the API is
        unreachable, so this only returns empty for a name we don't handle.

        A `context` turns a failure from silent into recorded: the panels that
        loaded their own models used to note it on the model box as a tooltip,
        and that survives the move here.
        """
        clients = {
            "ollama": self.ollama,
            "openai": self.openai,
            "deepseek": self.deepseek,
            "kimi": self.kimi,
            "gemini": self.gemini,
            "anthropic": self.anthropic,
            "qwen": self.qwen,
        }
        client = clients.get(provider)
        if client is None:
            return []
        try:
            return list(client.list_models())
        except Exception as exc:
            if context:
                self._note_failure(f"{context}: load models", exc, widget)
            return []

    # ── The provider/model pair every agent panel owns ───────────────────────
    # One implementation, six panels. Each used to inline the same seven-branch
    # provider chain; two of them swallowed load failures silently and one
    # reported them, which is the kind of drift that duplication guarantees.

    def load_models_into(self, provider_box, model_box, context: str,
                         empty_placeholder: bool = False) -> None:
        """Fill `model_box` with the models of the provider next to it."""
        provider = provider_box.currentText()
        model_box.clear()
        models = self.models_for_provider(provider, context, model_box)
        if models:
            model_box.addItems(models)
        elif empty_placeholder:
            model_box.addItem(
                "(no local models)" if provider == "ollama" else "(unavailable)"
            )
        polish_combo_box(provider_box)
        polish_combo_box(model_box)
        self._mark_paid_route_choices(provider_box, model_box)

    def setup_widgets_for(self, agent_key: str):
        """One agent's provider and model boxes, wherever they now live.

        A panel that has moved to `ui/panels/` owns its own combos; one that has
        not is still a pile of `GodAI` attributes named in `AGENT_SETUP_WIDGETS`.
        Everything that marks or pre-selects a recommendation asks here, so the
        two can coexist for as long as phase 4 takes.
        """
        panel = self.panels.get(agent_key)
        if panel is not None:
            return panel.provider_box, panel.model_box

        names = AGENT_SETUP_WIDGETS.get(agent_key)
        if not names:
            return None, None
        return getattr(self, names[0], None), getattr(self, names[1], None)

    def _mark_paid_route_choices(self, provider_box, model_box) -> None:
        """Mark cloud-backed choices yellow; Ollama remains local and neutral."""
        if provider_box is None or model_box is None:
            return
        for index in range(provider_box.count()):
            paid = provider_box.itemText(index).strip().lower() in CLOUD_PROVIDERS
            provider_box.setItemData(index, paid, MenuComboBox.COST_ROLE)

        selected_paid = provider_box.currentText().strip().lower() in CLOUD_PROVIDERS
        for index in range(model_box.count()):
            model_box.setItemData(index, selected_paid, MenuComboBox.COST_ROLE)

        for combo in (provider_box, model_box):
            combo.setProperty("paidSelection", selected_paid)
            combo.style().unpolish(combo)
            combo.style().polish(combo)
            combo.update()

    def register_model_loader(self, agent_key: str, loader) -> None:
        """Record how to repopulate one agent's model box.

        Panels register while they build. Replaces a map of method *names* that
        had to be kept in step with the methods by hand.
        """
        self._model_loaders[agent_key] = loader

    def load_models_for(self, agent_key: str) -> None:
        """Repopulate one agent's model box, whichever panel owns it."""
        loader = self._model_loaders.get(agent_key)
        if loader is None:
            return
        try:
            loader()
        except Exception as exc:
            self._note_failure(f"{agent_key}: reload models", exc)

    # ── Per-agent recommended setup ──────────────────────────────────────────
    # Every agent panel gets its recommendation from AGENT_RECOMMENDATIONS
    # pre-selected on startup, and the recommended provider/model entries are
    # painted red inside their dropdowns. The red entry survives the user
    # switching to something else, so the original recommendation stays visible
    # for the whole session.

    @staticmethod
    def _find_model_index(combo, wanted: str) -> int:
        """Locate `wanted` in a model combo; see `find_model` for the rule."""
        return find_model([combo.itemText(i) for i in range(combo.count())], wanted)

    def _paint_recommended_item(self, combo, index: int, tooltip: str,
                                *, badge: str = BEST_FIT_BADGE) -> None:
        """Mark one dropdown entry as the best fit, clearing any previous one.

        The marking is data, not a colour: `RECOMMENDED_ROLE` says *which*
        entry it is and `SelectorMenu` decides what that looks like — an accent
        BEST FIT pill, the same badge Imprint paints. It used to be a red
        ForegroundRole, which no reader downstream could tell apart from the
        grey `mark_oversized_models` paints on a model this machine cannot run,
        and which the amber "costs money" dot covered up entirely on every
        cloud provider.

        Only the item's marking and tooltip change — never its text — because
        the panels read `currentText()` straight back as the provider/model
        name.
        """
        if combo is None:
            return

        default_font = combo.font()
        for i in range(combo.count()):
            # Only the entry that *was* recommended gets its tooltip cleared;
            # mark_oversized_models owns the tooltip on the rest.
            if combo.itemData(i, RECOMMENDED_ROLE):
                combo.setItemData(i, "", Qt.ToolTipRole)
            combo.setItemData(i, False, RECOMMENDED_ROLE)
            combo.setItemData(i, None, RECOMMENDATION_REASON_ROLE)
            combo.setItemData(i, None, RECOMMENDATION_BADGE_ROLE)
            combo.setItemData(i, default_font, Qt.FontRole)

        if index < 0:
            return

        marked_font = QFont(default_font)
        marked_font.setBold(True)
        combo.setItemData(index, True, RECOMMENDED_ROLE)
        combo.setItemData(index, tooltip, RECOMMENDATION_REASON_ROLE)
        combo.setItemData(index, badge, RECOMMENDATION_BADGE_ROLE)
        combo.setItemData(index, marked_font, Qt.FontRole)

    def _recommendation_for(self, agent_key: str) -> dict | None:
        """Return {provider, model, reason} for an agent.

        Chat is the one agent whose recommendation is not fixed — it already has
        a live recommender that reacts to the selected tool, command and prompt
        text — so defer to that and let the red marking follow it around.
        """
        if agent_key == "chat":
            try:
                rec = self.get_recommended_setup()
            except Exception:
                return None
            return rec if rec.get("model") != "tts" else None

        return AGENT_RECOMMENDATIONS.get(agent_key)

    def refresh_recommendation_marks(self, agent_key: str) -> None:
        """Re-apply the BEST FIT marking for one agent's provider and model.

        Called after any model-list reload, since clearing a combo also drops
        the per-item role data.

        The closed control carries the same sentence as a tooltip. The badge is
        only legible once the list is open, and "am I on the best fit?" is a
        question worth being able to answer without opening anything.
        """
        rec = self._recommendation_for(agent_key)
        if not rec:
            return

        provider_box, model_box = self.setup_widgets_for(agent_key)
        pretty = BUILTIN_AGENTS.get(agent_key, {}).get("label", agent_key)
        tooltip = (
            f"Best fit for {pretty}: {rec['provider']} · {rec['model']}\n"
            f"{rec['reason']}"
        )

        if provider_box is not None:
            idx = provider_box.findText(rec["provider"])
            self._paint_recommended_item(provider_box, idx, tooltip)
            provider_box.setToolTip(
                f"Selected provider: {provider_box.currentText()}.\n{tooltip}"
            )

        if model_box is not None:
            idx = self._find_model_index(model_box, rec["model"])
            self._paint_recommended_item(model_box, idx, tooltip)
            selected_provider = (
                provider_box.currentText() if provider_box is not None else ""
            )
            model_box.setToolTip(
                f"Selected model: {selected_provider} · "
                f"{model_box.currentText()}.\n{tooltip}"
            )
            self.mark_oversized_models(model_box)

    def _on_recommended_provider_changed(self, agent_key: str) -> None:
        """React to the user switching provider on an agent panel.

        Whenever the provider lands back on the recommended one, snap the model
        box to the recommended model too — otherwise the panel's loader leaves
        it on whatever happens to be first in the list. A deliberate model change
        afterwards is left alone.
        """
        rec = self._recommendation_for(agent_key)
        if rec:
            provider_box, model_box = self.setup_widgets_for(agent_key)
            if (provider_box is not None and model_box is not None
                    and provider_box.currentText() == rec["provider"]):
                idx = self._find_model_index(model_box, rec["model"])
                if idx >= 0:
                    model_box.setCurrentIndex(idx)

        self.refresh_recommendation_marks(agent_key)

    def apply_agent_recommendation(self, agent_key: str) -> None:
        """Pre-select this agent's recommended provider + model, then mark them."""
        if agent_key == "chat":
            # Chat has its own apply path that also updates the recommendation
            # panel and the live cost estimate.
            self.apply_recommended_setup()
            self.refresh_recommendation_marks("chat")
            return

        rec = AGENT_RECOMMENDATIONS.get(agent_key)
        if not rec:
            return

        provider_box, model_box = self.setup_widgets_for(agent_key)

        if provider_box is not None:
            idx = provider_box.findText(rec["provider"])
            if idx >= 0:
                provider_box.setCurrentIndex(idx)

        # Populate the model list for the provider we just selected. Setting the
        # provider fires currentTextChanged -> the panel's loader, but only when
        # the value actually changed, so call the loader directly to cover the
        # case where the recommended provider was already selected.
        self.load_models_for(agent_key)

        if model_box is not None:
            idx = self._find_model_index(model_box, rec["model"])
            if idx >= 0:
                model_box.setCurrentIndex(idx)

        self.refresh_recommendation_marks(agent_key)

    def install_agent_recommendations(self) -> None:
        """Apply every agent's recommended setup once, at startup, and keep the
        red markings in sync as the user changes providers or models later."""

        for agent_key in AGENT_SETUP_WIDGETS:
            provider_box, model_box = self.setup_widgets_for(agent_key)

            try:
                self.apply_agent_recommendation(agent_key)
            except Exception as e:
                print(f"[Recommendations] {agent_key}: {e}")

            # Re-mark after the panel's own loader has repopulated the model box.
            # Connected last, so it runs after the loader already wired up above.
            if provider_box is not None:
                provider_box.currentTextChanged.connect(
                    lambda _t, k=agent_key: self._on_recommended_provider_changed(k)
                )
            if model_box is not None:
                model_box.currentTextChanged.connect(
                    lambda _t, k=agent_key: self.refresh_recommendation_marks(k)
                )

    def build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #262d29; }")
        self.main_splitter = splitter

        left_widget = self.build_left_panel()
        center_widget = self.build_center_panel()
        right_widget = self.build_right_panel()
        self.left_panel = left_widget
        self.right_panel = right_widget
        self.update_recommendation_label()

        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([245, 910, 245])
        right_widget.setVisible(self.inspector_visible)

        outer_layout.addWidget(splitter)
        self.apply_global_style()

    def build_left_panel(self) -> QWidget:
        left_widget = QWidget()
        left_widget.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 12, 0, 10)
        left_layout.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(16, 6, 16, 12)
        brand_row.setSpacing(7)

        brand = QLabel("SENTINEL")
        brand.setStyleSheet(
            "color: #5d6862; font-family: Menlo, Monaco, monospace; "
            "font-size: 10px; font-weight: 500; letter-spacing: 1.8px; "
            "padding: 0;"
        )
        self.version_label = QLabel(DISPLAY_VERSION)
        self.version_label.setObjectName("AppVersion")
        self.version_label.setStyleSheet(
            "color: #7f8b85; font-family: Menlo, Monaco, monospace; "
            "font-size: 9px; font-weight: 500; padding: 1px 0 0 0;"
        )
        version_details = build_description()
        brand.setToolTip(version_details)
        self.version_label.setToolTip(version_details)
        brand_row.addWidget(brand)
        brand_row.addWidget(self.version_label)
        brand_row.addStretch(1)
        # The themes, as themselves. A picker also lives in Settings, which is
        # where someone goes to *read* what the choice means; this is for
        # changing your mind about it mid-session.
        self.theme_dots = ThemeDots(on_change=self.apply_global_style)
        brand_row.addWidget(self.theme_dots, 0, Qt.AlignVCenter)
        left_layout.addLayout(brand_row)

        # Inner scrollable container holds all the agent categories so they never
        # get clipped or vertically squashed when the window is short.
        agents_header = QLabel("AGENTS")
        agents_header.setObjectName("RailHeading")
        left_layout.addWidget(agents_header)

        agents_scroll = QScrollArea()
        agents_scroll.setWidgetResizable(True)
        agents_scroll.setFrameShape(QFrame.NoFrame)
        agents_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        agents_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        agents_container = QWidget()
        agents_container.setStyleSheet("background: transparent;")
        agents_layout = QVBoxLayout(agents_container)
        agents_layout.setContentsMargins(0, 0, 0, 0)
        agents_layout.setSpacing(2)

        # Every section starts collapsed — launch shows just the category list,
        # and you open the one you want.
        # A flat list, in the order the work usually runs. The accordion this
        # replaced was built when there were fifteen agents; at six it was a
        # click between you and everything, and four headings for six rows.
        sidebar_agents = BUILTIN_AGENT_ORDER

        # Minimal sidebar row — clear separation via padding + hover fill
        agent_btn_style = """
            QPushButton#AgentBtn {
                text-align: left;
                padding: 9px 12px 9px 18px;
                background-color: transparent;
                border: none;
                border-left: 2px solid transparent;
                border-radius: 0;
                color: #a8b3ad;
                font-size: 13px;
                font-weight: normal;
            }
            QPushButton#AgentBtn:hover {
                background-color: #151816;
                color: #e8ece9;
            }
            QPushButton#AgentBtn:checked {
                background-color: rgba(60, 255, 136, 0.07);
                border-left: 2px solid #3cff88;
                color: #3cff88;
                font-weight: 500;
            }
        """

        self.agent_buttons = {}
        for name in sidebar_agents:
            metadata = BUILTIN_AGENTS[name]
            btn = QPushButton(f"{metadata['icon']}  {metadata['label']}")
            btn.setObjectName("AgentBtn")
            self.themed_sheet(btn, agent_btn_style)
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(lambda checked, n=name: self.select_agent(n))
            agents_layout.addWidget(btn)
            self.agent_buttons[name] = btn

        agents_layout.addStretch()
        agents_scroll.setWidget(agents_container)
        left_layout.addWidget(agents_scroll, 1)

        # Saved chats are part of the Chat workflow, not every specialist
        # workflow. The reference shell keeps them behind one quiet footer row
        # rather than permanently filling half of the navigation rail.
        self.saved_chats_toggle = QPushButton("▸  History")
        self.saved_chats_toggle.setObjectName("RailFooterToggle")
        self.saved_chats_toggle.setCheckable(True)
        self.saved_chats_toggle.setToolTip("Show saved conversations")
        self.saved_chats_toggle.clicked.connect(self._toggle_saved_chats)
        left_layout.addWidget(self.saved_chats_toggle)

        self.saved_chats_panel = QWidget()
        saved_layout = QVBoxLayout(self.saved_chats_panel)
        saved_layout.setContentsMargins(0, 0, 0, 0)
        saved_layout.setSpacing(4)

        # ── Divider ──────────────────────────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #242424; background-color: #242424; max-height: 1px;")
        saved_layout.addWidget(divider)

        saved_header = QLabel("  SAVED CHATS")
        saved_header.setStyleSheet(
            "color: #707070; font-weight: bold; font-size: 10px; "
            "letter-spacing: 1.5px; padding: 8px 0 4px 8px; "
            "background: transparent;"
        )
        saved_layout.addWidget(saved_header)

        project_filter_row = QHBoxLayout()
        project_filter_row.setSpacing(4)
        self.history_project_filter = MenuComboBox()
        self.history_project_filter.addItem(
            ALL_PROJECTS_FILTER, ALL_PROJECTS_FILTER
        )
        self.history_project_filter.currentIndexChanged.connect(
            self.load_history_list
        )
        self.history_project_filter.setToolTip(
            "Show chats from every project, one project, or only unfiled chats."
        )
        project_filter_row.addWidget(self.history_project_filter, 1)

        self.new_project_btn = QPushButton("+")
        self.new_project_btn.setObjectName("ChipBtn")
        self.new_project_btn.setFixedWidth(34)
        self.new_project_btn.setToolTip("Create a chat project")
        self.new_project_btn.clicked.connect(self.create_chat_project)
        project_filter_row.addWidget(self.new_project_btn)
        saved_layout.addLayout(project_filter_row)

        # Narrow the list to one agent. Populated from the chats that exist, so
        # it only ever offers agents you have actually used.
        self.history_agent_filter = MenuComboBox()
        self.history_agent_filter.addItem(ALL_AGENTS_FILTER)
        self.history_agent_filter.currentTextChanged.connect(self.load_history_list)
        saved_layout.addWidget(self.history_agent_filter)

        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search saved chats...")
        self.history_search.textChanged.connect(self.load_history_list)
        saved_layout.addWidget(self.history_search)

        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self.open_selected_chat)
        self.history_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(
            self.show_history_context_menu
        )
        # Double-click renames: chat_title_from_data already prefers a stored
        # "title" over the truncated first prompt, it was just never written.
        self.history_list.itemDoubleClicked.connect(self.rename_selected_chat)
        # Keep the saved-chats list bounded so the agents area always has room
        self.history_list.setMinimumHeight(70)
        self.history_list.setMaximumHeight(100)
        saved_layout.addWidget(self.history_list)

        self.delete_chat_btn = QPushButton("Delete selected")
        self.delete_chat_btn.clicked.connect(self.delete_selected_chat)
        saved_layout.addWidget(self.delete_chat_btn)

        self.new_chat_btn = QPushButton("New chat")
        self.new_chat_btn.clicked.connect(self.new_chat)
        saved_layout.addWidget(self.new_chat_btn)
        left_layout.addWidget(self.saved_chats_panel)
        self.saved_chats_panel.hide()

        # Trace uses the same durable history records as Chat, presented through
        # a dedicated search-focused library whenever the Trace view is active.
        self.saved_searches_toggle = QPushButton("▸  Saved searches")
        self.saved_searches_toggle.setObjectName("RailFooterToggle")
        self.saved_searches_toggle.setCheckable(True)
        self.saved_searches_toggle.setToolTip("Show saved Trace searches")
        self.saved_searches_toggle.clicked.connect(self._toggle_saved_searches)
        left_layout.addWidget(self.saved_searches_toggle)

        self.saved_searches_panel = QWidget()
        searches_layout = QVBoxLayout(self.saved_searches_panel)
        searches_layout.setContentsMargins(0, 0, 0, 0)
        searches_layout.setSpacing(4)

        searches_divider = QFrame()
        searches_divider.setFrameShape(QFrame.HLine)
        searches_divider.setStyleSheet(
            "color: #242424; background-color: #242424; max-height: 1px;"
        )
        searches_layout.addWidget(searches_divider)

        searches_header = QLabel("  SAVED SEARCHES")
        searches_header.setStyleSheet(
            "color: #707070; font-weight: bold; font-size: 10px; "
            "letter-spacing: 1.5px; padding: 8px 0 4px 8px; "
            "background: transparent;"
        )
        searches_layout.addWidget(searches_header)

        self.saved_search_search = QLineEdit()
        self.saved_search_search.setPlaceholderText("Search saved searches...")
        self.saved_search_search.textChanged.connect(self.load_saved_searches)
        searches_layout.addWidget(self.saved_search_search)

        self.saved_search_list = QListWidget()
        self.saved_search_list.itemClicked.connect(self.open_selected_search)
        self.saved_search_list.itemDoubleClicked.connect(self.rename_selected_search)
        self.saved_search_list.setMinimumHeight(70)
        self.saved_search_list.setMaximumHeight(130)
        searches_layout.addWidget(self.saved_search_list)

        self.delete_search_btn = QPushButton("Delete selected")
        self.delete_search_btn.clicked.connect(self.delete_selected_search)
        searches_layout.addWidget(self.delete_search_btn)

        self.new_search_btn = QPushButton("New search")
        self.new_search_btn.clicked.connect(self.new_trace_search)
        searches_layout.addWidget(self.new_search_btn)
        left_layout.addWidget(self.saved_searches_panel)
        self.saved_searches_panel.hide()

        # Global setup belongs with global navigation. API connections and
        # application actions are populated after the right-side live inspector
        # is built, but their stable home is this left-rail container.
        self.left_utility_container = QWidget()
        self.left_utility_container.setObjectName("LeftUtilities")
        self.left_utility_layout = QVBoxLayout(self.left_utility_container)
        self.left_utility_layout.setContentsMargins(12, 4, 12, 0)
        self.left_utility_layout.setSpacing(4)
        left_layout.addWidget(self.left_utility_container)

        left_widget.setMinimumWidth(220)
        left_widget.setMaximumWidth(270)

        self.themed_sheet(left_widget, """
        QWidget#LeftPanel {
            background-color: #0f0f0f;
        }
        QWidget#LeftPanel QLineEdit {
            font-size: 12px;
            color: #ffffff;
            background-color: #161616;
            border: 1px solid #242424;
            border-radius: 8px;
            padding: 6px 10px;
        }
        QWidget#LeftPanel QLineEdit:focus {
            border: 1px solid #3cff88;
        }
        QWidget#LeftPanel QListWidget {
            background-color: #161616;
            border: 1px solid #242424;
            border-radius: 8px;
            font-size: 12px;
            color: #c8c8c8;
            padding: 4px;
        }
        QWidget#LeftPanel QListWidget::item {
            padding: 5px 8px;
            border-radius: 4px;
        }
        QWidget#LeftPanel QListWidget::item:hover {
            background-color: #1f1f1f;
        }
        QWidget#LeftPanel QListWidget::item:selected {
            background-color: rgba(60, 255, 136, 0.10);
            color: #3cff88;
        }
        QWidget#LeftPanel > QPushButton {
            font-size: 12px;
            font-weight: 600;
            color: #d0d0d0;
            background-color: #161616;
            border: 1px solid #242424;
            border-radius: 8px;
            padding: 8px 12px;
            margin-top: 6px;
        }
        QWidget#LeftPanel > QPushButton:hover {
            background-color: #1f1f1f;
            border: 1px solid #3cff88;
            color: #ffffff;
        }
        """)

        return left_widget

    def build_center_panel(self) -> QWidget:
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        self.center_widget = center_widget
        self.center_layout = center_layout
        center_layout.setContentsMargins(18, 16, 18, 16)
        center_layout.setSpacing(12)

        # ── Agent header bar: big accent title + status pill ─────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        self.agent_title_label = QLabel("Chat")
        self.agent_title_label.setObjectName("AgentTitle")
        header_row.addWidget(self.agent_title_label)

        self.agent_subtitle_label = QLabel("")
        self.agent_subtitle_label.setObjectName("AgentSubtitle")
        self.agent_subtitle_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        header_row.addWidget(self.agent_subtitle_label, 0, Qt.AlignBottom)

        header_row.addStretch()

        self.agent_docs_btn = QPushButton("Agent guide")
        self.agent_docs_btn.setObjectName("ChipBtn")
        self.agent_docs_btn.setToolTip("Open searchable documentation for the current agent.")
        self.agent_docs_btn.clicked.connect(self.show_agent_docs)
        header_row.addWidget(self.agent_docs_btn)

        self.tooltips_toggle_btn = QPushButton(
            "Tips on" if self.tooltips_enabled else "Tips off"
        )
        self.tooltips_toggle_btn.setObjectName("ChipBtn")
        self.tooltips_toggle_btn.setCheckable(True)
        self.tooltips_toggle_btn.setChecked(self.tooltips_enabled)
        self.tooltips_toggle_btn.setToolTip(
            "Toggle hover tooltips across the entire app. Tooltips explain what each control does."
        )
        self.tooltips_toggle_btn.clicked.connect(self._toggle_tooltips)
        header_row.addWidget(self.tooltips_toggle_btn)

        self.inspector_btn = QPushButton("Inspector")
        self.inspector_btn.setObjectName("ChipBtn")
        self.inspector_btn.setCheckable(True)
        self.inspector_btn.setChecked(self.inspector_visible)
        self.inspector_btn.setToolTip(
            "Show system, routing, cost, budget, logs and API status."
        )
        self.inspector_btn.clicked.connect(self.toggle_inspector)
        header_row.addWidget(self.inspector_btn)

        # Keep infrequent application utilities available without permanently
        # occupying the Inspector. This matters on specialist screens, where
        # the Chat run-bar Options menu is not present.
        self.header_more_btn = QPushButton("•••")
        self.header_more_btn.setObjectName("ChipBtn")
        self.header_more_btn.setToolTip("More application tools")
        header_more_menu = QMenu(self.header_more_btn)
        header_more_menu.addAction("Learning centre").triggered.connect(
            self.show_learning_center
        )
        header_more_menu.addSeparator()
        header_more_menu.addAction("App documentation").triggered.connect(
            self.show_docs
        )
        header_more_menu.addAction("Model guide").triggered.connect(
            self.show_model_guide
        )
        header_more_menu.addSeparator()
        header_more_menu.addAction("Cost history").triggered.connect(
            self.show_cost_history
        )
        header_more_menu.addAction("Run log").triggered.connect(
            self.show_run_log
        )
        header_more_menu.addSeparator()
        header_more_menu.addAction("Settings").triggered.connect(
            self.show_settings
        )
        self.header_more_btn.setMenu(header_more_menu)
        header_row.addWidget(self.header_more_btn)

        center_layout.addLayout(header_row)


        self.normal_panel = QWidget()
        normal_layout = QVBoxLayout(self.normal_panel)
        normal_layout.setContentsMargins(0, 0, 0, 0)
        normal_layout.setSpacing(10)

        # Row 1: command only
        # ── Run bar ───────────────────────────────────────────────────────────
        # One row instead of three. Everything here changes per request; the
        # things that change weekly — execution mode, the provider permissions,
        # the model tools — moved behind the gear. They were occupying the strip
        # directly above the input box permanently.
        self.agent_box = MenuComboBox()
        # Built-ins come exclusively from the canonical catalog.
        agent_items = list(BUILTIN_AGENT_ORDER)
        self.agent_box.addItems(agent_items)
        self.agent_box.hide()

        runbar_container = QWidget()
        runbar_container.setObjectName("RunBar")
        # Without a fixed vertical policy this surface absorbs all spare height
        # when the response area is empty and centres its controls in a huge
        # blank card.
        runbar_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # A plain QWidget does not paint a stylesheet background unless asked;
        # without this the run bar has no surface and its controls float loose.
        runbar_container.setAttribute(Qt.WA_StyledBackground, True)
        runbar = QHBoxLayout(runbar_container)
        runbar.setContentsMargins(10, 8, 10, 8)
        runbar.setSpacing(8)

        self.active_project_box = MenuComboBox()
        self.active_project_box.setObjectName("ProjectPick")
        self.active_project_box.addItem(NO_ACTIVE_PROJECT, None)
        self.active_project_box.setMinimumWidth(120)
        self.active_project_box.setToolTip(
            "New Chat responses are filed in this project."
        )
        self.active_project_box.currentIndexChanged.connect(
            self._active_project_changed
        )
        runbar.addWidget(self.active_project_box)

        self.tool_box = MenuComboBox()
        self.tool_box.setObjectName("ToolChip")
        self.tool_box.addItems(self.tool_prompts.keys())
        self.tool_box.setMinimumWidth(100)
        runbar.addWidget(self.tool_box)

        self.provider_box = MenuComboBox()
        self.provider_box.setObjectName("MachinePick")
        self.provider_box.addItems(PROVIDERS)
        self.provider_box.setMinimumWidth(90)
        runbar.addWidget(self.provider_box)

        dot = QLabel("·")
        dot.setObjectName("RunBarDot")
        runbar.addWidget(dot)

        self.model_box = MenuComboBox()
        self.model_box.setObjectName("MachinePick")
        configure_model_controls(self.provider_box, self.model_box)
        runbar.addWidget(self.model_box)

        self.model_summary_label = QLabel("Recommended model")
        self.model_summary_label.setObjectName("ModelSummary")
        self.model_summary_label.setToolTip(
            "Sentinel applies the recommended model. Use the gear to override it."
        )
        self.model_summary_label.hide()

        # The one number that changes with every keystroke, next to the controls
        # that change it rather than in the far rail.
        runbar.addStretch()

        self.runbar_cost = QLabel("—")
        self.runbar_cost.setObjectName("RunBarCost")
        runbar.addWidget(self.runbar_cost)

        self.stop_chat_btn = QPushButton("Stop")
        self.stop_chat_btn.setObjectName("StopAction")
        self.stop_chat_btn.setEnabled(False)
        self.stop_chat_btn.clicked.connect(self.stop_current_task)
        self.stop_chat_btn.hide()

        self.run_btn = QPushButton("Run")
        self.run_btn.setObjectName("RunAction")
        self.run_btn.clicked.connect(self.send_prompt)

        self.auto_route_btn = QPushButton("Auto-route")
        self.auto_route_btn.setObjectName("ChipBtn")
        self.auto_route_btn.setToolTip(
            "Choose the best available provider and model for the current message"
        )
        self.auto_route_btn.clicked.connect(self.auto_route_agent)

        self.runbar_settings_btn = QPushButton("Options")
        self.runbar_settings_btn.setObjectName("ChipBtn")
        self.runbar_settings_btn.setToolTip("Execution mode, provider permissions and model tools")
        runbar.addWidget(self.auto_route_btn)
        runbar.addWidget(self.runbar_settings_btn)
        runbar.addWidget(self.stop_chat_btn)
        runbar.addWidget(self.run_btn)

        normal_layout.addWidget(runbar_container)

        # Labels other code still addresses, now unused on screen.
        self.tool_label = QLabel("Tool:")
        self.tool_label.hide()
        self.command_label = QLabel("Command:")
        self.command_label.hide()

        # ── Compact request options ───────────────────────────────────────────
        # Keep state in ordinary widgets because request execution and settings
        # already depend on them, but expose it through native nested menus.
        # Global utilities live in the header overflow and Inspector instead of
        # being duplicated in a settings dialog disguised as a popup.
        self.command_box = MenuComboBox(self)
        self.command_box.addItems(self.commands.keys())
        self.command_box.hide()

        self.execution_mode_box = MenuComboBox(self)
        self.execution_mode_box.addItems(["Local only", "Hybrid allowed", "Cloud only"])
        self.execution_mode_box.hide()

        self.allow_openai_checkbox = QCheckBox("OpenAI", self)
        self.allow_deepseek_checkbox = QCheckBox("DeepSeek", self)
        self.allow_kimi_checkbox = QCheckBox("Kimi", self)
        self.allow_gemini_checkbox = QCheckBox("Gemini", self)
        self.allow_anthropic_checkbox = QCheckBox("Anthropic", self)
        self.allow_qwen_checkbox = QCheckBox("Qwen", self)
        for box in (self.allow_openai_checkbox, self.allow_deepseek_checkbox,
                    self.allow_kimi_checkbox, self.allow_gemini_checkbox,
                    self.allow_anthropic_checkbox, self.allow_qwen_checkbox):
            box.setChecked(False)
            box.hide()

        self.recommend_setup_btn = QPushButton("Use recommended", self)
        self.recommend_setup_btn.setObjectName("ChipBtn")
        self.recommend_setup_btn.clicked.connect(self.apply_recommended_setup)
        self.recommend_setup_btn.hide()

        self.auto_recommend_checkbox = QCheckBox("Auto-apply", self)
        self.auto_recommend_checkbox.setChecked(False)
        self.auto_recommend_checkbox.hide()

        self.estimate_btn = QPushButton("Estimate cost", self)
        self.estimate_btn.setObjectName("ChipBtn")
        self.estimate_btn.clicked.connect(self.show_cost_estimate_popup)
        self.estimate_btn.hide()

        self.export_btn = QPushButton("Export report", self)
        self.export_btn.setObjectName("ChipBtn")
        self.export_btn.clicked.connect(self.export_report)
        self.export_btn.hide()

        self.refresh_models_btn = QPushButton("Refresh models", self)
        self.refresh_models_btn.setObjectName("ChipBtn")
        self.refresh_models_btn.clicked.connect(self.load_provider_models)
        self.refresh_models_btn.hide()

        self.get_muse_btn = QPushButton("Get Muse Glimmer", self)
        self.get_muse_btn.setObjectName("ChipBtn")
        self.get_muse_btn.clicked.connect(self.pull_muse_glimmer)
        self.get_muse_btn.hide()

        self.model_guide_btn = QPushButton("Model guide", self)
        self.model_guide_btn.setObjectName("ChipBtn")
        self.model_guide_btn.clicked.connect(self.show_model_guide)
        self.model_guide_btn.hide()

        self.docs_btn = QPushButton("App docs", self)
        self.docs_btn.setObjectName("ChipBtn")
        self.docs_btn.clicked.connect(self.show_docs)
        self.docs_btn.hide()

        self.options_cost_history_btn = QPushButton("Cost history", self)
        self.options_cost_history_btn.setObjectName("ChipBtn")
        self.options_cost_history_btn.clicked.connect(self.show_cost_history)
        self.options_cost_history_btn.hide()

        self.options_run_log_btn = QPushButton("Run log", self)
        self.options_run_log_btn.setObjectName("ChipBtn")
        self.options_run_log_btn.clicked.connect(self.show_run_log)
        self.options_run_log_btn.hide()

        self.options_settings_btn = QPushButton("Settings", self)
        self.options_settings_btn.setObjectName("ChipBtn")
        self.options_settings_btn.clicked.connect(self.show_settings)
        self.options_settings_btn.hide()

        self.runbar_menu = QMenu(self.runbar_settings_btn)
        self.runbar_menu.setMinimumWidth(230)

        def add_combo_submenu(title: str, combo: QComboBox) -> QMenu:
            submenu = self.runbar_menu.addMenu(title)
            actions = {}
            for index in range(combo.count()):
                value = combo.itemText(index)
                action = submenu.addAction(value)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda _checked=False, selected=value: combo.setCurrentText(selected)
                )
                actions[value] = action

            def sync(selected: str) -> None:
                for value, action in actions.items():
                    action.setChecked(value == selected)

            combo.currentTextChanged.connect(sync)
            sync(combo.currentText())
            return submenu

        add_combo_submenu("Command", self.command_box)
        add_combo_submenu("Execution mode", self.execution_mode_box)

        provider_menu = self.runbar_menu.addMenu("Paid provider access")
        for box in (self.allow_openai_checkbox, self.allow_deepseek_checkbox,
                    self.allow_kimi_checkbox, self.allow_gemini_checkbox,
                    self.allow_anthropic_checkbox, self.allow_qwen_checkbox):
            action = provider_menu.addAction(box.text())
            action.setCheckable(True)
            action.setChecked(box.isChecked())
            action.toggled.connect(box.setChecked)
            box.toggled.connect(action.setChecked)

        self.runbar_menu.addSeparator()
        self.runbar_menu.addAction("Auto route now").triggered.connect(
            self.auto_route_btn.click
        )
        self.runbar_menu.addAction("Use recommended model").triggered.connect(
            self.recommend_setup_btn.click
        )
        self.auto_recommend_action = self.runbar_menu.addAction(
            "Auto-apply recommendations"
        )
        self.auto_recommend_action.setCheckable(True)
        self.auto_recommend_action.toggled.connect(
            self.auto_recommend_checkbox.setChecked
        )
        self.auto_recommend_checkbox.toggled.connect(
            self.auto_recommend_action.setChecked
        )
        self.runbar_menu.addAction("Estimate this request").triggered.connect(
            self.estimate_btn.click
        )
        self.runbar_menu.addAction("Export current report").triggered.connect(
            self.export_btn.click
        )

        models_menu = self.runbar_menu.addMenu("Models")
        models_menu.addAction("Refresh model list").triggered.connect(
            self.refresh_models_btn.click
        )
        self.get_muse_menu_action = models_menu.addAction("Get Muse Glimmer")
        self.get_muse_menu_action.triggered.connect(self.get_muse_btn.click)
        models_menu.addAction("Open model guide").triggered.connect(
            self.model_guide_btn.click
        )

        self.runbar_settings_btn.setMenu(self.runbar_menu)

        self.model_box.currentTextChanged.connect(self.save_provider_model_preference)

        def update_model_summary(*_args):
            provider = self.provider_box.currentText()
            model = self.model_box.currentText()
            self.model_summary_label.setText(f"Auto · {provider} / {model}".rstrip(" /"))

        self.provider_box.currentTextChanged.connect(update_model_summary)
        self.model_box.currentTextChanged.connect(update_model_summary)
        update_model_summary()

        self.input_box = ChatInput()
        self.input_box.setObjectName("PromptInput")
        self.input_box.setPlaceholderText(
            "Type your message here…  Enter to send · Shift+Enter for a new line"
        )
        self.input_box.setFixedHeight(140)
        # The caret you compose against, with a cadence per theme, and the
        # corner marks the blue theme puts around whatever has focus.
        vibe.install_caret(self.input_box)
        self.prompt_brackets = vibe.install_brackets(self.input_box)
        normal_layout.addWidget(self.input_box)

        self.send_btn = self.run_btn
        self.input_box.sendRequested.connect(
            lambda: self.send_btn.click()
            if not self.send_btn.isHidden() and self.send_btn.isEnabled() else None
        )

        # ===== INPUT =====
        self.input_box.textChanged.connect(self.update_live_cost_estimate)
        self.input_box.textChanged.connect(self.update_recommendation_label)
        self.input_box.textChanged.connect(self.maybe_auto_apply_recommendation)

        # ===== TOOL / COMMAND =====
        self.command_box.currentTextChanged.connect(self.update_live_cost_estimate)
        self.command_box.currentTextChanged.connect(self.update_recommendation_label)

        self.tool_box.currentTextChanged.connect(self.update_live_cost_estimate)
        self.tool_box.currentTextChanged.connect(self.update_recommendation_label)

        # ===== PROVIDER =====
        # Chat keeps its own loader — it remembers a per-provider default model
        # and falls back to hand-written lists per API — but registers it like
        # any other panel so the recommendation system has one way in.
        self.register_model_loader("chat", self.load_provider_models)
        self.provider_box.currentTextChanged.connect(self.load_provider_models)
        self.provider_box.currentTextChanged.connect(self.update_live_cost_estimate)
        self.provider_box.currentTextChanged.connect(self.update_recommendation_label)

        # ===== MODEL =====
        self.model_box.currentTextChanged.connect(self.update_live_cost_estimate)

        # ===== MODE =====
        self.execution_mode_box.currentTextChanged.connect(self.update_live_cost_estimate)
        self.execution_mode_box.currentTextChanged.connect(self.update_recommendation_label)

        # ===== API CHECKBOXES =====
        self.allow_openai_checkbox.stateChanged.connect(self.update_live_cost_estimate)
        self.allow_openai_checkbox.stateChanged.connect(self.update_recommendation_label)

        self.allow_deepseek_checkbox.stateChanged.connect(self.update_live_cost_estimate)
        self.allow_deepseek_checkbox.stateChanged.connect(self.update_recommendation_label)

        self.allow_kimi_checkbox.stateChanged.connect(self.update_live_cost_estimate)
        self.allow_kimi_checkbox.stateChanged.connect(self.update_recommendation_label)

        self.allow_gemini_checkbox.stateChanged.connect(self.update_live_cost_estimate)
        self.allow_gemini_checkbox.stateChanged.connect(self.update_recommendation_label)

        self.allow_anthropic_checkbox.stateChanged.connect(self.update_live_cost_estimate)
        self.allow_anthropic_checkbox.stateChanged.connect(self.update_recommendation_label)

        self.chat_progress = QProgressBar()
        self.chat_progress.setMinimum(0)
        self.chat_progress.setMaximum(0)
        self.chat_progress.hide()
        normal_layout.addWidget(self.chat_progress)

        self.chat_status_label = QLabel("")
        self.chat_status_label.hide()
        normal_layout.addWidget(self.chat_status_label)

        # Chat controls are a compact workflow stack. Aligning the container to
        # the top prevents Qt from stretching the run bar and prompt apart when
        # there is no response yet.
        center_layout.addWidget(self.normal_panel, 0, Qt.AlignTop)

        # Every specialist workspace gets its own vertical viewport. This keeps
        # controls readable on smaller displays instead of compressing and
        # clipping rows at the bottom of the window.
        self.panel_scrolls = {}

        def add_specialist_panel(key, panel):
            self.panels[key] = panel
            panel.setMaximumWidth(1600)
            scroll = QScrollArea()
            scroll.setObjectName("AgentWorkspaceScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            scroll.setWidget(panel)
            scroll.hide()
            self.panel_scrolls[key] = scroll
            center_layout.addWidget(scroll, 1)

        self.manager_panel = ManagerPanel(self)
        add_specialist_panel("manager", self.manager_panel)

        self.osint_panel = OsintPanel(self)
        add_specialist_panel("osint", self.osint_panel)

        self.osint_heavy_panel = OsintHeavyPanel(self)
        add_specialist_panel("osint_heavy", self.osint_heavy_panel)

        self.wifi_panel = WifiPanel(self)
        add_specialist_panel("wifi", self.wifi_panel)

        self.sentry_panel = SentryPanel(self)
        add_specialist_panel("sentry", self.sentry_panel)

        self.bug_bounty_panel = BugBountyPanel(self)
        add_specialist_panel("bug_bounty", self.bug_bounty_panel)

        self.vpn_panel = VpnPanel(self)
        add_specialist_panel("vpn", self.vpn_panel)

        self.output_label = QPushButton("▾  Conversation")
        self.output_label.setObjectName("OutputToggle")
        self.output_label.setCheckable(True)
        self.output_label.setChecked(True)
        self.output_label.clicked.connect(self._toggle_chat_output)
        self.output_label.hide()
        center_layout.addWidget(self.output_label)

        self.output_box = QTextEdit()
        self.output_box.setObjectName("OutputBox")
        self.output_box.setReadOnly(True)
        self.output_box.setMinimumHeight(260)
        self.output_box.setPlaceholderText(
            "Conversation messages and generated results will appear here."
        )
        self.output_box.hide()
        center_layout.addWidget(self.output_box, 1)

        # The empty state belongs to the centre column, not to the output box:
        # that box is hidden until something arrives, so a backdrop inside it
        # would be invisible at exactly the moment it is meant to show. This
        # sits behind the column's own children and comes through in the space
        # under the prompt — the space that is empty because nothing has run.
        self.output_backdrop = vibe.install_backdrop(
            center_widget,
            visible_while=lambda: not self.output_box.toPlainText().strip(),
            heading="Nothing run yet",
            subcopy="Results and conversation appear here once something runs.",
        )

        self.load_provider_models()

        return center_widget

    def show_output_area(self):
        """Reveal the output label and box. Called when content arrives."""
        if hasattr(self, "output_label") and hasattr(self, "output_box"):
            self.output_label.setChecked(True)
            self.output_label.setText("▾  Conversation")
            self.output_label.setVisible(True)
            self.output_box.setVisible(True)

    def hide_output_area(self):
        """Hide the output label and box (e.g. after New Chat clears state)."""
        if hasattr(self, "output_label") and hasattr(self, "output_box"):
            self.output_label.setVisible(False)
            self.output_box.setVisible(False)

    def _toggle_chat_output(self, expanded: bool) -> None:
        """Collapse previous output into the compact row used by the reference."""
        self.output_label.setText(
            "▾  Conversation" if expanded else "▸  Previous turns"
        )
        self.output_box.setVisible(bool(expanded))

    def toggle_inspector(self, visible: bool) -> None:
        """Reveal diagnostics only when requested, preserving workspace width."""
        if not hasattr(self, "right_panel"):
            return
        self.right_panel.setVisible(bool(visible))
        self.inspector_btn.setChecked(bool(visible))
        self.inspector_visible = bool(visible)
        save_setting("inspector_visible", "true" if visible else "false")
        if visible:
            sizes = self.main_splitter.sizes()
            total = sum(sizes) or self.width()
            right = min(270, max(230, total // 6))
            left = min(270, max(220, total // 6))
            self.main_splitter.setSizes([left, max(520, total - left - right), right])
    
    def build_right_panel(self) -> QWidget:
        right_widget = QWidget()
        right_widget.setObjectName("RightPanel")

        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(0)

        # ── Inner container that holds all cards (scrollable) ───────────
        cards_container = QWidget()
        cards_container.setObjectName("RightCardsContainer")
        cards_container.setMinimumWidth(0)
        cards_container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        cards_layout = QVBoxLayout(cards_container)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)

        inspector_heading = QLabel("INSPECTOR")
        inspector_heading.setObjectName("RailHeading")
        inspector_heading.setToolTip(
            "Current route at a glance; open a section only when you need its details."
        )
        cards_layout.addWidget(inspector_heading)

        # ── Card 1: System ──────────────────────────────────────────────
        system_card = QGroupBox("SYSTEM")
        system_card.setObjectName("RightCard")
        system_layout = QVBoxLayout(system_card)
        system_layout.setContentsMargins(0, 4, 0, 8)
        system_layout.setSpacing(8)

        # Figures, not sentences: every one of these is "x of y", so it is drawn
        # as a proportion. "Used: 11.3 GB · Free: 9.4 GB" cannot be read at a
        # glance, and reading at a glance is the whole job of a status rail.
        self.resource_meters = {
            "RAM": Meter("Memory", "Physical memory in use"),
            "SWAP": Meter("Swap", "Memory paged out to disk. Sustained high swap "
                                  "means real pressure; a large idle figure is "
                                  "usually just accumulated history."),
            "CPU": Meter("CPU", "Processor load across all cores"),
            "BATT": Meter("Battery", "Charge remaining"),
        }
        for meter in self.resource_meters.values():
            system_layout.addWidget(meter)

        self.realtime_monitor_btn = QPushButton("Realtime monitor")
        self.realtime_monitor_btn.setEnabled(False)
        self.realtime_monitor_btn.setToolTip(
            "Detailed realtime monitoring is not available yet; live summary "
            "metrics above refresh every second."
        )
        system_layout.addWidget(self.realtime_monitor_btn)

        # ── Card 2: Routing & Recommendation ────────────────────────────
        routing_card = QGroupBox("CURRENT ROUTE")
        routing_card.setObjectName("RightCard")
        routing_layout = QVBoxLayout(routing_card)
        routing_layout.setContentsMargins(0, 4, 0, 8)
        routing_layout.setSpacing(7)

        # Rows, not prose. The reasoning sentence became the tooltip — it is
        # an explanation you want occasionally, not four wrapped lines you read
        # past every time.
        self.routing_rows = {
            "Suggested": KeyValue("Provider", "—"),
            "Model": KeyValue("Model", "—"),
            "Mode": KeyValue("Mode", "—"),
            "Cost": KeyValue("Cost", "—"),
            "Last run": KeyValue("Last run", "—"),
        }
        for key in ("Suggested", "Model", "Mode", "Cost"):
            row = self.routing_rows[key]
            routing_layout.addWidget(row)

        # kept so the existing update paths still have something to write to
        self.route_result_label = QLabel()
        self.route_result_label.hide()
        self.recommendation_label = QLabel()
        self.recommendation_label.hide()

        # ── Card 3: Cost ────────────────────────────────────────────────
        cost_card = QGroupBox("COST")
        cost_card.setObjectName("RightCard")
        cost_layout = QVBoxLayout(cost_card)
        cost_layout.setContentsMargins(10, 6, 10, 10)
        cost_layout.setSpacing(6)

        self.cost_rows = {
            "estimate": KeyValue("Live estimate", "€0.00 · 0 tok"),
            "last": KeyValue("Last request", "€0.00"),
            "session": KeyValue("This session", "€0.00"),
            "today": KeyValue("Today", "€0.00"),
            "requests": KeyValue("Requests", "0"),
        }
        for row in self.cost_rows.values():
            cost_layout.addWidget(row)
        cost_layout.addWidget(self.routing_rows["Last run"])

        # written to by the existing update paths, no longer shown
        self.live_estimate_label = QLabel(); self.live_estimate_label.hide()
        self.last_request_label = QLabel(); self.last_request_label.hide()
        self.session_cost_label = QLabel(); self.session_cost_label.hide()
        self.today_cost_label = QLabel(); self.today_cost_label.hide()
        self.request_count_label = QLabel(); self.request_count_label.hide()

        # ── Card 4: Budget ──────────────────────────────────────────────
        budget_card = QGroupBox("BUDGET")
        budget_card.setObjectName("RightCard")
        budget_layout = QVBoxLayout(budget_card)
        budget_layout.setContentsMargins(0, 4, 0, 8)
        budget_layout.setSpacing(8)

        # Spend is "x of y" too, and the one figure worth seeing without reading.
        self.budget_meters = {
            "SESSION": Meter("Session", "Spent this session against the session cap"),
            "DAILY": Meter("Today", "Spent today against the daily cap"),
        }
        for meter in self.budget_meters.values():
            budget_layout.addWidget(meter)

        # The caps themselves are set in Settings. A rail reports state; two
        # text fields and two buttons in it made the busiest block on screen out
        # of something changed once a month.
        self.session_budget_input = QLineEdit(str(int(self.session_budget_eur)))
        self.daily_budget_input = QLineEdit(str(int(self.daily_budget_eur)))
        self.save_budget_btn = QPushButton("Save Limits")
        self.save_budget_btn.clicked.connect(self.save_budget_limits)
        self.reset_session_budget_btn = QPushButton("Reset Session Spend")
        self.reset_session_budget_btn.clicked.connect(self.reset_session_spend)
        for widget in (self.session_budget_input, self.daily_budget_input,
                       self.save_budget_btn, self.reset_session_budget_btn):
            widget.hide()

        self.edit_budget_btn = QPushButton("Edit limits…")
        self.edit_budget_btn.setObjectName("RailLink")
        self.edit_budget_btn.clicked.connect(self.show_settings)
        budget_layout.addWidget(self.edit_budget_btn)

        # ── Card 5: Quick Actions ───────────────────────────────────────
        actions_card = QGroupBox("ACTIONS", cards_container)
        actions_card.setObjectName("RightCard")
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(10, 6, 10, 10)
        actions_layout.setSpacing(6)

        self.cost_history_btn = QPushButton("Cost history")
        self.cost_history_btn.clicked.connect(self.show_cost_history)
        actions_layout.addWidget(self.cost_history_btn)

        self.run_log_btn = QPushButton("Run log")
        self.run_log_btn.clicked.connect(self.show_run_log)
        actions_layout.addWidget(self.run_log_btn)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.show_settings)
        actions_layout.addWidget(self.settings_btn)

        # ── Card 6: API Keys ────────────────────────────────────────────
        keys_card = QGroupBox("API KEYS")
        keys_card.setObjectName("RightCard")
        keys_layout = QVBoxLayout(keys_card)
        keys_layout.setContentsMargins(10, 6, 10, 10)
        keys_layout.setSpacing(4)

        # A tick and a cross in every row is five pieces of punctuation saying
        # what one word says. Present or absent, stated plainly, coloured.
        self.key_rows = {}
        for name, wrapper in (("OpenAI", OpenAIClientWrapper),
                              ("DeepSeek", DeepSeekClientWrapper),
                              ("Kimi", KimiClientWrapper),
                              ("Gemini", GeminiClientWrapper),
                              ("Anthropic", AnthropicClientWrapper),
                              ("Qwen", QwenClientWrapper)):
            ready = False
            try:
                ready = bool(wrapper.key_available())
            except Exception:
                ready = False
            row = KeyValue(name, "ready" if ready else "no key")
            row.value.setObjectName("KVValueOn" if ready else "KVValueOff")
            row.setToolTip(f"{name} API key {'found' if ready else 'not set'} in .env")
            self.key_rows[name] = row
            keys_layout.addWidget(row)

        # the old labels, still written to by the settings dialog
        self.openai_key_label = QLabel(); self.openai_key_label.hide()
        self.deepseek_key_label = QLabel(); self.deepseek_key_label.hide()
        self.kimi_key_label = QLabel(); self.kimi_key_label.hide()
        self.gemini_key_label = QLabel(); self.gemini_key_label.hide()
        self.anthropic_key_label = QLabel(); self.anthropic_key_label.hide()

        # The right rail is the live request inspector: selection, spend and
        # machine state. Global connections/actions live with navigation on the
        # left so the two rails carry comparable visual weight.
        cards_layout.addWidget(routing_card)
        cards_layout.addWidget(cost_card)
        cards_layout.addWidget(budget_card)
        cards_layout.addWidget(system_card)
        self.left_utility_layout.addWidget(keys_card)
        self.left_utility_layout.addWidget(actions_card)

        cards_layout.addStretch()

        # ── Scroll area wrapping all cards ──────────────────────────────
        scroll_area = QScrollArea()
        scroll_area.setObjectName("InspectorScroll")
        scroll_area.setMinimumWidth(0)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setWidget(cards_container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        right_layout.addWidget(scroll_area)

        right_widget.setMinimumWidth(230)
        right_widget.setMaximumWidth(270)

        # ── Sizing for buttons/inputs ───────────────────────────────────
        for w in [
            self.realtime_monitor_btn,
            self.save_budget_btn,
            self.reset_session_budget_btn,
            self.cost_history_btn,
            self.run_log_btn,
            self.settings_btn,
        ]:
            w.setFixedHeight(30)
            w.setMinimumWidth(0)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.session_budget_input.setFixedHeight(28)
        self.daily_budget_input.setFixedHeight(28)

        # ── VPN-Agent-inspired card stylesheet ──────────────────────────
        self.themed_sheet(right_widget, """
        QWidget#RightPanel {
            background-color: #0d0f0e;
        }
        QWidget#RightCardsContainer {
            background-color: transparent;
        }

        /* Flat sections, not boxes: a hairline, a small heading, then rows.
           A rail is reference material — boxing each group makes six competing
           containers out of what should read as one column. */
        QGroupBox#RightCard {
            background: transparent;
            border: none;
            border-top: 1px solid #262d29;
            margin-top: 16px;
            padding: 11px 1px 3px 1px;
        }
        QGroupBox#RightCard::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 0px;
            top: 2px;
            padding: 0 6px 0 0;
            background: transparent;
            color: #5d6862;
            font-size: 10px;
            font-weight: 500;
            letter-spacing: 2px;
        }

        QGroupBox#RightCard QLabel {
            font-size: 11px;
            font-weight: normal;
            color: #c8c8c8;
            letter-spacing: 0;
            border: none;
            background: transparent;
        }
        QLabel#ResourceLabel {
            background-color: transparent;
            border: none;
            padding: 0;
            font-size: 11px;
            color: #c8c8c8;
        }
        QFrame#CardDivider {
            background-color: #242424;
            color: #242424;
            max-height: 1px;
            border: none;
        }

        QGroupBox#RightCard QLineEdit {
            font-size: 12px;
            color: #ffffff;
            background-color: #0f0f0f;
            border: 1px solid #242424;
            border-radius: 6px;
            padding: 5px 10px;
        }
        QGroupBox#RightCard QLineEdit:focus {
            border: 1px solid #3cff88;
        }

        QGroupBox#RightCard QPushButton {
            font-size: 12px;
            font-weight: 500;
            color: #d0d0d0;
            background-color: #1a1a1a;
            border: 1px solid #262626;
            border-radius: 8px;
            padding: 8px 12px;
            text-align: left;
        }
        QGroupBox#RightCard QPushButton:hover {
            background-color: #232323;
            border: 1px solid #3cff88;
            color: #ffffff;
        }
        QGroupBox#RightCard QPushButton:pressed {
            background-color: #0f0f0f;
        }
        QGroupBox#RightCard QPushButton:disabled {
            color: #4a4a4a;
            background-color: #161616;
            border: 1px solid #1f1f1f;
        }
        """)

        return right_widget

    def load_provider_models(self):
        if not hasattr(self, "provider_box") or not hasattr(self, "model_box"):
            return

        provider = self.provider_box.currentText()
        previous_model = self.settings.get(f"default_model_{provider}", "")

        self.model_box.clear()

        try:
            # The same route the agent panels take: each client owns its API
            # call and its offline fallback, and a failure is noted on the
            # model box as "chat: load models" instead of raising here.
            models = self.models_for_provider(provider, "chat", self.model_box)
            if provider == "ollama" and not models:
                # No daemon, or nothing pulled yet: offer the names we know.
                models = list(OllamaClient.KNOWN_MODELS)
            elif provider == "openai":
                # Chat is text-only; the client's list also carries the image
                # models that other routes can use.
                models = [
                    m for m in models
                    if any(x in m.lower() for x in ("gpt", "o1", "o3", "o4"))
                ]

            self.model_box.addItems(models)

            if previous_model:
                idx = self.model_box.findText(previous_model)
                if idx >= 0:
                    self.model_box.setCurrentIndex(idx)

        except Exception as e:
            # Model discovery is setup metadata, not conversation output. Keep
            # the Chat canvas clean and expose the diagnostic through the model
            # control tooltip and launcher log instead.
            self._note_failure("chat: load models", e, self.model_box)

        # Route cost styling reflects the selected provider even when model
        # discovery is offline or fails. Otherwise a previous cloud selection
        # leaves Ollama painted as paid until the next successful refresh.
        self.update_live_cost_estimate()
        self._mark_paid_route_choices(self.provider_box, self.model_box)

    def save_provider_model_preference(self):
        if not hasattr(self, "provider_box") or not hasattr(self, "model_box"):
            return

        provider = self.provider_box.currentText()
        model = self.model_box.currentText()

        if not provider or not model:
            return

        self.settings[f"default_model_{provider}"] = model

        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            self.output_box.append(f"[Settings Save Error] {e}")   

    def themed_sheet(self, widget, css: str) -> None:
        """Apply `css` to `widget` under the current theme, and on every change.

        A handful of sheets predate ui/style.py and still carry their own copy
        of the accent. They stay authored in green, like the main sheet, and
        are recoloured on the way in — registering them here is what makes a
        theme switch repaint them instead of leaving one panel behind.
        """
        self._inline_sheets.append((widget, css))
        widget.setStyleSheet(recolour(css))

    def apply_global_style(self):
        self.setStyleSheet(global_stylesheet())
        # The dots paint from `theme.current()`, so they also have to be told
        # when the choice was made somewhere else — the Settings picker, or the
        # restore when it is cancelled.
        if hasattr(self, "theme_dots"):
            self.theme_dots.update()
        # The brackets belong to one theme, so a theme change is exactly when
        # they have to appear or go.
        if hasattr(self, "prompt_brackets"):
            self.prompt_brackets.refresh()
        for widget, css in self._inline_sheets:
            try:
                widget.setStyleSheet(recolour(css))
            except RuntimeError:
                pass    # the widget was deleted; the next rebuild re-registers it

    # ──────────────────────────────────────────────────────────────────
    # OP IDENTITY PANEL
    # ──────────────────────────────────────────────────────────────────

    OSINT_TOOLS = [
        # id, display name, category, cost label, signup URL, .env key (or "")
        ("emailrep",       "EmailRep.io",        "Email",    "Free",       "https://emailrep.io",                           ""),
        ("urlscan",        "URLScan.io",          "Domain",   "Free",       "https://urlscan.io/user/register",              "URLSCAN_API_KEY"),
        ("virustotal",     "VirusTotal",          "Threat",   "Free",       "https://www.virustotal.com/gui/join-us",         "VIRUSTOTAL_API_KEY"),
        ("otx",            "AlienVault OTX",      "Threat",   "Free",       "https://otx.alienvault.com/",                   "OTX_API_KEY"),
        ("ipinfo",         "IPinfo.io",           "Network",  "Free",       "https://ipinfo.io/signup",                      "IPINFO_API_KEY"),
        ("abuseipdb",      "AbuseIPDB",           "Network",  "Free",       "https://www.abuseipdb.com/register",            "ABUSEIPDB_API_KEY"),
        ("greynoise",      "GreyNoise",           "Network",  "Free",       "https://www.greynoise.io/signup",               "GREYNOISE_API_KEY"),
        ("censys",         "Censys",              "Network",  "Free",       "https://search.censys.io/register",             "CENSYS_API_KEY"),
        ("criminalip",     "Criminal IP",         "Network",  "Free",       "https://www.criminalip.io/register",            "CRIMINALIP_API_KEY"),
        ("securitytrails", "SecurityTrails",      "Domain",   "Free",       "https://securitytrails.com/app/signup",         "SECURITYTRAILS_API_KEY"),
        ("hunter",         "Hunter.io",           "Email",    "Free",       "https://hunter.io/users/sign_up",               "HUNTER_API_KEY"),
        ("addyio",         "addy.io",             "Email",    "Free",       "https://app.addy.io/register",                  "ADDYIO_API_KEY"),
        ("breachdirectory","BreachDirectory",     "Breach",   "Free",       "https://breachdirectory.org",                   ""),
        ("hibp",           "HaveIBeenPwned",      "Breach",   "$3.50/mo",   "https://haveibeenpwned.com/API/Key",            "HIBP_API_KEY"),
        ("shodan",         "Shodan",              "Network",  "$49/mo",     "https://account.shodan.io/register",            "SHODAN_API_KEY"),
        ("dehashed",       "DeHashed",            "Breach",   "$5/mo",      "https://dehashed.com/register",                 "DEHASHED_API_KEY"),
        ("snusbase",       "Snusbase",            "Breach",   "$2/mo",      "https://snusbase.com/",                         "SNUSBASE_API_KEY"),
        ("leakcheck",      "LeakCheck",           "Breach",   "Paid",       "https://leakcheck.io/",                         "LEAKCHECK_API_KEY"),
        ("ransomware_live","Ransomware.live",     "Dark Web", "Free",       "https://www.ransomware.live/api",               ""),
        ("ahmia",          "Ahmia",               "Dark Web", "Free",       "https://ahmia.fi/",                             ""),
        ("intelx",         "IntelligenceX",       "Dark Web", "Paid",       "https://intelx.io/",                            "INTELX_API_KEY"),
        ("domaintools",    "DomainTools",         "Domain",   "Paid",       "https://www.domaintools.com/",                  "DOMAINTOOLS_API_KEY"),
        ("courtlistener",  "CourtListener",       "Legal",    "Free",       "https://www.courtlistener.com/register/",       "COURTLISTENER_API_KEY"),
    ]

    def select_agent(self, agent_name):
        self.agent_box.setCurrentText(agent_name)
        for btn in self.agent_buttons.values():
            btn.setChecked(False)
        if agent_name in self.agent_buttons:
            self.agent_buttons[agent_name].setChecked(True)
        self.update_agent_ui(agent_name)

    def update_agent_ui(self, agent_name):
        self._current_agent = agent_name  # track for show_agent_docs()
        self._update_workspace_margins()
        # ── Update the agent header bar (title + subtitle + status pill) ─
        metadata = BUILTIN_AGENTS.get(agent_name, {})
        if hasattr(self, "agent_title_label"):
            self.agent_title_label.setText(metadata.get("label", agent_name.capitalize()))
        if hasattr(self, "agent_subtitle_label"):
            self.agent_subtitle_label.setText(metadata.get("subtitle", ""))
        is_manager = agent_name == "manager"
        is_osint = agent_name == "osint"
        is_osint_heavy = agent_name == "osint_heavy"
        is_wifi = agent_name == "wifi"
        is_sentry = agent_name == "sentry"
        is_bug_bounty = agent_name == "bug_bounty"
        is_vpn = agent_name == "vpn"
        is_custom = (is_manager
                     or is_osint or is_osint_heavy or is_wifi or is_sentry
                     or is_bug_bounty or is_vpn)

        chat_active = agent_name == "chat"
        if hasattr(self, "saved_chats_toggle"):
            self.saved_chats_toggle.setVisible(chat_active)
            self.saved_chats_panel.setVisible(
                chat_active and self.saved_chats_toggle.isChecked()
            )
        if hasattr(self, "saved_searches_toggle"):
            self.saved_searches_toggle.setVisible(is_osint)
            self.saved_searches_panel.setVisible(
                is_osint and self.saved_searches_toggle.isChecked()
            )

        self.normal_panel.setVisible(not is_custom)
        self.manager_panel.setVisible(is_manager)
        self.osint_panel.setVisible(is_osint)
        self.osint_heavy_panel.setVisible(is_osint_heavy)
        self.wifi_panel.setVisible(is_wifi)
        self.sentry_panel.setVisible(is_sentry)
        self.bug_bounty_panel.setVisible(is_bug_bounty)
        self.vpn_panel.setVisible(is_vpn)
        for key, scroll in self.panel_scrolls.items():
            scroll.setVisible(key == agent_name)
        has_output = bool(self.output_box.toPlainText().strip())
        show_output = chat_active and has_output
        self.output_label.setVisible(show_output)
        self.output_box.setVisible(show_output and self.output_label.isChecked())
        self.update_routing_tile_for_agent(agent_name)

    def update_routing_tile_for_agent(self, agent_name):
        """Show the active agent's recommendation using shared catalog metadata."""
        if not hasattr(self, "routing_rows"):
            return
        rec = self._recommendation_for(agent_name)
        if not rec:
            return
        reason = rec.get("reason", "")
        cost_label = rec.get("cost_label")
        if not cost_label:
            cost_label = pricing_metadata(rec["provider"], rec["model"]).compact
        self.routing_rows["Suggested"].set(rec["provider"], reason)
        self.routing_rows["Model"].set(rec["model"], reason)
        self.routing_rows["Mode"].set(rec.get("mode", "—"), reason)
        self.routing_rows["Cost"].set(cost_label, reason)

    def load_models(self):
        self.model_box.clear()
        try:
            models = self.ollama.list_models()
        except Exception:
            models = []
        if not models:
            models = list(OllamaClient.KNOWN_MODELS)
        self.model_box.addItems(models)
        self.update_live_cost_estimate()

    def build_tool_messages(self, selected_tool, full_prompt):
        tool_config = self.tool_prompts.get(selected_tool, {})
        system_prompt = tool_config.get("system", "You are a helpful assistant.")

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt},
        ]

    def auto_route_agent(self):
        raw_text = self.input_box.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "Warning", "Please enter text first.")
            return

        selected_agent = self.agent_box.currentText()
        selected_tool = self.tool_box.currentText() if hasattr(self, "tool_box") else "General Chat"
        decision = self.route_for_request(
            raw_text, agent=selected_agent, tool=selected_tool,
            keep_manual=False,
        )
        self._apply_route_to_widgets(decision, self.provider_box, self.model_box)
        self._show_route(decision)

    def _enabled_provider_names(self) -> set[str]:
        enabled = {"ollama"}
        for provider in ("openai", "deepseek", "kimi", "gemini", "anthropic", "qwen"):
            checkbox = getattr(self, f"allow_{provider}_checkbox", None)
            if checkbox is not None and checkbox.isChecked():
                enabled.add(provider)
        return enabled

    def _routing_preferences(self) -> RoutingPreferences:
        mode = self.execution_mode_box.currentText() if hasattr(self, "execution_mode_box") else "Hybrid allowed"
        priority = str(self.settings.get("routing_priority", "balanced")).lower()
        if priority not in {"balanced", "cost", "speed", "quality", "privacy"}:
            priority = "balanced"
        return RoutingPreferences(
            local_only=mode == "Local only",
            cloud_only=mode == "Cloud only",
            priority=priority,
        )

    def route_for_request(self, prompt: str, *, agent: str = "chat", tool: str = "",
                          keep_manual: bool = True):
        enabled = self._enabled_provider_names()
        available = {provider: self.models_for_provider(provider) for provider in sorted(enabled)}
        manual_provider = self.provider_box.currentText() if keep_manual and hasattr(self, "provider_box") else None
        manual_model = self.model_box.currentText() if keep_manual and hasattr(self, "model_box") else None
        return route_request(
            prompt, agent=agent, tool=tool,
            preferences=self._routing_preferences(),
            available_models=available, enabled_providers=enabled,
            manual_provider=manual_provider, manual_model=manual_model,
        )

    def _show_route(self, decision) -> None:
        text = f"Auto: {decision.provider} · {decision.model} — {decision.reason}"
        if hasattr(self, "route_result_label"):
            self.route_result_label.setText(text)
            self.route_result_label.setToolTip(
                decision.reason + ("\nFallbacks: " + ", ".join(
                    f"{item.provider} · {item.model}" for item in decision.fallbacks
                ) if decision.fallbacks else "")
            )
        if hasattr(self, "routing_rows"):
            self.routing_rows["Suggested"].set(decision.provider, decision.reason)
            self.routing_rows["Model"].set(decision.model, decision.reason)
            self.routing_rows["Mode"].set(decision.mode, decision.reason)
            pricing = getattr(decision, "pricing", None) or pricing_metadata(
                decision.provider, decision.model
            )
            self.routing_rows["Cost"].set(pricing.compact, decision.reason)

    def _apply_route_to_widgets(self, decision, provider_box, model_box) -> None:
        if provider_box is None or model_box is None:
            return
        index = provider_box.findText(decision.provider)
        if index >= 0:
            provider_box.setCurrentIndex(index)
        model_index = self._find_model_index(model_box, decision.model)
        if model_index >= 0:
            model_box.setCurrentIndex(model_index)
        tooltip = f"Auto-selected {decision.provider} · {decision.model}\n{decision.reason}"
        provider_box.setToolTip(tooltip)
        model_box.setToolTip(tooltip)

    def prepare_agent_route(self, agent: str, prompt: str, provider_box, model_box,
                            *, tool: str = "", force: bool = False):
        """Shared request-time auto-routing entry point for every agent panel."""
        auto = getattr(self, "auto_recommend_checkbox", None)
        provider = provider_box.currentText() if provider_box is not None else ""
        panel = self.panels.get(agent) if hasattr(self, "panels") else None
        if not force and getattr(panel, "_prefer_local_retry_once", False):
            panel._prefer_local_retry_once = False
            if provider == "ollama":
                return None
        permissions = self.current_api_permissions()
        permitted = provider == "ollama" or permissions.get(f"allow_{provider}", False)
        # A configured cloud provider can be granted for this request by the
        # single consent dialog in authorize_request(). Do not silently replace
        # the user's explicit selection merely because persistent permission is off.
        if not force and not permitted and self.provider_key_available(provider):
            return None
        if not force and (auto is None or not auto.isChecked()) and permitted:
            return None
        try:
            decision = self.route_for_request(
                prompt, agent=agent, tool=tool, keep_manual=False
            )
        except RuntimeError as exc:
            panel = self.panels.get(agent) if hasattr(self, "panels") else None
            status = getattr(panel, "status_label", None)
            if status is not None:
                if not permitted:
                    status.setText(
                        f"{provider} permission is off. Enable it in Inspector, "
                        "or choose an available local model."
                    )
                else:
                    status.setText(str(exc))
            return False
        self._apply_route_to_widgets(decision, provider_box, model_box)
        self._show_route(decision)
        if not permitted and hasattr(self, "panels"):
            panel = self.panels.get(agent)
            status = getattr(panel, "status_label", None)
            if status is not None:
                panel._route_notice = (
                    f"{provider} permission is off — using "
                    f"{decision.provider} · {decision.model}."
                )
                status.setText(panel._route_notice)
        return decision

    def resolve_backend_model(self):
        provider = self.provider_box.currentText()
        model = self.model_box.currentText()
        execution_mode = self.execution_mode_box.currentText()

        auto = getattr(self, "auto_recommend_checkbox", None)
        if auto is not None and auto.isChecked():
            prompt = self.input_box.toPlainText().strip() if hasattr(self, "input_box") else ""
            agent = self.agent_box.currentText() if hasattr(self, "agent_box") else "chat"
            tool = self.tool_box.currentText() if hasattr(self, "tool_box") else ""
            decision = self.route_for_request(prompt, agent=agent, tool=tool, keep_manual=False)
            self._apply_route_to_widgets(decision, self.provider_box, self.model_box)
            self._show_route(decision)
            return decision.provider, decision.model

        allowed_apis = {
            "openai": self.allow_openai_checkbox.isChecked(),
            "deepseek": self.allow_deepseek_checkbox.isChecked(),
            "kimi": self.allow_kimi_checkbox.isChecked(),
            "gemini": self.allow_gemini_checkbox.isChecked(),
            "anthropic": self.allow_anthropic_checkbox.isChecked(),
            "qwen": self.allow_qwen_checkbox.isChecked(),
        }

        if execution_mode == "Local only":
            return "ollama", model if model else "deepseek-r1:8b"

        if execution_mode == "Cloud only":
            if provider == "ollama":
                raise RuntimeError("Cloud only mode selected, but provider is Ollama/local.")

            if provider not in allowed_apis:
                raise RuntimeError(f"Unknown cloud provider: {provider}")

            if not allowed_apis[provider]:
                raise RuntimeError(f"{provider} API is not enabled. Tick the checkbox first.")

            return provider, model

        if execution_mode == "Hybrid allowed":
            if provider == "ollama":
                return "ollama", model if model else "deepseek-r1:8b"

            if provider in allowed_apis and not allowed_apis[provider]:
                raise RuntimeError(f"{provider} API is not enabled. Tick the checkbox first.")

            return provider, model

        return "ollama", model if model else "deepseek-r1:8b"

    def build_user_prompt(self, raw_text: str):
        command_name = self.command_box.currentText()
        prefix = self.commands.get(command_name, "")
        if prefix.strip():
            return command_name, f"{prefix}\n\n{raw_text}"
        return command_name, raw_text

    @staticmethod
    def _message_timestamp(value=None) -> str:
        """Return an ISO local timestamp suitable for durable chat records."""
        if isinstance(value, datetime):
            moment = value
        elif value:
            try:
                moment = datetime.fromisoformat(str(value))
            except ValueError:
                try:
                    moment = datetime.strptime(str(value), "%Y-%m-%d_%H-%M-%S-%f")
                except ValueError:
                    moment = datetime.now().astimezone()
        else:
            moment = datetime.now().astimezone()
        if moment.tzinfo is None:
            moment = moment.astimezone()
        return moment.isoformat(timespec="seconds")

    def _timestamped_message(self, role: str, content: str, timestamp=None) -> dict:
        return {
            "role": role,
            "content": content,
            "timestamp": self._message_timestamp(timestamp),
        }

    def _normalise_chat_messages(self, messages: list, fallback=None) -> list:
        return [
            self._timestamped_message(
                str(message.get("role", "system")),
                str(message.get("content", "")),
                message.get("timestamp") or fallback,
            )
            for message in messages
            if isinstance(message, dict)
        ]

    @staticmethod
    def _backend_messages(messages: list) -> list:
        """Provider APIs receive only their standard role/content fields."""
        return [
            {"role": message["role"], "content": message["content"]}
            for message in messages
        ]

    def _render_chat_conversation(self, *, force_tail: bool = False) -> None:
        """Render the transcript without yanking a reader away from history."""
        bar = self.output_box.verticalScrollBar()
        previous_scroll = bar.value()
        follow_tail = force_tail or bar.maximum() - bar.value() <= 24
        role_labels = {"user": "YOU", "assistant": "ASSISTANT", "system": "SYSTEM"}
        blocks = []
        for message in self.current_messages:
            role = str(message.get("role", "system")).lower()
            label = role_labels.get(role, role.upper())
            timestamp = self._message_timestamp(message.get("timestamp"))
            try:
                stamp = datetime.fromisoformat(timestamp).astimezone().strftime(
                    "%d %b %Y · %H:%M"
                )
            except ValueError:
                stamp = timestamp
            content = html.escape(str(message.get("content", ""))).replace("\n", "<br>")
            blocks.append(
                f'<div class="message {role}">'
                f'<div class="meta">{html.escape(label)} · {html.escape(stamp)}</div>'
                f'<div class="body">{content or "&nbsp;"}</div></div>'
            )
        self.output_box.setHtml(
            recolour(
                "<style>"
                ".message{margin:4px 0 16px 0;padding:10px 12px;border-left:2px solid #2f3733;}"
                ".message.user{border-left-color:#3cff88;background:#121714;}"
                ".message.assistant{border-left-color:#65726b;}"
                ".message.system{border-left-color:#9b8b52;background:#17160f;}"
                ".meta{font-size:10px;color:#6f7b75;letter-spacing:.5px;margin-bottom:6px;}"
                ".body{font-size:13px;color:#d8dedb;line-height:1.45;}"
                "</style>"
            ) + "".join(blocks)
        )
        if follow_tail:
            self.output_box.moveCursor(QTextCursor.End)
            self.output_box.ensureCursorVisible()
        else:
            bar.setValue(min(previous_scroll, bar.maximum()))

    def send_prompt(self):
        selected_agent = self.agent_box.currentText()

        if selected_agent == "manager":
            # Forge's Send goes to its own panel; the chat box is not its input.
            self.panels["manager"].analyze_idea()
            return

        raw_text = self.input_box.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "Warning", "Please enter text first.")
            return

        selected_tool = self.tool_box.currentText() if hasattr(self, "tool_box") else "General Chat"
        command_name, full_prompt = self.build_user_prompt(raw_text)
        final_backend, final_model = self.resolve_backend_model()

        estimated_cost, approx_tokens = self.estimate_chat_cost(final_backend, final_model, full_prompt)

        api_permissions = {
            "allow_openai": self.allow_openai_checkbox.isChecked(),
            "allow_deepseek": self.allow_deepseek_checkbox.isChecked(),
            "allow_kimi": self.allow_kimi_checkbox.isChecked(),
            "allow_gemini": self.allow_gemini_checkbox.isChecked(),
            "allow_anthropic": self.allow_anthropic_checkbox.isChecked(),
            "allow_qwen": self.allow_qwen_checkbox.isChecked(),
        }

        validation = self.validator.validate(
            agent_name=selected_agent,
            tool_name=selected_tool,
            provider=final_backend,
            api_permissions=api_permissions,
            session_cost=self.session_cost_total,
            session_budget=self.session_budget_eur,
            daily_cost=self.usage_tracker.get_today_total(),
            daily_budget=self.daily_budget_eur,
            estimated_cost=estimated_cost,
        )
        if not validation.allowed:
            QMessageBox.warning(self, "Request Blocked", validation.reason)
            return

        if not self.confirm_external_api_request(
            final_backend,
            final_model,
            estimated_cost,
            approx_tokens,
        ):
            return

        # Memory pre-flight sits with the other gates, before any UI state is
        # changed. Run it after the worker is armed and a cancel would strand a
        # disabled Send button and an open run-log entry.
        if not self.check_memory_before_request(final_backend, final_model):
            return

        try:
            if selected_tool in self.tool_prompts:
                messages = self.build_tool_messages(selected_tool, full_prompt)
            elif selected_agent in self.agent_instances:
                agent = self.agent_instances[selected_agent]
                messages = agent.build_messages(full_prompt)
            else:
                messages = [{"role": "user", "content": full_prompt}]

            self.pending_agent = selected_agent
            self.pending_tool = selected_tool
            self.pending_backend = final_backend
            self.pending_model = final_model
            self.pending_command = command_name
            self.pending_project = self.active_project_id
            prior = list(self.current_messages) if selected_agent == "chat" else []
            fresh = self._normalise_chat_messages(messages)
            if prior and fresh and fresh[0].get("role") == "system":
                fresh = fresh[1:]
            self.pending_messages = prior + fresh
            self.pending_prompt = full_prompt
            self.pending_usage = None

            self.show_output_area()
            self.current_messages = list(self.pending_messages)
            self.current_messages.append(self._timestamped_message("assistant", ""))
            self.input_box.clear()
            self._render_chat_conversation(force_tail=True)

            self.route_result_label.setText(f"Router: {selected_agent} · {final_backend} · {final_model}")

            self.send_btn.hide()
            self.send_btn.setEnabled(False)
            self.stop_chat_btn.show()
            self.stop_chat_btn.setEnabled(True)

            self.start_chat_timer(final_backend, final_model, full_prompt)

            self.active_run_id = self.run_logger.start(
                agent=selected_agent,
                tool=selected_tool,
                provider=final_backend,
                model=final_model,
                mode=self.execution_mode_box.currentText() if hasattr(self, "execution_mode_box") else "",
                prompt_summary=full_prompt,
            )

            self.chat_worker = ChatWorker(
                self.run_backend, final_backend, final_model,
                self._backend_messages(self.pending_messages), full_prompt,
            )
            self.chat_worker.status_signal.connect(self.handle_chat_status)
            self.chat_worker.token_signal.connect(self.handle_chat_token)
            self.chat_worker.finished_signal.connect(self.handle_chat_finished)
            self.chat_worker.usage_signal.connect(self.handle_chat_usage)
            self.chat_worker.error_signal.connect(self.handle_chat_error)
            self.chat_worker.start()

        except Exception as e:
            QMessageBox.warning(self, "Request failed", str(e))

    def confirm_external_api_request(self, backend, model, estimated_cost, approx_tokens):
        if backend == "ollama":
            return True

        message = (
            f"This request will use an external API.\n\n"
            f"Provider: {backend}\n"
            f"Model: {model}\n"
            f"Approx tokens: {approx_tokens}\n"
            f"Estimated cost/quota impact: ~€{estimated_cost:.2f}\n\n"
            f"Continue?"
        )

        result = QMessageBox.question(
            self,
            "Confirm External API Request",
            message,
            QMessageBox.Yes | QMessageBox.No,
        )

        return result == QMessageBox.Yes

    # ── Shared request guard (TODO.md #1) ───────────────────────────────
    # Any agent that can spend money must go through these. Only send_prompt()
    # used to, so the budget caps, the spend counters, the paid-API prompt and
    # Saved Chats all silently ignored every other agent.

    def _note_failure(self, context, exc, widget=None):
        """Record a swallowed exception instead of discarding it.

        These paths deliberately must not raise into the UI, but a bare
        `except: pass` made a failed model listing or history load look exactly
        like "there is nothing here". stderr is captured by the app launcher in
        /tmp/sentinelai_launch.log; when a widget is given, the reason is also
        attached to it as a tooltip so it is visible without reading a log.
        """
        message = f"{context}: {type(exc).__name__}: {exc}"
        print(f"[warn] {message}", file=sys.stderr)
        if widget is not None:
            try:
                widget.setToolTip(f"Last error — {message}")
            except Exception:
                pass

    def current_api_permissions(self) -> dict:
        """The provider checkboxes, as the validator expects them."""
        return {
            "allow_openai": self.allow_openai_checkbox.isChecked(),
            "allow_deepseek": self.allow_deepseek_checkbox.isChecked(),
            "allow_kimi": self.allow_kimi_checkbox.isChecked(),
            "allow_gemini": self.allow_gemini_checkbox.isChecked(),
            "allow_anthropic": self.allow_anthropic_checkbox.isChecked(),
            "allow_qwen": self.allow_qwen_checkbox.isChecked(),
        }

    def provider_key_available(self, provider: str) -> bool:
        if provider == "ollama":
            return True
        client = getattr(self, provider, None)
        checker = getattr(client, "key_available", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return bool(getattr(client, "api_key", None))

    def _pending_request_key(self, agent, request_id=None):
        """Resolve a request id while retaining agent-only direct calls.

        Panels pass a unique request id. Older callers pass only an agent name;
        that remains unambiguous for their normal one-in-flight request shape.
        With several matches, the newest preserves the old map's overwrite
        semantics without destroying the other pending contexts.
        """
        if request_id is not None:
            context = self._pending_requests.get(request_id)
            if context is not None and context.get("agent") == agent:
                return request_id
            return None

        for key, context in reversed(tuple(self._pending_requests.items())):
            if context.get("agent") == agent:
                return key
        return None

    def note_request_usage(self, agent, usage, request_id=None):
        """Real token counts from the worker, when it reports them."""
        key = self._pending_request_key(agent, request_id)
        context = self._pending_requests.get(key) if key is not None else None
        if context is not None:
            context["usage"] = usage

    def authorize_request(self, agent, provider, model, prompt, tool=None,
                          label=None, request_id=None) -> bool:
        """Budget-check and confirm one request. False means: do not send it.

        `tool` is a registry tool name and is validated as one — pass it only
        when the request really runs a registered tool (the chat panel does).
        The agent panels pick their own mode ("Person", "Deep Scan", …), which
        is not a registry entry: pass that as `label` instead, so it still shows
        up in the run log and Saved Chats without failing the tool check.

        On success the context record_request() needs is stashed under a unique
        request id. ``request_id`` is optional so older direct callers can keep
        using the agent-only record/abandon methods for one in-flight request.
        """
        estimated_cost, approx_tokens = self.estimate_chat_cost(provider, model, prompt)

        permissions = self.current_api_permissions()
        one_time_consent = False
        permission_key = f"allow_{provider}"
        if provider != "ollama" and not permissions.get(permission_key, False):
            panel = self.panels.get(agent) if hasattr(self, "panels") else None
            status = getattr(panel, "status_label", None)
            if not self.provider_key_available(provider):
                if status is not None:
                    status.setText(
                        f"{provider} API key is not configured. Add it in Settings "
                        "or choose a local model."
                    )
                return False
            if not self.confirm_external_api_request(
                provider, model, estimated_cost, approx_tokens
            ):
                if status is not None:
                    status.setText("Request cancelled.")
                return False
            permissions[permission_key] = True
            one_time_consent = True

        validation = self.validator.validate(
            agent_name=agent,
            tool_name=tool,
            provider=provider,
            api_permissions=permissions,
            session_cost=self.session_cost_total,
            session_budget=self.session_budget_eur,
            daily_cost=self.usage_tracker.get_today_total(),
            daily_budget=self.daily_budget_eur,
            estimated_cost=estimated_cost,
        )
        if not validation.allowed:
            QMessageBox.warning(self, "Request Blocked", validation.reason)
            return False

        if (not one_time_consent
                and not self.confirm_external_api_request(
                    provider, model, estimated_cost, approx_tokens
                )):
            return False

        descriptor = label or tool or "-"
        request_id = request_id or uuid4().hex
        if request_id in self._pending_requests:
            raise ValueError(f"Request id is already pending: {request_id}")
        self._pending_requests[request_id] = {
            "request_id": request_id,
            "agent": agent,
            "tool": descriptor,
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "usage": None,
            "started_at": time.monotonic(),
            "run_id": self.run_logger.start(
                agent=agent,
                tool=descriptor,
                provider=provider,
                model=model,
                mode=self.execution_mode_box.currentText() if hasattr(self, "execution_mode_box") else "",
                prompt_summary=prompt,
            ),
        }
        return True

    def record_request(self, agent, response, messages=None, request_id=None):
        """Bill, save and close out a request authorised by authorize_request()."""
        key = self._pending_request_key(agent, request_id)
        context = self._pending_requests.pop(key, None) if key is not None else None
        if context is None:          # never authorised (or already recorded)
            return

        entry = self.usage_tracker.log_request(
            agent=context["agent"],
            backend=context["provider"],
            model=context["model"],
            prompt_text=context["prompt"],
            response_text=response,
            usage=context["usage"],
        )

        self.last_request_cost = entry.get("cost_eur", entry.get("estimated_cost", 0.0))
        if hasattr(self, "routing_rows") and "Last run" in self.routing_rows:
            elapsed = max(0.0, time.monotonic() - context.get("started_at", time.monotonic()))
            self.routing_rows["Last run"].set(f"{elapsed:.1f} s")
        self.last_tool_name = f"{context['agent']}/{context['tool']} - {context['provider']}"
        self.session_cost_total += entry.get("estimated_cost", 0.0)
        self.session_request_count += 1
        self.update_usage_labels()

        if messages is None:
            messages = [{"role": "user", "content": context["prompt"]}]
        self.history.save_chat(
            agent=context["agent"],
            backend=context["provider"],
            model=context["model"],
            command=context["tool"],
            messages=messages + [{"role": "assistant", "content": response}],
            response=response,
        )

        if context["run_id"]:
            self.run_logger.finish(
                run_id=context["run_id"],
                status="success",
                input_tokens=entry.get("input_tokens", 0),
                output_tokens=entry.get("output_tokens", 0),
                cost_eur=entry.get("cost_eur", 0.0),
            )

        self.load_history_list()
        self.load_saved_searches()

    def abandon_request(self, agent, reason="error", request_id=None):
        """Drop a request that failed, so it is not billed and the log closes."""
        key = self._pending_request_key(agent, request_id)
        context = self._pending_requests.pop(key, None) if key is not None else None
        if context and context["run_id"]:
            self.run_logger.finish(run_id=context["run_id"], status=reason)

    def record_external_research(self, *, agent: str, target: str,
                                 query_type: str, response: str,
                                 cancelled: bool = False) -> None:
        """Persist a consented public-source lookup without billing an LLM request."""
        run_id = self.run_logger.start(
            agent=agent,
            tool=f"Live Research · {query_type}",
            provider="public-sources",
            model="selected-public-sources",
            mode="External lookup",
            prompt_summary=target,
        )
        try:
            self.history.save_chat(
                agent=agent,
            backend="public-sources",
            model="selected-public-sources",
                command=f"Live Research · {query_type}",
                messages=[
                    {"role": "user", "content": target},
                    {"role": "assistant", "content": response},
                ],
                response=response,
            )
        except Exception:
            if run_id:
                self.run_logger.finish(run_id=run_id, status="error")
            raise
        if run_id:
            self.run_logger.finish(
                run_id=run_id,
                status="cancelled" if cancelled else "success",
                input_tokens=0,
                output_tokens=0,
                cost_eur=0.0,
            )
        self.load_history_list()
        self.load_saved_searches()

    def run_backend(self, backend, model, messages, prompt):
        if backend == "ollama":
            # Runs on the worker thread, so this cannot prompt — it is the hard
            # floor only. Every agent funnels through here, so a model that
            # physically cannot fit is stopped once, for all of them, and the
            # message surfaces via each agent's existing error path instead of
            # freezing the machine for the full request timeout.
            verdict = self.assess_local_model(model)
            if verdict is not None and verdict["level"] == "too_big":
                raise RuntimeError(verdict["message"])

            if hasattr(self.ollama, "chat"):
                return self.ollama.chat(model=model, messages=messages)
            if hasattr(self.ollama, "generate"):
                return self.ollama.generate(model=model, prompt=prompt)

        if backend == "openai":
            if is_image_model(model):
                return self.openai.generate_image(prompt, model=model)
            if hasattr(self.openai, "stream_chat"):
                return self.openai.stream_chat(messages=messages, model=model)
            if hasattr(self.openai, "chat"):
                return self.openai.chat(messages=messages, model=model)
            if hasattr(self.openai, "generate"):
                return self.openai.generate(prompt, model=model)

        if backend == "deepseek":
            if hasattr(self.deepseek, "stream_chat"):
                return self.deepseek.stream_chat(messages=messages, model=model)
            if hasattr(self.deepseek, "chat"):
                return self.deepseek.chat(messages=messages, model=model)
            if hasattr(self.deepseek, "generate"):
                return self.deepseek.generate(prompt, model=model)

        if backend == "kimi":
            if hasattr(self.kimi, "stream_chat"):
                return self.kimi.stream_chat(messages=messages, model=model)
            if hasattr(self.kimi, "chat"):
                return self.kimi.chat(messages=messages, model=model)
            if hasattr(self.kimi, "generate"):
                return self.kimi.generate(prompt, model=model)

        if backend == "qwen":
            if hasattr(self.qwen, "stream_chat"):
                return self.qwen.stream_chat(messages=messages, model=model)
            if hasattr(self.qwen, "chat"):
                return self.qwen.chat(messages=messages, model=model)
            if hasattr(self.qwen, "generate"):
                return self.qwen.generate(prompt, model=model)

        if backend == "gemini":
            if hasattr(self.gemini, "stream_chat"):
                return self.gemini.stream_chat(messages=messages, model=model)

            if hasattr(self.gemini, "chat"):
                return self.gemini.chat(messages=messages, model=model)

            if hasattr(self.gemini, "generate"):
                return self.gemini.generate(prompt, model=model)

        if backend == "anthropic":
            if hasattr(self.anthropic, "stream_chat"):
                return self.anthropic.stream_chat(messages=messages, model=model)

            if hasattr(self.anthropic, "chat"):
                return self.anthropic.chat(messages=messages, model=model)

        raise RuntimeError(f"No compatible backend method found for backend: {backend}")

    def start_chat_timer(self, backend: str, model: str, prompt: str):
        self.chat_started_at = time.time()
        self.chat_elapsed_seconds = 0
        self.chat_estimated_seconds = self.estimate_chat_seconds(backend, model, prompt)
        self.chat_progress.setMinimum(0)
        self.chat_progress.setMaximum(0)
        self.chat_progress.show()
        self.chat_status_label.show()
        if not hasattr(self, "chat_timer"):
            self.chat_timer = QTimer(self)
            self.chat_timer.timeout.connect(self.update_chat_timer)
        self.chat_timer.start(1000)
        self.update_chat_timer()

    def update_chat_timer(self):
        elapsed = int(time.time() - self.chat_started_at) if self.chat_started_at else self.chat_elapsed_seconds
        remaining = max(0, self.chat_estimated_seconds - elapsed)
        self.chat_status_label.setText(
            f"Processing... elapsed {self.format_seconds(elapsed)} · rough remaining {self.format_seconds(remaining)}"
        )

    def stop_chat_timer(self):
        if hasattr(self, "chat_timer"):
            self.chat_timer.stop()
        self.chat_progress.hide()

    def handle_chat_status(self, text):
        self.chat_status_label.setText(text)

    def handle_chat_token(self, text):
        if not self.current_messages or self.current_messages[-1].get("role") != "assistant":
            self.current_messages.append(self._timestamped_message("assistant", ""))
        self.current_messages[-1]["content"] += text
        self._render_chat_conversation()

    def handle_chat_finished(self, response):
        self.stop_chat_timer()
        self.send_btn.show()
        self.send_btn.setEnabled(True)
        self.stop_chat_btn.hide()
        self.stop_chat_btn.setEnabled(False)

        assistant = self._timestamped_message("assistant", response)
        if self.current_messages and self.current_messages[-1].get("role") == "assistant":
            assistant["timestamp"] = self.current_messages[-1].get(
                "timestamp", assistant["timestamp"]
            )
        self.current_messages = self.pending_messages + [assistant]
        self._render_chat_conversation()

        usage_entry = self.usage_tracker.log_request(
            agent=self.pending_agent,
            backend=self.pending_backend,
            model=self.pending_model,
            prompt_text=self.pending_prompt,
            response_text=response,
            usage=self.pending_usage,
            project=self.pending_project,
        )

        self.last_request_cost = usage_entry.get("cost_eur", usage_entry.get("estimated_cost", 0.0))
        tool = getattr(self, "pending_tool", "General Chat")
        self.last_tool_name = f"{self.pending_agent}/{tool} - {self.pending_backend}"
        self.session_cost_total += usage_entry["estimated_cost"]
        self.session_request_count += 1
        self.update_usage_labels()

        run_id = getattr(self, "active_run_id", None)
        if run_id:
            self.run_logger.finish(
                run_id=run_id,
                status="success",
                input_tokens=usage_entry.get("input_tokens", 0),
                output_tokens=usage_entry.get("output_tokens", 0),
                cost_eur=usage_entry.get("cost_eur", 0.0),
            )
            self.active_run_id = None

        self.history.save_chat(
            agent=self.pending_agent,
            backend=self.pending_backend,
            model=self.pending_model,
            command=self.pending_command,
            messages=self.current_messages,
            response=response,
            project=self.pending_project,
        )

        self.load_history_list()
        self.route_result_label.setText(f"Router: {self.pending_agent} · {self.pending_backend} · {self.pending_model}")

    def handle_chat_error(self, error):
        self.stop_chat_timer()
        if (self.current_messages
                and self.current_messages[-1].get("role") == "assistant"
                and not self.current_messages[-1].get("content")):
            self.current_messages.pop()
        self.current_messages.append(
            self._timestamped_message("system", f"The request could not be completed. {error}")
        )
        self._render_chat_conversation()
        self.send_btn.show()
        self.send_btn.setEnabled(True)
        self.stop_chat_btn.hide()
        self.stop_chat_btn.setEnabled(False)

        run_id = getattr(self, "active_run_id", None)
        if run_id:
            self.run_logger.finish(run_id=run_id, status="error", error=error)
            self.active_run_id = None
        
    def handle_chat_usage(self, usage):
        self.pending_usage = usage

    def stop_chat_worker(self):
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.chat_worker.cancel()
            self.chat_worker.terminate()
            self.chat_worker.wait(2000)
            if (self.current_messages
                    and self.current_messages[-1].get("role") == "assistant"
                    and not self.current_messages[-1].get("content")):
                self.current_messages.pop()
            self.current_messages.append(
                self._timestamped_message("system", "Chat request stopped by user.")
            )
            self._render_chat_conversation()
        self.stop_chat_timer()
        self.send_btn.show()
        self.send_btn.setEnabled(True)
        self.stop_chat_btn.hide()
        self.stop_chat_btn.setEnabled(False)

        run_id = getattr(self, "active_run_id", None)
        if run_id:
            self.run_logger.cancel(run_id)
            self.active_run_id = None

    def stop_current_task(self):
        """The window's Stop button: cancel whatever is actually running.

        Every specialised agent answers for itself through ``self.panels``;
        only Chat, which still lives directly on the window, is handled here.
        """
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.stop_chat_worker()
            return

        for panel in self.panels.values():
            if panel.is_running():
                panel.stop()
                return

        self.output_box.append("\n[Info] No running task to stop.")

    def update_resource_label(self):
        """Drive the SYSTEM meters. The exact figures move to the tooltips —
        visible on demand, rather than crowding the glanceable number."""
        stats = self.monitor.snapshot()

        self.resource_meters["RAM"].set(
            stats["ram_percent"] / 100.0,
            f"{stats['ram_percent']:.0f}%",
            stats["ram_level"],
            f"{stats['ram_used_gb']:.1f} GB used · {stats['ram_available_gb']:.1f} GB free",
        )
        self.resource_meters["CPU"].set(
            stats["cpu_percent"] / 100.0,
            f"{stats['cpu_percent']:.0f}%",
            stats["cpu_level"],
        )
        self.resource_meters["SWAP"].set(
            stats["swap_percent"] / 100.0,
            f"{stats['swap_percent']:.0f}%",
            stats["swap_level"],
            f"{stats['swap_used_gb']:.1f} of {stats['swap_total_gb']:.1f} GB",
        )

        if stats["battery_percent"] is None:
            self.resource_meters["BATT"].set_unavailable()
        else:
            self.resource_meters["BATT"].set(
                stats["battery_percent"] / 100.0,
                f"{stats['battery_percent']:.0f}%",
                stats["battery_level"],
                stats["battery_note"],
            )

    def update_usage_labels(self):
        today_total = self.usage_tracker.get_today_total()
        today_requests = self.usage_tracker.get_total_requests_today()
        tool_name = getattr(self, "last_tool_name", "-")

        # ✅ updated last request label (now includes tool name)
        self.last_request_label.setText(
            f"Last Request Cost: €{self.last_request_cost:.2f} ({tool_name})"
        )
        if hasattr(self, "cost_rows"):
            self.cost_rows["last"].set(f"€{self.last_request_cost:.2f}", tool_name)
            self.cost_rows["session"].set(f"€{self.session_cost_total:.2f}")
            self.cost_rows["today"].set(f"€{today_total:.2f}")
            self.cost_rows["requests"].set(
                f"{today_requests} today · {self.session_request_count} session")

        # keep your existing labels
        self.session_cost_label.setText(f"Session Cost: €{self.session_cost_total:.2f}")
        self.today_cost_label.setText(f"Cost Today: €{today_total:.2f}")
        self.request_count_label.setText(
            f"Requests Today: {today_requests} | Session: {self.session_request_count}"
        )

        # budget calculations
        session_remaining = self.session_budget_eur - self.session_cost_total
        daily_remaining = self.daily_budget_eur - today_total

        if hasattr(self, "budget_meters"):
            # Filled by what is spent, not what is left: a bar that empties as
            # you spend reads as progress towards something good.
            for key, spent, cap in (
                ("SESSION", self.session_cost_total, self.session_budget_eur),
                ("DAILY", today_total, self.daily_budget_eur),
            ):
                fraction = (spent / cap) if cap > 0 else 0.0
                level = "red" if fraction >= 0.9 else "yellow" if fraction >= 0.6 else "green"
                remaining = cap - spent
                self.budget_meters[key].set(
                    fraction,
                    f"€{spent:.2f} / €{cap:.0f}",
                    level,
                    f"€{remaining:.2f} left of the €{cap:.2f} {key.lower()} cap",
                )

    def start_resource_timer(self):
        self.resource_timer = QTimer(self)
        self.resource_timer.timeout.connect(self.update_resource_label)
        self.resource_timer.start(1000)

    def chat_title_from_data(self, path: Path, data: Optional[dict] = None) -> str:
        try:
            if data is None:
                data = self.history.load_chat(str(path))
            if data.get("title"):
                return data["title"]

            agent = data.get("agent", "chat")
            first_user = ""
            for msg in data.get("messages", []):
                if msg.get("role") == "user":
                    first_user = msg.get("content", "")
                    break

            clean = re.sub(r"\s+", " ", first_user).strip()
            if not clean:
                clean = path.stem
            return f"{agent}: {clean[:52].rstrip()}"
        except Exception:
            return path.stem

    @staticmethod
    def _combo_index_for_data(combo: QComboBox, value) -> int:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                return index
        return -1

    def refresh_project_controls(self) -> None:
        """Reload both project selectors without losing either selection."""
        try:
            projects = self.registry.list_projects()
        except Exception as exc:
            self._note_failure("chat projects: load", exc)
            projects = []
        self._chat_projects = {
            project["id"]: project for project in projects if project.get("id")
        }

        if self.active_project_id not in self._chat_projects:
            self.active_project_id = None

        if hasattr(self, "active_project_box"):
            box = self.active_project_box
            box.blockSignals(True)
            box.clear()
            box.addItem(NO_ACTIVE_PROJECT, None)
            for project in projects:
                box.addItem(project["name"], project["id"])
            active_index = self._combo_index_for_data(box, self.active_project_id)
            box.setCurrentIndex(max(0, active_index))
            box.blockSignals(False)

        if hasattr(self, "history_project_filter"):
            box = self.history_project_filter
            wanted = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem(ALL_PROJECTS_FILTER, ALL_PROJECTS_FILTER)
            box.addItem(UNFILED_PROJECT_FILTER, UNFILED_PROJECT_FILTER)
            for project in projects:
                box.addItem(project["name"], project["id"])
            wanted_index = self._combo_index_for_data(box, wanted)
            box.setCurrentIndex(max(0, wanted_index))
            box.blockSignals(False)

    def _set_active_project(self, project_id: str | None) -> None:
        project_id = project_id if project_id in getattr(
            self, "_chat_projects", {}
        ) else None
        self.active_project_id = project_id
        if not hasattr(self, "active_project_box"):
            return
        index = self._combo_index_for_data(self.active_project_box, project_id)
        self.active_project_box.setCurrentIndex(max(0, index))

    def _active_project_changed(self, _index: int) -> None:
        self.active_project_id = self.active_project_box.currentData()
        project = getattr(self, "_chat_projects", {}).get(self.active_project_id)
        if project:
            self.active_project_box.setToolTip(
                f"New Chat responses are filed in {project['name']}."
            )
        else:
            self.active_project_box.setToolTip(
                "New Chat responses are saved without a project."
            )

    def create_chat_project(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "New Project", "Project name:"
        )
        name = name.strip()
        if not accepted or not name:
            return
        try:
            project = self.registry.upsert_project({"name": name})
            self.refresh_project_controls()
            self._set_active_project(project["id"])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Project Not Created", f"Could not create the project:\n{exc}"
            )

    def _assign_chat_to_project(self, filepath: str, project_id: str | None) -> None:
        """Assign one durable chat record; ``None`` returns it to Unfiled."""
        data = self.history.load_chat(filepath)
        if project_id:
            if project_id not in getattr(self, "_chat_projects", {}):
                raise ValueError("That project is no longer available.")
            data["project"] = project_id
        else:
            data.pop("project", None)
        with open(filepath, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        self.load_history_list()

    def assign_selected_chat_to_project(self) -> None:
        item = self.history_list.currentItem()
        if not item:
            QMessageBox.information(
                self, "No Selection", "Select a saved chat first."
            )
            return
        choices = [(UNFILED_PROJECT_FILTER, None)] + [
            (project["name"], project["id"])
            for project in getattr(self, "_chat_projects", {}).values()
        ]
        labels = [label for label, _project_id in choices]
        selected, accepted = QInputDialog.getItem(
            self, "Assign Chat", "Project:", labels, 0, False
        )
        if not accepted:
            return
        project_id = choices[labels.index(selected)][1]
        try:
            self._assign_chat_to_project(item.data(Qt.UserRole), project_id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(
                self, "Assignment Failed", f"Could not update the saved chat:\n{exc}"
            )

    def show_history_context_menu(self, position: QPoint) -> None:
        item = self.history_list.itemAt(position)
        if item is None:
            return
        self.history_list.setCurrentItem(item)
        menu = QMenu(self.history_list)
        menu.addAction("Assign to project…").triggered.connect(
            self.assign_selected_chat_to_project
        )
        menu.addAction("Rename…").triggered.connect(
            lambda: self.rename_selected_chat(item)
        )
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(self.delete_selected_chat)
        menu.exec(self.history_list.mapToGlobal(position))

    def load_history_list(self):
        """Fill Saved Chats; project, agent and search filters all intersect.

        Every file is read once here and reused for both the filter options and
        the rows, so adding the filter costs no extra disk reads.
        """
        self.history_list.clear()
        query = self.history_search.text().strip().lower() if hasattr(self, "history_search") else ""
        try:
            loaded = []
            for file in sorted(CHATS_DIR.glob("*.json"), reverse=True):
                try:
                    data = self.history.load_chat(str(file))
                except Exception:
                    data = {}
                loaded.append((file, data))

            self._refresh_history_agent_filter(loaded)
            wanted = (self.history_agent_filter.currentText()
                      if hasattr(self, "history_agent_filter") else ALL_AGENTS_FILTER)
            wanted_project = (
                self.history_project_filter.currentData()
                if hasattr(self, "history_project_filter")
                else ALL_PROJECTS_FILTER
            )

            for file, data in loaded:
                if wanted != ALL_AGENTS_FILTER and data.get("agent", "chat") != wanted:
                    continue
                chat_project = data.get("project")
                if (wanted_project == UNFILED_PROJECT_FILTER and chat_project):
                    continue
                if (wanted_project not in (
                        ALL_PROJECTS_FILTER, UNFILED_PROJECT_FILTER
                    ) and chat_project != wanted_project):
                    continue
                title = self.chat_title_from_data(file, data)
                if query and query not in title.lower():
                    continue
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, str(file))
                project = getattr(self, "_chat_projects", {}).get(chat_project)
                item.setToolTip(
                    f"Project: {project['name']}" if project else "Project: Unfiled"
                )
                self.history_list.addItem(item)
        except Exception as exc:
            self._note_failure("saved chats: load list", exc)

    def saved_search_title_from_data(self, path: Path, data: dict) -> str:
        """Readable Trace title: custom title, otherwise target and query type."""
        if data.get("title"):
            return data["title"]
        target = ""
        for message in data.get("messages", []):
            if message.get("role") == "user":
                target = re.sub(r"\s+", " ", message.get("content", "")).strip()
                break
        target = target or path.stem
        query_type = data.get("command", "Auto-detect")
        return f"{target[:42].rstrip()} · {query_type}"

    def load_saved_searches(self):
        """Show completed Trace runs without mixing them into Saved Chats."""
        if not hasattr(self, "saved_search_list"):
            return
        self.saved_search_list.clear()
        query = (
            self.saved_search_search.text().strip().lower()
            if hasattr(self, "saved_search_search") else ""
        )
        try:
            for path in self.history.list_chats():
                data = self.history.load_chat(str(path))
                if data.get("agent") != "osint":
                    continue
                title = self.saved_search_title_from_data(path, data)
                searchable = " ".join((title, data.get("response", ""))).lower()
                if query and query not in searchable:
                    continue
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, str(path))
                self.saved_search_list.addItem(item)
        except Exception as exc:
            self._note_failure("saved searches: load list", exc)

    def open_selected_search(self, item):
        path = item.data(Qt.UserRole) or item.text()
        try:
            data = self.history.load_chat(path)
            self.select_agent("osint")
            panel = self.panels["osint"]

            target = ""
            for message in data.get("messages", []):
                if message.get("role") == "user":
                    target = message.get("content", "")
                    break
            panel.target_input.setText(target)

            query_type = data.get("command", "Auto-detect")
            if panel.type_box.findText(query_type) >= 0:
                panel.type_box.setCurrentText(query_type)

            provider = data.get("backend", "")
            model = data.get("model", "")
            if panel.provider_box.findText(provider) >= 0:
                panel.provider_box.setCurrentText(provider)
                if panel.model_box.findText(model) >= 0:
                    panel.model_box.setCurrentText(model)

            response = data.get("response", "")
            panel._clear_output()
            panel._last_response = response
            if str(data.get("command", "")).startswith("Live Research"):
                panel._show_lookup_result(json.loads(response), save=False)
            else:
                panel._populate_sections(response)
            panel._reset_activity()
            panel._append_activity(
                "Opened a saved Trace search. This is a stored query-planning "
                "result; no external sources were queried while reopening it."
            )
            panel.status_label.setText("Saved search loaded.")
        except Exception as exc:
            QMessageBox.warning(self, "Open Failed", f"Could not open saved search:\n{exc}")

    def rename_selected_search(self, item):
        path = item.data(Qt.UserRole) or item.text()
        try:
            data = self.history.load_chat(path)
            title, accepted = QInputDialog.getText(
                self, "Rename Search", "Name for this saved search:",
                text=data.get("title", ""),
            )
            if not accepted:
                return
            title = title.strip()
            if title:
                data["title"] = title
            else:
                data.pop("title", None)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
            self.load_saved_searches()
        except Exception as exc:
            self._note_failure("saved searches: rename", exc)

    def delete_selected_search(self):
        item = self.saved_search_list.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Select a saved search first.")
            return
        if QMessageBox.question(
                self, "Delete Search", f"Delete saved search?\n\n{item.text()}"
        ) != QMessageBox.Yes:
            return
        try:
            Path(item.data(Qt.UserRole)).unlink(missing_ok=True)
            self.load_saved_searches()
            self.load_history_list()
        except Exception as exc:
            QMessageBox.warning(self, "Delete Failed", str(exc))

    def new_trace_search(self):
        self.select_agent("osint")
        self.panels["osint"].clear()

    def _refresh_history_agent_filter(self, loaded):
        """Keep the filter's options in step with the chats that exist.

        Signals are blocked while repopulating: the combo's own change signal
        calls back into load_history_list, which would recurse.
        """
        if not hasattr(self, "history_agent_filter"):
            return
        agents = sorted({data.get("agent", "chat") for _f, data in loaded})
        options = [ALL_AGENTS_FILTER] + agents
        current = self.history_agent_filter.currentText()
        if options == [self.history_agent_filter.itemText(i)
                       for i in range(self.history_agent_filter.count())]:
            return                                   # nothing changed
        self.history_agent_filter.blockSignals(True)
        self.history_agent_filter.clear()
        self.history_agent_filter.addItems(options)
        # keep the user's selection when its agent still has chats
        self.history_agent_filter.setCurrentText(
            current if current in options else ALL_AGENTS_FILTER
        )
        self.history_agent_filter.blockSignals(False)

    def rename_selected_chat(self, item):
        """Give a saved chat a name of your own instead of its first prompt."""
        path = item.data(Qt.UserRole) or item.text()
        try:
            data = self.history.load_chat(path)
        except Exception as exc:
            self._note_failure("saved chats: open for rename", exc)
            return

        current = data.get("title", "")
        new_title, ok = QInputDialog.getText(
            self, "Rename Chat", "Name for this chat:", text=current
        )
        if not ok:
            return

        new_title = new_title.strip()
        if new_title:
            data["title"] = new_title
        else:
            data.pop("title", None)      # cleared — fall back to the first prompt
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._note_failure("saved chats: save new name", exc)
            return
        self.load_history_list()

    def open_selected_chat(self, item):
        filepath = item.data(Qt.UserRole) or item.text()
        try:
            data = self.history.load_chat(filepath)
            self.show_output_area()
            messages = data.get("messages", [])
            if not messages and data.get("response"):
                messages = [{"role": "assistant", "content": data["response"]}]
            self.current_messages = self._normalise_chat_messages(
                messages, fallback=data.get("timestamp")
            )
            self._render_chat_conversation(force_tail=True)
            self.input_box.clear()
            self.route_result_label.setText(
                f"Router: {data.get('agent')} · {data.get('backend')} · {data.get('model')}"
            )

            agent_name = data.get("agent", "chat")
            if self.agent_box.findText(agent_name) >= 0:
                self.select_agent(agent_name)
            self._set_active_project(data.get("project"))

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open saved chat:\n{e}")

    def delete_selected_chat(self):
        item = self.history_list.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Select a saved chat first.")
            return

        filepath = item.data(Qt.UserRole)
        confirm = QMessageBox.question(self, "Delete Chat", f"Delete saved chat?\n\n{item.text()}")
        if confirm != QMessageBox.Yes:
            return

        try:
            Path(filepath).unlink(missing_ok=True)
            self.load_history_list()
        except Exception as e:
            QMessageBox.warning(self, "Delete Failed", str(e))

    def new_chat(self):
        self.current_messages = []
        self.input_box.clear()
        self.output_box.clear()
        self.hide_output_area()
        self.route_result_label.setText("Router: not yet computed")

    def export_report(self):
        content = self.output_box.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Warning", "No output to export.")
            return
        title = self.agent_box.currentText() + "_report"
        filepath = self.report_exporter.export_text_report(title, content)
        QMessageBox.information(self, "Export Complete", f"Report saved to:\n{filepath}")

    @staticmethod
    def _wire_document_search(search_box, previous_btn, next_btn,
                              match_label, browser):
        """Give every documentation reader identical search navigation."""
        def find_term(backward=False):
            term = search_box.text().strip()
            if not term:
                match_label.setText("")
                return
            flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlags()
            if not browser.find(term, flags):
                cursor = browser.textCursor()
                cursor.movePosition(
                    QTextCursor.End if backward else QTextCursor.Start
                )
                browser.setTextCursor(cursor)
                browser.find(term, flags)
            total = browser.toPlainText().lower().count(term.lower())
            match_label.setText(f"{total} match{'es' if total != 1 else ''}")

        search_box.returnPressed.connect(lambda: find_term(False))
        search_box.textChanged.connect(lambda _text: find_term(False))
        next_btn.clicked.connect(lambda: find_term(False))
        previous_btn.clicked.connect(lambda: find_term(True))
        return find_term

    def show_agent_docs(self):
        """Open the documentation dialog for the currently active agent."""
        agent_name = getattr(self, "_current_agent", "chat")

        # Map agent key → doc filename (same as the key for most)
        doc_file_map = {
            "chat": "chat", "osint": "osint", "osint_heavy": "osint_heavy",
            "wifi": "wifi", "bug_bounty": "bug_bounty",
            "vpn": "vpn",
            "manager": "manager", }
        doc_key = doc_file_map.get(agent_name, agent_name)

        # Read-only bundled resource (works in dev and in the frozen .app)
        docs_dir = RESOURCE_DIR / "docs" / "agents"
        doc_path = docs_dir / f"{doc_key}.md"

        # Read the markdown source
        if doc_path.exists():
            raw_md = doc_path.read_text(encoding="utf-8")
        else:
            raw_md = f"# No documentation found\n\nNo documentation file was found for **{agent_name}**.\n\nExpected path: `{doc_path}`"

        # Convert markdown to basic HTML (handles headings, bold, tables, code, lists)
        def md_to_html(text: str) -> str:
            import re
            lines = text.split("\n")
            html_lines = []
            in_table = False
            in_code = False
            i = 0
            while i < len(lines):
                line = lines[i]
                # Code block
                if line.startswith("```"):
                    if not in_code:
                        html_lines.append('<pre style="background:#1e1e1e;color:#d4d4d4;padding:10px;border-radius:6px;font-size:12px;overflow:auto;">')
                        in_code = True
                    else:
                        html_lines.append("</pre>")
                        in_code = False
                    i += 1
                    continue
                if in_code:
                    html_lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                    i += 1
                    continue
                # Table row
                if line.startswith("|"):
                    if not in_table:
                        html_lines.append('<table style="border-collapse:collapse;width:100%;margin:8px 0;">')
                        in_table = True
                    cells = [c.strip() for c in line.split("|")[1:-1]]
                    # Skip separator row
                    if all(re.match(r"^[-:]+$", c) for c in cells):
                        i += 1
                        continue
                    is_header = (i == 0 or not lines[i-1].startswith("|")) and \
                                i + 1 < len(lines) and re.match(r"^\|[-| :]+\|$", lines[i+1]) if i+1 < len(lines) else False
                    tag = "th" if is_header else "td"
                    row_html = "".join(
                        f'<{tag} style="border:1px solid #333;padding:6px 10px;text-align:left;">{c}</{tag}>'
                        for c in cells
                    )
                    html_lines.append(f"<tr>{row_html}</tr>")
                    i += 1
                    continue
                else:
                    if in_table:
                        html_lines.append("</table>")
                        in_table = False
                # Headings
                if line.startswith("#### "):
                    html_lines.append(f'<h4 style="color:#e8e8e8;margin:10px 0 4px;">{line[5:]}</h4>')
                elif line.startswith("### "):
                    html_lines.append(f'<h3 style="color:{accent()};margin:14px 0 6px;">{line[4:]}</h3>')
                elif line.startswith("## "):
                    html_lines.append(f'<h2 style="color:#ffffff;border-bottom:1px solid #333;padding-bottom:4px;margin:18px 0 8px;">{line[3:]}</h2>')
                elif line.startswith("# "):
                    html_lines.append(f'<h1 style="color:{accent()};font-size:20px;margin:0 0 4px;">{line[2:]}</h1>')
                # Blockquote / warning
                elif line.startswith("> "):
                    html_lines.append(f'<blockquote style="border-left:3px solid #f0a000;padding:6px 12px;margin:6px 0;background:#1e1a00;color:#f0c050;">{line[2:]}</blockquote>')
                # Unordered list
                elif line.startswith("- ") or line.startswith("* "):
                    html_lines.append(f'<li style="margin:2px 0;">{line[2:]}</li>')
                # Horizontal rule
                elif line.startswith("---"):
                    html_lines.append('<hr style="border:none;border-top:1px solid #333;margin:12px 0;">')
                # Blank line
                elif line.strip() == "":
                    html_lines.append("<br>")
                # Normal paragraph
                else:
                    html_lines.append(f"<p style='margin:3px 0;'>{line}</p>")
                i += 1
            if in_table:
                html_lines.append("</table>")
            if in_code:
                html_lines.append("</pre>")
            html = "\n".join(html_lines)
            # Inline: **bold**, `code`, *italic*
            html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
            html = re.sub(r"`([^`]+)`", r'<code style="background:#2a2a2a;padding:1px 5px;border-radius:3px;font-size:12px;">\1</code>', html)
            html = re.sub(r"\*(.+?)\*", r"<i>\1</i>", html)
            return html

        html_content = md_to_html(raw_md)
        full_html = f"""
        <html><body style="background:#111111;color:#d5ddd8;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:15px;line-height:1.55;padding:10px 14px;">
        {html_content}
        </body></html>
        """

        # Build dialog
        title = BUILTIN_AGENTS.get(agent_name, {}).get("label", agent_name.upper())

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Docs — {title}")
        dialog.resize(900, 720)
        dialog.setStyleSheet("background-color: #111111;")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search this documentation…")
        search_box.setClearButtonEnabled(True)
        search_row.addWidget(search_box, 1)

        previous_btn = QPushButton("Previous")
        previous_btn.setObjectName("ChipBtn")
        search_row.addWidget(previous_btn)

        next_btn = QPushButton("Next")
        next_btn.setObjectName("ChipBtn")
        search_row.addWidget(next_btn)

        match_label = QLabel("")
        match_label.setObjectName("DocsMatchLabel")
        search_row.addWidget(match_label)
        layout.addLayout(search_row)

        browser = QTextBrowser()
        browser.setHtml(full_html)
        browser.setStyleSheet(
            "QTextBrowser { background: #111111; color: #d5ddd8; border: none; font-size: 15px; padding: 10px; }"
            "QScrollBar:vertical { background: #1a1a1a; width: 10px; }"
            "QScrollBar::handle:vertical { background: #333333; border-radius: 5px; }"
        )
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)

        self._wire_document_search(
            search_box, previous_btn, next_btn, match_label, browser
        )

        close_btn = QPushButton("Close")
        close_btn.setObjectName("ChipBtn")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setFixedWidth(100)
        layout.addWidget(close_btn, 0, Qt.AlignRight)

        dialog.exec()

    def show_model_guide(self):
        from ui.dialogs import show_model_guide as _show_model_guide
        return _show_model_guide(self)

    def show_learning_center(self):
        from ui.learning_center import show_learning_center as _show_learning_center
        return _show_learning_center(self)

    def show_docs(self, anchor: str = ""):
        dialog = QDialog(self)
        dialog.setWindowTitle("App documentation")
        dialog.resize(950, 700)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search this documentation…")
        search_box.setClearButtonEnabled(True)
        search_row.addWidget(search_box, 1)

        previous_btn = QPushButton("Previous")
        previous_btn.setObjectName("ChipBtn")
        search_row.addWidget(previous_btn)

        next_btn = QPushButton("Next")
        next_btn.setObjectName("ChipBtn")
        search_row.addWidget(next_btn)

        match_label = QLabel("")
        match_label.setObjectName("DocsMatchLabel")
        search_row.addWidget(match_label)
        layout.addLayout(search_row)

        browser = QTextBrowser()
        browser.setOpenLinks(False)
        browser.setStyleSheet(
            "QTextBrowser { background: #111111; color: #d5ddd8; border: none; "
            "font-size: 15px; padding: 10px; }"
            "QScrollBar:vertical { background: #1a1a1a; width: 10px; }"
            "QScrollBar::handle:vertical { background: #333333; border-radius: 5px; }"
        )

        if README_FILE.exists():
            text = README_FILE.read_text(encoding="utf-8")
            html = markdown.markdown(text, extensions=["toc", "tables"])
        else:
            html = "<h2>No README.md found</h2><p>Create README.md in the project root.</p>"

        browser.setHtml(html)

        def on_anchor_clicked(url):
            fragment = url.fragment()
            if fragment:
                browser.scrollToAnchor(fragment)

        browser.anchorClicked.connect(on_anchor_clicked)

        if anchor:
            QTimer.singleShot(50, lambda: browser.scrollToAnchor(anchor))

        self._wire_document_search(
            search_box, previous_btn, next_btn, match_label, browser
        )
        layout.addWidget(browser)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("ChipBtn")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setFixedWidth(100)
        layout.addWidget(close_btn, 0, Qt.AlignRight)

        search_box.setFocus()
        dialog.exec()

    def closeEvent(self, event):
        try:
            if self.chat_worker is not None and self.chat_worker.isRunning():
                self.chat_worker.cancel()
                self.chat_worker.terminate()
                self.chat_worker.wait(1000)
            from ui.dialogs import shutdown_panels
            shutdown_panels(self)
        except Exception as exc:
            self._note_failure("shutdown: stop background work", exc)
        event.accept()

SINGLE_INSTANCE_KEY = "sentinel.single-instance.v2"
WINDOW_SETTINGS_KEY = "mainWindow/geometry"


def _activate_native_app():
    """Foreground the Python GUI process itself.

    The AppKit call this needs now lives with the rest of the project's
    Objective-C in ui/app_identity.py, which was added to fix the name and icon
    macOS shows for this process. It is the same ctypes call, moved rather than
    rewritten, so a second copy of the runtime plumbing does not go stale here.
    """
    app_identity.activate()


def _tray_status(window) -> str:
    """The menu bar item's first line: what Sentinel is doing, and what it cost.

    Read from the window rather than kept in step with it, so there is no second
    copy of the session's state to go stale. ui/tray.py calls this each time the
    menu opens.
    """
    worker = getattr(window, "chat_worker", None)
    if worker is not None and worker.isRunning():
        agent = BUILTIN_AGENTS.get(
            window.pending_agent or getattr(window, "_current_agent", ""), {}
        ).get("label", "")
        activity = f"Working — {agent}" if agent else "Working"
    else:
        activity = "Idle"
    return f"{activity}  ·  €{window.session_cost_total:.2f} this session"


def _show_main_window(window):
    """Show, restore, clamp to the primary work area, and foreground a window."""
    screen = QApplication.primaryScreen()
    if screen is not None and not window.isFullScreen() and not window.isMaximized():
        work_area = screen.availableGeometry()
        frame = window.frameGeometry()
        width = min(frame.width(), work_area.width())
        height = min(frame.height(), work_area.height())
        x = min(max(frame.x(), work_area.left()), work_area.right() - width + 1)
        y = min(max(frame.y(), work_area.top()), work_area.bottom() - height + 1)
        window.resize(width, height)
        window.move(x, y)

    if window.isMinimized():
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
    if window.windowHandle() is not None:
        window.windowHandle().requestActivate()
    _activate_native_app()


def _hand_off_to_running_instance() -> bool:
    """True when another copy is already running — it is asked to come forward.

    A local socket is the reliable signal here: a lock file can be left behind by
    a crash, and the .app launcher spawns a fresh python each time, so the OS
    can't dedupe the launch for us.
    """
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_KEY)
    if not probe.waitForConnected(400):
        return False
    probe.write(b"raise")
    probe.waitForBytesWritten(400)
    probe.disconnectFromServer()
    return True


if __name__ == "__main__":
    # Both before QApplication, and both for a reason. AppKit reads the name it
    # titles the application menu with when the application object is created,
    # which Qt does inside QApplication(); and the menu bar item's menu aborts
    # the process on macOS 27 unless the clickCount guard is already in place.
    app_identity.name_in_menu_bar("Sentinel")
    appkit_guard.install()

    app = QApplication([])

    # Qt does not set the Dock icon from setWindowIcon on macOS, so AppKit is
    # asked directly, now that QApplication has made NSApp exist.
    app_identity.set_dock_icon(RESOURCE_DIR / "assets" / "icon.icns")

    # Portable window preferences remain on the removable volume too.
    if is_portable():
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(CONFIG_DIR))

    # Second launch: focus the window that is already open and leave. The exit
    # code has to be 0 — the launcher raises an error dialog on anything else.
    if _hand_off_to_running_instance():
        sys.exit(0)

    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)   # clear a socket left by a crash
    instance_server = QLocalServer()
    instance_server.listen(SINGLE_INSTANCE_KEY)

    window = GodAI()
    settings = QSettings("Sentinel", "Sentinel")
    saved_geometry = settings.value(WINDOW_SETTINGS_KEY)
    if saved_geometry is not None:
        window.restoreGeometry(saved_geometry)
    _show_main_window(window)
    QTimer.singleShot(150, lambda: _show_main_window(window))

    def _raise_existing_window():
        instance_server.nextPendingConnection()      # drain the pending connection
        _show_main_window(window)

    instance_server.newConnection.connect(_raise_existing_window)

    # The menu bar item. Quit goes through the window's close, not app.quit():
    # closeEvent is what cancels an in-flight request and shuts the panels'
    # background work down, and quitting around it leaves both running.
    if tray.available():
        menu_bar_item = tray.Tray(RESOURCE_DIR, lambda: _tray_status(window))
        menu_bar_item.open_requested.connect(lambda: _show_main_window(window))
        menu_bar_item.quit_requested.connect(window.close)
        menu_bar_item.show()

    app.aboutToQuit.connect(
        lambda: None if getattr(window, "_portable_reset_committed", False)
        else settings.setValue(WINDOW_SETTINGS_KEY, window.saveGeometry())
    )
    app.exec()
