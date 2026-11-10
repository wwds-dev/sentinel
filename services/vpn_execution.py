"""Gated execution for Tunnel's live Connect/Disconnect (phase 3b).

``vpn_connection`` knows *how* to bring a tunnel up or down. This module decides
*whether* it may. It writes no log of any kind:

1. **Target review** — before anything runs, name the exact target, the command,
   the non-secret routing/DNS intent of the config, and every reason to refuse.
   Blockers stop execution outright; warnings are shown in the confirmation.
2. **Explicit confirmation** happens in the panel, from ``review.confirmation_text()``.
3. **Administrator authorisation** stays inside ``vpn_connection`` (injected
   ``run_as_root``), so tests never touch ``sudo``/``osascript``.
4. **Fresh post-change check** — after the command, re-read local state (the
   ``/var/run/wireguard`` records, the OpenVPN process, the route to a public
   address) instead of trusting the command's exit status.
5. **No log** — nothing about an attempt, refused or declined ones included,
   is written to disk or kept after the result is shown. Config key material is
   never read into this module (``inspect_wireguard_config`` discards it at
   parse time).
6. **Rollback guidance** travels with every review and every outcome.

The review is re-run inside ``execute`` so a file changed between the dialog and
the worker is still caught. Root then copies the file into a root-owned
directory and runs the copy only if its SHA-256 is the one shown in the dialog
(audit steps S7 and S8b, ``services/vpn_rootscript.py``, ADR 0004): root never
runs the user's file. For OpenVPN the same goes for every data file the config
names beside it (certificates, keys, password files): they are listed with
their hashes, copied, and openvpn starts in the copies' folder (``--cd``). A
data file named by absolute path must be owned by root all the way up and is
checked in place just before start (residual D2). The contents of a hook
script are not covered by the config hash either: a hook program must be
root-owned all the way up, and root checks that again before openvpn starts;
an OpenVPN hook must also name it directly by absolute path, never through a
system program, an interpreter or a wrapper.

wg-quick runs a WireGuard hook through bash's ``eval``, so the review only
accepts plain commands with full paths (or plain names of programs in /usr/bin,
/bin, /usr/sbin, /sbin), separated by ``;``, ``&&``, ``||`` or ``&``. Shell syntax
($, quotes, backslashes, globs, braces, redirects, subshells, ~, !, pipes),
variable assignments, shell keywords and builtins (``.``, ``source``, ``eval``,
``exec``, ``if``...) and any other bare command make the hook a blocker on
Connect and a risk on Disconnect. Only the allowlisted network programs (route,
ifconfig, networksetup, pfctl, scutil, killall, dscacheutil, wg, sysctl,
ipconfig, logger; the macOS tools at their own paths, and wg wherever root
installed it) may be given plain words; any other command gets only options and
full paths (checked as code), not numbers or %i / %I. What a program does with
its options is not interpreted: an option that takes a program name
(``dtrace -c -x``) still names one found on wg-quick's PATH. What a program reads
on its own (startup files, the working directory, the environment) is closed off
by running wg-quick from / with only HOME=/var/root and PATH
(``vpn_rootscript.ROOT_SCRIPT``); openvpn is started the same way
(``vpn_rootscript.OVPN_SCRIPT``), in root's copy of the config's folder, so the
review refuses a relative OpenVPN hook program or path argument
(``_ovpn_program_findings``). A DNS entry with a shell
wildcard is a blocker on Connect: wg-quick expands it, so the DNS that applies
is unknown.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agents.vpn_agent.services.config_inspection import (
    inspect_wireguard_text as inspect_config_text,
)
from services import openvpn_manager, vpn_apps, vpn_connection, vpn_rootscript

# wg-quick's own interface-name rule (wg-quick(8)); a config file whose stem
# fails it is refused by wg-quick, so refuse before the password prompt instead.
WG_INTERFACE_NAME = re.compile(r"^[a-zA-Z0-9_=+.-]{1,15}$")
WG_RUN_DIR = Path("/var/run/wireguard")
# A routing-table lookup only — `route get` sends no packets to this address.
ROUTE_PROBE_ADDRESS = "1.1.1.1"
OUTPUT_LIMIT = 2000

# Directives that make the tunnel tool run a program as root when the tunnel
# comes up or goes down. A config with one of these is a script, not just a
# set of keys and routes; the review names each so the operator confirms it
# knowingly (auditor finding: these were neither inspected nor warned about).
WG_HOOK_KEYS = ("PreUp", "PostUp", "PreDown", "PostDown")
_WG_HOOK_KEYS_LOWER = frozenset(k.lower() for k in WG_HOOK_KEYS)
OPENVPN_HOOK_KEYS = ("up", "down", "route-up", "route-pre-down", "ipchange",
                     "client-connect", "client-disconnect", "learn-address",
                     "tls-verify", "auth-user-pass-verify", "plugin",
                     "script-security", "pkcs11-providers",
                     "dns-updown", "tls-crypt-v2-verify", "client-crresponse")
# A hook is shown whole: the model keeps every character. The dialog body
# shortens each one to HOOK_DISPLAY_LIMIT (the full text goes in the dialog's
# detail and in sections()); a single line over HOOK_LINE_LIMIT is refused
# outright, because nobody can review that.
HOOK_DISPLAY_LIMIT = 300
HOOK_LINE_LIMIT = 4000
# The review reads the config once (the hash and the inspection describe the
# same bytes), so the size is capped well below what a profile ever needs.
CONFIG_READ_LIMIT = 256 * 1024
# OpenVPN directives that hide hooks (`config` pulls in a second file), open a
# local control socket (`management*`), or make root write, log or change
# directory somewhere the review cannot see (`daemon` and `syslog` also take
# the start-up log the root script reads the tunnel device from). Blockers,
# not warnings.
OPENVPN_BLOCKER_KEYS = ("config", "log", "log-append", "writepid", "status",
                        "tmp-dir", "cd", "chroot", "setenv", "replay-persist",
                        "daemon", "syslog", "genkey", "ifconfig-pool-persist",
                        "tls-export-cert", "client-config-dir", "auth-gen-token-secret")
# Tunnel is a client: a server profile multiplies the hook surface (S8b security P1-3).
OPENVPN_SERVER_KEYS = ("server", "server-bridge", "tls-server", "server-ipv6")
# The only dev-node values allowed: any other makes root open that path (S8b security P2-1).
OPENVPN_DEV_NODE = re.compile(r"utun[0-9]{0,3}")
# The only dev values allowed: a TAP name with a path makes root open it (re-review R-P3-3).
OPENVPN_DEV = re.compile(r"(tun|tap|utun)[0-9]{0,3}")
# OpenVPN reads each config line into a 256-byte buffer; a line this long or
# longer (bytes, without its newline) is refused, with a small margin.
OPENVPN_LINE_LIMIT = 250
# What a directive name may look like; anything else (an invisible or look-alike
# character) could rename it for this review but not for OpenVPN (S8b security P1-1).
OPENVPN_DIRECTIVE_NAME = re.compile(r"-{0,2}[A-Za-z0-9][A-Za-z0-9-]*")
# Directives that load a library into the root process with no hook line to
# check: `engine x` loads x as a shared library when it is no built-in engine
# (crypto_openssl.c, the "dynamic" engine's SO_PATH), and `show-pkcs11-ids x`
# loads x while the config is still being read (security review P1-2). Blockers.
OPENVPN_CODE_KEYS = ("engine", "show-pkcs11-ids")    # the latter loads its library at parse time
# `providers` hands each word to OSSL_PROVIDER_load (crypto_openssl.c), which
# loads a path, or a name it does not know from OpenSSL's module folder. Only
# OpenSSL's own providers are accepted, matched exactly as OpenSSL does.
OPENVPN_PROVIDERS = frozenset(("default", "base", "legacy", "null", "fips"))
# Directives whose value is a program or plugin that runs as root.
OPENVPN_EXECUTABLE_KEYS = ("up", "down", "route-up", "route-pre-down", "ipchange",
                           "client-connect", "client-disconnect", "learn-address",
                           "tls-verify", "auth-user-pass-verify", "plugin",
                           "dns-updown", "tls-crypt-v2-verify", "client-crresponse")
# Directives that name a file OpenVPN reads, and which word it is (the first,
# or a proxy's auth file after server and port). Each goes into the review's
# manifest with its hash; root copies a relative one into its own folder,
# keeping the layout, and starts openvpn there (--cd), so a relative name is
# read from root's copy of the config's folder. OpenVPN expands no ~ and
# compares its special words
# exactly (streq): http-proxy's "auto" and "auto-nct", and "stdin" only where it
# reads credentials (get_user_pass, misc.c); anywhere else they are files.
# "[inline]" is not special to OpenVPN 2.6: it only works because a <tag> block
# of the same directive replaces the name, so it is exempt only beside one.
OPENVPN_FILE_KEYS = ("ca", "capath", "cert", "key", "dh", "extra-certs", "pkcs12", "tls-auth",
                     "tls-crypt", "tls-crypt-v2", "secret", "crl-verify",
                     "auth-user-pass", "askpass", "http-proxy-user-pass")
OPENVPN_FILE_WORD = {**{key: 0 for key in OPENVPN_FILE_KEYS},
                     "http-proxy": 2, "socks-proxy": 2}
OPENVPN_STDIN_KEYS = ("auth-user-pass", "askpass", "http-proxy-user-pass", "http-proxy",
                      "socks-proxy")
# The reviewed client directive list. Anything else is still allowed (a real
# profile must not break) but is named in one warning.
OPENVPN_KNOWN_KEYS = (frozenset(OPENVPN_HOOK_KEYS) | frozenset(OPENVPN_BLOCKER_KEYS)
                      | frozenset(OPENVPN_CODE_KEYS)) | frozenset((
    "client", "capath", "dev", "dev-type", "dev-node", "proto", "remote", "remote-random",
    "remote-random-hostname", "resolv-retry", "nobind", "persist-key", "persist-tun",
    "persist-local-ip", "persist-remote-ip", "ca", "cert", "key", "dh", "tls-client",
    "tls-version-min", "tls-version-max", "tls-cipher", "tls-ciphersuites", "tls-groups",
    "remote-cert-tls", "remote-cert-ku", "remote-cert-eku", "verify-x509-name",
    "verify-hash", "peer-fingerprint", "cipher", "data-ciphers", "data-ciphers-fallback",
    "ncp-ciphers", "auth", "auth-nocache", "auth-user-pass", "auth-retry", "key-direction",
    "comp-lzo", "compress", "allow-compression", "verb", "mute", "mute-replay-warnings",
    "float", "keepalive", "ping", "ping-restart", "ping-exit", "reneg-sec", "route",
    "route-ipv6", "route-nopull", "route-metric", "route-delay", "route-gateway",
    "redirect-gateway", "redirect-private", "pull", "pull-filter", "push-peer-info",
    "dhcp-option", "block-outside-dns", "ifconfig", "ifconfig-ipv6", "topology", "mssfix",
    "fragment", "tun-mtu", "link-mtu", "sndbuf", "rcvbuf", "socks-proxy", "http-proxy",
    "http-proxy-option", "http-proxy-retry", "http-proxy-user-pass", "connect-retry",
    "connect-retry-max", "connect-timeout", "server-poll-timeout", "explicit-exit-notify",
    "ns-cert-type", "crl-verify", "extra-certs", "pkcs12", "lport", "rport", "local",
    "bind", "mark", "fast-io", "user", "group", "askpass", "setenv-safe",
    "ignore-unknown-option", "inactive", "replay-window", "reneg-bytes",
    "reneg-pkts", "tcp-queue-limit", "tran-window", "hand-window", "tls-timeout",
    "tls-auth", "tls-crypt", "tls-crypt-v2", "secret", "providers", "show-net",
    "disable-dco", "dns", "register-dns", "windows-driver", "lladdr", "txqueuelen",
    "multihome", "passtos", "nice", "mlock", "up-restart", "down-pre", "ifconfig-nowarn",
    "ifconfig-noexec", "route-noexec", "script-security", "socket-flags",
    "cryptoapicert", "single-session", "ccd-exclusive", "auth-gen-token",
))
# Where wg-quick looks for <interface>.conf when it is given a bare name.
WG_CONFIG_DIRS = ("/etc/wireguard", "/opt/homebrew/etc/wireguard", "/usr/local/etc/wireguard")


# Anything the tunnel tools print can quote the config: wg-quick echoes the
# offending line of a bad config ("Line unrecognized: `PrivateKey = …`"), and
# OpenVPN logs inline <key> blocks on parse errors. Redact before anything
# reaches the Execution tab or a saved chat.
_SECRET_FIELD = re.compile(
    r"(?i)\b(PrivateKey|PresharedKey|password|passwd|auth-user-pass|secret|token)"
    r"(\s*[=:]\s*|\s+)(?!\[redacted)(\S+)")
_INLINE_BLOCK = re.compile(
    r"(?is)<(key|tls-auth|tls-crypt|tls-crypt-v2|pkcs12|secret)>.*?</\1>")
_PEM_BLOCK = re.compile(r"(?s)-----BEGIN ([A-Za-z0-9 ]+)-----.*?-----END \1-----")
_KEY_TOKEN = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{42,}={0,2}(?![A-Za-z0-9+/=])")


def redact_secrets(text: str | None) -> str:
    """Scrub key material from tool output before it is shown or stored."""
    if not text:
        return ""
    text = _INLINE_BLOCK.sub(lambda m: f"<{m.group(1)}>[redacted]</{m.group(1)}>", text)
    text = _PEM_BLOCK.sub(
        lambda m: m.group(0) if "CERTIFICATE" in m.group(1) and "PRIVATE" not in m.group(1)
        else f"-----BEGIN {m.group(1)}-----[redacted]-----END {m.group(1)}-----", text)
    text = _SECRET_FIELD.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    return _KEY_TOKEN.sub("[redacted key]", text)


# Lines end at "\n" only, and whitespace is the ASCII set: str.splitlines() and
# str.strip() also split on U+2028, U+0085 and friends, which wg-quick and
# OpenVPN do not, so a scanner using them could see different lines than the tool.
_WS = " \t\r\v\f"
_OVPN_TAG = re.compile(r"<([A-Za-z0-9_-]+)>")


# The only backslash escapes OpenVPN's parse_line accepts (re-review R-P3-4).
_ESCAPABLE = '\\"' + _WS


def _ovpn_tokens(line: str) -> list[str] | None:
    """The tokens of ``_ovpn_split``, or None when it refuses the line."""
    return _ovpn_split(line)[0]


def _ovpn_split(line: str) -> tuple[list[str] | None, str]:
    r"""(tokens, "") for a line split as OpenVPN does, or (None, why) to refuse
    it: quotes group, a # or ; at the start of a token begins a comment, and a
    backslash escapes only ``\\``, ``\"`` or a space (outside single quotes).

    None means a line the two parsers could read differently, so the caller
    refuses it: an unbalanced quote; a quote that touches other text (OpenVPN
    opens a quote only at a token's start and ends the token at the closing
    quote, so ``"a"b`` splits differently; S8b security P1-2); or any other
    backslash, which OpenVPN rejects or treats as a comment start where this
    scanner would not (``\q``, ``\#``, a trailing backslash; re-review R-P3-4)."""
    tokens: list[str] = []
    cur: list[str] = []
    in_token = False
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == quote:
                quote = ""
                if i + 1 < len(line) and line[i + 1] not in _WS:
                    return None, "quote"
            elif c == "\\" and quote == '"':
                if i + 1 >= len(line) or line[i + 1] not in _ESCAPABLE:
                    return None, "backslash"
                i += 1
                cur.append(line[i])
            else:
                cur.append(c)
        elif c in _WS:
            if in_token:
                tokens.append("".join(cur))
                cur, in_token = [], False
        elif not in_token and c in "#;":
            break
        elif c in "'\"":
            if in_token:
                return None, "quote"
            quote, in_token = c, True
        elif c == "\\":
            if i + 1 >= len(line) or line[i + 1] not in _ESCAPABLE:
                return None, "backslash"
            i += 1
            cur.append(line[i])
            in_token = True
        else:
            cur.append(c)
            in_token = True
        i += 1
    if quote:
        return None, "quote"
    if in_token:
        tokens.append("".join(cur))
    return tokens, ""


def _split_problem(where: str, what: str, why: str) -> str:
    if why == "backslash":
        return (f"{where}: a backslash in '{what}' is not \\\\, \\\" or before a space, so "
                "OpenVPN would read it differently; remove it")
    return (f"{where}: a quote in '{what}' is unbalanced or touches other text; put spaces "
            "around quoted values")


def _scan(text: str, protocol: str):
    """([(raw key, lowercased key, value, words)], [problems]) for a config.

    ``words`` are the OpenVPN-tokenised value (WireGuard: the value split on
    whitespace). Problems are things the scanner refuses to guess about: a
    directive name written with quotes or backslashes, an unbalanced quote, or
    an inline block whose end is not exactly ``</tag>`` (OpenVPN may close such a
    block early, and what follows would then be live directives).

    A ``<connection>`` block is not data: OpenVPN reads its lines as directives
    (options.c, ``read_config_string`` with only ``OPT_P_CONNECTION`` allowed, so
    a hook there is a fatal error), and they can name files (``tls-auth``,
    ``http-proxy``). Its lines are scanned like the rest, inline blocks in it
    included."""
    found: list[tuple[str, str, str, list[str]]] = []
    problems: list[str] = []
    block = ""
    connection = False
    if protocol == "WireGuard":
        # bash's read (wg-quick) drops every NUL byte, so "Post\0Up = x" is a
        # PostUp to it: scan the text it sees. _hook_findings refuses NULs too.
        text = text.replace("\x00", "")
    for number, line_ in enumerate(text.split("\n"), 1):
        if number == 1 and protocol == "OpenVPN" and line_.startswith("\ufeff"):
            line_ = line_[1:]              # OpenVPN skips a leading byte-order mark
        line = line_.strip(_WS)
        if protocol == "WireGuard":
            if not line or line[0] in "#;":
                continue
            # Read the line as wg-quick does: the key comes from the line cut at
            # its first "#", but a hook's command is everything after the first
            # "=" of the uncut line, or the whole line when there is no "=".
            # So "PostUp # = cmd" and a bare "PreUp" both run as administrator.
            stripped = line.split("#", 1)[0]
            key = stripped.partition("=")[0].strip(_WS)
            lowered = key.lower()
            hook = lowered in _WG_HOOK_KEYS_LOWER
            if "=" not in stripped and not hook:
                continue                            # a section header or a note
            value = line.partition("=")[2].strip(_WS) if "=" in line else line
            if hook and "=" not in stripped:
                problems.append(f"line {number}: the {key} line has no '=' before its "
                                f"first '#', but wg-quick still runs {value[:60]!r} as "
                                "administrator")
            found.append((key, lowered, value, value.split()))
            continue
        if block:
            if line_.rstrip(_WS) == f"</{block}>":
                block = ""
            elif line.startswith("</"):
                problems.append(f"ambiguous inline block <{block}> (line {number}): a line "
                                "starting with </ is not exactly the closing tag")
                block = ""
            continue
        if not line or line[0] in "#;":
            continue
        if connection and line.startswith("</"):
            if line_.rstrip(_WS) != "</connection>":
                problems.append(f"ambiguous inline block <connection> (line {number}): a line "
                                "starting with </ is not exactly the closing tag")
            connection = False
            continue
        if line.startswith("<") and not line.startswith("</"):
            match = _OVPN_TAG.fullmatch(line)
            if not match:
                problems.append(f"ambiguous inline block (line {number}): '{line[:40]}' is not a "
                                "clean <tag> line")
                continue
            if match.group(1).lower() == "connection":
                if connection:
                    # OpenVPN ends the outer block at the first </connection>.
                    problems.append(f"ambiguous inline block (line {number}): a <connection> "
                                    "block inside another")
                connection = True
            else:
                block = match.group(1).lower()
            continue
        first = re.split(f"[{re.escape(_WS)}]", line, 1)[0]
        if any(ch in first for ch in "'\"\\"):
            problems.append(f"line {number}: the directive name {first[:30]!r} uses a quote or "
                            "backslash; OpenVPN and Sentinel might read it differently")
            continue
        if not OPENVPN_DIRECTIVE_NAME.fullmatch(first):
            problems.append(f"line {number}: the directive name {first[:30]!r} has characters "
                            "OpenVPN and Sentinel might read differently")
            continue
        tokens, why = _ovpn_split(line)
        if tokens is None:
            problems.append(_split_problem(f"line {number}", first[:30], why))
            continue
        if not tokens:
            continue
        rest = line[len(first):].strip(_WS)
        found.append((first, first.lstrip("-").lower(), rest, tokens[1:]))
    if block or connection:
        tag = block or "connection"
        problems.append(f"ambiguous inline block <{tag}>: it is never closed with exactly </{tag}>")
    return found, problems


def _config_directives(text: str, protocol: str):
    """(raw key, lowercased key, value) for each directive line."""
    for raw, key, value, _words in _scan(text, protocol)[0]:
        yield raw, key, value


def hooks_from_text(text: str, protocol: str) -> list[str]:
    """'Directive: value' for each line that executes as root; the value is the
    whole value, never shortened."""
    wanted = ({k.lower() for k in WG_HOOK_KEYS} if protocol == "WireGuard"
              else set(OPENVPN_HOOK_KEYS))
    return [f"{raw}: {value}" for raw, key, value in _config_directives(text, protocol)
            if key in wanted]


def config_root_hooks(path: str | Path, protocol: str) -> list[str]:
    """Lines in a WireGuard/OpenVPN config that execute as root, as
    'Directive: value' strings; secrets are not read (only hook lines are kept).
    An unreadable file yields no hooks — the review reports that separately."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return hooks_from_text(text, protocol)


def _read_config(path: Path) -> tuple[str | None, str, list[str], list[str]]:
    """(text, sha256 of the exact bytes, blockers, warnings). The bytes are read
    once so the hash, the hook scan and the inspection describe the same file."""
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        if path.is_symlink():
            warnings.append(f"{path} is a symbolic link; the review describes the file it "
                            "points to right now.")
        if not path.is_file():
            return None, "", [f"The config file {path} no longer exists."], warnings
        with path.open("rb") as handle:
            data = handle.read(CONFIG_READ_LIMIT + 1)
    except OSError:
        return None, "", [f"The config file {path} cannot be read."], warnings
    if len(data) > CONFIG_READ_LIMIT:
        return None, "", [f"The config file {path} is larger than "
                          f"{CONFIG_READ_LIMIT // 1024} KiB; Tunnel will not review it."], warnings
    digest = hashlib.sha256(data).hexdigest()
    try:
        return data.decode("utf-8"), digest, blockers, warnings
    except UnicodeError:
        return None, digest, ["The selected configuration is not readable UTF-8 text."], warnings


_stat = os.stat      # indirection so tests can describe a root-owned filesystem
_lstat = os.lstat    # the link walk's view of a path; tests inject errors through it
_realpath = os.path.realpath    # where the allowlist looks a tool up; tests pin a Mac's layout

# A stat that fails with one of these (ENOENT, ENOTDIR) means the path is not
# there (yet), which is judged by the folders that do exist. Any other failure
# (EACCES, ELOOP, ENAMETOOLONG, EIO...) means it could not be checked: unsafe.
_MISSING = (FileNotFoundError, NotADirectoryError)


# OpenVPN: commands that run another program or script named in their
# arguments, so any path they are given is code that root will run. Matched on
# the lowercased name with any version suffix: APFS is case-insensitive, so
# /bin/SH is /bin/sh, and perl5.34 or tclsh8.5 are perl and tclsh. A list like
# this cannot be complete; WireGuard hooks use the allowlist below instead.
_INTERPRETER = re.compile(
    r"(?:sh|bash|zsh|dash|ksh|csh|tcsh|fish|python|perl|ruby|irb|node|php|lua|awk|"
    r"osascript|expect|tclsh|wish|swift|env|sudo|exec|command|nohup|nice|time|xargs|"
    r"open|find|arch|caffeinate|timeout|stdbuf|chroot|su|doas|script|watch|flock|"
    r"parallel|taskpolicy|xcrun|ssh|ssh-agent|screen|tmux|dtrace|rsync|lldb|gdb|tar|"
    r"zip|sandbox-exec|installer|make|sqlite3|lockf|launchctl)[0-9.]*")


def _fold(text: str) -> str:
    """A name as the default macOS volume compares it: case and Unicode form ignored."""
    return unicodedata.normalize("NFD", text).casefold()


def _runs_code(command: str) -> bool:
    """Whether an OpenVPN command word names a known interpreter or wrapper, by
    the name it is written with or the name links lead to, ignoring case,
    Unicode form and a version suffix (S8b review R5-P1-1)."""
    command = command.strip("'\"")
    names = {command.rsplit("/", 1)[-1]}
    if command.startswith("/"):
        names.add(os.path.basename(_realpath(command)))
    return any(_INTERPRETER.fullmatch(_fold(name)) for name in names)


# Where macOS keeps its own programs. Many of them run another program or code
# given as an argument, so an OpenVPN hook may not name one (S8b review
# R5-P1-1, R7-P2-1); hooks are purpose-built scripts. Compared case-folded on
# the written and the real path, because the system volume ignores case.
_SYSTEM_PROGRAM_DIRS = ("/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/usr/libexec/",
                        "/usr/lib/", "/system/", "/private/var/select/", "/library/apple/",
                        "/library/developer/")
_ALLOWED_SYSTEM_PROGRAMS = frozenset(("/usr/bin/true", "/usr/bin/false"))
# An address or network (10.0.0.0/8, fd00::/64): a slash that is not a path (review R7-P3-1).
_CIDR = re.compile(r"[0-9A-Fa-f:.]+/[0-9]{1,3}")


def _system_program(program: str) -> bool:
    forms = {_fold(posixpath.normpath(program)), _fold(_realpath(program))}
    if forms <= _ALLOWED_SYSTEM_PROGRAMS:
        return False
    return any(form.startswith(_SYSTEM_PROGRAM_DIRS) for form in forms)

# The most commands and paths the hooks of one config may name. A config is at
# most CONFIG_READ_LIMIT bytes, which is room for ~130,000 one-letter commands;
# checking each took seconds on the UI thread. Above this the config is refused.
HOOK_CHECK_LIMIT = 200

# ── WireGuard hooks: plain commands with full paths (owner's rule, 2026-11-06) ──
# wg-quick runs each hook as root through bash's `eval`, so bash expands,
# quotes, redirects and substitutes it before anything runs. The review checks
# words literally, so a hook is accepted only when the words it checks are the
# words bash runs: no shell syntax at all, every command a full path or a plain
# program name found in the system folders. Commands may be separated by ;, &&,
# || or & (each one is checked); a pipe is refused, because what it feeds a
# shell or interpreter is code nobody checked. A "#" that starts a word (after a
# blank, ; or &) ends the hook, as bash's comment does.
_WG_SHELL_CHARS = "$`\\'\"*?[]{}<>()!~|"
_WG_SEPARATOR = re.compile(r"[ \t]*(?:;|&&|\|\||&)[ \t]*")
_WG_COMMENT = re.compile(r"(?:^|[ \t;&])#")
_WG_WORDS = re.compile(r"[ \t]+")       # bash splits on blanks only, not \v or U+2028
_PLAIN_NAME = re.compile(r"[A-Za-z0-9_.+-]+")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Owner's allowlist (2026-11-06): network programs whose arguments are data.
# They may be given any plain arguments. Any other command may be given only
# option letters, long options and full paths (each checked as code, like the
# command). Many programs run a program or file named in their arguments
# (xcrun, taskpolicy, dtrace -c, ssh -o ProxyCommand=, rsync -e, sh 1...) and no
# list of them is complete, so every other word is refused for all of them:
# numbers and %i / %I too (`/bin/sh 1` runs ./1, `dtrace -c %I` runs a program
# named after the config; both found where a user may write).
# Each listed tool is the macOS one at its own path, compared as written and
# where a link leads, ignoring case (APFS is case-insensitive); a root-owned
# program elsewhere that merely shares the name is not trusted. wg is not part
# of macOS, so it is accepted by name wherever root installed it.
_DATA_ONLY_PROGRAMS = {
    "route": "/sbin/route", "ifconfig": "/sbin/ifconfig", "pfctl": "/sbin/pfctl",
    "networksetup": "/usr/sbin/networksetup", "scutil": "/usr/sbin/scutil",
    "sysctl": "/usr/sbin/sysctl", "ipconfig": "/usr/sbin/ipconfig",
    "killall": "/usr/bin/killall", "dscacheutil": "/usr/bin/dscacheutil",
    "logger": "/usr/bin/logger", "wg": None,
}
_PLAIN_ARGUMENT = re.compile(r"-[A-Za-z0-9]|--[A-Za-z0-9][A-Za-z0-9-]*")
# Words bash runs itself instead of looking them up on PATH: its keywords and
# builtins (bash 3.2 to 5.2) and the functions wg-quick (darwin.bash and
# linux.bash) defines before it runs a hook. Each can run, source or redefine
# code that no path names.
_SHELL_WORDS = frozenset((
    "!", "[[", "]]", "{", "}", "case", "coproc", "do", "done", "elif", "else", "esac",
    "fi", "for", "function", "if", "in", "select", "then", "time", "until", "while",
    ".", ":", "[", "alias", "bg", "bind", "break", "builtin", "caller", "cd", "command",
    "compgen", "complete", "compopt", "continue", "declare", "dirs", "disown", "echo",
    "enable", "eval", "exec", "exit", "export", "false", "fc", "fg", "getopts", "hash",
    "help", "history", "jobs", "kill", "let", "local", "logout", "mapfile", "popd",
    "printf", "pushd", "pwd", "read", "readarray", "readonly", "return", "set", "shift",
    "shopt", "source", "suspend", "test", "times", "trap", "true", "type", "typeset",
    "ulimit", "umask", "unalias", "unset", "wait",
    "cmd", "die", "parse_options", "detect_launchd", "read_bool", "auto_su",
    "get_real_interface", "add_if", "del_routes", "del_if", "up_if", "add_addr", "set_mtu",
    "set_mtu_up", "collect_gateways", "collect_endpoints", "collect_new_service_dns",
    "set_endpoint_direct_route", "set_dns", "del_dns", "unset_dns", "monitor_daemon",
    "add_route", "add_default", "get_fwmark", "remove_firewall", "resolvconf_iface_prefix",
    "set_config", "save_config", "execute_hooks", "cmd_usage", "cmd_up", "cmd_down",
    "cmd_save", "cmd_strip",
))


def _segments(words: list[str]) -> list[list[str]]:
    """Commands in an OpenVPN executable directive: one program and its
    arguments. OpenVPN splits a script value again with the same rules
    (argv_parse_cmd); a value it could read differently raises ValueError."""
    flat: list[str] = []
    for word in words:
        tokens, why = _ovpn_split(word)
        if tokens is None:
            raise ValueError(why)
        flat.extend(tokens)
    return [flat]


def _path_word(word: str) -> str | None:
    """The path a word names (quotes and VAR= prefixes removed), or None."""
    word = word.strip("'\"()`{}")
    if "=" in word and not word.startswith(("/", "~")):
        word = word.split("=", 1)[1].strip("'\"")
    return word if word.startswith(("/", "~")) else None


# The system folders wg-quick puts first on its PATH, in its order
# (darwin.bash: PATH=/usr/bin:/bin:/usr/sbin:/sbin:<its own folder>:$PATH).
_PRIVILEGED_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def _which_privileged(name: str) -> str | None:
    """Where a bare command word resolves among the system folders wg-quick
    searches first, in its order, or None. A program found only further along
    wg-quick's PATH (its own, often user-writable, Homebrew folder, or the PATH
    it inherits) is not accepted: a user could create it there after the
    review, so the hook must name it by full path instead."""
    for directory in _PRIVILEGED_PATH:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _paths_touched(path: Path) -> list[Path]:
    """``path`` and its folders as written, then every path the kernel goes
    through to resolve it: one component at a time, each link resolved before
    any ".." after it (as the kernel does), every folder passed through added
    (even one a later ".." leaves), each link and its folders, and the final
    file. More than 40 links raises OSError (ELOOP), as the kernel would refuse
    the path; a component that cannot be looked at for any reason but "it is
    not there" raises its OSError; ValueError (a NUL byte) propagates. The
    caller treats all of these as unsafe."""
    seen: dict[Path, None] = {}                     # ordered set: linear, not quadratic

    def add(item: Path) -> None:
        each = item
        while each not in seen:                     # climbs only to the first known folder
            seen[each] = None
            if each.parent == each:
                break
            each = each.parent

    add(path)
    current = Path(path.anchor or os.sep)
    # Never normalise before walking: a ".." after a link is applied to where
    # the link leads, not to the text of the path.
    parts = deque((path if path.is_absolute() else Path(os.getcwd()) / path).parts[1:])
    links = 0
    while parts:
        part = parts.popleft()
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        step = current / part
        add(step)                                    # every folder passed through
        try:
            is_link = stat.S_ISLNK(_lstat(step).st_mode)
        except _MISSING:
            is_link = False                          # EACCES and the rest propagate
        if is_link:
            links += 1
            if links > 40:
                raise OSError(errno.ELOOP, "too many links", str(path))
            target = os.readlink(step)
            if os.path.isabs(target):
                current = Path(os.sep)
            parts.extendleft(reversed(Path(target).parts[1 if os.path.isabs(target) else 0:]))
            continue
        current = step
    add(current)
    add(Path(os.path.realpath(path)))
    return list(seen)


def _unsafe_executable_reason(path: Path) -> str | None:
    """Why a root-run program could be replaced by a normal user, or None.
    Root must own the file and every directory above it, none of them group- or
    world-writable, for the path as written and for where a link leads. A path starting with ~ is a user's home: never acceptable."""
    if str(path).startswith("~"):
        return f"{path} is in a user's home directory"
    if path.is_absolute() and len(path.parts) > 1 and path.parts[1].startswith("."):
        # /.vol/<dev>/<ino>, /.nofollow, /.resolve/N reach any file under another
        # name, past every location and name rule (S8b review R7-P1-1).
        return (f"{path} uses a hidden macOS path prefix (/{path.parts[1]}) that can reach "
                "any file under another name")
    # Every path that resolving it touches: each component as written, each link on
    # the way and the folders it sits in, and where it finally leads. A link in a
    # root-only folder can point into a folder a user can write.
    try:
        chain = _paths_touched(path)
    except ValueError:                            # a NUL byte: no file has that name
        return f"{path!r} is not a usable path"
    except OSError as exc:                        # a link or folder that cannot be read
        return f"{path} could not be resolved ({exc.strerror or 'read error'})"
    for candidate in chain:
        try:
            info = _stat(candidate)
        except _MISSING:
            continue                              # not there yet: judged by what exists
        except OSError as exc:                    # EACCES, ELOOP...: cannot tell
            return f"{candidate} could not be checked ({exc.strerror or 'error'})"
        if info.st_uid != 0:
            return f"{candidate} is not owned by root"
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{candidate} is writable by group or others"
    return None


def _exists(path: str) -> bool:
    """Path.exists() raises for a name the system cannot hold (ENAMETOOLONG on a
    path over PATH_MAX, ValueError for a NUL); such a path is simply not there."""
    try:
        return os.path.exists(path)
    except (OSError, ValueError):
        return False


def _spend(budget: dict | None) -> bool:
    """Take one check from the config's budget; False once it is used up."""
    if budget is None:
        return True
    budget["left"] -= 1
    if budget["left"] < 0:
        budget["over"] = True
        return False
    return True


def _path_findings(raw: str, path: str, position: int, runs_code: bool, protocol: str,
                   unsafe: list[str], warnings: list[str]) -> None:
    """Judge one path word of a hook command. The command itself (position 0)
    is code root runs, and so is every path given to it when ``runs_code``
    (WireGuard: any program not on the allowlist; OpenVPN: an interpreter or
    wrapper). Otherwise an unsafe path only draws a warning."""
    if protocol == "WireGuard" and ("%i" in path or "%I" in path):
        # wg-quick puts the tunnel device (%i, unknown until it is up) or
        # the interface (%I) there: the literal path is not what runs.
        reason = "its path contains %i or %I"
        message = (f"The {raw} hook names {path}; wg-quick fills in %i and %I "
                   "when the tunnel starts, so the program that runs as "
                   "administrator cannot be checked. Write the path out in full.")
    else:
        reason = _unsafe_executable_reason(Path(path))
        message = (f"The {raw} hook runs {path} as administrator, and {reason}, "
                   "so a normal user could replace it.")
    if reason is None:
        if position == 0 and not _exists(path):
            warnings.append(f"The {raw} hook names {path}, which does not exist.")
        return
    if position == 0 or runs_code:
        unsafe.append(message)
    else:
        warnings.append(f"The {raw} hook hands {path} to a program; {reason}.")


def _data_only(program: str) -> bool:
    """Whether a program is one of the allowlisted network tools: its name as
    written and the file a link leads to are the same listed tool, at that
    tool's own path (any case), or wg anywhere."""
    for candidate in (program, _realpath(program)):
        name = candidate.rsplit("/", 1)[-1].lower()
        if name not in _DATA_ONLY_PROGRAMS:
            return False
        where = _DATA_ONLY_PROGRAMS[name]
        if where is not None and candidate.lower() != where:
            return False
    return True


def _wg_hook_findings(raw: str, value: str,
                      budget: dict | None = None) -> tuple[list[str], list[str]]:
    """(unsafe findings, warnings) for one WireGuard hook under the plain-commands
    rule above. A hook gets at most one unsafe finding (the first), so a hook of
    thousands of bad commands is one line in the dialog, not thousands."""
    unsafe: list[str] = []
    warnings: list[str] = []
    shown = value if len(value) <= HOOK_DISPLAY_LIMIT else value[:HOOK_DISPLAY_LIMIT] + "…"

    def refuse(problem: str) -> tuple[list[str], list[str]]:
        unsafe.append(f"The {raw} hook ({shown}) {problem}. Tunnel cannot check what "
                      "this hook runs; write plain commands with full paths.")
        return unsafe, warnings

    command_text = _WG_COMMENT.split(value, 1)[0]
    if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in command_text):
        return refuse("contains a control character")
    syntax = next((c for c in command_text.replace("||", "") if c in _WG_SHELL_CHARS), None)
    if syntax is not None:
        return refuse(f"uses the shell character '{syntax}'")
    for text in _WG_SEPARATOR.split(command_text):
        words = [w for w in _WG_WORDS.split(text) if w]
        if not words:
            continue
        if not _spend(budget):
            return unsafe, warnings
        command = words[0]
        if _ASSIGNMENT.match(command):
            return refuse(f"sets {command.split('=', 1)[0]} for its command, which "
                          "changes what runs")
        if command in _SHELL_WORDS:
            return refuse(f"uses '{command}', which bash runs itself rather than a program")
        if "/" in command:
            if not command.startswith("/"):
                return refuse(f"runs {command}, which is not a full path")
            program = command
        elif not _PLAIN_NAME.fullmatch(command):
            return refuse(f"runs '{command}', which is not a plain program name")
        else:
            resolved = _which_privileged(command)
            if resolved is None:
                return refuse(f"runs '{command}', which is not a program in "
                              + ", ".join(_PRIVILEGED_PATH))
            reason = _unsafe_executable_reason(Path(resolved))
            if reason:
                unsafe.append(f"The {raw} hook runs '{command}' as administrator, which "
                              f"resolves to {resolved}, and {reason}, so a normal user "
                              "could replace it.")
                return unsafe, warnings
            program = resolved
        data_only = _data_only(program)
        for position, word in enumerate(words):
            if (position and not data_only and not word.startswith("/")
                    and not _PLAIN_ARGUMENT.fullmatch(word)):
                return refuse(f"hands '{word}' to {words[0]}, which is not one of the "
                              "network programs that may take plain words ("
                              + ", ".join(sorted(_DATA_ONLY_PROGRAMS)) + ")")
            path = command if position == 0 else _path_word(word)
            if path is None or not path.startswith("/"):
                continue                        # a bare command (judged above) or data
            if not _spend(budget):
                return unsafe, warnings
            _path_findings(raw, path, position, not data_only, "WireGuard",
                           unsafe, warnings)
            if unsafe:
                return unsafe, warnings
    return unsafe, warnings


def _program_findings(raw: str, value: str, words: list[str], protocol: str,
                      budget: dict | None = None) -> tuple[list[str], list[str]]:
    """(unsafe findings, warnings) for the programs a hook runs. The contents of a
    script are NOT covered by the config hash: only a program that root owns,
    in directories only root can write, is accepted. A WireGuard hook must be
    plain commands with full paths (``_wg_hook_findings``). An OpenVPN
    executable directive is one program and its arguments, judged by
    ``_ovpn_program_findings``."""
    if protocol == "WireGuard":
        return _wg_hook_findings(raw, value, budget)
    try:
        segment = _segments(words)[0]
    except ValueError as exc:
        return [_split_problem(f"The {raw} hook", value[:30], str(exc)) + "."], []
    key = raw.lstrip("-").lower()
    if not segment or (key == "dns-updown" and segment in (["disable"], ["force"])):
        return [], []
    if key != "plugin":
        return _ovpn_program_findings(raw, segment, budget), []
    # The module is a library; down-root runs its arguments as a command, other
    # plugins take them as data (S8b review R5-P2-1).
    unsafe = _ovpn_program_findings(raw, segment[:1], budget, library=True)
    rest = segment[1:]
    names = (segment[0].rsplit("/", 1)[-1], os.path.basename(_realpath(segment[0])))
    if rest and any("down-root" in _fold(name) for name in names):
        unsafe += _ovpn_program_findings(raw, rest, budget)
    else:
        unsafe += _ovpn_argument_findings(raw, rest, budget)
    return unsafe, []


def _interpreter(program: str) -> str | None:
    """The program a script's ``#!`` line runs, as the kernel finds it: an
    absolute path, or for ``#!/usr/bin/env name`` the name looked up in the
    system folders ("" when it is not there). None for no ``#!`` line or a file
    that cannot be read without blocking."""
    try:
        fd = os.open(program, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            head = handle.read(256)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    words = head[2:].split(b"\n", 1)[0].decode("utf-8", "replace").split()
    if not words:
        return ""
    if _fold(os.path.basename(words[0])) == "env" and len(words) > 1:
        name = next((w for w in words[1:] if not w.startswith("-")), "")
        return _which_privileged(name) or "" if _PLAIN_NAME.fullmatch(name) else ""
    return words[0]


def _ovpn_program_findings(raw: str, command: list[str], budget: dict | None = None, *,
                           library: bool = False) -> list[str]:
    """Why an OpenVPN hook command could run code the review did not show, or [].

    OpenVPN execs the first word itself: no shell, no PATH, no VAR=. It must be
    an absolute, existing, root-owned path. A relative one would resolve inside
    root's copies of the config's folder (openvpn starts there with --cd), and
    '..' leaves them (S8b re-review R-P1-1); a bare plugin name is loaded from
    OpenVPN's plugin folder, which Homebrew leaves to the user. A system
    program or an interpreter or wrapper, by any spelling, runs other code the
    review cannot check (R-P1-2, review R5-P1-1). The arguments follow
    ``_ovpn_argument_findings``. ``library`` (a plugin module) skips the
    program-kind checks."""
    program, args = command[0], command[1:]
    if not program.startswith("/"):
        return [f"The {raw} hook names '{program}', which is not an absolute path. OpenVPN "
                "starts in root's copy of the config's folder, so Tunnel cannot check what "
                "it runs; name a root-owned program by absolute path."]
    if not _spend(budget):
        return []
    unsafe: list[str] = []
    reason = _unsafe_executable_reason(Path(program))
    if reason:
        unsafe.append(f"The {raw} hook runs {program} as administrator, and {reason}, so a "
                      "normal user could replace it.")
    elif not _exists(program):
        # It would fail anyway, and a missing path is what an OpenVPN parse
        # mismatch looks like (S8b security P1-2).
        unsafe.append(f"The {raw} hook names {program}, which does not exist.")
    if not library and _system_program(program):
        unsafe.append(f"The {raw} hook runs {program}, a macOS system program; many of these "
                      "run other code they are given (an interpreter or wrapper), so name a "
                      "root-owned script of your own by absolute path.")
    elif not library and _runs_code(program):
        unsafe.append(f"The {raw} hook runs its command through {program}, an interpreter or "
                      "wrapper whose arguments the review cannot check; name a root-owned "
                      "script directly.")
    elif not library and not unsafe:
        # A root-owned script is only as safe as the interpreter its #! line
        # names (security review P2-1); root re-checks it too (_openvpn_programs).
        interpreter = _interpreter(program)
        if interpreter == "" or (interpreter and not interpreter.startswith("/")):
            unsafe.append(f"The {raw} hook runs {program}, whose #! line names no program in "
                          f"{', '.join(_PRIVILEGED_PATH)} by full path; Tunnel cannot check "
                          "what runs it.")
        elif interpreter:
            reason = _unsafe_executable_reason(Path(interpreter))
            if reason is None and not _exists(interpreter):
                reason = f"{interpreter} does not exist"
            if reason:
                unsafe.append(f"The {raw} hook runs {program}, whose #! line runs it with "
                              f"{interpreter} as administrator, and {reason}, so a normal "
                              "user could replace it.")
    return unsafe + _ovpn_argument_findings(raw, args, budget)


def _ovpn_argument_findings(raw: str, args: list[str], budget: dict | None = None) -> list[str]:
    """Any argument that names a path (contains '/', or starts with '.' or '~')
    must be an absolute, existing, root-owned path: the program may run or read
    it, a relative one resolves inside root's copies or, through '..',
    anywhere, and a missing one is whatever a user mounts there later (review
    R5-P1-1, R5-P2-1, R7-P2-3)."""
    unsafe: list[str] = []
    for arg in args:
        # --conf=/x and KEY=../x: judge the value (review R7-P3-1).
        word = arg.split("=", 1)[1] if "=" in arg and not arg.startswith(("/", "~", ".")) else arg
        if word == "/dev/null":
            continue
        if word.startswith("/"):
            if not _spend(budget):
                return unsafe
            reason = _unsafe_executable_reason(Path(word))
            if reason is None and not _exists(word):
                reason = f"{word} does not exist"
            if reason:
                unsafe.append(f"The {raw} hook hands {word} to the program as administrator, "
                              f"and {reason}, so a normal user could replace it.")
        elif word.startswith("~"):
            unsafe.append(f"The {raw} hook hands {word}, in a user's home directory, to the "
                          "program; name a root-owned file by absolute path.")
        elif ("/" in word or word.startswith(".")) and not _CIDR.fullmatch(word):
            unsafe.append(f"The {raw} hook hands '{word}', a relative path, to the program; "
                          "name a root-owned file by absolute path.")
    return unsafe


def _openvpn_programs(text: str) -> list[str]:
    """The hook programs, plugin modules and PKCS#11 libraries an OpenVPN config
    names by absolute path, as root re-checks them before start (spec 1.3)."""
    found: list[str] = []
    for raw, key, _value, words in _scan(text, "OpenVPN")[0]:
        if key in OPENVPN_EXECUTABLE_KEYS:
            try:
                segment = _segments(words)[0]
            except ValueError:
                continue
            candidates = segment[:1]
            names = ((segment[0].rsplit("/", 1)[-1], os.path.basename(_realpath(segment[0])))
                     if segment else ())
            if key == "plugin" and len(segment) > 1 and any("down-root" in _fold(n) for n in names):
                candidates.append(segment[1])
        elif key == "pkcs11-providers":
            candidates = list(words)
        else:
            continue
        if key != "pkcs11-providers":
            # The interpreter of a hook script is re-checked like the script.
            candidates += [i for i in map(_interpreter, candidates[:1])
                           if i and i.startswith("/")]
        for program in candidates:
            if program.startswith("/") and program not in found:
                found.append(program)
    return found


def _hook_findings(text: str, protocol: str) -> tuple[list[str], list[str], list[str]]:
    """(blockers, soft blockers, warnings). Soft blockers (a hook program a user
    could replace) are downgraded to risks on a Disconnect, which must never be
    impossible; everything in ``blockers`` always stops."""
    blockers: list[str] = []
    soft: list[str] = []
    warnings: list[str] = []
    unknown: list[str] = []
    found, problems = _scan(text, protocol)
    blockers.extend(f"{protocol} config: {item}." for item in problems)
    if protocol == "OpenVPN":
        # OpenVPN reads a config through a 256-byte buffer (OPTION_LINE_SIZE):
        # a longer line, inline blocks included, is split, and its tail read as
        # a line of its own, which can end a <ca> block early and turn what
        # follows into directives this review took for data (security review P1-1).
        long_lines = [number for number, line in enumerate(text.split("\n"), 1)
                      if len(line.encode("utf-8", "surrogatepass")) >= OPENVPN_LINE_LIMIT]
        if long_lines:
            blockers.append(f"OpenVPN config: line {long_lines[0]} is {OPENVPN_LINE_LIMIT} bytes "
                            "or longer, and OpenVPN would read it as more than one line; "
                            "Tunnel cannot tell what it would see. Shorten it.")
    if "\x00" in text:
        # Never depend on agreeing with bash or OpenVPN byte for byte.
        blockers.append(f"{protocol} config: it contains a NUL byte, which can hide a "
                        "directive from this review; remove it.")
    hook_keys = ({k.lower() for k in WG_HOOK_KEYS} if protocol == "WireGuard"
                 else set(OPENVPN_HOOK_KEYS))
    budget = {"left": HOOK_CHECK_LIMIT, "over": False}
    for raw, key, value, words in found:
        overlong = len(raw) + len(value) > HOOK_LINE_LIMIT and key in hook_keys
        if overlong:
            blockers.append(f"The {raw} line is longer than {HOOK_LINE_LIMIT} characters; "
                            "Tunnel will not run what a person cannot review.")
        # wg-quick drops everything from the first "#" before it reads a value.
        if (protocol == "WireGuard" and key == "saveconfig"
                and value.split("#", 1)[0].strip(_WS).lower() == "true"):
            blockers.append("SaveConfig = true makes wg-quick rewrite the config as "
                            "administrator when the tunnel goes down; set it to false.")
        if protocol == "OpenVPN":
            if key in OPENVPN_BLOCKER_KEYS or key.startswith("management"):
                blockers.append(f"The OpenVPN directive '{key}' can hide commands or redirect "
                                "what administrator rights write; remove it from the profile.")
            elif key in OPENVPN_CODE_KEYS:
                blockers.append(f"The OpenVPN directive '{key}' can load a library into "
                                "OpenVPN as administrator; remove it from the profile.")
            elif key in OPENVPN_SERVER_KEYS or (key == "mode" and words[:1] == ["server"]):
                blockers.append(f"The OpenVPN directive '{key}' sets up a server; Tunnel runs "
                                "only client profiles.")
            elif key == "dev-node" and not (len(words) == 1
                                            and OPENVPN_DEV_NODE.fullmatch(words[0])):
                blockers.append("The OpenVPN directive 'dev-node' names something other than a "
                                "utun device, which administrator rights would open; remove it.")
            elif key == "dev" and not (len(words) == 1 and OPENVPN_DEV.fullmatch(words[0])):
                blockers.append("The OpenVPN directive 'dev' names something other than a tun, "
                                "tap or utun device, which administrator rights could open; use "
                                "'dev tun'.")
            elif key not in OPENVPN_KNOWN_KEYS and key not in unknown:
                unknown.append(key)
            other = [word for word in words if word not in OPENVPN_PROVIDERS]
            if key == "providers" and other:
                blockers.append(f"The OpenVPN providers line names {other[0]!r}, which is not "
                                "one of OpenSSL's own providers (" + ", ".join(
                                    sorted(OPENVPN_PROVIDERS)) + "); OpenSSL would load it "
                                "as a module into OpenVPN as administrator. Remove it.")
            if key == "pkcs11-providers" and not overlong:
                for library in words:
                    if not _spend(budget):
                        break
                    if not library.startswith("/"):
                        soft.append(f"The {raw} line loads {library}, a relative path, into "
                                    "OpenVPN as administrator; the system decides where it "
                                    "is found, so Tunnel cannot check it. Write the full path.")
                    elif not _exists(library):
                        # A library that is not there is what a parse mismatch
                        # looks like, and a user could create it later (S8b security P1-2).
                        soft.append(f"The {raw} line loads {library}, which does not exist.")
                    else:
                        _path_findings(raw, library, 0, True, protocol, soft, warnings)
        # An overlong hook is already refused; walking its paths could take long
        # enough (a 256 KiB line) to freeze the window before the refusal shows.
        if (key in hook_keys and not overlong and not budget["over"]
                and (protocol == "WireGuard" or key in OPENVPN_EXECUTABLE_KEYS)):
            more_soft, more_warn = _program_findings(raw, value, words, protocol, budget)
            soft.extend(more_soft)
            warnings.extend(more_warn)
    if budget["over"]:
        blockers.append(f"The {protocol} config's hooks name more than {HOOK_CHECK_LIMIT} "
                        "commands and paths; Tunnel will not run more than a person can "
                        "review.")
    if unknown:
        warnings.append("OpenVPN directives not on the reviewed list (allowed, but Tunnel "
                        "has not checked what they do): " + ", ".join(unknown[:12])
                        + (" …" if len(unknown) > 12 else ""))
    return blockers, soft, warnings


def _same_name(a: str, b: str) -> bool:
    """Whether two file names are one name on the default macOS volume, which
    ignores case and Unicode normalisation form (S8b security P3-5)."""
    return _fold(a) == _fold(b)


def _openvpn_file_names(text: str) -> tuple[list[str], list[str]]:
    """(names, blockers): each file an OpenVPN config reads, once, as root will
    take it (a relative name normalised), and why any name is refused. Inline
    blocks, ``[inline]`` beside its block and OpenVPN's special words are not
    files (see ``OPENVPN_FILE_WORD``)."""
    inline_tags = {match.group(1).lower() for line in text.split("\n")
                   if (match := _OVPN_TAG.fullmatch(line.strip(_WS)))}
    names: list[str] = []
    blockers: list[str] = []
    for _raw, key, _value, words in _scan(text, "OpenVPN")[0]:
        index = OPENVPN_FILE_WORD.get(key)
        ref = words[index] if index is not None and len(words) > index else ""
        not_files = ((("[inline]",) if key in inline_tags else ())
                     + (("stdin",) if key in OPENVPN_STDIN_KEYS else ())
                     + (("auto", "auto-nct") if key == "http-proxy" else ())
                     + (("none",) if key == "dh" else ()))      # ECDH only, no file
        if not ref or ref in not_files:
            continue
        if ref.startswith("/"):
            name = ref
        elif ".." in ref.split("/"):
            blockers.append(f"The config reads '{ref}', which is outside the folder the config "
                            "is in or reached through '..'; Tunnel copies only files beside "
                            "the config. Move it next to the config and name it plainly.")
            continue
        else:
            name = posixpath.normpath(ref)
        try:
            vpn_rootscript.include_path(name)
        except ValueError:
            blockers.append(f"The config reads '{ref}', which is not a plain file path; "
                            "write it without '.', '..', doubled slashes or a hidden '/.' prefix.")
            continue
        twin = next((other for other in names
                     if other != name and _same_name(other, name)), None)
        if twin is not None:
            # The default macOS volume reads both as one file, and root refuses the pair.
            blockers.append(f"The config reads '{twin}' and '{name}', which differ only in case "
                            "or Unicode form; use one spelling.")
            continue
        if name not in names:
            names.append(name)
    return names, blockers


def _openvpn_includes(text: str, config: Path
                      ) -> tuple[list[tuple[str, str]], list[str], list[str], list[str]]:
    """(manifest, blockers, warnings, root_only) for the data files an OpenVPN
    config reads. ``root_only`` holds absolute files only root may read (a key in
    ``/etc/openvpn``): Sentinel cannot hash them, and they are root-owned all the
    way up, so root re-checks that ownership before start instead (code review P2-1).

    The manifest holds (path as root takes it, SHA-256 of the bytes). A relative
    path is normalised and must stay inside the config's folder, through
    symbolic links too, because root copies it there; an absolute one must be
    root-owned all the way up and is checked in place. Each must be a regular
    file (not a link) of at most ``vpn_rootscript.INCLUDE_LIMIT`` bytes, at most
    ``vpn_rootscript.MAX_INCLUDES`` in all. Anything else blocks, and then the
    manifest is empty (audit step S8b)."""
    names, blockers = _openvpn_file_names(text)
    if len(names) > vpn_rootscript.MAX_INCLUDES:
        # Checked before any file is opened.
        return [], blockers + [f"The config reads {len(names)} files; Tunnel copies at most "
                               f"{vpn_rootscript.MAX_INCLUDES}."], [], []
    folder = config.parent
    limit = vpn_rootscript.INCLUDE_LIMIT
    manifest: list[tuple[str, str]] = []
    root_only: list[str] = []
    real_folder = _realpath(folder)
    for name in names:
        path = Path(name) if name.startswith("/") else folder / name
        try:
            info = path.lstat()
        except OSError:
            blockers.append(f"The config reads {name}, which does not exist or cannot be read.")
            continue
        if stat.S_ISLNK(info.st_mode):
            blockers.append(f"The config reads {name}, which is a symbolic link; point the "
                            "config at the file itself.")
            continue
        if stat.S_ISDIR(info.st_mode):
            # capath, crl-verify <dir> dir: a folder's contents cannot be listed
            # and checked like one file (code review P3-4).
            blockers.append(f"The config reads {name}, which is a folder; Tunnel copies and "
                            "checks only files. Put the certificates in one file (ca, "
                            "crl-verify) or inline (<ca>…</ca>).")
            continue
        if not stat.S_ISREG(info.st_mode):
            blockers.append(f"The config reads {name}, which is not a regular file.")
            continue
        if name.startswith("/"):
            # Not copied, so it must be a file the user cannot swap after root's
            # check: openvpn reads it as root, and re-reads some while the tunnel
            # is up (S8b security P2-2).
            reason = _unsafe_executable_reason(path)
            if reason:
                blockers.append(f"The config reads {name} by absolute path, and {reason}, so "
                                "it could be swapped after the check; put it beside the config "
                                "so root copies it.")
                continue
        elif os.path.commonpath([_realpath(path), real_folder]) != real_folder:
            blockers.append(f"The config reads {name}, which is outside the folder the config "
                            "is in (through a symbolic link); move it next to the config.")
            continue
        try:
            # O_NONBLOCK: a file swapped for a FIFO since lstat must not hang the review.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise OSError("not a regular file")
                data = handle.read(limit + 1)
        except PermissionError:
            if name.startswith("/"):                 # root-owned all the way up (above)
                root_only.append(name)
                continue
            blockers.append(f"The config reads {name}, which cannot be read.")
            continue
        except OSError:
            blockers.append(f"The config reads {name}, which cannot be read.")
            continue
        if len(data) > limit:
            blockers.append(f"The config reads {name}, which is larger than "
                            f"{limit // 1024} KiB; Tunnel will not copy it.")
            continue
        manifest.append((name, hashlib.sha256(data).hexdigest()))
    if blockers:
        return [], blockers, [], []
    absolute = [name for name, _ in manifest if name.startswith("/")]
    warnings = []
    if absolute:
        warnings.append(f"{len(absolute)} file(s) the config reads are named by absolute path "
                        f"({', '.join(absolute[:3])}{' …' if len(absolute) > 3 else ''}). "
                        "Root does not copy them: they are checked in place just before "
                        "OpenVPN starts. Root owns them, so your user cannot change them; "
                        "keep them beside the config to have them copied instead.")
    if root_only:
        warnings.append(f"{len(root_only)} file(s) the config reads can be read only by root "
                        f"({', '.join(root_only[:3])}{' …' if len(root_only) > 3 else ''}), "
                        "so Tunnel cannot check their contents. Root owns them and every folder "
                        "above them, and checks that again just before OpenVPN starts.")
    return manifest, [], warnings, root_only


ACTIONS = ("connect", "disconnect")


# ── Local state probes (read-only, no root) ─────────────────────────────────

def wireguard_interface_state(name: str, run_dir: Path = WG_RUN_DIR) -> dict:
    """Whether wg-quick currently has ``name`` up, from its own run records.

    On macOS wg-quick maps the configured name to a kernel-numbered utun in
    ``<run_dir>/<name>.name`` and serves ``<utun>.sock``. The name record can be
    unreadable to a normal user, so its presence is the signal and the device is
    reported only when it can be read.
    """
    record = run_dir / f"{name}.name"
    try:
        present = record.exists()
    except OSError:
        present = False
    if not present:
        return {"up": False, "device": None}
    try:
        device = record.read_text(encoding="utf-8").strip() or None
    except OSError:
        return {"up": True, "device": None}
    if device and not (run_dir / f"{device}.sock").exists():
        return {"up": False, "device": device, "stale": True}
    return {"up": True, "device": device}


def route_interface(address: str = ROUTE_PROBE_ADDRESS) -> str | None:
    """The interface macOS would use for ``address`` (routing lookup only)."""
    route = shutil.which("route") or ("/sbin/route" if Path("/sbin/route").is_file() else None)
    if route is None:
        return None
    try:
        proc = subprocess.run([route, "-n", "get", address],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key == "interface":
            return value.strip() or None
    return None


def _default_probe() -> dict:
    return {
        "wireguard": wireguard_interface_state,
        "openvpn_running": openvpn_manager.is_running,
        "route_interface": route_interface,
    }


# ── Target review ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionReview:
    action: str
    protocol: str
    profile_name: str
    target: str
    interface: str
    command: str
    route_mode: str = ""
    config_lines: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    rollback: tuple[str, ...] = ()
    root_hooks: tuple[str, ...] = ()
    config_sha256: str = ""
    # WireGuard: the wg-quick the review judged, by absolute path. Root runs
    # exactly this one, so a wg-quick put earlier on PATH after the dialog is
    # not picked up (S7 security review P2-A).
    wg_quick: str = ""
    # Disconnect only: findings that would block a Connect but must not make a
    # Disconnect impossible. They need a second, explicit "Disconnect anyway".
    risks: tuple[str, ...] = ()
    # OpenVPN Connect only: (path as root takes it, SHA-256) for each file the
    # config reads. Relative paths are inside the config's folder; root copies
    # those and checks absolute ones in place (ADR 0004, S8b).
    includes: tuple[tuple[str, str], ...] = ()
    # OpenVPN Connect only: the openvpn program the review resolved; root runs
    # this path and no other (S8b security P3-1).
    program: str = ""
    # OpenVPN Connect only: the hook programs and libraries the review accepted
    # as root-owned; root checks them again just before start (spec 1.3).
    programs: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.blockers

    @property
    def needs_second_confirmation(self) -> bool:
        return bool(self.risks)

    def fingerprint(self) -> str:
        """Identity of what administrator rights would run: action, protocol,
        target, interface, command, every hook in full, the hash of the config
        bytes and of every file an OpenVPN config reads, and the program that
        runs. Warnings and blockers are left out on purpose (probe state may
        legitimately change between the dialog and the worker)."""
        payload = json.dumps([self.action, self.protocol, self.target, self.interface,
                              self.command, list(self.root_hooks), self.config_sha256,
                              list(self.risks), self.wg_quick,
                              [list(item) for item in self.includes], self.program,
                              list(self.programs)],
                             ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _shown_hook(item: str) -> str:
        if len(item) <= HOOK_DISPLAY_LIMIT:
            return item
        return (item[:HOOK_DISPLAY_LIMIT]
                + f" … ({len(item) - HOOK_DISPLAY_LIMIT} more characters; see the details)")

    @property
    def hooks_shortened(self) -> bool:
        return any(len(item) > HOOK_DISPLAY_LIMIT for item in self.root_hooks)

    def confirmation_detail(self) -> str:
        """Everything the dialog body shortens: each hook whole, and the hash."""
        lines = [f"• {item}" for item in self.root_hooks]
        if self.config_sha256:
            lines += ["", self._hash_line()]
        if self.includes:
            lines += ["", self._includes_heading()] + self._include_lines()
        return "\n".join(lines)

    @staticmethod
    def _includes_heading() -> str:
        return ("Files the config reads (root copies those beside the config and checks each "
                "against the SHA-256 this review read; a file named by absolute path is "
                "checked where it is):")

    def _include_lines(self) -> list[str]:
        # No hash is shown: a password file's hash can be guessed offline (S8b
        # review P3-4). The hashes are in the fingerprint and go to root.
        return [f"• {path}" for path, _digest in self.includes]

    @property
    def _may_run_kept_copy(self) -> bool:
        """A WireGuard Disconnect: root runs the copy kept since Connect when there
        is one, and Sentinel cannot see into root's folder to know which."""
        return self.protocol == "WireGuard" and self.action == "disconnect"

    def _hash_line(self) -> str:
        if self._may_run_kept_copy:
            return (f"SHA-256 of your config file now: {self.config_sha256} (root checks "
                    "it on its own copy only if it kept no copy at Connect; otherwise the "
                    "copy you connected with runs)")
        return (f"Config SHA-256: {self.config_sha256} (checked again by root on its "
                "own copy, which is what runs)")

    def _hooks_heading(self) -> str:
        if self._may_run_kept_copy:
            return ("RUNS COMMANDS AS ADMINISTRATOR — your config file now executes these, "
                    "but only if Sentinel kept no copy at Connect; otherwise the copy you "
                    "connected with runs, with its hooks as they were then:")
        return "RUNS COMMANDS AS ADMINISTRATOR — this config executes:"

    def confirmation_text(self) -> str:
        verb = "Start" if self.action == "connect" else "Stop"
        lines = [
            f"{verb} a real {self.protocol} tunnel for '{self.profile_name}'?",
            "",
            f"Target: {self.target}",
            f"Command (as administrator): {self.command}",
        ]
        if self.config_lines:
            lines += [""] + list(self.config_lines)
        if self.root_hooks:
            lines += ["", self._hooks_heading()]
            lines += [f"• {self._shown_hook(item)}" for item in self.root_hooks]
        if self.risks:
            lines += ["", "Not reviewable (Disconnect asks once more before it goes ahead):"]
            lines += [f"• {item}" for item in self.risks]
        if self.warnings:
            lines += ["", "Check first:"] + [f"• {item}" for item in self.warnings]
        lines += ["", "To undo:"] + [f"• {item}" for item in self.rollback]
        lines += ["", "Tunnel will re-check local state afterwards. It keeps no log "
                      "of this attempt. Traffic protection is not verified by "
                      "this check."]
        return "\n".join(lines)

    def sections(self) -> list[tuple[str, str, bool]]:
        head = [f"Action: {self.action.title()}", f"Protocol: {self.protocol}",
                f"Profile: {self.profile_name}", f"Target: {self.target or 'None'}"]
        if self.interface:
            head.append(f"Interface: {self.interface}")
        if self.config_sha256:
            head.append(self._hash_line())
        sections: list[tuple[str, str, bool]] = [
            ("Target review", "\n".join(head), True),
        ]
        if self.command:
            sections.append(("Command (runs as administrator)", self.command, True))
        if self.config_lines:
            sections.append(("Configuration intent (keys discarded)",
                             "\n".join(self.config_lines), True))
        if self.root_hooks:
            sections.append(("Runs commands as administrator" + (
                                 " (your file now)" if self._may_run_kept_copy else ""),
                             "\n".join(f"• {item}" for item in self.root_hooks), True))
        if self.includes:
            sections.append(("Files the config reads (hashed)",
                             "\n".join([self._includes_heading()] + self._include_lines()),
                             True))
        if self.risks:
            sections.append(("Risks — Disconnect anyway?",
                             "\n".join(f"• {item}" for item in self.risks), False))
        if self.blockers:
            sections.append(("Refused — nothing was run",
                             "\n".join(f"• {item}" for item in self.blockers), False))
        if self.warnings:
            sections.append(("Check before running",
                             "\n".join(f"• {item}" for item in self.warnings), False))
        if self.rollback:
            sections.append(("Rollback", "\n".join(f"• {item}" for item in self.rollback), False))
        return sections


def _wg_interface_for(profile: dict) -> tuple[str, str]:
    """(target passed to wg-quick, interface name wg-quick will create)."""
    config_path = vpn_connection.resolve_config_path(profile)
    if config_path:
        return config_path, Path(config_path).stem
    interface = str(profile.get("interface") or "").strip()
    return interface, interface


def _killswitch_armed() -> bool:
    ks = vpn_connection._killswitch()
    if ks is None:
        return False
    try:
        # A marker from before the last boot is dropped first; a recorded (not
        # verified) switch counts as armed here, the fail-safe direction: the
        # warning says traffic stays blocked.
        reconcile = getattr(ks, "reconcile", None)
        if reconcile is not None:
            reconcile()
        return bool(ks.status().armed)
    except Exception:
        return False


def review_execution(action: str, profile: dict | None, *,
                     probe: dict | None = None,
                     killswitch_armed: Callable[[], bool] | None = None) -> ExecutionReview:
    """Everything the user must see — and every reason to refuse — before running."""
    probe = probe or _default_probe()
    killswitch_armed = killswitch_armed or _killswitch_armed
    profile = dict(profile or {})
    action = str(action).strip().lower()
    name = str(profile.get("name") or "Unnamed")
    protocol = vpn_connection.resolve_protocol(profile)
    if action not in ACTIONS:
        return ExecutionReview(action, protocol, name, "", "", "",
                               blockers=(f"Unknown action '{action}'.",))
    if not profile:
        return ExecutionReview(action, protocol, name, "", "", "",
                               blockers=("Choose a server profile first.",))

    blockers: list[str] = []
    warnings: list[str] = []
    config_lines: list[str] = []
    root_hooks: list[str] = []
    risks: list[str] = []
    route_mode = ""
    config_sha256 = ""
    wg_quick = ""
    program = ""
    includes: list[tuple[str, str]] = []
    programs: list[str] = []
    ks_armed = killswitch_armed()

    if action == "connect" and vpn_connection.is_placeholder(profile):
        blockers.append("This profile is an example/template, not a real server. "
                        "Import a WireGuard .conf or OpenVPN .ovpn first.")

    if protocol == "WireGuard":
        target, interface = _wg_interface_for(profile)
        command = (vpn_connection.wg_root_summary("up" if action == "connect" else "down", target)
                   if target else "")
        if not vpn_connection.wireguard_manager.is_wg_quick_available():
            blockers.append("wg-quick not found — install wireguard-tools "
                            "(`brew install wireguard-tools`).")
        if not target:
            blockers.append("This WireGuard profile has no interface or config file.")
        elif not WG_INTERFACE_NAME.fullmatch(interface):
            blockers.append(
                f"'{interface}' is not a valid WireGuard interface name (letters, digits "
                "and _=+.- only, at most 15 characters). wg-quick names the interface "
                "after the config file, so rename the file and import it again.")
        elif (vpn_connection.resolve_config_path(profile)
              and not vpn_rootscript.CONFIG_NAME_RE.fullmatch(
                  Path(vpn_connection.resolve_config_path(profile)).name)):
            # Any config_path is a file to root (made absolute by the runner),
            # even one with no folder and no .conf (S7 Opus audit P3-5).
            blockers.append(
                f"'{Path(target).name}' is not named <interface>.conf. wg-quick only loads a "
                "config named after its interface (letters, digits and _=+.- only, at most "
                "15 characters, then .conf), so rename the file and import it again.")
        config_path = vpn_connection.resolve_config_path(profile)
        source = Path(config_path).expanduser() if config_path else None
        if source is None and interface and WG_INTERFACE_NAME.fullmatch(interface):
            # An interface-only profile: wg-quick finds <interface>.conf itself, so
            # the review finds the same file and hashes and scans that.
            source = next((Path(d) / f"{interface}.conf" for d in WG_CONFIG_DIRS
                           if (Path(d) / f"{interface}.conf").is_file()), None)
            if source is not None:
                # Run exactly the file that is hashed and scanned here. A bare name
                # lets wg-quick search its own directory list (which differs between
                # builds), so it could load a different <interface>.conf as root.
                target = str(source)
                command = vpn_connection.wg_root_summary(
                    "up" if action == "connect" else "down", target)
        if source is not None:
            # Both actions read the file: wg-quick down re-reads it and runs
            # PreDown/PostDown, so a disconnect is reviewed like a connect.
            text, config_sha256, read_blockers, read_warnings = _read_config(source)
            warnings.extend(read_warnings)
            if text is None:
                # Bytes that could not be scanned must never reach root as a
                # verified config: without a hash a WireGuard Disconnect can only
                # use the copy kept since Connect, or refuses (S7).
                config_sha256 = ""
                if action == "connect":
                    blockers.extend(read_blockers)
                elif source.exists():
                    risks.extend(read_blockers)      # never strand a Disconnect
            else:
                root_hooks = hooks_from_text(text, "WireGuard")
                hook_blockers, soft_blockers, hook_warnings = _hook_findings(text, "WireGuard")
                warnings.extend(hook_warnings)
                if action == "connect":
                    blockers.extend(hook_blockers + soft_blockers)
                else:
                    risks.extend(hook_blockers + soft_blockers)
                if action == "connect":
                    summary = inspect_config_text(text, source)
                    route_mode = summary.route_mode
                    config_lines = [
                        f"Routing: {summary.route_mode}",
                        "Endpoints: " + (", ".join(summary.endpoints) or "Not specified"),
                        "DNS: " + (", ".join(summary.dns_servers)
                                   or ("Unknown — an entry has a wildcard" if summary.dns_wildcards
                                       else "Not specified — system DNS stays in use")),
                        *(["DNS search domains: " + ", ".join(summary.dns_search)
                           + ("" if summary.dns_servers else
                              " (not applied: wg-quick sets them only with a DNS server)")]
                          if summary.dns_search else []),
                        f"Peers: {summary.peer_count}",
                    ]
                    warnings.extend(summary.warnings)
                    if summary.dns_wildcards:
                        # wg-quick expands DNS unquoted: what a wildcard becomes, and so
                        # which DNS server applies, cannot be known before it runs.
                        blockers.append("The DNS line has an entry with a wildcard ("
                                        + ", ".join(summary.dns_wildcards[:5])
                                        + "); wg-quick's shell may expand it to other "
                                        "entries, so the DNS this tunnel uses cannot be "
                                        "checked. Write each DNS server out in full.")
                    if summary.peer_count == 0 or not summary.endpoints:
                        blockers.append("The config has no peer endpoint to connect to.")
                    if route_mode == "Split tunnel":
                        warnings.append("Split tunnel: only the listed networks use the VPN; "
                                        "other traffic keeps your normal route.")
                    try:
                        mode = source.stat().st_mode
                        if mode & (stat.S_IRGRP | stat.S_IROTH):
                            warnings.append("The config file (which holds your private key) is "
                                            "readable by other users on this Mac — consider "
                                            "`chmod 600` on it.")
                    except OSError:
                        pass
        elif action == "connect" and interface and WG_INTERFACE_NAME.fullmatch(interface):
            blockers.append(
                f"No config for '{interface}' was found in {', '.join(WG_CONFIG_DIRS)}. "
                "wg-quick would fail, and Tunnel cannot review what it cannot read; "
                "import the .conf file instead.")
        if action == "disconnect" and target and not config_sha256:
            # No hash travels (the file is missing or could not be scanned):
            # root can only use the copy kept since Connect, so say just that.
            command = vpn_connection.wg_root_summary(
                "down", vpn_rootscript.interface_for(target) or interface)
        exe = shutil.which("wg-quick")
        exe = wg_quick = vpn_connection._absolute(exe) if exe else ""
        reason = _unsafe_executable_reason(Path(exe)) if exe else None
        if reason:
            # Owner decision 2026-11-05: warn, do not block (it would stop every
            # Homebrew install). The verified copy protects the config, not the tools.
            warnings.append(
                f"wg-quick runs as administrator from {exe}, and {reason}: a program "
                "running as you could replace it (or wg and wireguard-go beside it). "
                "Sentinel's verified copy protects the config file, not these tools.")
        if action == "disconnect" and source is not None:
            warnings.append(
                "If Sentinel kept a copy of the config at Connect, Disconnect runs that "
                "copy (its PreDown/PostDown as they were when you connected), not your "
                "file as it is now.")
        if interface and WG_INTERFACE_NAME.fullmatch(interface):
            state = probe["wireguard"](interface)
            if action == "connect" and state.get("up"):
                blockers.append(f"'{interface}' is already up — Disconnect it first.")
            if action == "disconnect" and not state.get("up"):
                warnings.append(f"'{interface}' is not currently recorded as up; "
                                "wg-quick down will probably report that.")
        # For a person at a terminal: sudo can refuse a leading PATH=… assignment,
        # and their shell already finds wg-quick.
        shown = vpn_connection._quote(target) if target else "…"
        up_cmd, down_cmd = f"wg-quick up {shown}", f"wg-quick down {shown}"
        if action == "connect":
            rollback = [f"Press Disconnect, or run: sudo {down_cmd}"]
        else:
            rollback = [f"Press Connect, or run: sudo {up_cmd}"]
    elif protocol == "OpenVPN":
        interface = ""
        target = vpn_connection.resolve_config_path(profile) or ""
        # Resolved once: root runs exactly this path, and it is in the fingerprint,
        # so a program planted earlier in the search order changes the review
        # (S8b security P3-1).
        program = (openvpn_manager.openvpn_binary() or "") if action == "connect" else ""
        if action == "connect":
            command = (f"copy {vpn_connection._quote(target)} and the files it reads into "
                       f"{vpn_rootscript.ovpn_path('ovpn/private')}, check each copy's SHA-256 "
                       f"against this review, then from / with an empty environment: "
                       f"{shlex.quote(program) if program else 'openvpn'} --cd "
                       f"{vpn_rootscript.ovpn_path(vpn_rootscript.OVPN_COPY_DIR)} --config "
                       f"{vpn_rootscript.ovpn_path(vpn_rootscript.OVPN_CONFIG_COPY)} "
                       f"--daemon sentinel-ovpn --writepid "
                       f"{vpn_rootscript.ovpn_path(vpn_rootscript.OVPN_PID)} …"
                       if target else "")
        else:
            command = "kill -TERM <pid of Sentinel's openvpn, re-checked as administrator>"
        if action == "connect":
            if not program:
                # The resolved program decides, not a second lookup (re-review R-P3-5).
                blockers.append("openvpn client not found — install it, e.g. "
                                "`brew install openvpn`.")
            elif any(ch.isspace() for ch in program):
                # ps prints argv unquoted, so Sentinel could never recognise the
                # tunnel; the root script refuses it too (code review P3-3).
                blockers.append(f"openvpn is installed at {program}, a path with a space; "
                                "Tunnel cannot track a tunnel started from there. Install "
                                "it where the path has no spaces (Homebrew does).")
            if not target:
                blockers.append("This OpenVPN profile has no config file.")
            else:
                source = Path(target).expanduser()
                text, config_sha256, read_blockers, read_warnings = _read_config(source)
                warnings.extend(read_warnings)
                blockers.extend(read_blockers)
                if text is not None:
                    root_hooks = hooks_from_text(text, "OpenVPN")
                    hook_blockers, soft_blockers, hook_warnings = _hook_findings(text, "OpenVPN")
                    blockers.extend(hook_blockers + soft_blockers)
                    warnings.extend(hook_warnings)
                    includes, file_blockers, file_warnings, root_only = _openvpn_includes(
                        text, Path(vpn_connection._absolute(str(source))))
                    blockers.extend(file_blockers)
                    warnings.extend(file_warnings)
                    # Root re-checks hook programs, libraries and root-only files
                    # the same way: owned by root all the way up (spec 1.3).
                    programs = _openvpn_programs(text) + root_only
                    if len(programs) > vpn_rootscript.MAX_PROGRAMS:
                        blockers.append(f"The config names {len(programs)} hook programs; "
                                        f"Tunnel checks at most {vpn_rootscript.MAX_PROGRAMS}.")
            if probe["openvpn_running"]():
                blockers.append("An OpenVPN process is already running — Disconnect it first.")
            reason = _unsafe_executable_reason(Path(program)) if program else None
            if reason:
                # As for wg-quick (owner decision 2026-11-05): warn, do not block.
                warnings.append(
                    f"openvpn runs as administrator from {program}, and {reason}: a program "
                    "running as you could replace it. Sentinel's verified copies protect the "
                    "config and its files, not the tool.")
            rollback = ["Press Disconnect (only a process Sentinel started and "
                        "tracks will be stopped)."]
        else:
            warnings.append("OpenVPN runs its down hooks inside the daemon you confirmed at "
                            "Connect; Disconnect only signals that process.")
            rollback = ["Press Connect to start the tunnel again."]
    else:
        interface, target, command = "", "", ""
        blockers.append(f"Unsupported protocol '{profile.get('protocol')}'.")
        rollback = []

    if root_hooks:
        warnings.append(f"This config runs {len(root_hooks)} command(s) as administrator "
                        "when the tunnel comes up or goes down (see the list above).")

    if action == "connect":
        app_warning = vpn_apps.connect_warning()
        if app_warning:
            warnings.append(app_warning)

    if action == "disconnect":
        if ks_armed:
            warnings.append("The kill switch is armed: after the tunnel stops, all "
                            "traffic stays blocked until you Disarm it.")
        else:
            warnings.append("After the tunnel stops, traffic returns to your ordinary "
                            "network in the clear.")
    elif ks_armed:
        rollback.append("The kill switch is armed; Disarm restores ordinary traffic.")

    return ExecutionReview(
        action=action, protocol=protocol, profile_name=name, target=target,
        interface=interface, command=command, route_mode=route_mode,
        config_lines=tuple(config_lines), blockers=tuple(blockers),
        warnings=tuple(warnings), rollback=tuple(rollback),
        root_hooks=tuple(root_hooks), config_sha256=config_sha256,
        wg_quick=wg_quick, risks=tuple(risks), includes=tuple(includes),
        program=program, programs=tuple(programs),
    )


# ── Post-change check ───────────────────────────────────────────────────────

def post_change_check(review: ExecutionReview, probe: dict | None = None, *,
                      attempts: int = 3, delay: float = 0.5,
                      sleep: Callable[[float], None] = time.sleep) -> tuple[bool | None, str]:
    """Re-read local state after the command. (verified, human-readable detail).

    ``True``/``False`` say whether the expected state was observed; ``None`` means
    it could not be determined. A short retry absorbs the moment wg-quick or
    openvpn takes to write its records.
    """
    probe = probe or _default_probe()
    want_up = review.action == "connect"
    if review.protocol == "WireGuard" and review.interface:
        state: dict = {}
        for attempt in range(max(1, attempts)):
            state = probe["wireguard"](review.interface)
            if bool(state.get("up")) == want_up:
                break
            if attempt + 1 < attempts:
                sleep(delay)
        up = bool(state.get("up"))
        device = state.get("device")
        if want_up and not up:
            return False, (f"'{review.interface}' is not recorded as up after the command. "
                           "Run Connection Check before relying on the tunnel.")
        if not want_up and up:
            return False, f"'{review.interface}' is still recorded as up."
        if not want_up:
            return True, f"'{review.interface}' is no longer recorded as up."
        detail = f"'{review.interface}' is up" + (f" on {device}." if device else ".")
        if review.route_mode == "Full tunnel":
            via = probe["route_interface"]()
            if device and via == device:
                detail += f" Public traffic routes via {device}."
            elif via:
                detail += (f" Public traffic still routes via {via}, not the tunnel"
                           + (f" ({device})." if device else "."))
                return False, detail
            else:
                detail += " The route to the internet could not be read."
        detail += " Handshake, DNS and leak behaviour are not verified by this check."
        return True, detail
    if review.protocol == "OpenVPN":
        running = False
        for attempt in range(max(1, attempts)):
            running = bool(probe["openvpn_running"]())
            if running == want_up:
                break
            if attempt + 1 < attempts:
                sleep(delay)
        if running == want_up:
            return True, ("An OpenVPN process is running." if running
                          else "No OpenVPN process is running.")
        return False, ("No OpenVPN process was found after the command." if want_up
                       else "An OpenVPN process is still running.")
    return None, "No post-change check exists for this protocol."


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ── Execution ───────────────────────────────────────────────────────────────

@dataclass
class ExecutionOutcome:
    review: ExecutionReview
    ran: bool
    success: bool
    output: str = ""
    error: str | None = None
    verified: bool | None = None
    verification: str = ""
    checked_at: str = field(default_factory=_now)

    @property
    def status_line(self) -> str:
        if not self.ran:
            return "Refused: " + "; ".join(self.review.blockers)
        if not self.success:
            return f"Failed: {self.error or 'the command reported an error.'}"
        verb = "connect" if self.review.action == "connect" else "disconnect"
        if self.verified is True:
            return f"{self.review.protocol} {verb}: verified locally — {self.verification}"
        if self.verified is False:
            return f"{self.review.protocol} {verb}: command completed but NOT verified — {self.verification}"
        return f"{self.review.protocol} {verb}: command completed; state not verified."

    def sections(self) -> list[tuple[str, str, bool]]:
        if not self.ran:
            result = "Nothing was executed and no administrator access was requested."
        elif self.success:
            result = "The command completed."
        else:
            result = f"The command failed: {self.error or 'no detail'}"
        sections = [("Result", f"{result}\nChecked: {self.checked_at}", False)]
        if self.ran:
            label = {True: "Verified", False: "Not verified", None: "Unknown"}[self.verified]
            sections.append((f"Post-change check — {label}", self.verification, False))
        sections += self.review.sections()
        if self.output:
            sections.append(("Tool output", self.output[:OUTPUT_LIMIT], True))
        sections.append(("Logging", "Tunnel keeps no log of this attempt.", False))
        return sections

    def as_result(self) -> dict:
        """The dict shape the panel and older callers already understand."""
        return {
            "success": self.ran and self.success,
            "protocol": self.review.protocol,
            "action": self.review.action,
            "output": self.output,
            "error": self.error if self.ran else "; ".join(self.review.blockers),
            "verified": self.verified,
            "verification": self.verification,
            "rollback": list(self.review.rollback),
            "status_line": self.status_line,
            "sections": self.sections(),
        }


CHANGED_MESSAGE = ("The config, command or target changed since you confirmed it; "
                   "nothing was run. Review again.")
UNCONFIRMED_MESSAGE = "No confirmed review was supplied; nothing was run. Review again."


def execute(action: str, profile: dict, *,
            confirmed: ExecutionReview | None = None,
            run_as_root: vpn_connection.RunAsRoot | None = None,
            probe: dict | None = None,
            killswitch_armed: Callable[[], bool] | None = None,
            sleep: Callable[[float], None] = time.sleep) -> ExecutionOutcome:
    """Review again, run only if nothing blocks and the fresh review is the one the
    user confirmed, and check afterwards. Logs nothing.

    ``confirmed`` is the review the dialog showed. Without it, or when the
    fresh review's fingerprint differs, nothing runs and no administrator
    prompt appears (fail closed).

    Root copies the config into a root-owned directory and runs the copy only
    if its hash is the confirmed one (audit steps S7 and S8b, ADR 0004).
    OpenVPN also hands root the manifest of data files: relative ones are
    copied and checked, those named by absolute path are checked in place just
    before start."""
    probe = probe or _default_probe()
    review = review_execution(action, profile, probe=probe, killswitch_armed=killswitch_armed)
    kwargs = {"run_as_root": run_as_root} if run_as_root is not None else {}
    if not review.allowed:
        outcome = ExecutionOutcome(review, ran=False, success=False,
                                   error="; ".join(review.blockers))
    elif confirmed is None or review.fingerprint() != confirmed.fingerprint():
        message = UNCONFIRMED_MESSAGE if confirmed is None else CHANGED_MESSAGE
        outcome = ExecutionOutcome(replace(review, blockers=(message,)), ran=False,
                                   success=False, error=message)
    else:
        runner = vpn_connection.connect if review.action == "connect" else vpn_connection.disconnect
        run_profile = profile
        if (review.protocol == "WireGuard" and review.target
                and not vpn_connection.resolve_config_path(profile)
                and os.path.isabs(review.target)):
            # An interface-only profile: run the reviewed file, not a bare name.
            run_profile = {**profile, "config_path": review.target}
        if review.protocol == "WireGuard":
            kwargs["wg_quick"] = review.wg_quick       # the one the review judged
        elif review.action == "connect":
            kwargs.update(includes=review.includes, openvpn=review.program,
                          programs=review.programs)
        result = runner(run_profile, config_sha256=review.config_sha256, **kwargs)
        outcome = ExecutionOutcome(
            review, ran=True, success=bool(result.get("success")),
            output=redact_secrets(str(result.get("output") or ""))[:OUTPUT_LIMIT],
            error=(redact_secrets(str(result.get("error")))
                   if result.get("error") else None),
        )
        outcome.verified, outcome.verification = post_change_check(review, probe, sleep=sleep)

    return outcome
