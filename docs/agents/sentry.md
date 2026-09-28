# SENTRY — Network anomaly watch

`key: sentry` · class: `agents/sentry/sentinel_chat_agent.py → SentryAgent` · panel: `ui/panels/sentry.py → SentryPanel` · engine: `agents/sentry/sentry/`

> Sentry watches **your own** network. It is defensive monitoring only — it never
> scans, probes, or interferes with other people's devices or networks.

## What it does
Sentry observes the local network and this host with unprivileged, **read-only**
commands, records a trusted baseline on its first pass, and on every later pass
reports only what is *new* or *anomalous*:

- **New device on the segment** — a MAC/IP not seen on earlier trusted passes.
- **ARP spoofing / man-in-the-middle indicators** — one IP claimed by several
  MACs, or the default gateway's hardware address changing. Anomalies on the
  **gateway** are escalated as alerts.
- **New listening service** — a process on this Mac that began accepting
  connections, flagged higher when it is bound to an externally reachable address.
- **New outbound connection** — this Mac talking to a public endpoint it has not
  contacted before. Local chatter (loopback, link-local AirDrop/Handoff, mDNS
  multicast) is deliberately ignored.

An optional AI read interprets the findings: what each one means, its benign vs.
malicious explanations, and safe checks the operator can run themselves.

## What it deliberately does not do
- **No packet capture / deep inspection.** Everything comes from the ARP/NDP
  neighbour table, the routing table, and this host's own sockets — never from
  sniffing traffic (that would need root/BPF). This is stated plainly rather than
  implied away.
- **No active scanning** of other hosts (no port scans, no probes).
- **No changes** to any interface, route, or remote host.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Run watch pass | Observe now, compare against the baseline, persist what is seen. |
| Dry run | Compare against the baseline **without** updating it. |
| Reset baseline | Forget everything seen; the next pass records a fresh baseline. |
| Explain findings with AI | When ticked, findings (never "nothing to report") are sent to the selected model. |
| Continuous background watch | Interval + Install/Remove — a launchd agent that keeps watching while the app is closed. |
| Provider / Model / Auto-route / Stop | Shared run-bar controls for the AI read. |

## Outputs
The findings pane renders each finding with a severity tag
(`ALERT` / `WARNING` / `NOTICE` / `INFO`) and a one-line detail. The AI read (when
enabled and there is something to report) streams below it: a verdict, each
finding explained both ways, and recommended non-destructive checks.

## How it works
- `agents/sentry/sentry/collectors.py` — read-only probes and their pure parsers:
  `arp -an` + `ndp -an` (merged IPv4/IPv6 neighbours), `route -n get default`,
  `lsof` (listeners and established connections). MACs are normalised so a NIC
  keeps one identity across passes.
- `agents/sentry/sentry/engine.py` — `diff(baseline, current)` is a **pure**
  function returning severity-sorted findings; `run_watch()` persists a pass.
- `agents/sentry/sentry/baseline.py` — JSON baseline + rolling findings log under
  Sentinel's normal writable base (`…/data/sentry/`), so the panel shows what the
  background watcher found while the app was closed.
- `agents/sentry/sentry/watchd.py` — installs/removes the launchd StartInterval
  agent (`com.sentinel.sentry.watch`) that re-runs `main.py --headless` each
  interval. No long-lived daemon.
- `ui/workers.py → SentryWatchWorker` runs a pass off the UI thread; the AI read
  goes through the shared request guard (`authorize → ChatWorker → record`).

## Baseline model
The first pass records everything as trusted (no findings). Later passes report
new items, then fold them into the baseline so each is reported **once**, not
every tick. A legitimate router swap is reported once as a gateway-MAC change and
then settles. **Reset baseline** starts over — use it after moving to a new
network.

## Privileges & platform notes
All collectors run without `sudo`. On some sandboxes the IPv4 ARP cache reads
empty to a child process while IPv6 NDP still works; merging both keeps the
device list populated, and a normally-launched app sees the full table. The
background watch is a per-user launchd agent in `~/Library/LaunchAgents/`.

## CLI (standalone / headless)
```bash
python agents/sentry/main.py selftest      # verify collectors run here (read-only)
python agents/sentry/main.py scan          # dry-run diff, nothing persisted
python agents/sentry/main.py watch-once    # one persisted pass (launchd calls this)
python agents/sentry/main.py watch --interval 300
python agents/sentry/main.py report        # print the findings log
```

## Tests
`agents/sentry/tests/` (engine: parsers, classification, the diff, persistence) and
`tests/test_ui_panels.py::TestSentryPanel` (panel wiring, spend guard, launchd calls).
