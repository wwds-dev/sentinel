"""The root side of a VPN Connect or Disconnect (audit tier-2 steps S7, S8a, S8b).

Root never runs the user's own config file. Inside the one administrator
prompt, ``ROOT_SCRIPT`` copies the file into a root-owned directory, hashes the
COPY and runs ``wg-quick`` on it only when that hash is the one the user
confirmed. ``OVPN_SCRIPT`` does the same for OpenVPN and for every data file
the config names beside it, starts openvpn with ``--cd`` in the copies' folder,
and keeps openvpn's pid, device and start-up log in the same root-owned
directory, so Disconnect's ``kill`` can only reach the process Sentinel
started. Layout and rules: ``docs/adr/0004-root-runs-verified-copies.md``.

The script is a constant; the verb, source path, hash, interface, ``wg-quick``
path, base, owner and wg-quick's run directory arrive as ``$1``-``$8`` (the
same shape as ``macaddr.ROOT_SCRIPT``), so nothing a caller passes is ever
parsed as shell. The words are quoted by ``shlex.join`` here, never by the
caller. Root starts with an empty environment (``env -i``): ``BASH_ENV`` and
the like would otherwise run code as root before the hash check. wg-quick and
its hooks then run from ``/`` with only ``HOME=/var/root`` and ``PATH``, so a
hook's program reads nothing of the user's (startup files in their home, a
Makefile or relative file in Sentinel's working directory, files a ``DNS``
wildcard expands to).

What the copy does NOT protect: ``wg-quick`` itself, ``wg``/``wireguard-go``
beside it and Sentinel's own checkout. Homebrew's bin folder is usually the
user's, and the review warns when it is (owner decision 2026-11-05).

Exit codes of the script (nothing ran for any of them): 94 the tunnel is
already up (Connect) or not up (Disconnect; a stale copy is removed), 96 no
verified config to run, 97 a directory Sentinel owns is not what it should be,
98 the config changed after it was confirmed or could not be read (one message
for both, so the exit status says nothing about other files), 75 another run
for the same interface held its lock for 30 seconds (``lockf``). Anything else
is wg-quick's own. ``OVPN_SCRIPT``'s codes are listed above it.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
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
HOME=/var/root; export HOME
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
# One Connect or Disconnect per interface at a time: a second run that passed
# the "is it up" check while the first was still inside wg-quick up would
# replace, then remove, the copy the live tunnel needs for its Disconnect. The
# script runs again from the top under lockf (the lock dies with its holder);
# '@' is not allowed in an interface name, so no lock is ever a copy's folder.
if [ "$9" != locked ]; then
  exec /usr/bin/lockf -k -t 30 "wg/lock@$iface" /bin/bash -c "$BASH_EXECUTION_STRING" \
    "$0" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" locked
fi
d="wg/$iface"; copy="$d/$iface.conf"; run="$base/$copy"
# Up as Sentinel's review decides it (vpn_execution.wireguard_interface_state):
# wg-quick's <iface>.name, and the socket of the device it names. A .name left
# without its socket (a crash) is stale: not up.
up_now() {
  [ -e "$wgrun/$iface.name" ] || return 1
  n=$(/bin/cat -- "$wgrun/$iface.name" 2>/dev/null) || return 0
  case $n in ''|*/*) return 0;; esac
  [ -e "$wgrun/$n.sock" ]
}
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
  (cd / && exec "$wgq" up "$run") || { rc=$?; /bin/rm -rf -- "$d"; exit "$rc"; }
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
  (cd / && exec "$wgq" down "$run") || exit
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


# ── OpenVPN (S8a: pid, device and log in root's run dir; S8b: verified copies) ──
# Each file an OpenVPN config names (vpn_execution.OPENVPN_FILE_WORD) is read at
# most this far by the review; root copies one byte more, as for the config.
INCLUDE_LIMIT = 1024 * 1024
MAX_INCLUDES = 16
# Hook programs and libraries root re-checks before openvpn starts (spec 1.3).
MAX_PROGRAMS = 32
EXIT_BAD_ARGUMENT = 99
OVPN_START_WAIT = 30       # x 0.5 s: how long Connect waits for the device line
OVPN_STOP_WAIT = 20        # x 0.5 s: how long Disconnect waits for the process to exit
# Root's files under the base: Sentinel reads the pid and device (0644 in the
# 0755 ovpn folder); the copies and the start-up log stay in the 0700 private/.
OVPN_DIR = "ovpn"
OVPN_PID = "ovpn/openvpn.pid"
OVPN_DEVICE = "ovpn/device"
OVPN_COPY_DIR = "ovpn/private/cfg"
OVPN_CONFIG_COPY = "ovpn/private/config.ovpn"

# $1 is "locked" once the script runs under its lock; then verb, config,
# hash, openvpn, base, owner, pid, legacy pid file, wait, the number of files,
# that many (path, SHA-256) pairs, and the hook programs, libraries and
# root-only files root re-checks by ownership.
# Exit codes (nothing was started or signalled for 94-99): 94 Connect while
# Sentinel's openvpn runs, or Disconnect of a pid that is no longer Sentinel's
# openvpn (its copies are removed); 96 no reviewed hash; 97 a directory
# Sentinel owns is not what it should be; 98 a file changed after it was
# confirmed or could not be read, a re-checked path is not root-owned all the
# way up, or the recorded pid changed; 99 a value root rejects; 75 another
# OpenVPN run held the lock for 30 s (lockf). Disconnect: 0 when stopped, and
# also 0 with "Signal sent; OpenVPN is still running." (copies kept for the
# running tunnel), 1 when the signal could not be sent. Connect: openvpn's
# own status otherwise.
OVPN_SCRIPT = """\
phase=$1; shift
verb=$1; cfg=$2; want=$3; ovpn=$4; base=$5; owner=$6; pid=$7; legacy=$8; tries=$9
args=("$@")
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
HOME=/var/root; export HOME
uid=${owner%%:*}
bad() { echo "refusing: $1" >&2; exit 99; }
# Every value is checked again here: the builder is not the only way in.
[[ $base == /* && $owner =~ ^[0-9]+:[0-9]+$ ]] || bad "invalid base directory or owner"
[[ $tries =~ ^[1-9][0-9]?$ ]] || bad "invalid wait"
shift 9
n=${1-}
[[ $n =~ ^(0|[1-9][0-9]?)$ ]] && [ "$n" -le 16 ] && [ $(( $# - 1 )) -ge $(( n * 2 )) ] \\
  || bad "invalid list of files"
shift
files=(); hashes=(); progs=()
while [ "${#files[@]}" -lt "$n" ]; do
  p=$1; h=$2; shift 2
  q=$p; [[ $q == /* ]] || q=/$q
  # Paths arrive normalised, so a relative copy can never land outside cfg/.
  case $q/ in //*|*/../*|*/./*|*//*) bad "invalid path of a file the config reads";; esac
  case $p in /.*) bad "invalid path of a file the config reads";; esac
  [[ $h =~ ^[0-9a-f]{64}$ ]] || bad "invalid hash of a file the config reads"
  for o in "${files[@]}"; do [ "$o" != "$p" ] || bad "a file is listed twice"; done
  files+=("$p"); hashes+=("$h")
done
for p in "$@"; do
  case $p in /.*|[!/]*|'') bad "a hook program must be named by a plain absolute path";; esac
  progs+=("$p")
done
[ "${#progs[@]}" -le 32 ] || bad "too many hook programs"
case $verb in
up)
  [[ $want =~ ^[0-9a-f]{64}$ ]] || { echo "refused: no verified copy of this config; nothing was run." >&2; exit 96; }
  [[ $cfg == /* && $ovpn == /* ]] || bad "the config and openvpn must be absolute paths"
  # ps prints argv unquoted: a space in the program path would hide it from tracked().
  [[ $ovpn != *[[:space:]]* ]] || bad "the openvpn path must not contain spaces"
  [ -z "$pid$legacy" ] || bad "Connect takes no process ID";;
down)
  [[ $pid =~ ^[0-9]{1,10}$ ]] && [ "$pid" -gt 1 ] || bad "invalid process ID"
  [ -z "$legacy" ] || [[ $legacy == /* ]] || bad "the legacy pid file must be an absolute path"
  [ -z "$cfg$want$ovpn" ] && [ "$n" -eq 0 ] && [ "${#progs[@]}" -eq 0 ] \\
    || bad "Disconnect takes no files";;
*) bad "unknown action";;
esac
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
cd -P -- "$base" && [ "$(/bin/pwd -P)" = "$base" ] || {
  echo "refusing: $base moved" >&2; exit 97; }
make_dir ovpn 755
# One OpenVPN Connect or Disconnect at a time: two Connects would share the
# copies, and a Disconnect could remove what a starting tunnel reads.
if [ "$phase" != locked ]; then
  exec /usr/bin/lockf -k -t 30 ovpn/lock /bin/bash -c "$BASH_EXECUTION_STRING" \\
    "$0" locked "${args[@]}"
fi
priv=ovpn/private; cfgd=ovpn/private/cfg; copy=ovpn/private/config.ovpn
log=ovpn/private/openvpn.log; pidf=ovpn/openvpn.pid; devf=ovpn/device
# Sentinel's openvpn: a program named openvpn, daemonised under Sentinel's tag,
# writing its pid to the given file. ps prints argv unquoted, so this compares
# strings (a pid path with a space still matches), exactly as Python does.
tracked() {
  [[ $1 =~ ^[0-9]{1,10}$ ]] && [ "$1" -gt 1 ] || return 1
  c=$(/bin/ps -ww -p "$1" -o command= 2>/dev/null) || return 1
  [ -n "$c" ] || return 1
  f=${c%% *}
  [ "${f##*/}" = openvpn ] || return 1
  [[ "$c " == *" --daemon sentinel-ovpn "* ]] || return 1
  [[ "$c " == *" --writepid $2 "* ]]
}
recorded_pid() {
  [ -f "$pidf" ] && [ ! -L "$pidf" ] || return 0
  /usr/bin/head -c 16 -- "$pidf" | /usr/bin/tr -d ' \\n'
}
forget() { /bin/rm -rf -- "$priv" "$pidf" "$devf" "$devf.new"; }
refused() {
  forget
  echo "refused: $1 changed after you confirmed it, or could not be read; nothing was run." >&2
  exit 98
}
# Copy the regular file $1 to $2, keep it only if it is at most $3 bytes and
# has the SHA-256 $4. A FIFO or a stalling file must not hold the run open
# until a time someone else picks, so a read is cut off after 20 seconds, and
# all of them together after 30 (deadline, set before the first).
grab() {
  local reader t=0
  [ -f "$1" ] || return 1
  /usr/bin/head -c "$(( $3 + 1 ))" -- "$1" > "$2" 2>/dev/null & reader=$!
  while kill -0 "$reader" 2>/dev/null; do
    [ "$t" -ge 200 ] || [ "$SECONDS" -ge "$deadline" ] && { kill -9 "$reader" 2>/dev/null; break; }
    /bin/sleep 0.1; t=$((t + 1))
  done
  wait "$reader" || return 1
  [ "$(/usr/bin/stat -f %z -- "$2")" -le "$3" ] && [ "$(/sbin/sha256 -q -- "$2")" = "$4" ]
}
# A hook program or library and every folder above it: owned by root and
# writable by nobody else, as the review judged it (spec 1.3). A link's own
# mode means nothing on macOS (the review reads it through the link), so only
# its owner counts.
root_owned() {
  local p=$1 s u m
  while :; do
    s=$(/usr/bin/stat -f '%u %Lp' -- "$p" 2>/dev/null) || return 1
    u=${s%% *}; m=${s##* }
    [ "$u" = 0 ] || [ "$u" = "$uid" ] || return 1
    [ -L "$p" ] || case $m in *[2367]?|*[2367]) return 1;; esac
    [ "$p" = / ] && return 0
    p=${p%/*}; [ -n "$p" ] || p=/
  done
}
# The physical path of $1: each link followed (at most 40) and its folder
# resolved by cd -P. /bin/realpath does not exist before macOS 13.
real() {
  local p=$1 n=0 l d
  while [ -L "$p" ]; do
    n=$((n + 1)); [ "$n" -le 40 ] || return 1
    l=$(/usr/bin/readlink -- "$p") || return 1
    case $l in /*) p=$l;; *) p=${p%/*}/$l;; esac
  done
  d=${p%/*}; [ -n "$d" ] || d=/
  d=$(cd -P -- "$d" 2>/dev/null && /bin/pwd -P) || return 1
  printf '%s/%s\n' "${d%/}" "${p##*/}"
}
if [ "$verb" = up ]; then
  old=$(recorded_pid)
  if [ -n "$old" ] && tracked "$old" "$base/$pidf"; then
    echo "refused: the OpenVPN tunnel Sentinel started is still running; Disconnect it first." >&2
    exit 94
  fi
  forget                                # a crash's copies, pid and device
  make_dir "$priv" 700; make_dir "$cfgd" 700
  deadline=$((SECONDS + 30))
  grab "$cfg" "$copy" 262144 "$want" || refused "the config"
  srcd=${cfg%/*}; [ -n "$srcd" ] || srcd=/
  i=0
  while [ "$i" -lt "$n" ]; do           # beside the config: copied, layout kept
    p=${files[$i]}; h=${hashes[$i]}; i=$((i + 1))
    [[ $p == /* ]] && continue
    case $p in */*) /bin/mkdir -p -- "$cfgd/${p%/*}" || refused "$p";; esac
    [ ! -e "$cfgd/$p" ] && [ ! -L "$cfgd/$p" ] || refused "$p"
    grab "$srcd/$p" "$cfgd/$p" 1048576 "$h" || refused "$p"
  done
  i=0
  while [ "$i" -lt "$n" ]; do           # by absolute path: checked in place, last
    p=${files[$i]}; h=${hashes[$i]}; i=$((i + 1))
    [[ $p == /* ]] || continue
    grab "$p" "$priv/check" 1048576 "$h" || refused "$p"
    /bin/rm -f -- "$priv/check"
  done
  for p in "${progs[@]}"; do
    r=$p && root_owned "$p" && root_owned "$r" || {
      forget
      echo "refused: $p is not owned by root all the way up; nothing was run." >&2
      exit 98; }
  done
  # The pid file is for Sentinel to read: 0644. --cd comes first, so every
  # relative file resolves inside the verified copies.
  umask 022
  (cd / && exec "$ovpn" --cd "$base/$cfgd" --config "$base/$copy" --daemon sentinel-ovpn \\
     --log "$base/$log" --writepid "$base/$pidf" --verb 3 --suppress-timestamps)
  rc=$?
  umask 077
  dev=""
  if [ "$rc" -eq 0 ]; then
    i=0
    while :; do
      # Whole lines only (no timestamps: --suppress-timestamps): a certificate
      # subject or a pushed option the server controls can contain the same
      # words mid-line (security review P3-1).
      dev=$(/usr/bin/grep -E -e '^Opened utun device utun[0-9]+$' \\
              -e '^TUN/TAP device (/dev/)?tun[0-9]+ opened$' "$log" 2>/dev/null \\
            | /usr/bin/tail -n 1 | /usr/bin/sed -E 's/^Opened utun device //; s/^TUN.TAP device (\\/dev\\/)?//; s/ opened$//')
      [ -n "$dev" ] && break
      i=$((i + 1)); [ "$i" -lt "$tries" ] || break
      /bin/sleep 0.5
    done
  fi
  if [ -n "$dev" ]; then
    (umask 022; printf '%s' "$dev" > "$devf.new") && /bin/mv -f -- "$devf.new" "$devf" \\
      || echo "Tunnel: could not record the tunnel device."   # stdout: the dialog drops stderr on success
  else
    # Explained once, with the result (on stderr when openvpn failed, which is
    # what the dialog reports); the log itself is not kept.
    fd=1; [ "$rc" -eq 0 ] || fd=2
    { echo "OpenVPN start-up output:"; /usr/bin/tail -n 6 -- "$log" 2>/dev/null; } >&"$fd"
  fi
  /bin/rm -f -- "$log"
  [ "$rc" -eq 0 ] || forget
  exit "$rc"
fi
# Down: signal only the process Sentinel started, checked here as root.
if [ -n "$legacy" ]; then
  pf=$legacy                            # an older Sentinel's tunnel (spec 5)
else
  pf=$base/$pidf
  [ "$(recorded_pid)" = "$pid" ] || {
    echo "refused: the OpenVPN process Sentinel recorded has changed; nothing was stopped." >&2
    exit 98; }
fi
if ! tracked "$pid" "$pf"; then
  [ -n "$legacy" ] || forget            # gone already: no copy of a key is left behind
  echo "refused: process $pid is not the OpenVPN tunnel Sentinel started; nothing was stopped." >&2
  exit 94
fi
if ! /bin/kill -TERM "$pid" && tracked "$pid" "$pf"; then
  echo "Tunnel: could not signal OpenVPN (process $pid)." >&2; exit 1
fi                                      # a kill that failed because it had just exited is a stop
i=0
while tracked "$pid" "$pf" && [ "$i" -lt "$tries" ]; do
  /bin/sleep 0.5; i=$((i + 1))
done
if tracked "$pid" "$pf"; then
  echo "Signal sent; OpenVPN is still running."
  exit 0
fi
[ -n "$legacy" ] || forget
echo "OpenVPN stopped."
"""


def include_path(path: str) -> str:
    """A file an OpenVPN config reads, as root will copy or check it: absolute,
    or relative to the config's folder and unable to leave it. Returns the path
    unchanged; raises ``ValueError`` unless it is already in normal form (no
    ``..``, ``.``, empty or doubled component, no trailing slash, no hidden
    ``/.vol``-style prefix) with no control characters."""
    if not isinstance(path, str) or not path:
        raise ValueError("A file the config reads has no path.")
    if any(ord(ch) < 32 or ord(ch) == 0x7F for ch in path):
        raise ValueError("A file the config reads has control characters in its path.")
    parts = path.split("/")[1:] if path.startswith("/") else path.split("/")
    if any(part in ("", ".", "..") for part in parts) or (path.startswith("/.")):
        raise ValueError(f"'{path}' is not a plain path inside the config's folder.")
    return path


def _includes(includes) -> list[str]:
    """The manifest as argv values: the count, then path, hash, path, hash..."""
    try:
        pairs = [tuple(item) for item in includes]
    except TypeError:
        raise ValueError("The list of files the config reads is malformed.") from None
    if len(pairs) > MAX_INCLUDES:
        raise ValueError(f"A config may read at most {MAX_INCLUDES} files.")
    flat: list[str] = []
    for pair in pairs:
        if len(pair) != 2:
            raise ValueError("The list of files the config reads is malformed.")
        path = include_path(pair[0])
        if path in flat[::2]:
            raise ValueError(f"'{path}' is listed twice.")
        if not SHA256_RE.fullmatch(str(pair[1])):
            raise ValueError(f"The hash of {path} is not a SHA-256.")
        flat += [path, pair[1]]
    return [str(len(pairs)), *flat]


def _programs(programs) -> list[str]:
    out = [str(p) for p in programs]
    if len(out) > MAX_PROGRAMS:
        raise ValueError(f"A config may name at most {MAX_PROGRAMS} hook programs.")
    for program in out:
        if (not program.startswith("/") or program.startswith("/.")
                or any(ord(ch) < 32 or ord(ch) == 0x7F for ch in program)):
            raise ValueError(f"The hook program {program!r} is not a plain absolute path.")
    return out


def openvpn_argv(verb: str, *, config: str = "", sha256: str = "", openvpn: str = "",
                 includes=(), programs=(), pid: int | None = None, legacy_pid_file: str = "",
                 base: str = BASE_DIR, owner: str = ROOT_OWNER,
                 wait: int | None = None) -> list[str]:
    """argv for root: ``env -i /bin/bash -c OVPN_SCRIPT`` and its positional values.

    Connect: root copies ``config`` and every relative file in ``includes``
    (path, SHA-256) into the run directory, checks each copy's hash and the
    absolute files in place, re-checks that each of ``programs`` is root-owned,
    and starts ``openvpn`` on the copy with ``--cd`` there. Disconnect: root
    re-checks that ``pid`` is Sentinel's openvpn (its pid file is root's own,
    or ``legacy_pid_file`` for a tunnel an older Sentinel started) and sends it
    SIGTERM. ``wait`` (half-seconds) is for the bash harness: Connect waits
    ``OVPN_START_WAIT`` for the device line, Disconnect ``OVPN_STOP_WAIT`` for
    the process to exit."""
    def absolute(value: str, what: str) -> str:
        value = str(value)
        if not value.startswith("/") or any(ord(ch) < 32 or ord(ch) == 0x7F for ch in value):
            raise ValueError(f"{what} must be an absolute path without control characters.")
        return value

    if verb == "up":
        config, openvpn = absolute(config, "The config"), absolute(openvpn, "openvpn")
        if any(ch.isspace() for ch in openvpn):
            # ps prints argv unquoted, so the running tunnel could never be recognised.
            raise ValueError("The openvpn path must not contain spaces.")
        if not SHA256_RE.fullmatch(sha256 or ""):
            raise ValueError("Connect needs the reviewed config's SHA-256.")
        values = [config, sha256, openvpn, base, owner, "", "", str(wait or OVPN_START_WAIT),
                  *_includes(includes), *_programs(programs)]
    elif verb == "down":
        if isinstance(pid, bool) or not isinstance(pid, int) or not 1 < pid < 10 ** 10:
            raise ValueError("The OpenVPN process ID is not valid.")
        legacy = absolute(legacy_pid_file, "The legacy pid file") if legacy_pid_file else ""
        values = ["", "", "", base, owner, str(pid), legacy, str(wait or OVPN_STOP_WAIT), "0"]
    else:
        raise ValueError(f"unknown OpenVPN verb {verb!r}")
    if not (base.startswith("/") and OWNER_RE.fullmatch(owner)):
        raise ValueError("invalid base directory or owner")
    if wait is not None and (isinstance(wait, bool) or not isinstance(wait, int)
                             or not 1 <= wait <= 99):
        raise ValueError("The wait must be 1 to 99 half-seconds.")
    return ["/usr/bin/env", "-i", "/bin/bash", "-c", OVPN_SCRIPT, "sentinel-ovpn", "start",
            verb, *values]


def openvpn_command(*args, **kwargs) -> str:
    """``openvpn_argv`` as the one command line the administrator dialog runs."""
    return shlex.join(openvpn_argv(*args, **kwargs))


def ovpn_path(relative: str, base: str = BASE_DIR) -> str:
    """Where root keeps one of its OpenVPN files (``OVPN_PID``, ``OVPN_COPY_DIR``...)."""
    return f"{base}/{relative}"


_lstat = os.lstat        # indirection so tests can describe a root-owned file
_OWNER_UID = 0
_READ_BASE = BASE_DIR    # read at call time; the test suite points it at a temp dir


def read_root_file(relative: str, *, limit: int = 16) -> str:
    """Up to ``limit`` ASCII characters of a file under the run directory,
    stripped, or "" when it is missing, not a regular file, or not owned by root.

    Only root can write the run directory; the ownership check costs nothing
    and keeps a planted file from ever being believed. Never raises."""
    try:
        path = os.path.join(_READ_BASE, relative)
        info = _lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != _OWNER_UID:
            return ""
        with open(path, encoding="ascii", errors="strict") as handle:
            return handle.read(limit).strip()
    except (OSError, ValueError, UnicodeError):
        return ""
