"""Per-task model ratings from LMArena's public leaderboard.

The router used to rank models on hand-set 1-3 buckets, on which a local 30B
model and Claude Opus were both a 3. Those buckets cannot say whether a cheap
model is *good enough* for a request, which is the question that decides
spend. LMArena publishes ratings per kind of work — coding, creative writing,
hard prompts, long queries, vision — from millions of head-to-head votes, so
"within 20 points of the best for this kind of work" means something: a
20-point gap is about a 53/47 split when the two models meet.

Source: lmarena-ai/leaderboard-dataset on Hugging Face, CC BY 4.0. The app
must credit it wherever a rating is shown, which `ATTRIBUTION` is for.

How the data arrives, most to least current:

1. Hugging Face's dataset row pager (`fetch_live`), at most once a day, from
   the model-scan thread, never something the app waits on. (Its query
   endpoint would need fewer requests, but answered "the dataset index is
   loading" for minutes at a time when this was written.)
2. The last successful fetch, cached in the data folder.
3. `config/lmarena_snapshot.json`, shipped with the code. Refresh it with
   `python -m services.benchmarks --snapshot` when the live service answers.

Name matching (`arena_key`) is the fragile part and is deliberately
conservative: dots and dashes are unified, date stamps dropped, and an effort
suffix (-high, -max, ...) is matched only when no plain entry exists — and
then the *lowest*-rated variant is used, because the app calls a model with
its default settings and must not credit it with a setting it never asks for.
Local Ollama models are never rated: the leaderboard rates the full model, not
the quantised copy on this Mac.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping

SOURCE = "LMArena"
LICENSE = "CC BY 4.0"
SOURCE_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
ATTRIBUTION = f"Ratings: {SOURCE} leaderboard ({LICENSE})"

DATASET = "lmarena-ai/leaderboard-dataset"
ROWS_API = "https://datasets-server.huggingface.co/rows"
SNAPSHOT_FILE = Path(__file__).resolve().parents[1] / "config" / "lmarena_snapshot.json"
MAX_AGE = timedelta(hours=24)

# Sentinel's task -> (leaderboard subset, category). A task with no entry
# (image generation) has no rating and falls back to the router's own score.
TASK_CATEGORIES: dict[str, tuple[str, str]] = {
    "general": ("text_style_control", "overall"),
    "simple": ("text_style_control", "overall"),
    "writing": ("text_style_control", "creative_writing"),
    "coding": ("text_style_control", "coding"),
    "reasoning": ("text_style_control", "hard_prompts"),
    "research": ("text_style_control", "longer_query"),
    "long_context": ("text_style_control", "longer_query"),
    "summarize": ("text_style_control", "longer_query"),
    "vision": ("vision_style_control", "overall"),
}
CATEGORY_LABELS = {
    "overall": "overall", "creative_writing": "creative writing",
    "coding": "coding", "hard_prompts": "hard prompts",
    "longer_query": "long queries",
}

# Leaderboard organisation -> Sentinel provider. Rows from anyone else are
# dropped: they can never be routed to, and a name like "qwen3-max" from a
# reseller must not shadow Alibaba's own.
ORGANISATIONS = {
    "anthropic": "anthropic", "openai": "openai", "google": "gemini",
    "deepseek": "deepseek", "moonshot": "kimi", "alibaba": "qwen",
}

# Provider ids that the leaderboard spells differently and no rule can derive.
# Keep this short and explain every entry.
ALIASES = {
    # DeepSeek's API renamed deepseek-v4-flash to deepseek-flash in Sep 2026;
    # the leaderboard still uses the version name.
    ("deepseek", "deepseek-flash"): "deepseek-v4-flash",
}

_EFFORT = re.compile(
    r"-(high|xhigh|max|max-effort|low|medium|minimal|none|instant|thinking|"
    r"no-thinking|reasoning)$"
)
_DATE = (
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),
    re.compile(r"-\d{8}$"),
    re.compile(r"-\d{2}-\d{4}$"),          # gemini "-09-2025"
    re.compile(r"-\d{4}$"),                # "-0125", "-0905"
)
_PREVIEW = re.compile(r"-(preview|exp|experimental|latest)$")


def arena_key(name: str) -> tuple[str, bool]:
    """(normalised name, whether an effort suffix was removed).

    "claude-opus-5.5-high" -> ("claude-opus-5-5", True);
    "gpt-4.1-2025-04-14" -> ("gpt-4-1", False);
    "gemini-2.5-flash-preview-09-2025" -> ("gemini-2-5-flash", False).
    """
    key = re.sub(r"\s*\(.*?\)\s*", "", name.lower().strip())
    key = key.split("/")[-1]
    effort = False
    changed = True
    while changed:
        changed = False
        for pattern in (*_DATE, _PREVIEW):
            stripped = pattern.sub("", key)
            if stripped != key and stripped:
                key, changed = stripped, True
        stripped = _EFFORT.sub("", key)
        if stripped != key and stripped:
            key, changed, effort = stripped, True, True
    return re.sub(r"(?<=\d)\.(?=\d)", "-", key), effort


@dataclass(frozen=True)
class Rating:
    score: float
    lower: float
    upper: float
    votes: int
    arena_name: str
    category: str

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category.replace("_", " "))


class RatingTable:
    """Ratings by (subset, category) and normalised name, plus provenance."""

    def __init__(self, data: Mapping | None = None, *, origin: str = "none"):
        data = data or {}
        self.published = str(data.get("published", ""))
        self.fetched = str(data.get("fetched", ""))
        self.origin = origin
        self._by_category: dict[str, dict[str, list[Rating]]] = {}
        for table, rows in (data.get("categories") or {}).items():
            category = table.split("/", 1)[-1]
            index: dict[str, list[Rating]] = {}
            for row in rows:
                try:
                    name, score, lower, upper, votes, org = row
                except (TypeError, ValueError):
                    continue
                key, effort = arena_key(name)
                provider = ORGANISATIONS.get(org)
                if provider is None:
                    continue
                index.setdefault(f"{provider}/{key}", []).append(Rating(
                    float(score), float(lower), float(upper), int(votes),
                    name, category))
                # Plain entries first, so `rating` can prefer them.
                index[f"{provider}/{key}"].sort(
                    key=lambda r: (arena_key(r.arena_name)[1], r.score))
            self._by_category[table] = index

    def __bool__(self) -> bool:
        return any(self._by_category.values())

    def rating(self, provider: str, model: str, task: str) -> Rating | None:
        if provider not in ORGANISATIONS.values():
            return None                     # never Ollama; see the module notes
        subset_category = TASK_CATEGORIES.get(task)
        if subset_category is None:
            return None
        index = self._by_category.get("/".join(subset_category))
        if not index:
            return None
        name = ALIASES.get((provider, model), model)
        variants = index.get(f"{provider}/{arena_key(name)[0]}")
        if not variants:
            return None
        plain = [r for r in variants if not arena_key(r.arena_name)[1]]
        # Several plain snapshots (dated releases of one model): the lowest.
        return min(plain or variants, key=lambda r: r.score)

    def describe(self) -> str:
        if not self:
            return "No ratings loaded."
        where = {"live": "fetched", "cache": "cached", "snapshot": "shipped snapshot"}
        return (f"{ATTRIBUTION}, published {self.published or 'unknown'} "
                f"({where.get(self.origin, self.origin)}).")


# ── Fetching ─────────────────────────────────────────────────────────────────

def _needed_tables() -> list[tuple[str, str]]:
    return sorted(set(TASK_CATEGORIES.values()))


def fetch_live(*, get: Callable[[str], dict] | None = None,
               attempts: int = 3, backoff: float = 10.0, pause: float = 1.0,
               sleep: Callable[[float], None] = time.sleep) -> dict:
    """Page through each subset the router needs and keep its categories.

    Uses the service's plain row pager rather than its query endpoint: the
    query endpoint answered "the dataset index is loading" for minutes at a
    time, while the pager reads the stored file and answers in a fraction of
    a second. Rows arrive grouped by category, so a subset stops being read
    once every category it is needed for has gone by: the five text
    categories all sit in roughly the first 65 of 110 pages. Requests go one
    after another, a second apart: Hugging Face answers 429 after about 40
    quick anonymous requests, and the home router refuses bursts anyway.

    Every category read to its end is kept even if a later page fails; the
    tables that did not arrive are named in `missing`, and `refresh` fills
    them from the previous copy. Raises RuntimeError only if nothing arrived.
    """
    get = get or (lambda url: _get_json(url))   # looked up late, so tests can stub it
    wanted: dict[str, set[str]] = {}
    for subset, category in _needed_tables():
        wanted.setdefault(subset, set()).add(category)

    tables: dict[str, list] = {}
    published, missing, last_error = "", [], ""
    for subset, categories in sorted(wanted.items()):
        subset_rows: dict[str, list] = {}
        finished: set[str] = set()
        dates: list[str] = []
        try:
            _page_subset(get, subset, categories, attempts, backoff, pause, sleep,
                         subset_rows, finished, dates)
        except RuntimeError as exc:
            last_error = str(exc)
        published = max([published, *dates])
        for category in categories:
            table = f"{subset}/{category}"
            if category in finished and table in subset_rows:
                tables[table] = subset_rows[table]
            else:
                missing.append(table)

    if not tables:
        raise RuntimeError(last_error or "the leaderboard service returned no rows")
    return {"source": SOURCE, "license": LICENSE, "published": published,
            "fetched": datetime.now().isoformat(timespec="seconds"),
            "categories": tables, "missing": missing}


def _page_subset(get, subset, categories, attempts, backoff, pause, sleep,
                 tables, finished, dates) -> None:
    """Read one subset into `tables` until all `categories` have gone by.

    `finished` collects the categories read to their end and `dates` the
    publish dates seen, both filled as it goes, so a caller keeps them when a
    later page fails.
    """
    offset, total, current = 0, None, None
    while (total is None or offset < total) and finished != categories:
        params = urllib.parse.urlencode({
            "dataset": DATASET, "config": subset, "split": "latest",
            "offset": offset, "length": 100,
        })
        if offset:
            sleep(pause)
        page = _with_retries(lambda: get(f"{ROWS_API}?{params}"),
                             attempts, backoff, sleep)
        total = int(page.get("num_rows_total") or 0)
        rows = [item.get("row", {}) for item in page.get("rows", [])]
        if not rows:
            finished.add(current)             # the file ended mid-run
            break
        for row in rows:
            category = row.get("category")
            if category != current:
                if current in categories:
                    finished.add(current)     # its run is over; the data is grouped
                current = category
            if category in categories and row.get("organization") in ORGANISATIONS:
                tables.setdefault(f"{subset}/{category}", []).append(_compact(row))
                dates.append(str(row.get("leaderboard_publish_date") or ""))
        offset += len(rows)
    if total is not None and offset >= total:
        finished.add(current)                 # the last run ends with the file


def _compact(row: Mapping) -> list:
    return [row.get("model_name", ""), round(float(row.get("rating") or 0), 1),
            round(float(row.get("rating_lower") or 0), 1),
            round(float(row.get("rating_upper") or 0), 1),
            int(row.get("vote_count") or 0), row.get("organization", "")]


def _with_retries(call, attempts, backoff, sleep):
    last = None
    for attempt in range(attempts):
        try:
            page = call()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:               # retrying only extends the limit
                raise RuntimeError("rate limited by Hugging Face (HTTP 429)") from exc
            last = str(exc)
        except Exception as exc:              # network, bad JSON
            last = str(exc)
        else:
            if "error" not in page:
                return page
            last = str(page["error"])        # "the dataset index is loading"
        if attempt + 1 < attempts:
            sleep(backoff * (attempt + 1))
    raise RuntimeError(f"leaderboard service unavailable: {last}")


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Sentinel"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# ── Loading ──────────────────────────────────────────────────────────────────

def _read(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("categories") else None


def load(cache_file: Path, snapshot_file: Path = SNAPSHOT_FILE) -> RatingTable:
    """The cached fetch if there is one, else the shipped snapshot."""
    cached = _read(cache_file)
    if cached:
        return RatingTable(cached, origin="cache")
    shipped = _read(snapshot_file)
    return RatingTable(shipped, origin="snapshot") if shipped else RatingTable()


def is_stale(cache_file: Path, now: datetime | None = None) -> bool:
    """Whether the cached ratings are missing or more than a day old."""
    return _older_than_max_age(cache_file, now)


def _older_than_max_age(cache_file: Path, now: datetime | None = None) -> bool:
    cached = _read(cache_file)
    if not cached:
        return True
    try:
        fetched = datetime.fromisoformat(cached.get("fetched", ""))
    except ValueError:
        return True
    return (now or datetime.now()) - fetched > MAX_AGE


def refresh(cache_file: Path, **kwargs) -> RatingTable:
    """Fetch, cache and return fresh ratings. Blocking; run it off the UI thread."""
    data = fetch_live(**kwargs)
    cache_file = Path(cache_file)
    if data.get("missing"):
        # Keep the previous copy of any table that did not arrive this time.
        previous = _read(cache_file) or _read(SNAPSHOT_FILE) or {}
        for table in data["missing"]:
            rows = (previous.get("categories") or {}).get(table)
            if rows:
                data["categories"].setdefault(table, rows)
        data["published"] = data["published"] or str(previous.get("published", ""))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(cache_file)
    return RatingTable(data, origin="live")


if __name__ == "__main__" and "--snapshot" in sys.argv:
    refresh(SNAPSHOT_FILE)
    print(f"Wrote {SNAPSHOT_FILE}")
