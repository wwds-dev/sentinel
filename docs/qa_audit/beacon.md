# Beacon (`wifi`) — Requirements-Traceability Audit

Repo: `/home/claude/wwds-dev/sentinel` (read-only audit; nothing executed or modified)

## 1. Verdict

Beacon's safety architecture holds up: every subprocess call it actually issues
(`system_profiler`, `networksetup -listallhardwareports`, `route -n get default`,
`ping`) is read-only or harmless, the Kali command builder only ever produces
text — it is never passed to `subprocess`/`exec` anywhere in the repo — and
Connection Preflight genuinely changes nothing. The injection-safe placeholder
logic (`_safe_bssid`/`_safe_channel`/`_safe_essid`) is real and well-reasoned.
However, several concrete promises do not match the shipped code. The most
consequential is a privacy-relevant default-behaviour reversal: both
`docs/agents/wifi.md` and `docs/training/beacon.md` promise AI interpretation
is "off by default" and advise preferring a local model with redaction before
any cloud route, but the panel ships with the AI checkbox **checked by
default** and defaulting to the **cloud** provider `anthropic` (inherited,
never overridden) — so a first-time run of any scan mode sends raw, unredacted
SSIDs/signal/security data to a cloud model with no prompt. Second, the
**Interface** selector is fully decorative: none of Interface Info, Scan
Networks, Signal Monitor or Ping Test ever reads `interface_box.currentText()`,
so picking `en1` vs `en0` has zero effect. Third, "Signal Monitor" is not a
monitor at all — it issues the identical one-shot `system_profiler` call used
by Scan Networks, with no loop or polling, contradicting the training doc's
"follows signal conditions over time." Fourth, the documented Help button
(`wifi.help_btn`) does not exist on the panel — the tooltip is dead and
silently dropped. Fifth, the inject-capability refusal in
`build_kali_commands()` only guards the "Deauth Attack" operation; "Handshake
Capture" also contains a packet-injection step and is generated unconditionally
regardless of adapter capability. The macOS-version-dependency documentation
is stale: the `airport` binary and its `AIRPORT` constant are dead code never
referenced anywhere outside their own definition — `docs/agents/wifi.md`'s
"Requirements" line names a dependency the shipped code does not use. The
"Under the hood" file-path table in that same doc also predates the
`main.py` → `ui/workers.py` refactor and the `wifi_agent.py` → `wifi_agent/`
package change.

## 2. Counts

| Status | Count |
|---|---|
| IMPLEMENTED | 33 |
| PARTIAL | 4 |
| STUB | 1 |
| MISSING | 8 |
| NOT CODE-VERIFIABLE | 4 |
| **Total promises audited** | **50** |

## 3. Promise table

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 1 | Agent key `wifi`, display name "Beacon" (README roster) | IMPLEMENTED | `services/agent_catalog.py:46-49`; `ui/panels/wifi.py:62` | Matches exactly. |
| 2 | "for networks the operator is authorised to test" (README responsibility line) | NOT CODE-VERIFIABLE | — | A policy/legal condition; no technical authorisation gate exists anywhere in the files audited, only warning text. |
| 3 | Safety boundary: Beacon intended for owned/authorised systems (README) | NOT CODE-VERIFIABLE | `README.md:196` | Text-only; nothing enforces it. |
| 4 | "Generated commands... require human review before execution" (README) | IMPLEMENTED | `agents/wifi_agent/__init__.py:333-439` (grep confirms `cmds` is never passed to `subprocess`) | Structurally guaranteed: the app has no code path that executes a Kali command. |
| 5 | Live macOS diagnostics need no external adapter (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:384-399` | None of the 4 live-mode commands touch USB/adapter code. |
| 6 | Kali planning detects supported USB adapters (docs/agents/wifi.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:205-218` `detect_usb_adapters()` | — |
| 7 | Generates reviewable Kali command sequences (docs/agents/wifi.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:333-439` `build_kali_commands()` | — |
| 8 | "Sentinel does not execute these sequences" (docs/agents/wifi.md) | IMPLEMENTED | repo-wide grep for `build_kali_commands`/`cmds` | Only ever displayed, saved to a `.txt`, or sent as prompt text to an LLM — never subprocess'd. |
| 9 | Mode control: 5 named options (docs/agents/wifi.md Inputs table) | IMPLEMENTED | `ui/panels/wifi.py:89-92` | — |
| 10 | Interface control selects which interface diagnostics use (docs/agents/wifi.md; training doc; tooltip) | MISSING | `ui/panels/wifi.py:97-99`, `370-404` | Widget exists and is populated, but `interface_box.currentText()` is never read anywhere in `run()` or any command builder — selection has no effect on any executed command. |
| 11 | Target Host used by Ping Test (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:390-397` | — |
| 12 | Kali sub-form (Operation/Adapter/BSSID/Channel/ESSID), hidden unless Kali mode (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:108-142, 288-291` | — |
| 13 | AI interpretation "optional collapsed section, off by default" (docs/agents/wifi.md) | PARTIAL | `ui/panels/wifi.py:184-186` | It is a plain `QCheckBox`, not a collapsible section, and `setChecked(True)` — opposite of "off by default." |
| 14 | AI interpretation sends subprocess output to the selected LLM (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:460-464` | Raw output is interpolated verbatim into the prompt. |
| 15 | Detect Adapters: scan USB for known adapters (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:293-346` | — |
| 16 | Run Preflight: read interfaces/default route/USB, assign roles, show risk, change nothing (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:348-367`; `agents/wifi_agent/__init__.py:257-292` | Only read-only subprocess calls involved. |
| 17 | Run/Stop execute or cancel (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:370-404, 526-531` | — |
| 18 | "Results reveal Save and Clear controls" (docs/agents/wifi.md) | PARTIAL | `ui/panels/wifi.py:270-277` | Both buttons are always visible from construction; only `save_btn`'s *enabled* state toggles. Nothing is actually hidden/revealed. |
| 19 | "Use the shared Help button for docs" (docs/agents/wifi.md) | MISSING | `ui/panels/wifi.py` (`_build()`, full file); `ui/tooltips.py:97`; `main.py:464-481` | No `help_btn` attribute exists anywhere on `WifiPanel`. The `wifi.help_btn` tooltip is silently dropped by `_set_tooltips` (`getattr(panel, "help_btn", None)` is `None`). |
| 20 | Outputs as readable cards; local commands show raw findings (docs/agents/wifi.md) | IMPLEMENTED | `ui/widgets.py:700-789`; `ui/panels/wifi.py:453-458` | — |
| 21 | AI output split into Summary/Network Findings/Security Observations/Recommendations (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:579-608`; `agents/wifi_agent/__init__.py:164-180` | — |
| 22 | Kali planning shows a reviewable command sequence (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:406-431` | — |
| 23 | Raw model text behind a collapsed disclosure (docs/agents/wifi.md) | IMPLEMENTED | `ui/widgets.py:727-739, 788` | `_raw_btn`/`_raw_box` default hidden, toggled on click. |
| 24 | Side indicators: adapter/chipset/monitor/inject/signal/security (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/wifi.py:213-266, 610-640` | — |
| 25 | `detect_usb_adapters()` parses `system_profiler SPUSBDataType -json` against `KNOWN_ADAPTERS` (docs/agents/wifi.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:205-218, 295-308` | Exact match. |
| 26 | `network_interface_status()`/`build_connection_preflight()` are read-only (docs/agents/wifi.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:245-254` | Only `networksetup -listallhardwareports` and `route -n get default`; neither mutates state. |
| 27 | `build_kali_commands()` "refuses injection ops on adapters that can't inject" (docs/agents/wifi.md) | PARTIAL | `agents/wifi_agent/__init__.py:362-383` | The refusal check exists only for "Deauth Attack." "Handshake Capture" also emits an injection-class command and is generated unconditionally regardless of the adapter's `inject` flag. |
| 28 | Live modes run via `SubprocessWorker` (QThread) (docs/agents/wifi.md) | IMPLEMENTED | `ui/workers.py:260-286`; `ui/panels/wifi.py:401` | — |
| 29 | AI Analysis routes through `ChatWorker` + `WiFiAgent.build_messages()` (docs/agents/wifi.md) | IMPLEMENTED | `ui/panels/base.py:411-432`; `agents/wifi_agent/__init__.py:446-450` | — |
| 30 | "Under the hood" paths: `agents/wifi_agent.py`, `main.py: SubprocessWorker` (docs/agents/wifi.md) | MISSING | `docs/agents/wifi.md:41,43` vs. actual `agents/wifi_agent/__init__.py` and `ui/workers.py:1-5` (own header: "Moved verbatim out of main.py") | Stale paths from before two refactors; doc not updated. |
| 31 | "Add an adapter" extension note: add an entry to `KNOWN_ADAPTERS` (docs/agents/wifi.md) | PARTIAL | `agents/wifi_agent/__init__.py:4-32` vs. `ui/panels/wifi.py:41-56` | A second, separately maintained dict, `KALI_ADAPTERS`, drives the Kali builder's own dropdown; the doc never mentions it, so following the doc alone leaves the Kali dropdown out of sync with `KNOWN_ADAPTERS`. |
| 32 | Requirements: macOS `airport` binary, built-in path `AIRPORT` (docs/agents/wifi.md) | MISSING | `agents/wifi_agent/__init__.py:34-37` vs. repo-wide grep for `AIRPORT` (zero other references) | `AIRPORT` is defined but never called; the module's own comment says `airport` was removed in macOS 14.4 and scanning now exclusively uses `system_profiler`. The "Requirement" names a dependency the shipped code doesn't use. |
| 33 | Kali commands assume Kali + one of three named adapters (docs/agents/wifi.md) | NOT CODE-VERIFIABLE | `ui/panels/wifi.py:41-56` | The three-adapter static data matches; actual behaviour on a real Kali box/VM cannot be verified from this repo. |
| 34 | "A single adapter in monitor mode cannot remain an ordinary managed connection" (docs/agents/wifi.md) | IMPLEMENTED (as displayed guidance text) | `agents/wifi_agent/__init__.py:288` | — |
| 35 | "AI Analysis needs a provider key" (docs/agents/wifi.md) | NOT CODE-VERIFIABLE | `ui/panels/base.py:370-387` | Gate is `authorize()` → `host.authorize_request()`; key-checking logic lives in services outside this audit's file set. |
| 36 | Interface Info "describes the selected Mac network interface" (docs/training/beacon.md) | MISSING | `ui/panels/wifi.py:384-385` | Same cause as row 10: `["networksetup", "-listallhardwareports"]` lists every hardware port, ignoring `interface_box`. |
| 37 | Scan Networks "lists nearby wireless networks visible to the device" (docs/training/beacon.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:119-132` | — |
| 38 | Signal Monitor "follows signal conditions over time" (docs/training/beacon.md) | STUB | `ui/panels/wifi.py:386-389` | Issues the identical one-shot `system_profiler` call used for Scan Networks. No loop, timer, or repeated sampling exists anywhere in the file. |
| 39 | Ping Test "checks reachability to the host you enter" (docs/training/beacon.md) | IMPLEMENTED | `ui/panels/wifi.py:397` | — |
| 40 | Kali Command Builder "does not silently run them on the Mac" (docs/training/beacon.md) | IMPLEMENTED | Same evidence as row 8 | — |
| 41 | "Detect Adapters... Detection is guidance; drivers and OS support still matter" (docs/training/beacon.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:4-32, 205-218` | Detection is a static VID/PID→capability lookup, not a live driver probe — the "guidance only" framing accurately describes it. |
| 42 | No external adapter needed for the 4 live diagnostic modes (docs/training/beacon.md) | IMPLEMENTED | `ui/panels/wifi.py:384-399` | — |
| 43 | Preflight "never changes mode, disconnects Wi-Fi, or runs an intrusive command" (docs/training/beacon.md) | IMPLEMENTED | Same evidence as row 26 | — |
| 44 | Preflight "warns when it cannot find a separate routed connection" (docs/training/beacon.md) | IMPLEMENTED | `agents/wifi_agent/__init__.py:280-282`; echoed in `ui/panels/wifi.py:416-420` | — |
| 45 | VM/USB passthrough guidance text (docs/training/beacon.md) | IMPLEMENTED (as static text) | `agents/wifi_agent/__init__.py:289` | — |
| 46 | "Make every mode change yourself; Sentinel intentionally does not do it silently" (docs/training/beacon.md) | IMPLEMENTED | Full subprocess inventory: `system_profiler` ×2, `networksetup -listallhardwareports`, `route -n get default`, `ping` | None change Wi-Fi mode, power state, or association. |
| 47 | AI interpretation "off by default... prefer a local model; redact... before approving a cloud route" (docs/training/beacon.md) | MISSING (contradicted by code) | `ui/panels/wifi.py:186`; `ui/panels/base.py:143` (`default_provider = "anthropic"`, not overridden in `WifiPanel`) | Checkbox defaults checked AND provider defaults to a cloud model; no redaction step exists anywhere in `_scan_finished`/`_start_ai_pass`. A first run sends raw SSID/signal/security data to a cloud model by default — the opposite of the documented behaviour. |
| 48 | `wifi.help_btn` tooltip "Open the Beacon documentation section" (ui/tooltips.py:97) | MISSING | Same as row 19 | — |
| 49 | `wifi.interface_box` tooltip "typically en0 on Mac" implies selection matters (ui/tooltips.py:93) | MISSING (functionally) | Same as row 10 | Tooltip text promises an effect the control doesn't have. |
| 50 | `wifi.mode_box` / `target_input` / `run_btn` / `stop_btn` / `detect_btn` / `save_btn` tooltips (ui/tooltips.py:92,94-96,98-99) | IMPLEMENTED | `ui/panels/wifi.py:88,102,155,168,160,270` | Each names a widget that exists and behaves as the tooltip describes. |

## 4. (a) Promise-coverage gaps between sources

- `ui/tooltips.py` and `docs/agents/wifi.md` both reference a Help button
  (`wifi.help_btn` / "Use the shared Help button for docs") that does not
  exist anywhere in `ui/panels/wifi.py`. The same missing promise appears in
  two sources and neither can be checked against real code.
- Of the panel's ~20 interactive controls, only 8 have tooltips at all
  (`mode_box`, `interface_box`, `target_input`, `run_btn`, `stop_btn`,
  `help_btn` [dead], `detect_btn`, `save_btn`). The Kali sub-form
  (`kali_op_box`, `kali_adapter_box`, `kali_bssid_input`, `kali_channel_input`,
  `kali_essid_input`), `preflight_btn`, `ai_checkbox`, and `clear_btn` — all
  documented in `docs/agents/wifi.md`'s Inputs table — have none.
- `docs/training/beacon.md`'s detailed privacy rationale for AI interpretation
  ("raw network output may reveal device names, addresses and infrastructure
  details... prefer a local model; redact...") has no counterpart in
  `docs/agents/wifi.md` (which states only the mechanical "send subprocess
  output to the selected LLM") and no counterpart in the UI: the checkbox
  label itself ("AI Analysis — feed results to LLM for interpretation",
  `ui/panels/wifi.py:185`) carries none of that caveat.
- `docs/training/beacon.md`'s "Detection is guidance; drivers and
  operating-system support still matter" nuance about Detect Adapters has no
  equivalent sentence in `docs/agents/wifi.md` or any tooltip.
- README's one-line responsibility ("Wi-Fi diagnostics and commands for
  networks the operator is authorised to test") and the fuller "two
  capabilities" framing in `docs/agents/wifi.md` are consistent, just
  different levels of detail — not a gap, noted for completeness.

## 4. (b) Contradictions

1. **AI-analysis default.** `docs/agents/wifi.md` ("off by default") and
   `docs/training/beacon.md` ("optional and off by default... prefer a local
   model") both promise the AI pass is opt-in and cloud-averse by default.
   The code (`ui/panels/wifi.py:186`, `ui/panels/base.py:143`) ships it
   **checked** and defaulting to the **cloud** provider `anthropic`, with no
   redaction step. This is the audit's highest-priority finding.
2. **"Collapsed section" vs. plain checkbox.** `docs/agents/wifi.md` describes
   the AI toggle as an "Optional collapsed section"; the implementation is a
   non-collapsible `QCheckBox` with no disclosure widget.
3. **Stale `airport` requirement.** `docs/agents/wifi.md`'s Requirements line
   names the macOS `airport` binary; the code's own comments and the fact that
   `AIRPORT` is referenced nowhere else in the repo confirm the binary was
   dropped after Apple removed it in macOS 14.4, and `system_profiler` is the
   sole live scan path now.
4. **"Signal Monitor... over time."** `docs/training/beacon.md` promises
   continuous monitoring; the implementation runs the exact same one-shot
   `system_profiler` call as Scan Networks (`ui/panels/wifi.py:386-389`).
5. **Interface selector.** Both docs and the tooltip present the Interface
   combo as selecting which interface is diagnosed; no command dispatch in
   `run()` ever reads it.
6. **Stale "Under the hood" paths.** `docs/agents/wifi.md` still cites
   `agents/wifi_agent.py` (now a package, `agents/wifi_agent/__init__.py`) and
   `main.py: SubprocessWorker` (moved to `ui/workers.py`, per that file's own
   header comment referencing the same refactor the doc should have tracked).

## 4. (c) Undocumented behaviour

- **Dead Kali-AI-explain branch.** `_on_mode_changed` disables `ai_checkbox`
  whenever Kali mode is selected (`ui/panels/wifi.py:289-291`), and
  `_run_kali_builder`'s own gate checks `ai_checkbox.isEnabled()` first
  (`ui/panels/wifi.py:433`). Since the checkbox is force-disabled in exactly
  the mode this branch is meant to run in, the "explain the generated Kali
  commands" AI pass (lines 433-439) can never execute. None of the four
  promise sources mention this feature works or is broken.
- **Duplicate adapter data.** `KNOWN_ADAPTERS` (`agents/wifi_agent/__init__.py`)
  and `KALI_ADAPTERS` (`ui/panels/wifi.py`) are two independently maintained
  dicts describing the same three physical adapters. Nothing enforces they
  stay in sync, and the docs' extension guidance only mentions one of them
  (see table row 31).
- **Dead legacy regex.** `_update_indicators()` (`ui/panels/wifi.py:610-640`)
  still matches the old `airport -I` text format (`agrCtlRSSI:`, `link auth:`)
  alongside the current `system_profiler`-derived format. Since `airport` is
  never invoked (see 4(b).3), the legacy branch is unreachable dead code —
  harmless, but undocumented.
- **Silent placeholder substitution.** `_safe_bssid`/`_safe_channel`/`_safe_essid`
  silently swap any malformed input for a bare placeholder (`TARGET_BSSID`,
  `CHANNEL`, `ESSID`) with no UI feedback telling the operator their value was
  rejected versus accepted.
- **Redundant re-preflight.** `_run_kali_builder()` calls `self.run_preflight()`
  again at the start of every Kali-mode run (`ui/panels/wifi.py:407`), even if
  the operator already ran it — re-issuing `networksetup`, `route`, and
  `system_profiler SPUSBDataType` each time. Harmless (all read-only) but not
  mentioned anywhere.
- **Save omits the preflight context.** In Kali mode, `Save` persists only
  `cmds` (`ui/panels/wifi.py:429`), not the preflight text shown above it in
  the results view — the saved file loses the connection-warning context the
  operator saw on screen.

## 5. Must fix before test phase

| # | Fix |
|---|---|
| 1 | Set `self.ai_checkbox.setChecked(False)` and give `WifiPanel` a non-cloud (or explicitly "ask first") `default_provider`, so the documented "off by default, prefer local, redact before cloud" behaviour actually ships. |
| 2 | Either wire `interface_box.currentText()` into the Interface Info / Scan / Signal Monitor / Ping commands, or remove the control and its tooltip/docs claims that it selects anything. |
| 3 | Implement real polling/timer behaviour for "Signal Monitor," or rename/redocument it as a single snapshot identical to Scan Networks. |
| 4 | Add the missing `help_btn` widget to `WifiPanel` (wired to open the Beacon doc section), or delete the `wifi.help_btn` tooltip entry and the "shared Help button" line from `docs/agents/wifi.md`. |
| 5 | Add the same `inject` check used for "Deauth Attack" to "Handshake Capture" in `build_kali_commands()`, since it also emits an injection-class step. |
| 6 | Update `docs/agents/wifi.md`'s Requirements line to name `system_profiler` instead of the dead `airport`/`AIRPORT` path, and fix the stale `agents/wifi_agent.py` / `main.py: SubprocessWorker` paths in its "Under the hood" table. |
| 7 | Either merge `KALI_ADAPTERS` and `KNOWN_ADAPTERS` into one source of truth, or update the "Add an adapter" doc note to say both dicts must be updated. |
| 8 | Remove or re-enable the dead Kali-mode AI-explain branch (`ui/panels/wifi.py:433-439`) so it isn't shipped as unreachable code. |
