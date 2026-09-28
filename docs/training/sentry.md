# Sentry — network anomaly watch

**Goal:** watch your own network for suspicious changes, and read the findings
correctly. **Time:** 15 minutes.

Sentry is defensive monitoring of the network **you** are connected to. It never
scans, probes, or touches other people's devices, and it changes nothing on your
system. Every check is read-only.

## What a watch pass sees

Sentry reads what macOS already knows, using unprivileged commands:

- the **neighbour table** (`arp` for IPv4, `ndp` for IPv6) — the devices your Mac
  has recently talked to on the local segment;
- the **default gateway** — your router's address and hardware (MAC);
- this Mac's **listening services** — sockets accepting connections;
- this Mac's **outbound connections** — where it is currently talking to.

It does **not** capture or inspect packets. True deep inspection needs root and a
packet-capture layer, which v1 deliberately avoids; Sentry works from the tables
and sockets the operating system exposes for free.

## Baseline, then differences

The **first** pass records everything as a trusted baseline and reports nothing —
there is nothing to compare against yet. Every later pass compares against that
baseline and reports only what is new:

- **New device** — a MAC/IP not seen before joined the segment.
- **ARP spoofing / gateway change** — one address claimed by two MACs, or the
  gateway's hardware address changing. On the **gateway** this is escalated as an
  alert: it is a credible man-in-the-middle signal, so treat the network as
  untrusted until you can explain it.
- **New listening service** — a program on this Mac began accepting connections;
  it matters more when bound to an externally reachable address.
- **New outbound connection** — this Mac reached a public endpoint it had not
  contacted before. Ordinary local chatter (AirDrop, Handoff, mDNS) is ignored.

After a finding is reported once, Sentry folds it into the baseline so it is not
repeated every pass. Use **Reset baseline** when you move to a new network, so its
normal devices do not all read as new.

## Reading the findings

Most findings on a busy home or office network are benign: a phone joining Wi-Fi,
a Mac service opening an AirPlay port, a normal software-update connection. Tick
**Explain findings with AI** to get a calibrated read — a verdict, each finding
explained both the benign and the malicious way, and safe checks you can run
yourself, such as confirming a MAC against a device's own Wi-Fi settings or
looking up its vendor. Nothing is sent to a model when there is nothing to report.

## Watching in the background

Install the **Continuous Background Watch** to keep checking on a schedule even
while Sentinel is closed. It runs a small launch agent that repeats one read-only
pass at your chosen interval and records what it finds, so the next time you open
the panel you see anything that happened while you were away. Remove it at any
time from the same controls.
