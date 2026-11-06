"""Real VPN connect/disconnect controller — WireGuard + OpenVPN.

Every privileged call is injected, so these tests never run wg-quick, openvpn,
pfctl, sudo, or osascript. They assert the controller builds the right commands,
refuses example/placeholder profiles instead of pretending, and maps tool
results honestly.
"""

from __future__ import annotations

import os
import shlex
import types

import pytest

from services import openvpn_manager, vpn_connection, vpn_rootscript

FAKE_WG_QUICK = "/opt/test/bin/wg-quick"
SHA = "a" * 64
H = "b" * 64
G = "c" * 64


class Recorder:
    """Fake run_as_root that records scripts and returns a canned result."""
    def __init__(self, ok=True, output="ok"):
        self.ok, self.output, self.scripts = ok, output, []

    def __call__(self, script, prompt, timeout=30):
        self.scripts.append(script)
        return self.ok, self.output


@pytest.fixture(autouse=True)
def fixed_wg_quick(monkeypatch):
    """Pin the command shape: without this the expected strings depend on
    whether (and where) this Mac has wireguard-tools installed."""
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: FAKE_WG_QUICK if name == "wg-quick" else real_which(name, *a, **k))


# ── Placeholder honesty (the owner's core fear applied to the new feature) ──

def test_placeholder_profiles_are_detected():
    assert vpn_connection.is_placeholder({"placeholder": True, "endpoint": "1.2.3.4"})
    assert vpn_connection.is_placeholder({"endpoint": "<SERVER_IP>"})
    assert vpn_connection.is_placeholder({"endpoint": "0.0.0.0"})
    assert not vpn_connection.is_placeholder({"endpoint": "203.0.113.7"})


def test_connect_refuses_a_placeholder_without_running_anything():
    run = Recorder()
    profile = {"name": "Example — Japan", "protocol": "WireGuard",
               "endpoint": "<SERVER_IP>", "interface": "wgjp", "placeholder": True}
    result = vpn_connection.connect(profile, run_as_root=run)
    assert result["success"] is False
    assert "example" in result["error"].lower() or "template" in result["error"].lower()
    assert run.scripts == []          # nothing was executed


def test_example_country_profiles_are_all_non_connectable():
    run = Recorder()
    for profile in vpn_connection.example_country_profiles():
        assert vpn_connection.is_placeholder(profile)
        assert vpn_connection.connect(profile, run_as_root=run)["success"] is False
    assert run.scripts == []


# ── WireGuard ───────────────────────────────────────────────────────────────

def test_wireguard_connect_runs_the_root_script_on_a_verified_copy(monkeypatch):
    """S7: root gets the constant script, the user's file and the confirmed hash;
    it never runs ``wg-quick up <user file>``."""
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder(ok=True, output="[#] wg-quick up wg0")
    profile = {"name": "Imported", "protocol": "WireGuard", "endpoint": "203.0.113.7",
               "interface": "wg0", "config_path": "/Users/me/nl.conf"}
    result = vpn_connection.connect(profile, run_as_root=run, config_sha256=SHA)
    assert result["success"] is True
    assert result["protocol"] == "WireGuard"
    assert run.scripts == [vpn_rootscript.wireguard_command(
        "up", "/Users/me/nl.conf", SHA, "nl", FAKE_WG_QUICK)]


@pytest.mark.parametrize("profile, sha", [
    ({"interface": "wg0"}, SHA),                                  # bare name: no file to copy
    ({"interface": "wg0", "config_path": "/Users/me/nl.conf"}, ""),  # no confirmed hash
    ({"config_path": "/Users/me/not-a-valid-interface-name.conf"}, SHA),
])
def test_wireguard_connect_without_a_verifiable_file_runs_nothing(monkeypatch, profile, sha):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder()
    result = vpn_connection.connect({"protocol": "WireGuard", "endpoint": "203.0.113.7",
                                     **profile}, run_as_root=run, config_sha256=sha)
    assert result["success"] is False
    assert result["error"].startswith("Nothing was run")
    assert run.scripts == []


def test_wireguard_connect_refuses_without_an_absolute_wg_quick(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k: None)
    run = Recorder()
    result = vpn_connection.connect(
        {"protocol": "WireGuard", "endpoint": "203.0.113.7", "config_path": "/Users/me/nl.conf"},
        run_as_root=run, config_sha256=SHA)
    assert result["success"] is False and run.scripts == []


def test_wireguard_connect_reports_tool_failure(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder(ok=False, output="wg-quick: `wg0' already exists")
    result = vpn_connection.connect(
        {"protocol": "WireGuard", "endpoint": "203.0.113.7", "config_path": "/Users/me/wg0.conf"},
        run_as_root=run, config_sha256=SHA)
    assert result["success"] is False
    assert "already exists" in result["error"]


def test_wireguard_missing_tool_is_reported(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: False)
    run = Recorder()
    result = vpn_connection.connect(
        {"protocol": "WireGuard", "endpoint": "203.0.113.7", "interface": "wg0"},
        run_as_root=run)
    assert result["success"] is False
    assert "wg-quick" in result["error"]
    assert run.scripts == []


@pytest.mark.parametrize("profile, sha, source, sent_sha", [
    # The confirmed file and hash travel with it, for the no-stored-copy case.
    ({"interface": "wg0", "config_path": "/Users/me/wg0.conf"}, SHA, "/Users/me/wg0.conf", SHA),
    # Nothing verifiable: root may only use the copy kept since Connect.
    ({"interface": "wg0"}, "", "", ""),
    ({"interface": "wg0", "config_path": "/Users/me/wg0.conf"}, "", "", ""),
])
def test_wireguard_disconnect_runs_the_root_script(monkeypatch, profile, sha, source, sent_sha):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder()
    vpn_connection.disconnect({"protocol": "WireGuard", "endpoint": "203.0.113.7", **profile},
                              run_as_root=run, config_sha256=sha)
    assert run.scripts == [vpn_rootscript.wireguard_command(
        "down", source, sent_sha, "wg0", FAKE_WG_QUICK)]


# ── OpenVPN ─────────────────────────────────────────────────────────────────

def test_openvpn_connect_requires_a_config_path():
    run = Recorder()
    result = vpn_connection.connect(
        {"name": "OVPN", "protocol": "OpenVPN", "endpoint": "203.0.113.7"},
        run_as_root=run)
    assert result["success"] is False
    assert "config" in result["error"].lower()
    assert run.scripts == []


def ovpn_root_argv(script: str) -> list[str]:
    """The values a dialog-path OpenVPN script hands the constant root script."""
    parts = shlex.split(script)
    assert parts[:4] == ["/usr/bin/env", "-i", "/bin/bash", "-c"]
    assert parts[4] == vpn_rootscript.OVPN_SCRIPT
    return parts[5:]


class ArgvRecorder(Recorder):
    """Recorder whose ``calls`` are the root argv each script line carries."""
    @property
    def calls(self):
        return [shlex.split(script) for script in self.scripts]


@pytest.fixture
def root_files(tmp_path, monkeypatch):
    """Root's run dir as Sentinel reads it, with files that count as root-owned."""
    base = tmp_path / "run"
    (base / "ovpn").mkdir(parents=True)
    monkeypatch.setattr(openvpn_manager.vpn_rootscript, "_READ_BASE", str(base))
    monkeypatch.setattr(openvpn_manager.vpn_rootscript, "_OWNER_UID", os.getuid())
    return base / "ovpn"


def test_openvpn_connect_launches_daemon(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\nremote 203.0.113.7 1194\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    run = Recorder(ok=True, output="")
    result = vpn_connection.connect(
        {"name": "JP", "protocol": "OpenVPN", "endpoint": "203.0.113.7",
         "config_path": str(cfg)},
        run_as_root=run, config_sha256=H, includes=(("certs/ca.crt", G),),
        openvpn="/opt/homebrew/sbin/openvpn")
    assert result["success"] is True
    assert result["protocol"] == "OpenVPN"
    assert len(run.scripts) == 1
    # S8a: one constant root script; pid, device and log under root's run dir.
    # S8b: the confirmed hash and the manifest follow, for root to check its copies.
    assert ovpn_root_argv(run.scripts[0]) == [
        "sentinel-ovpn", "start", "up", str(cfg), H, "/opt/homebrew/sbin/openvpn",
        vpn_rootscript.BASE_DIR, vpn_rootscript.ROOT_OWNER, "", "",
        str(vpn_rootscript.OVPN_START_WAIT), "1", "certs/ca.crt", G]


@pytest.mark.parametrize("sha256, includes", [
    ("", ()), ("A" * 64, ()), (H, (("../ca.crt", H),)),
    (H, tuple((f"f{i}", H) for i in range(17))),
])
def test_openvpn_connect_without_a_valid_hash_or_manifest_runs_nothing(monkeypatch, tmp_path,
                                                                       sha256, includes):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    run = ArgvRecorder()
    result = openvpn_manager.connect(str(cfg), config_sha256=sha256, includes=includes,
                                     openvpn="/opt/homebrew/sbin/openvpn", run_as_root=run)
    assert result["success"] is False and "Nothing was run" in result["error"]
    assert run.calls == []


def test_openvpn_connect_refuses_when_already_running(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "is_openvpn_available", lambda: True)
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: True)
    run = ArgvRecorder()
    result = openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=run)
    assert result["success"] is False
    assert "already running" in result["error"].lower()
    assert run.calls == []


def test_openvpn_connect_reports_missing_binary(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "is_openvpn_available", lambda: False)
    result = openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=ArgvRecorder())
    assert result["success"] is False
    assert "openvpn" in result["error"].lower()


def test_openvpn_connect_reads_the_device_root_recorded(monkeypatch, tmp_path, root_files):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)

    def run(script, prompt, timeout=30):
        (root_files / "device").write_text("utun7")
        return True, ""
    assert openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=run)["device"] == "utun7"


def test_openvpn_connect_ignores_a_root_device_that_is_not_a_tunnel(monkeypatch, tmp_path, root_files):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    (root_files / "device").write_text("en0")
    result = openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=ArgvRecorder(output=""))
    assert result["success"] and result["device"] is None


def test_openvpn_connect_ignores_a_device_file_root_does_not_own(monkeypatch, tmp_path, root_files):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    monkeypatch.setattr(openvpn_manager.vpn_rootscript, "_OWNER_UID", 0)
    (root_files / "device").write_text("utun7")
    result = openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=ArgvRecorder(output=""))
    assert result["success"] and result["device"] is None


def test_openvpn_connect_reports_the_root_scripts_failure(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    run = ArgvRecorder(ok=False, output="OpenVPN start-up output:\nOptions error: bad")
    result = openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=run)
    assert result["success"] is False and "Options error" in result["error"]
    assert result["device"] is None


def test_openvpn_connect_with_no_binary_path_runs_nothing(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "is_openvpn_available", lambda: True)
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    run = ArgvRecorder()
    result = openvpn_manager.connect(str(cfg), config_sha256=H, run_as_root=run)
    assert result["success"] is False
    assert result["error"] == "Nothing was run: no reviewed openvpn program."
    assert run.calls == []


@pytest.mark.parametrize("pid", [None, -1, 0, 1])
def test_openvpn_disconnect_refuses_unverified_process(monkeypatch, pid):
    monkeypatch.setattr(openvpn_manager, "_read_pid", lambda: pid)
    monkeypatch.setattr(openvpn_manager, "_read_legacy_pid", lambda: pid)
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p, f=None: False)
    run = ArgvRecorder()
    result = openvpn_manager.disconnect(run_as_root=run)
    assert result["success"] is False
    assert run.calls == []


def test_disconnect_after_openvpn_exited_lets_root_remove_its_copies(monkeypatch):
    """Code review P3-1: root still records a pid but no Sentinel openvpn holds
    it (auth failure, ping-exit). Root re-checks, signals nothing, and removes
    the key copies; the result still says nothing was stopped."""
    monkeypatch.setattr(openvpn_manager, "_read_pid", lambda: 12345)
    monkeypatch.setattr(openvpn_manager, "_read_legacy_pid", lambda: None)
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p, f=None: False)
    run = ArgvRecorder(ok=False, output="refused: process 12345 is not the OpenVPN tunnel")
    result = openvpn_manager.disconnect(run_as_root=run)
    assert result["success"] is False and "Nothing was stopped" in result["error"]
    assert run.calls == [vpn_rootscript.openvpn_argv("down", pid=12345)]


@pytest.mark.parametrize("ok", [True, False])
def test_openvpn_disconnect_preserves_tracking_and_reports_signal_failure(monkeypatch, root_files, ok):
    pidfile = root_files / "openvpn.pid"
    pidfile.write_text("12345\n")
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p, f=None: p == 12345 and f is None)
    run = ArgvRecorder(ok=ok, output="signal result")
    result = openvpn_manager.disconnect(run_as_root=run)
    assert result["success"] is ok
    assert run.calls == [vpn_rootscript.openvpn_argv("down", pid=12345)]
    assert run.calls[0][-5:] == [vpn_rootscript.ROOT_OWNER, "12345", "",
                                 str(vpn_rootscript.OVPN_STOP_WAIT), "0"]
    assert pidfile.exists()


def test_a_pid_file_that_is_not_a_regular_file_is_not_believed(root_files, tmp_path):
    real = tmp_path / "elsewhere.pid"
    real.write_text("12345")
    (root_files / "openvpn.pid").symlink_to(real)
    assert openvpn_manager._read_pid() is None
    (root_files / "device").mkdir()
    assert openvpn_manager._root_device() is None


def test_a_pid_file_root_does_not_own_is_not_believed(monkeypatch, root_files):
    (root_files / "openvpn.pid").write_text("12345")
    monkeypatch.setattr(openvpn_manager.vpn_rootscript, "_OWNER_UID", 0)
    assert openvpn_manager._read_pid() is None


@pytest.mark.parametrize("text, expected", [
    ("12345\n", 12345), (" 77 ", 77), ("", None), ("12a", None), ("-5", None),
    ("1" * 11, None),
])
def test_root_pid_file_parsing(root_files, text, expected):
    (root_files / "openvpn.pid").write_text(text)
    assert openvpn_manager._read_pid() == expected


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """An older Sentinel's user-side files, with process 4242 as its openvpn."""
    folder = tmp_path / "vpn"
    folder.mkdir()
    (folder / "openvpn.pid").write_text("4242")
    (folder / "openvpn.device").write_text("utun9")
    (folder / "openvpn.log").write_text("TLS: Initial packet from [AF_INET]203.0.113.7:1194\n")
    state = {"alive": True}
    monkeypatch.setattr(openvpn_manager, "_legacy_dir", lambda: folder)
    monkeypatch.setattr(openvpn_manager, "_read_pid", lambda: None)
    monkeypatch.setattr(openvpn_manager, "_legacy_pid_is_its_own", lambda pid: pid == 4242)
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process",
                        lambda p, f=None: state["alive"] and p == 4242 and f == folder / "openvpn.pid")
    monkeypatch.setattr(openvpn_manager, "_legacy_is_gone", lambda: not state["alive"])
    return types.SimpleNamespace(folder=folder, state=state)


def test_a_tunnel_an_older_sentinel_started_is_still_tracked_and_stopped(legacy):
    assert openvpn_manager.is_running() is True
    assert openvpn_manager.tunnel_device() == "utun9"
    calls = []

    def run(script, prompt, timeout=30):
        calls.append(script)
        legacy.state["alive"] = False
        return True, "OpenVPN stopped."
    result = openvpn_manager.disconnect(run_as_root=run)
    assert result["success"] is True
    assert [shlex.split(c) for c in calls] == [vpn_rootscript.openvpn_argv(
        "down", pid=4242, legacy_pid_file=str(legacy.folder / "openvpn.pid"))]
    # Once it has exited, nothing of it stays in the user folder (S8a review P2-1).
    assert list(legacy.folder.iterdir()) == []


def test_an_older_sentinels_tunnel_still_running_keeps_its_device(legacy):
    # S8a review P3-3: "signal sent; still running" must not lose the device.
    result = openvpn_manager.disconnect(
        run_as_root=lambda script, prompt, timeout=30: (True, "Signal sent; OpenVPN is still running."))
    assert result["success"] is True
    assert openvpn_manager.tunnel_device() == "utun9"


def test_a_legacy_pid_written_after_its_process_started_is_not_believed(legacy, monkeypatch):
    # S8a review P3-2: the user-side file can be rewritten to name another openvpn.
    monkeypatch.setattr(openvpn_manager, "_legacy_pid_is_its_own", lambda pid: False)
    assert openvpn_manager.is_running() is False
    run = ArgvRecorder()
    assert openvpn_manager.disconnect(run_as_root=run)["success"] is False
    assert run.calls == []


def test_an_older_sentinels_log_is_deleted_once_its_tunnel_is_gone(legacy):
    # S8a review P2-1: that log can name the server. A status read never deletes
    # (code review P2-2: a ps timeout is not proof); Disconnect does, once the
    # process has positively gone.
    legacy.state["alive"] = False
    assert openvpn_manager.is_running() is False
    assert len(list(legacy.folder.iterdir())) == 3
    run = ArgvRecorder()
    assert openvpn_manager.disconnect(run_as_root=run)["success"] is False
    assert run.calls == [] and list(legacy.folder.iterdir()) == []


@pytest.fixture
def legacy_files(tmp_path, monkeypatch):
    """An older Sentinel's three files for pid 4242, with the real gone-check."""
    folder = tmp_path / "vpn"
    folder.mkdir()
    for name, text in (("openvpn.pid", "4242"), ("openvpn.device", "utun9"),
                       ("openvpn.log", "TLS: Initial packet\n")):
        (folder / name).write_text(text)
    monkeypatch.setattr(openvpn_manager, "_legacy_dir", lambda: folder)
    return folder


def test_a_failed_status_check_keeps_an_older_sentinels_files(legacy_files, monkeypatch):
    # Code review P2-2: one ps that times out under load must not drop the
    # files that track a running tunnel.
    from types import SimpleNamespace
    monkeypatch.setattr(openvpn_manager.os, "kill", lambda pid, sig: None)   # alive

    def timeout(*a, **k):
        raise openvpn_manager.subprocess.TimeoutExpired("ps", 3)
    monkeypatch.setattr(openvpn_manager.subprocess, "run", timeout)
    assert openvpn_manager._legacy_is_gone() is False
    openvpn_manager._sweep_legacy()
    assert len(list(legacy_files.iterdir())) == 3
    monkeypatch.setattr(openvpn_manager.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="/usr/bin/some-other-program\n"))        # pid reused
    assert openvpn_manager._legacy_is_gone() is True
    openvpn_manager._sweep_legacy()
    assert list(legacy_files.iterdir()) == []


def test_a_vanished_legacy_process_is_gone(legacy_files, monkeypatch):
    def gone(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(openvpn_manager.os, "kill", gone)
    assert openvpn_manager._legacy_is_gone() is True


def test_connect_deletes_an_older_sentinels_leftover_log(monkeypatch, tmp_path, legacy):
    legacy.state["alive"] = False
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    openvpn_manager.connect(str(cfg), config_sha256=H, openvpn=openvpn_manager.openvpn_binary() or "", run_as_root=ArgvRecorder(output=""))
    assert not (legacy.folder / "openvpn.log").exists()


@pytest.mark.parametrize("etime, seconds", [
    ("00:07", 7), ("12:34", 754), ("01:02:03", 3723), ("2-01:02:03", 176523),
    ("", None), ("junk", None), ("1:2:3:4", None),
])
def test_process_age_reads_ps_etime(monkeypatch, etime, seconds):
    from types import SimpleNamespace
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=etime + "\n")
    monkeypatch.setattr(openvpn_manager.subprocess, "run", fake_run)
    assert openvpn_manager._process_age(4242) == seconds
    assert seen["argv"] == ["/bin/ps", "-p", "4242", "-o", "etime="]


@pytest.mark.parametrize("age, written_ago, expected", [
    (600, 600, True),             # pid file written as the process started
    (600, 590, True),             # within the slack
    (600, 5, False),              # written long after: someone else's pid
    (600, 900, False),            # written before the process existed
    (None, 600, False),           # age unknown: fail closed
])
def test_a_legacy_pid_must_be_written_when_its_process_started(
        monkeypatch, tmp_path, age, written_ago, expected):
    import time as _time
    folder = tmp_path / "vpn"
    folder.mkdir()
    pid_file = folder / "openvpn.pid"
    pid_file.write_text("4242")
    now = _time.time()
    os.utime(pid_file, (now - written_ago, now - written_ago))
    monkeypatch.setattr(openvpn_manager, "_legacy_dir", lambda: folder)
    monkeypatch.setattr(openvpn_manager, "_process_age", lambda pid: age)
    monkeypatch.setattr(openvpn_manager.time, "time", lambda: now)
    assert openvpn_manager._legacy_pid_is_its_own(4242) is expected


def test_a_missing_legacy_pid_file_is_not_believed(monkeypatch, tmp_path):
    monkeypatch.setattr(openvpn_manager, "_legacy_dir", lambda: tmp_path / "nowhere")
    monkeypatch.setattr(openvpn_manager, "_process_age", lambda pid: 10)
    assert openvpn_manager._legacy_pid_is_its_own(4242) is False


def test_the_root_pid_wins_over_a_legacy_one(monkeypatch, root_files, tmp_path):
    (root_files / "openvpn.pid").write_text("100")
    (root_files / "device").write_text("utun4")
    monkeypatch.setattr(openvpn_manager, "_read_legacy_pid", lambda: 200)
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p, f=None: True)
    assert openvpn_manager._tracked() == (100, None)
    assert openvpn_manager.tunnel_device() == "utun4"


def test_a_root_device_that_is_not_a_tunnel_name_is_ignored(monkeypatch, root_files):
    (root_files / "openvpn.pid").write_text("100")
    (root_files / "device").write_text("en0")
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p, f=None: True)
    assert openvpn_manager.tunnel_device() is None


def test_nothing_is_read_or_created_in_the_user_folder_by_status(monkeypatch, tmp_path):
    legacy = tmp_path / "vpn"
    monkeypatch.setattr(openvpn_manager, "_legacy_dir", lambda: legacy)
    assert openvpn_manager.is_running() is False
    assert not legacy.exists()


@pytest.mark.parametrize("command, expected", [
    ("/opt/homebrew/sbin/openvpn --daemon sentinel-ovpn --writepid /tmp/sentinel.pid", True),
    ("/opt/homebrew/sbin/openvpn --config /tmp/a.ovpn --daemon sentinel-ovpn "
     "--log /tmp/o.log --writepid /tmp/sentinel.pid --verb 3", True),
    ("/usr/bin/python --daemon sentinel-ovpn --writepid /tmp/sentinel.pid", False),
    ("openvpn --daemon other --writepid /tmp/sentinel.pid", False),
    ("openvpn --daemon sentinel-ovpn --writepid /tmp/other.pid", False),
    ("openvpn --daemon sentinel-ovpn --writepid /tmp/sentinel.pid.bak", False),
    ("openvpn --daemon", False),
    ("", False),
])
def test_openvpn_process_identity(monkeypatch, command, expected):
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.setattr(openvpn_manager, "pid_file", lambda: Path("/tmp/sentinel.pid"))
    monkeypatch.setattr(openvpn_manager.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=command))
    assert openvpn_manager._is_tracked_process(12345) is expected


def test_the_root_pid_path_is_what_openvpn_is_told_to_write():
    assert str(openvpn_manager.pid_file()) == "/private/var/run/sentinel-vpn/ovpn/openvpn.pid"


@pytest.mark.parametrize("pid_dir", [
    "/Users/me/Library/Application Support/Sentinel/vpn",   # packaged build
    "/Volumes/USB/Sentinel Data/vpn",                        # portable build
])
def test_openvpn_process_identity_survives_spaces_in_the_pid_path(monkeypatch, pid_dir):
    # D2 regression: ps prints argv unquoted, and the old check re-tokenised
    # it with shlex, so any data directory containing a space made Disconnect
    # refuse, let a second Connect through, and reported "not verified".
    # The legacy pid file still lives in such a folder (S8a).
    from pathlib import Path
    from types import SimpleNamespace
    pid_path = Path(pid_dir) / "openvpn.pid"
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(
            returncode=0,
            stdout=f"/opt/homebrew/sbin/openvpn --config {pid_dir}/srv.ovpn "
                   f"--daemon sentinel-ovpn --log {pid_dir}/openvpn.log "
                   f"--writepid {pid_path} --verb 3\n")
    monkeypatch.setattr(openvpn_manager.subprocess, "run", fake_run)
    assert openvpn_manager._is_tracked_process(4242, pid_path) is True
    assert openvpn_manager._is_tracked_process(4242) is False      # not root's pid file
    assert "-ww" in seen["argv"]          # never truncated by the terminal width


# ── Protocol dispatch ───────────────────────────────────────────────────────

def test_unknown_protocol_is_refused():
    result = vpn_connection.connect(
        {"protocol": "carrier-pigeon", "endpoint": "203.0.113.7"},
        run_as_root=Recorder())
    assert result["success"] is False
    assert "protocol" in result["error"].lower()


@pytest.mark.parametrize("pid", [-100, -1, 0, 1])
def test_openvpn_invalid_pid_never_queries_or_signals_processes(monkeypatch, pid):
    def unexpected(*a, **k):
        pytest.fail("Invalid PID reached process inspection")
    monkeypatch.setattr(openvpn_manager.subprocess, "run", unexpected)
    assert openvpn_manager._is_tracked_process(pid) is False
