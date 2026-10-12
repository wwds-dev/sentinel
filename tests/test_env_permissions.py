"""API keys live in .env, so the file must be owner-only (0600) after every write.

All paths are in tmp_path; nothing here may touch the real .env.
"""
import os
import stat
from pathlib import Path

import pytest

from services import runtime_paths
from services.runtime_paths import (
    tighten_env_files, tighten_private_file, write_private_text,
)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_new_file_is_created_0600(tmp_path):
    target = tmp_path / "sub" / ".env"
    write_private_text(target, "A=1\n")
    assert target.read_text() == "A=1\n"
    assert _mode(target) == 0o600


def test_existing_0644_file_becomes_0600_on_write(tmp_path):
    target = tmp_path / ".env"
    target.write_text("OLD=1\n")
    os.chmod(target, 0o644)
    write_private_text(target, "NEW=2\n")
    assert target.read_text() == "NEW=2\n"
    assert _mode(target) == 0o600


def test_write_leaves_no_temp_files_behind(tmp_path):
    write_private_text(tmp_path / ".env", "A=1\n")
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_failed_write_keeps_the_old_file_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    write_private_text(target, "KEEP=1\n")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(runtime_paths.os, "replace", boom)
    with pytest.raises(OSError):
        write_private_text(target, "LOST=1\n")
    assert target.read_text() == "KEEP=1\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_tighten_private_file_fixes_a_0644_file(tmp_path):
    target = tmp_path / ".env"
    target.write_text("A=1\n")
    os.chmod(target, 0o644)
    assert tighten_private_file(target) is True
    assert _mode(target) == 0o600
    assert tighten_private_file(target) is False


def test_tighten_private_file_ignores_a_missing_file(tmp_path):
    assert tighten_private_file(tmp_path / "absent") is False


def test_tighten_env_files_covers_checkout_and_app_support(tmp_path, monkeypatch):
    home = tmp_path / "home"
    app_support = home / "Library" / "Application Support" / "Sentinel"
    app_support.mkdir(parents=True)
    live = app_support / ".env"
    live.write_text("A=1\n")
    os.chmod(live, 0o644)
    data = tmp_path / "data"
    data.mkdir()
    other = data / ".env"
    other.write_text("B=2\n")
    os.chmod(other, 0o664)
    monkeypatch.setattr(runtime_paths.Path, "home", classmethod(lambda cls: home))
    # Keep the checkout path inside tmp_path too: no real .env is ever named.
    monkeypatch.setattr(runtime_paths, "__file__", str(tmp_path / "services" / "runtime_paths.py"))
    monkeypatch.setattr(runtime_paths, "user_data_base", lambda: data)
    tighten_env_files()
    assert _mode(live) == 0o600
    assert _mode(other) == 0o600


def test_tighten_private_file_leaves_an_already_0600_file_alone(tmp_path):
    target = tmp_path / ".env"
    target.write_text("A=1\n")
    os.chmod(target, 0o600)
    before = os.stat(target).st_mtime_ns
    assert tighten_private_file(target) is False
    assert _mode(target) == 0o600
    assert os.stat(target).st_mtime_ns == before


def test_tighten_private_file_skips_a_symlink(tmp_path):
    real = tmp_path / "real.env"
    real.write_text("A=1\n")
    os.chmod(real, 0o644)
    link = tmp_path / ".env"
    link.symlink_to(real)
    assert tighten_private_file(link) is False
    assert _mode(real) == 0o644


def test_tighten_warns_with_the_path_only_when_it_cannot(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".env"
    target.write_text("SECRET_VALUE=1\n")
    os.chmod(target, 0o644)

    def refuse(*a, **k):
        raise PermissionError("not yours")

    monkeypatch.setattr(runtime_paths.os, "chmod", refuse)
    assert tighten_private_file(target) is False
    err = capsys.readouterr().err
    assert str(target) in err and "SECRET_VALUE" not in err


def test_symlinked_env_keeps_its_link_and_the_target_is_updated(tmp_path):
    real = tmp_path / "vault" / "real.env"
    real.parent.mkdir()
    real.write_text("OLD=1\n")
    os.chmod(real, 0o644)
    link = tmp_path / ".env"
    link.symlink_to(real)
    write_private_text(link, "NEW=2\n")
    assert link.is_symlink()
    assert real.read_text() == "NEW=2\n"
    assert _mode(real) == 0o600
    assert sorted(p.name for p in real.parent.iterdir()) == ["real.env"]


def test_failure_while_writing_keeps_the_old_file_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    write_private_text(target, "KEEP=1\n")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(runtime_paths.os, "fsync", boom)
    with pytest.raises(OSError):
        write_private_text(target, "LOST=1\n")
    assert target.read_text() == "KEEP=1\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_unwritable_directory_falls_back_to_writing_in_place(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_text("OLD=1\n")
    os.chmod(target, 0o644)

    def no_temp(*a, **k):
        raise PermissionError("directory not writable")

    monkeypatch.setattr(runtime_paths.tempfile, "mkstemp", no_temp)
    write_private_text(target, "NEW=2\n")
    assert target.read_text() == "NEW=2\n"
    assert _mode(target) == 0o600


def test_main_tightens_the_env_at_launch_not_at_import():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    # never at module level: importing main (as many tests do) must not chmod real files
    assert not any(line.startswith("tighten_env_files(") for line in lines)
    entry = source.index('if __name__ == "__main__":')
    assert "tighten_env_files()" in source[entry:]
    # and before the application object exists
    assert source.index("tighten_env_files()", entry) < source.index("QApplication([])", entry)
