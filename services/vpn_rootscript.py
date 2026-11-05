"""The root side of a WireGuard Connect or Disconnect (audit tier-2 step S7).

Root never runs the user's own config file. Inside the one administrator
prompt, ``ROOT_SCRIPT`` copies the file into a root-owned directory, hashes the
COPY and runs ``wg-quick`` on it only when that hash is the one the user
confirmed. Layout and rules: ``docs/adr/0004-root-runs-verified-copies.md``.

The script is a constant; the verb, source path, hash, interface, ``wg-quick``
path, base, owner and wg-quick's run directory arrive as ``$1``-``$8`` (the
same shape as ``macaddr.ROOT_SCRIPT``), so nothing a caller passes is ever
parsed as shell. The words are quoted by ``shlex.join`` here, never by the
caller. Root starts with an empty environment (``env -i``): ``BASH_ENV`` and
the like would otherwise run code as root before the hash check.

What the copy does NOT protect: ``wg-quick`` itself, ``wg``/``wireguard-go``
beside it and Sentinel's own checkout. Homebrew's bin folder is usually the
user's, and the review warns when it is (owner decision 2026-11-05).

Exit codes of the script (nothing ran for any of them): 94 the tunnel is
already up (Connect) or not up (Disconnect; a stale copy is removed), 96 no
verified config to run, 97 a directory Sentinel owns is not what it should be,
98 the config changed after it was confirmed or could not be read (one message
for both, so the exit status says nothing about other files). Anything else is
wg-quick's own.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

# On macOS /var is a symlink to private/var, and the script refuses a symlink in
# any directory it owns, so the base is written in its real form.
BASE_DIR = "/private/var/run/sentinel-vpn"
# uid:gid the directories must belong to (root:wheel). Tests pass their own.
ROOT_OWNER = "0:0"
# Where wg-quick records an interface that is up (<iface>.name).
WG_RUN_DIR = "/var/run/wireguard"
# What the review reads at most (vpn_execution.CONFIG_READ_LIMIT); root copies
# one byte more, so a bigger file can never hash equal.
COPY_LIMIT = 256 * 1024 + 1
# Root's PATH after wg-quick's own folder (vpn_execution._which_privileged
# resolves hook commands in the same order).
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

EXIT_STATE = 94
EXIT_UNVERIFIED = 96
EXIT_REFUSED_DIR = 97
EXIT_CHANGED = 98

SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
# wg-quick's own rule for a config path: an interface name, then ".conf".
CONFIG_NAME_RE = re.compile(r"^([a-zA-Z0-9_=+.-]{1,15})\.conf\Z")
INTERFACE_RE = re.compile(r"^[a-zA-Z0-9_=+.-]{1,15}\Z")
OWNER_RE = re.compile(r"^[0-9]+:[0-9]+\Z")

ROOT_SCRIPT = """\
verb=$1; src=$2; want=$3; iface=$4; wgq=$5; base=$6; owner=$7; wgrun=$8
umask 077
PATH="${wgq%/*}:/usr/bin:/bin:/usr/sbin:/sbin"; export PATH
case $iface in ''|.|..|*/*) echo "refusing: bad interface name" >&2; exit 97;; esac
uid=${owner%%:*}
# A directory Sentinel owns: a real directory (never a link), owned by root.
check_dir() {
  [ ! -L "$1" ] && [ -d "$1" ] && [ "$(/usr/bin/stat -f %u "$1")" = "$uid" ] || {
    echo "refusing: $1 is not a directory owned by root" >&2; exit 97; }
}
make_dir() {
  if [ -e "$1" ] || [ -L "$1" ]; then check_dir "$1"
  else /bin/mkdir -m "$2" "$1" || { echo "refusing: cannot create $1" >&2; exit 97; }
       check_dir "$1"
  fi
  /usr/sbin/chown "$owner" "$1" && /bin/chmod "$2" "$1" || exit 97
}
make_dir "$base" 755
# From here on work inside the base by relative paths, so renaming the base
# (its parent is group-writable) cannot redirect the copy or the rm -rf.
cd -P -- "$base" && [ "$(/bin/pwd -P)" = "$base" ] || {
  echo "refusing: $base moved" >&2; exit 97; }
make_dir wg 700
d="wg/$iface"; copy="$d/$iface.conf"; run="$base/$copy"
up_now() { [ -e "$wgrun/$iface.name" ]; }
refused() {
  /bin/rm -rf -- "$d"
  echo "refused: the config changed after you confirmed it, or could not be read; nothing was run." >&2
  exit 98
}
# Copy the user's file, then hash the copy: only the copy is ever run. A FIFO or
# device must not hold the run open until a time someone else picks, so the
# source must be a regular file and the read is cut off after 20 seconds.
copy_verified() {
  [ -n "$src" ] && [ -n "$want" ] || {
    echo "refused: no verified copy of this config; nothing was run." >&2; exit 96; }
  /bin/rm -rf -- "$d"; make_dir "$d" 700
  [ -f "$src" ] || refused
  /usr/bin/head -c 262145 -- "$src" > "$copy" 2>/dev/null & reader=$!
  i=0
  while kill -0 "$reader" 2>/dev/null; do
    [ "$i" -ge 200 ] && { kill -9 "$reader" 2>/dev/null; break; }
    /bin/sleep 0.1; i=$((i + 1))
  done
  wait "$reader" || refused
  got=$(/sbin/sha256 -q -- "$copy") || refused
  [ "$got" = "$want" ] || refused
}
case $verb in
up)
  if up_now; then echo "refused: $iface is already up; nothing was run." >&2; exit 94; fi
  copy_verified
  "$wgq" up "$run" || { rc=$?; /bin/rm -rf -- "$d"; exit "$rc"; }
  ;;
down)
  if ! up_now; then
    /bin/rm -rf -- "$d"
    echo "refused: $iface is not up; nothing to stop (any copy kept since Connect was removed)." >&2
    exit 94
  fi
  # The copy confirmed at Connect is kept while the tunnel is up: wg-quick down
  # re-reads it and runs its PreDown/PostDown. No copy: verify the user's file.
  if [ -L "$copy" ] || [ ! -f "$copy" ]; then
    copy_verified; echo "Disconnect verified your config file and ran a copy of it."
  else
    echo "Disconnect ran the copy kept since Connect, not your file as it is now."
  fi
  "$wgq" down "$run" || exit
  /bin/rm -rf -- "$d"
  ;;
*) echo "refusing: unknown action" >&2; exit 97;;
esac
"""


def interface_for(target: str) -> str | None:
    """The interface wg-quick creates for ``target`` (a config path or a bare
    name), or None when wg-quick would refuse the name."""
    target = str(target or "")
    if "/" in target or target.endswith(".conf"):
        match = CONFIG_NAME_RE.fullmatch(Path(target).name)
        return match.group(1) if match else None
    return target if INTERFACE_RE.fullmatch(target) else None


def wireguard_argv(verb: str, source: str, sha256: str, interface: str, wg_quick: str, *,
                   base: str = BASE_DIR, owner: str = ROOT_OWNER,
                   wg_run: str = WG_RUN_DIR) -> list[str]:
    """argv for root: ``env -i /bin/bash -c ROOT_SCRIPT`` and its positional values.

    ``source`` and ``sha256`` may both be empty only for a Disconnect, which
    then uses the copy kept since Connect or refuses (exit 96)."""
    if verb not in ("up", "down"):
        raise ValueError(f"unknown wg-quick verb {verb!r}")
    if not INTERFACE_RE.fullmatch(interface or ""):
        raise ValueError(f"invalid WireGuard interface name {interface!r}")
    if not (wg_quick and wg_quick.startswith("/")):
        raise ValueError("wg-quick must be named by its absolute path")
    if bool(source) != bool(sha256) or (sha256 and not SHA256_RE.fullmatch(sha256)):
        raise ValueError("a config path needs its SHA-256 (64 lowercase hex digits)")
    if source and not source.startswith("/"):
        raise ValueError("the config path must be absolute")
    if verb == "up" and not source:
        raise ValueError("Connect needs a reviewed config file and its SHA-256")
    if not (base.startswith("/") and wg_run.startswith("/") and OWNER_RE.fullmatch(owner)):
        raise ValueError("invalid base directory, run directory or owner")
    return ["/usr/bin/env", "-i", "/bin/bash", "-c", ROOT_SCRIPT, "sentinel-wg", verb, source,
            sha256, interface, wg_quick, base, owner, wg_run]


def wireguard_command(*args, **kwargs) -> str:
    """``wireguard_argv`` as the one command line the administrator dialog runs."""
    return shlex.join(wireguard_argv(*args, **kwargs))
