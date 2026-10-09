# SENTRY — Network anomaly watch

`key: sentry` · class: `agents/sentry/sentinel_chat_agent.py → SentryAgent` · panel: `ui/panels/sentry.py → SentryPanel` · engine: `agents/sentry/sentry/`

> Sentry watches **your own** network. It is defensive monitoring only — it never
> scans, probes, or interferes with other people's devices or networks.

## What it does
Sentry observes the local network and this host with unprivileged, **read-only**
commands, records a trusted baseline on its first pass, and on every later pass
reports only what is *new* or *anomalous*:

- **New device on the segment** — a MAC address not in the baseline, read from the
  ARP/NDP neighbour table, so only devices this Mac has recently talked to can
  appear. The MAC is the identity: a known device that gets a new IP is not
  reported; an unknown MAC appearing on a known IP is. Severity NOTICE.
- **ARP spoofing / man-in-the-middle indicators** — exactly two checks:
  (1) one IP claimed by several MACs in the same snapshot — WARNING, or ALERT when
  that IP is the default gateway; it is reported only when a MAC newly claims the
  IP; (2) the default gateway's hardware address differing from the baseline's
  while the gateway's IP is unchanged — ALERT; it needs the gateway to be in the
  ARP cache so that its MAC is known. *Known limitation:* nothing compares an
  ordinary host's MAC with the one the baseline holds for that IP, so poisoning of a
  non-gateway host that does not produce a duplicate shows up, at most, as a "new
  device" notice. Whether a real poisoning attempt produces either signal has not
  been verified in a lab.
- **New listening service** — a socket on this Mac that is not in the baseline: a
  TCP listener, or any UDP socket. It is identified by protocol, bound address and
  port, **not** by process, so a different program taking over a baselined port is
  not reported. WARNING when it is bound to all interfaces or to a LAN or public
  address, NOTICE otherwise (for example loopback).
- **New outbound connection** — an established TCP connection to a remote address
  and port not in the baseline (the process is not part of the identity). NOTICE for
  a public address, INFO for a private-network address. Local chatter (loopback,
  link-local AirDrop/Handoff, mDNS multicast) is deliberately ignored. UDP/QUIC and
  connections that open and close between two passes are never seen.

An optional AI read interprets the findings: what each one means, its benign vs.
malicious explanations, and safe checks the operator can run themselves. It is
**off by default** (see *What is sent to a model*).

## What it deliberately does not do
- **No packet capture / deep inspection.** Everything comes from the ARP/NDP
  neighbour table, the routing table, and this host's own sockets — never from
  sniffing traffic (that would need root/BPF). This is stated plainly rather than
  implied away.
- **No active scanning** of other hosts (no port scans, no probes, no pings).
- **No changes to the network** — no interface, route, or remote host is touched.
  Sentry does write its own state files (baseline, findings log, `watch.log`) and,
  only if you press Install, one per-user launchd agent (see *Background watch*).

## Commands it runs
Everything Sentry executes. The collectors use a fixed argument list (no shell) and
a 15-second limit per command:

| Command | Reads |
|---|---|
| `arp -an` | IPv4 neighbour table (numeric, no name lookups) |
| `ndp -an` | IPv6 neighbour table |
| `route -n get default` | default gateway and interface (IPv4 only) |
| `lsof -nP -iTCP -sTCP:LISTEN` | this Mac's TCP listeners |
| `lsof -nP -iUDP` | this Mac's UDP sockets (no state filter) |
| `lsof -nP -iTCP -sTCP:ESTABLISHED` | this Mac's established TCP connections |

The background watch also runs `launchctl list` (is the agent loaded; 10-second
limit), `launchctl load -w <plist>` (Install, Reinstall) and
`launchctl unload -w <plist>` (Remove, Reinstall). Those change your user's launchd
state, not the network. Nothing else is executed: no `sudo`, `nmap`, `ping` or `tcpdump`, and no
socket is opened by Sentry itself.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Run watch pass | Observe now, compare against the baseline, persist what is seen. The first pass only records the baseline, and does not even do that if a collector failed. |
| Dry run | Compare against the baseline **without** updating it; the result is headed "dry run, nothing saved". With no baseline it says so and saves nothing. |
| Reset baseline | Delete the baseline **and** the recorded findings; the next pass records a fresh baseline. There is no confirmation. The background watch shares both files, so it is reset too. |
| Explain findings with AI | **Off by default.** When ticked, the findings of a pass that has some (a dry run counts) are sent to the selected model — see *What is sent to a model*. |
| Continuous background watch | Interval (1–720 min, default 5) + Install/Remove — a launchd agent that keeps watching while the app is closed. |
| Provider / Model / Auto-route / Stop | Shared run-bar controls for the AI read. Stop also cancels a watch pass that is in progress. |

## Outputs
The findings pane opens with the counts (devices · listeners · connections), the
time of the pass and, for a dry run, "dry run, nothing saved". Each finding then
shows a severity tag (`ALERT` / `WARNING` / `NOTICE` / `INFO`), its title, and a
short explanatory detail; findings are sorted strongest first. A collector that
failed appears as an amber `[COLLECTOR FAILED] <command>: <reason>` line above the
findings (see *When a collector fails*). The AI read (when enabled and there is
something to report) streams below it: a verdict, each finding explained both ways,
and recommended non-destructive checks.

Until a pass finishes in the session, the pane lists what the log already holds: the
25 most recent recorded findings, newest first, each with the time it was recorded.
That includes everything the background watcher found while the app was closed.
This list is read when Sentinel starts and again after Install, Remove or Reset
baseline; it is not refreshed on its own while the app stays open, and any pass that
finishes (a dry run included) replaces it for the rest of the session. There is no
"seen" marker: the same list is shown at every start.

## How it works
- `agents/sentry/sentry/collectors.py` — read-only probes and their pure parsers:
  `arp -an` + `ndp -an` (merged IPv4/IPv6 neighbours), `route -n get default`,
  `lsof` (listeners and established connections). MACs are normalised so a NIC
  keeps one identity across passes. Failures are recorded (see below).
- `agents/sentry/sentry/engine.py` — `diff(baseline, current)` is a **pure**
  function returning severity-sorted findings; `run_watch()` persists a pass and
  honours a stop request made before it saves.
- `agents/sentry/sentry/baseline.py` — JSON baseline + rolling findings log (last
  500) in one state directory, resolved in this order: the `SENTRY_STATE_DIR`
  environment variable; Sentinel's normal writable base (`…/data/sentry/` — the
  checkout's `data/sentry` in development, Application Support in a self-contained
  build, the volume's data folder in portable mode); `agents/sentry/data` if the
  Sentinel package cannot be imported. The panel and the background watcher use the
  same directory because the launch agent is given the panel's directory in
  `SENTRY_STATE_DIR`. Passes are serialised with file locks.
- `agents/sentry/sentry/watchd.py` — installs/removes the launchd StartInterval
  agent (`com.sentinel.sentry.watch`) that re-runs `main.py --headless` each
  interval. No long-lived daemon.
- `ui/workers.py → SentryWatchWorker` runs a pass off the UI thread; the AI read
  goes through the shared request guard (`authorize → ChatWorker → record`).

## Baseline model
The first pass records everything as trusted (no findings) — whatever is on the
network and listening on this Mac at that moment, including anything hostile that
is already there. Later passes report new items, then fold them into the baseline so
each is reported **once**, not every tick; an item reported once is trusted from
then on. A legitimate router swap is reported once as a gateway-MAC change and then
settles. Moving to a new network (a different gateway IP) raises no finding of its
own: the stored gateway is replaced and every device then reads as new, so use
**Reset baseline** after moving. The connections baseline is capped at 4096
endpoints, so a long-idle one can be evicted and reported again; devices and
listeners are never pruned. A baseline file that cannot be read is treated as "no
baseline": the next saved pass records a fresh one without a finding.
**Reset baseline** starts over.

## When a collector fails
A collector counts as failed when its command is not found, cannot start, times out
(15 s), or exits non-zero with no output. The panel then shows the
`[COLLECTOR FAILED]` line, a first pass is **not** adopted as a baseline ("No
baseline saved…"), and a later pass with nothing to report says "No new anomalies.
Some collectors failed — see above." — a failed dimension is simply empty, so it
cannot show anything new. `selftest` exits 1 on any failure and when it observes nothing at all.
*Known limitations:* (1) `lsof` exits 1 when it finds nothing to list, so a Mac with
no matching sockets may show a failed collector instead of a quiet result — this
follows lsof's documented exit status and has not been checked on a Mac here. (2) The
command line (`watch-once`, `watch`, `scan`) and therefore `watch.log` do not print
collector failures; with no baseline and a failing collector, `watch-once` prints
"No new anomalies since the last trusted baseline" although nothing was saved. Only
the panel and `selftest` report them.

## Background watch
**Install background watch** writes `~/Library/LaunchAgents/com.sentinel.sentry.watch.plist`
and loads it with `launchctl load -w`. **Remove** unloads it and deletes the file.
Once installed, the button reads **Reinstall** and reloads the agent with the
interval in the box. The agent is a launchd `StartInterval` job — launchd starts one
short process per interval (60 seconds is the floor) — and the plist contains:

- `ProgramArguments`: `<repo>/.venv/bin/python agents/sentry/main.py --headless`
  (the app's own interpreter if there is no `.venv`). `--headless` means one
  `watch-once` pass: the same as **Run watch pass**.
- `WorkingDirectory`: `agents/sentry`; `EnvironmentVariables`: `SENTRY_STATE_DIR` set
  to the panel's state directory, so both share one baseline and findings log.
- `RunAtLoad`: true — a pass also runs when you install it and at every login or
  restart, until you press Remove.
- `StandardOutPath` / `StandardErrorPath`: `watch.log` in the state directory. It is
  never rotated.

What that means in practice:
- It keeps running when Sentinel is closed or quit; Sentinel never stops it. Closing
  the window only cancels a pass that is running inside the app.
- It has no alert channel: no notification, badge or email. Its findings reach you
  only through the findings list at the next start of Sentinel (see *Outputs*) or
  `python agents/sentry/main.py report`. It folds each finding into the baseline, so
  a later manual pass does not show it again.
- With no baseline (first install, or after **Reset baseline**) whichever pass runs
  first — background or manual — records the baseline from whatever the network looks
  like at that moment, with no confirmation.
- The state directory is written into the plist when you install, so press
  **Reinstall** after the data location changes.
- The status line under the controls reads "running every N min", "installed but not
  loaded" or "off". Install reports success from `launchctl load`'s exit code
  alone, and Remove from the plist file having been deleted, so trust the status line.
- `agents/sentry/launchd/com.sentinel.sentry.watch.plist.template` is a reference for
  a manual install. It has placeholders and omits the `WorkingDirectory` and
  `SENTRY_STATE_DIR` entries the app writes.
- *Known limitation — self-contained builds:* Install is offered in every build, but
  the plist uses the app's own executable when there is no `.venv` and points at
  `agents/sentry/main.py`, which the build does not ship as a file. The background watch is
  only meant for a source checkout with a `.venv`; it has not been tried from a built
  app.
- *Known limitation — portable mode:* the launch agent lives on the host Mac, outside
  the portable volume, while its state directory (and, without a `.venv`, its
  interpreter) point into the volume. Press Remove when you no longer want it to fire.

## What is sent to a model
Sentry sends nothing to a model unless **Explain findings with AI** is ticked (it is
off by default) **and** a pass with at least one finding finishes. A pass with no findings,
a first-pass baseline, a refused baseline, and a dry run with no baseline send
nothing.

When it does send, the request goes to whichever provider and model the run bar has
selected. The panel's default provider is Anthropic, a paid cloud route; pick an
Ollama model to keep the read on this Mac. The request carries:
- the fixed Sentry system prompt;
- the number of devices, listeners and connections seen;
- for each finding, its severity, title and evidence: for a device its IP, MAC and
  interface; for an address conflict or gateway change the IP and MAC(s); for a
  listener the process name, PID, protocol, bound address and port; for a connection
  the process name, PID, remote address and port.

It does **not** carry the findings' detail text, the baseline, or the raw neighbour
table, listener list or connection list. Every non-Ollama request shows the shared
confirmation (provider, model, approximate tokens, estimated cost — not the content)
and is checked against the budget caps. The findings message and the reply are then
saved as an ordinary Saved Chat on this Mac, and the run log keeps the first 200
characters of the request. *Known limitation:* the system prompt tells the model it
receives the neighbour table, listeners and connections; it receives only what is
listed above.

## Privileges & platform notes
All collectors run without `sudo`. *Known limitation:* a non-root `lsof` may
list only the invoking user's processes, so listeners and connections owned by root
or by another user (Remote Login, File Sharing, Screen Sharing) may not appear at all.
The code cannot tell; this has not been verified on a Mac. On some sandboxes the
IPv4 ARP cache is reported to read empty to a child process while IPv6 NDP still
works (also unverified here); merging both keeps the device list populated. The
parsers and the launch agent are macOS-only and have no platform guard.

Starting Sentinel builds the Sentry panel, which creates the state directory and
`~/Library/LaunchAgents/` if they are missing and runs `launchctl list` to show the
background-watch state.

## Other known limitations
- **UDP.** Every UDP socket counts as a "listener". A connected UDP socket (its name
  contains `->`) is mis-parsed into an odd address and described as bound to
  loopback only, so UDP notices can be noisy and misleading.
- **Randomised MACs.** A device that rotates its Wi-Fi MAC reads as a new device each
  time.
- **Visibility.** A device that never exchanges traffic with this Mac, or whose
  neighbour-table entry has aged out, is invisible.
- **Duplicate gateway rows.** The gateway's MAC is taken from the first matching
  ARP row.

## CLI (standalone / headless)
```bash
python agents/sentry/main.py selftest      # verify collectors run here (read-only); exit 1 on failure
python agents/sentry/main.py scan          # dry-run diff, nothing persisted
python agents/sentry/main.py watch-once    # one persisted pass (launchd calls this as --headless)
python agents/sentry/main.py watch --interval 300   # foreground loop; interval at least 30 s
python agents/sentry/main.py report        # print the last 50 recorded findings (the log keeps 500)
```
`main.py --headless` is the alias for `watch-once` that launchd uses. `scan` with no
baseline prints first-pass wording ("Baseline established") followed by a line
saying the baseline was not written; nothing is saved.

## Tests
`agents/sentry/tests/` (engine: parsers, classification, the diff, persistence,
collector failures, cancel, state directory) and
`tests/test_ui_panels.py::TestSentryPanel` (panel wiring, spend guard, recorded
findings, dry-run and failed-collector wording, AI off by default; the launchd calls
are mocked). The parent suite does not collect `agents/sentry/tests/`.
