# Tunnel: bringing the VPN Agent companion features into Sentinel

Status: design, approved for build · 2026-10-09 · owner decision: these features
stay and must be fully functional in Sentinel.

## What exists and what is missing

The libraries are complete and well written (about 5,300 lines under
`agents/vpn_agent/`). What Sentinel lacks is everything around them: no screen
reaches them, their state lives outside Sentinel's data folder, two of them use
privilege in a way Sentinel's policy forbids, and none has a test in this repo.

| Feature | Library | Missing in Sentinel |
|---|---|---|
| MAC address changer | `services/macaddr.py` | UI, confirmation, audit; uses cached `sudo` |
| Tor (local client) | `services/tor.py`, `socks_client.py` | UI, consent, audit; path and pid defects (D7, D8) |
| Proxy chain | `services/proxychain.py` | UI, consent for the probe, audit |
| Health monitor | `services/health_monitor.py` | wiring to Tunnel status, start/stop with the panel |
| VPN servers you own | `server/model, store, provision, keys, pki, render, bootstrap` | UI for sites and peers |
| Deploy / status / teardown | `server/deploy.py` | UI, preview, confirmation, audit; native deploy uses cached `sudo` |
| Export (files, QR) | `server/export.py` | UI; needs `segno` |
| Encrypted backup / restore | `server/backup.py` | UI; needs `cryptography` pinned |
| Obfuscation (stunnel, onion service) | `server/obfuscation.py` | options in the site editor, client instructions |
| Profiles | `services/profile_store.py` | merge with Sentinel's imported-profile list |

## Decisions

**D1 — State lives in Sentinel's data folder.** `server/paths.state_dir()`
returns `user_data_base()/vpn` (the folder OpenVPN already uses), so portable
mode, Emergency Reset and Sentinel backups cover sites, keys, the Tor data dir,
the proxy chain and kill-switch state. On first run, if
`~/Library/Application Support/VPN Agent` exists and the new folder does not,
its contents are **copied** (never moved, never deleted), permissions re-hardened
to 0700/0600, and a one-line notice says where the old copy is.
`VPN_AGENT_STATE_DIR` keeps working for tests.

**D2 — One privilege path.** Every root action goes through
`privileged.run_as_root(..., allow_cached_sudo=False)`: the macOS dialog, never a
cached ticket. That changes `macaddr.set_mac` and native deploy. Native deploy
cannot pipe a script through the dialog, so it writes the installer to a
0600 file inside the private state dir, runs `bash <file>` via the dialog, and
removes the file in a `finally` (the script contains server keys). Remote deploy
needs no local root and stays `ssh … bash -s` with the script on stdin.

**D3 — Every action that changes something or contacts something is gated and
audited**, using the Tunnel pattern that already exists: a review that lists
exactly what will happen (command, target, files), blockers that disable Yes,
a default-No confirmation, an off-thread worker with cancel/join, and one line in
`tunnel_audit.jsonl` for every attempt including refused and declined ones.

| Action | Gate | Audit action |
|---|---|---|
| MAC randomise / set / restore | review (interface, old → new, Wi-Fi will drop) + confirm + dialog | `mac-set` |
| Tor start / stop / new identity | confirm on start (what Tor is and is not); stop and new identity no confirm | `tor-start`, `tor-stop`, `tor-newnym` |
| Tor check / proxy-chain probe | confirm naming the check URL contacted through the hops | `tor-check`, `chain-probe` |
| Create / edit site, add / remove / rotate peer | none (local, reversible) except remove peer and rotate keys: confirm, stating every issued config stops working | `site-*`, `peer-*` |
| Check SSH | confirm naming the host; shows accept-new host-key policy | `deploy-check` |
| Deploy | review = rendered script with secrets redacted, target, mode + confirm + (native) dialog | `deploy` |
| Teardown | review + typed confirmation of the site name | `teardown` |
| Delete site | typed confirmation of the site name, and only after an export or backup exists or the user ticks "I have no backup and accept this" | `site-delete` |
| Export config / QR | confirm that the file holds a private key; file 0600 | `export` |
| Backup / restore | passphrase rules from `backup.passphrase_problems`; restore over an existing site needs confirm | `backup`, `restore` |

**D4 — Secrets never reach the screen, logs, chats or a model.** Private keys,
the CA key, PSKs and backup passphrases are redacted with
`vpn_execution.redact_secrets` everywhere output is shown or audited. The deploy
preview shows the script with base64 payloads replaced by `<redacted N bytes>`.
QR codes render only in a dialog that is never saved to history. None of these
features talks to an AI provider. Canary tests cover every view.

**D5 — Host keys.** Deploy uses `StrictHostKeyChecking=accept-new` (first use is
trusted, a changed key is refused). The Check SSH step comes first and shows the
fingerprint it accepted, so the person sees it before any key material leaves.

**D6 — Honesty copy is kept.** Each library's opening docstring states plainly
what the feature does not do (MAC is one hop; Tor plus a VPN is not anonymity;
proxychains barely works on macOS). That text becomes the tab's info line and
tooltip, and goes into docs/agents/vpn.md and the training lesson.

**D7 — Tor config with spaces.** `torrc` values containing spaces
(`Application Support`, `Sentinel Data`) are quoted. `Log notice file` takes
space-separated arguments, so a path with a space breaks it today. Fix and test.

**D8 — Tor stop checks identity.** Like OpenVPN, `stop()` confirms the pid is
our `tor -f <our torrc>` before signalling; a reused pid is never killed.

**D9 — Dependencies.** Add `segno` and `cryptography` (already present through
paramiko, now pinned directly) to requirements.txt and the PyInstaller
hidden imports. `tor`, `stunnel`, `proxychains-ng` and `wireguard-tools` stay
optional system tools: each tab detects them and shows the Homebrew command.

**D10 — Health monitor.** Starts with the Tunnel panel, stops on close, Emergency
Reset and app quit (joined like the other workers). Its signals update the
Tunnel status line and Diagnostics; it never changes anything by itself.

## Screens

The Tunnel panel gains two tabs beside the existing ones:

- **Privacy** — three cards: *Hardware address* (interface list, current and
  hardware MAC, Randomise / Restore, the Private Wi-Fi Address note); *Tor*
  (installed, running, bootstrap %, Start, Stop, Check, New identity);
  *Proxy chain* (hop table with add/edit/remove, Test chain, proxychains
  config path and the macOS limits).
- **Servers** — site list (New, Import backup) and a site editor: mode
  (remote VPS / native), endpoint, ports, OpenVPN on/off, obfuscation
  (none / stunnel / onion), SSH target; peers table (add, disable, rotate,
  remove, export file, show QR); actions (Check SSH, Preview deploy, Deploy,
  Server status, Teardown, Backup, Delete); a live output pane fed by the
  deploy stream. A deployed site's peer can be registered as a Tunnel profile in
  one click (`export.register_profile`), so Connect works on it.

The existing Diagnostics, Advisor, Config & Commands, Action Preview,
Config Inspection and Execution tabs are unchanged.

## Tests (all offline, no real root, ssh, tor or network)

- Port the companion's own test suite from the archived VPN Agent repository
  (on the Mac under `_archive/nested_git`) where it exists; otherwise write unit
  tests per module against the public functions listed above.
- Per action in D3: positive, invalid input, declined, cancel/error and
  persist-and-restart, with fake runners (`Recorder`), fake `subprocess`,
  fake sockets. Audit line written for every attempt.
- Canary tests (D4) over every view, the audit log, exported files' modes and
  the deploy preview.
- D1 migration: copy not move, permissions, idempotent, portable root.
- D7/D8 regression tests; D2 test that `macaddr` and native deploy reach the
  dialog runner with `allow_cached_sudo=False`.
- `.coveragerc` loses its companion omissions; coverage for these modules is
  reported like the rest.

## Build order

1. D1 paths and migration, D9 dependencies, D7/D8/D2 library fixes, module tests.
2. Privacy tab (MAC, Tor, proxy chain) with gates, audit and tests.
3. Servers tab: sites, peers, export, backup/restore.
4. Deploy, server status, teardown, obfuscation options, register-as-profile.
5. Health monitor wiring.
6. Docs (README, docs/agents/vpn.md, training lesson, tooltips, manual test
   cases), screenshots, full suite, then an independent review of the key
   handling and deploy paths on Opus.

## Manual acceptance (needs the Mac and, for deploy, a test VPS)

MAC randomise and restore on a USB Ethernet adapter; Tor start, check,
new identity, stop; proxy-chain probe through a SOCKS proxy you control; create a
site, deploy to a throwaway VPS, export a peer, import it in the WireGuard app,
connect from Tunnel, server status shows the handshake, teardown, delete; backup
and restore on a second account.
