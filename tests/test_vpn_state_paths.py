"""D1: VPN state lives in Sentinel's data folder; the standalone app's folder is copied, never moved."""

from __future__ import annotations

import os
import stat

import pytest

from agents.vpn_agent.server import paths


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.ENV_STATE_DIR, raising=False)
    monkeypatch.setattr(paths, "_prepared", set())
    monkeypatch.setattr(paths, "_notice", "")
    data = tmp_path / "sentinel-data"
    data.mkdir()
    monkeypatch.setattr("services.runtime_paths.user_data_base", lambda: data)
    legacy = tmp_path / "old-vpn-agent"
    monkeypatch.setattr(paths, "legacy_state_dir", lambda: legacy)
    return data, legacy


def test_state_lives_under_the_sentinel_data_folder(fresh):
    data, _ = fresh
    assert paths.state_dir() == data / "vpn"
    assert stat.S_IMODE((data / "vpn").stat().st_mode) == 0o700


def test_the_environment_override_still_wins(fresh, tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_STATE_DIR, str(tmp_path / "elsewhere"))
    assert paths.state_dir() == tmp_path / "elsewhere"


def test_the_standalone_apps_state_is_copied_not_moved(fresh):
    data, legacy = fresh
    (legacy / "sites").mkdir(parents=True)
    (legacy / "sites" / "berlin.json").write_text('{"name": "Berlin"}')
    (legacy / "sites" / "berlin.json").chmod(0o644)
    (legacy / "vpn_profiles.json").write_text("[]")

    root = paths.state_dir()

    assert (root / "sites" / "berlin.json").read_text() == '{"name": "Berlin"}'
    assert (legacy / "sites" / "berlin.json").exists()                 # original untouched
    assert stat.S_IMODE((root / "sites" / "berlin.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "sites").stat().st_mode) == 0o700
    assert "Copied 2 file(s)" in paths.migration_notice()
    assert str(legacy) in paths.migration_notice()


def test_the_copy_runs_once_and_never_overwrites(fresh, monkeypatch):
    data, legacy = fresh
    legacy.mkdir()
    (legacy / "vpn_profiles.json").write_text("old")
    root = paths.state_dir()
    (root / "vpn_profiles.json").write_text("edited in Sentinel")
    (legacy / "extra.json").write_text("added later to the old folder")

    monkeypatch.setattr(paths, "_prepared", set())          # a new process
    paths.state_dir()

    assert (root / "vpn_profiles.json").read_text() == "edited in Sentinel"
    assert not (root / "extra.json").exists()               # marker: already migrated


def test_symlinks_in_the_old_folder_are_not_followed(fresh, tmp_path):
    data, legacy = fresh
    legacy.mkdir()
    secret = tmp_path / "outside-secret"
    secret.write_text("do not copy")
    os.symlink(secret, legacy / "link.json")
    root = paths.state_dir()
    assert not (root / "link.json").exists()


def test_no_old_folder_means_no_notice(fresh):
    paths.state_dir()
    assert paths.migration_notice() == ""
