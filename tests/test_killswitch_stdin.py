"""Audit tier-2, step S6: root loads the validated rule TEXT, not a user-owned file.

Before, arm() wrote <state>/killswitch.pf (user-owned) and the root script ran
`pfctl -f <that path>`: anyone able to write the file between validation and
the load chose the firewall rules root applied. Now the validated text travels
inside the root script and is piped to `pfctl -f -`; if pf refuses stdin, the
script writes the same text to a root-owned temp file, checks its SHA-256 and
loads that. The generated script runs here under real /bin/bash against stub
pfctl/shasum in tmp_path, without root; real pf behaviour (does `pfctl -f -`
work, what a swapped file does during the dialog) is the owner checklist.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import types

import pytest

from agents.vpn_agent.services import killswitch as ks

needs_tools = pytest.mark.skipif(
    not (shutil.which("shasum") and shutil.which("mktemp")), reason="needs shasum and mktemp")


@pytest.fixture
def armed_script(monkeypatch, tmp_path):
    """arm() with only its privileged hop replaced; yields the script it built."""
    state_dir = tmp_path / "state"
    got = types.SimpleNamespace(script="", rules="")
    monkeypatch.setattr(ks.paths, "state_dir", lambda: state_dir)
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: 1)
    monkeypatch.setattr(ks, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(ks, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(ks, "_run_privileged",
                        lambda script, why: setattr(got, "script", script) or (True, "ok"))
    real_build = ks.build_rules
    monkeypatch.setattr(ks, "build_rules",
                        lambda *a, **k: setattr(got, "rules", real_build(*a, **k)) or got.rules)
    ok, _ = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is True
    return got


def make_stubs(tmp_path, *, fail_stdin=False, fail_file=False, bad_hash=False):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "pfctl.log"
    stdin_copy = tmp_path / "stdin.copy"
    file_copy = tmp_path / "file.copy"
    (stubs / "pfctl").write_text(f"""#!/bin/bash
printf '%s\\n' "$*" >> '{log}'
if [ "$3" = "-f" ] && [ "$4" = "-" ]; then
  cat > '{stdin_copy}'
  exit {1 if fail_stdin else 0}
fi
if [ "$3" = "-f" ]; then
  cp "$4" '{file_copy}'
  echo "$4" > '{tmp_path / "file.path"}'
  exit {1 if fail_file else 0}
fi
exit 0
""")
    (stubs / "pfctl").chmod(0o755)
    if bad_hash:
        (stubs / "shasum").write_text("#!/bin/bash\ncat > /dev/null\necho 'deadbeef  -'\n")
        (stubs / "shasum").chmod(0o755)
    return types.SimpleNamespace(stubs=stubs, log=log, stdin=stdin_copy, file=file_copy,
                                 file_path=tmp_path / "file.path", tmp=tmp_path / "tmp")


def run_script(script, tmp_path, stub):
    stub.tmp.mkdir(exist_ok=True)
    env = {"PATH": f"{stub.stubs}:/usr/bin:/bin", "TMPDIR": str(stub.tmp)}
    return subprocess.run(["/bin/bash", "-c", script], env=env, capture_output=True,
                          text=True, timeout=30)


def calls(stub):
    return stub.log.read_text().splitlines() if stub.log.exists() else []


# ── what the script contains ────────────────────────────────────────────────

def test_the_script_carries_the_rules_and_never_names_the_user_owned_file(armed_script):
    script = armed_script.script
    assert "pass quick on utun7 all" in script
    assert "block drop all" in script
    assert str(ks.rules_path()) not in script and "killswitch.pf" not in script
    assert "pfctl -a vpn-agent-killswitch -f -" in script


def test_the_user_owned_copy_is_still_written_for_recovery(armed_script):
    assert ks.rules_path().read_text(encoding="utf-8") == armed_script.rules


def test_the_loaded_text_is_ascii_without_comments():
    text = ks.loadable_rules("# a comment — with a dash\n\nblock drop all\n  # more\npass quick on lo0 all\n")
    assert text == "block drop all\npass quick on lo0 all\n"
    with pytest.raises(ValueError):
        ks.loadable_rules("pass quick on lo0 all # é\n")
    with pytest.raises(ValueError):
        ks.loadable_rules("pass quick on lo0 all\x00\n")


def test_arm_refuses_rules_it_cannot_pass_as_plain_text(monkeypatch, tmp_path):
    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: 1)
    monkeypatch.setattr(ks, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(ks, "build_rules", lambda *a, **k: "block drop all\npass quick on utuné all\n")
    privileged = []
    monkeypatch.setattr(ks, "_run_privileged", lambda s, w: privileged.append(s) or (True, ""))
    ok, message = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and privileged == [] and "plain text" in message
    assert not (tmp_path / "killswitch.armed").exists()


# ── the script under real bash, on stubs ────────────────────────────────────

@needs_tools
@pytest.mark.allow_process
def test_root_loads_the_text_on_stdin_then_enables_pf(armed_script, tmp_path):
    stub = make_stubs(tmp_path)
    done = run_script(armed_script.script, tmp_path, stub)
    assert done.returncode == 0, done.stderr
    assert calls(stub) == ["-a vpn-agent-killswitch -f -", "-E"]
    assert stub.stdin.read_text() == ks.loadable_rules(armed_script.rules)


@needs_tools
@pytest.mark.allow_process
def test_swapping_the_user_owned_file_after_arming_changes_nothing_root_loads(armed_script, tmp_path):
    ks.rules_path().write_text("pass all\n")                 # attacker edits the file
    stub = make_stubs(tmp_path)
    run_script(armed_script.script, tmp_path, stub)
    loaded = stub.stdin.read_text()
    assert "pass all" not in loaded.splitlines()
    assert loaded == ks.loadable_rules(armed_script.rules)
    assert not stub.file.exists()                            # no file path was ever handed to pfctl


@needs_tools
@pytest.mark.allow_process
def test_when_pf_refuses_stdin_the_same_text_is_loaded_from_a_hash_checked_temp_file(armed_script, tmp_path):
    stub = make_stubs(tmp_path, fail_stdin=True)
    done = run_script(armed_script.script, tmp_path, stub)
    assert done.returncode == 0, done.stderr
    first, second, third = calls(stub)
    assert first == "-a vpn-agent-killswitch -f -" and third == "-E"
    assert second.startswith("-a vpn-agent-killswitch -f ") and not second.endswith(" -")
    text = ks.loadable_rules(armed_script.rules)
    assert stub.file.read_text() == text
    assert hashlib.sha256(stub.file.read_bytes()).hexdigest() in armed_script.script
    assert list(stub.tmp.iterdir()) == []                    # the temp copy is gone


@needs_tools
@pytest.mark.allow_process
def test_a_temp_copy_whose_hash_does_not_match_is_never_loaded(armed_script, tmp_path):
    stub = make_stubs(tmp_path, fail_stdin=True, bad_hash=True)
    done = run_script(armed_script.script, tmp_path, stub)
    assert done.returncode != 0
    assert calls(stub) == ["-a vpn-agent-killswitch -f -"]   # no file load, no -E
    assert list(stub.tmp.iterdir()) == []


@needs_tools
@pytest.mark.allow_process
def test_a_load_failure_does_not_enable_pf_and_reports_failure(armed_script, tmp_path):
    stub = make_stubs(tmp_path, fail_stdin=True, fail_file=True)
    done = run_script(armed_script.script, tmp_path, stub)
    assert done.returncode != 0
    assert "-E" not in calls(stub)
    assert list(stub.tmp.iterdir()) == []
