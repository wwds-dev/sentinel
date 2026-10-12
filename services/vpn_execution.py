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
the worker is still caught.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agents.vpn_agent.services.config_inspection import (
    inspect_wireguard_text as inspect_config_text,
)
from services import openvpn_manager, vpn_apps, vpn_connection

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
OPENVPN_HOOK_KEYS = ("up", "down", "route-up", "route-pre-down", "ipchange",
                     "client-connect", "client-disconnect", "learn-address",
                     "tls-verify", "auth-user-pass-verify", "plugin",
                     "script-security")
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
# local control socket (`management*`), or make root write or change directory
# somewhere the review cannot see. Blockers, not warnings.
OPENVPN_BLOCKER_KEYS = ("config", "log", "log-append", "writepid", "status",
                        "tmp-dir", "cd", "chroot", "setenv")
# Directives whose value is a program or plugin that runs as root.
OPENVPN_EXECUTABLE_KEYS = ("up", "down", "route-up", "route-pre-down", "ipchange",
                           "client-connect", "client-disconnect", "learn-address",
                           "tls-verify", "auth-user-pass-verify", "plugin")
# The reviewed client directive list. Anything else is still allowed (a real
# profile must not break) but is named in one warning.
OPENVPN_KNOWN_KEYS = frozenset(OPENVPN_HOOK_KEYS) | frozenset(OPENVPN_BLOCKER_KEYS) | frozenset((
    "client", "dev", "dev-type", "dev-node", "proto", "remote", "remote-random",
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
    "ignore-unknown-option", "engine", "inactive", "replay-window", "reneg-bytes",
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


def _config_directives(text: str, protocol: str):
    """(raw key, lowercased key, value) for each directive line. OpenVPN inline
    blocks (<ca>…</ca>) are data, not directives, so their content is skipped."""
    block = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if protocol == "WireGuard":
            key, sep, value = line.partition("=")
            if sep:
                yield key.strip(), key.strip().lower(), value.strip()
            continue
        if block:
            if line.lower() == f"</{block}>":
                block = ""
            continue
        if line.startswith("<") and line.endswith(">") and not line.startswith("</"):
            block = line[1:-1].strip().lower()
            continue
        parts = line.split(None, 1)
        yield parts[0], parts[0].lstrip("-").lower(), (parts[1].strip() if len(parts) > 1 else "")


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


def _unsafe_executable_reason(path: Path) -> str | None:
    """Why a root-run program could be replaced by a normal user, or None.
    Root must own the file and every directory above it, none of them group- or
    world-writable (a sticky world-writable directory such as /tmp still lets a
    user replace nothing they do not own, but is not a place to keep a hook)."""
    for candidate in (path, *path.parents):
        try:
            info = _stat(candidate)
        except OSError:
            continue                              # not there yet: judged by what exists
        if info.st_uid != 0:
            return f"{candidate} is not owned by root"
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{candidate} is writable by group or others"
    return None


def _hook_executable(key: str, value: str, protocol: str) -> Path | None:
    """The absolute program a hook line names, if it names one."""
    if protocol == "OpenVPN" and key not in OPENVPN_EXECUTABLE_KEYS:
        return None
    try:
        words = shlex.split(value)
    except ValueError:
        words = value.split()
    if words and words[0].startswith("/"):
        return Path(words[0])
    return None


def _hook_findings(text: str, protocol: str) -> tuple[list[str], list[str]]:
    """(blockers, warnings) for hook programs and OpenVPN directives."""
    blockers: list[str] = []
    warnings: list[str] = []
    unknown: list[str] = []
    for raw, key, value in _config_directives(text, protocol):
        hook_keys = ({k.lower() for k in WG_HOOK_KEYS} if protocol == "WireGuard"
                     else set(OPENVPN_HOOK_KEYS))
        if len(raw) + len(value) > HOOK_LINE_LIMIT and key in hook_keys:
            blockers.append(f"The {raw} line is longer than {HOOK_LINE_LIMIT} characters; "
                            "Tunnel will not run what a person cannot review.")
        if protocol == "OpenVPN":
            if key in OPENVPN_BLOCKER_KEYS or key.startswith("management"):
                blockers.append(f"The OpenVPN directive '{key}' can hide commands or redirect "
                                "what administrator rights write; remove it from the profile.")
            elif key not in OPENVPN_KNOWN_KEYS and key not in unknown:
                unknown.append(key)
        if key in hook_keys:
            program = _hook_executable(key, value, protocol)
            if program is not None:
                reason = _unsafe_executable_reason(program)
                if reason:
                    blockers.append(f"The {raw} hook runs {program} as administrator, and "
                                    f"{reason}, so a normal user could replace it.")
                elif not program.exists():
                    warnings.append(f"The {raw} hook names {program}, which does not exist.")
    if unknown:
        warnings.append("OpenVPN directives not on the reviewed list (allowed, but Tunnel "
                        "has not checked what they do): " + ", ".join(unknown[:12])
                        + (" …" if len(unknown) > 12 else ""))
    return blockers, warnings


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

    @property
    def allowed(self) -> bool:
        return not self.blockers

    def fingerprint(self) -> str:
        """Identity of what administrator rights would run: action, protocol,
        target, interface, command, every hook in full, and the hash of the
        config bytes. Warnings and blockers are left out on purpose (probe
        state may legitimately change between the dialog and the worker)."""
        payload = json.dumps([self.action, self.protocol, self.target, self.interface,
                              self.command, list(self.root_hooks), self.config_sha256],
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
            lines += ["", f"Config SHA-256: {self.config_sha256}"]
        return "\n".join(lines)

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
            lines += ["", "RUNS COMMANDS AS ADMINISTRATOR — this config executes:"]
            lines += [f"• {self._shown_hook(item)}" for item in self.root_hooks]
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
            head.append(f"Config SHA-256: {self.config_sha256}")
        sections: list[tuple[str, str, bool]] = [
            ("Target review", "\n".join(head), True),
        ]
        if self.command:
            sections.append(("Command (runs as administrator)", self.command, True))
        if self.config_lines:
            sections.append(("Configuration intent (keys discarded)",
                             "\n".join(self.config_lines), True))
        if self.root_hooks:
            sections.append(("Runs commands as administrator",
                             "\n".join(f"• {item}" for item in self.root_hooks), True))
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
    route_mode = ""
    config_sha256 = ""
    ks_armed = killswitch_armed()

    if action == "connect" and vpn_connection.is_placeholder(profile):
        blockers.append("This profile is an example/template, not a real server. "
                        "Import a WireGuard .conf or OpenVPN .ovpn first.")

    if protocol == "WireGuard":
        target, interface = _wg_interface_for(profile)
        command = (vpn_connection.wg_quick_command("up" if action == "connect" else "down", target)
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
        config_path = vpn_connection.resolve_config_path(profile)
        if config_path:
            source = Path(config_path).expanduser()
            # Both actions read the file: wg-quick down re-reads it and runs
            # PreDown/PostDown, so a disconnect is reviewed like a connect.
            text, config_sha256, read_blockers, read_warnings = _read_config(source)
            warnings.extend(read_warnings)
            if text is None:
                if action == "connect" or source.exists():
                    blockers.extend(read_blockers)
            else:
                root_hooks = hooks_from_text(text, "WireGuard")
                hook_blockers, hook_warnings = _hook_findings(text, "WireGuard")
                blockers.extend(hook_blockers)
                warnings.extend(hook_warnings)
                if action == "connect":
                    summary = inspect_config_text(text, source)
                    route_mode = summary.route_mode
                    config_lines = [
                        f"Routing: {summary.route_mode}",
                        "Endpoints: " + (", ".join(summary.endpoints) or "Not specified"),
                        "DNS: " + (", ".join(summary.dns_servers) or "Not specified — system DNS stays in use"),
                        f"Peers: {summary.peer_count}",
                    ]
                    warnings.extend(summary.warnings)
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
        elif (not config_path and action == "connect" and interface
              and WG_INTERFACE_NAME.fullmatch(interface)
              and not any((Path(d) / f"{interface}.conf").is_file() for d in WG_CONFIG_DIRS)):
            blockers.append(
                f"No config for '{interface}' was found in {', '.join(WG_CONFIG_DIRS)}. "
                "wg-quick would fail; import the .conf file instead.")
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
        command = (f"openvpn --config {vpn_connection._quote(target)} --daemon sentinel-ovpn …"
                   if action == "connect" else "kill -TERM <pid of Sentinel's tracked openvpn>")
        if action == "connect":
            if not openvpn_manager.is_openvpn_available():
                blockers.append("openvpn client not found — install it, e.g. "
                                "`brew install openvpn`.")
            if not target:
                blockers.append("This OpenVPN profile has no config file.")
            else:
                text, config_sha256, read_blockers, read_warnings = _read_config(
                    Path(target).expanduser())
                warnings.extend(read_warnings)
                blockers.extend(read_blockers)
                if text is not None:
                    root_hooks = hooks_from_text(text, "OpenVPN")
                    hook_blockers, hook_warnings = _hook_findings(text, "OpenVPN")
                    blockers.extend(hook_blockers)
                    warnings.extend(hook_warnings)
            if probe["openvpn_running"]():
                blockers.append("An OpenVPN process is already running — Disconnect it first.")
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
    prompt appears (fail closed)."""
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
        result = runner(profile, **kwargs)
        outcome = ExecutionOutcome(
            review, ran=True, success=bool(result.get("success")),
            output=redact_secrets(str(result.get("output") or ""))[:OUTPUT_LIMIT],
            error=(redact_secrets(str(result.get("error")))
                   if result.get("error") else None),
        )
        outcome.verified, outcome.verification = post_change_check(review, probe, sleep=sleep)

    return outcome
