"""
deploy.py — Applying a rendered configuration to a server.

The installer script is streamed to the target over stdin and never written to
the target's disk. That is not incidental: the script embeds the server's
WireGuard private key and the OpenVPN server key, and a file in /tmp survives
long enough for anything else on the box to read it.

Both paths run the same script. Remote wraps it in ssh; native pipes it to a
local shell. Everything that differs between a VPS and a Raspberry Pi is
already resolved by the time the script is built.
"""

from __future__ import annotations

import platform
import re
import shlex
import shutil
import subprocess
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import bootstrap, obfuscation, paths, provision
from .model import MODE_NATIVE, MODE_REMOTE, Site

OutputCallback = Callable[[str], None]

DEPLOY_TIMEOUT = 600      # apt-get on a cold VPS is genuinely slow
PREFLIGHT_TIMEOUT = 20

SSH_BASE_OPTIONS = [
    "-o", "BatchMode=yes",           # never hang waiting for a password prompt
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
]


_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_IFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,14}$")


def ssh_problems(site: Site) -> list[str]:
    """Reject an SSH target that could be read by ssh as an option or a shell word.

    The target can come from a restored backup, so it is checked rather than
    trusted: a host or user beginning with ``-`` would become an ssh option
    (ProxyCommand runs a program), and the identity file must be a real file.
    """
    ssh = site.ssh
    problems: list[str] = []
    if not _HOST_RE.match(ssh.host or ""):
        problems.append("The SSH host is not a plain host name or address.")
    if not _USER_RE.match(ssh.user or ""):
        problems.append("The SSH user is not a plain account name.")
    if not (1 <= int(ssh.port or 0) <= 65535):
        problems.append("The SSH port is out of range.")
    if ssh.identity_file:
        path = Path(ssh.identity_file).expanduser()
        if str(ssh.identity_file).startswith("-") or not path.is_file():
            problems.append("The SSH key file does not exist.")
    if not _IFACE_RE.match(site.wg_interface or ""):
        problems.append("The WireGuard interface name is not valid.")
    return problems


@dataclass
class DeployResult:
    success: bool
    output: str = ""
    error: str = ""
    command: str = ""
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.success:
            return "Deploy succeeded."
        if self.problems:
            return "Deploy blocked: " + "; ".join(self.problems)
        return self.error or "Deploy failed."


# ── Preflight ────────────────────────────────────


def preflight(site: Site) -> list[str]:
    """Return blocking problems, or an empty list if the site is deployable."""
    problems = site.validate()

    if site.mode == MODE_REMOTE:
        if not shutil.which("ssh"):
            problems.append("ssh not found on this machine.")
        problems.extend(ssh_problems(site))
    elif site.mode == MODE_NATIVE:
        system = platform.system().lower()
        if system not in ("linux", "darwin"):
            problems.append(f"Native deploys are not supported on {platform.system()}.")

    if not any(p.enabled for p in site.peers):
        problems.append(
            "No enabled peers — the server would start with nothing able to connect. "
            "Add a peer first."
        )
    return problems


def check_ssh(site: Site) -> DeployResult:
    """Verify the remote host is reachable and we can act as root there."""
    if not site.ssh.is_configured():
        return DeployResult(False, error="No SSH host configured.")
    bad = ssh_problems(site)
    if bad:
        return DeployResult(False, problems=bad)

    probe = "id -u; uname -s; command -v apt-get >/dev/null && echo has-apt || echo no-apt"
    command = _ssh_command(site) + [probe]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return DeployResult(
            False,
            error=f"Timed out connecting to {site.ssh.destination()}.",
            command=" ".join(command),
        )
    except OSError as exc:
        return DeployResult(False, error=str(exc), command=" ".join(command))

    if proc.returncode != 0:
        return DeployResult(
            False,
            output=proc.stdout,
            error=(proc.stderr.strip() or f"ssh exited {proc.returncode}"),
            command=" ".join(command),
        )

    lines = proc.stdout.split()
    problems: list[str] = []
    if lines and lines[0] != "0" and site.ssh.user != "root":
        problems.append(
            f"Connected as a non-root user ({site.ssh.user}). The installer will "
            "use `sudo -n`, which needs passwordless sudo configured for that user."
        )
    if "no-apt" in proc.stdout:
        problems.append("Remote host has no apt-get — the installer targets Debian/Ubuntu.")

    output = proc.stdout.strip()
    fingerprint = host_fingerprint(site)
    if fingerprint:
        output += f"\nHost key accepted: {fingerprint}"
    return DeployResult(
        success=not problems,
        output=output,
        command=" ".join(command),
        problems=problems,
    )


def host_fingerprint(site: Site) -> str:
    """The fingerprint ssh has on record for this host (local lookup, no network)."""
    host = site.ssh.host if site.ssh.port == 22 else f"[{site.ssh.host}]:{site.ssh.port}"
    try:
        proc = subprocess.run(["ssh-keygen", "-F", host, "-l"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in proc.stdout.splitlines():
        if not line.startswith("#"):
            return line.strip()
    return ""


# ── Deploy ───────────────────────────────────────


def deploy(
    site: Site,
    *,
    dry_run: bool = False,
    on_output: OutputCallback | None = None,
) -> DeployResult:
    """
    Push the current site configuration to its server.

    Safe to call repeatedly — the installer is idempotent, and adding a peer is
    just a redeploy. With dry_run the script is rendered and returned without
    touching anything.
    """
    problems = preflight(site)
    if problems:
        return DeployResult(False, problems=problems)

    script = build_script(site)

    if dry_run:
        return DeployResult(True, output=script, command="(dry run — nothing executed)")

    if site.mode == MODE_REMOTE:
        result = _run_remote(site, script, on_output)
    else:
        result = _run_local(site, script, on_output)

    if result.success:
        # The installer echoes the onion address on a line of its own because
        # it is generated on the server and would otherwise exist only there.
        if site.onion_enabled:
            address = obfuscation.parse_onion_address(result.output)
            if address and address != site.onion_address:
                site.onion_address = address
        provision.mark_deployed(site)
    return result


_B64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def preview_script(site: Site) -> str:
    """The installer as shown to the person: secrets replaced, structure kept.

    Long base64 payloads (config files with keys) become ``<redacted N bytes>``
    and key material outside them is scrubbed, so the preview can be shown,
    logged or screenshotted without leaking the server's keys.
    """
    from services.vpn_execution import redact_secrets
    text = _B64_RUN.sub(lambda m: f"<redacted {len(m.group(0))} bytes>", build_script(site))
    return redact_secrets(text)


def build_script(site: Site) -> str:
    """Render the installer that would be run for this site."""
    if site.mode == MODE_REMOTE:
        target_platform = "linux"
    else:
        target_platform = "darwin" if platform.system() == "Darwin" else "linux"
    return bootstrap.bootstrap_for(site, target_platform)


def _ssh_command(site: Site) -> list[str]:
    command = ["ssh", *SSH_BASE_OPTIONS]
    if site.ssh.port and site.ssh.port != 22:
        command += ["-p", str(site.ssh.port)]
    if site.ssh.identity_file:
        command += ["-i", site.ssh.identity_file, "-o", "IdentitiesOnly=yes"]
    command += ["--", site.ssh.destination()]
    return command


def _run_remote(site: Site, script: str, on_output: OutputCallback | None) -> DeployResult:
    shell = "bash -s" if site.ssh.user == "root" else "sudo -n bash -s"
    command = _ssh_command(site) + [shell]
    return _stream(command, script, on_output)


def _run_local(site: Site, script: str, on_output: OutputCallback | None,
               purpose: str = "set up the VPN server") -> DeployResult:
    """
    Run the installer on this machine, as root.

    Sentinel's gated actions use the macOS administrator dialog and never a
    cached ``sudo`` ticket. The dialog runs one command, not a stdin stream, so
    the installer goes into a private (0600) file inside the state folder, runs
    as ``bash <file>``, and the file is removed afterwards whatever happens: the
    script embeds the server's private keys.
    """
    import os
    import tempfile

    from agents.vpn_agent.services import privileged

    if os.geteuid() == 0:
        return _stream(["bash", "-s"], script, on_output)

    directory = paths.ensure_private_dir(paths.state_dir() / "tmp")
    fd, name = tempfile.mkstemp(prefix="install-", suffix=".sh", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(script)
        os.chmod(path, 0o600)
        ok, output = privileged.run_as_root(
            f"/bin/bash {shlex.quote(str(path))}",
            f"Sentinel needs administrator access to {purpose} on {site.name}.",
            DEPLOY_TIMEOUT,
            allow_cached_sudo=False,
        )
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if on_output:
        for line in output.splitlines():
            on_output(line)
    if ok:
        return DeployResult(True, output=output, command="(installer run through the macOS dialog)")
    return DeployResult(False, output=output, error=_explain_failure(1, output),
                        command="(installer run through the macOS dialog)")


def _stream(
    command: list[str],
    script: str,
    on_output: OutputCallback | None,
) -> DeployResult:
    """
    Run a command, feed it the script on stdin, and collect its output live.

    stdin is written from a helper thread. Writing it inline would deadlock as
    soon as the script outgrew the pipe buffer, because nothing would be
    draining stdout while we blocked on the write.
    """
    printable = " ".join(command)
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return DeployResult(False, error=str(exc), command=printable)

    def feed() -> None:
        try:
            proc.stdin.write(script)
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            # The remote end rejected us before reading the script — the real
            # error will be on stdout, so let the reader report it.
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()

    collected: list[str] = []
    try:
        for line in proc.stdout:
            collected.append(line)
            if on_output:
                on_output(line.rstrip("\n"))
        proc.wait(timeout=DEPLOY_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        return DeployResult(
            False,
            output="".join(collected),
            error=f"Deploy timed out after {DEPLOY_TIMEOUT}s.",
            command=printable,
        )
    finally:
        writer.join(timeout=5)

    output = "".join(collected)
    if proc.returncode == 0:
        return DeployResult(True, output=output, command=printable)

    return DeployResult(
        False,
        output=output,
        error=_explain_failure(proc.returncode, output),
        command=printable,
    )


def _explain_failure(returncode: int, output: str) -> str:
    """Translate the common failure modes into something actionable."""
    lowered = output.lower()

    if "permission denied (publickey" in lowered:
        return (
            "SSH rejected the key. Add your public key to the server's "
            "~/.ssh/authorized_keys, or set an identity file on the site."
        )
    if "could not resolve hostname" in lowered:
        return "Could not resolve the host name. Check the SSH host on the site."
    if "connection refused" in lowered:
        return "Connection refused — is sshd running, and is the port right?"
    if "sudo: a password is required" in lowered or "sudo: a terminal is required" in lowered:
        return (
            "sudo needs a password. Connect as root, or configure passwordless "
            "sudo for this user on the target."
        )
    if "host key verification failed" in lowered:
        return (
            "Host key verification failed — the server's key changed since last "
            "time. Verify why, then remove the stale entry from ~/.ssh/known_hosts."
        )
    if "unable to locate package" in lowered:
        return "A package was not found. Run `apt-get update` on the target and retry."
    if returncode == 255:
        return "SSH failed to connect. Check host, port, user and network."
    return f"Installer exited with status {returncode}. See the output above."


# ── Teardown ─────────────────────────────────────


def build_teardown_script(site: Site) -> str:
    """
    Render a script that removes everything this tool installed.

    Stops and disables both services, removes the NAT unit and its rules, and
    deletes the configs. Packages are left installed — removing them could take
    out something else on the box that depends on them.

    A native macOS install is a different animal — pf and Homebrew rather than
    systemd and apt — so it gets its own script.
    """
    if site.mode == MODE_NATIVE and platform.system() == "Darwin":
        return bootstrap.macos_teardown_script(site)

    ovpn = ""
    if site.enable_openvpn:
        ovpn = """
systemctl disable --now openvpn-server@server.service 2>/dev/null || true
rm -rf /etc/openvpn/server/ca.crt /etc/openvpn/server/server.crt \\
       /etc/openvpn/server/server.key /etc/openvpn/server/tls-crypt.key \\
       /etc/openvpn/server/server.conf
echo "[vpn-agent] OpenVPN removed."
"""

    return f"""#!/usr/bin/env bash
# Remove the {site.name} VPN server. Generated by VPN Agent.
set -uo pipefail

[ "$(id -u)" -eq 0 ] || {{ echo "Must run as root." >&2; exit 1; }}

systemctl disable --now wg-quick@{site.wg_interface}.service 2>/dev/null || true
rm -f /etc/wireguard/{site.wg_interface}.conf
echo "[vpn-agent] WireGuard removed."
{ovpn}
systemctl disable --now vpn-agent-nat.service 2>/dev/null || true
[ -x {bootstrap.NAT_HELPER_PATH} ] && {bootstrap.NAT_HELPER_PATH} down 2>/dev/null || true
rm -f {bootstrap.NAT_UNIT_PATH} {bootstrap.NAT_HELPER_PATH} {bootstrap.SYSCTL_PATH}
systemctl daemon-reload
echo "[vpn-agent] NAT rules and forwarding removed."

echo "[vpn-agent] Teardown complete. Packages were left installed."
"""


def teardown(site: Site, *, on_output: OutputCallback | None = None) -> DeployResult:
    """
    Remove the VPN server from its host. Does not delete local site state.

    Native installs tear down locally; remote ones over SSH. The macOS path cuts
    our block out of /etc/pf.conf by marker rather than rewriting the file, so
    Apple's own anchors survive untouched.
    """
    script = build_teardown_script(site)

    if site.mode == MODE_REMOTE:
        bad = ssh_problems(site)
        if bad:
            return DeployResult(False, problems=bad)
        command = _ssh_command(site) + (
            ["bash -s"] if site.ssh.user == "root" else ["sudo -n bash -s"]
        )
        return _stream(command, script, on_output)

    system = platform.system()
    if system not in ("Darwin", "Linux"):
        return DeployResult(False, problems=[f"Native teardown is not supported on {system}."])

    return _run_local(site, script, on_output, purpose="remove the VPN server")


# ── Live status ──────────────────────────────────


@dataclass
class PeerStatus:
    """What the server currently knows about one peer."""

    public_key: str
    name: str = ""                      # filled in by matching against the site
    endpoint: str = ""
    allowed_ips: str = ""
    last_handshake: int = 0             # unix seconds; 0 means never
    rx_bytes: int = 0
    tx_bytes: int = 0

    @property
    def connected(self) -> bool:
        """
        WireGuard is connectionless, so "connected" is really "handshaked
        recently". A peer rekeys every two minutes while active, so a handshake
        older than about three minutes means the device has gone away.
        """
        return 0 < self.seconds_since_handshake() < 190

    def seconds_since_handshake(self) -> int:
        if not self.last_handshake:
            return 0
        return max(0, int(time.time()) - self.last_handshake)

    def describe_handshake(self) -> str:
        if not self.last_handshake:
            return "never"
        seconds = self.seconds_since_handshake()
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"

    def describe_transfer(self) -> str:
        return f"{_human_bytes(self.rx_bytes)} in / {_human_bytes(self.tx_bytes)} out"


@dataclass
class ServerStatus:
    reachable: bool = False
    wg_active: bool = False
    ovpn_active: bool = False
    uptime: str = ""
    peers: list[PeerStatus] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if not self.reachable:
            return self.error or "Server unreachable."
        parts = [f"WireGuard {'up' if self.wg_active else 'DOWN'}"]
        if self.ovpn_active:
            parts.append("OpenVPN up")
        live = sum(1 for p in self.peers if p.connected)
        parts.append(f"{live}/{len(self.peers)} device(s) connected")
        if self.uptime:
            parts.append(self.uptime)
        return " · ".join(parts)


def _human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def server_status(site: Site) -> ServerStatus:
    """
    Ask the server what it is actually doing.

    Deploying tells you the configuration was written; this tells you which
    devices have since handshaked and how much they have moved. The two are
    different questions and only this one answers "is my phone still connected".
    """
    if site.mode != MODE_REMOTE:
        return ServerStatus(error="Live status is available for remote servers only.")
    if not site.ssh.is_configured():
        return ServerStatus(error="No SSH host configured.")
    bad = ssh_problems(site)
    if bad:
        return ServerStatus(error="; ".join(bad))

    probe = (
        f"wg show {site.wg_interface} dump 2>/dev/null; "
        "echo '--MARK--'; "
        "systemctl is-active openvpn-server@server 2>/dev/null || true; "
        "echo '--MARK--'; "
        "uptime -p 2>/dev/null || true"
    )
    command = _ssh_command(site) + [probe]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=PREFLIGHT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ServerStatus(error=f"Timed out talking to {site.ssh.destination()}.")
    except OSError as exc:
        return ServerStatus(error=str(exc))

    if proc.returncode != 0:
        return ServerStatus(error=_explain_failure(proc.returncode, proc.stdout + proc.stderr))

    wg_text, ovpn_text, uptime_text = (proc.stdout.split("--MARK--") + ["", "", ""])[:3]
    status = parse_wg_dump(wg_text)
    status.reachable = True
    status.ovpn_active = ovpn_text.strip() == "active"
    status.uptime = uptime_text.strip()

    # Attach the names the user actually recognises.
    by_key = {p.wg_public_key: p.name for p in site.peers}
    for peer in status.peers:
        peer.name = by_key.get(peer.public_key, "(unknown peer)")

    return status


def parse_wg_dump(text: str) -> ServerStatus:
    """
    Parse `wg show <iface> dump`.

    The first line describes the interface and its SECOND field is the server's
    private key. It is dropped here and never stored or returned — a status
    display has no use for it, and anything that holds it can impersonate the
    server.

    Peer lines are: public_key, preshared_key, endpoint, allowed_ips,
    latest_handshake, rx, tx, keepalive.
    """
    status = ServerStatus()
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return status

    status.wg_active = True
    for line in lines[1:]:                       # line 0 is the interface
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        status.peers.append(
            PeerStatus(
                public_key=fields[0],
                # fields[1] is the pre-shared key — deliberately not kept.
                endpoint="" if fields[2] == "(none)" else fields[2],
                allowed_ips=fields[3],
                last_handshake=int(fields[4]) if fields[4].isdigit() else 0,
                rx_bytes=int(fields[5]) if fields[5].isdigit() else 0,
                tx_bytes=int(fields[6]) if fields[6].isdigit() else 0,
            )
        )
    return status
