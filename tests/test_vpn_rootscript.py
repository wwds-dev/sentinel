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
