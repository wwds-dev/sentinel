"""Tooltip text for every control in the app.

Moved verbatim out of main.py (see docs/refactor_plan.md, phase 1) — the body
is unchanged apart from the receiver being named `app`, so the section comments
and the
order the tooltips are applied in are preserved exactly.
"""

from services.agent_catalog import BUILTIN_AGENTS


def seed_tooltips(app):
    """Apply explanatory tooltips to every important control in every
    panel. Tooltips can be toggled off via the chip in the header bar."""
    # ── Centre-panel general controls (Chat / normal panel) ──────────
    app._set_tooltips({
        "tool_box":                "System prompt for the conversation (General Chat, Writing, Coding, Summarize, Rewrite). Changing it applies from your next message.",
        "command_box":             "Prompt scaffold from config/commands.json, put in front of every message until you change it. Choose it under Options → Command.",
        "provider_box":            "AI provider that will run this request. Ollama is local and free; OpenAI, DeepSeek, Kimi, Gemini, Anthropic and Qwen are cloud (pay-as-you-go). In Local only mode a cloud pick runs on Ollama.",
        "model_box":               "Specific model under the chosen provider. Larger models cost more but produce stronger output.",
        "refresh_models_btn":      "Re-fetch the model list from the selected provider.",
        "model_guide_btn":         "Open the in-app Model Guide with current models, pricing, and recommendations.",
        "docs_btn":                "Open the full Sentinel documentation.",
        "agent_docs_btn":          "Open the documentation for the currently active agent.",
        "execution_mode_box":      "Local only: every request runs on Ollama, even if a cloud provider is selected. Hybrid allowed: the selected provider runs if it is ticked under Paid provider access. Cloud only: a ticked cloud provider is required.",
        "allow_openai_checkbox":   "Allow this request to use the OpenAI API (paid).",
        "allow_deepseek_checkbox": "Allow this request to use the DeepSeek API (paid, cheap).",
        "allow_kimi_checkbox": "Allow this request to use the Kimi API (paid, strong at coding/agentic tasks).",
        "allow_gemini_checkbox":   "Allow this request to use Google Gemini (free tier available).",
        "allow_anthropic_checkbox":"Allow this request to use Anthropic Claude (paid).",
        "allow_qwen_checkbox":     "Allow this request to use Qwen (Alibaba DashScope, paid).",
        "input_box":               "Type your prompt here. Long prompts cost more on paid providers.",
        "send_btn":                "Run the prompt on the selected provider and model (on Ollama if the execution mode is Local only).",
        "stop_chat_btn":           "Cancel the in-flight request.",
        "auto_route_btn":          "Choose the best available provider and model for the current message.",
        "recommend_setup_btn":     "Apply the recommended provider + model for the current tool / agent.",
        "auto_recommend_checkbox": "Apply the recommendation automatically on every input change.",
        "estimate_btn":            "Show the estimated cost of the current prompt + settings before sending.",
        "export_btn":              "Save the whole conversation as a plain-text file in data/reports (Options → Export current report).",
        "tooltips_toggle_btn":     "Toggle hover tooltips across the entire app.",
        "agent_title_label":       "Current agent. Click an agent in the left sidebar to switch.",
        "agent_subtitle_label":    "What this agent does in one line.",
    })

    # ── Left panel ───────────────────────────────────────────────────
    if hasattr(app, "agent_buttons"):
        for name, metadata in BUILTIN_AGENTS.items():
            btn = app.agent_buttons.get(name)
            if btn is not None:
                btn.setToolTip(metadata["tooltip"])
    app._set_tooltips({
        "history_search":   "Filter saved chats by title (your first message, or the name you gave it). Replies are not searched.",
        "history_list":     "Click a saved chat to re-open it; double-click to rename; right-click for Assign to project, Rename and Delete.",
        "delete_chat_btn":  "Delete the currently selected saved chat.",
        "saved_search_search": "Filter saved Trace searches by target or result text.",
        "saved_search_list": "Click a saved search to reopen its target and stored result (nothing is sent again); double-click to rename it.",
        "delete_search_btn": "Delete the currently selected saved Trace search.",
        "new_search_btn": "Clear Trace and begin a new search.",
        "new_chat_btn":     "Start a fresh conversation: clears the current context and stops a request that is still running.",
    })

    # ── Right panel cards ────────────────────────────────────────────
    app._set_tooltips({
        "resource_label":           "Live RAM / CPU / SWAP / battery snapshot. Green = healthy, yellow = busy, red = stressed.",
        "realtime_monitor_btn":     "(Coming soon) Live charts of system resource usage.",
        "route_result_label":       "Last routing decision — which agent + provider + model was used.",
        "recommendation_label":     "Recommendation for the current tool / agent — provider + model + reason.",
        "live_estimate_label":      "Estimated cost of the current prompt at the selected provider + model.",
        "last_request_label":       "Cost of the most recently completed request.",
        "session_cost_label":       "Total spend since this app session started.",
        "today_cost_label":         "Total spend today (resets at midnight local time).",
        "request_count_label":      "Number of requests sent today and during this session.",
        "budget_label":             "How much of the budget remains for this session and today.",
        "session_budget_input":     "Maximum spend allowed for this session in euros.",
        "daily_budget_input":       "Maximum spend allowed per day in euros.",
        "save_budget_btn":          "Persist the budget limits to settings.",
        "reset_session_budget_btn": "Reset the session spend counter back to zero.",
        "cost_history_btn":         "Open the Cost History dialog (charts and tables of past spending).",
        "run_log_btn":              "Open the Run Log dialog (every request with status, duration, cost).",
        "settings_btn":             "Open the Settings dialog (pricing, agents, tools, EUR/USD rate).",
        "openai_key_label":         "Whether an OpenAI API key is configured. Set OPENAI_API_KEY in .env or ~/.zshrc.",
        "deepseek_key_label":       "Whether a DeepSeek API key is configured. Set DEEPSEEK_API_KEY in .env or ~/.zshrc.",
        "kimi_key_label":           "Whether a Kimi (Moonshot AI) API key is configured. Set KIMI_API_KEY in .env or ~/.zshrc.",
        "gemini_key_label":         "Whether a Google Gemini API key is configured. Set GOOGLE_API_KEY in .env or ~/.zshrc.",
        "anthropic_key_label":      "Whether an Anthropic API key is configured. Set ANTHROPIC_API_KEY in .env or ~/.zshrc.",
    })

    # ── Per-agent panel tooltips ─────────────────────────────────────


    # Wi-Fi (Beacon)
    app._set_tooltips({
        "wifi.mode_box":          "What to run — Interface Info, Scan Networks, Signal Monitor, Ping Test, or Kali Command Builder.",
        "wifi.interface_box":     "Interface names, shown for reference (typically en0 on Mac). The selection here does not change which interface the modes use.",
        "wifi.target_input":      "Target host (only used by Ping Test mode).",
        "wifi.run_btn":           "Run the selected mode.",
        "wifi.stop_btn":          "Cancel the running scan / probe.",
        "wifi.detect_btn":        "Scan USB for known compatible Wi-Fi adapters (TL-WN722N, AWUS036ACH, etc.).",
        "wifi.save_btn":          "Save the raw output to a file.",
    })

    # OSINT (Trace) — moved to ui/panels/osint.py, so the names are dotted.
    app._set_tooltips({
        "osint.target_input":   "The identifier to research: a name, username, email, domain, IP address, company or phone number. Identifier only — there is no context field, and extra words around an email, domain or IP fail validation.",
        "osint.type_box":       "Choose the kind of identifier, or leave Auto-detect, which guesses locally and never picks Company. A dotted handle reads as a domain and a crypto address as a username, so set the type yourself before a live lookup.",
        "osint.provider_box":   "Provider for Structure Query. Live Research and Exposure Check call no model.",
        "osint.model_box":      "Model for Structure Query. BEST FIT marks the recommended one; Auto-route picks one for the current input.",
        "osint.analyse_btn":    "Structure Query: ask the selected model for an investigation plan. Contacts no research source; a cloud model receives the prompt only after you confirm the request.",
        "osint.stop_btn":       "Cancel the running request. A stopped Structure Query ends as an error and its partial text is discarded; a stopped Live Research or Exposure Check keeps the sources that already finished.",
    })

    # OSINT Pro (Bloodhound)
    app._set_tooltips({
        "osint_heavy.target_input":     "Target identifier (person, username, email, domain, IP, organisation, phone number or crypto address).",
        "osint_heavy.type_box":         "Target type — guides which tools and pivots are used.",
        "osint_heavy.scope_box":        "Investigation depth: Quick Scan / Standard / Deep Dive.",
        "osint_heavy.objective_input":  "Investigation objective / context for the analyst.",
        "osint_heavy.browse_btn":      "Optional — image to read EXIF metadata from. The metadata stays on this Mac unless you tick the metadata box below.",
        "osint_heavy.investigate_btn":  "Ask before sending the target to the public sources that apply (No is the default), collect their records, then generate the five-section dossier with the selected model.",
        "osint_heavy.stop_btn":         "Cancel the investigation.",
        "osint_heavy.save_btn":         "Save the dossier text (the model's reply) to a .txt file. The collected source records and the image metadata panel are not added to it.",
        "osint_heavy.threat_bar":       "Threat level on a 0–10 scale: the model's own estimate, read from the dossier.",
        "osint_heavy.add_folder_btn":   "Choose a local folder Bloodhound is allowed to search.",
        "osint_heavy.remove_folder_btn": "Remove selected folders from the search scope.",
        "osint_heavy.file_name_filter": "Match all or part of a file name.",
        "osint_heavy.file_extension_filter": "Limit results to extensions such as pdf, jpg, or docx.",
        "osint_heavy.file_search_btn":  "Search the selected folders (This Mac) or the remote folders (SSH machine) using file metadata only.",
        "osint_heavy.file_cancel_btn":  "Stop the file search and keep results found so far.",
        "osint_heavy.file_results":     "Read-only matches; double-click one to reveal its folder (This Mac) or copy its path (remote).",
        "osint_heavy.file_source_box":  "Search this Mac or a machine you already access through SSH.",
        "osint_heavy.remote_host_input": "Owned SSH host, IP address, or ~/.ssh/config alias. Its host key must already be in ~/.ssh/known_hosts.",
        "osint_heavy.remote_user_input": "SSH account name. A User set for this host in ~/.ssh/config overrides it.",
        "osint_heavy.remote_roots_input": "Comma-separated absolute folders allowed for remote search.",
        "osint_heavy.open_ssh_btn":      "Open ssh://user@host:port, built from the host, user and port entered here, in the system's SSH handler.",
    })

    # Network watch (Sentry)
    app._set_tooltips({
        "sentry.state_label":     "Whether a baseline exists and when it was last updated. Passes compare new activity against it.",
        "sentry.dry_run_btn":     "Compare against the baseline without saving anything. The result is headed 'dry run, nothing saved'; with no baseline it says so.",
        "sentry.reset_btn":       "Delete the saved baseline and the recorded findings (no confirmation); the next pass records a fresh baseline. The background watch shares both, so it is reset too.",
        "sentry.interval_box":    "How often the background watch runs, from 1 to 720 minutes. Applied when you press Install or Reinstall.",
        "sentry.install_btn":     "Add a per-user launch agent (~/Library/LaunchAgents) that runs one read-only pass at this interval, at every login and straight away, until you press Remove. Reinstall reloads it with the interval shown.",
        "sentry.remove_btn":      "Unload the background watch and delete its launch agent. The baseline and recorded findings are kept.",
        "sentry.bg_status_label": "Whether the launch agent is running, installed but not loaded, or off, and where its log is.",
        "sentry.run_btn":         "Observe the network now, compare against the baseline and save what is seen. The first pass only records the baseline.",
        "sentry.stop_btn":        "Cancel a watch pass or the AI read. A pass is checked once its read-only commands have returned; one stopped in time saves nothing.",
        "sentry.ai_checkbox":     "After a pass with findings, send them — LAN IPs, MAC addresses and process names included — to the selected provider and model for a calibrated read. Off by default; nothing is sent when there is nothing to report.",
        "sentry.findings_box":    "Findings from the latest pass, strongest first (ALERT, WARNING, NOTICE, INFO). Before any pass this session it lists the most recent recorded findings, background watch included.",
        "sentry.stream_box":      "The AI read of the findings (only when Explain findings with AI is ticked).",
        "sentry.status_label":    "Progress and the result of the last action.",
    })

    # Bug Bounty (Bug Spray)
    app._set_tooltips({
        "bug_bounty.target_input":       "Asset the report is about (URL or IP). Free text: not checked against the program's scope.",
        "bug_bounty.program_input":      "Name of the bug bounty program (HackerOne, Bugcrowd, etc.). Declared by you; not verified.",
        "bug_bounty.scope_box":          "Scope category sent to the model — Web, Mobile, API, Network, etc. Not checked against anything.",
        "bug_bounty.severity_box":       "Severity you expect; sent to the model as an unverified hint. The Severity tile shows what the model rates.",
        "bug_bounty.findings_input":     "Paste raw findings: HTTP responses, Burp output, source snippets, recon notes.",
        "bug_bounty.nmap_cmd_input":     "An nmap command; only nmap will start. Leave empty to build one from the Target. Runs on your machine with no scope check.",
        "bug_bounty.nmap_run_btn":       "Run nmap and capture its output below; stops after 10 minutes or 256 KB. With an empty field it builds and starts a default scan of the Target's host (nmap -sV -sC -T4 --open) immediately.",
        "bug_bounty.nmap_stop_btn":      "Kill the running Nmap process.",
        "bug_bounty.nmap_output":        "Live Nmap output. Only the scanner's text is sent to the model, not the [Running]/[Done]/[Error] lines.",
        "bug_bounty.analyse_btn":        "Ask the selected model for a vulnerability report (CWE and CVSS are its suggestions, for your review) and a submission draft. Sends your inputs; a cloud model asks you to confirm the cost first.",
        "bug_bounty.stop_btn":           "Cancel the analysis.",
        "bug_bounty.save_btn":           "Save the model's full reply to a Markdown (.md, the default) or text (.txt) file.",
        "bug_bounty.clear_btn":          "Clear the fields, Nmap output, results and tiles. Does not stop a running request or scan.",
    })

    # Tunnel (VPN). Inline tooltips in ui/panels/vpn.py cover the other widgets;
    # the keys below replace the inline text of import_config_btn and build_btn.
    app._set_tooltips({
        "vpn.connect_btn":         "Review the target and command, then ask for confirmation (default No) before starting the selected tunnel. Uses the macOS administrator dialog.",
        "vpn.disconnect_btn":      "Review the target, then ask for confirmation (default No) before stopping the selected tunnel. OpenVPN: only the process Sentinel started.",
        "vpn.import_config_btn":   "Load a WireGuard .conf or OpenVPN .ovpn as a profile. The file's location is remembered, not copied.",
        "vpn.disarm_ks_btn":       "Remove the kill switch's blocking rules (asks for your administrator password). Leaves its /etc/pf.conf anchor block and pf itself switched on.",
        "vpn.diagnostics_btn":     "Local, read-only snapshot of VPN tools, tunnels, route and DNS. No model, no administrator password.",
        "vpn.diagnostics_stop_btn": "Stop the running check and show the partial results.",
        "vpn.preview_action_btn":  "Show the plan and commands for the chosen action without running anything.",
        "vpn.action_box":          "Connect, Disconnect or Restart. Preview only; WireGuard profiles.",
        "vpn.build_btn":           "Render WireGuard configs and a deploy runbook locally. No model, no network.",
        "vpn.protocol_box":        "WireGuard, the OpenVPN TCP/443 fallback outline, or both. Used by Build Config and the Advisor only.",
        "vpn.host_input":          "VPS IP or DDNS name for the client Endpoint. Build Config and Advisor only; does not affect Connect.",
        "vpn.ssh_input":           "SSH user for the remote runbook. Build Config only.",
        "vpn.lan_input":           "Native mode: the home subnet that goes through the tunnel (AllowedIPs).",
        "vpn.egress_input":        "Server NIC for the NAT MASQUERADE rule (default eth0).",
        "vpn.question_input":      "Ask the Advisor (uses the selected model and the request guard).",
        "vpn.clear_btn":           "Clear all result tabs and the question.",
    })

    # Manager (Forge)
    app._set_tooltips({
        "manager.idea_input":   "Describe the agent you want to create in plain language.",
        "manager.provider_box": "Provider used to generate the agent spec.",
        "manager.model_box":    "Specific model.",
        "manager.analyze_btn":  "Analyse the idea and produce a JSON spec for review.",
        "manager.clear_btn":    "Clear the idea and the review. The Creation Log is kept.",
        "manager.approve_btn":  "Approve the spec — after you confirm, Forge writes an inactive scaffold file and two disabled registry rows. Nothing is loaded.",
        "manager.reject_btn":   "Discard the spec. Your idea text stays and nothing is written.",
        "manager.log":          "Log of this session: spec ready or invalid, stop, error, created, failed, rejected and cleared. Not saved when the app closes.",
    })
