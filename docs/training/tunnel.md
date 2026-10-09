# Tunnel — personal VPN design and troubleshooting

**Goal:** compare the current VPN state with an intended profile, understand the topology, and either create a reviewable configuration or run a gated connection. **Time:** 30 minutes.

![Tunnel workspace. This capture predates the Your IP & DNS group, which sits between VPN Connection and Deployment.](docs/training/images/tunnel.png)

Tunnel is for infrastructure you own or administer. It has six distinct
paths: **VPN Connection (live)** actually connects or disconnects a real
tunnel, but only through an execution gate; **Connection Check** reads current
status; **Safe Action Preview** shows but cannot run a proposed change; the
**Advisor** uses an AI model; and **Build Config** and **Config Inspection** are
deterministic and offline. A status check, preview, or inspection does not send
anything to a model and does not count against an AI budget. Neither do Connect
and Disconnect: only **Ask Advisor** uses a model.

The amber banner at the top of the screen states the boundary: connecting starts
WireGuard or OpenVPN and may ask for your administrator password, traffic
protection is not verified afterwards, and the example country profiles are
templates that will not connect until you import a real config. Only the live
connection and the kill switch can change your machine; every other path stays
read-only.

## VPN Connection (live)

This is the only path that touches a real tunnel. Choose a **Server** profile,
or select **Import config…** to load a WireGuard `.conf` or OpenVPN `.ovpn` file
(a `.ovpn` file is OpenVPN; any other file is read as WireGuard). Importing
reads the server address and port out of the file, saves the profile locally,
and selects it. Tunnel remembers where the file is rather than copying it, so
moving or deleting the file later makes Connect refuse. The status line reads
**Connection state not checked** until you act — Tunnel does not silently probe
the tunnel.

Imported profiles, server sites and kill-switch state are kept in Sentinel's
data folder (`vpn/`), so Portable mode carries them and Emergency Reset covers
them. If you used the standalone VPN Agent before, Sentinel copied its folder
across on first run (the old copy is left untouched). On a Mac with no profile
file yet, the first import creates one that holds only the imported profile, so
the starter profiles disappear from the pickers.

Selecting **Connect** or **Disconnect** never runs the command immediately. It
first opens the **Execution** results tab with a target review, and only then
asks a Yes/No question that defaults to **No**. The gate has fixed stages:

1. **Target review** validates the profile and lists any blockers, warnings and
   the steps that undo the action. If the review is not allowed, Tunnel refuses,
   shows why, and executes nothing.
2. **Explicit confirmation** — you must confirm the exact action, target and
   command. If the config contains lines that run commands as administrator, a
   second dialog lists them and also defaults to **No**.
3. **Administrator prompt** — macOS asks for your password in its own dialog,
   even when an unrelated `sudo` ticket happens to be cached, and Tunnel never
   stores it. The prompt and the command share a 30-second limit; cancelling the
   prompt is reported as a failed attempt.
4. **Post-change check** re-reads local state after the command so the result
   reflects what actually happened, not what was requested.
5. **Local audit** records one JSON line per attempt — including refused ones
   (a template, a blocker) and confirmations you declined — in
   `tunnel_audit.jsonl`, under `data/logs/` in Sentinel's data folder (the
   `Sentinel Data` folder on a portable install). Kill-switch results are
   recorded too. Key-like values are scrubbed from tool output and error text
   before they are shown or stored.
6. **Rollback guidance** travels with every review and outcome, so you always
   have the step that undoes what you just did.

A template or example profile is refused before any of this: Tunnel tells you to
import a real config or set a real endpoint first. Other blockers appear in the
Execution tab and in a dialog, and nothing runs and no password is requested:
`wg-quick` or `openvpn` is not installed, the config file is missing, the config
file name is one `wg-quick` would reject (letters, digits and `_=+.-` only, at
most 15 characters — rename the file and import it again), a WireGuard config has
no peer endpoint, or the tunnel is already up. Disconnect is deliberately not
blocked for a template, so a stuck tunnel can always be brought down. Warnings
do not block: a split tunnel, a config file that other users on this Mac can
read (it holds your private key), and what happens to your traffic after a
Disconnect.

### Config lines that run as administrator

A WireGuard config can contain `PreUp`, `PostUp`, `PreDown` and `PostDown` lines,
and an OpenVPN config can contain script or plugin directives such as `up`,
`down`, `route-up`, `plugin` and `script-security`. The tunnel tool runs these as
administrator. The Connect review lists each one (cut at 100 characters) in its
own section and repeats them in the confirmation, and a second dialog asks
again, defaulting to **No**. Continue only if you wrote them or trust their
source; if a line is cut off or you do not recognise it, read the file itself
first.

**Known limitation:** the list appears on Connect only — not on Disconnect and not
in **Inspect config…** — and other `wg-quick` directives such as `SaveConfig` are
not flagged.

### Reading the result

When the command completes, Tunnel re-reads local state and the status line says
**verified locally** or **NOT verified**; the Execution tab shows the same with
the review, the tool output and a note on where the attempt was audited. For
WireGuard it looks for the tunnel's run records and, for a full tunnel, checks
which interface the route to a public address now uses (a routing lookup; no
traffic is sent). For OpenVPN it checks that the process Sentinel started exists.
**Verified never means a handshake happened, DNS is safe, or traffic is
protected** — confirm with Connection Check, **Your IP & DNS** and, if it
matters, an external IP check, rather than trusting the button alone.

A config that splits the default route into two halves (`0.0.0.0/1` and
`128.0.0.0/1`) is treated as a split tunnel, so the route check is skipped.

### OpenVPN

OpenVPN is a basic client. Connect starts it as a background process, with a log
and a pid file in Sentinel's data folder; the log is not shown in the panel.
Disconnect stops only the process Sentinel started and can identify as its own.
If it cannot, it refuses, stops nothing, and tells you to use the client that
started the connection — that refusal comes after you answer Yes and is audited
as a failure. Tunnel never stops OpenVPN by process name. The review shows an
abbreviated command and no routing or DNS lines for OpenVPN, and configs that
ask for a username and password, pushed DNS and live status are not supported.

### If you close Sentinel mid-connection

Normal app close and Portable Emergency Reset cancel every Tunnel worker, wait
about two seconds for each, then end any that is still running. Cancelling does
not interrupt the macOS prompt or a command already handed to macOS: Sentinel
drops the result, so it may be neither shown nor audited, and a change that
already happened is not undone. After reopening, run Connection Check before
relying on the tunnel.

### Kill switch

**Arm** loads a firewall rule set (macOS `pf`) that blocks network traffic except
loopback, the WireGuard tunnel interface that exists at that moment, the tunnel's
own transport to the selected server (WireGuard is UDP; OpenVPN follows the
config's `proto` line), DHCP, and your local network, so a dropped tunnel cannot
leak. It asks for confirmation (default **No**), which states what stays open
and prints the Terminal command that undoes it, and then for your administrator
password. It refuses to arm for a template, when the endpoint cannot be
resolved (a name is looked up once, when you arm), or when the generated rules
fail pf's own parse check. **Disarm** removes the blocking rules and asks only
for the password. Connect first and arm second: the tunnel interface is the one
present at arm time, so re-arm if the tunnel is re-created. Arm the kill switch
against a real server profile, not a template. Treat arming, connecting, and
verifying no-leak as three separate steps: an armed kill switch proves traffic
is blocked when the tunnel is down, not that the tunnel itself is protecting you.

If something goes wrong, the recovery command printed by the Arm confirmation
(also in the first lines of the rules file in Sentinel's `vpn/` folder, and in
the audit log) is:

```
sudo pfctl -a vpn-agent-killswitch -F all && sudo pfctl -F all -f /etc/pf.conf
```

Sentinel does not disarm when you quit or run Emergency Reset.

**Known limitations:** the first arm adds a small anchor block to `/etc/pf.conf`
(a copy is kept as `/etc/pf.conf.vpn-agent.bak`) and switches pf on; Disarm does
not remove that block or switch pf off. The kill switch only recognises
WireGuard tunnel interfaces — Sentinel does not stop you arming it for an
OpenVPN profile, and then the OpenVPN tunnel's own traffic is blocked as well, so
use it with WireGuard profiles. The label next to the buttons reports what you
did in this session (it starts at "Kill switch not armed."), not a live read.

## Connection Check

Start here when a VPN is slow, appears disconnected, or you simply want to know
what this Mac can see. Choose a saved profile under **Compare profile**, leave
**Include public IP and latency** off, and select **Check Connection**. Choosing
a profile here does not activate it or change the VPN Agent's saved selection;
it is a separate picker from the **Server** list used by Connect. Tunnel then
reports:

- whether the WireGuard app, `wg`, `wg-quick`, and OpenVPN command are present;
- active WireGuard interfaces and whether any OpenVPN process was detected;
- peer count, most recent WireGuard handshake, and transfer totals when the
  local `wg` status tool allows those reads;
- the current default network interface and gateway;
- the DNS servers configured on this Mac;
- whether the selected profile's interface is active, whether it has a recent
  handshake, whether its saved endpoint and port look usable, and whether the
  default route matches the interface.

The default check is entirely local and read-only. It does not ask for an admin
password, connect or disconnect a tunnel, alter a route, touch the firewall, or
read private keys. Its OpenVPN line counts any process named `openvpn`, whereas
Connect and Disconnect act only on the one Sentinel started.

### Optional external checks

Enable **Include public IP and latency** only when you need to compare the
visible internet address or basic reachability. Tunnel asks for confirmation
and names the destinations before it contacts them:

- `api.ipify.org` returns the public IP visible from the current connection;
- `1.1.1.1` is used for a latency test, with a TCP fallback when ping is blocked.

Declining the confirmation contacts neither destination. These results are
useful clues, not proof of anonymity or a complete leak test. A VPN interface
can be active while an application uses another route, and a configured DNS
server list does not show every resolver an application might contact.

### Your IP & DNS

The **Your IP & DNS** group is separate from Check Connection and is always
visible. **Local** reads your LAN address and any active tunnel interface (IPv4
only) with no network contact; it also runs once when the panel opens. **Check
public IP** contacts an address service — IPinfo when an `IPINFO_API_KEY` is set,
otherwise `ipapi.co` — to show your exit IP, its location and network owner. A
VPN/proxy/hosting flag appears only with an IPinfo key on a plan that returns
the privacy object; the keyless `ipapi.co` fallback does not report one. It is a
quick way to confirm a tunnel changed your apparent location. **Run test**
performs a DNS-leak test through bash.ws: it looks up a set of probe hostnames
with the resolvers in your system resolver configuration, then reads back which
resolvers answered and whether any sit on a different network than your exit IP.
Check public IP and Run test contact their services as soon as you press them;
there is no extra confirmation dialog. Unlike the configured-DNS list above, the
test shows who actually answered — but it is still not proof of anonymity, and a
per-application route can differ.

**Known limitation:** macOS can also use resolvers scoped to a single interface
that a resolver-configuration read does not show, and a public resolver reached
through the tunnel can read as a possible leak because it sits on a different
network than the server. Treat the verdict as a clue to follow up. After you
connect or disconnect, the local readout refreshes automatically, and the public
IP re-checks only if you already ran it this session.

### Reading the cards

1. Check **Connection summary** for the high-level state.
2. In **Detected tunnels**, a recent handshake is stronger evidence than an
   interface name alone. A zero or old handshake suggests that the interface
   exists but has not recently reached its peer.
3. Compare **Default interface** with the behavior you expect. Full-tunnel VPNs
   normally influence the default route; split tunnels may not.
4. Treat **Configured DNS servers** as context. If DNS behavior matters, follow
   up with **Your IP & DNS → Run test**, keeping its limits in mind.
5. Copy an individual card when asking the Advisor for help. Never copy private
   keys or complete secret configuration files into an AI request.

### Profile comparison

Tunnel reads the VPN Agent's live profile list when it exists and otherwise
reads the bundled starter list. This read does not create a file, save a
selection, or expose key material. Select **No profile comparison** when you only
want a machine-wide snapshot. The list is read when the panel opens and when you
select **Reload**; importing a config updates the Server list but not this one
until you reload.

Only non-secret profile fields enter Sentinel's report; key-like and unknown
fields are discarded. The comparison is intentionally cautious. An interface
match plus a recent handshake is strong evidence that the chosen WireGuard
profile is communicating. A present endpoint and valid port only mean the saved
profile values look usable; Tunnel deliberately does not query the live peer
endpoint or claim that the active interface is connected to that exact server.
A different default interface is not automatically a failure because split
tunnels legitimately keep the ordinary default route. For a full tunnel, a
route mismatch is a reason to inspect `AllowedIPs` and optionally compare the
public IP—not proof by itself.

On macOS, a tunnel often appears as an automatically assigned `utun` interface
instead of the profile's friendly name. Tunnel keeps an exact-name mismatch
visible and asks you to confirm it in the WireGuard app; it does not assume that
an arbitrary active `utun` belongs to the selected profile.

**Known limitation:** this also applies to a tunnel Sentinel itself started. Right
after a verified Connect, Connection Check may still report the selected tunnel
as not active. This has not been confirmed on a Mac yet; until it is, trust the
Execution tab's result and **Your IP & DNS → Local**, which lists the tunnel
interface and its address.

The **Recommended next steps** card prioritizes concrete follow-up work. Follow
only steps that fit your intended topology; it is guidance based on a snapshot,
not an automatic repair system.

## Safe Action Preview

Choose Connect, Disconnect, or Restart and select **Preview**. Tunnel shows:

- the exact profile and interface it would target;
- expected network effects, including possible route/DNS changes or a brief
  outage during restart;
- checks to perform before making the change manually;
- proposed `wg-quick` commands in a copyable card.

WireGuard is the only protocol currently supported by action previews. OpenVPN
and unknown protocols produce no command. The preview has no execution path: it
does not open a shell, request an admin password, or change a tunnel, route, DNS
setting, or firewall rule. A missing or unsafe interface name produces no
command. It uses the **Compare profile** picker and shows the plain
`sudo wg-quick` form for the profile's interface; the live Connect confirmation
shows the exact command that would really run. After any change, return to
Connection Check and collect a fresh snapshot rather than relying on old status.

If Sentinel is closed or Portable Emergency Reset is used during a check, the
app first cancels and finishes the background worker before closing or erasing
Sentinel-owned portable data. This avoids leaving the check running after its
screen has gone away. A Connect in flight is handled as described under VPN
Connection.

![Tunnel action preview](docs/training/images/tunnel-action-preview.png)

## Config Inspection

Select **Inspect config…** and choose a WireGuard `.conf` file when you want to
understand its intended behavior before using it. Tunnel shows interface
addresses, DNS values, peer count, endpoints, `AllowedIPs`, and whether the file
describes a full or split tunnel. If you already ran Connection Check, it also
compares that intent with the current route and DNS snapshot.

The parser has a strict privacy boundary: `PrivateKey` and `PresharedKey` values
are discarded as each line is read. The result contains only a non-secret
summary; the original file is not copied into the result, an AI prompt, Saved
Chats, or the run log. The inspection is local, bounded to a 1 MiB text file,
does not resolve the endpoint, and cannot connect or change the VPN. It does not
list `PostUp`-style hooks; the Connect review does.

Treat comparisons as evidence, not proof. On macOS the WireGuard app may expose
a friendly profile as a `utun` interface. A full-tunnel route mismatch can also
mean the tunnel is simply stopped. Re-run Connection Check after a deliberate
change before drawing a conclusion.

## Privacy tab

Three cards. Each says plainly what it does not do.

**Hardware address.** Pick an interface, then *Randomise…* or *Restore hardware
address…*. The review shows `old → new` and warns that Wi-Fi will be switched off
and on. macOS asks for your administrator password. This changes what the local
network sees, nothing more: your router still sees your traffic, and the change
lasts until restart or Restore. For Wi-Fi, also set Private Wi-Fi Address to Off
for that network or macOS may override it.

**Tor.** *Start Tor…* runs a local client on 127.0.0.1:9250 (never a relay).
*Check…* asks the Tor Project's check site, through Tor, whether you really exit
through Tor. *New identity* asks for fresh circuits. Tor hides where you connect
from, not who you are; logging in, cookies and your browser fingerprint still
identify you. Install with `brew install tor`.

**Proxy chain.** Build an ordered list of SOCKS5/SOCKS4/HTTP proxies (you can add
Tor as a hop), then *Test chain…*. The test names the site it contacts and each
hop that will see the request. The proxychains wrapper barely works on macOS: it
is ignored by Apple's own tools such as `/usr/bin/curl`, which looks exactly like
success, so always check the exit address. Passwords are stored owner-only and
never shown or logged.

Everything on this tab is audited (`mac-set`, `tor-*`, `chain-probe`), including
actions you declined.

## Servers tab — a VPN server you own

Create a site (remote VPS, or native on your home network), set its endpoint,
ports, routes and SSH details, add a peer per device, and Sentinel generates the
keys and certificates locally. *Export files…* and *Show QR…* hand a device its
config; both contain a private key and ask first (the QR is drawn from memory and
never saved). *Rotate keys…* and *Remove…* make an issued config stop working at
the next deploy. *Back up this site…* writes an AES-GCM encrypted file; the
passphrase is never stored. *Delete site…* needs you to type its name, and if no
backup exists, to accept that the keys are gone for good.

To put the server online: *Check SSH…* (key-based only; the first host key is
trusted and its fingerprint shown; compare it with the one your VPS provider
gives you; Deploy is refused until this has been done), *Preview deploy* (nothing runs; keys are
replaced by `<redacted N bytes>`), *Deploy…*, then *Server status…* to see which
devices have handshaked. *Teardown…* removes the server (type the site name).
*Use for Connect…* adds one peer as a profile so Tunnel's Connect can use it from
this Mac. Everything is audited (`deploy`, `teardown`, `export`, `backup`, …).

While Tunnel is on screen a local health monitor watches the selected profile's
tunnel and your DNS resolvers and warns in the status line if the tunnel drops. It
never contacts the internet and never changes anything.

## Deployment choices

**Remote (VPS)** routes traffic through a rented server and can change the
public exit address. **Native (home LAN)** provides encrypted access back into
your home network; it normally does not hide or change your home public IP.

Choose WireGuard, OpenVPN TCP/443 fallback, or both. Enter the server host, SSH
user, home subnet and server egress interface where relevant; these feed Build
Config and the Advisor and do not affect Connect. Incorrect routes can cut off
connectivity, so verify values before applying generated material.

## Advisor

Ask about topology, connection failures, firewalls, DNS/IPv6/WebRTC leaks or
kill-switch behaviour. The mode, protocol, server host and home subnet are
included as context. Review the route before sending infrastructure details to a
cloud model. The Advisor's background knowledge also mentions Tor, proxy chains
and MAC randomisation; Tunnel has no controls for those.

## Config Builder

Build Config produces server/client templates and a deployment runbook with
clearly marked key placeholders, an outline of the OpenVPN TCP/443 fallback, and
in remote mode a short macOS kill-switch sketch. The sketch is for orientation:
it is not what the Arm button loads. It does not create real keys, connect to a
server or change system settings. Generate keys locally, protect private keys,
back up working configurations and keep an independent recovery session open
when changing a remote firewall.

## Beginner exercise

Run the local-only Connection Check with no VPN active. Identify which VPN
tools are installed and find the default interface. Explain why "no active
tunnel detected" is a status observation rather than proof of a fault.

## Intermediate exercise

Generate a Native WireGuard example for a fictional home subnet. Locate the
placeholders and explain why native mode does not change the internet exit IP.

Then select a saved profile and preview Disconnect. Identify the warning about
ordinary traffic resuming when no kill switch is active. Confirm that the VPN
remains unchanged after generating the preview.

Finally, choose an **Example** profile marked `(template)` under **Server** and
select **Connect**. Confirm that Tunnel refuses without asking for a password,
then find the matching `refused` line in `tunnel_audit.jsonl`.

## Independent exercise

With a VPN you own connected, run the local check twice: once immediately and
once after ordinary browsing. Compare handshake age and transfer totals. If you
also opt into the external check, record the public IP before and during the
tunnel without placing either address into an AI prompt. Write a short verdict
that separates observations, likely explanations, and what remains unverified.

Then, with a config you wrote yourself, run a gated Connect and read every line
of the review before answering. In your verdict, state what "verified locally"
did and did not cover.

## Best-practice checklist

- Keep an independent recovery session open before changing a remote firewall.
- Back up site state and client configurations before deployment or key rotation.
- Keep private keys in files only you can read (`chmod 600`); Tunnel warns when
  an imported config is readable by other users, and it never stores or shows
  key values.
- Use the local check before the Advisor; send only the non-secret card needed
  to explain the problem.
- Inspect a configuration locally before importing it, read any lines listed
  under "runs commands as administrator", and never paste the original file or
  private-key lines into an AI prompt.
- Treat an Action Preview as a review artifact. Confirm the target and keep
  recovery access available; to act, use the gated Connect or Disconnect, or your
  normal trusted VPN tool.
- Before a live **Connect** or **Disconnect**, read the Execution review, confirm
  the exact target, and keep a rollback and independent recovery path ready. The
  confirmation defaults to No for a reason.
- After a live connection completes, verify it with Connection Check and **Your
  IP & DNS**; the status line explicitly does not confirm traffic protection.
- Treat an active tunnel, public-IP change, DNS configuration, and kill-switch
  behavior as separate checks. One passing result does not prove the others.
- Know the recovery command before you arm the kill switch, and disarm it before
  you quit if you do not want traffic left blocked.
