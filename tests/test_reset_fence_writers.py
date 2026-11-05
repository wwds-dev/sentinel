"""Background writers stay out of the data folder while an Emergency Reset erases it
(no-thread-terminate A11 #7): Sentry's baseline and findings, the WhatsMyName
cache and the OSINT catalogue cache."""

from __future__ import annotations

import json

import pytest

from agents.sentry.sentry import baseline as sentry_baseline
from agents.sentry.sentry.baseline import BaselineStore
from agents.sentry.sentry.models import Finding, Snapshot
from providers import whatsmyname as wmn
from services import osint_catalog as oc
from services import portable_reset


@pytest.fixture
def latch(monkeypatch):
    portable_reset._ERASING.clear()
    monkeypatch.setattr(sentry_baseline, "WRITE_GUARD", portable_reset.writes_allowed)
    yield portable_reset._ERASING
    portable_reset._ERASING.clear()


def _finding():
    return Finding(severity="notice", kind="new_device", title="x")


def _snapshot():
    return Snapshot(gateway_ip="", gateway_mac="", devices=[], listeners=[], connections=[])


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


# ── Sentry ─────────────────────────────────────────────────────────────────
def test_sentry_writes_nothing_while_erasing(latch, tmp_path):
    store = BaselineStore(tmp_path)
    latch.set()
    store.save_baseline(_snapshot())
    store.append_findings([_finding()])
    assert not store.baseline_path.exists()
    assert not store.findings_path.exists()
    assert not list(tmp_path.glob(".tmp-*"))


def test_sentry_writes_normally_when_no_reset_is_running(latch, tmp_path):
    store = BaselineStore(tmp_path)
    store.save_baseline(_snapshot())
    assert len(store.append_findings([_finding()])) == 1
    assert store.baseline_path.is_file() and store.findings_path.is_file()


def test_sentry_writes_again_after_a_refused_reset(latch, tmp_path):
    store = BaselineStore(tmp_path)
    latch.set()
    store.save_baseline(_snapshot())
    latch.clear()                      # a refused/failed reset clears the latch
    store.save_baseline(_snapshot())
    assert store.baseline_path.is_file()


def test_main_wires_the_sentry_guard(monkeypatch):
    monkeypatch.undo()
    import main  # noqa: F401  (importing it installs the guards)
    assert sentry_baseline.WRITE_GUARD is portable_reset.writes_allowed


# ── WhatsMyName ────────────────────────────────────────────────────────────
@pytest.fixture
def wmn_net(monkeypatch, tmp_path):
    path = tmp_path / "cache" / "wmn-data.json"
    monkeypatch.setattr(wmn, "_cache_path", lambda: path)
    monkeypatch.setattr(wmn.requests, "get",
                        lambda *a, **k: _Resp({"sites": [{"name": "A", "uri_check": "https://a/{account}"}]}))
    return path


def test_wmn_cache_is_not_written_while_erasing(latch, wmn_net):
    latch.set()
    sites, origin = wmn.load_sites()
    assert origin == "download" and len(sites) == 1     # the sweep still gets its list
    assert not wmn_net.parent.exists()


def test_wmn_cache_is_written_when_no_reset_is_running(latch, wmn_net):
    wmn.load_sites()
    assert json.loads(wmn_net.read_text())["sites"]


# ── OSINT catalogue ────────────────────────────────────────────────────────
@pytest.fixture
def oc_net(monkeypatch, tmp_path):
    path = tmp_path / "cache" / "osint-framework.json"
    monkeypatch.setattr(oc, "_cache_path", lambda: path)
    monkeypatch.setattr(oc, "parse", lambda data: [object()])
    monkeypatch.setattr(oc.requests, "get", lambda *a, **k: _Resp({"name": "root"}))
    return path


def test_catalogue_cache_is_not_written_while_erasing(latch, oc_net):
    latch.set()
    assert oc.refresh().startswith("skipped")
    assert not oc_net.parent.exists()


def test_catalogue_cache_is_written_when_no_reset_is_running(latch, oc_net):
    assert oc.refresh() == "downloaded"
    assert oc_net.is_file()
