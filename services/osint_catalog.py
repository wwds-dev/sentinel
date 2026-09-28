"""The OSINT Framework catalogue, as tool suggestions for Trace and Bloodhound.

OSINT Framework (https://osintframework.com, MIT licence, lockfale/OSINT-
Framework on GitHub) publishes its whole tree as one JSON file. Every tool
carries machine-readable facts — live or down, deprecated, pricing, whether
it needs an account or has an API, and whether using it is passive or
*active* (the target may notice). That lets the agents' prompts suggest
current tools filtered to what each agent should recommend, instead of a
hand-kept list that silently rots.

The file is downloaded at most once a week into `data/cache/` by a background
thread started when a Trace or Bloodhound panel is first shown. Prompt
building only ever reads that cache, so it stays offline; with no cache the
agents fall back to their built-in lists alone.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from services.runtime_paths import user_data_base

CATALOG_URL = (
    "https://raw.githubusercontent.com/lockfale/OSINT-Framework/master/public/arf.json"
)
CACHE_MAX_AGE = 7 * 24 * 3600
ATTRIBUTION = "OSINT Framework (osintframework.com, MIT licence)"

#: Target kind → catalogue branches, most relevant first. A branch is a
#: prefix of the tool's category path.
TARGET_BRANCHES: dict[str, tuple[tuple[str, ...], ...]] = {
    "domain": (("Domain Name",), ("Archives", "Web"), ("Cyber Threat Intelligence",)),
    "ip": (("IP & MAC Address",), ("Cyber Threat Intelligence",)),
    "email": (("Email Address",),),
    "username": (("Username",), ("Social Networks",)),
    "company": (("Business Records",), ("Compliance & Risk Intelligence",)),
    "phone": (("Telephone Numbers",),),
    "person": (("People Search Engines",), ("Public Records",), ("Social Networks",)),
}

#: Trace does not send personal identifiers to people-search or dating
#: services, so it does not recommend them either.
TRACE_EXCLUDED_BRANCHES = {"People Search Engines", "Dating"}

#: Never suggested to anyone: shadow libraries distribute copyrighted books
#: and papers. Matched as a substring of the host.
BLOCKED_HOST_PARTS = ("annas-archive", "libgen", "z-lib", "zlibrary", "sci-hub")


@dataclass(frozen=True)
class Tool:
    name: str
    url: str
    path: tuple[str, ...]
    description: str = ""
    pricing: str = ""
    opsec: str = ""
    registration: bool = False
    api: bool = False
    local_install: bool = False

    @property
    def host(self) -> str:
        host = (urlsplit(self.url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    @property
    def active(self) -> bool:
        return self.opsec.lower() == "active"


def _cache_path() -> Path:
    return user_data_base() / "data" / "cache" / "osint-framework.json"


def parse(data: dict) -> list[Tool]:
    """Every live, non-deprecated tool in the tree, in the tree's own order."""
    tools: list[Tool] = []

    def walk(node: dict, path: tuple[str, ...]) -> None:
        children = node.get("children") or []
        url = node.get("url") or ""
        if not children and url.startswith(("http://", "https://")):
            if node.get("status") == "live" and not node.get("deprecated"):
                tools.append(Tool(
                    name=(node.get("name") or "").strip(),
                    url=url,
                    path=path,
                    description=" ".join((node.get("description") or "").split()),
                    pricing=(node.get("pricing") or "").lower(),
                    opsec=(node.get("opsec") or "").lower(),
                    registration=bool(node.get("registration")),
                    api=bool(node.get("api")),
                    local_install=bool(node.get("localInstall")),
                ))
        for child in children:
            walk(child, path + ((node.get("name") or "").strip(),))

    walk(data or {}, ())
    # The root folder's own name ("OSINT Framework") is not a category.
    return [Tool(**{**tool.__dict__, "path": tool.path[1:]}) for tool in tools]


_memo: dict = {"mtime": None, "tools": []}


def cached_tools() -> list[Tool]:
    """The catalogue from the local cache only; [] when there is none."""
    path = _cache_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _memo["mtime"] != mtime:
        try:
            _memo["tools"] = parse(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _memo["tools"] = []
        _memo["mtime"] = mtime
    return _memo["tools"]


def refresh(*, now: float | None = None) -> str:
    """Download the catalogue when the cache is missing or a week old.

    Returns "fresh", "downloaded", or "failed: <reason>"; never raises, and
    a failed download leaves an older cache in place.
    """
    now = time.time() if now is None else now
    path = _cache_path()
    try:
        if now - path.stat().st_mtime < CACHE_MAX_AGE:
            return "fresh"
    except OSError:
        pass
    try:
        resp = requests.get(CATALOG_URL, timeout=30,
                            headers={"User-Agent": "Sentinel-OSINT/2.0"})
        resp.raise_for_status()
        data = resp.json()
        if not parse(data):
            return "failed: the downloaded catalogue held no usable tools"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
        return "downloaded"
    except Exception as exc:
        return f"failed: {str(exc)[:200]}"


_refresh_lock = threading.Lock()
_refresh_started = False


def refresh_in_background() -> bool:
    """Start one refresh per process on a daemon thread. True if started."""
    global _refresh_started
    with _refresh_lock:
        if _refresh_started:
            return False
        _refresh_started = True
    threading.Thread(target=refresh, name="osint-catalog-refresh", daemon=True).start()
    return True


def _in_branch(tool: Tool, branch: tuple[str, ...]) -> bool:
    return tool.path[:len(branch)] == branch


def select(tools: list[Tool], target_kind: str, *, audience: str, limit: int,
           exclude_hosts=()) -> list[Tool]:
    """Pick up to ``limit`` tools for a target kind.

    ``audience`` "trace" keeps only free, no-account, passive, web-based
    tools and leaves out people-search and dating sites; "bloodhound" keeps
    everything live. Branches are interleaved so one large branch (Domain
    Name has ~150 tools) cannot crowd out the rest, and hosts in
    ``exclude_hosts`` — already in the agent's built-in list — are skipped.
    """
    branches = TARGET_BRANCHES.get(target_kind, ())
    if audience == "trace":
        branches = tuple(b for b in branches if b[0] not in TRACE_EXCLUDED_BRANCHES)

    def allowed(tool: Tool) -> bool:
        if any(part in tool.host for part in BLOCKED_HOST_PARTS):
            return False
        if audience != "trace":
            return True
        return (tool.pricing == "free" and not tool.registration
                and tool.opsec == "passive" and not tool.local_install)

    queues = [[t for t in tools if _in_branch(t, b) and allowed(t)] for b in branches]
    seen = set(exclude_hosts)
    picked: list[Tool] = []
    while len(picked) < limit and any(queues):
        for queue in queues:
            while queue:
                tool = queue.pop(0)
                if tool.host and tool.host not in seen:
                    seen.add(tool.host)
                    picked.append(tool)
                    break
            if len(picked) >= limit:
                break
    return picked


def tags(tool: Tool) -> str:
    parts = [tool.pricing or "pricing unknown"]
    if tool.registration:
        parts.append("account needed")
    if tool.api:
        parts.append("API")
    if tool.local_install:
        parts.append("local install")
    if tool.active:
        parts.append("ACTIVE — the target may notice")
    return " · ".join(parts)


def format_block(tools: list[Tool], *, description_limit: int = 140) -> str:
    """One prompt line per tool: name, URL, what it is for, and its tags."""
    lines = []
    for tool in tools:
        text = tool.description
        if len(text) > description_limit:
            text = text[:description_limit].rsplit(" ", 1)[0] + "…"
        where = " > ".join(tool.path)
        lines.append(f"- {tool.name}: {tool.url} — {text} ({where}) [{tags(tool)}]")
    return "\n".join(lines)


def hosts_in(text: str) -> set[str]:
    """Hosts already named in a prompt, so the catalogue does not repeat them."""
    import re

    hosts = set()
    for match in re.findall(r"https?://[^\s)\]]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\.[a-z]{2,}\b"
                            r"|\b[a-z0-9-]+\.[a-z]{2,}\b", text.lower()):
        url = match if "://" in match else f"https://{match}"
        host = (urlsplit(url).hostname or "")
        hosts.add(host[4:] if host.startswith("www.") else host)
    hosts.discard("")
    return hosts
