"""
Modal dialogs lifted out of `GodAI` (TODO.md #2, Phase 2).

Each function takes the application window as `app` — used both as the dialog's
parent and to read the handful of members it needs — and shows the dialog. The
bodies are moved verbatim; only the receiver was renamed from `self` to `app`.
"""

import json
import os

from PySide6.QtCore import QPropertyAnimation, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from services.anthropic_client import AnthropicClientWrapper
from services.database import get_connection, get_setting, save_setting
from services.deepseek_client import DeepSeekClientWrapper
from services.gemini_client import GeminiClientWrapper
from services.kimi_client import KimiClientWrapper
from services.openai_client import OpenAIClientWrapper
from services.registry import Registry
from services.runtime_paths import PortableRuntimeError, is_portable, user_data_base
from services.validator import Validator
from ui.style import polish_combo_box
from ui.widgets import MenuComboBox


# Alias-mint workers whose settings dialog closed while a request was still in
# flight are parked here so the QThread is not garbage-collected mid-request; a
# worker removes itself via _discard_detached_worker when it finishes.
_DETACHED_ALIAS_WORKERS = []


def _discard_detached_worker(worker) -> None:
    try:
        _DETACHED_ALIAS_WORKERS.remove(worker)
    except ValueError:
        pass


def shutdown_panels(app) -> None:
    """Stop panel work and join workers that provide a shutdown contract."""
    for panel in getattr(app, "panels", {}).values():
        shutdown = getattr(panel, "shutdown", None)
        if callable(shutdown):
            shutdown()
        elif panel.is_running():
            panel.stop()


def show_cost_history(app):
    entries = app.usage_tracker.load_log()

    dialog = QDialog(app)
    dialog.setWindowTitle("Cost History")
    dialog.resize(1050, 650)

    layout = QVBoxLayout(dialog)

    filter_row = QHBoxLayout()

    provider_filter = MenuComboBox()
    provider_filter.addItems([
        "all", "ollama", "openai", "deepseek", "kimi", "gemini",
        "anthropic", "qwen",
    ])
    polish_combo_box(provider_filter)
    filter_row.addWidget(QLabel("Provider:"))
    filter_row.addWidget(provider_filter)

    export_btn = QPushButton("Export CSV")
    filter_row.addWidget(export_btn)

    filter_row.addStretch()
    layout.addLayout(filter_row)

    summary_label = QLabel("")
    layout.addWidget(summary_label)

    browser = QTextBrowser()
    layout.addWidget(browser)

    def render():
        provider = provider_filter.currentText()

        filtered = entries
        if provider != "all":
            filtered = [e for e in entries if e.get("backend") == provider]

        total_cost = sum(
            float(e.get("cost_eur", e.get("estimated_cost", 0.0)))
            for e in filtered
        )
        total_tokens = sum(int(e.get("total_tokens", 0)) for e in filtered)
        total_requests = len(filtered)

        summary_label.setText(
            f"Requests: {total_requests} | "
            f"Tokens: {total_tokens:,} | "
            f"Total Cost: €{total_cost:.2f}"
        )

        if not filtered:
            browser.setHtml("<h2>No cost history for this filter.</h2>")
            return

        rows = ""
        for e in reversed(filtered[-200:]):
            rows += f"""
            <tr>
                <td>{e.get('timestamp', '')}</td>
                <td>{e.get('agent', '')}</td>
                <td>{e.get('backend', '')}</td>
                <td>{e.get('model', '')}</td>
                <td>{e.get('input_tokens', 0)}</td>
                <td>{e.get('cached_input_tokens', 0)}</td>
                <td>{e.get('output_tokens', 0)}</td>
                <td>{e.get('total_tokens', 0)}</td>
                <td>€{float(e.get('cost_eur', e.get('estimated_cost', 0.0))):.2f}</td>
                <td>{e.get('cost_type', '')}</td>
            </tr>
            """

        browser.setHtml(f"""
        <h2>Cost History</h2>
        <table border="1" cellspacing="0" cellpadding="6">
            <tr>
                <th>Time</th>
                <th>Agent</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Input</th>
                <th>Cached input</th>
                <th>Output</th>
                <th>Total</th>
                <th>Cost</th>
                <th>Type</th>
            </tr>
            {rows}
        </table>
        """)

    def export_csv():
        provider = provider_filter.currentText()

        filtered = entries
        if provider != "all":
            filtered = [e for e in entries if e.get("backend") == provider]

        if not filtered:
            QMessageBox.information(dialog, "No Data", "No entries to export.")
            return

        export_path, _ = QFileDialog.getSaveFileName(
            dialog,
            "Export Cost History",
            "cost_history.csv",
            "CSV Files (*.csv)"
        )

        if not export_path:
            return

        import csv

        with open(export_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "agent",
                "backend",
                "model",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "total_tokens",
                "cost_eur",
                "cost_type",
            ])

            for e in filtered:
                writer.writerow([
                    e.get("timestamp", ""),
                    e.get("agent", ""),
                    e.get("backend", ""),
                    e.get("model", ""),
                    e.get("input_tokens", 0),
                    e.get("cached_input_tokens", 0),
                    e.get("output_tokens", 0),
                    e.get("total_tokens", 0),
                    float(e.get("cost_eur", e.get("estimated_cost", 0.0))),
                    e.get("cost_type", ""),
                ])

        QMessageBox.information(dialog, "Export Complete", f"Saved to:\n{export_path}")

    provider_filter.currentTextChanged.connect(render)
    export_btn.clicked.connect(export_csv)

    render()
    dialog.exec()


def show_run_log(app):
    entries = app.run_logger.load_recent(500)

    dialog = QDialog(app)
    dialog.setWindowTitle("Run Log")
    dialog.resize(1050, 650)

    layout = QVBoxLayout(dialog)

    filter_row = QHBoxLayout()

    status_filter = MenuComboBox()
    status_filter.addItems(["all", "success", "error", "cancelled"])
    polish_combo_box(status_filter)
    filter_row.addWidget(QLabel("Status:"))
    filter_row.addWidget(status_filter)

    agent_filter = MenuComboBox()
    agent_filter.addItems(["all"] + sorted({e.get("agent", "") for e in entries if e.get("agent")}))
    polish_combo_box(agent_filter)
    filter_row.addWidget(QLabel("Agent:"))
    filter_row.addWidget(agent_filter)

    filter_row.addStretch()
    layout.addLayout(filter_row)

    summary_label = QLabel("")
    layout.addWidget(summary_label)

    browser = QTextBrowser()
    layout.addWidget(browser)

    def render():
        status = status_filter.currentText()
        agent = agent_filter.currentText()

        filtered = entries
        if status != "all":
            filtered = [e for e in filtered if e.get("status") == status]
        if agent != "all":
            filtered = [e for e in filtered if e.get("agent") == agent]

        total_runs = len(filtered)
        total_cost = sum(float(e.get("cost_eur", 0.0)) for e in filtered)
        errors = sum(1 for e in filtered if e.get("status") == "error")

        summary_label.setText(
            f"Runs: {total_runs} | Errors: {errors} | Total Cost: €{total_cost:.4f}"
        )

        if not filtered:
            browser.setHtml("<h2>No runs match this filter.</h2>")
            return

        rows = ""
        for e in reversed(filtered[-300:]):
            status_val = e.get("status", "")
            color = {"success": "#3cff88", "error": "#ff5555", "cancelled": "#ffaa00"}.get(status_val, "#ffffff")
            error_cell = f'<span style="color:#ff5555">{e.get("error", "")}</span>' if e.get("error") else ""
            rows += f"""
            <tr>
                <td>{e.get("timestamp", "")}</td>
                <td>{e.get("run_id", "")}</td>
                <td>{e.get("agent", "")}</td>
                <td>{e.get("tool", "")}</td>
                <td>{e.get("provider", "")}</td>
                <td>{e.get("model", "")}</td>
                <td><span style="color:{color}">{status_val}</span></td>
                <td>{e.get("input_tokens", 0)}</td>
                <td>{e.get("output_tokens", 0)}</td>
                <td>€{float(e.get("cost_eur", 0.0)):.4f}</td>
                <td>{e.get("duration_sec", 0.0)}s</td>
                <td>{error_cell}</td>
            </tr>
            """

        browser.setHtml(f"""
        <h2>Run Log</h2>
        <table border="1" cellspacing="0" cellpadding="5" style="font-size:11px">
            <tr>
                <th>Time</th><th>Run ID</th><th>Agent</th><th>Tool</th>
                <th>Provider</th><th>Model</th><th>Status</th>
                <th>In</th><th>Out</th><th>Cost</th><th>Duration</th><th>Error</th>
            </tr>
            {rows}
        </table>
        """)

    status_filter.currentTextChanged.connect(render)
    agent_filter.currentTextChanged.connect(render)

    render()
    dialog.exec()


def show_settings(app):
    dialog = QDialog(app)
    dialog.setWindowTitle("Settings")
    dialog.resize(940, 600)

    outer = QVBoxLayout(dialog)
    tabs = QTabWidget()
    outer.addWidget(tabs)

    btn_row = QHBoxLayout()
    save_all_btn = QPushButton("Save All")
    save_all_btn.setFixedHeight(32)
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setFixedHeight(32)
    cancel_btn.clicked.connect(dialog.reject)
    btn_row.addStretch()
    btn_row.addWidget(save_all_btn)
    btn_row.addWidget(cancel_btn)
    outer.addLayout(btn_row)

    # ── Tab 1: General ────────────────────────────────────────────
    general_tab = QWidget()
    gl = QGridLayout(general_tab)
    gl.setSpacing(10)
    gl.setContentsMargins(16, 16, 16, 16)

    gl.addWidget(QLabel("EUR / USD rate:"), 0, 0)
    eur_input = QLineEdit(get_setting("eur_per_usd", "0.92"))
    gl.addWidget(eur_input, 0, 1)

    gl.addWidget(QLabel("Default session budget (€):"), 1, 0)
    sess_input = QLineEdit(get_setting("session_budget_eur", str(app.session_budget_eur)))
    gl.addWidget(sess_input, 1, 1)

    gl.addWidget(QLabel("Default daily budget (€):"), 2, 0)
    daily_input = QLineEdit(get_setting("daily_budget_eur", str(app.daily_budget_eur)))
    gl.addWidget(daily_input, 2, 1)

    reset_heading = QLabel("Portable emergency reset")
    reset_heading.setStyleSheet("font-weight: 600; color: #ff6b6b;")
    reset_description = QLabel(
        "Permanently erase Sentinel's portable chats, history, logs, settings, "
        "locally stored reports and API keys. This does not erase macOS, network or provider records."
    )
    reset_description.setWordWrap(True)
    emergency_reset_btn = QPushButton("Emergency Reset…")
    emergency_reset_btn.setObjectName("EmergencyPortableReset")
    emergency_reset_btn.setToolTip(
        "Portable only: double-confirm deletion of Sentinel-owned data and API keys; "
        "does not format the drive or erase external records."
    )
    emergency_reset_btn.setStyleSheet("color: #ff6b6b; border-color: #7a3030;")
    reset_heading.setVisible(is_portable())
    reset_description.setVisible(is_portable())
    emergency_reset_btn.setVisible(is_portable())
    gl.addWidget(reset_heading, 3, 0, 1, 2)
    gl.addWidget(reset_description, 4, 0, 1, 2)
    gl.addWidget(emergency_reset_btn, 5, 0, 1, 2)

    gl.setRowStretch(6, 1)
    tabs.addTab(general_tab, "General")

    # ── Tab 2: Agents ─────────────────────────────────────────────
    agents_tab = QWidget()
    al = QVBoxLayout(agents_tab)
    al.setContentsMargins(8, 8, 8, 8)

    agents_grid = QGridLayout()
    agents_grid.setSpacing(6)
    agents_grid.addWidget(QLabel("<b>Agent</b>"), 0, 0)
    agents_grid.addWidget(QLabel("<b>Enabled</b>"), 0, 1)
    agents_grid.addWidget(QLabel("<b>Budget cap (€, blank = none)</b>"), 0, 2)

    agent_widgets = {}
    for i, agent in enumerate(app.registry.list_agents(), start=1):
        lbl = QLabel(agent["label"] or agent["name"])
        chk = QCheckBox()
        chk.setChecked(agent.get("enabled", True))
        budget_val = agent.get("budget_limit_eur")
        budget_edit = QLineEdit("" if budget_val is None else str(budget_val))
        budget_edit.setPlaceholderText("no limit")
        budget_edit.setMaximumWidth(120)
        agents_grid.addWidget(lbl, i, 0)
        agents_grid.addWidget(chk, i, 1)
        agents_grid.addWidget(budget_edit, i, 2)
        agent_widgets[agent["name"]] = (chk, budget_edit)

    al.addLayout(agents_grid)
    al.addStretch()
    tabs.addTab(agents_tab, "Agents")

    # ── Tab 3: Tools ──────────────────────────────────────────────
    tools_tab = QWidget()
    tl = QVBoxLayout(tools_tab)
    tl.setContentsMargins(8, 8, 8, 8)

    tools_grid = QGridLayout()
    tools_grid.setSpacing(6)
    tools_grid.addWidget(QLabel("<b>Tool</b>"), 0, 0)
    tools_grid.addWidget(QLabel("<b>Enabled</b>"), 0, 1)
    tools_grid.addWidget(QLabel("<b>System Prompt (first 80 chars)</b>"), 0, 2)

    tool_widgets = {}
    for i, tool in enumerate(app.registry.list_tools(), start=1):
        lbl = QLabel(tool["name"])
        chk = QCheckBox()
        chk.setChecked(tool.get("enabled", True))
        prompt_preview = QLabel((tool.get("system_prompt") or "")[:80])
        prompt_preview.setStyleSheet("color: #888; font-size: 11px;")
        tools_grid.addWidget(lbl, i, 0)
        tools_grid.addWidget(chk, i, 1)
        tools_grid.addWidget(prompt_preview, i, 2)
        tool_widgets[tool["name"]] = chk

    tl.addLayout(tools_grid)
    tl.addStretch()
    tabs.addTab(tools_tab, "Tools")

    # ── Tab 4: Pricing ────────────────────────────────────────────
    pricing_tab = QWidget()
    pl = QVBoxLayout(pricing_tab)
    pl.setContentsMargins(8, 8, 8, 8)

    pl.addWidget(QLabel("Model pricing (USD per 1M tokens):"))

    pricing_grid = QGridLayout()
    pricing_grid.setSpacing(6)
    for col, hdr in enumerate([
        "Provider", "Model", "Input /1M USD", "Cached input /1M USD",
        "Output /1M USD",
    ]):
        pricing_grid.addWidget(QLabel(f"<b>{hdr}</b>"), 0, col)

    pricing_widgets = {}
    with get_connection() as conn:
        pricing_rows = conn.execute(
            "SELECT backend, model, input_per_1m_usd, "
            "cached_input_per_1m_usd, output_per_1m_usd "
            "FROM pricing ORDER BY backend, model"
        ).fetchall()

    for i, row in enumerate(pricing_rows, start=1):
        key = (row["backend"], row["model"])
        pricing_grid.addWidget(QLabel(row["backend"]), i, 0)
        pricing_grid.addWidget(QLabel(row["model"]), i, 1)
        in_edit = QLineEdit(str(row["input_per_1m_usd"]))
        in_edit.setMaximumWidth(100)
        cached_edit = QLineEdit(
            "" if row["cached_input_per_1m_usd"] is None
            else str(row["cached_input_per_1m_usd"])
        )
        cached_edit.setMaximumWidth(100)
        cached_edit.setPlaceholderText("same as input")
        out_edit = QLineEdit(str(row["output_per_1m_usd"]))
        out_edit.setMaximumWidth(100)
        pricing_grid.addWidget(in_edit, i, 2)
        pricing_grid.addWidget(cached_edit, i, 3)
        pricing_grid.addWidget(out_edit, i, 4)
        pricing_widgets[key] = (in_edit, cached_edit, out_edit)

    pl.addLayout(pricing_grid)
    pl.addStretch()
    tabs.addTab(pricing_tab, "Pricing")

    # ── Tab 5: OSINT Keys ─────────────────────────────────────────
    env_path = user_data_base() / ".env"

    def _read_env_values():
        """Parse KEY=value pairs from the .env file (missing file -> {})."""
        values = {}
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            return values
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            k, _, v = stripped.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            values[k.strip()] = v
        return values

    def _write_env_key(key, value):
        """Set KEY=value in .env: replace the existing line or append one."""
        value = value.replace("\r", "").replace("\n", "").strip()
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            existed = True
        except FileNotFoundError:
            lines = []
            existed = False
        new_line = f"{key}={value}"
        replaced = False
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            if stripped.partition("=")[0].strip() == key and "=" in stripped:
                lines[idx] = new_line
                replaced = True
                break
        if not replaced:
            lines.append(new_line)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if not existed:
            try:
                os.chmod(env_path, 0o600)
            except OSError:
                pass
        # Make the new key visible to the running app without a restart.
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)

    osint_tools = list(getattr(app, "OSINT_TOOLS", []))
    env_values = _read_env_values()
    try:
        saved_regs = json.loads(get_setting("osint_registrations", "{}") or "{}")
        if not isinstance(saved_regs, dict):
            saved_regs = {}
    except (TypeError, ValueError):
        saved_regs = {}

    osint_tab = QWidget()
    ol = QVBoxLayout(osint_tab)
    ol.setContentsMargins(12, 12, 12, 12)
    ol.setSpacing(8)

    # Ops email
    email_row = QHBoxLayout()
    email_row.addWidget(QLabel("Operational email"))
    ops_email_input = QLineEdit(get_setting("ops_email", ""))
    ops_email_input.setPlaceholderText("research@proton.me")
    email_row.addWidget(ops_email_input, 1)
    copy_email_btn = QPushButton("Copy")
    copy_email_btn.setFixedWidth(64)
    email_row.addWidget(copy_email_btn)
    ol.addLayout(email_row)

    email_hint = QLabel(
        "Use a dedicated research address (e.g. Proton Mail). Never your personal email."
    )
    email_hint.setWordWrap(True)
    email_hint.setStyleSheet("color: #888; font-size: 11px;")
    ol.addWidget(email_hint)

    def copy_ops_email():
        email = ops_email_input.text().strip()
        if email:
            QApplication.clipboard().setText(email)

    copy_email_btn.clicked.connect(copy_ops_email)

    # addy.io burner-alias minting — a user-triggered write to the operator's own
    # addy.io account. Needs ADDYIO_API_KEY, saved in the tool list below.
    mint_row = QHBoxLayout()
    mint_alias_btn = QPushButton("Mint addy.io alias")
    mint_alias_btn.setToolTip(
        "Create a fresh burner alias on your addy.io account and use it as the "
        "operational email above. Needs ADDYIO_API_KEY (saved in the list below).")
    mint_status_lbl = QLabel("")
    mint_status_lbl.setStyleSheet("color: #888; font-size: 11px;")
    mint_status_lbl.setWordWrap(True)
    mint_row.addWidget(mint_alias_btn)
    mint_row.addWidget(mint_status_lbl, 1)
    ol.addLayout(mint_row)

    def mint_alias():
        worker = getattr(dialog, "_alias_worker", None)
        if worker is not None and worker.isRunning():
            return
        from ui.workers import AliasMintWorker
        mint_alias_btn.setEnabled(False)
        mint_status_lbl.setText("Minting alias…")

        def on_mint_finished(result):
            mint_alias_btn.setEnabled(True)
            status = result.get("status")
            if status == "ok" and result.get("email"):
                alias = result["email"]
                ops_email_input.setText(alias)
                QApplication.clipboard().setText(alias)
                mint_status_lbl.setText(f"New alias {alias} — set as ops email and copied.")
            elif status == "skipped":
                mint_status_lbl.setText(result.get("reason", "addy.io key not set."))
            else:
                mint_status_lbl.setText(result.get("detail", "Could not mint an alias.")[:200])

        def on_mint_error(message):
            mint_alias_btn.setEnabled(True)
            mint_status_lbl.setText(f"Error: {message}"[:200])

        worker = AliasMintWorker(description="Sentinel operational alias")
        worker.finished_signal.connect(on_mint_finished)
        worker.error_signal.connect(on_mint_error)
        dialog._alias_worker = worker
        worker.start()

    mint_alias_btn.clicked.connect(mint_alias)

    def _join_alias_worker(_result=None):
        worker = getattr(dialog, "_alias_worker", None)
        if worker is None or not worker.isRunning():
            return
        # The dialog is closing; its widgets are about to be destroyed. Drop the
        # slots first so a late finish can't call into dead widgets. Do NOT
        # QThread.terminate() a thread blocked in an HTTP POST — Qt documents
        # that as unsafe and it can abort the addy.io alias-creation request in
        # an undefined state. Instead park the worker in a module-level registry
        # so it is not garbage-collected while running, and let it end on its
        # own within the request timeout (~35s max); it removes itself when done.
        for signal in (worker.finished_signal, worker.error_signal):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        _DETACHED_ALIAS_WORKERS.append(worker)
        worker.finished.connect(lambda w=worker: _discard_detached_worker(w))

    dialog.finished.connect(_join_alias_worker)

    # Progress / summary
    total_tools = len(osint_tools)
    progress_row = QHBoxLayout()
    reg_progress = QProgressBar()
    reg_progress.setRange(0, max(total_tools, 1))
    reg_progress.setTextVisible(False)
    reg_progress.setFixedHeight(8)
    reg_summary = QLabel("")
    progress_row.addWidget(reg_progress, 1)
    progress_row.addWidget(reg_summary)
    ol.addLayout(progress_row)

    # Filter chips
    filter_row = QHBoxLayout()
    filter_row.setSpacing(4)
    filter_group = QButtonGroup(osint_tab)
    filter_group.setExclusive(True)
    chip_style = (
        "QPushButton { padding: 3px 10px; border-radius: 10px; font-size: 11px; }"
        "QPushButton:checked { background: #2d6cdf; color: white; }"
    )
    for name in ["All", "Free", "Paid", "Email", "Domain", "Network",
                 "Breach", "Threat", "Dark Web", "Legal"]:
        chip = QPushButton(name)
        chip.setCheckable(True)
        chip.setStyleSheet(chip_style)
        chip.setChecked(name == "All")
        filter_group.addButton(chip)
        filter_row.addWidget(chip)
    filter_row.addStretch()
    ol.addLayout(filter_row)

    # Tool rows
    category_colors = {
        "Email": "#3b82f6", "Domain": "#8b5cf6", "Network": "#0ea5e9",
        "Breach": "#ef4444", "Threat": "#f97316", "Dark Web": "#6b7280",
        "Legal": "#0d9488",
    }
    rows_host = QWidget()
    rows_layout = QVBoxLayout(rows_host)
    rows_layout.setContentsMargins(0, 0, 0, 0)
    rows_layout.setSpacing(4)

    osint_rows = []          # (frame, category, cost)
    osint_checks = {}        # tool_id -> QCheckBox
    fade_anims = []          # keep QPropertyAnimation objects alive

    def update_reg_summary():
        count = sum(1 for chk in osint_checks.values() if chk.isChecked())
        reg_progress.setValue(count)
        reg_summary.setText(f"{count} / {total_tools} tools registered")

    for tool_id, display, category, cost, url, env_key in osint_tools:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        rl = QHBoxLayout(frame)
        rl.setContentsMargins(8, 4, 8, 4)
        rl.setSpacing(6)

        chk = QCheckBox()
        chk.setChecked(bool(saved_regs.get(tool_id, False)))
        chk.setToolTip("Registered")
        chk.toggled.connect(update_reg_summary)
        osint_checks[tool_id] = chk
        rl.addWidget(chk)

        name_lbl = QLabel(display)
        name_lbl.setStyleSheet("font-weight: bold;")
        name_lbl.setMinimumWidth(120)
        rl.addWidget(name_lbl)

        cat_lbl = QLabel(category)
        cat_lbl.setStyleSheet(
            f"background: {category_colors.get(category, '#555')}; color: white; "
            "border-radius: 6px; padding: 1px 6px; font-size: 10px;"
        )
        rl.addWidget(cat_lbl)

        is_free = cost.strip().lower() == "free"
        cost_lbl = QLabel(cost)
        cost_lbl.setMinimumWidth(60)
        cost_lbl.setStyleSheet(
            f"color: {'#3cff88' if is_free else '#ffaa00'}; font-size: 11px;"
        )
        rl.addWidget(cost_lbl)

        rl.addStretch()

        reg_btn = QPushButton("Register →")
        if url:
            def open_signup(_checked=False, u=url):
                email = ops_email_input.text().strip()
                if email:
                    QApplication.clipboard().setText(email)
                QDesktopServices.openUrl(QUrl(u))
            reg_btn.clicked.connect(open_signup)
            reg_btn.setToolTip(url)
        else:
            reg_btn.setEnabled(False)
            reg_btn.setVisible(False)
        rl.addWidget(reg_btn)

        key_edit = QLineEdit(env_values.get(env_key, "") if env_key else "")
        key_edit.setEchoMode(QLineEdit.Password)
        key_edit.setPlaceholderText(env_key)
        key_edit.setMinimumWidth(160)
        eye_btn = QPushButton("👁")
        eye_btn.setCheckable(True)
        eye_btn.setFixedWidth(30)
        eye_btn.setToolTip("Show / hide key")
        save_key_btn = QPushButton("Save Key")
        saved_lbl = QLabel("")
        saved_lbl.setStyleSheet("color: #3cff88; font-size: 11px;")
        saved_lbl.setFixedWidth(56)
        saved_effect = QGraphicsOpacityEffect(saved_lbl)
        saved_effect.setOpacity(1.0)
        saved_lbl.setGraphicsEffect(saved_effect)

        def toggle_echo(checked, edit=key_edit):
            edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

        eye_btn.toggled.connect(toggle_echo)

        def save_key(_checked=False, k=env_key, edit=key_edit,
                     lbl=saved_lbl, effect=saved_effect):
            try:
                _write_env_key(k, edit.text())
            except OSError as exc:
                QMessageBox.warning(dialog, "Save failed", f"Could not write {k}:\n{exc}")
                return
            effect.setOpacity(1.0)
            lbl.setText("Saved ✓")

            def start_fade():
                anim = QPropertyAnimation(effect, b"opacity", lbl)
                anim.setDuration(400)
                anim.setStartValue(1.0)
                anim.setEndValue(0.0)
                anim.finished.connect(lambda: lbl.setText(""))
                anim.finished.connect(lambda: fade_anims.remove(anim) if anim in fade_anims else None)
                fade_anims.append(anim)
                anim.start()

            QTimer.singleShot(2000, lbl, start_fade)

        save_key_btn.clicked.connect(save_key)

        for w in (key_edit, eye_btn, save_key_btn, saved_lbl):
            rl.addWidget(w)
            if not env_key:
                w.setVisible(False)

        rows_layout.addWidget(frame)
        osint_rows.append((frame, category, is_free))

    rows_layout.addStretch()

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(rows_host)
    ol.addWidget(scroll, 1)

    def apply_osint_filter(button):
        chosen = button.text()
        for frame, category, is_free in osint_rows:
            if chosen == "All":
                show = True
            elif chosen == "Free":
                show = is_free
            elif chosen == "Paid":
                show = not is_free
            else:
                show = category == chosen
            frame.setVisible(show)

    filter_group.buttonClicked.connect(apply_osint_filter)

    update_reg_summary()
    tabs.addTab(osint_tab, "OSINT Keys")

    def emergency_reset():
        phrase, accepted = QInputDialog.getText(
            dialog,
            "Emergency Reset",
            "This permanently deletes all Sentinel data and API keys stored in its portable data folder.\n"
            "It cannot remove traces retained by macOS, networks, or AI providers.\n\n"
            "Type ERASE SENTINEL DATA to continue:",
        )
        if not accepted or phrase != "ERASE SENTINEL DATA":
            return
        answer = QMessageBox.question(
            dialog,
            "Final confirmation",
            "Erase the complete Sentinel Data folder contents and quit now?\n\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            if getattr(app, "chat_worker", None) is not None and app.chat_worker.isRunning():
                app.stop_chat_worker()
            shutdown_panels(app)
            from services.portable_reset import erase_portable_user_data
            erase_portable_user_data()
        except (OSError, PortableRuntimeError) as exc:
            QMessageBox.critical(dialog, "Reset refused", str(exc))
            return
        app._portable_reset_committed = True
        QMessageBox.information(
            dialog,
            "Portable data erased",
            "Sentinel's portable data and API keys were erased. The application will quit.\n\n"
            "This does not guarantee that macOS, network equipment, or providers retained no records.",
        )
        dialog.accept()
        QTimer.singleShot(0, QApplication.instance().quit)

    emergency_reset_btn.clicked.connect(emergency_reset)

    # ── Save handler ──────────────────────────────────────────────
    def save_all():
        errors = []

        # General
        try:
            eur = float(eur_input.text().strip())
            sess = float(sess_input.text().strip())
            daily = float(daily_input.text().strip())
            save_setting("eur_per_usd", str(eur))
            save_setting("session_budget_eur", str(sess))
            save_setting("daily_budget_eur", str(daily))
            app.session_budget_eur = sess
            app.daily_budget_eur = daily
            if hasattr(app, "session_budget_input"):
                app.session_budget_input.setText(str(sess))
            if hasattr(app, "daily_budget_input"):
                app.daily_budget_input.setText(str(daily))
        except ValueError:
            errors.append("General: invalid number in EUR rate or budget fields.")

        # Agents
        with get_connection() as conn:
            for name, (chk, budget_edit) in agent_widgets.items():
                raw = budget_edit.text().strip()
                try:
                    budget = float(raw) if raw else None
                except ValueError:
                    errors.append(f"Agent '{name}': invalid budget value '{raw}'.")
                    continue
                conn.execute(
                    "UPDATE agents SET enabled = ?, budget_limit_eur = ? WHERE name = ?",
                    (1 if chk.isChecked() else 0, budget, name)
                )
            conn.commit()

        # Tools
        with get_connection() as conn:
            for name, chk in tool_widgets.items():
                conn.execute(
                    "UPDATE tools SET enabled = ? WHERE name = ?",
                    (1 if chk.isChecked() else 0, name)
                )
            conn.commit()

        # Pricing
        with get_connection() as conn:
            for (backend, model), (in_edit, cached_edit, out_edit) in pricing_widgets.items():
                try:
                    in_val = float(in_edit.text().strip())
                    cached_raw = cached_edit.text().strip()
                    cached_val = float(cached_raw) if cached_raw else None
                    out_val = float(out_edit.text().strip())
                    conn.execute(
                        "UPDATE pricing SET input_per_1m_usd = ?, "
                        "cached_input_per_1m_usd = ?, output_per_1m_usd = ? "
                        "WHERE backend = ? AND model = ?",
                        (in_val, cached_val, out_val, backend, model)
                    )
                except ValueError:
                    errors.append(f"Pricing {backend}/{model}: invalid number.")
            conn.commit()

        # OSINT Keys (API keys are written to .env on "Save Key", not here)
        save_setting("ops_email", ops_email_input.text().strip())
        save_setting(
            "osint_registrations",
            json.dumps({tid: chk.isChecked() for tid, chk in osint_checks.items()}),
        )

        app.update_usage_labels()
        app.registry = Registry()
        app.validator = Validator(app.registry)

        if errors:
            QMessageBox.warning(dialog, "Saved with errors", "\n".join(errors))
        else:
            QMessageBox.information(dialog, "Saved", "All settings saved successfully.")
            dialog.accept()

    save_all_btn.clicked.connect(save_all)
    dialog.exec()


def show_model_guide(app):
    dialog = QDialog(app)
    dialog.setWindowTitle("Model & Agent Control Panel")
    dialog.resize(1050, 750)

    layout = QVBoxLayout(dialog)

    # =========================
    # SEARCH BAR
    # =========================
    search_box = QLineEdit()
    search_box.setPlaceholderText("Search guide: ollama, openai, coding, osint, cost, routing...")
    layout.addWidget(search_box)

    tabs = QTabWidget()
    layout.addWidget(tabs)

    # =========================
    # DYNAMIC SYSTEM INFO
    # =========================
    try:
        ollama_models = app.ollama.list_models()
    except Exception:
        ollama_models = []

    openai_status = "✅ Available" if OpenAIClientWrapper.key_available() else "❌ Not set"
    deepseek_status = "✅ Available" if DeepSeekClientWrapper.key_available() else "❌ Not set"
    kimi_status = "✅ Available" if KimiClientWrapper.key_available() else "❌ Not set"
    gemini_status = "✅ Available" if GeminiClientWrapper.key_available() else "❌ Not set"
    anthropic_status = "✅ Available" if AnthropicClientWrapper.key_available() else "❌ Not set"

    current_mode = app.execution_mode_box.currentText() if hasattr(app, "execution_mode_box") else "Unknown"
    current_provider = app.provider_box.currentText() if hasattr(app, "provider_box") else "Unknown"
    current_model = app.model_box.currentText() if hasattr(app, "model_box") else "Unknown"

    ollama_html = "<br>".join(ollama_models) if ollama_models else "No local Ollama models detected."

    # =========================
    # SIMPLE RECOMMENDATION ENGINE
    # =========================
    def get_recommendation():
        agent = app.agent_box.currentText() if hasattr(app, "agent_box") else "chat"
        command = app.command_box.currentText() if hasattr(app, "command_box") else "General Chat"

        text = f"{agent} {command}".lower()

        if "coding" in text or "code" in text or "debug" in text:
            return "Recommended: Claude Sonnet or DeepSeek for complex coding. Use Ollama for small/private fixes."
        if "writing" in text or "rewrite" in text or "email" in text:
            return "Recommended: Claude Sonnet or OpenAI for polished writing. Gemini is a good fallback. Ollama is fine for drafts."
        if "osint" in text:
            return "Recommended: DeepSeek or Gemini for analysis. Claude or OpenAI for polished final reports."
        if current_mode == "Local only":
            return "Current setup is privacy-safe and free: Local only with Ollama."
        return "Recommended default: use Ollama for simple tasks, enable APIs only when quality or context length matters."

    recommendation = get_recommendation()

    # =========================
    # TAB 1: MODELS
    # =========================
    model_tab = QTextBrowser()
    model_tab.setHtml("""
    <h2>Model Guide</h2>

    <h3>Ollama / Local Models</h3>
    <p><b>Best for:</b> private tasks, simple chat, drafts, quick analysis, offline usage.</p>
    <p><b>Cost:</b> FREE — local execution. Uses your CPU/RAM instead of API credits.</p>
    <p><b>Use when:</b> the task is not critical, not too complex, or you want privacy.</p>
    <p><b>Popular models:</b> deepseek-r1:8b, deepseek-r1:1.5b, llama3, mistral, phi3</p>

    <h3>Anthropic (Claude) API</h3>
    <p><b>Best for:</b> coding, writing, reasoning, document analysis, nuanced instruction-following.</p>
    <p><b>Key:</b> ANTHROPIC_API_KEY — get it at console.anthropic.com</p>
    <p><b>Models:</b></p>
    <ul>
        <li><b>claude-opus-4-6</b> — Most capable. Best for complex reasoning, long documents, difficult coding. ~$15/$75 per 1M tokens.</li>
        <li><b>claude-sonnet-4-6</b> — Best balance of quality and cost. Recommended for most tasks. ~$3/$15 per 1M tokens.</li>
        <li><b>claude-haiku-4-5-20251001</b> — Fastest and cheapest. Good for simple tasks and high-volume use. ~$0.80/$4 per 1M tokens.</li>
    </ul>
    <p><b>Use when:</b> you need high-quality, nuanced responses — especially for coding, writing, and analysis.</p>

    <h3>OpenAI API</h3>
    <p><b>Best for:</b> coding, difficult reasoning, polished writing, professional documents, complex planning.</p>
    <p><b>Key:</b> OPENAI_API_KEY — get it at platform.openai.com</p>
    <p><b>Models:</b></p>
    <ul>
        <li><b>gpt-4o-mini</b> — Fast and affordable. Good for most everyday tasks.</li>
        <li><b>gpt-4.1-mini</b> — Improved mini model. Better reasoning than gpt-4o-mini.</li>
        <li><b>gpt-4.1</b> — Full model. Best for demanding tasks where quality is critical.</li>
        <li><b>o1 / o3 / o4-mini</b> — Reasoning models. Slow but excellent for hard logic problems.</li>
    </ul>
    <p><b>Use when:</b> quality matters more than cost or a task needs strong reasoning.</p>

    <h3>DeepSeek API</h3>
    <p><b>Best for:</b> structured analysis, coding support, OSINT-style reasoning, long analytical tasks.</p>
    <p><b>Key:</b> DEEPSEEK_API_KEY — get it at platform.deepseek.com</p>
    <p><b>Models:</b></p>
    <ul>
        <li><b>deepseek-flash</b> — Fast and inexpensive. Trace's recommended model for structured research.</li>
        <li><b>deepseek-v4-pro</b> — Stronger reasoning and coding. The Coding tool's recommended model.</li>
    </ul>
    <p><b>Use when:</b> you want strong analysis and coding at potentially lower cost than OpenAI.</p>

    <h3>Kimi API (Moonshot AI)</h3>
    <p><b>Best for:</b> coding and long-context agentic/tool-use tasks (OSINT-style multi-step work).</p>
    <p><b>Key:</b> KIMI_API_KEY — get it at platform.kimi.ai</p>
    <p><b>Models:</b></p>
    <ul>
        <li><b>kimi-k2.7-code</b> — Dedicated coding model, 256k context. Default Kimi model here.</li>
        <li><b>kimi-k2.7-code-highspeed</b> — Same model, faster output.</li>
        <li><b>kimi-k2.6</b> — General dialogue/agent model, visual + text input, 256k context.</li>
        <li><b>kimi-k3</b> — Flagship model, 1M token context, strongest reasoning.</li>
    </ul>
    <p><b>Use when:</b> the task is coding-heavy or involves many chained tool calls / long context.</p>

    <h3>Gemini API</h3>
    <p><b>Best for:</b> general fallback, broad summaries, mixed tasks, long-context tasks.</p>
    <p><b>Key:</b> GOOGLE_API_KEY — get it at console.cloud.google.com</p>
    <p><b>Models:</b></p>
    <ul>
        <li><b>gemini-2.5-pro</b> — Most capable Gemini. Excellent long-context handling.</li>
        <li><b>gemini-2.5-flash</b> — Fast and cost-effective. Good for summaries and drafts.</li>
    </ul>
    <p><b>Use when:</b> you need very long context or a cost-effective alternative to OpenAI/Claude.</p>

    """)
    tabs.addTab(model_tab, "Models")

    # =========================
    # TAB 2: AGENTS
    # =========================
    agent_tab = QTextBrowser()
    agent_tab.setHtml("""
    <h2>Agent Guide</h2>

    <h3>Chat Agent</h3>
    <p><b>Use for:</b> general questions, explanations, planning, brainstorming.</p>
    <p><b>Recommended:</b> Ollama for simple/private tasks. Claude Sonnet, OpenAI, or Gemini for higher-quality answers.</p>

    <h3>Writing Tool</h3>
    <p><b>Use for:</b> emails, documentation, CVs, professional writing, and rewriting from Chat.</p>
    <p><b>Recommended:</b> Claude Sonnet (best for nuanced writing). OpenAI as alternative. Ollama for drafts.</p>

    <h3>Coding Tool</h3>
    <p><b>Use for:</b> debugging, code generation, refactoring, and explaining errors from Chat.</p>
    <p><b>Recommended:</b> Claude Sonnet or DeepSeek for complex code. Ollama for small fixes and private testing.</p>

    <h3>OSINT Agent</h3>
    <p><b>Use for:</b> legal/defensive OSINT summaries, public-source analysis, report structuring.</p>
    <p><b>Recommended:</b> DeepSeek or Gemini for broad analysis. Claude or OpenAI for polished final reports.</p>

    <h3>Manager Agent</h3>
    <p><b>Use for:</b> designing and creating new agents from a plain-language description.</p>
    <p><b>Recommended:</b> Claude Sonnet or DeepSeek for spec generation. The Manager Agent writes the code and DB entry automatically.</p>
    """)
    tabs.addTab(agent_tab, "Agents")

    # =========================
    # TAB 3: ROUTING
    # =========================
    routing_tab = QTextBrowser()
    routing_tab.setHtml("""
    <h2>Routing & API Permissions</h2>

    <h3>Execution Mode</h3>
    <p><b>Local only:</b> always use Ollama/local model. No API cost.</p>
    <p><b>Hybrid allowed:</b> use selected provider, but APIs must be explicitly enabled via checkbox.</p>
    <p><b>Cloud only:</b> force a cloud provider (OpenAI, DeepSeek, Gemini, or Anthropic). API checkbox must be enabled.</p>

    <h3>API Checkboxes</h3>
    <p>Each cloud provider has a checkbox: <b>OpenAI · DeepSeek · Gemini · Anthropic</b>.</p>
    <p>If a checkbox is not ticked, that API will be blocked even if selected as provider.</p>
    <p>This prevents accidental API usage and unexpected costs.</p>

    <h3>Recommended Setup by Task</h3>
    <ul>
        <li><b>Private / simple tasks:</b> Local only + Ollama</li>
        <li><b>Coding / debugging:</b> Hybrid + Anthropic (Claude Sonnet) or DeepSeek</li>
        <li><b>Writing / documents:</b> Hybrid + Anthropic (Claude Sonnet) or OpenAI</li>
        <li><b>OSINT / analysis:</b> Hybrid + DeepSeek or Gemini</li>
    </ul>
    """)
    tabs.addTab(routing_tab, "Routing")

    # =========================
    # TAB 4: SYSTEM / DYNAMIC INFO
    # =========================
    system_tab = QTextBrowser()
    system_tab.setHtml(f"""
    <h2>Current System & Model Status</h2>

    <h3>Current Selection</h3>
    <p><b>Execution Mode:</b> {current_mode}</p>
    <p><b>Provider:</b> {current_provider}</p>
    <p><b>Model:</b> {current_model}</p>

    <h3>API Key Status</h3>
    <p><b>OpenAI:</b> {openai_status}</p>
    <p><b>DeepSeek:</b> {deepseek_status}</p>
    <p><b>Kimi:</b> {kimi_status}</p>
    <p><b>Gemini:</b> {gemini_status}</p>
    <p><b>Anthropic:</b> {anthropic_status}</p>

    <h3>Installed Ollama Models</h3>
    <p>{ollama_html}</p>

    <h3>Recommendation</h3>
    <p><b>{recommendation}</b></p>
    """)
    tabs.addTab(system_tab, "System")

    # =========================
    # SEARCH FUNCTION
    # =========================
    all_tabs = {
        "Models": model_tab,
        "Agents": agent_tab,
        "Routing": routing_tab,
        "System": system_tab,
    }

    original_html = {
        "Models": model_tab.toHtml(),
        "Agents": agent_tab.toHtml(),
        "Routing": routing_tab.toHtml(),
        "System": system_tab.toHtml(),
    }

    def apply_search():
        query = search_box.text().strip().lower()

        if not query:
            for name, widget in all_tabs.items():
                widget.setHtml(original_html[name])
            return

        for name, widget in all_tabs.items():
            html = original_html[name]
            plain = widget.toPlainText().lower()

            if query in plain:
                widget.setHtml(html)
            else:
                widget.setHtml(
                    f"<h2>{name}</h2>"
                    f"<p>No matches for: <b>{query}</b></p>"
                )

    search_box.textChanged.connect(apply_search)

    dialog.exec()
