# Sentinel

Sentinel is a local-first PySide6 desktop command centre for security, investigation, and controlled AI-assisted workflows. It supports local Ollama models and explicitly enabled cloud providers, records request usage and cost, and keeps each specialist workflow behind a clear panel and permission gate.

**Current release: v2.002.** The canonical value lives in [`VERSION`](VERSION)
and is shown beside **SENTINEL** in the app. See the
[versioning policy](docs/versioning.md) for numbering and release steps.

Sentinel's built-in roster is intentionally limited to eight agents:

| Display name | Key | Responsibility |
|---|---|---|
| Chat | `chat` | General conversation plus Writing, Coding, Summarize, and Rewrite tools |
| Trace | `osint` | Focused open-source research and source-led investigation planning |
| Bloodhound | `osint_heavy` | Deep OSINT dossiers plus read-only file discovery in user-selected folders |
| Beacon | `wifi` | Wi-Fi diagnostics and commands for networks the operator is authorised to test |
| Sentry | `sentry` | Read-only network anomaly watch (new devices, ARP spoofing, unexpected services) with a continuous background option |
| Bug Spray | `bug_bounty` | Public program radar with background updates, plus in-scope vulnerability analysis and report drafts |
| Tunnel | `vpn` | Real WireGuard/OpenVPN connect, profile-aware checks, action previews, and self-hosted VPN design |
| Forge | `manager` | Creates and reviews specifications for new agents and tools |

Writing and Coding are Chat tools, not standalone agents. Creative publishing, audiobook, health, investing, and sports-betting workflows are not part of the current Sentinel product.

## Run locally

Requirements:

- Python 3.11 or newer
- the packages in `requirements.txt` (runtime) or `requirements-dev.txt` (development and builds)
- Ollama for local inference, or an API key for any cloud provider you choose to enable

From the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python main.py
```

API keys are read from the process environment or from a `.env` file: the project's own when Sentinel runs from this checkout (`python main.py` or the installed thin launcher), `~/Library/Application Support/Sentinel/.env` for a packaged build, and `Sentinel Data/.env` in portable mode. Copy `.env.example` there, paste each key after its `=`, and restart. AI provider keys are read once, at startup. Step by step, with where to create each key: the Learning Centre lesson [API keys and new models](docs/training/api_keys.md). Supported provider variables include:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
DEEPSEEK_API_KEY
GOOGLE_API_KEY
KIMI_API_KEY
DASHSCOPE_API_KEY
DASHSCOPE_BASE_URL
```

Only enable a paid provider when you intend to use it. Sentinel checks provider permissions and configured budgets before a request, then records the resulting run and usage. Kimi's cached-input rate is priced separately from its base input rate (roughly 80% cheaper), so a request that reuses recent context costs less than the headline per-token estimate would suggest.

## Using the app

Choose an agent from the left sidebar. Chat uses the shared centre workspace; each specialist has its own panel with the inputs and actions relevant to that workflow. Provider and model controls remain explicit, and the recommended choice carries a **BEST FIT** badge in the dropdown, next to the entry itself, with the reason on hover.

Chat's Tool selector changes its system guidance:

- **General Chat** for open-ended assistance
- **Writing** for editing, tone, clarity, and structure
- **Coding** for code generation, explanation, debugging, and refactoring
- **Summarize** for condensation and key points
- **Rewrite** for rephrasing while preserving meaning

Type a message and press **Enter** to send; **Shift+Enter** inserts a newline
instead of sending. The transcript — labelled **Conversation** — shows every
message with a role and a timestamp (`YOU · 31 Aug 2026 · 10:15`).

Every agent, Chat included, has an **Auto-route** button next to its run
controls: it asks the router for a recommended provider and model for the
current input and applies the choice directly. Any provider or model other
than Ollama is a paid selection: the control turns amber while one is
selected, and every cloud entry says so on hover, so a cloud route is visible
before you run it, not only after the cost is logged.

Saved chats can be searched and filtered by agent. The two side rails split by what they carry rather than by left/right habit: the right rail is the live request inspector — Current Route, Cost, Budget, System, in that order, so the cards read top-to-bottom in the order a request actually happens — while API Keys, Model Updates and Actions (Cost history, Run log, Settings) sit in the left rail with the agent list, since those are global setup rather than per-request state. **Model Updates** asks every provider with a key for its live model list at each start (or on **Check now**), lists the models that appeared since the last check as rows you click to select, and **Update** brings in only the selected ones (`services/model_watch.py`). Being newer counts for nothing: the router can rank an updated model, but it wins a request only by fitting it best (the cheapest wins among equally fit models), and it takes an agent's BEST FIT only when it is rated at least as well *and* known to cost less. A cloud model with no price, or a price of zero, is never treated as cheap. Every sidebar tile is a small screen (`ui.widgets.ScreenCard`): a header strip with a status light and a short status, over aligned monospace readouts. Settings controls registered agents, tools, pricing, and provider permissions.

Three dots beside **SENTINEL** and the version pick the colour theme —
**Green (Matrix)**, **Red** or **Blue (Cyberpunk)** — and each dot is painted in
its own theme's accent, with a ring on the current one. **Settings → General →
Theme** offers the same choice with an explanation; it repaints live while the
dialog is open, and **Cancel** puts the previous theme back. The whole window is
one stylesheet authored in green and hue-rotated for the other two
(`ui/theme.py`), confined to the app's own greens, so semantic colour keeps its
meaning in every theme: a destructive button stays red, a paid route stays
amber, an informational badge stays blue, and Beacon's adapter "monitor mode OK"
stays green. Each theme also carries a small, bounded "vibe" (`ui/vibe.py`): a
faint moving texture in the empty Chat transcript — rain for green, a hex dump
for red, a receding grid for blue — that stops the moment there is something to
read; an underscore caret in the multi-line composer whose blink cadence differs
per theme (single-line fields keep the native caret); and, under the blue theme
only, HUD corner brackets around the focused composer.

While Sentinel is open it also places an item in the macOS menu bar — a shield
glyph that takes the menu bar's own colours. Its menu reads the current state
off the running window: whether a request is in flight and which agent is
running it, and what the session has cost so far. **Open Sentinel** brings the
window forward; **Quit Sentinel** closes it the same way the window's own close
does, so an in-flight request is cancelled and background work is shut down
rather than left running. Closing the window still quits Sentinel: the menu bar
item accompanies the app, and nothing hides to it.

The in-app **Learning Centre** is available from **More (•••)**. It contains a
guided Quick Start, full workspace and Settings reference, courses for all
eight agents, privacy/cost guidance, troubleshooting, multi-agent workflows,
practice exercises, current-interface screenshots, and the v3 advanced-tools
roadmap. It also includes the complete testing roadmap for every agent, shared
control, and release mode. Training source files live in `docs/training/`; they
are separate from the developer reference in `docs/agents/`.

For a removable, self-contained macOS copy, see
[`docs/portable_mode.md`](docs/portable_mode.md). Portable mode keeps settings,
history, logs and API-key storage on the removable volume and never silently
mixes them with the Lab checkout or Application Support. Its portable-only
**Settings → General → Emergency Reset** can erase Sentinel-owned data and API
keys after two confirmations. It is a privacy reset, not a Tails-style amnesic
session or forensic wipe: macOS, network equipment and providers may retain
separate records, and files deliberately exported elsewhere are not removed.

Bloodhound's **File Discovery** searches only locations you deliberately enter.
It supports folders on this Mac and owned macOS/Linux machines reachable by
SSH. Remote searches use SFTP with the SSH agent, `~/.ssh/config`, and strict
`known_hosts` verification; Sentinel stores no password or private key. Search
filters include full or partial name, extensions, size, and modified date.
Results show name, path, type, size, and modified time. Searches read metadata
only, never send file information to an AI provider, do not follow directory
links, and stop at safety limits. They cannot change files. **Open SSH Terminal**
hands an explicitly entered host to the operating system's normal SSH client
for interactive administration outside Sentinel.

Tunnel's **Connection Check** is local and read-only by default. It reports
installed WireGuard/OpenVPN tools, detected tunnels, recent WireGuard handshake
and transfer totals when the `wg` status tool permits them, the current default
route, and configured DNS servers. A selected profile can
be compared with the snapshot to explain interface, endpoint, port, handshake,
and routing findings. Endpoint and port are checked for usable profile values,
but the live peer endpoint is deliberately not queried or verified.

Tunnel's **Your IP & DNS** group is always visible and independent of the check.
**Local** reads your LAN and tunnel-interface addresses with no network contact;
**Check public IP** shows your exit IP, its location and network owner, and —
only with an `IPINFO_API_KEY` on a plan that returns the privacy object — any
VPN/proxy/hosting flag (the keyless `ipapi.co` fallback does not report one);
and **Run test**
runs a real DNS-leak test through bash.ws, resolving probe hostnames with your
configured resolvers and reporting which resolvers actually answered and whether
any leave your tunnel's network. It exercises the real resolver path but is not
proof of anonymity, and the local readout refreshes automatically after a
connect or disconnect.

Tunnel's **VPN Connection** brings a real tunnel up and down. Choosing a
WireGuard or OpenVPN profile and clicking **Connect** or **Disconnect** first
builds a target review (`services/vpn_execution.py`): the exact target and
command, the config's non-secret routing and DNS intent (keys discarded while
reading), warnings, rollback steps, and any blocker — a template, a missing
`wg-quick`/`openvpn` or config file, a config name `wg-quick` would reject, an
interface that is already up. A blocked action runs nothing; otherwise the review
is the confirmation dialog (default **No**), and `wg-quick`/`openvpn` then runs
through the macOS authorisation dialog off the interface thread. Afterwards Tunnel
re-reads local state — the `/var/run/wireguard` records, Sentinel's tracked
OpenVPN process and, for a full tunnel, which interface the route to the internet
uses — and reports **verified** or **not verified** in the **Execution** tab.
Every attempt, refused ones included, and every kill-switch change is appended to
the local audit log `data/logs/tunnel_audit.jsonl` (mode 600, no key material).
The post-change check does not verify a handshake, DNS, or leak behaviour. OpenVPN
shutdown refuses if Sentinel cannot identify its tracked process; it never stops
all OpenVPN processes by name. **Import
config…** loads a `.conf`/`.ovpn`, reads its real server endpoint out of the
file, and stores it as a connectable profile. A few
country-labelled example profiles ship as explicit templates — they are marked
`(template)` and the connect path refuses them until you import a real config or
set a real endpoint, so nothing pretends to be a working server it is not. An
optional **Kill switch** (macOS pf) can be armed to block all traffic except the
selected tunnel's endpoint, so a dropped tunnel cannot leak; it refuses to arm
for a template or when the endpoint cannot be resolved, and reports its recovery
command on failure.
**Action Preview** still shows the equivalent commands without running them, and
**Connection Check** stays read-only. It never
requests private key material. Public-IP and latency
checks are optional, name the external destinations, and require a separate
confirmation; none of these paths uses an AI model or incurs model cost.
Normal app close and Portable Emergency Reset cancel and finish an active check
before the UI is torn down. Packaged builds include a non-secret starter profile
catalog so Tunnel does not depend on source-tree files.

**Inspect config…** reads one explicitly selected WireGuard file locally and
returns only its interface, routing, DNS, peer and endpoint summary. Private and
pre-shared key values are discarded during parsing; the source file is not sent
to a model, Saved Chats, or the run log. When a recent Connection Check exists,
Tunnel compares the file's intended full/split routing and DNS with that snapshot.

### Safety boundaries

Beacon, Bug Spray, and Tunnel are intended for systems, networks, and programs the operator owns or is explicitly authorised to assess. Trace and Bloodhound should be used lawfully and with respect for privacy. Bloodhound never scans a local or remote machine automatically: the operator must enter each host and folder and already possess valid SSH access. Generated commands and findings require human review before execution or submission.

Forge writes an agent scaffold and inactive registry entries after review. Sentinel does not dynamically load it or add it to the sidebar; inspect, test, and deliberately integrate generated code before enabling it, especially when it adds tools or external access.

## Architecture

The main runtime is organised around:

- `main.py` — application window, Chat workflow, navigation, shared request controls
- `agents/` — the Chat, Trace, Bloodhound, Beacon, and Forge package repos
  (`agents/chat_agent/`, `agents/osint_agent/`, `agents/osint_heavy_agent/`,
  `agents/wifi_agent/`, and `agents/manager_agent/`)
- `agents/bug_spray/` — Bug Spray's nested repo: public program scanner,
  saved feed and the in-app `bug_bounty` message builder
- `agents/vpn_agent/` — Tunnel's VPN library, merged in-tree (formerly a
  standalone submodule): the `vpn` agent (`sentinel_chat_agent.py`), the
  `services/` stack (WireGuard/OpenVPN control, `privileged`, `killswitch`,
  `config_inspection`, DNS/latency/public-IP checks) and `server/` provisioning.
  The real connect path lives in `services/vpn_connection.py` and
  `services/openvpn_manager.py`
- `ui/panels/` — specialist panels for Trace, Bloodhound, Beacon, Bug Spray, Tunnel, and Forge
- `ui/theme.py` and `ui/vibe.py` — the three colour themes (one green
  stylesheet, hue-rotated) and the per-theme caret, empty-transcript backdrop
  and blue-theme focus brackets; `ThemeDots` in `ui/widgets.py` is the picker
  in the brand row
- `ui/tray.py` — the menu bar item; `ui/app_identity.py` — the app-menu name
  and Dock icon when running from source; `ui/appkit_guard.py` — the macOS 27
  `-[NSEvent clickCount]` guard (ported from Lab Hub) that must be installed
  before any tray menu can open
- `providers/` — the live OSINT source adapters Trace and Bloodhound call for
  Live Research/collection: `domain_lookup.py` (WHOIS, DNS, Team Cymru IP-to-ASN,
  Mnemonic passive DNS, crt.sh, Wayback Machine, and for IPs SANS DShield, Shodan
  InternetDB, plus key-gated IPinfo and Criminal IP), `email_lookup.py`,
  `username_lookup.py`, `whatsmyname.py`, `crypto_lookup.py` (Blockstream for
  Bitcoin, Blockscout for Ethereum, both keyless), `company_lookup.py` (GLEIF, opt-in ICIJ
  Offshore Leaks and CourtListener court dockets), `exposure_lookup.py`
  (ransomware.live, Ahmia, key-gated Intelligence X and DeHashed), and
  `alias_mint.py` (addy.io burner-alias minting, the one write-capable helper,
  user-triggered only). Every source is metadata-only and self-skips without its
  key when one is required.
- `services/agent_catalog.py` — canonical built-in roster and metadata
- `VERSION` and `services/app_version.py` — canonical public version and the
  exact development-build description shown by the app
- `services/registry.py` and `services/validator.py` — permissions and tool/provider checks; `registry.py` also has a `projects` table with full CRUD (`docs/projects_roadmap.md`, Stage 2) that nothing in the UI reads or writes yet
- `services/database.py` — SQLite schema and built-in registration
- `services/*_client.py` — local and cloud model clients
- `services/usage_tracker.py` and `services/run_logger.py` — cost and request lifecycle records
- `config/tool_prompts.json` — Chat tool instructions
- `data/sentinel.db` — local application data
- `assets/` — `icon.icns` and its source PNG for the macOS app bundle; used by
  `scripts/install_app.sh`, `scripts/build_app.sh`, and `Sentinel.spec`. Also
  `tray.png` / `tray@2x.png`, the menu bar template glyph drawn by
  `scripts/make_icon.py` and bundled by `Sentinel.spec`
- `scripts/app_launcher.c` — the app's executable, compiled by
  `scripts/install_app.sh` against the venv's libpython; runs `main.py` with
  the interpreter inside the bundle, so the process is named Sentinel
- `output/` — gitignored, generated-only. Currently holds leftover files from
  before the project was narrowed to the security roster (`launch_assets/` has
  a KDP listing, an ARC outreach email and a BookTok pitch — publishing-agent
  output, not something Sentinel builds). Safe to clear; nothing in this repo
  reads from it.

Built-in agents come from the canonical catalog. Forge-generated agents use the dynamic registry and remain separate from the built-in roster.

## Data and configuration

Development runs and the everyday thin launcher use the Lab project directory for writable data. Self-contained release builds use Sentinel's application-support directory. Runtime-path handling and initial seed copying live in `services/runtime_paths.py`.

### macOS launch modes

`./scripts/install_app.sh` installs the everyday live launcher. The bundle's executable, `Contents/MacOS/Sentinel`, is `scripts/app_launcher.c` compiled against the libpython the project's `.venv` was made from: Launch Services starts it, and it runs `main.py` with the venv's packages *in its own process* — nothing is exec'd and nothing is left behind, so a quit or crash simply ends the app. It runs directly from this Lab checkout and uses this folder's `data/`, `config/`, and `.env`, exactly like `python main.py`. Because the interpreter is linked rather than started, re-run the script after rebuilding the venv on a new Python minor version; patch upgrades need nothing. It quits a running Sentinel before replacing the bundle, through the app's own Quit, never by killing it.

`./scripts/build_app.sh` creates a self-contained release in `dist.noindex/` but does not install it. A self-contained build uses `~/Library/Application Support/Sentinel/` when launched. On first launch it renames existing `Sentinel Fork` application-support data in place; it never takes data from the archived `Sentinel AI` app. Installing with `./scripts/build_app.sh --install` explicitly replaces the thin launcher, so use that option only when you intend to switch modes. Source and frozen modes do not otherwise merge their data.

macOS names a process after the executable it is running, which is why the
interpreter runs inside the bundle rather than being handed off. The fork-and-exec
shim this replaced (`thin_launcher.c`, until 2026-10-07) exec'd `.venv/bin/python`:
the Dock, the app switcher, Force Quit and Activity Monitor all said `python`, and
Launch Services kept the exited launcher's bundle as a second, windowless Dock tile.
Neither is fixable from inside a process that is running `.venv/bin/python` — the
name is read from the executable at launch and never re-read. Python workers the
app starts through `sys.executable` come back through the same executable, so they
are named Sentinel too.

A plain `python main.py` from a terminal is still the venv's interpreter, so there
`ui/app_identity.py` does what it can from inside the process: it titles the menu
beside the Apple logo (writing `CFBundleName` before `QApplication()` exists) and
sets the Dock icon through AppKit. In the installed app and the frozen build it
leaves the bundle's own name and icon alone.

Both launch modes read the same canonical `VERSION`. The live launcher shows
the updated version on its next launch; packaged and portable copies keep the
version embedded at build time. Follow `docs/versioning.md` for every release
increment so the UI, bundle metadata and Lab monitor task remain aligned.

Important data includes saved chats, settings, usage, run history, and registry records. Do not replace or delete `data/sentinel.db` during an upgrade. Schema and roster changes should be applied through migrations that preserve user history.

Do not commit `.env`, credentials, generated reports containing sensitive information, or private investigation data.

## Testing

Run the automated suite from the activated environment:

```bash
pytest
```

The suite never contacts a model provider or the local Ollama daemon:
`tests/conftest.py` serves every client's offline `KNOWN_MODELS` and points
Chat's saved defaults at a temporary copy of `config/settings.json`. That keeps
results independent of which keys are in `.env` and what is pulled locally, but
it also means the suite cannot notice a provider renaming or retiring a model.
Check that by hand, before a release or when a panel opens on the wrong model:

```bash
.venv/bin/python scripts/check_live_models.py
```

It lists each provider's models (free; no prompt is sent), and reports every
recommended model and saved Chat default as found or missing. It also names
offline `KNOWN_MODELS` entries the live API no longer serves. Exit status 1
means something is missing.

The current manual acceptance checklist is in `tests/manual_test_cases.md`. It covers all eight built-in agents and verifies that Writing and Coding remain Chat tools rather than sidebar agents.
The [testing roadmap](docs/testing_roadmap.md) maps every shipped agent workflow
and shared control to automated, packaged-app, and owned-lab checks, with
priority and release gates. Sentinel's main test suite does not include the
separate Bug Spray companion-repository suite. (The VPN Agent code is now merged
in-tree; its connect/disconnect layer is covered by `tests/test_vpn_connection.py`.)
Tunnel's diagnostics and connection boundaries are covered by
`tests/test_vpn_diagnostics.py`, `tests/test_vpn_connection.py`,
`tests/test_vpn_execution.py` (the Connect/Disconnect gate), and the Tunnel
panel tests in `tests/test_ui_panels.py`.

## Further documentation

Agent-specific reference guides live in `docs/agents/`: `chat.md`, `osint.md`, `osint_heavy.md`, `wifi.md`, `bug_bounty.md`, `vpn.md`, and `manager.md`.
User training and the course roadmap live in [`docs/training/`](docs/training/).

Documents describing the workspace split or earlier architecture are historical records. They explain how features moved between projects; they do not define current Sentinel behaviour.
