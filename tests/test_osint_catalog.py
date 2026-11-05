"""services/osint_catalog.py: parsing, per-agent filtering, the shadow-library
blocklist, the weekly cache refresh and the offline fallback.

Nothing here touches the network: `requests.get` is replaced, and the cache
file lives under tmp_path (tests/conftest.py already points the session at an
empty one; each test here repoints it so it can write).
"""

from __future__ import annotations

import json
import os

import pytest

from services import osint_catalog as oc


def leaf(name, url, *, status="live", pricing="free", opsec="passive", registration=False,
         api=False, local=False, deprecated=False, description="A tool."):
    node = {"name": name, "url": url, "status": status, "pricing": pricing, "opsec": opsec,
            "registration": registration, "api": api, "localInstall": local,
            "deprecated": deprecated, "description": description}
    return node


def folder(name, *children):
    return {"name": name, "children": list(children)}


TREE = folder(
    "OSINT Framework",
    folder("Domain Name",
           leaf("Free Passive", "https://www.free-passive.example/x"),
           leaf("Paid", "https://paid.example", pricing="paid"),
           leaf("Needs Account", "https://acct.example", registration=True),
           leaf("Active Scanner", "https://scan.example", opsec="active"),
           leaf("Local Only", "https://local.example", local=True),
           leaf("Dead", "https://dead.example", status="down"),
           leaf("Old", "https://old.example", deprecated=True),
           leaf("No Url", ""),
           leaf("Shadow", "https://www.libgen.example/book"),
           leaf("Shadow2", "https://annas-archive.example"),
           folder("Sub", leaf("Nested", "https://nested.example", description="  spaced   out  "))),
    folder("People Search Engines",
           leaf("Person Finder", "https://people.example")),
    folder("Dating", leaf("Date Lookup", "https://date.example")),
    folder("Username", leaf("Handle Check", "https://handle.example")),
)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "osint-framework.json"
    monkeypatch.setattr(oc, "_cache_path", lambda: path)
    monkeypatch.setitem(oc._memo, "mtime", None)
    monkeypatch.setitem(oc._memo, "tools", [])
    return path


def by_name(tools):
    return {t.name: t for t in tools}


# ── parsing ───────────────────────────────────────────────────────────────

def test_parse_keeps_only_live_non_deprecated_leaves_with_a_url():
    names = {t.name for t in oc.parse(TREE)}
    assert "Dead" not in names and "Old" not in names and "No Url" not in names
    assert {"Free Passive", "Paid", "Nested", "Person Finder"} <= names


def test_parse_drops_the_root_name_from_the_category_path_and_keeps_nesting():
    tools = by_name(oc.parse(TREE))
    assert tools["Free Passive"].path == ("Domain Name",)
    assert tools["Nested"].path == ("Domain Name", "Sub")


def test_parse_reads_the_facts_and_normalises_text():
    tool = by_name(oc.parse(TREE))["Nested"]
    assert tool.description == "spaced out"
    scanner = by_name(oc.parse(TREE))["Active Scanner"]
    assert scanner.active and scanner.opsec == "active"
    acct = by_name(oc.parse(TREE))["Needs Account"]
    assert acct.registration and not acct.api


def test_parse_of_empty_or_none_is_empty():
    assert oc.parse({}) == []
    assert oc.parse(None) == []


def test_host_strips_www_and_lowercases():
    assert by_name(oc.parse(TREE))["Free Passive"].host == "free-passive.example"
    assert oc.Tool("x", "HTTPS://WWW.Mixed.Example/p", ()).host == "mixed.example"


# ── per-agent filtering ───────────────────────────────────────────────────

def select(kind, audience, **kw):
    return oc.select(oc.parse(TREE), kind, audience=audience, limit=kw.pop("limit", 50), **kw)


def test_trace_keeps_only_free_no_account_passive_non_local_tools():
    names = {t.name for t in select("domain", "trace")}
    assert names == {"Free Passive", "Nested"}


def test_trace_never_suggests_people_search_or_dating_branches():
    assert select("person", "trace") == []
    assert {t.name for t in select("person", "bloodhound")} == {"Person Finder"}


def test_bloodhound_keeps_scope_limited_tools_and_tags_active_ones():
    tools = select("domain", "bloodhound")
    names = {t.name for t in tools}
    assert {"Paid", "Needs Account", "Active Scanner", "Local Only"} <= names
    tagged = {t.name: oc.tags(t) for t in tools}
    assert "ACTIVE — the target may notice" in tagged["Active Scanner"]
    assert "ACTIVE" not in tagged["Free Passive"]
    assert "account needed" in tagged["Needs Account"]
    assert "local install" in tagged["Local Only"]


def test_unknown_target_kind_selects_nothing():
    assert select("spaceship", "bloodhound") == []


def test_select_respects_the_limit_and_the_excluded_hosts():
    assert len(select("domain", "bloodhound", limit=2)) == 2
    picked = select("domain", "trace", exclude_hosts={"free-passive.example"})
    assert {t.name for t in picked} == {"Nested"}


def test_select_interleaves_branches_so_a_big_one_cannot_crowd_out_the_rest():
    big = folder("OSINT Framework",
                 folder("Domain Name", *[leaf(f"A{i}", f"https://a{i}.example") for i in range(5)]),
                 folder("Cyber Threat Intelligence", leaf("W", "https://w.example")))
    got = oc.select(oc.parse(big), "domain", audience="bloodhound", limit=2)
    assert [t.name for t in got] == ["A0", "W"]


def test_select_does_not_repeat_a_host():
    twin = folder("OSINT Framework", folder("Domain Name",
                  leaf("One", "https://same.example/a"), leaf("Two", "https://www.same.example/b")))
    assert [t.name for t in oc.select(oc.parse(twin), "domain", audience="bloodhound", limit=5)] == ["One"]


# ── shadow-library blocklist ──────────────────────────────────────────────

@pytest.mark.parametrize("audience", ["trace", "bloodhound"])
def test_shadow_libraries_are_never_suggested_to_anyone(audience):
    names = {t.name for t in select("domain", audience)}
    assert "Shadow" not in names and "Shadow2" not in names


@pytest.mark.parametrize("host", ["annas-archive.org", "libgen.is", "z-lib.example",
                                  "zlibrary.example", "sci-hub.se"])
def test_every_blocked_host_part_is_refused(host):
    tree = folder("OSINT Framework", folder("Domain Name", leaf("B", f"https://{host}/")))
    assert oc.select(oc.parse(tree), "domain", audience="bloodhound", limit=5) == []


# ── prompt text ───────────────────────────────────────────────────────────

def test_format_block_has_name_url_path_and_tags_and_truncates_long_text():
    tool = oc.Tool("T", "https://t.example", ("Domain Name", "Sub"),
                   description="word " * 100, pricing="free", api=True)
    line = oc.format_block([tool], description_limit=20)
    assert line.startswith("- T: https://t.example — ")
    assert "…" in line and "(Domain Name > Sub)" in line and "[free · API]" in line


def test_hosts_in_finds_urls_and_bare_domains_without_www():
    found = oc.hosts_in("Try https://www.Shodan.io/x and crt.sh, or whois.example.com.")
    assert {"shodan.io", "crt.sh", "whois.example.com"} <= found


# ── cache, weekly refresh and the offline fallback ────────────────────────

class Resp:
    def __init__(self, data, status_ok=True):
        self._data, self._ok = data, status_ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._data


@pytest.fixture
def net(monkeypatch):
    calls = []

    def install(resp=None, exc=None):
        def get(url, **kw):
            calls.append((url, kw))
            if exc:
                raise exc
            return resp
        monkeypatch.setattr(oc.requests, "get", get)
        return calls
    return install


def test_no_cache_means_no_tools_and_no_network(cache, net):
    calls = net(exc=AssertionError("must not fetch"))
    assert oc.cached_tools() == []
    assert calls == []


def test_a_corrupt_cache_falls_back_to_empty(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text("{not json", encoding="utf-8")
    assert oc.cached_tools() == []


def test_cached_tools_reads_the_file_and_rereads_when_it_changes(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(TREE), encoding="utf-8")
    assert "Nested" in {t.name for t in oc.cached_tools()}
    cache.write_text(json.dumps(folder("OSINT Framework", leaf("Only", "https://only.example"))),
                     encoding="utf-8")
    os.utime(cache, (cache.stat().st_mtime + 5,) * 2)
    assert [t.name for t in oc.cached_tools()] == ["Only"]


def test_refresh_downloads_when_missing_and_writes_the_cache(cache, net):
    calls = net(Resp(TREE))
    assert oc.refresh() == "downloaded"
    assert calls[0][0] == oc.CATALOG_URL
    assert json.loads(cache.read_text(encoding="utf-8")) == TREE
    assert not cache.with_suffix(".tmp").exists()


def test_refresh_leaves_a_cache_under_a_week_old_alone(cache, net):
    cache.parent.mkdir(parents=True)
    cache.write_text("{}", encoding="utf-8")
    calls = net(exc=AssertionError("must not fetch"))
    now = cache.stat().st_mtime + oc.CACHE_MAX_AGE - 60
    assert oc.refresh(now=now) == "fresh"
    assert calls == []


def test_refresh_replaces_a_cache_a_week_old(cache, net):
    cache.parent.mkdir(parents=True)
    cache.write_text("{}", encoding="utf-8")
    net(Resp(TREE))
    now = cache.stat().st_mtime + oc.CACHE_MAX_AGE + 1
    assert oc.refresh(now=now) == "downloaded"
    assert json.loads(cache.read_text(encoding="utf-8")) == TREE


def test_failed_download_keeps_the_old_cache_and_never_raises(cache, net):
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(TREE), encoding="utf-8")
    net(exc=ConnectionError("offline"))
    result = oc.refresh(now=cache.stat().st_mtime + oc.CACHE_MAX_AGE + 1)
    assert result.startswith("failed: ") and "offline" in result
    assert json.loads(cache.read_text(encoding="utf-8")) == TREE


def test_http_error_is_a_failure_not_an_exception(cache, net):
    net(Resp({}, status_ok=False))
    assert oc.refresh().startswith("failed: ")
    assert not cache.exists()


def test_a_download_with_no_usable_tools_is_not_cached(cache, net):
    net(Resp(folder("OSINT Framework", leaf("Dead", "https://d.example", status="down"))))
    assert oc.refresh().startswith("failed: ")
    assert not cache.exists()


def test_offline_with_no_cache_the_agents_get_an_empty_selection(cache, net):
    net(exc=ConnectionError("offline"))
    assert oc.refresh().startswith("failed: ")
    assert oc.select(oc.cached_tools(), "domain", audience="trace", limit=10) == []
