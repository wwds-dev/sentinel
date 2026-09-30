# Tunnel — personal VPN design and troubleshooting

**Goal:** compare the current VPN state with an intended profile, understand the topology, and either create a reviewable configuration or run a gated connection. **Time:** 30 minutes.

![Tunnel workspace](docs/training/images/tunnel.png)

Tunnel is for infrastructure you own or administer. It has six distinct
paths: **VPN Connection (live)** actually connects or disconnects a real
tunnel, but only through an execution gate; **Connection Check** reads current
status; **Safe Action Preview** shows but cannot run a proposed change; the
**Advisor** uses an AI model; and **Build Config** and **Config Inspection** are
deterministic and offline. A status check, preview, or inspection does not send
anything to a model and does not count against an AI budget.

The amber banner at the top of the screen states the boundary: connecting starts
WireGuard or OpenVPN and may ask for your administrator password, traffic
protection is not verified afterwards, and the example country profiles are
templates that will not connect until you import a real config. Only the live
connection and the kill switch can change your machine; every other path stays
read-only.

## VPN Connection (live)

This is the only path that touches a real tunnel. Choose a **Server** profile,
or select **Import config…** to load a WireGuard `.conf` or OpenVPN `.ovpn` file;
importing saves the profile locally and selects it. The status line reads
**Connection state not checked** until you act — Tunnel does not silently probe
the tunnel.

Selecting **Connect** or **Disconnect** never runs the command immediately. It
first opens the **Execution** results tab with a target review, and only then
asks a Yes/No question that defaults to **No**. The gate has fixed stages:

1. **Target review** validates the profile and lists any blockers. If the review
   is not allowed, Tunnel refuses, shows why, and executes nothing.
2. **Explicit confirmation** — you must confirm the exact action and target.
3. **Administrator prompt** — macOS asks for your password; Tunnel never stores it.
4. **Post-change check** re-reads local state after the command so the result
   reflects what actually happened, not what was requested.
5. **Local audit** records one JSON line per privileged attempt — including
   refused ones — in `tunnel_audit.jsonl`.
6. **Rollback guidance** travels with every review and outcome, so you always
   have the step that undoes what you just did.

A template or example profile is refused before any of this: Tunnel tells you to
import a real config or set a real endpoint first. When a connect command
completes, the status line says the command completed and that **connection
state and traffic protection are not verified** — confirm with Connection Check
and, if it matters, an external IP check, rather than trusting the button alone.

### Kill switch

**Arm** installs a firewall rule that blocks all traffic except the selected
tunnel's endpoint, so a dropped tunnel cannot leak; it needs macOS `pf` and your
administrator password, and refuses to arm if the endpoint cannot be exempted.
**Disarm** restores ordinary traffic. Arm the kill switch against a real server
profile, not a template. Treat arming, connecting, and verifying no-leak as three
separate steps: an armed kill switch proves traffic is blocked when the tunnel is
down, not that the tunnel itself is protecting you.

## Connection Check

Start here when a VPN is slow, appears disconnected, or you simply want to know
what this Mac can see. Choose a saved profile under **Compare profile**, leave
**Include public IP and latency** off, and select **Check Connection**. Choosing
a profile here does not activate it or change the VPN Agent's saved selection.
Tunnel then reports:

- whether the WireGuard app, `wg`, `wg-quick`, and OpenVPN command are present;
- active WireGuard interfaces and whether an OpenVPN process was detected;
- peer count, most recent WireGuard handshake, and transfer totals when the
  local `wg` status tool allows those reads;
- the current default network interface and gateway;
- the DNS servers configured on this Mac;
- whether the selected profile's interface is active, whether it has a recent
  handshake, whether its saved endpoint and port look usable, and whether the
  default route matches the interface.

The default check is entirely local and read-only. It does not ask for an admin
password, connect or disconnect a tunnel, alter a route, touch the firewall, or
read private keys.

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
visible. **Local** reads your LAN address and any active tunnel interface with no
network contact. **Check public IP** contacts an address service — IPinfo when
an `IPINFO_API_KEY` is set, otherwise `ipapi.co` — to show your exit IP, its
location and network owner. A VPN/proxy/hosting flag appears only with an IPinfo
key on a plan that returns the privacy object; the keyless `ipapi.co` fallback
does not report one. It is a quick way to confirm a tunnel changed your apparent
location. **Run test** performs a real DNS-leak test
through bash.ws: it resolves a set of probe hostnames using your configured
resolvers, then reads back which resolvers actually answered and whether any sit
on a different network than your exit IP. Unlike the configured-DNS list above,
this exercises the resolver path traffic really takes — but it is still not proof
of anonymity, and a per-application route can differ. After you connect or
disconnect, the local readout refreshes automatically, and the public IP
re-checks only if you already ran it this session.

### Reading the cards

1. Check **Connection summary** for the high-level state.
2. In **Detected tunnels**, a recent handshake is stronger evidence than an
   interface name alone. A zero or old handshake suggests that the interface
   exists but has not recently reached its peer.
3. Compare **Default interface** with the behavior you expect. Full-tunnel VPNs
   normally influence the default route; split tunnels may not.
4. Treat **Configured DNS servers** as context. If DNS behavior matters, follow
   up with the standalone VPN Agent's deeper checks.
5. Copy an individual card when asking the Advisor for help. Never copy private
   keys or complete secret configuration files into an AI request.

### Profile comparison

Tunnel reads the standalone VPN Agent's live profile list when it exists and
otherwise reads the bundled starter list. This read does not create a file,
save a selection, or expose key material. Select **No profile comparison** when
you only want a machine-wide snapshot.

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

On macOS, a tunnel managed by the WireGuard app may appear as an automatically
assigned `utun` interface instead of the profile's friendly name. Tunnel keeps
an exact-name mismatch visible and asks you to confirm it in the WireGuard app;
it does not assume that an arbitrary active `utun` belongs to the selected profile.

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
command. After any manual change, return to Connection Check and collect a fresh
snapshot rather than relying on old status.

If Sentinel is closed or Portable Emergency Reset is used during a check, the
app first cancels and finishes the background worker before closing or erasing
Sentinel-owned portable data. This avoids leaving the check running after its
screen has gone away.

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
does not resolve the endpoint, and cannot connect or change the VPN.

Treat comparisons as evidence, not proof. On macOS the WireGuard app may expose
a friendly profile as a `utun` interface. A full-tunnel route mismatch can also
mean the tunnel is simply stopped. Re-run Connection Check after a deliberate
manual change before drawing a conclusion.

## Deployment choices

**Remote (VPS)** routes traffic through a rented server and can change the
public exit address. **Native (home LAN)** provides encrypted access back into
your home network; it normally does not hide or change your home public IP.

Choose WireGuard, OpenVPN TCP/443 fallback, or both. Enter the server host, SSH
user, home subnet and server egress interface where relevant. Incorrect routes
can cut off connectivity, so verify values before applying generated material.

## Advisor

Ask about topology, connection failures, firewalls, DNS/IPv6/WebRTC leaks or
kill-switch behaviour. Deployment fields are included as context. Review the
route before sending infrastructure details to a cloud model.

## Config Builder

Build Config produces server/client templates and a deployment runbook with
clearly marked key placeholders. It does not create real keys, connect to a
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

## Independent exercise

With a VPN you own connected, run the local check twice: once immediately and
once after ordinary browsing. Compare handshake age and transfer totals. If you
also opt into the external check, record the public IP before and during the
tunnel without placing either address into an AI prompt. Write a short verdict
that separates observations, likely explanations, and what remains unverified.

## Best-practice checklist

- Keep an independent recovery session open before changing a remote firewall.
- Back up site state and client configurations before deployment or key rotation.
- Store private keys only in the VPN Agent's protected state location.
- Use the local check before the Advisor; send only the non-secret card needed
  to explain the problem.
- Inspect a configuration locally before importing it, and never paste the
  original file or private-key lines into an AI prompt.
- Treat an Action Preview as a review artifact. Confirm the target, keep recovery
  access available, and make the change through your normal trusted VPN tool.
- Before a live **Connect** or **Disconnect**, read the Execution review, confirm
  the exact target, and keep a rollback and independent recovery path ready. The
  confirmation defaults to No for a reason.
- After a live connection completes, verify it with Connection Check; the status
  line explicitly does not confirm traffic protection.
- Treat an active tunnel, public-IP change, DNS configuration, and kill-switch
  behavior as separate checks. One passing result does not prove the others.
