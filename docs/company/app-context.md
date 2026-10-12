# App context

The only app-specific file the company agents read. Keep it short and true. Agents treat the
**Invariants** as hard rules. Evidence: README.md, CLAUDE.md, TODO.md, requirements*.txt,
Sentinel.spec, .github/workflows/tests.yml, scripts/, services/, main.py greps.

## Product
Sentinel (v2.002, `VERSION`) is a local-first PySide6 macOS desktop command centre for security,
investigation and controlled AI-assisted work, for one operator (the owner). Eight agents: Chat,
Trace (OSINT), Bloodhound (deep OSINT + read-only file discovery, local/SFTP), Beacon (Wi-Fi),
Sentry (read-only LAN watch), Bug Spray (bounty radar/report drafts), Tunnel (real WireGuard/OpenVPN
client, kill switch, Tor/proxy chain/MAC changer/server provisioning), Forge (agent scaffolds).
Local Ollama by default; cloud providers only when explicitly enabled; usage and cost recorded.
It must never claim to verify scope or authorisation (Bug Spray scope is declared, not enforced),
to keep VPN logs, or to run privileged actions without confirmation. Creative publishing, audiobook,
health, investing and betting workflows are not part of the product.

## Stack
- Python 3.11+ (CI uses 3.13); PySide6 (unpinned); SQLite (`data/sentinel.db`); PyInstaller for bundles.
- Pinned in requirements.txt: openai 2.31.0, httpx 0.28.1, requests 2.33.1, pydantic 2.13.0,
  tiktoken 0.12.0, pillow 12.2.0, Markdown 3.10.2, python-dotenv 1.2.2.
- Unpinned/ranged: anthropic, google-genai, python-whois, dnspython, psutil, keyring,
  paramiko>=3.4,<4, segno>=1.6,<2, cryptography>=43.
- Dev: pytest, pyinstaller (requirements-dev.txt). Native launcher `scripts/app_launcher.c`.
- Nested code: `agents/vpn_agent` (merged in-tree), `agents/sentry`, `agents/bug_spray`
  (git submodule per .gitmodules; own tests/requirements).

## Commands
- Install deps: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt`
- Run: `python main.py`; live launcher: `./scripts/install_app.sh`
- Run tests: `python -m pytest -q` (pytest.ini: `testpaths = tests`; CI sets `QT_QPA_PLATFORM=offscreen`,
  runs on ubuntu-latest and macos-latest with `--cov`). Separate suites: `agents/bug_spray/tests`
  and `agents/sentry/tests`, each run from its own directory. Manual: `tests/manual_test_cases.md`.
- Live model check (free, no prompts sent): `.venv/bin/python scripts/check_live_models.py`
- Lint / types: none configured (no ruff/mypy config or CI step found). `.ruff_cache` exists locally; UNVERIFIED whether ruff is used.
- Build / package: `./scripts/build_app.sh` (PyInstaller via `Sentinel.spec` into `dist.noindex/`;
  `--install` replaces the live launcher; `--skip-tests`); `./scripts/build_portable.sh` for portable mode.
- Release smoke test checklist: UNVERIFIED as a single list; see `docs/versioning.md`, `tests/manual_test_cases.md`,
  `docs/testing_roadmap.md`. Minimum: launch, version beside SENTINEL matches `VERSION`, one Local-only chat,
  quit, relaunch with `data/` intact (never replace `data/sentinel.db`).

## Docs and process files
README.md (long; HTTP API table moved out), `docs/versioning.md` (numbering/release steps; version log),
TODO.md and SUGGESTIONS.md (feed Lab Projects Monitor), `docs/agents/*.md`, `docs/training/` (Learning Centre),
`docs/qa_audit/`, `docs/honesty_audit.md`, `docs/portable_mode.md`, `docs/specs/`, `docs/adr/`, `docs/audit/`,
`docs/playbooks/`, AGENTS.md (pipeline), CLAUDE.md (app rules). No CHANGELOG file found; UNVERIFIED where
release notes go (versioning.md table is the nearest). UI entry: `main.py`, panels in `ui/panels/`.
Several docs (app_split, refactor_plan, workspace_structure) are historical, not current behaviour.

## Architecture hot spots
- `main.py` is a very large single module (window, Chat, nav; single-instance `QLocalServer` key near line 5233).
- Writable state: `data/` (sentinel.db, usage_log.json, chats, logs, backups, sentry), `config/` (settings.json,
  registry.json, pricing.json), `data/settings.local.json`. Path mode chosen in `services/runtime_paths.py`:
  checkout dir, `~/Library/Application Support/Sentinel/` (frozen), or `Sentinel Data/` (portable). Legacy
  `Sentinel Fork` data is migrated; `Sentinel AI` data is never touched.
- Cost gate: every paid request goes through authorisation, budget (Decimal), consent, usage and run logging
  (`services/usage_tracker.py`, `api_limits.py`, `run_logger.py`). Local-only mode substitutes Ollama.
- Tunnel: privileged execution is injected (`services/vpn_connection.py`, `openvpn_manager.py`, `vpn_execution.py`);
  pf kill switch in `agents/vpn_agent/services/killswitch.py`; connection runs in a cancellable worker.
- Threads: `threading.Lock` in `services/osint_catalog.py`; Qt worker threads in UI/VPN (details UNVERIFIED).
- Providers: `providers/*` (OSINT lookups, `intel_sources.py`), `services/*_client.py` (cloud LLMs, Ollama).
- Nested repos: `agents/bug_spray` submodule (`git submodule status` showed it uninitialised/`-` yet files exist; UNVERIFIED), CI needs `submodules: recursive`.

## Security surface
- Secrets: `.env` (gitignored; keys for OpenAI, Anthropic, DeepSeek, Google, Kimi, DashScope and ~25 OSINT/threat-intel
  services per `.env.example`) written by Settings -> API Keys / OSINT Keys; Bug Spray uses macOS Keychain via `keyring`.
  `.gitleaksignore` present. Never read or print values.
- Network: cloud LLM APIs, Ollama (local), OSINT sources (targets sent only after a consent prompt naming each),
  bash.ws DNS-leak test, IPinfo/ipapi.co, 5 bounty-platform directories, Hunter/HIBP/DeHashed etc., SFTP via paramiko.
- Privileged actions: wg-quick and openvpn via macOS authorisation dialog (`do shell script`) after explicit
  confirmation, cached sudo refused; `pfctl` kill switch with recovery `sudo pfctl -d`; Mullvad/ProtonVPN app control;
  MAC changer, Tor, proxy chain, server provisioning. Sentry optional background watch installs a launchd agent
  (`agents/sentry/sentry/watchd.py`) only when the user installs it. Bug Spray can start nmap in one click with no scope check.
- Untrusted input: WireGuard/OpenVPN imports (private keys discarded at parse time), Forge spec -> scaffold (string
  literals, compiled before save), OSINT responses, remote file listings (no file content to AI).
- Packaging: PyInstaller bundle (unsigned/notarised status UNVERIFIED), native C launcher, portable drive mode with Emergency Reset.
- Encrypted site backups use AES-GCM + scrypt (`cryptography`). No CVE/dependency audit step found in CI.

## Test bar
`pytest --collect-only -q` collects 1715 tests with 5 errors in the repo venv (missing `segno`: test_osint_keys,
test_ui_panels, test_vpn_ip_readout, test_vpn_servers_tab, test_vpn_worker_shutdown); CI installs requirements-dev.txt.
README quotes a committed test count (git log: 1,763); numbers drift. Tests are offline: `tests/conftest.py`
fakes model lists and uses a temp copy of settings, and fails if `config/settings.json` or `data/settings.local.json` change.
VPN/pf/osascript are never run in tests (fake runners). Coverage via `.coveragerc` (omits cli.py, profile_store.py,
scripts, builds); no numeric target found, UNVERIFIED. Flaky definition: none documented.

## Invariants
- Never print, copy or commit values from `.env`, keys or credentials; do not commit private investigation data.
- TODO.md and SUGGESTIONS.md keep their exact format: `- [ ] \`P1\` \`bug\` \`@me\` Task text` (P0-P3; categories
  from the legend; `@me` needs the user, `@ai` Claude can do; long reasoning under **Detail**; finished = `- [x]`).
  SUGGESTIONS statuses: IDEA, CONSIDERING, PLANNED, DONE, REJECTED.
- After code changes, finish by listing README/TODO/SUGGESTIONS entries now out of date; update only if told.
- State the scope (and a cheaper alternative) before big refactors; edit only what needs changing.
- Do not remove VPN Agent companion features (Tor, proxy chain, MAC changer, server provisioning).
- Never replace or delete `data/sentinel.db`; schema changes via migrations preserving history.
- No paid-provider call without permission, budget and consent; Local-only never contacts a cloud provider.
- Do not run privileged VPN/pf actions without explicit user confirmation; tests must not touch sudo/pfctl.
- Do not imply Bug Spray verifies scope, or that Tunnel keeps logs; person/phone data-broker lookup stays excluded.
- Do not touch `_archive`, `build`, `dist.noindex`.

## Optional roles installed
None detected in AGENTS.md beyond the standard pipeline; UNVERIFIED whether a model-validator role exists.
