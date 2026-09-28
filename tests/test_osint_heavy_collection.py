"""Bloodhound (osint_heavy) live-collection dispatch and gauge sourcing.

Regression cover for the defect where the panel passed combobox labels
("Email Address", "Domain / IP", "Auto-detect", …) verbatim while the
dispatcher only matched short lowercase tokens, so live collection silently
no-opped for the two most common target types. Providers are stubbed, so
these tests never touch the network.
"""

from __future__ import annotations

import pytest

import agents.osint_heavy_agent as heavy


@pytest.fixture
def recording_providers(monkeypatch):
    """Replace every provider .lookup with a recorder that logs which ran."""
    calls: list[str] = []

    def make(source_name):
        def _fake(target, *args, **kwargs):
            calls.append(source_name)
            # Mimic the real providers' shape, including the real per-source trail.
            return {
                "type": source_name,
                "query": target,
                "sources_contacted": [{"source": source_name, "status": "checked"}],
            }
        return _fake

    monkeypatch.setattr(heavy._domain_prov, "lookup", make("domain"))
    monkeypatch.setattr(heavy._email_prov, "lookup", make("email"))
    monkeypatch.setattr(heavy._username_prov, "lookup", make("username"))
    monkeypatch.setattr(heavy._company_prov, "lookup", make("company"))
    # Domain, organisation and email collection also runs a dark-web exposure
    # check; unstubbed, it queried Ransomware.live and Ahmia on every run.
    monkeypatch.setattr(heavy._exposure_prov, "lookup", make("exposure"))
    return calls


# ── Label → provider dispatch ────────────────────────────────────────────────

@pytest.mark.parametrize("label,target,expected", [
    ("Email Address", "suspect@darkmail.io", ["email", "exposure"]),
    ("email", "suspect@darkmail.io", ["email", "exposure"]),
    ("Domain / IP", "phishkit-delivery.net", ["domain", "exposure"]),
    ("Domain / IP", "192.0.2.10", ["domain", "exposure"]),
    ("domain", "example.com", ["domain", "exposure"]),
    ("Username", "h4x0r_pete", ["username"]),
    ("Organisation", "Acme Corporation", ["company", "exposure"]),
    ("Phone Number", "+353 1 234 5678", []),          # no provider by design
])
def test_labels_dispatch_to_the_right_provider(
        recording_providers, label, target, expected):
    heavy._run_providers(target, label)
    assert recording_providers == expected


def test_auto_detect_routes_by_target_shape(recording_providers):
    heavy._run_providers("suspect@darkmail.io", "Auto-detect")
    heavy._run_providers("example.com", "Auto-detect")
    heavy._run_providers("lonewolf", "Auto-detect")
    assert recording_providers == ["email", "exposure", "domain", "exposure", "username"]


def test_email_address_label_is_no_longer_a_silent_noop(recording_providers):
    # The exact regression: this used to return [] because "email address" != "email".
    results = heavy._run_providers("victim@example.com", "Email Address")
    assert results and results[0]["type"] == "email"
    assert recording_providers == ["email", "exposure"]


# ── Real source count feeds the gauge, not the model's estimate ─────────────

def test_real_source_count_sums_contacted_sources(recording_providers):
    agent = heavy.OsintHeavyAgent()
    agent.collect_live("victim@example.com", "Email Address")
    assert agent.last_source_count == 2  # stubbed email + exposure sources
    assert heavy.real_source_count([]) == 0
    assert heavy.real_source_count(None) == 0


def test_phone_target_contacts_zero_sources(recording_providers):
    agent = heavy.OsintHeavyAgent()
    agent.collect_live("+353 1 234 5678", "Phone Number")
    assert agent.last_source_count == 0


# ── build_messages stays offline ────────────────────────────────────────────

def test_build_messages_does_not_collect(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(heavy, "_run_providers",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    agent = heavy.OsintHeavyAgent()
    msgs = agent.build_messages("example.com", "Domain / IP", "Deep Dive", "why")
    assert called["n"] == 0                     # no network from build_messages
    assert "LIVE OSINT DATA" not in msgs[1]["content"]


def test_build_messages_injects_supplied_live_results():
    agent = heavy.OsintHeavyAgent()
    live = [{"type": "domain", "query": "example.com",
             "sources_contacted": [{"source": "WHOIS", "status": "checked"}]}]
    msgs = agent.build_messages(
        "example.com", "Domain / IP", "Deep Dive", "why", live_results=live)
    assert "LIVE OSINT DATA" in msgs[1]["content"]
    assert "example.com" in msgs[1]["content"]


# ── Scope decides whether the WhatsMyName sweep runs ─────────────────────────

@pytest.fixture
def username_kwargs(monkeypatch):
    seen: list[dict] = []

    def fake(target, **kwargs):
        seen.append(kwargs)
        return {"type": "username", "query": target, "sources_contacted": []}

    monkeypatch.setattr(heavy._username_prov, "lookup", fake)
    return seen


@pytest.mark.parametrize("scope,sweeps", [
    ("Deep Dive", True),
    ("Standard Investigation", False),
    ("Quick Scan", False),
    ("", False),
])
def test_only_a_deep_dive_sweeps_whatsmyname(username_kwargs, scope, sweeps):
    heavy._run_providers("lonewolf", "Username", scope)
    assert username_kwargs[-1]["whatsmyname"] is sweeps


def test_collection_reports_progress_and_honours_stop(username_kwargs):
    messages: list[str] = []
    stop = lambda: False
    heavy.OsintHeavyAgent().collect_live(
        "lonewolf", "Username", "Deep Dive",
        on_progress=messages.append, should_stop=stop)
    kwargs = username_kwargs[-1]
    assert kwargs["should_stop"] is stop
    kwargs["on_progress"]("URLScan", "checked")
    kwargs["on_sweep_progress"](10, 600)
    assert messages == ["URLScan: checked", "WhatsMyName: 10 of 600 sites checked"]


def test_organisation_targets_also_check_offshore_leaks(monkeypatch):
    seen: list[dict] = []

    def fake(target, **kwargs):
        seen.append(kwargs)
        return {"type": "company", "query": target, "sources_contacted": []}

    monkeypatch.setattr(heavy._company_prov, "lookup", fake)
    if hasattr(heavy, "_exposure_prov"):
        monkeypatch.setattr(heavy._exposure_prov, "lookup",
                            lambda *a, **k: {"sources_contacted": []})
    heavy._run_providers("Acme Corporation", "Organisation")
    assert seen[-1]["offshore_leaks"] is True
    assert seen[-1]["sanctions"] is True       # skipped inside without a key


@pytest.mark.parametrize("label,target", [
    ("Crypto Address", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"),
    ("Auto-detect", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"),
    ("Auto-detect", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"),
])
def test_crypto_addresses_go_to_the_crypto_provider(monkeypatch, label, target):
    calls = []
    monkeypatch.setattr(heavy._crypto_prov, "lookup",
                        lambda t, **k: calls.append(t) or {"type": "crypto", "query": t,
                                                           "sources_contacted": []})
    monkeypatch.setattr(heavy._username_prov, "lookup",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("not a username")))
    heavy._run_providers(target, label)
    assert calls == [target]
    assert heavy._catalog_kind(target, label) == "crypto"


def test_an_ordinary_handle_is_still_a_username():
    assert heavy._normalize_target_type("lonewolf", "Auto-detect") == "username"
