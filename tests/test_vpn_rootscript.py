"""Audit tier-2, step S7: root runs only a verified copy of a WireGuard config.

Before, the hash was checked as the password prompt opened and root then ran
``wg-quick up <the user's file>``: anything able to write that file in between
chose what ran as root (PostUp and friends). Now root copies the file into a
root-owned directory, hashes the copy and runs only the copy
(``services/vpn_rootscript.py``, ADR 0004).

The generated script runs here under real /bin/bash against a fake wg-quick in
tmp_path, with the base directory and owner passed in, so no root is needed.
Real ownership, /private/var/run and wg-quick behaviour are the owner checklist
(spec section 9, S7).
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import types

import pytest

from services import vpn_execution, vpn_rootscript as rs

SHA = "a" * 64
WGQ = "/opt/test/bin/wg-quick"

on_macos = pytest.mark.skipif(sys.platform != "darwin",
                              reason="the root script uses macOS stat/chown paths")


# ── argv: what reaches root ─────────────────────────────────────────────────

def test_the_script_is_a_constant_and_values_are_positional():
    argv = rs.wireguard_argv("up", "/Users/me/wg nl'; id #/x.conf", SHA, "x", WGQ)
    assert argv[:4] == ["/bin/bash", "-c", rs.ROOT_SCRIPT, "sentinel-wg"]
    assert argv[4:] == ["up", "/Users/me/wg nl'; id #/x.conf", SHA, "x", WGQ,
                        rs.BASE_DIR, rs.ROOT_OWNER]
    # The one command line the dialog runs splits back into exactly that argv.
    assert shlex.split(rs.wireguard_command("up", "/Users/me/wg nl'; id #/x.conf",
                                            SHA, "x", WGQ)) == argv


def test_the_base_is_the_real_path_not_the_var_symlink():
    assert rs.BASE_DIR == "/private/var/run/sentinel-vpn"
    assert rs.ROOT_OWNER == "0:0"


def test_root_copies_one_byte_more_than_the_review_reads():
    assert rs.COPY_LIMIT == vpn_execution.CONFIG_READ_LIMIT + 1
    assert f"head -c {rs.COPY_LIMIT} " in rs.ROOT_SCRIPT


@pytest.mark.parametrize("args, kwargs", [
    (("start", "/a/wg0.conf", SHA, "wg0", WGQ), {}),            # unknown verb
    (("up", "/a/wg0.conf", SHA, "../x", WGQ), {}),              # interface in a path
    (("up", "/a/wg0.conf", SHA, "", WGQ), {}),
    (("up", "/a/wg0.conf", SHA, "a" * 16, WGQ), {}),             # longer than 15
    (("up", "/a/wg0.conf", SHA, "wg0", "wg-quick"), {}),        # bare tool name
    (("up", "/a/wg0.conf", SHA, "wg0", ""), {}),
    (("up", "/a/wg0.conf", "", "wg0", WGQ), {}),                # path without a hash
    (("down", "", SHA, "wg0", WGQ), {}),                        # hash without a path
    (("up", "/a/wg0.conf", "A" * 64, "wg0", WGQ), {}),          # not lowercase hex
    (("up", "/a/wg0.conf", SHA[:-1], "wg0", WGQ), {}),
    (("up", "wg0.conf", SHA, "wg0", WGQ), {}),                  # relative path
    (("up", "", "", "wg0", WGQ), {}),                           # Connect needs a file
    (("up", "/a/wg0.conf", SHA, "wg0", WGQ), {"base": "rel"}),
    (("up", "/a/wg0.conf", SHA, "wg0", WGQ), {"owner": "root:wheel"}),
])
def test_argv_refuses_anything_it_cannot_run_safely(args, kwargs):
    with pytest.raises(ValueError):
        rs.wireguard_argv(*args, **kwargs)


def test_a_disconnect_may_carry_no_file():
    assert rs.wireguard_argv("down", "", "", "wg0", WGQ)[5:7] == ["", ""]


@pytest.mark.parametrize("target, expected", [
    ("/Users/me/wgnl.conf", "wgnl"),
    ("~/Downloads/mullvad-se1.conf", "mullvad-se1"),
    ("wg0", "wg0"),
    ("/Users/me/not-a-valid-iface.conf", None),                 # 16 characters
    ("/Users/me/wgnl.txt", None),                               # wg-quick needs .conf
    ("/Users/me/wg nl.conf", None),
    ("", None),
])
def test_interface_for_follows_wg_quicks_own_rule(target, expected):
    assert rs.interface_for(target) == expected


# ── the script itself, under real bash ──────────────────────────────────────

@pytest.fixture
def root(tmp_path):
    """A fake wg-quick that logs its verb, its file and that file's contents."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "wgquick.log"
    fake = stubs / "wg-quick"
    fake.write_text(f"""#!/bin/bash
printf '%s %s\\n' "$1" "$2" >> '{log}'
/bin/cat -- "$2" >> '{log}'
exit "${{FAKE_RC:-0}}"
""")
    fake.chmod(0o755)
    user = tmp_path / "home"
    user.mkdir()
    conf = user / "wgt.conf"
    conf.write_text("[Interface]\nPostUp = echo GOOD\n")
    base = tmp_path / "run" / "sentinel-vpn"
    base.parent.mkdir()

    def run(verb, source=None, sha=None, *, iface="wgt", rc=0, owner=None):
        source = str(conf) if source is None else source
        sha = (hashlib.sha256(conf.read_bytes()).hexdigest() if source and conf.exists()
               else "") if sha is None else sha
        argv = rs.wireguard_argv(verb, source, sha, iface, str(fake), base=str(base),
                                 owner=owner or f"{os.getuid()}:{os.getgid()}")
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                              env={**os.environ, "FAKE_RC": str(rc)})
        return proc

    return types.SimpleNamespace(run=run, log=log, conf=conf, base=base,
                                 copy=base / "wg" / "wgt" / "wgt.conf",
                                 ran=lambda: log.read_text() if log.exists() else "")


@on_macos
@pytest.mark.allow_process
def test_connect_runs_wg_quick_on_the_copy_only(root):
    proc = root.run("up")
    assert proc.returncode == 0, proc.stderr
    assert root.ran() == f"up {root.copy}\n[Interface]\nPostUp = echo GOOD\n"
    assert str(root.conf) not in root.ran()
    assert oct(root.base.stat().st_mode & 0o777) == "0o755"
    assert oct((root.base / "wg").stat().st_mode & 0o777) == "0o700"
    assert oct(root.copy.parent.stat().st_mode & 0o777) == "0o700"
    assert root.copy.read_bytes() == root.conf.read_bytes()       # kept for Disconnect
    assert oct(root.copy.stat().st_mode & 0o777) == "0o600"       # umask 077: key material


@on_macos
@pytest.mark.allow_process
def test_a_config_changed_after_confirming_is_refused_and_nothing_runs(root):
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()       # what the user confirmed
    root.conf.write_text("[Interface]\nPostUp = echo EVIL\n")      # swapped in the window
    proc = root.run("up", sha=sha)
    assert proc.returncode == rs.EXIT_CHANGED
    assert "config changed after you confirmed it; nothing was run" in proc.stderr
    assert root.ran() == ""
    assert not root.copy.parent.exists()


@on_macos
@pytest.mark.allow_process
def test_a_file_too_big_for_the_review_never_hashes_equal(root):
    root.conf.write_bytes(b"#" * (vpn_execution.CONFIG_READ_LIMIT + 10))
    proc = root.run("up")                      # hash of the whole (oversized) file
    assert proc.returncode == rs.EXIT_CHANGED and root.ran() == ""


@on_macos
@pytest.mark.allow_process
def test_a_failed_connect_removes_the_copy_and_keeps_wg_quicks_status(root):
    proc = root.run("up", rc=3)
    assert proc.returncode == 3
    assert root.ran().startswith(f"up {root.copy}")
    assert not root.copy.parent.exists()


@on_macos
@pytest.mark.allow_process
def test_disconnect_uses_the_copy_kept_since_connect(root):
    assert root.run("up").returncode == 0
    root.conf.write_text("[Interface]\nPostDown = echo EVIL\n")    # edited while up
    root.log.unlink()
    proc = root.run("down", source="", sha="")
    assert proc.returncode == 0, proc.stderr
    assert root.ran() == f"down {root.copy}\n[Interface]\nPostUp = echo GOOD\n"
    assert not root.copy.parent.exists()


@on_macos
@pytest.mark.allow_process
def test_disconnect_without_a_copy_verifies_the_users_file_first(root):
    proc = root.run("down")
    assert proc.returncode == 0, proc.stderr
    assert root.ran().startswith(f"down {root.copy}\n")
    assert not root.copy.parent.exists()


@on_macos
@pytest.mark.allow_process
def test_disconnect_without_a_copy_or_a_verified_file_runs_nothing(root):
    proc = root.run("down", source="", sha="")
    assert proc.returncode == rs.EXIT_UNVERIFIED
    assert "nothing was run" in proc.stderr and root.ran() == ""


@on_macos
@pytest.mark.allow_process
def test_a_changed_file_is_refused_on_disconnect_too(root):
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()
    root.conf.write_text("[Interface]\nPostDown = echo EVIL\n")
    proc = root.run("down", sha=sha)
    assert proc.returncode == rs.EXIT_CHANGED and root.ran() == ""


@on_macos
@pytest.mark.allow_process
def test_a_failed_disconnect_keeps_the_copy_for_a_retry(root):
    assert root.run("up").returncode == 0
    proc = root.run("down", source="", sha="", rc=1)
    assert proc.returncode == 1
    assert root.copy.exists()


@on_macos
@pytest.mark.allow_process
def test_a_symlinked_base_is_refused(root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.base.symlink_to(elsewhere)
    proc = root.run("up")
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert root.ran() == "" and list(elsewhere.iterdir()) == []


@on_macos
@pytest.mark.allow_process
def test_a_base_owned_by_someone_else_is_refused(root):
    root.base.mkdir()
    proc = root.run("up", owner=f"{os.getuid() + 1}:{os.getgid()}")
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert "not a directory owned by root" in proc.stderr and root.ran() == ""


@on_macos
@pytest.mark.allow_process
def test_a_symlinked_wg_directory_is_refused(root, tmp_path):
    root.base.mkdir()
    (root.base / "wg").symlink_to(tmp_path)
    proc = root.run("up")
    assert proc.returncode == rs.EXIT_REFUSED_DIR and root.ran() == ""


@on_macos
@pytest.mark.allow_process
@pytest.mark.parametrize("iface", ["..", ".", "a/b", ""])
def test_the_script_itself_refuses_a_path_as_interface(root, iface):
    # wireguard_argv already refuses these; the script must not rely on that.
    argv = rs.wireguard_argv("up", str(root.conf), SHA, "wgt", WGQ,
                             base=str(root.base), owner=f"{os.getuid()}:{os.getgid()}")
    argv[7] = iface                          # $4, the interface
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert not root.base.exists()
