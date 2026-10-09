# TUNNEL — VPN connection, checks & self-hosted VPN design

`key: vpn` · class: `agents/vpn_agent/sentinel_chat_agent.py → VpnAgent` · panel: `ui/panels/vpn.py → VpnPanel`

> Defensive, self-hosted infrastructure only — a VPN you own end to end, on hosts you are authorised to run.

## What it does
Starts and stops a real WireGuard or OpenVPN tunnel behind a confirmation gate, and keeps read-only checks, VPN design help and config tools in the same workspace. The amber banner at the top of the panel states the boundary: Connect starts WireGuard/OpenVPN, traffic protection is not verified, and the example country profiles are templates that will not connect. Six paths share one workspace; only the first, and the kill switch beside it, can change your machine. The VPN library under `agents/vpn_agent/` began as the standalone **VPN Agent** app and now lives in-tree.
1. **VPN Connection (live)** — Connect and Disconnect for a WireGuard or OpenVPN profile, plus Import config… and an optional pf kill switch. Every attempt goes through a target review, a default-No confirmation, the macOS administrator prompt, a fresh local check and a local audit line. Details in "The live path" below. No model is used.
2. **Connection Check** — a read-only, model-free snapshot of installed VPN tools, active WireGuard state, whether any OpenVPN process is running, the default route, and configured DNS. It can compare that snapshot with a selected profile and turn mismatches into plain-language next steps. WireGuard peer keys are not displayed and private key material is never requested. Optional public-IP and latency checks require a separate confirmation.
3. **Advisor** — an LLM that reasons from a structured system prompt about remote vs native topology, WireGuard vs the OpenVPN 443 fallback, the fail-closed kill switch, and DNS/IPv6/WebRTC leaks. It is asked to answer as SUMMARY · TOPOLOGY · SECURITY & LEAKS · COMMANDS/CONFIG · RECOMMENDATIONS. It is the only paid path, so it goes through the request guard. Its background knowledge also mentions Tor, proxy chains, MAC randomisation and obfuscation from the original VPN Agent; Tunnel has no controls for those.
4. **Config & Deploy Builder** — deterministic and offline: renders a WireGuard **server** + **client** config plus a numbered stand-up runbook, an outline of the OpenVPN TCP/443 fallback, and (remote mode) a short macOS kill-switch sketch. No LLM, no network, no crypto dependency — keys are clearly-marked placeholders next to the exact `wg genkey` commands that fill them. The kill-switch sketch is for orientation only: it is not what the Arm button loads, and Sentinel does not parse-check it with pf.
5. **Safe Action Preview** — shows the expected effects, pre-flight checklist, and proposed `wg-quick` commands for Connect, Disconnect, or Restart. It is display-only: it does not run the command, request administrator access, or change network state. Use the live path to actually act.
6. **Config Inspection** — reads one WireGuard file the user explicitly selects, discards `PrivateKey` and `PresharedKey` values during parsing, and reports only routing, DNS, interface and endpoint metadata. It can compare those non-secret values with the latest Connection Check without copying the file into chat or logs.

The **Your IP & DNS** group sits between VPN Connection and Deployment and is always visible. It is independent of the other paths (see Inputs).

## The live path — Connect, Disconnect, Import
What you see, in order:
- **Server** lists your saved profiles and five example country profiles marked `(template)`. **Import config…** loads a WireGuard `.conf` or OpenVPN `.ovpn`: `.ovpn` is OpenVPN, any other file is read as WireGuard. Import reads the server address and port out of the file, saves a profile named after it, and selects it. Tunnel remembers where the file is rather than copying it, so moving or deleting the file later makes Connect refuse. Import does not refresh the Compare profile picker; use Reload there.
- The status line starts at **Connection state not checked.** Tunnel does not probe the tunnel on its own.
- **Connect** or **Disconnect** never runs anything immediately. It first fills the **Execution** tab with a target review, then asks a Yes/No question that defaults to **No**. The question is the review: exact target and command, the config's routing/DNS intent (WireGuard files only; keys discarded), warnings, and the steps that undo the action.
- If you answer Yes, macOS asks for your administrator password in its own dialog. The command runs off the interface thread, then Tunnel re-reads local state and the Execution tab shows **Result**, **Post-change check** (Verified, Not verified or Unknown), the review, redacted tool output, and where the attempt was audited. The status line says the same in one sentence. Connect and Disconnect stay disabled while it runs, a second connection or kill-switch action is refused with "A connection action is already running", and there is no Stop for it.
- Afterwards the **Your IP & DNS → Local** readout refreshes; the public IP re-checks only if you already ran it this session.

### What blocks an action
A blocked action runs nothing and asks for no password. The reason appears in a dialog and, except for a template, in the Execution tab.
| Blocker | Notes |
|---|---|
| Template profile | Connect only, refused before a review is built. Disconnect is deliberately not blocked for a template, so a stuck tunnel can always be brought down. |
| `wg-quick` or `openvpn` not installed | The message names the Homebrew package. |
| No interface or config file on the profile | |
| Config file missing | Connect only. Includes a file you moved after importing it. A WireGuard file that is unreadable, over 1 MiB or not UTF-8 text is refused too. |
| Config name wg-quick would reject | WireGuard names the interface after the config file: letters, digits and `_=+.-` only, at most 15 characters. Rename the file and import it again. |
| WireGuard config with no peer endpoint | Connect only. A `.conf` that is really an OpenVPN file lands here. |
| Unsupported protocol | A profile whose protocol is neither WireGuard nor OpenVPN. |
| Tunnel already up | WireGuard: the run record for that interface exists. OpenVPN: the process Sentinel started is running. |

Warnings do not block: a split tunnel (only the listed networks use the VPN), a config file readable by other users on this Mac (it holds your private key), and on Disconnect either "traffic returns to your ordinary network in the clear" or "the kill switch is armed, so traffic stays blocked until you Disarm it".

### Config hooks that run as administrator
A WireGuard config can contain `PreUp`, `PostUp`, `PreDown` and `PostDown` lines, and an OpenVPN config can contain script or plugin directives such as `up`, `down`, `route-up`, `plugin` and `script-security`. The tunnel tool runs these as administrator. The **Connect** review lists each one (each cut at 100 characters) in its own section, repeats them in the confirmation text and adds a warning, and after you answer Yes a second dialog lists them again and defaults to No. Declining at either step is recorded as declined. Known limitation: the list appears on Connect only (not on Disconnect or in Inspect config…), and other wg-quick directives such as `SaveConfig` are not flagged.

### Administrator prompt
Connect, Disconnect, Arm and Disarm always go through the macOS authorisation dialog, even if an unrelated sudo ticket is cached, and Tunnel never stores the password. The prompt and the command share a 30-second limit. Cancelling the prompt, or a command that fails, is reported as Failed and audited. If Sentinel itself is running as root there is nothing to ask for and the command runs directly; running Sentinel that way is not recommended.

### The post-change check
It re-reads local state instead of trusting the command's exit status. WireGuard: the tunnel's run record under `/var/run/wireguard` (and, for a full tunnel, whether the route to a public address now uses the tunnel interface; `route get` sends no packets). OpenVPN: whether the process Sentinel started exists. **Verified** means that state was seen. It does not mean a handshake completed, DNS is safe, or traffic is protected — run Connection Check and Your IP & DNS. A full tunnel is recognised by a literal `0.0.0.0/0` or `::/0`; a config that splits the default route into two halves is treated as split, so the route check is skipped.

### OpenVPN
OpenVPN is a basic client. Connect starts it as a background process with a log and a pid file in Sentinel's data folder (`vpn/`); the log is not shown in the panel. Disconnect stops only the process Sentinel started and can identify as its own: if it cannot, it refuses, stops nothing, and tells you to use the client that started the connection. Tunnel never stops OpenVPN by process name. That refusal happens after you answer Yes, and is audited as failed. The review shows an abbreviated command and no routing/DNS intent for OpenVPN. Configs that need a username and password prompt, pushed-DNS handling and live status are not supported. Connection Check counts any process named openvpn; Connect and Disconnect act only on Sentinel's own.

### Audit log
Every attempt is appended to `data/logs/tunnel_audit.jsonl` in Sentinel's data folder (the `Sentinel Data` folder on a portable install), set to owner-only (mode 600) where the volume supports it. That includes attempts that ended before anything ran: a template, a blocker, or a confirmation you declined (outcome refused or declined), as well as succeeded and failed runs and every kill-switch arm or disarm result. An entry holds the time, action, protocol, profile name, target path, command, outcome, blockers, warnings and the post-change result. Tool output and error text have key fields, inline key blocks and long key-like strings scrubbed before they reach the Execution tab or the log (the log keeps the error text, not the full tool output); the scrubbing is pattern-based. The profile name and config path are logged, and a config path can contain your user name.

### Kill switch (optional, macOS pf)
**Arm** loads a pf anchor that blocks network traffic except: loopback, the tunnel interface that exists at that moment (WireGuard's, or the OpenVPN device read from OpenVPN's log — if OpenVPN is up but its device cannot be read, Arm refuses rather than block traffic inside the tunnel), the tunnel's own transport to the profile's server (WireGuard is UDP; OpenVPN follows the protocol on the config's first `remote` line, then its `proto` line; the port comes from the profile, then the config's `remote` line, `rport` or `port`, then the protocol default; only the first `remote` is exempted), ICMP to the server, DHCP, and your local network. If the tunnel drops, everything else stays blocked until you press **Disarm**. The Arm confirmation (default No) states what stays open and prints the Terminal recovery command; read it before you answer. Arm asks for your administrator password. It refuses for a template, when the profile's endpoint cannot be resolved (a name is resolved once, when you arm), and when the generated rules fail pf's own parse check.
- Connect first, then arm: the tunnel interface is the one present at arm time, so re-arm if the tunnel is re-created.
- The first arm adds a small anchor block to `/etc/pf.conf` (a copy is kept as `/etc/pf.conf.vpn-agent.bak`) and switches pf on. Disarm removes the blocking rules; it does not remove that block or switch pf off.
- Recovery command, as printed in the Arm confirmation and in the first lines of the rules file: `sudo pfctl -a vpn-agent-killswitch -F all && sudo pfctl -F all -f /etc/pf.conf`
- Sentinel does not disarm on quit or Emergency Reset.
- Known limitation: the kill switch only recognises WireGuard tunnel interfaces. Sentinel does not stop you arming it for an OpenVPN profile, and then the OpenVPN tunnel's own traffic is blocked as well; use it with WireGuard profiles.
- Known limitation: the label next to the buttons reports what you did in this session ("Kill switch not armed." at start), not a live read, and long results are clipped; the full message is in the audit log.

### Closing Sentinel during a connection
Normal app close and Portable Emergency Reset both cancel every Tunnel worker, wait about two seconds for each, then end any that is still running. Cancelling does not interrupt the macOS prompt or a command already handed to macOS: Sentinel drops the result, so it may be neither shown nor audited, and a change that already happened is not undone. Re-run Connection Check after reopening.

### Where Tunnel keeps things
| What | Where |
|---|---|
| Audit log, OpenVPN pid file and log | Sentinel's data folder (`data/logs/`, `vpn/`); in Portable mode, inside `Sentinel Data`. |
| Imported profiles, kill-switch rules and marker | The VPN Agent state folder: `~/Library/Application Support/VPN Agent/` on a Mac. |
| Starter profile catalog | Bundled with the app and read only until the state folder has its own file. |
Known limitation: the VPN Agent state folder is outside Sentinel's data folder, so Portable mode does not carry it and Emergency Reset does not erase it. On a Mac with no profile file yet, the first import creates one holding only the imported profile, so the starter profiles disappear from both pickers.

## Privacy tab — hardware address, Tor, proxy chain

Three cards, each honest about its limits (the same text is in the tab).

- **Hardware address.** Randomise or restore an interface's MAC. One hop only:
  your router still sees your traffic; the change lasts until restart or Restore.
  Review shows `old → new` and warns that Wi-Fi will be cycled. Asks for the
  macOS administrator dialog (never a cached sudo). Audit action `mac-set`.
- **Tor.** A local client on 127.0.0.1:9250 (never a relay), state in Sentinel's
  data folder. Start asks first; Stop only signals the Tor Sentinel started;
  Check contacts check.torproject.org through Tor (asks first); New identity
  requests fresh circuits. Needs `brew install tor`. Audit `tor-start`,
  `tor-stop`, `tor-check`, `tor-newnym`.
- **Proxy chain.** Ordered hops (SOCKS5/SOCKS4/HTTP), saved 0600; passwords are
  never shown or logged. Test chain asks first, naming api.ipify.org and each hop
  (audit `chain-probe`). The proxychains wrapper barely works on macOS (SIP).

Every action runs off the interface thread, is audited including declined and
refused attempts, and none of it talks to an AI provider.

## Servers tab — VPN servers you own

Create a site (remote VPS or native), edit its settings, and manage peers (the
devices). Keys and certificates are generated locally and kept 0600 in
Sentinel's data folder. Local, reversible edits (create site, add peer,
enable/disable, change ports) need no prompt. These ask first, default No, and
are audited: rotate keys and remove a peer (issued configs stop working at the
next deploy), export files and show QR (they contain a private key; the QR is
drawn from memory and never saved), backup (passphrase checked, AES-GCM with
scrypt), restore over an existing site, and delete (type the site name; if no
backup or export exists you must also accept that the keys are gone for good).
Private keys, the CA key and passphrases never appear on screen, in the audit
log or in any model request.

### Deploying a server you own

On a site (Servers tab): **Check SSH** (asks first; key-based only, never a
password; the first host key is trusted and its fingerprint shown, a changed key
is refused), **Preview deploy** (the installer with keys replaced by
`<redacted N bytes>`; nothing runs), **Deploy** (refused until Check SSH has recorded the host key; asks first,
showing target, host-key fingerprint and what is installed; live output with
keys redacted), **Server status** (asks first;
read-only), **Teardown** (type the site name) and **Use for Connect** (writes one
peer's config, which holds a private key, and adds a profile so Connect can use
it). A remote deploy streams the installer over SSH and writes nothing to the
server's disk. A native deploy on this Mac writes the installer to a private
temporary file, runs it through the macOS administrator dialog (never a cached
sudo) and removes the file afterwards. Every site field (names, interface, DNS,
routes, SSH target, ports) is checked against an allowlist when a site is
saved, loaded, restored or deployed, so a backup from someone else cannot
smuggle a command into the installer. Names may use letters, digits, spaces
and `. _ - ( )`.

## Remote vs Native (the choice the agent keeps you honest about)
| | Remote (VPS) | Native (home LAN) |
|---|---|---|
| Runs on | a rented VPS, over SSH | hardware you own on your LAN |
| Traffic exits at | the server | your own home ISP |
| Hides your IP | yes | **no** |
| Changes apparent country | yes | **no** |
| Default routing | full tunnel (`0.0.0.0/0`) | split tunnel (LAN subnet) |

Native mode is an encrypted way **into** your network (NAS, printer, router), not a new way out.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Server | Profile used by Connect, Disconnect and the kill switch: saved profiles plus the example templates. It is separate from Compare profile. The starter profiles Home Server and GL-iNet Flint 2 name only an interface, so their review has no routing, DNS or hook lines and wg-quick looks for a config of that name in its usual folders. |
| Import config… | Load a WireGuard `.conf` or OpenVPN `.ovpn`, save it as a profile, select it. |
| Connect / Disconnect | Build the target review and ask for confirmation. Nothing runs until you answer Yes (default No). |
| Kill switch Arm / Disarm | Optional macOS pf block. Arm asks for confirmation (default No) and your administrator password; Disarm asks only for the password. |
| Mode | `Remote (VPS)` or `Native (home LAN)`. |
| Protocol | `WireGuard` · `OpenVPN 443 fallback` · `Both`. |
| Server host | VPS IP / DDNS hostname → the client config `Endpoint`. |
| SSH user | Used in the remote deploy runbook. |
| LAN subnet | Native mode split-tunnel `AllowedIPs`. |
| Egress iface | Server NIC for the NAT `MASQUERADE` rule (default `eth0`). |
| Question | Free-text for the Advisor. The Deployment fields above feed Build Config and the Advisor only; they do not affect Connect. |
| Check Connection | Runs the local read-only diagnostic snapshot. No provider or model is used. |
| Compare profile | Reads the VPN Agent profile list without selecting, creating, or changing a profile. The saved active profile is preselected when available. Reload re-reads the list; Import does not. Action Preview uses this picker too. |
| Include public IP and latency | Off by default. When enabled, a confirmation names `api.ipify.org` and `1.1.1.1` before either is contacted. |
| Your IP & DNS → Local | Reads LAN and tunnel-interface IPv4 addresses from `ifconfig` off the UI thread. No exit IP is fetched and no network is contacted. It also runs once when the panel is created. |
| Your IP & DNS → Check public IP | Contacts an address service — IPinfo when `IPINFO_API_KEY` is set, otherwise `ipapi.co` — for the exit IP, location and network owner. It starts as soon as you press it, with no extra confirmation. A VPN/proxy/hosting flag appears only with an IPinfo key on a plan that returns it. Once run, it refreshes automatically after each connect/disconnect this session. |
| Your IP & DNS → Run test | Runs a DNS-leak test through `bash.ws`, also without an extra confirmation: it looks up 12 probe hostnames with the resolvers in your system resolver configuration, then reads back which resolvers answered and whether any sit on a different network than your exit IP. Known limitation: macOS can also use per-interface resolvers that this read does not show, and a public resolver reached through the tunnel can read as a possible leak, so treat the verdict as a clue. |
| Safe Action Preview | Choose Connect, Disconnect, or Restart and inspect a non-executing plan for a WireGuard profile. OpenVPN, unknown protocols, and unsafe or missing interface names produce no command. The preview shows the plain `sudo wg-quick` form; the live confirmation shows the exact command that will run. |
| Inspect config… | Choose one local WireGuard configuration for private-key-free routing and DNS inspection. No model, network request, or configuration change is involved. |
| Ask Advisor / Build Config / Stop / Clear | LLM answer · offline render · cancel the Advisor or a Connection Check · clear all result tabs. **Agent guide** opens this page. |

## Outputs
Tabs: **Diagnostics** (structured connection and profile-comparison cards), **Advisor** (LLM answer), **Config & Commands** (rendered configs + runbook), **Action Preview** (display-only change plan), **Config Inspection** (non-secret file summary) and **Execution** (the live target review, the result of the last Connect/Disconnect, the post-change check and the audit note). The Advisor receives the mode, protocol, server host and LAN subnet as context, so a question inherits what you picked; the SSH user and egress interface are not sent.

## How it works
- `VpnAgent.build_messages(prompt)` → system prompt + the context-prefixed question; runs through `ChatWorker` like the other agents.
- `build_configs(mode, protocol, server_host, ssh_user, lan_subnet, egress_iface)` returns the whole config + runbook as text — pure string assembly, so it is instant and safe to run offline.
- `load_vpn_profile_catalog()` reads the live profile file in the VPN Agent state folder when present and otherwise reads its bundled seed. It never seeds, edits, or changes the active profile, and only carries the non-secret name, endpoint, port, interface, notes, and protocol fields into the Compare picker.
- `vpn_connection.load_connectable_profiles()` feeds the Server picker with the full profiles (config path included) plus the example templates.
- `collect_vpn_diagnostics(include_external=False, selected_profile=...)` coordinates the library's status helpers in a background worker. It compares the selected interface and handshake/route expectations with observed state, while checking that the saved endpoint and port are usable profile values. It does not query or verify the live peer endpoint. The default path performs only local reads. External IP/latency calls cannot run unless the user selects the option and confirms it.
- `build_vpn_action_preview(action, profile, report)` validates the interface and returns sections and copyable commands. It has no subprocess or privileged execution path.
- `inspect_wireguard_config(path, report)` calls the library's parser, receives only its non-secret summary, and cautiously compares intended full/split routing and DNS with the most recent Connection Check. It does not resolve or contact the saved endpoint.
- `vpn_execution.review_execution(action, profile)` builds the target review: exact target and command, config intent, blockers, warnings, root-run hooks and rollback steps. `execute()` re-runs it inside the worker so a file changed after the dialog is still caught, runs `vpn_connection.connect()`/`disconnect()` only if nothing blocks, runs `post_change_check()`, and appends the audit line.
- `vpn_execution.record_refusal()` writes the same audit shape for the refusals and declined confirmations the panel decides before `execute()` is reached. `redact_secrets()` scrubs tool output, errors, refusal reasons and kill-switch detail.
- `vpn_connection.arm_killswitch(profile)` derives the transport to leave open from the profile and calls the library's `killswitch.arm`. All privileged commands go through `privileged.run_as_root(..., allow_cached_sudo=False)`, which uses the macOS dialog.
- `ui/workers.py: VpnConnectionWorker` runs Connect, Disconnect, Arm and Disarm off the interface thread. Its `cancel()` discards the result rather than interrupting a privileged call.
- `VpnPanel.shutdown()` cancels every active Tunnel worker (connection, check, IP, DNS, Advisor) and waits up to two seconds for each, then ends one that is still running. Normal app close and Portable Emergency Reset both use this path.
- Packaged builds include the non-secret starter profile catalog. If no live profile file exists, Tunnel can still open with the same safe baseline instead of depending on source-tree files.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/vpn_agent/sentinel_chat_agent.py` | Shared `SYSTEM_PROMPT`, `build_configs()` helpers, and `VpnAgent`. |
| `agents/vpn_agent/services/` | The in-tree VPN library the panel reaches: WireGuard status helpers, `privileged.py` (administrator runner), `killswitch.py` (pf anchor), DNS, public-IP and latency helpers. |
| `agents/vpn_agent/services/config_inspection.py` | Canonical bounded parser that discards private and pre-shared key values before returning a summary. |
| `agents/vpn_agent/server/paths.py` | Locates the VPN Agent state folder (profiles, kill-switch rules and marker). |
| `services/vpn_diagnostics.py` | Sentinel-specific read-only orchestration, selected-profile comparison, recommendations, and non-executing action previews. |
| `services/vpn_connection.py` | Profiles, import, `wg-quick` and kill-switch calls, example templates. |
| `services/vpn_execution.py` | The gate: target review, hooks, post-change check, redaction, audit log. |
| `services/openvpn_manager.py` | OpenVPN start/stop and tracked-process identity. |
| `ui/workers.py: VpnConnectionWorker, VpnDiagnosticsWorker, IpSnapshotWorker, DnsLeakWorker` | Keep system, privileged and network work off the UI thread; all support cancellation. |
| `ui/panels/vpn.py` | Panel, confirmations, results, and request lifecycle. |
| `ui/dialogs.py: shutdown_panels()` and `main.py: closeEvent()` | Share orderly worker shutdown between Portable Emergency Reset and normal app close. |
| `services/database.py: _seed_default_agents()` | Registers the `vpn` agent row. |
| `Sentinel.spec` | Bundles the starter profile catalog into frozen releases. |

`agents/vpn_agent/` also holds server-provisioning, Tor, proxy-chain, MAC-changer and health-monitor code from the original VPN Agent. Tunnel's panel does not reach it, and nothing in this guide describes it.

## Extend it
- **Real keys**: swap the placeholder key material for locally-generated X25519 keys; keep them out of chat logs. Tunnel's panel does not generate or store keys today.
- **More topologies**: add site-to-site or multi-peer variants to `build_configs()`.
- **OpenVPN inspection**: add an equivalent secret-filtering adapter before accepting OpenVPN files; the current inspector is WireGuard-only, which is why the OpenVPN review has no routing or DNS lines.
- **New privileged actions**: route them through `vpn_execution` so review, explicit confirmation, administrator authorisation, post-change verification, audit and rollback stay separate gates.

## Verification state
- Automated tests cover diagnostics, profile filtering/comparison, action-preview validation, the Connect/Disconnect gate (review, blockers, hooks, redaction, audit, post-change check), kill-switch inputs, import, and cancellation and shutdown behavior. They use fake privileged runners, fake state probes and temporary files, so they do not exercise a real tunnel, pf or the macOS password dialog.
- Not yet verified on a Mac, so treat as unconfirmed: that the WireGuard run records are readable and laid out as the post-change check expects; how the generated pf rules behave on a live network; whether Connection Check recognises a tunnel Sentinel started (it compares the profile's interface name with detected names, which on macOS are usually `utun` numbers, so it may report the selected tunnel as not active right after a verified Connect — use the Execution tab and Your IP & DNS → Local instead); and the behaviour of a slow or cancelled password prompt.
- The Learning Centre screenshots are generated from isolated sample state; the capture script never reads or displays the user's real profile names.
- Source and packaged-app paths are both covered: the library lives in-tree, and Sentinel bundles the non-secret starter catalog needed when no live profile file exists.

## Diagnostic boundaries
- A detected interface or OpenVPN process is evidence that software is active, not proof that every application is routed through it.
- The configured DNS-server list is useful context, not a complete DNS-leak test, and it can miss resolvers macOS scopes to a single interface.
- Public IP and latency reveal connectivity only. They do not prove anonymity, correct firewall policy, or protection against application-level leaks.
- A verified Connect or Disconnect means the expected local state was seen. It is not a handshake, DNS or leak test, and an armed kill switch proves only that traffic is blocked when the tunnel is down.
- Connection Check never deploys, connects, disconnects, edits routes, changes firewall rules, or reads a WireGuard private key. Only Connect, Disconnect, Arm and Disarm change the machine, and only after you confirm.
