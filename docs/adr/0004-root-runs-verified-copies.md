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
   without running anything. Only then run the tool, and only on the copy. The
   **config** is never read from a user-writable path after the hash check; the
   tool itself may be (see Consequences and the 2026-11-05 amendment).
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
- `SaveConfig = true` is refused by the review (a blocker since S1), so a root copy
  is never rewritten by wg-quick.
- **The copy protects the config, not the tools.** `wg-quick` is found with
  `shutil.which` and its folder goes first on root's PATH; on Apple silicon that is
  Homebrew's `/opt/homebrew/bin`, owned by the user. A process running as the user
  can replace `wg-quick`, `wg` or `wireguard-go` there and gets root at the next
  approved Connect or Disconnect, whatever the config hash says. This predates S7.
  The owner chose (2026-11-05) to **warn, not block**: the review says so when
  `wg-quick` or a folder above it is not root-owned or is group/other-writable. The
  same is true of Sentinel's own checkout and `.venv`. Closing it needs a privileged
  helper (spec §7, non-goal).
- `/private/var/run` itself is `root:daemon 0775` on macOS 27, so a process in group
  `daemon` could pre-create `sentinel-vpn`; the ownership check catches that and the
  action is refused (exit 97) rather than trusting it.
- Whether macOS empties `/var/run` at boot is **UNVERIFIED**. If it does not, a copy
  left by a crash survives a reboot until the next connect or disconnect for that
  interface; the owner checklist (spec §9, S7 line) checks it.
- Moving the base later means every build must also look in the old place until no
  tunnel from an older build can still be up.
- S8 reuses this layout; deferring S8 does not change this ADR.

## Amendment 2026-11-05 (after the independent code and security reviews)
Reports: `docs/audit/s7-wireguard-root-copy-code-review-2026-11-05.md`,
`docs/audit/s7-wireguard-root-copy-security-review-2026-11-05.md`. What was built
differs from decision 3 as follows (details in spec §14):
- Root starts from an **empty environment** (`/usr/bin/env -i /bin/bash -c …`), so
  `BASH_ENV`, `PERL5OPT` and the like cannot run code before the hash check; the
  script sets its own PATH (wg-quick's folder, then `/usr/bin:/bin:/usr/sbin:/sbin`).
- Directories: `mkdir -m` + ownership check + `chown root:wheel` + `chmod` (on an
  existing directory too, so its group is normalised), not `install -d`.
- After creating the base the script `cd -P`s into it, refuses (97) if the physical
  path differs, and works by relative paths, so renaming the base cannot redirect the
  copy or the `rm -rf`.
- The source must be a regular file; it is read with `head -c 262145` (one byte over
  the review's limit) and the read is cut off after 20 seconds, so a FIFO cannot hold
  an approved run open until a time someone else picks.
- The copy is hashed with `/sbin/sha256 -q` (a Mach-O tool), not `shasum` (Perl).
- A read failure and a hash mismatch give the same exit (98) and the same message,
  with no path, so the result says nothing about other files.
- wg-quick's run record (`/var/run/wireguard/<iface>.name`) decides state: Connect
  refuses (94) an interface that is up, before touching its copy; Disconnect of an
  interface that is not up removes any kept copy (no key left behind) and refuses (94).
- Disconnect prints which path it took (the kept copy, or a verified copy of the
  user's file), and its review says that the hooks shown are the file's now and that
  the kept copy, if any, is what runs.

