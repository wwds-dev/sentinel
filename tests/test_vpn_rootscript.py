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

import contextlib
import hashlib
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
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
    # env -i: root starts with an empty environment (no BASH_ENV, PERL5OPT, ...).
    assert argv[:6] == ["/usr/bin/env", "-i", "/bin/bash", "-c", rs.ROOT_SCRIPT, "sentinel-wg"]
    assert argv[6:] == ["up", "/Users/me/wg nl'; id #/x.conf", SHA, "x", WGQ,
                        rs.BASE_DIR, rs.ROOT_OWNER, rs.WG_RUN_DIR]
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
    (("up", "/a/wg0.conf", SHA, "wg0", WGQ), {"wg_run": "run/wireguard"}),
])
def test_argv_refuses_anything_it_cannot_run_safely(args, kwargs):
    with pytest.raises(ValueError):
        rs.wireguard_argv(*args, **kwargs)


def test_a_disconnect_may_carry_no_file():
    assert rs.wireguard_argv("down", "", "", "wg0", WGQ)[7:9] == ["", ""]


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

def run_group(argv, *, timeout, **kwargs):
    """subprocess.run that kills the script's whole process group on a timeout,
    so a blocked ``head`` or ``sleep`` cannot outlive the test (S7 Opus audit P3-3)."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


# A refused FIFO is refused at once; this only bounds a broken guard's wait.
FIFO_TIMEOUT = 10
UNREAD = b"never read"


@contextlib.contextmanager
def writer_held(fifo):
    """Keep a writer, with marker bytes, on a FIFO the script must refuse to read.

    If a broken ``[ -f ]`` guard lets ``head`` open it, the read ends at EOF when
    this closes (or pytest dies) instead of blocking forever with no writer.
    Yields a check that the marker is still unread; it reads the marker, so call
    it once."""
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, UNREAD)

        def unread() -> bool:
            try:
                return os.read(fd, len(UNREAD) + 1) == UNREAD
            except BlockingIOError:                    # emptied: something read it
                return False
        yield unread
    finally:
        os.close(fd)


def refused_without_reading(call):
    """Run ``call``; a timeout means the script blocked reading a FIFO it must refuse."""
    try:
        return call()
    except subprocess.TimeoutExpired:
        pytest.fail("the script blocked reading the FIFO: a [ -f ] guard is broken")


def test_writer_held_tells_whether_the_fifo_was_read(tmp_path):
    fifo = tmp_path / "pipe"
    for read_it in (False, True):
        os.mkfifo(fifo)
        with writer_held(fifo) as unread:
            if read_it:
                reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
                try:
                    assert os.read(reader, 64) == UNREAD
                finally:
                    os.close(reader)
            assert unread() is not read_it
        fifo.unlink()


@pytest.fixture
def root(tmp_path):
    """A fake wg-quick that logs PATH, its verb, its file and that file's
    contents, and marks the interface up or down in a fake run directory the
    way wg-quick does (<iface>.name). Its exit status comes from a file: root
    starts with an empty environment, so a variable would not reach it."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "wgquick.log"
    envlog = tmp_path / "wgquick.env"
    rc_file = tmp_path / "rc"
    rc_file.write_text("0")
    delay_file = tmp_path / "delay"
    delay_file.write_text("0")
    wgrun = tmp_path / "wireguard"
    wgrun.mkdir()
    fake = stubs / "wg-quick"
    fake.write_text(f"""#!/bin/bash
printf 'PATH=%s\\n%s %s\\n' "$PATH" "$1" "$2" >> '{log}'
{{ /bin/pwd; /usr/bin/env; }} > '{envlog}'
/bin/cat -- "$2" >> '{log}'
/bin/sleep "$(/bin/cat '{delay_file}')"
rc=$(/bin/cat '{rc_file}')
if [ "$rc" = 0 ]; then
  name=${{2##*/}}; name=${{name%.conf}}
  if [ "$1" = up ]; then : > '{wgrun}'/"$name".name; else /bin/rm -f '{wgrun}'/"$name".name; fi
fi
exit "$rc"
""")
    fake.chmod(0o755)
    user = tmp_path / "home"
    user.mkdir()
    conf = user / "wgt.conf"
    conf.write_text("[Interface]\nPostUp = echo GOOD\n")
    base = (tmp_path / "run").resolve() / "sentinel-vpn"
    base.parent.mkdir()
    owner = f"{os.getuid()}:{os.getgid()}"

    def run(verb, source=None, sha=None, *, iface="wgt", rc=0, owner=owner, env=None,
            base=base, timeout=20, script=None):
        source = str(conf) if source is None else source
        sha = (hashlib.sha256(conf.read_bytes()).hexdigest() if source and conf.exists()
               else "") if sha is None else sha
        rc_file.write_text(str(rc))
        argv = rs.wireguard_argv(verb, source, sha, iface, str(fake), base=str(base),
                                 owner=owner, wg_run=str(wgrun))
        if script is not None:
            argv[4] = script
        return run_group(argv, timeout=timeout, env={**os.environ, **(env or {})})

    return types.SimpleNamespace(
        run=run, log=log, envlog=envlog, conf=conf, delay=delay_file, base=base, stubs=stubs, wgrun=wgrun, owner=owner,
        copy=base / "wg" / "wgt" / "wgt.conf",
        mark_up=lambda: (wgrun / "wgt.name").touch(),
        is_up=lambda: (wgrun / "wgt.name").exists(),
        ran=lambda: log.read_text() if log.exists() else "")


def logged(root, verb, path, body):
    return f"PATH={root.stubs}:/usr/bin:/bin:/usr/sbin:/sbin\n{verb} {path}\n{body}"


GOOD = "[Interface]\nPostUp = echo GOOD\n"
def harness(test):
    """Runs the real script under /bin/bash (macOS only: BSD stat and chown)."""
    return on_macos(pytest.mark.allow_process(test))


@harness
def test_connect_runs_wg_quick_on_the_copy_only(root):
    proc = root.run("up")
    assert proc.returncode == 0, proc.stderr
    # PATH: wg-quick's own folder first, then the system folders, nothing inherited.
    assert root.ran() == logged(root, "up", root.copy, GOOD)
    assert str(root.conf) not in root.ran()
    assert oct(root.base.stat().st_mode & 0o777) == "0o755"
    assert oct((root.base / "wg").stat().st_mode & 0o777) == "0o700"
    assert oct(root.copy.parent.stat().st_mode & 0o777) == "0o700"
    assert root.copy.read_bytes() == root.conf.read_bytes()       # kept for Disconnect
    assert oct(root.copy.stat().st_mode & 0o777) == "0o600"       # umask 077: key material


@harness
def test_root_ignores_an_inherited_environment(root, tmp_path):
    marker = tmp_path / "pwned"
    hook = tmp_path / "hook.sh"
    hook.write_text(f": > '{marker}'\n")
    (tmp_path / "Evil.pm").write_text(f"package Evil; open(my $f, '>', '{marker}'); 1;\n")
    proc = root.run("up", env={"BASH_ENV": str(hook), "ENV": str(hook),
                               "PERL5LIB": str(tmp_path), "PERL5OPT": "-MEvil",
                               "SHELLOPTS": "xtrace", "PS4": "+TRACE+"})
    assert proc.returncode == 0, proc.stderr
    assert not marker.exists()
    assert "+TRACE+" not in proc.stderr


@harness
@pytest.mark.parametrize("verb", ["up", "down"])
def test_wg_quick_runs_from_root_with_only_home_and_path(root, tmp_path, verb):
    """PR #34 review P1-2, kept under S7: wg-quick and its hooks start in / (not
    the base, not Sentinel's working directory) and see only HOME=/var/root and
    PATH, whatever the caller had."""
    if verb == "down":
        assert root.run("up").returncode == 0
    proc = subprocess.run(
        rs.wireguard_argv(verb, str(root.conf),
                          hashlib.sha256(root.conf.read_bytes()).hexdigest(), "wgt",
                          str(root.stubs / "wg-quick"), base=str(root.base),
                          owner=root.owner, wg_run=str(root.wgrun)),
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={"SECRET": "x", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    out = root.envlog.read_text().splitlines()
    assert out[0] == "/"
    assert "HOME=/var/root" in out
    assert f"PATH={root.stubs}:/usr/bin:/bin:/usr/sbin:/sbin" in out
    assert {line.split("=", 1)[0] for line in out[1:]} <= {"HOME", "PATH", "PWD", "OLDPWD", "SHLVL", "_"}


@harness
def test_a_config_changed_after_confirming_is_refused_and_nothing_runs(root):
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()       # what the user confirmed
    root.conf.write_text("[Interface]\nPostUp = echo EVIL\n")    # swapped in the window
    proc = root.run("up", sha=sha)
    assert proc.returncode == rs.EXIT_CHANGED
    assert "changed after you confirmed it, or could not be read; nothing was run" in proc.stderr
    assert root.ran() == ""
    assert not root.copy.parent.exists()


@harness
def test_an_unreadable_source_gets_the_same_answer_and_no_path(root):
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()
    root.conf.unlink()
    proc = root.run("up", sha=sha)
    assert proc.returncode == rs.EXIT_CHANGED                      # not 97: one meaning each
    assert str(root.conf) not in proc.stderr                       # no existence oracle
    assert root.ran() == ""


@harness
def test_a_regular_file_root_cannot_read_leaks_no_path_either(root):
    # As root a 000 file is readable; here (not root) it makes head itself fail,
    # which is the path whose error text must not reach the user.
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()
    root.conf.chmod(0)
    try:
        proc = root.run("up", sha=sha)
    finally:
        root.conf.chmod(0o600)
    assert proc.returncode == rs.EXIT_CHANGED
    assert str(root.conf) not in proc.stderr and "head" not in proc.stderr
    assert root.ran() == ""


@harness
def test_a_fifo_is_refused_at_once_and_never_runs_later(root):
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()
    root.conf.unlink()
    os.mkfifo(root.conf)
    with writer_held(root.conf) as unread:
        proc = refused_without_reading(lambda: root.run("up", sha=sha, timeout=FIFO_TIMEOUT))
        assert unread(), "the script read the FIFO"
    assert proc.returncode == rs.EXIT_CHANGED and root.ran() == ""


@harness
def test_a_file_too_big_for_the_review_never_hashes_equal(root):
    root.conf.write_bytes(b"#" * (vpn_execution.CONFIG_READ_LIMIT + 10))
    proc = root.run("up")                      # hash of the whole (oversized) file
    assert proc.returncode == rs.EXIT_CHANGED and root.ran() == ""


@harness
def test_a_failed_connect_removes_the_copy_and_keeps_wg_quicks_status(root):
    proc = root.run("up", rc=3)
    assert proc.returncode == 3
    assert root.ran().startswith(logged(root, "up", root.copy, ""))
    assert not root.copy.parent.exists()


@harness
def test_connect_refuses_an_interface_that_is_already_up_and_keeps_its_copy(root):
    assert root.run("up").returncode == 0
    root.log.unlink()
    proc = root.run("up")
    assert proc.returncode == rs.EXIT_STATE
    assert "already up; nothing was run" in proc.stderr
    assert root.ran() == "" and root.copy.read_text() == GOOD


@harness
def test_disconnect_uses_the_copy_kept_since_connect_and_says_so(root):
    assert root.run("up").returncode == 0
    root.conf.write_text("[Interface]\nPostDown = echo EVIL\n")    # edited while up
    root.log.unlink()
    proc = root.run("down", source="", sha="")
    assert proc.returncode == 0, proc.stderr
    assert "copy kept since Connect" in proc.stdout
    assert root.ran() == logged(root, "down", root.copy, GOOD)
    assert not root.copy.parent.exists() and not root.is_up()


@harness
def test_disconnect_without_a_copy_verifies_the_users_file_first(root):
    root.mark_up()                              # started by hand or by an older build
    proc = root.run("down")
    assert proc.returncode == 0, proc.stderr
    assert "verified your config file" in proc.stdout
    assert root.ran() == logged(root, "down", root.copy, GOOD)
    assert not root.copy.parent.exists()


@harness
def test_disconnect_without_a_copy_or_a_verified_file_runs_nothing(root):
    root.mark_up()
    proc = root.run("down", source="", sha="")
    assert proc.returncode == rs.EXIT_UNVERIFIED
    assert "nothing was run" in proc.stderr and root.ran() == ""


@harness
def test_a_kept_copy_that_is_a_symlink_is_never_followed(root, tmp_path):
    """S7 rebase review P3-4: only a regular file root kept counts as the copy."""
    assert root.run("up").returncode == 0
    evil = tmp_path / "evil.conf"
    evil.write_text("[Interface]\nPostDown = echo EVIL\n")
    root.copy.unlink()
    root.copy.symlink_to(evil)
    root.log.unlink()
    proc = root.run("down", source="", sha="")           # nothing to verify instead
    assert proc.returncode == rs.EXIT_UNVERIFIED and root.ran() == ""
    root.copy.unlink()
    root.copy.symlink_to(evil)
    proc = root.run("down")                               # the reviewed file is verified
    assert proc.returncode == 0, proc.stderr
    assert "verified your config file" in proc.stdout
    assert root.ran() == logged(root, "down", root.copy, GOOD) and "EVIL" not in root.ran()


@harness
def test_a_changed_file_is_refused_on_disconnect_too(root):
    root.mark_up()
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()
    root.conf.write_text("[Interface]\nPostDown = echo EVIL\n")
    proc = root.run("down", sha=sha)
    assert proc.returncode == rs.EXIT_CHANGED and root.ran() == ""


@harness
def test_a_failed_disconnect_keeps_the_copy_for_a_retry(root):
    assert root.run("up").returncode == 0
    proc = root.run("down", source="", sha="", rc=1)
    assert proc.returncode == 1
    assert root.copy.exists() and root.is_up()


@harness
def test_disconnect_of_a_tunnel_that_is_not_up_removes_a_stale_copy(root):
    assert root.run("up").returncode == 0
    (root.wgrun / "wgt.name").unlink()          # taken down outside Sentinel
    root.log.unlink()
    proc = root.run("down", source="", sha="")
    assert proc.returncode == rs.EXIT_STATE
    assert "not up; nothing to stop" in proc.stderr
    assert root.ran() == "" and not root.copy.parent.exists()     # no key left behind


@harness
def test_a_symlinked_base_is_refused(root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.base.symlink_to(elsewhere)
    proc = root.run("up")
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert root.ran() == "" and list(elsewhere.iterdir()) == []


@harness
def test_a_base_reached_through_a_moved_parent_is_refused(root, tmp_path):
    # The final component is a real directory, but the path no longer leads where
    # it says: root must not write the copy (or rm -rf) somewhere else.
    (tmp_path / "moved").symlink_to(root.base.parent)
    proc = root.run("up", base=tmp_path / "moved" / "sentinel-vpn")
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert "moved" in proc.stderr and root.ran() == ""
    assert not (root.base / "wg").exists()


@harness
def test_a_base_owned_by_someone_else_is_refused(root):
    root.base.mkdir()
    proc = root.run("up", owner=f"{os.getuid() + 1}:{os.getgid()}")
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert "not a directory owned by root" in proc.stderr and root.ran() == ""


@harness
def test_a_symlinked_wg_directory_is_refused(root, tmp_path):
    root.base.mkdir()
    (root.base / "wg").symlink_to(tmp_path)
    proc = root.run("up")
    assert proc.returncode == rs.EXIT_REFUSED_DIR and root.ran() == ""


@harness
@pytest.mark.parametrize("iface", ["..", ".", "a/b", ""])
def test_the_script_itself_refuses_a_path_as_interface(root, iface):
    # wireguard_argv already refuses these; the script must not rely on that.
    argv = rs.wireguard_argv("up", str(root.conf), SHA, "wgt", WGQ, base=str(root.base),
                             owner=root.owner, wg_run=str(root.wgrun))
    argv[9] = iface                          # $4, the interface
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert not root.base.exists()


# ── S7 Opus audit follow-ups (docs/audit/s7-opus-audit-2026-11-06.md) ───────

def test_runs_for_one_interface_are_serialised_by_a_lock_no_copy_can_collide_with():
    assert '/usr/bin/lockf -k -t 30 "wg/lock@$iface"' in rs.ROOT_SCRIPT
    assert '"$BASH_EXECUTION_STRING"' in rs.ROOT_SCRIPT
    assert not rs.INTERFACE_RE.fullmatch("lock@wg0")    # never the name of a copy's folder


@harness
def test_a_second_connect_during_the_first_cannot_remove_its_kept_copy(root):
    """P3-1: B starts while A is inside wg-quick up. Before the lock, B passed the
    up check, replaced the copy, failed, and removed it: A's tunnel was left
    with nothing to disconnect from."""
    root.delay.write_text("1")
    results = {}
    a = threading.Thread(target=lambda: results.update(a=root.run("up")))
    a.start()
    time.sleep(0.3)
    results["b"] = root.run("up")
    a.join()
    assert results["a"].returncode == 0, results["a"].stderr
    assert results["b"].returncode == rs.EXIT_STATE        # waited, then saw it up
    assert "already up" in results["b"].stderr
    assert root.copy.read_bytes() == root.conf.read_bytes()
    root.delay.write_text("0")
    root.conf.write_text("[Interface]\nPostDown = echo EVIL\n")
    proc = root.run("down", source="", sha="")
    assert proc.returncode == 0 and "copy kept since Connect" in proc.stdout


@harness
def test_a_stale_name_without_its_socket_is_not_up(root):
    """P3-C: wg-quick's .name survives a crash; without the device's socket the
    review calls it not up, and root must agree."""
    (root.wgrun / "wgt.name").write_text("utun9\n")
    assert vpn_execution.wireguard_interface_state("wgt", root.wgrun)["up"] is False
    proc = root.run("down", source="", sha="")
    assert proc.returncode == rs.EXIT_STATE and "is not up" in proc.stderr
    (root.wgrun / "wgt.name").write_text("utun9\n")
    proc = root.run("up")
    assert proc.returncode == 0, proc.stderr
    assert root.copy.exists()


@harness
def test_a_name_with_its_socket_is_up(root):
    (root.wgrun / "wgt.name").write_text("utun9\n")
    (root.wgrun / "utun9.sock").touch()
    assert vpn_execution.wireguard_interface_state("wgt", root.wgrun)["up"] is True
    proc = root.run("up")
    assert proc.returncode == rs.EXIT_STATE and root.ran() == ""


@harness
def test_existing_folders_are_reset_to_their_modes(root):
    """P3-4 (G): ADR 0004 says an existing base and wg folder are normalised."""
    (root.base / "wg").mkdir(parents=True)
    root.base.chmod(0o777)
    (root.base / "wg").chmod(0o777)
    assert root.run("up").returncode == 0
    assert oct(root.base.stat().st_mode & 0o777) == "0o755"
    assert oct((root.base / "wg").stat().st_mode & 0o777) == "0o700"


@harness
def test_a_read_that_never_ends_is_cut_off_and_refused(root):
    """P3-4 (F, I): a source that delivers the reviewed bytes and then holds the
    read open is killed at the cut-off, and the non-zero ``wait`` refuses the
    copy even though it hashes equal. The cut-off is shortened from 200 ticks
    and the [ -f ] guard removed, which is the only way to reach this code;
    ``cat`` stands in for ``head``, whose buffered output a kill would discard
    (leaving a copy that fails the hash anyway), so only ``wait`` can refuse."""
    sha = hashlib.sha256(root.conf.read_bytes()).hexdigest()
    good = root.conf.read_bytes()
    root.conf.unlink()
    os.mkfifo(root.conf)
    script = rs.ROOT_SCRIPT
    for old, new in (('[ "$i" -ge 200 ]', '[ "$i" -ge 10 ]'),
                     ('  [ -f "$src" ] || refused\n', ""),
                     ("/usr/bin/head -c 262145 --", "/bin/cat --")):
        assert script.count(old) == 1, old
        script = script.replace(old, new)
    fd = os.open(root.conf, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, good)                                  # all of it, and no EOF
        started = time.monotonic()
        proc = root.run("up", sha=sha, script=script, timeout=15)
        elapsed = time.monotonic() - started
    finally:
        os.close(fd)
    assert proc.returncode == rs.EXIT_CHANGED, proc.stderr
    assert root.ran() == "" and not root.copy.parent.exists()
    assert elapsed < 10


# ── OpenVPN (S8a: pid, device and log in root's run dir; S8b: verified copies) ──

H = "c" * 64
G = "d" * 64
OVPN = "/opt/homebrew/sbin/openvpn"


def sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def mode(path) -> int:
    return os.stat(path).st_mode & 0o777


def test_the_ovpn_script_is_a_constant_and_values_are_positional():
    up = rs.openvpn_argv("up", config="/Users/me/a b'; id #/x.ovpn", sha256=H, openvpn=OVPN,
                         includes=(("certs/ca.crt", G), ("/etc/vpn/ta.key", H)),
                         programs=("/usr/local/libexec/up.sh",))
    assert up[:7] == ["/usr/bin/env", "-i", "/bin/bash", "-c", rs.OVPN_SCRIPT,
                      "sentinel-ovpn", "start"]
    assert up[7:] == ["up", "/Users/me/a b'; id #/x.ovpn", H, OVPN, rs.BASE_DIR, rs.ROOT_OWNER,
                      "", "", str(rs.OVPN_START_WAIT), "2", "certs/ca.crt", G,
                      "/etc/vpn/ta.key", H, "/usr/local/libexec/up.sh"]
    assert shlex.split(rs.openvpn_command("up", config="/Users/me/a b'; id #/x.ovpn", sha256=H,
                                          openvpn=OVPN, includes=(("certs/ca.crt", G),
                                                                  ("/etc/vpn/ta.key", H)),
                                          programs=("/usr/local/libexec/up.sh",))) == up
    down = rs.openvpn_argv("down", pid=4242, legacy_pid_file="/Users/me/x y/openvpn.pid")
    assert down[7:] == ["down", "", "", "", rs.BASE_DIR, rs.ROOT_OWNER, "4242",
                        "/Users/me/x y/openvpn.pid", str(rs.OVPN_STOP_WAIT), "0"]
    for text in ("/Users/me", "4242", "certs", H, "up.sh"):
        assert text not in rs.OVPN_SCRIPT


def test_the_pid_path_openvpn_is_given_is_under_the_base():
    assert rs.ovpn_path(rs.OVPN_PID) == "/private/var/run/sentinel-vpn/ovpn/openvpn.pid"
    assert rs.ovpn_path(rs.OVPN_COPY_DIR) == "/private/var/run/sentinel-vpn/ovpn/private/cfg"


@pytest.mark.parametrize("kwargs", [
    {"openvpn": "openvpn"}, {"config": "a.ovpn"}, {"config": "/Users/me/a\n.ovpn"},
    {"openvpn": ""}, {"openvpn": "/Applications/My VPN/openvpn"},   # ps prints argv unquoted
    {"sha256": ""}, {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"sha256": H + "\n"},
    {"base": "rel"}, {"owner": "root:wheel"}, {"wait": 0}, {"wait": 100}, {"wait": True},
    {"programs": ("up.sh",)}, {"programs": ("/.vol/1/2",)}, {"programs": ("/x/a\nb",)},
    {"programs": tuple(f"/p{i}" for i in range(33))},
])
def test_ovpn_up_refuses_anything_it_cannot_run_safely(kwargs):
    values = {"config": "/Users/me/a.ovpn", "sha256": H, "openvpn": OVPN, **kwargs}
    with pytest.raises(ValueError):
        rs.openvpn_argv("up", **values)


@pytest.mark.parametrize("pid", [None, 0, 1, -5, True, "4242", 10 ** 10])
def test_ovpn_down_refuses_an_invalid_pid(pid):
    with pytest.raises(ValueError):
        rs.openvpn_argv("down", pid=pid)


def test_ovpn_down_refuses_a_relative_legacy_pid_file_and_an_unknown_verb():
    with pytest.raises(ValueError):
        rs.openvpn_argv("down", pid=4242, legacy_pid_file="vpn/openvpn.pid")
    with pytest.raises(ValueError):
        rs.openvpn_argv("restart", pid=4242)


@pytest.mark.parametrize("path", [
    "../ca.crt", "..", "certs/../ca.crt", "certs/../../ca.crt", "./ca.crt", "certs//ca.crt",
    "certs/", ".", "", "ca\n.crt", "ca\x00.crt", "ca\x7f.crt", "/etc/ca\n.crt", "/.vol/1/2",
])
def test_a_manifest_path_that_could_leave_the_copy_is_refused(path):
    with pytest.raises(ValueError):
        rs.openvpn_argv("up", config="/x/a.ovpn", sha256=H, openvpn=OVPN, includes=((path, G),))


@pytest.mark.parametrize("entry", [("ca.crt", ""), ("ca.crt", "A" * 64), ("ca.crt",),
                                   ("ca.crt", G, "x"), "ca.crt", (None, G)])
def test_a_malformed_manifest_entry_is_refused(entry):
    with pytest.raises(ValueError):
        rs.openvpn_argv("up", config="/x/a.ovpn", sha256=H, openvpn=OVPN, includes=(entry,))


def test_the_manifest_holds_at_most_sixteen_files_each_once():
    files = tuple((f"f{i}", G) for i in range(rs.MAX_INCLUDES))
    argv = rs.openvpn_argv("up", config="/x/a.ovpn", sha256=H, openvpn=OVPN, includes=files)
    assert argv[16] == str(rs.MAX_INCLUDES) and len(argv) == 17 + 2 * rs.MAX_INCLUDES
    with pytest.raises(ValueError):
        rs.openvpn_argv("up", config="/x/a.ovpn", sha256=H, openvpn=OVPN,
                        includes=files + (("one-more", G),))
    with pytest.raises(ValueError):
        rs.openvpn_argv("up", config="/x/a.ovpn", sha256=H, openvpn=OVPN,
                        includes=(("ca.crt", G), ("ca.crt", G)))


def test_relative_and_absolute_manifest_paths_are_accepted_as_written():
    argv = rs.openvpn_argv("up", config="/x/a.ovpn", sha256=H, openvpn=OVPN, includes=(
        ("my certs/ca.crt", G), ("~/ta.key", G), ("/abs/k.pem", G), (".hidden.key", G)))
    assert argv[-8:] == ["my certs/ca.crt", G, "~/ta.key", G, "/abs/k.pem", G, ".hidden.key", G]


def test_the_script_caps_match_the_python_limits():
    assert f'grab "$cfg" "$copy" {rs.COPY_LIMIT - 1} "$want"' in rs.OVPN_SCRIPT
    assert f'"$cfgd/$p" {rs.INCLUDE_LIMIT} "$h"' in rs.OVPN_SCRIPT           # relative copies
    assert f'"$priv/check" {rs.INCLUDE_LIMIT} "$h"' in rs.OVPN_SCRIPT       # absolute, in place
    assert f'-le {rs.MAX_INCLUDES} ]' in rs.OVPN_SCRIPT
    assert f'-le {rs.MAX_PROGRAMS} ]' in rs.OVPN_SCRIPT
    assert rs.COPY_LIMIT - 1 == vpn_execution.CONFIG_READ_LIMIT


# The stub openvpn records its arguments, its working directory and
# environment, and what it can read below --cd, writes the log line the test
# chose, and "daemonises": a background process whose command line (exec -a)
# reads like openvpn's, so the root script's ps-based identity check sees a
# real process. Mode comes from files, because the script runs under env -i.
_OVPN_STUB = r"""#!/bin/bash
dir=${0%/*}
args=("$@")
printf '%s\n' "$*" >> "$dir/openvpn.args"
{ /bin/pwd; /usr/bin/env; } > "$dir/openvpn.env"
mode=$(/bin/cat "$dir/openvpn.mode")
cd_=""; cfg=""
while [ $# -gt 0 ]; do
  case $1 in --log) log=$2; shift;; --writepid) pidf=$2; shift;; --cd) cd_=$2; shift;;
    --config) cfg=$2; shift;; esac
  shift
done
# What openvpn would read: every file below --cd, and the config it was given.
{ echo "pwd-relative:"; [ -n "$cd_" ] && (cd "$cd_" && /usr/bin/find . -type f | /usr/bin/sort \
    | while read -r f; do printf '%s %s\n' "$f" "$(/bin/cat "$f")"; done)
  echo "config:"; /bin/cat "$cfg"; } > "$dir/openvpn.seen" 2>&1
if [ "$mode" = fail ]; then printf 'Options error: Unrecognized option\n' > "$log"; exit 1; fi
if [ "$mode" = late ]; then
  : > "$log"
  ( /bin/sleep 1; /bin/cat "$dir/openvpn.line" >> "$log" ) </dev/null >/dev/null 2>&1 &
else
  /bin/cat "$dir/openvpn.line" > "$log"
fi
( exec -a "$0" /bin/bash -c 'trap "" HUP; [ "$1" = stubborn ] && trap "" TERM; [ "$1" = slow ] && trap "/bin/sleep 1; exit 0" TERM; while :; do /bin/sleep 0.2; done' "$0" "$mode" "${args[@]}" ) </dev/null >/dev/null 2>&1 &
echo $! > "$pidf"
exit 0
"""


@pytest.fixture
def ovpn(tmp_path):
    o = types.SimpleNamespace()
    o.base = (tmp_path / "run").resolve() / "sentinel-vpn"
    o.base.parent.mkdir()
    o.owner = f"{os.getuid()}:{os.getgid()}"
    o.bin = tmp_path / "bin"
    o.bin.mkdir()
    o.exe = o.bin / "openvpn"
    o.exe.write_text(_OVPN_STUB)
    o.exe.chmod(0o755)
    o.args = o.bin / "openvpn.args"
    o.env = o.bin / "openvpn.env"
    o.mode = o.bin / "openvpn.mode"
    o.mode.write_text("ok")
    o.line = o.bin / "openvpn.line"
    o.line.write_text("TLS: Initial packet from [AF_INET]203.0.113.7:1194\nOpened utun device utun7\n")
    o.conf = tmp_path / "user dir" / "srv.ovpn"
    o.conf.parent.mkdir()
    o.conf.write_text("client\nremote 203.0.113.7 1194\n")
    o.od = o.base / "ovpn"
    o.pidf = o.od / "openvpn.pid"
    o.devf = o.od / "device"
    o.priv = o.od / "private"
    o.log = o.priv / "openvpn.log"
    o.cfgd = o.priv / "cfg"
    o.copy = o.priv / "config.ovpn"
    o.seen = o.bin / "openvpn.seen"
    o.procs = []
    yield o
    for pid in o.procs + _pids(o):
        try:
            os.kill(pid, 9)
        except (OSError, ProcessLookupError):
            pass


def _pids(o) -> list[int]:
    try:
        return [int(o.pidf.read_text().strip())]
    except (OSError, ValueError):
        return []


def prime(o):
    """A run dir as root left it, with an empty ovpn/."""
    o.od.mkdir(parents=True)
    o.base.chmod(0o755)
    o.od.chmod(0o755)


def run_ovpn(o, verb, *, pid=None, legacy="", wait=3, values=None, want=None,
             includes=(), programs=(), env=None, timeout=60):
    if verb == "up":
        want = sha(o.conf) if want is None else want    # the hash the review confirmed
        argv = rs.openvpn_argv("up", config=str(o.conf), sha256=want or "a" * 64,
                               openvpn=str(o.exe), includes=includes, programs=programs,
                               base=str(o.base), owner=o.owner, wait=wait)
        if not want:
            argv[9] = ""
    else:
        argv = rs.openvpn_argv("down", pid=pid, legacy_pid_file=legacy, base=str(o.base),
                               owner=o.owner, wait=wait)
    if values is not None:
        argv = argv[:7] + values
    return run_group(argv, timeout=timeout, env={**os.environ, **(env or {})})


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    out = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return out.returncode == 0 and "Z" not in out.stdout


def wait_gone(pid: int, seconds: float = 3) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


@harness
def test_ovpn_up_keeps_pid_and_device_in_root_files_and_deletes_the_log(ovpn):
    proc = run_ovpn(ovpn, "up")
    assert proc.returncode == 0, proc.stderr
    assert ovpn.devf.read_text() == "utun7"
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    assert alive(pid)
    assert mode(ovpn.base) == 0o755 and mode(ovpn.od) == 0o755
    assert mode(ovpn.pidf) == 0o644 and mode(ovpn.devf) == 0o644
    assert mode(ovpn.priv) == 0o700
    assert not ovpn.log.exists()
    assert "203.0.113.7" not in proc.stdout + proc.stderr     # nothing of the log is echoed
    assert (f"--cd {ovpn.cfgd} --config {ovpn.copy} --daemon sentinel-ovpn --log {ovpn.log} "
            f"--writepid {ovpn.pidf} --verb 3") in ovpn.args.read_text()


@harness
def test_openvpn_runs_from_root_with_only_home_and_path(ovpn, tmp_path):
    """#36 inside the root script: openvpn and its hooks keep its starting
    directory under --daemon, and openvpn reads OPENSSL_CONF and friends."""
    proc = run_ovpn(ovpn, "up", env={"OPENSSL_CONF": str(tmp_path / "evil.cnf"),
                                     "BASH_ENV": str(tmp_path / "x"), "SHELLOPTS": "xtrace",
                                     "PS4": "+TRACE+"})
    assert proc.returncode == 0, proc.stderr
    ovpn.procs.extend(_pids(ovpn))
    out = ovpn.env.read_text().splitlines()
    assert out[0] == "/"
    assert "HOME=/var/root" in out and "PATH=/usr/bin:/bin:/usr/sbin:/sbin" in out
    assert {line.split("=", 1)[0] for line in out[1:]} <= {"HOME", "PATH", "PWD", "OLDPWD",
                                                           "SHLVL", "_"}
    assert "+TRACE+" not in proc.stderr


@harness
@pytest.mark.parametrize("line, expected", [
    ("TLS: Initial packet\nOpened utun device utun4\n", "utun4"),
    ("TUN/TAP device tun0 opened\n", "tun0"),
    ("TUN/TAP device /dev/tun3 opened\n", "tun3"),
    ("Opened utun device utun3\n...restart...\nOpened utun device utun12\n", "utun12"),
    # Security review P3-1: text the server controls (a certificate subject, a
    # pushed option) can carry the words mid-line; only a whole line counts.
    ("VERIFY ERROR: depth=0, subject=CN=x Opened utun device utun0\n"
     "Opened utun device utun7\n", "utun7"),
    ("PUSH: Received control message: 'Opened utun device utun0'\n"
     "Opened utun device utun7\n", "utun7"),
    ("Opened utun device utun4; pass quick all\nOpened utun device utun7\n", "utun7"),
])
def test_ovpn_up_records_only_the_device_name(ovpn, line, expected):
    ovpn.line.write_text(line)
    proc = run_ovpn(ovpn, "up")
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert ovpn.devf.read_text() == expected
    assert "--verb 3 --suppress-timestamps" in ovpn.args.read_text()   # no prefix to skip


@harness
def test_a_device_named_only_by_server_text_is_not_recorded(ovpn):
    ovpn.line.write_text("VERIFY ERROR: depth=0, subject=CN=Opened utun device utun0\n")
    proc = run_ovpn(ovpn, "up", wait=1)
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert not ovpn.devf.exists()


@harness
def test_ovpn_up_without_a_device_line_explains_once_and_keeps_no_log(ovpn):
    ovpn.line.write_text("TLS Error: TLS handshake failed\n")
    proc = run_ovpn(ovpn, "up", wait=2)
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert "OpenVPN start-up output:" in proc.stdout and "TLS handshake failed" in proc.stdout
    assert not ovpn.devf.exists() and not ovpn.log.exists()
    assert ovpn.pidf.exists()                      # still running; Disconnect can stop it


@harness
def test_ovpn_up_waits_for_a_device_line_that_comes_late(ovpn):
    # S8a review P3-1: real openvpn reports the device seconds after --daemon returns.
    ovpn.mode.write_text("late")
    proc = run_ovpn(ovpn, "up", wait=6)
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert ovpn.devf.read_text() == "utun7"


@harness
def test_ovpn_up_gives_up_on_the_device_after_its_wait(ovpn):
    ovpn.mode.write_text("late")
    proc = run_ovpn(ovpn, "up", wait=1)
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert "OpenVPN start-up output:" in proc.stdout
    assert not ovpn.devf.exists()


@harness
def test_ovpn_up_failure_explains_on_stderr_and_leaves_nothing(ovpn):
    ovpn.mode.write_text("fail")
    proc = run_ovpn(ovpn, "up")
    assert proc.returncode == 1
    assert "Unrecognized option" in proc.stderr
    assert not ovpn.priv.exists()
    assert not ovpn.pidf.exists() and not ovpn.devf.exists()


@harness
def test_ovpn_up_while_sentinels_openvpn_runs_is_refused(ovpn):
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "up")
    assert proc.returncode == rs.EXIT_STATE
    assert len(ovpn.args.read_text().splitlines()) == 1        # openvpn ran once
    assert int(ovpn.pidf.read_text()) == pid and ovpn.devf.read_text() == "utun7"
    assert ovpn.copy.exists()                                  # the live tunnel keeps its copies


@harness
def test_ovpn_up_sweeps_a_stale_pid_that_names_another_process(ovpn):
    other = subprocess.Popen(["/bin/sleep", "30"])
    ovpn.procs.append(other.pid)
    prime(ovpn)
    ovpn.pidf.write_text(str(other.pid))
    ovpn.devf.write_text("utun1")
    (ovpn.priv / "cfg").mkdir(parents=True)
    (ovpn.priv / "cfg" / "old.key").write_text("crash leftover")
    proc = run_ovpn(ovpn, "up")
    assert proc.returncode == 0, proc.stderr
    ovpn.procs.extend(_pids(ovpn))
    assert int(ovpn.pidf.read_text()) != other.pid and ovpn.devf.read_text() == "utun7"
    assert not (ovpn.cfgd / "old.key").exists()    # a crash's copies are swept
    assert alive(other.pid)                        # the unrelated process is untouched


@harness
def test_ovpn_down_signals_the_checked_process_and_cleans_up(ovpn):
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "down", pid=pid, wait=10)
    assert proc.returncode == 0, proc.stderr
    assert "OpenVPN stopped." in proc.stdout
    assert wait_gone(pid)
    assert not ovpn.pidf.exists() and not ovpn.devf.exists()
    assert not ovpn.priv.exists()


@harness
def test_ovpn_down_refuses_a_pid_that_is_not_the_recorded_one(ovpn):
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    other = subprocess.Popen(["/bin/sleep", "30"])
    ovpn.procs.append(other.pid)
    proc = run_ovpn(ovpn, "down", pid=other.pid)
    assert proc.returncode == rs.EXIT_CHANGED
    assert alive(other.pid) and alive(pid)
    assert ovpn.copy.exists()


@harness
def test_ovpn_down_rechecks_identity_before_kill(ovpn):
    # The recorded pid names a process that is not Sentinel's openvpn (a stale
    # pid reused by something else): root refuses, signals nothing, and drops
    # the copies its own tunnel no longer needs.
    other = subprocess.Popen(["/bin/sleep", "30"])
    ovpn.procs.append(other.pid)
    prime(ovpn)
    ovpn.pidf.write_text(str(other.pid))
    ovpn.priv.mkdir()
    (ovpn.priv / "config.ovpn").write_text("key material")
    proc = run_ovpn(ovpn, "down", pid=other.pid)
    assert proc.returncode == rs.EXIT_STATE
    assert "not the OpenVPN tunnel Sentinel started" in proc.stderr
    assert alive(other.pid)
    assert not ovpn.priv.exists() and not ovpn.pidf.exists()


@harness
def test_ovpn_down_that_does_not_stop_the_process_keeps_everything(ovpn):
    ovpn.mode.write_text("stubborn")
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "down", pid=pid, wait=2)
    assert proc.returncode == 0
    assert "still running" in proc.stdout
    assert alive(pid) and ovpn.pidf.exists() and ovpn.devf.exists() and ovpn.copy.exists()


@harness
def test_ovpn_down_waits_for_a_slow_exit_before_cleaning_up(ovpn):
    ovpn.mode.write_text("slow")                   # exits about a second after SIGTERM
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "down", pid=pid, wait=10)
    assert proc.returncode == 0, proc.stderr
    assert "OpenVPN stopped." in proc.stdout
    assert not ovpn.pidf.exists() and not ovpn.devf.exists()


@harness
def test_ovpn_down_stops_an_older_sentinels_tunnel_by_its_legacy_pid_file(ovpn, tmp_path):
    legacy_dir = tmp_path / "Application Support" / "vpn"
    legacy_dir.mkdir(parents=True)
    legacy = legacy_dir / "openvpn.pid"
    ovpn.line.write_text("Opened utun device utun5\n")
    # How an older Sentinel started it: its own pid and log in the user folder.
    subprocess.run([str(ovpn.exe), "--config", str(ovpn.conf), "--daemon", "sentinel-ovpn",
                    "--log", str(legacy_dir / "openvpn.log"), "--writepid", str(legacy),
                    "--verb", "3"], check=True, env={"PATH": "/usr/bin:/bin"})
    pid = int(legacy.read_text())
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "down", pid=pid, legacy=str(legacy), wait=10)
    assert proc.returncode == 0, proc.stderr
    assert wait_gone(pid)


@harness
def test_ovpn_down_with_a_legacy_file_still_checks_the_process(ovpn, tmp_path):
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())                 # writes root's pid file, not the legacy one
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "down", pid=pid, legacy=str(tmp_path / "vpn" / "openvpn.pid"))
    assert proc.returncode == rs.EXIT_STATE
    assert alive(pid) and ovpn.copy.exists()          # root's own tunnel is left alone


def lookalike(o, program: str, *args: str) -> int:
    """A background process whose ps command line is ``program args...``."""
    proc = subprocess.Popen(["/bin/bash", "-c",
                             'exec -a "$0" /bin/bash -c "while :; do /bin/sleep 0.2; done" "$0" "$@"',
                             program, *args])
    o.procs.append(proc.pid)
    time.sleep(0.2)                                # let the exec land before ps looks
    return proc.pid


@harness
@pytest.mark.parametrize("program, args", [
    ("/tmp/x/notopenvpn", ["--daemon", "sentinel-ovpn", "--writepid", "PIDF"]),
    ("/tmp/x/openvpn", ["--daemon", "other", "--writepid", "PIDF"]),
    ("/tmp/x/openvpn", ["--writepid", "PIDF"]),
    ("/tmp/x/openvpn", ["--daemon", "sentinel-ovpn", "--writepid", "PIDF.bak"]),
], ids=["not-openvpn", "other-tag", "no-tag", "other-pid-file"])
def test_ovpn_down_refuses_a_process_that_only_resembles_sentinels(ovpn, program, args):
    prime(ovpn)
    pid = lookalike(ovpn, program, *[a.replace("PIDF", str(ovpn.pidf)) for a in args])
    ovpn.pidf.write_text(str(pid))
    proc = run_ovpn(ovpn, "down", pid=pid)
    assert proc.returncode == rs.EXIT_STATE, proc.stderr
    assert alive(pid)


@harness
def test_ovpn_down_signals_only_the_recorded_pid_even_among_lookalikes(ovpn):
    # Two processes pass the identity check; only the one root recorded may be signalled.
    assert run_ovpn(ovpn, "up").returncode == 0
    recorded = int(ovpn.pidf.read_text())
    ovpn.procs.append(recorded)
    twin = lookalike(ovpn, str(ovpn.exe), "--daemon", "sentinel-ovpn", "--writepid", str(ovpn.pidf))
    proc = run_ovpn(ovpn, "down", pid=twin)
    assert proc.returncode == rs.EXIT_CHANGED
    assert "recorded has changed" in proc.stderr
    assert alive(twin) and alive(recorded)


@harness
def test_ovpn_down_treats_a_process_that_exits_before_the_signal_as_stopped(ovpn):
    # S8a review P3-7: kill fails with ESRCH when the process left in between.
    assert run_ovpn(ovpn, "up").returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    os.kill(pid, 9)
    assert wait_gone(pid)
    argv = rs.openvpn_argv("down", pid=pid, base=str(ovpn.base), owner=ovpn.owner, wait=3)
    script = rs.OVPN_SCRIPT.replace('if ! tracked "$pid" "$pf"; then', "if false; then")
    assert script != rs.OVPN_SCRIPT
    argv[4] = script                   # skip the first check, as if it ran just before the exit
    proc = run_group(argv, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "OpenVPN stopped." in proc.stdout
    assert not ovpn.pidf.exists()


@harness
def test_ovpn_base_owned_by_someone_else_is_refused(ovpn):
    argv = rs.openvpn_argv("up", config=str(ovpn.conf), sha256=sha(ovpn.conf),
                           openvpn=str(ovpn.exe), base=str(ovpn.base), owner="0:0", wait=3)
    proc = run_group(argv, timeout=30)
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert not ovpn.args.exists()


@harness
def test_a_symlinked_ovpn_dir_is_refused(ovpn, tmp_path):
    ovpn.base.mkdir()
    (tmp_path / "elsewhere").mkdir()
    ovpn.od.symlink_to(tmp_path / "elsewhere")
    proc = run_ovpn(ovpn, "up")
    assert proc.returncode == rs.EXIT_REFUSED_DIR
    assert not ovpn.args.exists() and list((tmp_path / "elsewhere").iterdir()) == []


def _values(o, verb="up", *, cfg=None, want=None, exe=None, pid="", legacy="", wait="3",
            files=(), programs=()):
    if verb == "up":
        cfg = str(o.conf) if cfg is None else cfg
        want = sha(o.conf) if want is None else want
        exe = str(o.exe) if exe is None else exe
    return [verb, cfg or "", want or "", exe or "", str(o.base), o.owner, pid, legacy, wait,
            str(len(files) // 2), *files, *programs]


@harness
@pytest.mark.parametrize("change", [
    {"exe": "openvpn"}, {"cfg": "a.ovpn"}, {"exe": "/x/My VPN/openvpn"},
    {"verb": "down", "pid": "1"}, {"verb": "down", "pid": "12a"},
    {"verb": "down", "pid": "4242", "legacy": "vpn/openvpn.pid"},
    {"wait": "0"}, {"verb": "restart"},
    # S8b: the manifest and the programs, checked again by root
    {"files": ("ca.crt",)},
    {"files": ("ca.crt", "xyz")},
    {"files": ("../ca.crt", G)}, {"files": ("certs/../../ca.crt", G)},
    {"files": ("./ca.crt", G)}, {"files": ("certs//ca.crt", G)}, {"files": ("certs/", G)},
    {"files": ("..", G)}, {"files": ("", G)}, {"files": ("/.vol/1/2", G)},
    {"files": ("ca.crt", G, "ca.crt", G)},
    {"files": tuple(v for i in range(17) for v in (f"f{i}", G))},
    {"programs": ("up.sh",)}, {"programs": ("/.nofollow/x",)},
    {"verb": "down", "pid": "4242", "files": ("ca.crt", G)},
    {"verb": "down", "pid": "4242", "programs": ("/x/up.sh",)},
])
def test_ovpn_script_rejects_bad_values_itself(ovpn, change):
    proc = run_ovpn(ovpn, "up", values=_values(ovpn, **change))
    assert proc.returncode == rs.EXIT_BAD_ARGUMENT, proc.stderr
    assert not ovpn.args.exists() and not ovpn.base.exists()   # refused before any directory


@harness
def test_ovpn_script_without_a_reviewed_hash_runs_nothing(ovpn):
    proc = run_ovpn(ovpn, "up", values=_values(ovpn, want=""))
    assert proc.returncode == rs.EXIT_UNVERIFIED, proc.stderr
    assert not ovpn.args.exists()


@harness
def test_runs_for_openvpn_are_serialised_by_one_lock(ovpn):
    """Two OpenVPN Connects would share the copies; the second waits for the
    first and then finds its tunnel running (S8b review P3-3, backlog P2-6)."""
    ovpn.mode.write_text("late")                   # the first holds the lock while it waits
    first = subprocess.Popen(rs.openvpn_argv(
        "up", config=str(ovpn.conf), sha256=sha(ovpn.conf), openvpn=str(ovpn.exe),
        base=str(ovpn.base), owner=ovpn.owner, wait=6), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        time.sleep(0.5)
        second = run_ovpn(ovpn, "up")
        first.communicate(timeout=30)
    finally:
        if first.poll() is None:
            os.killpg(first.pid, signal.SIGKILL)
    ovpn.procs.extend(_pids(ovpn))
    assert first.returncode == 0
    assert second.returncode == rs.EXIT_STATE, second.stderr
    assert len(ovpn.args.read_text().splitlines()) == 1
    assert (ovpn.od / "lock").exists() and ovpn.copy.exists()


def make_files(o, files: dict[str, bytes]) -> tuple[tuple[str, str], ...]:
    """Write files beside the user's config; return the manifest for them."""
    from pathlib import Path
    manifest = []
    for rel, data in files.items():
        path = o.conf.parent / rel if not rel.startswith("/") else Path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest.append((rel, hashlib.sha256(data).hexdigest()))
    return tuple(manifest)


@harness
def test_relative_files_are_copied_with_their_layout_and_openvpn_reads_only_copies(ovpn):
    includes = make_files(ovpn, {"certs/ca.crt": b"CA", "ta.key": b"TA", "my keys/c.key": b"CK"})
    proc = run_ovpn(ovpn, "up", includes=includes)
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert (ovpn.cfgd / "certs" / "ca.crt").read_bytes() == b"CA"
    assert (ovpn.cfgd / "ta.key").read_bytes() == b"TA"
    assert (ovpn.cfgd / "my keys" / "c.key").read_bytes() == b"CK"
    assert ovpn.copy.read_bytes() == ovpn.conf.read_bytes()
    assert mode(ovpn.cfgd) == 0o700 and mode(ovpn.cfgd / "certs") == 0o700
    assert mode(ovpn.copy) == 0o600 and mode(ovpn.cfgd / "ta.key") == 0o600
    seen = ovpn.seen.read_text()
    assert "./certs/ca.crt CA" in seen and "./ta.key TA" in seen and "./my keys/c.key CK" in seen
    assert seen.endswith(ovpn.conf.read_text())                   # openvpn read the verified copy
    args = ovpn.args.read_text()
    assert str(ovpn.conf.parent) not in args                      # the user's path is gone
    assert args.index("--cd ") < args.index("--config ")


@harness
def test_an_absolute_file_is_hashed_in_place_and_not_copied(ovpn, tmp_path):
    ta = tmp_path / "elsewhere" / "ta.key"
    includes = make_files(ovpn, {str(ta): b"TA"})
    proc = run_ovpn(ovpn, "up", includes=includes)
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert list(ovpn.cfgd.rglob("*")) == []                       # nothing copied
    assert not (ovpn.priv / "check").exists()
    assert ta.read_bytes() == b"TA"


@harness
def test_a_config_changed_after_the_review_starts_nothing(ovpn):
    want = sha(ovpn.conf)
    ovpn.conf.write_text("client\nremote 198.51.100.9 1194\nup /tmp/evil.sh\n")
    proc = run_ovpn(ovpn, "up", want=want)
    assert proc.returncode == rs.EXIT_CHANGED
    assert "changed after you confirmed it" in proc.stderr
    assert not ovpn.args.exists()
    assert not ovpn.priv.exists() and not ovpn.pidf.exists()


@harness
@pytest.mark.parametrize("which", ["relative", "absolute"])
def test_a_data_file_changed_after_the_review_starts_nothing(ovpn, tmp_path, which):
    from pathlib import Path
    rel = "certs/ca.crt" if which == "relative" else str(tmp_path / "abs" / "ca.crt")
    includes = make_files(ovpn, {"ta.key": b"TA", rel: b"CA"})
    (ovpn.conf.parent / rel if which == "relative" else Path(rel)).write_bytes(b"EVIL CA")
    proc = run_ovpn(ovpn, "up", includes=includes)
    assert proc.returncode == rs.EXIT_CHANGED
    assert "changed after you confirmed it" in proc.stderr and "ca.crt" in proc.stderr
    assert not ovpn.args.exists()
    assert not ovpn.priv.exists()                                 # cfg/ and its copies gone


@harness
@pytest.mark.parametrize("kind", ["missing", "directory", "oversize"])
def test_a_data_file_that_is_not_a_small_regular_file_starts_nothing(ovpn, tmp_path, kind):
    for where in ("relative", "absolute"):
        target = ovpn.conf.parent / "ca.crt" if where == "relative" else tmp_path / "abs.crt"
        name = "ca.crt" if where == "relative" else str(target)
        want = G
        if kind == "directory":
            target.mkdir()
        elif kind == "oversize":
            data = b"#" * (rs.INCLUDE_LIMIT + 1)
            target.write_bytes(data)
            want = hashlib.sha256(data).hexdigest()   # even the true hash of the whole file
        proc = run_ovpn(ovpn, "up", includes=((name, want),))
        assert proc.returncode == rs.EXIT_CHANGED, (where, proc.stderr)
        assert not ovpn.args.exists()
        assert not ovpn.priv.exists()
        if target.is_dir():
            target.rmdir()
        elif target.exists():
            target.unlink()


@harness
@pytest.mark.parametrize("where", ["relative", "absolute", "config"])
def test_a_fifo_is_refused_at_once_and_never_read(ovpn, tmp_path, where):
    target = {"relative": ovpn.conf.parent / "ca.crt", "absolute": tmp_path / "abs.crt",
              "config": ovpn.conf}[where]
    if where == "config":
        target.unlink()
    os.mkfifo(target)
    includes = () if where == "config" else (
        ("ca.crt" if where == "relative" else str(target), G),)
    with writer_held(target) as unread:
        proc = refused_without_reading(lambda: run_ovpn(
            ovpn, "up", want=H, includes=includes, timeout=FIFO_TIMEOUT))
        assert unread()
    assert proc.returncode == rs.EXIT_CHANGED, proc.stderr
    assert not ovpn.args.exists() and not ovpn.priv.exists()


@harness
def test_an_oversize_config_is_never_verified(ovpn):
    data = b"#" * rs.COPY_LIMIT
    ovpn.conf.write_bytes(data)
    proc = run_ovpn(ovpn, "up", want=hashlib.sha256(data).hexdigest())
    assert proc.returncode == rs.EXIT_CHANGED
    assert not ovpn.args.exists()


@harness
def test_disconnect_removes_the_copies(ovpn):
    includes = make_files(ovpn, {"certs/ca.crt": b"CA"})
    assert run_ovpn(ovpn, "up", includes=includes).returncode == 0
    pid = int(ovpn.pidf.read_text())
    ovpn.procs.append(pid)
    proc = run_ovpn(ovpn, "down", pid=pid)
    assert proc.returncode == 0, proc.stderr
    assert not ovpn.priv.exists()


@harness
def test_a_failed_start_removes_the_copies(ovpn):
    ovpn.mode.write_text("fail")
    includes = make_files(ovpn, {"certs/ca.crt": b"CA"})
    proc = run_ovpn(ovpn, "up", includes=includes)
    assert proc.returncode == 1
    assert not ovpn.priv.exists()


@harness
def test_root_rechecks_that_each_hook_program_is_still_root_owned(ovpn, tmp_path):
    """Spec 1.3: the review judged each hook program root-owned; root checks
    again just before start (here the test's own uid stands in for root)."""
    good = tmp_path / "hooks" / "up.sh"
    good.parent.mkdir()
    good.write_text("#!/bin/sh\n")
    good.chmod(0o755)
    good.parent.chmod(0o755)
    proc = run_ovpn(ovpn, "up", programs=(str(good),))
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr
    assert run_ovpn(ovpn, "down", pid=_pids(ovpn)[0], wait=10).returncode == 0
    ovpn.args.unlink()
    for bad in ("writable", "missing", "link"):
        if bad == "writable":
            good.parent.chmod(0o777)                 # a folder above it others can write
            target = good
        elif bad == "missing":
            good.parent.chmod(0o755)
            target = tmp_path / "hooks" / "gone.sh"
        else:
            evil = tmp_path / "evil"
            evil.mkdir(mode=0o777)
            evil.chmod(0o777)
            (evil / "x.sh").write_text("")
            target = tmp_path / "hooks" / "link.sh"
            target.symlink_to(evil / "x.sh")        # owned path, writable real folder
        proc = run_ovpn(ovpn, "up", programs=(str(target),))
        assert proc.returncode == rs.EXIT_CHANGED, (bad, proc.stderr)
        assert "is not owned by root all the way up" in proc.stderr
        assert not ovpn.args.exists() and not ovpn.priv.exists()


@harness
def test_an_ovpn_read_that_never_ends_is_cut_off_and_refused(ovpn):
    """As the WireGuard test above: a source that delivers the reviewed bytes and
    holds the read open is killed at the cut-off, and the non-zero ``wait``
    refuses the copy although it hashes equal. Shortened cut-off, no [ -f ]
    guard, ``cat`` for ``head`` (the only way to reach this code)."""
    want = sha(ovpn.conf)
    good = ovpn.conf.read_bytes()
    ovpn.conf.unlink()
    os.mkfifo(ovpn.conf)
    script = rs.OVPN_SCRIPT
    for old, new in (('[ "$t" -ge 200 ]', '[ "$t" -ge 10 ]'),
                     ('  [ -f "$1" ] || return 1\n', ""),
                     ('/usr/bin/head -c "$(( $3 + 1 ))" --', "/bin/cat --")):
        assert script.count(old) == 1, old
        script = script.replace(old, new)
    argv = rs.openvpn_argv("up", config=str(ovpn.conf), sha256=want, openvpn=str(ovpn.exe),
                           base=str(ovpn.base), owner=ovpn.owner, wait=3)
    argv[4] = script
    fd = os.open(ovpn.conf, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, good)                                  # all of it, and no EOF
        started = time.monotonic()
        proc = run_group(argv, timeout=15)
        elapsed = time.monotonic() - started
    finally:
        os.close(fd)
    assert proc.returncode == rs.EXIT_CHANGED, proc.stderr
    assert not ovpn.args.exists() and not ovpn.priv.exists()
    assert elapsed < 10


# Folders macOS gives to service accounts: owned by neither root nor this user.
_SERVICE_OWNED = ("/private/var/networkd", "/private/var/dextcores", "/private/var/sntpd",
                  "/private/var/ma", "/private/var/jabberd")


@harness
def test_a_hook_program_owned_by_another_account_is_refused(ovpn):
    """Root accepts a program owned by root (or, in this harness, by the test's
    own uid standing in for root), never by a third account."""
    foreign = next((p for p in _SERVICE_OWNED if os.path.isdir(p)
                    and os.stat(p).st_uid not in (0, os.getuid())
                    and not os.stat(p).st_mode & 0o022), None)
    if foreign is None:
        pytest.skip("no service-owned folder on this Mac")
    proc = run_ovpn(ovpn, "up", programs=(foreign,))
    assert proc.returncode == rs.EXIT_CHANGED, proc.stderr
    assert "is not owned by root all the way up" in proc.stderr
    assert not ovpn.args.exists()


@harness
def test_a_links_own_mode_does_not_fail_the_program_recheck(ovpn, tmp_path):
    """Code review P3-2: macOS ignores a symlink's mode, and the review reads
    through it; root must not refuse a root-owned link made under umask 000."""
    real = tmp_path / "hooks" / "up.sh"
    real.parent.mkdir()
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "hooks" / "up-link.sh"
    link.symlink_to(real)
    os.lchmod(link, 0o777)
    assert subprocess.run(["/usr/bin/stat", "-f", "%Lp", str(link)], capture_output=True,
                          text=True).stdout.strip() == "777"
    proc = run_ovpn(ovpn, "up", programs=(str(link),))
    ovpn.procs.extend(_pids(ovpn))
    assert proc.returncode == 0, proc.stderr


@harness
@pytest.mark.parametrize("count", ["08", "01", "-1", "1x"])
def test_root_refuses_a_file_count_that_is_not_plain(ovpn, count):
    # Code review P3-6: "08" made bash itself fail (exit 1), not root's check (99).
    values = _values(ovpn)
    values[9] = count
    proc = run_ovpn(ovpn, "up", values=values + ["ca.crt", G])
    assert proc.returncode == rs.EXIT_BAD_ARGUMENT, proc.stderr
    assert not ovpn.args.exists()


@harness
def test_all_reads_together_are_cut_off_at_the_deadline(ovpn):
    """Security review P3-3: a slow filesystem cannot stretch the approved run
    by 20 s per file. Deadline shortened to 1 s, per-read cut-off left at 20 s,
    no [ -f ] guard and ``cat`` for ``head``, so only the deadline can end it."""
    want = sha(ovpn.conf)
    good = ovpn.conf.read_bytes()
    ovpn.conf.unlink()
    os.mkfifo(ovpn.conf)
    script = rs.OVPN_SCRIPT
    for old, new in (("deadline=$((SECONDS + 30))", "deadline=$((SECONDS + 1))"),
                     ('  [ -f "$1" ] || return 1\n', ""),
                     ('/usr/bin/head -c "$(( $3 + 1 ))" --', "/bin/cat --")):
        assert script.count(old) == 1, old
        script = script.replace(old, new)
    argv = rs.openvpn_argv("up", config=str(ovpn.conf), sha256=want, openvpn=str(ovpn.exe),
                           base=str(ovpn.base), owner=ovpn.owner, wait=3)
    argv[4] = script
    fd = os.open(ovpn.conf, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, good)
        started = time.monotonic()
        proc = run_group(argv, timeout=15)
        elapsed = time.monotonic() - started
    finally:
        os.close(fd)
    assert proc.returncode == rs.EXIT_CHANGED, proc.stderr
    assert elapsed < 6


@harness
def test_root_resolves_links_without_realpath_which_macos_12_lacks(tmp_path):
    """PR #40 review (Codex P2): /bin/realpath exists only from macOS 13, and
    Sentinel supports 12. The script's own resolver must agree with the kernel."""
    assert "/bin/realpath" not in rs.OVPN_SCRIPT.replace("/bin/realpath does not exist", "")
    start = rs.OVPN_SCRIPT.index("real() {")
    func = rs.OVPN_SCRIPT[start:rs.OVPN_SCRIPT.index("\n}\n", start) + 3]
    (tmp_path / "real" / "deep").mkdir(parents=True)
    target = tmp_path / "real" / "deep" / "up.sh"
    target.write_text("")
    (tmp_path / "abs.sh").symlink_to(target)                       # absolute target
    (tmp_path / "real" / "rel.sh").symlink_to("deep/up.sh")        # relative target
    (tmp_path / "chain.sh").symlink_to("real/rel.sh")              # link to a link
    (tmp_path / "dirlink").symlink_to(tmp_path / "real" / "deep")  # a linked folder
    (tmp_path / "up2.sh").symlink_to("dirlink/../deep/up.sh")      # .. after a link
    (tmp_path / "loop").symlink_to("loop")
    cases = ["abs.sh", "real/rel.sh", "chain.sh", "dirlink/up.sh", "up2.sh", "real/deep/up.sh"]
    for name in cases + ["loop"]:
        path = str(tmp_path / name)
        proc = subprocess.run(["/bin/bash", "-c", func + 'real "$1"', "x", path],
                              capture_output=True, text=True, timeout=10)
        if name == "loop":
            assert proc.returncode != 0                            # refused, not hung
        else:
            assert proc.returncode == 0, (name, proc.stderr)
            assert proc.stdout.strip() == os.path.realpath(path), name
    proc = subprocess.run(["/bin/bash", "-c", func + 'real "$1"', "x", "/etc"],
                          capture_output=True, text=True, timeout=10)
    assert proc.stdout.strip() == os.path.realpath("/etc")          # /private/etc on a Mac
