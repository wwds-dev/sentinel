"""User picks go to a git-ignored override, never into the tracked defaults."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import settings_store

SHIPPED = {
    "default_model_ollama": "deepseek-r1:8b",
    "default_model_openai": "gpt-4.1-mini",
}


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_paths_follow_the_writable_base(tmp_path):
    assert settings_store.defaults_path(tmp_path) == tmp_path / "config" / "settings.json"
    assert settings_store.override_path(tmp_path) == tmp_path / "data" / "settings.local.json"


def test_override_is_laid_over_the_defaults(tmp_path):
    defaults = tmp_path / "config" / "settings.json"
    override = tmp_path / "data" / "settings.local.json"
    _write(defaults, SHIPPED)
    _write(override, {"default_model_ollama": "mine", "routing_priority": "cost"})

    assert settings_store.load_settings(defaults, override) == {
        "default_model_ollama": "mine",           # the pick wins
        "default_model_openai": "gpt-4.1-mini",   # untouched keys follow the defaults
        "routing_priority": "cost",               # a key only the override has
    }


def test_missing_or_broken_files_read_as_empty(tmp_path):
    defaults = tmp_path / "settings.json"
    override = tmp_path / "settings.local.json"
    assert settings_store.load_settings(defaults, override) == {}
    defaults.write_text("{not json")
    _write(override, ["not", "an", "object"])
    assert settings_store.load_settings(defaults, override) == {}


def test_save_override_writes_only_the_picked_key(tmp_path):
    defaults = tmp_path / "config" / "settings.json"
    override = tmp_path / "data" / "settings.local.json"
    _write(defaults, SHIPPED)
    before = defaults.read_bytes()

    settings_store.save_override(override, "default_model_ollama", "mine")
    settings_store.save_override(override, "routing_priority", "speed")

    assert defaults.read_bytes() == before
    assert _read(override) == {"default_model_ollama": "mine", "routing_priority": "speed"}
    assert not list(override.parent.glob(".settings.local.json.*")), "temp file left behind"


def _fake_window(provider: str, model: str):
    import main

    window = SimpleNamespace(
        provider_box=SimpleNamespace(currentText=lambda: provider),
        model_box=SimpleNamespace(currentText=lambda: model),
        settings=dict(SHIPPED),
        output_box=SimpleNamespace(append=lambda text: pytest.fail(text)),
    )
    window.save_setting = lambda key, value: main.GodAI.save_setting(window, key, value)
    return window


def test_chat_model_pick_goes_to_the_override(tmp_path, monkeypatch):
    import main

    defaults = tmp_path / "config" / "settings.json"
    override = tmp_path / "data" / "settings.local.json"
    _write(defaults, SHIPPED)
    before = defaults.read_bytes()
    monkeypatch.setattr(main, "SETTINGS_FILE", defaults)
    monkeypatch.setattr(main, "SETTINGS_OVERRIDE_FILE", override)

    window = _fake_window("ollama", "muse-glimmer:30b-q4_K_M")
    main.GodAI.save_provider_model_preference(window)

    assert defaults.read_bytes() == before
    assert _read(override) == {"default_model_ollama": "muse-glimmer:30b-q4_K_M"}
    assert window.settings["default_model_ollama"] == "muse-glimmer:30b-q4_K_M"
    assert settings_store.load_settings(defaults, override)["default_model_ollama"] == (
        "muse-glimmer:30b-q4_K_M")


def test_settings_writers_never_name_the_tracked_file():
    """Every write in main.py goes through save_setting; nothing opens the
    shipped defaults for writing again."""
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert 'open(SETTINGS_FILE, "w"' not in source
    assert "load_json(SETTINGS_FILE" not in source


# ── One-time migration ────────────────────────────────────────────────────────

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture()
def checkout(tmp_path):
    repo = tmp_path / "checkout"
    defaults = repo / "config" / "settings.json"
    _write(defaults, SHIPPED)
    (repo / ".gitignore").write_text("data/settings.local.json\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "defaults")
    return repo, defaults, repo / "data" / "settings.local.json"


@needs_git
def test_migration_moves_picks_and_restores_the_tracked_file(checkout):
    repo, defaults, override = checkout
    committed = defaults.read_bytes()
    # What an older Sentinel did: rewrite the whole tracked file with a pick in it.
    _write(defaults, {**SHIPPED, "default_model_ollama": "muse-glimmer:30b-q4_K_M",
                      "routing_priority": "quality"})

    moved = settings_store.migrate_tracked_defaults(defaults, override)

    assert moved == {"default_model_ollama": "muse-glimmer:30b-q4_K_M",
                     "routing_priority": "quality"}
    assert _read(override) == moved
    assert defaults.read_bytes() == committed
    assert _git(repo, "status", "--porcelain") == ""
    merged = settings_store.load_settings(defaults, override)
    assert merged["default_model_ollama"] == "muse-glimmer:30b-q4_K_M"
    assert merged["default_model_openai"] == "gpt-4.1-mini"


@needs_git
def test_migration_runs_once(checkout):
    _repo, defaults, override = checkout

    # A clean checkout: nothing moves, but the override now marks it as done.
    assert settings_store.migrate_tracked_defaults(defaults, override) == {}
    assert _read(override) == {}

    # So a later hand edit of the shipped defaults is left alone.
    _write(defaults, {**SHIPPED, "default_model_openai": "gpt-5"})
    edited = defaults.read_bytes()
    assert settings_store.migrate_tracked_defaults(defaults, override) is None
    assert defaults.read_bytes() == edited
    assert _read(override) == {}


@needs_git
def test_migration_keeps_an_existing_override(checkout):
    _repo, defaults, override = checkout
    _write(override, {"default_model_ollama": "already-mine"})
    _write(defaults, {**SHIPPED, "default_model_ollama": "other"})

    assert settings_store.migrate_tracked_defaults(defaults, override) is None
    assert _read(override) == {"default_model_ollama": "already-mine"}


def test_migration_does_nothing_outside_a_checkout(tmp_path):
    """Frozen and portable data folders are not repos: their settings.json is
    the user's own seeded copy and must not be rewritten."""
    defaults = tmp_path / "config" / "settings.json"
    override = tmp_path / "data" / "settings.local.json"
    _write(defaults, {**SHIPPED, "default_model_ollama": "mine"})
    before = defaults.read_bytes()

    assert settings_store.migrate_tracked_defaults(defaults, override) is None
    assert defaults.read_bytes() == before
    assert not override.exists()
    assert settings_store.load_settings(defaults, override)["default_model_ollama"] == "mine"


@needs_git
def test_migration_leaves_an_unparseable_edit_alone(checkout):
    _repo, defaults, override = checkout
    defaults.write_text("{ half an edit")

    assert settings_store.migrate_tracked_defaults(defaults, override) is None
    assert defaults.read_text() == "{ half an edit"
    assert not override.exists()
