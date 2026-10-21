# Sentinel

Sentinel is a local-first PySide6 desktop command centre for security, investigation, and controlled AI-assisted workflows. It supports local Ollama models and explicitly enabled cloud providers, records request usage and cost, and keeps each specialist workflow behind a clear panel and permission gate.

**Current release: v2.002.** The canonical value lives in [`VERSION`](VERSION)
and is shown beside **SENTINEL** in the app. See the
[versioning policy](docs/versioning.md) for numbering and release steps, and
[`CHANGELOG.md`](CHANGELOG.md) for the release-by-release, user-facing history.

Sentinel's built-in roster is intentionally limited to eight agents:

| Display name | Key | Responsibility |
|---|---|---|
| Chat | `chat` | General conversation plus Writing, Coding, Summarize, and Rewrite tools |
| Trace | `osint` | Focused open-source research and source-led investigation planning |
| Bloodhound | `osint_heavy` | Deep OSINT dossiers plus read-only file discovery in folders you select, on this Mac or over SSH |
| Beacon | `wifi` | Wi-Fi diagnostics and commands for networks the operator is authorised to test |
| Sentry | `sentry` | Read-only watch of your own network (new devices, address conflicts and gateway-MAC changes, new listening services, new outbound connections) with an optional background watch you install yourself |
| Bug Spray | `bug_bounty` | Public program radar (scans five platforms' public directories in the background once a platform is enabled; source checkout only) plus vulnerability analysis and report drafts for an asset you declare in scope (not verified or enforced) |
| Tunnel | `vpn` | Real WireGuard/OpenVPN connect, profile-aware checks, action previews, self-hosted VPN design, and detection and start/stop of the Mullvad and ProtonVPN apps; keeps no log |
| Forge | `manager` | Drafts agent specifications and, after your approval, writes an inactive scaffold and disabled registry rows |


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

API keys are read from the process environment or from a `.env` file: the project's own when Sentinel runs from this checkout (`python main.py` or the installed thin launcher), `~/Library/Application Support/Sentinel/.env` for a packaged build, and `Sentinel Data/.env` in portable mode. The easiest way is **Settings → API Keys**: paste a provider's key, press Save, and it is written to that `.env` and used from the next request, with no restart. (OSINT source keys have their own **OSINT Keys** tab.) To edit the file by hand instead, copy `.env.example` there, paste each key after its `=`, and restart; the file is read once, at startup. At launch Sentinel also re-tightens each `.env` it may read — the checkout's, the Application Support copy and the portable one — to owner-only (`0600`) permissions, skipping symlinks and reporting any file it cannot restrict by path only, so a key file left group- or world-readable by an older write or a hand copy does not stay that way; the tightening runs at launch rather than at import, so the test suite's import of `main` never touches the real key files (`services/runtime_paths.py`). Step by step, with where to create each key: the Learning Centre lesson [API keys and new models](docs/training/api_keys.md). Supported provider variables include:

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

Chat's Tool selector sets the conversation's system prompt; pick a different Tool before a later message and its prompt replaces the old one from that message on:

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
before you run it, not only after the cost is logged. Chat starts in Local only
mode (Options → Execution mode) with every paid provider unticked (Options →
Paid provider access), so a cloud provider picked in the run bar runs on Ollama
until you switch to Hybrid allowed or Cloud only and tick that provider; while
that substitution applies, the cost beside Run ends with "local only" and
hovering it names the substitution.

Auto-route only considers providers the agent **and** the tool may use
(`allowed_providers` in `config/registry.json`), so it never proposes a route
the validator would refuse. A model counts as available only when the provider
lists that exact id (or a dated snapshot / `-latest` form of it), and when a
recommended model is missing and another stands in, the route's reason says so.

Chat's **Options → Execution mode** is enforced at send time. In **Local only**
a cloud provider still selected in the run bar is never contacted and its model
name is never sent to Ollama: the request goes to Ollama with the saved Ollama
default model, the run-bar estimate reads `free · … · local only`, and hovering
it names the substitution. In **Hybrid allowed** or **Cloud only**, a cloud
provider whose permission is not ticked blocks the request: the estimate reads
`blocked · see route` (hover for the reason) and **Send** shows **Request
blocked** instead of failing silently.

Each Chat conversation is one saved file that is updated after every reply; saved chats can be searched by title (not by reply text) and filtered by agent and by Chat Project, and a chat can be renamed, assigned to a project or deleted from its row. The two side rails split by what they carry rather than by left/right habit: the right rail is the live request inspector — Current Route, Cost, Budget, System, in that order, so the cards read top-to-bottom in the order a request actually happens — while API Keys, Model Updates and Actions (Cost history, Run log, Settings) sit in the left rail with the agent list, since those are global setup rather than per-request state. **Model Updates** asks every provider with a key for its live model list at each start (or on **Check now**), lists the models that appeared since the last check as rows you click to select, and **Update** brings in only the selected ones (`services/model_watch.py`). **Routing chooses on value, not novelty** (`services/benchmarks.py`, `route_request`): each request is classified (coding, writing, reasoning, research, vision, general), every usable model is looked up on the public LMArena leaderboard for that kind of work (CC BY 4.0, refreshed at most daily, with a shipped snapshot in `config/lmarena_snapshot.json`), and the cheapest model rated within 20 points of the best wins. Each agent's BEST FIT is that same assessment for the agent's kind of work, over the providers that have keys. Requests are classified on whole words, not substrings ("withdraw" is not "draw", "postcode" is not "code"), and the prompt's estimated size (characters ÷ 4, the figure the cost estimate uses) drops every model whose context window cannot hold it. **The bill and the router price a model the same way** (`services/price_resolution.py`): its own row in Settings → Pricing, else the row of the model it is a dated snapshot of (`gpt-4o-2024-08-06` bills as `gpt-4o`), else its provider's `default` row, which holds that provider's dearest current rate so an unpriced model is over- rather than under-estimated against the budget caps. A rate of zero means unknown, never free; a cloud model with no usable price is never treated as cheap; an unrated model (including a local model with neither an inherited nor a measured rating) is not chosen over rated ones, except in Local only or Privacy first, where Sentinel's own scoring decides. Refresh the snapshot with `python -m services.benchmarks --snapshot`. **Local providers are a registry, not a name check** (`services/providers.py`, identical in Imprint — a drift test keeps the copies equal). Ollama and **LM Studio** are the two local providers; a provider is local because its spec says so, so LM Studio is priced as free, scored as private, needs no permission checkbox, never raises the paid-request dialog and is never counted as paid usage. An agent or tool that allows `ollama` allows every local runtime. Ollama's address comes from `OLLAMA_HOST`; LM Studio's is `http://localhost:1234/v1` (`LMSTUDIO_URL` overrides). With LM Studio's server on, its models are listed from `/api/v0/models` (state, quantisation, architecture, maximum context) and chat goes through `/v1/chat/completions`; with it off, `~/.lmstudio/models` is read and the models are listed as *on disk · server off*, and **Models → Start LM Studio server** (shown only then) runs `lms server start`. Before a local model runs, its file size × 1.2 is compared with free memory: *too big right now* warns, and a file that cannot fit this machine at all is refused. **Uncensored and abliterated models are offered in every agent and always tagged** — an amber ABLITERATED or UNCENSORED pill in the model menu and on the closed picker, with a note on hover — derived from the model's name and publisher (`providers.uncensored_tag`), so a new download is tagged without anyone remembering to. **The model picker groups local models** (`services/local_models.py`, identical in Imprint): sections *On this Mac · LM Studio*, *On this Mac · Ollama* and *Cloud*, each local row showing quant, state (loaded / on disk, loads on first use / on disk, server off / too big now), size, rating (*measured writing 1281 · code 1240*, *base model 1338 − 10*, *measuring… 12/80* or *unrated*) and €0, with the uncensored/abliterated pill on the row and on the closed picker. Every discovered local model is a router candidate whichever provider is selected, so BEST FIT can land on one from a cloud selection (an unrated one still never beats a rated one). Discovery runs on a background thread into a cached snapshot; the UI never fetches. **Settings → Local models** shows LM Studio/Ollama status, free RAM and a table (model, source, size, state, rating, used by) with **Start server** (`lms server start`, or a hint to start it in LM Studio when `lms` is missing). **Local models are rated from evidence only** (`services/local_ratings.py`, identical in Imprint, phase 3 of `docs/local_model_routing_plan.html` in the lab root). An unmodified build of a model LMArena rates (Gemma 3 27B, Qwen3 32B …, matched by name and size) **inherits** that rating less a quantisation penalty — 0 for Q8 and wider, 10 for 4–6 bit, 25 for 3 bit and below or an unknown quant — shown as *rated (base model)*. A fine-tune, merge, distill or abliteration inherits nothing and is **measured**: 80 prompts (`config/local_rating_prompts.json`, 20 each of writing, code, summarising and extraction) are answered by the local model and by the best-rated cloud model you have a key for, and the cheapest priced cloud model judges each pair twice, in both orders. The local model's share of verdicts becomes a rating gap with the Elo formula (400·log10(p/(1−p))) below the reference's LMArena rating, never above it; the result is stored per model, quant and kind of work with its reference, judge and date in `data/local_ratings.json`, where reference answers are cached so the next model does not pay for them again. Measured beats inherited for the kinds it covers; reasoning, research and vision stay unrated for local models. **A new local model is measured once, automatically, on a background thread** when discovery first sees it and it can run (server on, fits in memory). The spend guard is asked first: automatically, only providers already ticked under API permissions, with a key, and within every budget for the whole run's estimate × 1.5; otherwise the model stays unrated with the reason on hover and a **Measure** button in Settings → Local models, which asks one confirmation for the run. Re-measure is manual. Every judge and reference call is billed under the `local_ratings` agent like any request, and the run stops if it spends past what was authorised. In the router a rated local model enters the 20-point band at a known price of 0 (only for kind local; a zero cloud price still means unknown), and the reason says *(measured)* or *(rated (base model))* instead of *on LMArena*. **Hybrid routing: one check per new job, nothing switched without you** (`services/routing_advisor.py`, identical in Imprint, phase 4). **Settings → Models** sets the routing mode, with an override per agent: *Local, advise per job* (the default), *Suggest local*, *Local first*, *Cloud only*, *Local only* (the run bar's Execution mode follows it: Local only and Cloud only map across, the other three are Hybrid). When a Chat job is sent, the advisor compares the selected model with what the router would pick and names every limit separately: a **quality gap** (rated more than 20 points below the best for this kind of work), **context** (the prompt is longer than the model's context), a missing **capability** (vision, tools), **not runnable** (LM Studio server off, Ollama down, too big for free memory) or **unmeasured** (no rating yet: it suggests but does not urge). When local is good enough the job runs and the chat status line says so — *✓ Ran on … (local) · writing 1281, within 37 of best · saved ≈ €0.03 · today €0.41 saved*. When it isn't, the job is **held** on a banner above the run bar with the recommended model, its cost and its time, and three answers: **Switch for this job**, **Always for <kind> jobs**, **Keep local** (absent when local cannot run the job at all). A paid choice still passes the budget check and the external-API confirmation. *Always* becomes a rule per agent and kind of work (`data/routing_rules.json`) that the next such job follows without the banner — the confirmation still asks — and Settings → Models lists the rules with **Remove**. *Suggest local* offers a good-enough local model when a cloud one is selected, with what it would save; *Local first* runs a good-enough rated local model at €0 instead of the cloud selection. **When a run fails** (server down, out of memory, timeout) the banner offers the router's next candidate (`RouteDecision.fallbacks`): **Send to <provider>** (through the confirmation) or **Try** a smaller local model; nothing is resent by itself, and a stopped run offers nothing. **Local only and Privacy first never reach the cloud**: no cloud suggestion, no cloud fallback, and a remembered cloud rule is ignored. The **Local** row of the Cost card shows what running local saved today (the estimate for the cloud model the value rule would have picked) and what accepted suggestions spent (`data/routing_savings.json`); hover for the totals. The advisor reads caches only (ratings, prices, the local inventory, the last model scan) and runs on the UI thread without fetching. Only Chat is advised so far; the specialist panels keep their own provider and model. Every sidebar tile is a small screen (`ui.widgets.ScreenCard`): a header strip with a status light and a short status, over aligned monospace readouts. Settings controls registered agents, tools, pricing, and provider permissions.

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

**Stop** (or **Esc**) in Chat gives you control back at once. A model request
cannot be interrupted part-way, so the model may still finish in the background;
Sentinel never shows that reply. A cloud provider may still bill for it: when
the late reply arrives while Sentinel is open, its cost is recorded in Cost
history as a cancelled run (`cancelled-exact`, or `cancelled-estimated` when the
provider's token counts never arrived), and the Run log row keeps the status
`cancelled` with that cost. A local Ollama model may stay busy until it
finishes, so your next local request can wait behind it.

While Sentinel is open it also places an item in the macOS menu bar — a shield
glyph that takes the menu bar's own colours. Its menu reads the current state
off the running window: whether a request is in flight and which agent is
running it, and what the session has cost so far. **Open Sentinel** brings the
window forward; **Quit Sentinel** closes it the same way the window's own close
does, so an in-flight request is cancelled and background work is told to
stop. With work running, quitting takes up to about 2.5
seconds: Sentinel gives all background work that long, in total, to finish, then
exits and abandons whatever is still running. A cloud request abandoned this way
may still be billed by the provider, and Sentinel has no record of that cost,
because it is known only when the reply arrives. (An administrator action, such
as a Tunnel Connect, still asks **Wait** or **Quit anyway** first and is not part
of that limit.) The one exception is Sentry's optional background
watch: a launchd agent that you install and remove yourself, which keeps running
after Sentinel quits and after a restart. Closing the window still quits
Sentinel: the menu bar item accompanies the app, and nothing hides to it.

The in-app **Learning Centre** is available from **More (•••)**. It contains a
guided Quick Start, full workspace and Settings reference, courses for all
eight agents, privacy/cost guidance, troubleshooting, multi-agent workflows,
practice exercises, interface screenshots, and the v3 advanced-tools
roadmap. It also includes the complete testing roadmap for every agent, shared
control, and release mode. Training source files live in `docs/training/`; they
are separate from the developer reference in `docs/agents/`.

For a removable, self-contained macOS copy, see
[`docs/portable_mode.md`](docs/portable_mode.md). Portable mode keeps settings,
history, logs and API-key storage on the removable volume and never silently
mixes them with the Lab checkout or Application Support. Its portable-only
**Settings → General → Emergency Reset** can erase Sentinel-owned data and API
keys after two confirmations. Before erasing, it stops Sentinel's background
work and fences off anything still running, so a late result writes nothing into
the folder being erased. If any step fails, it reports **Reset refused** and
Sentinel does not quit, so a failed reset never looks like a successful one; if
the failure came part-way through erasing, the message says some data may
already be erased, so check the `Sentinel Data` folder before using it again.
It is a privacy reset, not a Tails-style amnesic
session or forensic wipe: macOS, network equipment and providers may retain
separate records, and files deliberately exported elsewhere are not removed.

Bloodhound's **File Discovery** searches only locations you deliberately enter.
It supports folders on this Mac and owned macOS/Linux machines reachable by
SSH. Remote searches use SFTP with the SSH agent and default keys,
HostName/User/Port/ProxyCommand from `~/.ssh/config` (IdentityFile and
ProxyJump are ignored), and strict `known_hosts` verification (a new host must
be accepted once with your normal SSH client first); Sentinel stores no
password or private key. Search filters include full or partial name,
extensions, size, and modified date. Results show name, path, type, size, and
modified time. Searches read metadata only, never send file information to an
AI provider, skip every symbolic link (to folders and to files), and stop at
safety limits. They cannot change files. **Open SSH Terminal**
hands an explicitly entered host to the operating system's normal SSH client
for interactive administration outside Sentinel.

Tunnel's **Connection Check** is local and read-only by default. It reports
installed WireGuard/OpenVPN tools, detected tunnels, recent WireGuard handshake
and transfer totals when the `wg` status tool permits them, the current default
route, and configured DNS servers. A **VPN apps** card lists the Mullvad and
ProtonVPN apps if they are installed, with the state each reports (Mullvad
through `mullvad status`, ProtonVPN through its macOS system VPN service,
`scutil --nc list`; `services/vpn_apps.py`); Tunnel only reads it and never logs
in to or connects either app. A selected profile can
be compared with the snapshot to explain interface, endpoint, port, handshake,
and routing findings. Endpoint and port are checked for usable profile values,
but the live peer endpoint is deliberately not queried or verified. Its OpenVPN
line counts any process named openvpn, whereas Connect and Disconnect act only on
the process Sentinel started. Known limitation: on macOS a tunnel often appears
as a utun name, so right after a verified Sentinel Connect the profile comparison
may still say the selected tunnel is not active (not yet confirmed on a Mac).

Tunnel's **Your IP & DNS** group is always visible and independent of the check.
**Local** reads your LAN and tunnel-interface addresses with no network contact;
**Check public IP** shows your exit IP, its location and network owner, and —
only with an `IPINFO_API_KEY` on a plan that returns the privacy object — any
VPN/proxy/hosting flag (the keyless `ipapi.co` fallback does not report one);
and **Run test**
runs a DNS-leak test through bash.ws, looking up probe hostnames with the
resolvers in your system resolver configuration and reporting which resolvers
answered and whether any sit on a different network than your exit IP. **Check
public IP** and **Run test** start as soon as they are pressed, with no extra
confirmation. The test is a clue, not proof of anonymity: macOS can use
per-interface resolvers it does not see, and a public resolver reached through
the tunnel can read as a possible leak. The local readout refreshes
automatically after a connect or disconnect.

Tunnel's **VPN Connection** brings a real tunnel up and down. Choosing a
WireGuard or OpenVPN profile and clicking **Connect** or **Disconnect** first
builds a target review (`services/vpn_execution.py`): the exact target and
command, the config's non-secret routing and DNS intent (keys discarded while
reading), warnings (including one when the Mullvad or ProtonVPN app is already
connected, because two VPNs would stack), rollback steps, and any blocker — a template, a missing
`wg-quick`/`openvpn` or config file, a config name `wg-quick` would reject, an
interface that is already up. A blocked action runs nothing; otherwise the review
is the confirmation dialog (default **No**), and `wg-quick`/`openvpn` then runs
through the macOS authorisation dialog off the interface thread. The dialog is
always used, even when an unrelated sudo ticket is cached, and it is the only
prompt for the password (Tunnel never stores it). A WireGuard config's
PreUp/PostUp/PreDown/PostDown lines and an OpenVPN config's script and plugin
directives (`up`, `down`, `route-up`, `plugin`, `script-security`, …) run as
administrator; the Connect review lists them in their own section, and a second
confirmation, default **No**, follows. They are not listed on Disconnect or in
Inspect config…. Afterwards Tunnel re-reads local state — the `/var/run/wireguard`
records, Sentinel's tracked OpenVPN process and, for a full tunnel, which
interface the route to the internet uses — and reports **verified** or **not
verified** in the **Execution** tab.
Tunnel keeps no log: no attempt, refusal, declined confirmation or
kill-switch result is written to disk. Key fields, inline key blocks and long
key-like strings are scrubbed from tool output, errors and refusal reasons
before they are shown. The post-change check does
not verify a handshake, DNS, or leak behaviour. OpenVPN shutdown refuses if
Sentinel cannot identify its tracked process; it never stops all OpenVPN
processes by name. Identity is the full, untruncated `ps -ww` command line (Sentinel's `--daemon sentinel-ovpn` tag and its own pid file), so it holds in packaged and portable builds whose data path contains spaces, and `openvpn` is run by absolute path because the authorisation dialog's PATH has no Homebrew directory. OpenVPN is a basic client: configs that prompt for a username
and password, pushed DNS and live status are not supported. OpenVPN's start-up
log is read for the tunnel device name and then deleted; Tor's is deleted the
same way, and Sentinel removes logs older versions left behind. **Import
config…** loads a `.conf`/`.ovpn`, reads its real server endpoint out of the
file, and stores it as a connectable profile. The file's location is remembered,
not a copy. Imported profiles, server sites, the Tor data folder and kill-switch state now
live in Sentinel's data folder (`vpn/`), so Portable mode carries them and
Emergency Reset covers them; on first run an existing `~/Library/Application
Support/VPN Agent/` is copied across (never moved or deleted). On a Mac with no
profile file yet, the first import creates one holding only the imported profile,
so the starter profiles disappear. A few
country-labelled example profiles ship as explicit templates — they are marked
`(template)` and the connect path refuses them until you import a real config or
set a real endpoint, so nothing pretends to be a working server it is not. An
optional **Kill switch** (macOS pf) can be armed to block network traffic except
loopback, the tunnel interface present at arm time (WireGuard's, or the OpenVPN
device Sentinel stored when it connected), the tunnel's own
transport to the selected server (WireGuard UDP; OpenVPN per its first `remote`
line, then its `proto` line;
port from the profile, the config, or the protocol default), DHCP and the local
network, so a dropped tunnel cannot leak. Arm asks for confirmation (default
**No**), which states what stays open and prints the recovery command, and then
for the administrator password; it refuses for a template, when the endpoint
cannot be resolved, when the generated rules fail pf's parse check, while the
Mullvad or ProtonVPN app is connected (or Mullvad's state cannot be read), and
for an OpenVPN tunnel whose device Sentinel did not learn when it connected.
Known limitations: arming with a live OpenVPN tunnel up is covered only by
injected tests so far; the first arm adds
an anchor block to `/etc/pf.conf` and switches pf on, which Disarm does not undo;
Sentinel does not disarm on quit or Emergency Reset.
**Action Preview** still shows the equivalent commands without running them, and
**Connection Check** stays read-only. It never
requests private key material. The optional public-IP and latency checkbox in
Connection Check names `api.ipify.org` and `1.1.1.1` and requires a separate
confirmation (default **No**); Your IP & DNS → Check public IP and Run test
contact IPinfo (with a key) or `ipapi.co`, and `bash.ws`, as soon as they are
pressed. None of these paths, nor Connect/Disconnect, uses an AI model or incurs
model cost; only Ask Advisor does. Advisor requests create no Saved Chats entry
and no Run log row (`NO_CONTENT_LOG_AGENTS` in `main.py`); only the usage row
stays (agent, provider, model, token counts and price, no text), because the
budget caps are computed from it. The question still goes to the selected
provider, which may keep its own records.
Normal app close and Portable Emergency Reset cancel every Tunnel worker. The
checks (diagnostics, IP, DNS, Advisor) share the quit's 2.5-second allowance and
are left to finish on their own if they outlast it; nothing is force-stopped. A
Connect in flight gets its own two seconds and is not interrupted (the macOS
prompt and any command already handed to macOS continue); its result is
dropped, so it may not be shown.
While the Tunnel panel is on screen, a health monitor checks every 30 seconds,
using only this Mac's own state, whether the selected profile's tunnel is still
up and whether the DNS resolvers changed, and warns in the status line
("TUNNEL DROPPED", "DNS resolvers changed"). Until this change the checks
were scheduled but never actually ran, so these warnings may appear for the
first time.
Packaged builds include a non-secret starter profile
catalog so Tunnel does not depend on source-tree files.

Tunnel's **VPN Apps** tab starts and stops the Mullvad and ProtonVPN apps'
own tunnels (`services/vpn_app_control.py`, `ui/panels/vpn_apps_tab.py`).
Mullvad is driven through its official command-line tool (`mullvad connect`,
`disconnect`, `relay set location`), with a country or city picker filled from
`mullvad relay list`. Login stays in the Mullvad app: Tunnel never logs in and
never passes or keeps an account number (`mullvad account get` is reduced to
"logged in until <date>"), and Connect refuses unless the account is verifiably
logged in, because Mullvad blocks all traffic when told to connect while logged
out. ProtonVPN is started and stopped through its macOS system VPN service
(`scutil --nc start|stop`); it connects to the server Proton used last, Tunnel
cannot choose a server and never sees a Proton login. Each Connect and
Disconnect asks first (default **No**), runs off the interface thread, logs
nothing, waits about fifteen seconds for the app to report Connected and says
so honestly if it does not. Traffic protection is still not verified: check
**Your IP & DNS** afterwards. Both paths are covered by stubs only so far; live
checks against real Mullvad and Proton setups are open TODOs.

**Inspect config…** reads one explicitly selected WireGuard file locally and
returns only its interface, routing, DNS, peer and endpoint summary. Private and
pre-shared key values are discarded during parsing; the source file is not sent
to a model, Saved Chats, or the run log. When a recent Connection Check exists,
Tunnel compares the file's intended full/split routing and DNS with that snapshot.

### Safety boundaries

Beacon, Bug Spray, and Tunnel are intended for systems, networks, and programs the operator owns or is explicitly authorised to assess. Bug Spray does not verify a program or scope; the Program and Scope Type fields are declared by the operator. Trace and Bloodhound should be used lawfully and with respect for privacy. Bloodhound never scans a local or remote machine automatically: the operator must enter each host and folder and already possess valid SSH access. Before Bloodhound sends a target to public sources it lists them and asks, with No as the default; image metadata (EXIF, GPS) is sent to a model only if you tick the box. Generated commands and findings require human review before execution or submission. One exception to flag: Bug Spray's **Run Nmap** with an empty command field builds `nmap -sV -sC -T4 --open <host>` and starts it in the same click; only nmap can start, and no scope check runs. Beacon's Kali Command Builder refuses injection operations on an adapter whose chipset cannot inject: instead of a command sequence it returns an explanation naming the limitation and a supported-adapter hint, and passive operations are unaffected.

Beacon's **AI Analysis** and Sentry's **Explain findings with AI** are off by
default. Ticking them sends the scan output (network names, signal and security)
or the findings (LAN IPs, MAC addresses, process names) to the selected provider
and model, which may be a paid cloud one; each checkbox's hover text says so.
Prefer a local model.

Forge writes an agent scaffold and inactive registry entries after review. Every value from the spec is written into the scaffold as a Python string literal, and the module is compiled before anything is saved; if it would not parse, creation fails and neither the file nor the registry rows are left behind. Sentinel does not dynamically load it or add it to the sidebar; inspect, test, and deliberately integrate generated code before enabling it. The scaffold contains only the agent's name, description and system prompt; its registry rows stay disabled, and Chat's tool list is fixed, so the generated prompt cannot be used from Chat.

## Architecture

The main runtime is organised around:

- `main.py` — application window, Chat workflow, navigation, shared request controls
- `agents/` — the built-in agent packages, tracked in-tree like `vpn_agent`
  so a fresh clone imports `main.py` and collects the test suite with nothing
  else to fetch: `agents/chat_agent/` (Chat), `agents/osint_agent/` (Trace),
  `agents/osint_heavy_agent/` (Bloodhound), `agents/wifi_agent/` (Beacon),
  `agents/manager_agent/` (Forge) and `agents/sentry/` (Sentry's collectors,
  baseline, engine, CLI, its own `tests/` and the `launchd/` template for the
  Continuous Background Watch). Each keeps its own README, TODO and SUGGESTIONS.
  These were gitignored nested repos with no remote until 2026-10-09
- `_archive/` — gitignored. `_archive/nested_git/` holds the six packages'
  former nested `.git` directories, kept rather than deleted; nothing reads
  from it
- `agents/bug_spray/` — Bug Spray's nested repo: public program scanner,
  saved feed and the in-app `bug_bounty` message builder
- `agents/vpn_agent/` — Tunnel's VPN library, merged in-tree (formerly a
  standalone submodule): the `vpn` agent (`sentinel_chat_agent.py`), the
  `services/` stack (WireGuard/OpenVPN control, `privileged`, `killswitch`,
  `config_inspection`, DNS/latency/public-IP checks); the `server/`
  server provisioning, deploy, backup, Tor, proxy-chain, MAC and health-monitor
  code, reached from Tunnel's **Privacy** and **Servers** tabs (every action there
  is gated and none of it is logged; `server/paths.py` locates the state folder under
  Sentinel's data folder).
  The real connect path lives in `services/vpn_connection.py` and
  `services/openvpn_manager.py`
- `services/vpn_apps.py` and `services/vpn_app_control.py` — Tunnel's
  read-only Mullvad/ProtonVPN detection and their start/stop through Mullvad's
  CLI and ProtonVPN's macOS VPN service; the tab is `ui/panels/vpn_apps_tab.py`.
  `services/tunnel_logs.py` removes logs older versions left behind at launch
- `ui/panels/` — specialist panels for Trace, Bloodhound, Beacon, Sentry, Bug Spray, Tunnel, and Forge
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
  Mnemonic passive DNS, crt.sh and the Wayback Machine for domains, and for IPs
  SANS DShield and Shodan InternetDB, plus key-gated IPinfo and Criminal IP),
  `intel_sources.py` (the other key-gated threat-intelligence services for
  domain and IP runs: AbuseIPDB, GreyNoise, VirusTotal, AlienVault OTX, Shodan,
  Censys, SecurityTrails, DomainTools, URLScan and Hunter), `email_lookup.py`
  (EmailRep, Gravatar, Have I Been Pwned, BreachDirectory, Hunter),
  `username_lookup.py` (URLScan, GitHub, Keybase), `company_lookup.py` (GLEIF and
  CourtListener court dockets, key-gated OpenSanctions), `exposure_lookup.py`
  (Ransomware.live, Ahmia, key-gated Intelligence X, DeHashed, Snusbase and
  LeakCheck), `key_check.py` (the OSINT Keys tab's key checks) and `alias_mint.py`
  (addy.io burner-alias minting, the one write-capable helper, user-triggered
  only). Three adapters are called by Bloodhound only: `whatsmyname.py`,
  `crypto_lookup.py` (Blockstream for Bitcoin, Blockscout for Ethereum, both
  keyless) and the ICIJ Offshore Leaks search in `company_lookup.py`. Sources
  return text metadata only (the WhatsMyName sweep requests each profile page but
  keeps only whether the account exists), except BreachDirectory, whose reply is
  passed through unfiltered; a source that needs a key is left out until the key
  is saved.
- `services/agent_catalog.py` — canonical built-in roster and metadata
- `AGENTS.md` and `CLAUDE.md` — instructions for coding agents working in this
  repo: `AGENTS.md` is the engineering pipeline (roles, phases, owner approval
  gates), `CLAUDE.md` adds this app's own rules (TODO/SUGGESTIONS format,
  working rules, current goal). App facts for that pipeline live in
  `docs/company/app-context.md`, and the per-role playbooks in `docs/playbooks/`
- `VERSION` and `services/app_version.py` — canonical public version and the
  exact development-build description shown by the app
- `services/registry.py` and `services/validator.py` — permissions and tool/provider checks; `registry.py` also has a `projects` table behind Chat Projects (an active-project picker in the run bar, a project filter and "Assign to project…" in History; usage is attributed to the project). Its instructions, default agent/provider/model and budget columns are not read by anything yet (`docs/projects_roadmap.md`, Stage 2), and there is no UI to rename, archive or delete a project
- `services/database.py` — SQLite schema and built-in registration
- `services/*_client.py` — local and cloud model clients
- `services/usage_tracker.py` and `services/run_logger.py` — cost and request lifecycle records
- `config/tool_prompts.json` — seeds the `tools` table with the Chat tool instructions when the database is first created; the table's `system_prompt` wins afterwards (the JSON is only a fallback for an empty prompt), and its `recommended_*` fields are not used for the five built-in Tools
- `config/settings.json` — the shipped settings defaults (each provider's Chat
  model). Tracked and bundled; the app never writes it.
- `data/settings.local.json` — this machine's own picks (the Chat model chosen
  per provider, the routing priority), laid over those defaults on every read
  and ignored by git (`services/settings_store.py`). A checkout uses its own
  `data/`; a self-contained or portable build keeps both files in its
  user-data folder. The first launch after this change moves any pick an
  older Sentinel had written into `config/settings.json` across, once, and
  puts the tracked file back to its committed content.
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

Built-in agents come from the canonical catalog. Forge-generated agents use the dynamic registry and never reach the sidebar or the catalog; Settings → Agents and Tools list them alphabetically among the built-ins, without a marker.

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
Chat's saved defaults at a temporary copy of `config/settings.json`, with an
empty override, so neither the shipped defaults nor your own picks in
`data/settings.local.json` are read or written — the run fails if either file
changes. That keeps
results independent of which keys are in `.env` and what is pulled locally, but
it also means the suite cannot notice a provider renaming or retiring a model.
Check that by hand, before a release or when a panel opens on the wrong model:

```bash
.venv/bin/python scripts/check_live_models.py
```

It lists each provider's models (free; no prompt is sent), and reports every
recommended model and saved Chat default (shipped default or your own pick) as found or missing. It also names
offline `KNOWN_MODELS` entries the live API no longer serves. Exit status 1
means something is missing.

The current manual acceptance checklist is in `tests/manual_test_cases.md`. It covers all eight built-in agents and verifies that Writing and Coding remain Chat tools rather than sidebar agents.
The [testing roadmap](docs/testing_roadmap.md) maps every shipped agent workflow
and shared control to automated, packaged-app, and owned-lab checks, with
priority and release gates. Sentinel's main test suite does not include the
separate Bug Spray companion-repository suite (`agents/bug_spray/tests`) or
Sentry's engine tests (`agents/sentry/tests`); run each from its own directory.
`keyring`, which the Bug Spray companion imports for its Keychain-only secrets, is in
`requirements.txt`. (The VPN Agent code is now merged in-tree; its connect/disconnect layer is
covered by `tests/test_vpn_connection.py`.)
Tunnel's diagnostics and connection boundaries are covered by
`tests/test_vpn_diagnostics.py`, `tests/test_vpn_connection.py`,
`tests/test_vpn_execution.py` (the Connect/Disconnect gate),
`tests/test_vpn_killswitch.py`, `tests/test_vpn_ip_readout.py`,
`tests/test_vpn_worker_shutdown.py`, `tests/test_vpn_apps.py` and
`tests/test_vpn_app_control.py` (Mullvad and ProtonVPN detection and control),
`tests/test_tunnel_keeps_no_logs.py` (no Tunnel log file, start-up log or
Advisor chat/run row is left behind), and the Tunnel panel tests in
`tests/test_ui_panels.py`. These use fake privileged runners and probes: no
automated test runs wg-quick, openvpn, pfctl, `mullvad`, `scutil --nc` or the
macOS password dialog.

## Further documentation

Agent-specific reference guides live in `docs/agents/`: `chat.md`, `osint.md`, `osint_heavy.md`, `wifi.md`, `sentry.md`, `bug_bounty.md`, `vpn.md`, and `manager.md`. Each in-tree package under `agents/` also has its own README.
User training and the course roadmap live in [`docs/training/`](docs/training/).
[`docs/qa_audit/`](docs/qa_audit/README.md) is the promise-by-promise audit of 2026-10-09: one requirements-traceability table per agent plus one for the shared controls, each promise with file:line evidence, cross-document contradictions and a must-fix list. Defects D1–D7 found by it are fixed (kill-switch transport, OpenVPN identity with spaces in the path, Local only, Forge scaffold rendering, Beacon/Sentry AI default-off, cancellable Tunnel connection worker, selector hover text); the audit tables record the state before those fixes.

Documents describing the workspace split or earlier architecture are historical records. They explain how features moved between projects; they do not define current Sentinel behaviour.
