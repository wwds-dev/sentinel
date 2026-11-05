# 0004 — Root runs only verified copies under `/private/var/run/sentinel-vpn`

Date: 2026-11-05 · Status: accepted by the owner 2026-11-05 (decision D8 in
`docs/specs/audit-tier2-privileged-layer.md`)

## Context
The tier-2 audit (security P1-2, code-review P1-4) found that root runs VPN configs
the user can still edit. Since S1 the reviewed bytes are hashed and the fresh review
must match the confirmed one, but that check happens when the password prompt opens:
afterwards root reads the user's own `.conf` (`services/vpn_connection.py`, the
`wg_quick_command` calls). Any process running as the user can swap the file in that
window, and WireGuard's `PreUp`/`PostUp`/`PreDown`/`PostDown` lines then run as root.
The same applies to OpenVPN's `.ovpn` and its referenced files (S8).

The fix needs somewhere root can keep a copy that the user cannot change, for as
long as the tunnel is up, because `wg-quick down` re-reads the config and runs its
down hooks. Where that is, and how it is laid out, is expensive to reverse: tunnels
started by one build are torn down by the next, so the path becomes part of the
on-disk contract.

## Decision
1. **One root-owned base directory**, `/private/var/run/sentinel-vpn`, `root:wheel`,
   mode `0755` (S8 stores pid and device files there that Sentinel reads as the
   user). Written as `/private/var/run`, not `/var/run`: on macOS `/var` is a symlink
   to `private/var`, and the scripts refuse a symlink anywhere in the part they own.
2. **Per-protocol, per-tunnel subdirectories**, `root:wheel 0700`:
   - `wg/<iface>/<iface>.conf` — the WireGuard copy (S7). `<iface>` must match
     `WG_INTERFACE_NAME` (`^[a-zA-Z0-9_=+.-]{1,15}$`), so it is safe in a path and
     wg-quick names the interface exactly as before.
   - `ovpn/` — the OpenVPN config and data copies, pid and device file (S8; laid out
     in the spec §1.3 and §5, not built now).
3. **The root script owns every check.** Inside the one administrator prompt, with
   `umask 077`: create the directories with `install -d -o root -g wheel`; refuse
   (exit 97) if the base or a subdirectory exists and is a symlink, is not a
   directory or is not owned by root; copy the user's file with
   `/bin/cat -- SRC > copy`; hash the **copy** with `shasum -a 256` and compare it
   with the hash the user confirmed; on mismatch remove the subdirectory and exit 98
   without running anything. Only then run the tool, and only on the copy. No path
   in a user-writable directory is used after the hash check.
4. **Lifetime.** The copy stays while the tunnel is up and is removed after a
   successful `down`, or straight away if `up` fails. Disconnect with no stored copy
   (a tunnel from an older build, started by hand, or a missing copy) falls back to
   copy-and-hash of the user's file with a fresh review that lists the down hooks.
   A stale `wg/<iface>/` from a crash is replaced by the next connect for that
   interface after the same ownership checks.
5. **Gated actions are not run as root.** Sentinel refuses gated actions while it
   runs as root (`privileged.ROOT_REFUSAL`, `e4caccd`), so the script is only ever
   reached through the macOS administrator dialog. (The spec's §1.2 note that the
   `geteuid() == 0` branch "works unchanged" predates that decision.)
6. **Nothing secret goes through the command line.** The script carries the source
   path, the interface name and the hash, never the config text (`privileged.py`
   header rule).

## Alternatives rejected
- **`/tmp` or `mktemp` per run**: fine for a one-shot installer (`deploy._run_local`)
  but the WireGuard copy must survive until disconnect, and `/tmp` is world-writable.
- **A Sentinel-private user directory**: still writable by the user, so the race
  stays open.
- **`/Library/Application Support/Sentinel`**: survives reboots, which is wrong here
  (a tunnel does not) and leaves stale copies holding private keys.
- **A privileged helper or launchd root daemon**: the real fix for same-user to root,
  and out of scope (spec §7).

## Consequences
- The user's file is never run by root again; edits after confirming are refused
  with "config changed" (exit 98) and nothing runs.
- A private key exists in a second place, root-only, while the tunnel is up.
- `SaveConfig = true` rewrites the root copy, not the user's file; the review warns
  about this.
- `/private/var/run` itself is `root:daemon 0775` on macOS 27, so a process in group
  `daemon` could pre-create `sentinel-vpn`; the ownership check catches that and the
  action is refused (exit 97) rather than trusting it.
- Whether macOS empties `/var/run` at boot is **UNVERIFIED**. If it does not, a copy
  left by a crash survives a reboot until the next connect or disconnect for that
  interface; the owner checklist (spec §9, S7 line) checks it.
- Moving the base later means every build must also look in the old place until no
  tunnel from an older build can still be up.
- S8 reuses this layout; deferring S8 does not change this ADR.
