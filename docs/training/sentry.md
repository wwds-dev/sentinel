# Sentry — network anomaly watch

**Goal:** watch your own network for suspicious changes, and read the findings
correctly. **Time:** 15 minutes.

Sentry is defensive monitoring of the network **you** are connected to. It never
scans, probes, or touches other people's devices, and it changes no network
settings. Every check is a read-only command. It does keep its own small state
files on this Mac and, only if you choose to install the background watch, one
background launch agent.

## What a watch pass sees

Sentry reads what macOS already knows, using unprivileged commands:

- the **neighbour table** (`arp` for IPv4, `ndp` for IPv6) — the devices your Mac
  has recently talked to on the local segment;
- the **default gateway** — your router's address and, when your Mac has talked to
  it lately, its hardware (MAC) address;
- this Mac's **listening services** — sockets accepting connections (`lsof`);
- this Mac's **outbound connections** — the TCP connections it currently has open
  (`lsof`).

It does **not** capture or inspect packets. True deep inspection needs root and a
packet-capture layer, which v1 deliberately avoids; Sentry works from the tables
and sockets the operating system exposes for free.

Two limits follow. A device that has not exchanged traffic with your Mac, or whose
entry has aged out of the table, is invisible. And because nothing runs as an
administrator, services owned by other accounts (macOS's own Remote Login or File
Sharing, for example) may not be listed at all; this has not been verified on a Mac.

## Baseline, then differences

The **first** pass records everything as a trusted baseline and reports nothing —
there is nothing to compare against yet. That includes anything already wrong, so
record it on a network you trust. If one of the commands failed during that pass,
Sentry refuses to save the baseline and tells you, rather than trust an empty
picture. Every later pass compares against the baseline and reports only what is
new:

- **New device** — a MAC address not seen before appeared in the neighbour table.
  The MAC is what counts: a known device with a new IP is not reported, and a phone
  that rotates its private Wi-Fi address can look new each time.
- **Address conflict / gateway change** — these are Sentry's "ARP spoofing" checks,
  and there are exactly two: one address claimed by two MACs at once, or the
  gateway's hardware address changing. On the **gateway** either is escalated as an
  alert: it is a credible man-in-the-middle signal, so treat the network as
  untrusted until you can explain it. Sentry will not catch every spoofing attempt:
  poisoning of an ordinary device's entry, for instance, would most likely show up
  only as a new device.
- **New listening service** — a program on this Mac began accepting connections;
  it is a warning when bound to all interfaces or a LAN address, and a notice
  otherwise. Sentry identifies it by protocol and port, so a different program
  taking over a port it already knew is not reported.
- **New outbound connection** — this Mac opened a TCP connection to an address and
  port it had not used before: a notice for a public endpoint, plain information
  for one on a private network. Ordinary local chatter (AirDrop, Handoff, mDNS) is
  ignored, and connections that open and close between two passes are never seen.

Findings carry a tag: ALERT, WARNING, NOTICE or INFO, strongest first.

After a finding is reported once, Sentry folds it into the baseline so it is not
repeated every pass — which also means it is trusted from then on. **Dry run**
compares without saving, so you can look without folding what you see into the
baseline. **Stop** cancels a pass in progress; a pass stopped in time saves nothing.
Use **Reset baseline** when you move to a new network, so its normal devices do not
all read as new; it deletes the baseline **and** the recorded findings, and asks for
no confirmation.

## Reading the findings

Most findings on a busy home or office network are benign: a phone joining Wi-Fi,
a Mac service opening an AirPlay port, a normal software-update connection. Tick
**Explain findings with AI** to get a calibrated read — a verdict, each finding
explained both the benign and the malicious way, and safe checks you can run
yourself, such as confirming a MAC against a device's own Wi-Fi settings or
looking up its vendor.

If a command fails, the pane says so in an amber `[COLLECTOR FAILED]` line. A quiet
result with such a line is not evidence that the network is quiet.

## What leaves your Mac

Nothing, unless you tick **Explain findings with AI** (it is off by default) and a
pass has at least one finding. Then the findings go to the provider and model
selected in the run bar: for each one its title and evidence — LAN IPs, MAC
addresses, interface, process names and IDs, ports and remote addresses — plus the
counts of devices, listeners and connections. The baseline and the raw tables are
not sent. Sentry's default provider is a paid cloud one (Anthropic); choose an
Ollama model to keep the read on your Mac. A cloud request asks you to confirm, with
its estimated cost, and is checked against your budgets. The request and the reply
are kept in Saved Chats, so they stay on your Mac afterwards. Everything else Sentry
does is local and free.

## Watching in the background

Install the **Continuous Background Watch** to keep checking on a schedule even
while Sentinel is closed. Choose the interval (1 to 720 minutes, 5 by default) and
press **Install background watch**. That adds a small launch agent to your Mac, which
repeats one read-only pass at that interval, at every login, and once straight away,
and records what it finds. It sends nothing to a model and shows no notification:
the next time you start Sentinel, the Sentry panel lists the most recent recorded
findings, each with the time it was found. Press **Remove** to take the agent away
again; until you do, it keeps running after Sentinel quits. The line under the
buttons says whether it is running, installed but not loaded, or off.

Two details. After a **Reset baseline**, whichever pass runs first — background or
manual — records the new baseline, so run one yourself on a network you trust. And
the background watch is meant for Sentinel run from a source checkout; it has not
been tried from a self-contained app, and in portable mode the launch agent lives on
the host Mac rather than on the USB volume, so remove it when you no longer want it.

## Worked example

**Scenario:** you want Sentry to notice a device you add to your own home network.

**You have:**

- Your Mac on your home Wi-Fi, with Sentry's background watch **off**, **Explain
  findings with AI** unticked, and no baseline yet (press **Reset baseline** first if
  you have used Sentry before).
- A spare phone you own, called `Spare phone`, that is not yet on the network.
  Fictional values below: IP `192.168.1.42`, MAC `aa:bb:cc:00:11:22`.

**Do this:**

1. Open **Sentry** and press **Run watch pass**.
2. Join `Spare phone` to the Wi-Fi and let it and your Mac talk once — for example
   send it a file with AirDrop, or run `ping 192.168.1.42` yourself in Terminal.
   Sentry sends nothing of its own.
3. Press **Dry run**, then read the pane.
4. Press **Run watch pass**, then press it once more.

**You should see:** after step 1, "Baseline recorded." with the device, listener and
connection counts and nothing flagged. After step 3, a line headed "dry run, nothing
saved" and a NOTICE such as `New device on the network: 192.168.1.42
(aa:bb:cc:00:11:22)`. After the first pass of step 4, the same NOTICE again, now
saved. After the second, the phone is no longer listed: it was folded into the
baseline and is trusted from here on, so if nothing else changed you see "No new
anomalies." Other notices about your Mac's own connections may appear alongside;
read each one. If the phone never appears, your Mac has not talked to it recently;
that is not proof that it is not there.

**Why it's safe / what it costs:** every step is local and free, and the commands are
read-only. Nothing leaves the Mac unless you tick the AI box, which sends the
findings to the selected model as described above.

## Exercise

On your own network, press **Reset baseline**, then **Run watch pass** to record a
baseline and write down how many devices, listeners and connections it reports. Press
**Dry run** and confirm it reports "nothing saved". Then explain, in your own words,
which of those counts could change without anything being wrong (for example a browser
opening a new connection) and which would deserve a closer look (a device you do not
recognise). Finish by finding out whether the background watch is on, and say what
leaves your Mac when **Explain findings with AI** is ticked.

## Best practices

- Take the first baseline on a network you trust, and **Reset baseline** when you
  change networks.
- Treat "No new anomalies" as "nothing new that Sentry can see", not as "safe".
- Keep the AI box off unless you want the read; prefer a local model for it.
- Remove the background watch when you no longer want it.

## Completion check

Without looking, explain why the first pass reports nothing, why a finding appears
only once, what Sentry cannot see, and what leaves your Mac when the AI box is
ticked.
