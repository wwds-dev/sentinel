# Sentry — network anomaly watch

Sentry is Sentinel's **read-only** watch over the network the Mac is connected to.
It records a trusted baseline of the local segment and this host, then reports what
is new or anomalous on every later pass — a new device, an address conflict or
gateway-MAC change (its "ARP spoofing" indicators), a new listening service, or a
new outbound connection. Optionally a launchd agent keeps watching in the
background while Sentinel is closed (macOS).

It ships as an in-app agent (roster key `sentry`) and as a standalone CLI.

## The honest boundary

The pitch for a tool like this is usually "an IDS that catches hackers on your
network." Real intrusion detection at that level needs packet capture (root/BPF),
signatures, and a place to run continuously. Sentry is a narrower, real version:

- It reads what the OS already knows — the ARP/NDP **neighbour table**, the
  **default route**, and this host's own **sockets**. The commands are `arp -an`,
  `ndp -an`, `route -n get default` and three forms of `lsof -nP` (TCP listeners,
  all UDP sockets, established TCP). No `sudo`, no packet sniffing, no traffic
  inspection. The background watch additionally calls `launchctl` to install,
  remove and query its agent.
- It never scans, probes, or touches **other** hosts. Defensive, own-network only.
- It changes no network setting — no interface, route, or remote state is modified.
  It does write its own state files (see Data) and, only if you install the
  background watch, one per-user launchd agent.

What it reports: a MAC address not in the baseline (only devices in the neighbour
table, i.e. ones this Mac has recently talked to); an address conflict (one IP
claimed by several MACs in one snapshot) and a changed gateway MAC — the only two
"ARP spoofing" checks, escalated to an alert on the gateway; a new listening socket
on this Mac, identified by protocol, address and port (not by process); and a new
outbound TCP connection (a notice for a public endpoint, info for a private one).

What it does **not** do: deep packet inspection, Snort-style signatures, or catching
an attacker who never touches the ARP table or this host's sockets. In particular
nothing compares an ordinary host's MAC with the one the baseline holds for that IP,
so poisoning that does not create a duplicate shows up at most as a new device;
whether real poisoning triggers either check has not been verified in a lab. UDP/QUIC
connections are not observed. A non-root `lsof` may not list other users' sockets;
the code cannot tell and this has not been verified. Deep inspection is under
consideration, not built (`SUGGESTIONS.md`, item 1).

## Layout

```
sentry/            engine package (pure, testable)
  collectors.py    read-only probes + their parsers (arp/ndp/route/lsof)
  models.py        Device / Listener / Connection / Finding / Snapshot
  engine.py        diff(baseline, current) -> findings; run_watch()
  baseline.py      JSON baseline + rolling findings log + state directory
  watchd.py        install/remove the launchd background watch
  cli.py           scan / watch-once / watch / selftest / report
main.py            standalone + headless (--headless) entry
tests/             engine tests (parsers, classification, diff, persistence,
                   collector failures, cancel, state directory)
launchd/           reference plist template for a manual install (see below)
data/              gitignored fallback state directory (see Data below)
```

In the app, the panel is `ui/panels/sentry.py` and the chat agent is
`sentinel_chat_agent.py → SentryAgent`. In the panel the AI read of the findings is
**off by default**: when ticked it sends the findings (LAN IPs, MAC addresses,
process names, ports) to the selected provider. Developer reference, including
exactly what is sent: `../../docs/agents/sentry.md`.

## Data

Sentry's state is three small files in one writable directory (plus `*.lock` files
and short-lived `.tmp-*.json` files used for atomic writes):

- `baseline.json` — the trusted snapshot (`Snapshot.devices` / `listeners` /
  `connections`) that `diff()` compares every later pass against
- `findings.json` — the rolling findings log (last 500). Every saved pass appends its findings to
  it, whether it ran in the app or in the background; `report` and the in-app panel
  read it (the panel lists the 25 most recent when Sentinel starts, until a pass
  finishes there)
- `watch.log` — stdout/stderr from each launchd background pass; never rotated

The directory is resolved in this order (`default_state_dir()` in `sentry/baseline.py`):

1. `SENTRY_STATE_DIR`, if set. The background launch agent is given the panel's
   directory this way, so the panel and the watcher share one baseline and one
   findings log.
2. Sentinel's own writable base, `user_data_base() / "data" / "sentry"`, so it follows
   Sentinel's normal data-location rules (Lab checkout in development, Application
   Support for a self-contained build, the volume for portable mode). `main.py` puts
   the repo root on `sys.path`, so a standalone run from a checkout uses this too.
3. This package's own `data/` directory — gitignored (`.gitignore`), created on first
   use, empty in a fresh checkout — only when Sentinel's `services` package cannot
   be imported.

`BaselineStore(state_dir=...)` overrides the location, mainly for tests. See
`sentry/baseline.py`.

## Background watch

The app's **Install background watch** writes
`~/Library/LaunchAgents/com.sentinel.sentry.watch.plist` (`sentry/watchd.py`) and
loads it with `launchctl load -w`. It is a `StartInterval` job (60 s minimum) that
runs `<repo>/.venv/bin/python main.py --headless` — one `watch-once` pass — with
`agents/sentry` as its working directory and `SENTRY_STATE_DIR` set; `RunAtLoad` is
on, so it also runs at install and at every login. It has no alert channel: findings
are only written to the log. `launchd/com.sentinel.sentry.watch.plist.template` is a
reference for installing by hand; it uses placeholders and omits the
`WorkingDirectory` and `SENTRY_STATE_DIR` entries the app adds. The background watch
is meant for a source checkout with a `.venv`; it has not been tried from a
self-contained app.

## CLI

```bash
python main.py selftest      # verify the read-only collectors run on this host (exit 1 on failure)
python main.py scan          # dry-run diff against the baseline, nothing written
python main.py watch-once    # one persisted pass (what launchd runs, as --headless)
python main.py watch --interval 300   # foreground loop; interval is at least 30 s
python main.py report        # print the last 50 recorded findings (the log keeps 500)
```

A collector that cannot run (command missing, timed out, or exited non-zero with no
output) is recorded: `selftest` fails, the panel shows it, and a first pass is not
adopted as a baseline. The other commands do not print these failures — with no
baseline and a failing collector, `watch-once` prints "No new anomalies…" although
nothing was saved — and `scan` with no baseline prints first-pass wording followed by
a line saying the baseline was not written.

## Tests

```bash
cd agents/sentry && ../../.venv/bin/python -m pytest -q
```

Runs from this directory (a scoped `conftest.py` puts the package on the path).
The parent Sentinel suite (`testpaths = tests`) does not collect these; the panel's
tests are `tests/test_ui_panels.py::TestSentryPanel` there.
