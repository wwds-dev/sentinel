"""The pure parts of scripts/release_smoke.py.

The smoke itself launches the installed app, which the test suite must never
do; these cover the decisions it makes from what it reads.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_smoke", ROOT / "scripts" / "release_smoke.py")
smoke = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = smoke         # dataclasses look the module up while it loads
_spec.loader.exec_module(smoke)

APP = Path("/Applications/Sentinel.app")
CHECKOUT = Path("/Users/someone/lab/active/sentinel")


def test_parse_ps_keeps_spaces_in_the_command_and_skips_junk():
    rows = smoke.parse_ps(
        "  101     1 /Applications/Sentinel.app/Contents/MacOS/Sentinel\n"
        "  202   101 /Applications/Sentinel.app/Contents/MacOS/Sentinel -m some tool\n"
        "garbage line\n"
    )
    assert rows == [
        (101, 1, "/Applications/Sentinel.app/Contents/MacOS/Sentinel"),
        (202, 101, "/Applications/Sentinel.app/Contents/MacOS/Sentinel -m some tool"),
    ]


def test_a_child_started_with_sys_executable_is_not_a_second_copy():
    rows = [
        (101, 1, f"{APP}/Contents/MacOS/Sentinel"),
        (202, 101, f"{APP}/Contents/MacOS/Sentinel -u worker.py"),
        (303, 1, "/usr/bin/some-other-app"),
    ]
    assert smoke.sentinel_roots(rows, APP, CHECKOUT, own_pid=999) == [101]


def test_two_launched_copies_are_both_counted():
    rows = [
        (101, 1, f"{APP}/Contents/MacOS/Sentinel"),
        (202, 1, f"{APP}/Contents/MacOS/Sentinel"),
    ]
    assert smoke.sentinel_roots(rows, APP, CHECKOUT, own_pid=999) == [101, 202]


def test_a_source_run_of_main_py_counts_and_the_smoke_itself_does_not():
    rows = [
        (101, 50, f"{CHECKOUT}/.venv/bin/python {CHECKOUT}/main.py"),
        (999, 50, f"python3 {CHECKOUT}/scripts/release_smoke.py"),
    ]
    assert smoke.sentinel_roots(rows, APP, CHECKOUT, own_pid=999) == [101]
    assert smoke.sentinel_roots(rows, APP, None, own_pid=999) == []


def test_refuses_while_sentinel_runs_or_a_mac_change_is_recorded():
    assert smoke.refusal_reasons([], []) == []
    running = smoke.refusal_reasons([101], [])
    assert len(running) == 1 and "101" in running[0] and "quit it first" in running[0]
    record = Path("/x/vpn/mac_original.json")
    recorded = smoke.refusal_reasons([], [record])
    assert len(recorded) == 1 and str(record) in recorded[0]
    assert len(smoke.refusal_reasons([101], [record])) == 2


def test_recorded_mac_changes_reads_the_checkout_record(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    vpn = tmp_path / "vpn"
    vpn.mkdir()
    assert smoke.recorded_mac_changes(tmp_path) == []
    (vpn / "mac_original.json").write_text("{}")
    assert smoke.recorded_mac_changes(tmp_path) == []
    (vpn / "mac_original.json").write_text('{"en0": {"original": "x"}}')
    assert smoke.recorded_mac_changes(tmp_path) == [vpn / "mac_original.json"]


def test_socket_path_is_the_qt_temp_path_plus_the_key():
    assert smoke.socket_path("/var/folders/ab/T/") == Path(
        "/var/folders/ab/T/sentinel.single-instance.v2")
    assert smoke.SINGLE_INSTANCE_KEY == _main_single_instance_key()


def _main_single_instance_key() -> str:
    for line in (ROOT / "main.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("SINGLE_INSTANCE_KEY ="):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("main.py no longer defines SINGLE_INSTANCE_KEY")


def test_launch_log_path_matches_the_installer():
    text = (ROOT / "scripts" / "install_app.sh").read_text(encoding="utf-8")
    assert f'-DLOG_PATH="\\"{smoke.LAUNCH_LOG}\\""' in text


def test_launchd_mentions_ignores_launch_services_records():
    labels = ["com.apple.Finder", "application.com.netrunner3000.sentinel.1.2",
              "com.netrunner3000.sentinel.launch.123"]
    agents = {"/Users/x/Library/LaunchAgents/com.other.plist": b"<string>/Applications/Sentinel.app</string>",
              "/Users/x/Library/LaunchAgents/com.git.plist": b"nothing here"}
    found = smoke.launchd_mentions(labels, agents, APP)
    assert found == ["launchctl job com.netrunner3000.sentinel.launch.123",
                     "LaunchAgent /Users/x/Library/LaunchAgents/com.other.plist"]
    assert smoke.launchd_mentions(["application.com.netrunner3000.sentinel.9"], {}, APP) == []


def test_table_lists_every_check_with_its_status():
    table = smoke.format_table([smoke.Check("one", "PASS"), smoke.Check("longer name", "FAIL", "why")])
    assert table.splitlines() == ["PASS  one", "FAIL  longer name  why"]
