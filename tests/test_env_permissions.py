"""API keys live in .env, so the file must be owner-only (0600) after every write.

All paths are in tmp_path; nothing here may touch the real .env.
"""
import os
import stat

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
    monkeypatch.setattr(runtime_paths, "user_data_base", lambda: data)
    tighten_env_files()
    assert _mode(live) == 0o600
    assert _mode(other) == 0o600
