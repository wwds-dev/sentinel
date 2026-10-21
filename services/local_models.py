"""Local model inventory — what runs on this Mac, shared by Sentinel and Imprint.

This file exists twice in the lab workspace, byte for byte (see
`tests/test_providers_drift.py`). Port any change to both.

One cached snapshot of LM Studio's and Ollama's models, their state and the
machine's memory. **Discovery does I/O and belongs on a worker thread**:
`LocalInventory.refresh()` is the only method that talks to a server or the
disk, and the UI only ever calls `snapshot()`, which returns the last result
and never fetches. That is the lab's rule for the UI thread (a blocked event
loop shows as a white window that ignores the close button).

The pure helpers below turn a snapshot into what the pickers and the Local
models settings page show: sections ("On this Mac · LM Studio", "On this Mac ·
Ollama", "Cloud"), a detail line per row (quant, state, size, rating, €0) and
the uncensored/abliterated tag. Measuring a local model is a later phase:
`rating_text` answers "unrated" until `set_rating_lookup` is given a source.

Standard library only; the clients are passed in.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace

from services import providers

LMSTUDIO = "lmstudio"
OLLAMA = "ollama"

SECTION_LABELS = {
    LMSTUDIO: "On this Mac · LM Studio",
    OLLAMA: "On this Mac · Ollama",
}
CLOUD_SECTION = "Cloud"
SECTION_ORDER = (SECTION_LABELS[LMSTUDIO], SECTION_LABELS[OLLAMA], CLOUD_SECTION)

# What a row says about itself.
STATE_LOADED = "loaded"
STATE_ON_DISK = "on disk, loads on first use"
STATE_SERVER_OFF = "on disk, server off"
STATE_TOO_BIG = "too big now"
STATE_INSTALLED = "installed"

CACHE_SECONDS = 30.0
FREE_LABEL = "€0"


@dataclass(frozen=True)
class LocalModel:
    provider: str                 # "lmstudio" | "ollama"
    id: str                       # what a request names
    name: str                     # what a person reads
    quant: str = ""
    size_bytes: int | None = None
    loaded: bool = False
    publisher: str = ""
    max_context: int | None = None
    server_up: bool = True
    fit: str = ""                 # providers.fit_check level, "" if unknown

    @property
    def tag(self) -> str | None:
        return providers.uncensored_tag(self.id or self.name, self.publisher)

    @property
    def size_gb(self) -> float | None:
        return None if self.size_bytes is None else self.size_bytes / 1e9

    @property
    def state(self) -> str:
        if self.fit in {"tight", "too_big"}:
            return STATE_TOO_BIG
        if self.loaded:
            return STATE_LOADED
        if self.provider == OLLAMA:
            return STATE_INSTALLED
        return STATE_ON_DISK if self.server_up else STATE_SERVER_OFF

    @property
    def section(self) -> str:
        return SECTION_LABELS.get(self.provider, self.provider)


@dataclass(frozen=True)
class Snapshot:
    models: tuple[LocalModel, ...] = ()
    lmstudio_up: bool = False
    lms_found: bool = False
    ollama_up: bool = False
    ram_free_bytes: float = 0.0
    ram_total_bytes: float = 0.0
    taken_at: float = 0.0
    errors: tuple[str, ...] = ()

    def of(self, provider: str) -> tuple[LocalModel, ...]:
        return tuple(m for m in self.models if m.provider == provider)

    def find(self, provider: str, model_id: str) -> LocalModel | None:
        for m in self.models:
            if m.provider == provider and m.id == model_id:
                return m
        return None

    @property
    def ram_free_gb(self) -> float:
        return self.ram_free_bytes / 1e9

    @property
    def ram_total_gb(self) -> float:
        return self.ram_total_bytes / 1e9


EMPTY = Snapshot()


# ── Rating hook (Phase 3 fills it) ───────────────────────────────────────────

_RATING_LOOKUP = None


def set_rating_lookup(lookup) -> None:
    """lookup(provider, model_id) -> short text such as 'measured 1281', or None."""
    global _RATING_LOOKUP
    _RATING_LOOKUP = lookup


def rating_text(model: LocalModel) -> str:
    if _RATING_LOOKUP is not None:
        try:
            text = _RATING_LOOKUP(model.provider, model.id)
        except Exception:
            text = None
        if text:
            return str(text)
    return "unrated"


# ── Discovery (I/O — call from a worker thread) ──────────────────────────────

def _memory() -> tuple[float, float]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return float(vm.available), float(vm.total)
    except Exception:
        return 0.0, 0.0


def discover(lmstudio, ollama, memory=_memory, lms_binary=None) -> Snapshot:
    """Read both runtimes and the memory figures. Blocking; never raises."""
    errors: list[str] = []
    free, total = memory()
    models: list[LocalModel] = []

    lm_up = False
    try:
        lm_up = bool(lmstudio.server_up())
        for row in lmstudio.model_details():
            models.append(LocalModel(
                provider=LMSTUDIO, id=row["id"], name=row.get("name") or row["id"],
                quant=row.get("quant") or "", size_bytes=row.get("size_bytes"),
                loaded=row.get("state") == "loaded",
                publisher=row.get("publisher") or "",
                max_context=row.get("max_context"), server_up=lm_up))
    except Exception as exc:                      # a broken runtime is a row, not a crash
        errors.append(f"LM Studio: {exc}")

    ol_up = False
    try:
        details = ollama.model_details()
        try:
            names = list(ollama.list_models())
            ol_up = True
        except Exception:
            names, ol_up = list(details), bool(details)
        try:
            resident = {str(m.get("name", "")) for m in ollama.loaded_models()}
        except Exception:
            resident = set()
        for name in names:
            row = details.get(name) or {}
            models.append(LocalModel(
                provider=OLLAMA, id=name, name=name,
                quant=str(row.get("quantization") or ""),
                size_bytes=row.get("size_bytes") or None,
                loaded=name in resident, server_up=True))
    except Exception as exc:
        errors.append(f"Ollama: {exc}")

    if total:
        models = [_with_fit(m, free, total) for m in models]
    if lms_binary is None:
        try:
            from services import lmstudio_client
            lms_binary = lmstudio_client.lms_binary
        except Exception:
            lms_binary = lambda: None             # noqa: E731
    return Snapshot(models=tuple(models), lmstudio_up=lm_up,
                    lms_found=bool(lms_binary()), ollama_up=ol_up,
                    ram_free_bytes=free, ram_total_bytes=total,
                    taken_at=time.time(), errors=tuple(errors))


def _with_fit(model: LocalModel, free: float, total: float) -> LocalModel:
    if not model.size_bytes or model.loaded:      # a resident model already fits
        return model
    level = providers.fit_check(model.size_bytes, free, total)["level"]
    return replace(model, fit=level)


class LocalInventory:
    """The last discovery result, safe to read from any thread."""

    def __init__(self, ttl: float = CACHE_SECONDS):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._snapshot = EMPTY
        self._seen = False

    def snapshot(self) -> Snapshot:
        """The cached result. Never fetches."""
        with self._lock:
            return self._snapshot

    @property
    def has_data(self) -> bool:
        with self._lock:
            return self._seen

    def stale(self, now: float | None = None) -> bool:
        with self._lock:
            return (not self._seen
                    or (now if now is not None else time.time())
                    - self._snapshot.taken_at > self.ttl)

    def store(self, snap: Snapshot) -> Snapshot:
        with self._lock:
            self._snapshot, self._seen = snap, True
        return snap

    def refresh(self, lmstudio, ollama, **kwargs) -> Snapshot:
        """Discover and cache. Does I/O: call it from a worker thread only."""
        return self.store(discover(lmstudio, ollama, **kwargs))


# ── What the pickers show ────────────────────────────────────────────────────

def detail_text(model: LocalModel) -> str:
    """'loaded · 12 GB · measured 1281 · €0' — the muted half of a picker row."""
    parts = [model.state]
    if model.size_gb is not None:
        parts.append(f"{model.size_gb:.0f} GB" if model.size_gb >= 10
                     else f"{model.size_gb:.1f} GB")
    parts += [rating_text(model), FREE_LABEL]
    return " · ".join(parts)


def row_label(model: LocalModel) -> str:
    return f"{model.name} · {model.quant}" if model.quant else model.name


def tooltip_text(model: LocalModel, snap: Snapshot) -> str:
    lines = [f"{SECTION_LABELS.get(model.provider, model.provider)}: {model.id}"]
    if model.fit in {"tight", "too_big"} and model.size_bytes:
        need = model.size_bytes * providers.OVERHEAD_FACTOR / 1e9
        lines.append(f"Needs about {need:.0f} GB; {snap.ram_free_gb:.1f} GB free of "
                     f"{snap.ram_total_gb:.0f} GB.")
    if model.state == STATE_SERVER_OFF:
        lines.append("The LM Studio server is off. Start it in Settings → Local "
                     "models; the model loads on first use.")
    elif model.state == STATE_ON_DISK:
        lines.append("Loads on first use, so the first reply is slower.")
    return "\n".join(lines)


def picker_entries(snap: Snapshot, provider: str) -> list[dict]:
    """Rows for one local provider's model list: dicts the combo fills from.

    keys: id, label, section, detail, tag, tooltip, state, fit.
    """
    rows = []
    for model in snap.of(provider):
        rows.append({
            "id": model.id, "label": row_label(model), "section": model.section,
            "detail": detail_text(model), "tag": model.tag,
            "tooltip": tooltip_text(model, snap), "state": model.state,
            "fit": model.fit,
        })
    return rows


def empty_section_note(snap: Snapshot, provider: str) -> str:
    """What to show for a local runtime with nothing in it."""
    if provider == OLLAMA:
        return ("No models · ollama pull …" if snap.ollama_up
                else "Ollama is not running · open Ollama")
    return ("No models · download one in LM Studio" if snap.lmstudio_up
            else "LM Studio server off · Start it in Settings → Local models")


def start_server_hint(snap: Snapshot) -> str:
    """How to turn LM Studio's server on, for a person who cannot click Start."""
    if snap.lmstudio_up:
        return "The LM Studio server is running."
    if snap.lms_found:
        return "Press Start server, or run `lms server start`."
    return ("Open LM Studio → Developer → Start Server. (Install the `lms` tool "
            "with `lms bootstrap` to start it from here.)")


def table_rows(snap: Snapshot, used_by=None) -> list[dict]:
    """Rows for Settings → Local models: model, source, size, rating, used by."""
    used_by = used_by or {}
    out = []
    for model in snap.models:
        out.append({
            "model": row_label(model), "id": model.id, "tag": model.tag,
            "source": "LM Studio" if model.provider == LMSTUDIO else "Ollama",
            "provider": model.provider,
            "size": "?" if model.size_gb is None else f"{model.size_gb:.1f} GB",
            "state": model.state,
            "rating": rating_text(model),
            "used_by": ", ".join(used_by.get((model.provider, model.id), ())) or "—",
        })
    return out


def status_lines(snap: Snapshot) -> dict[str, str]:
    """The three status chips at the top of the page."""
    return {
        "lmstudio": ("LM Studio server running" if snap.lmstudio_up
                     else "LM Studio server off"),
        "ollama": (f"Ollama running · {len(snap.of(OLLAMA))} models"
                   if snap.ollama_up else "Ollama not running"),
        "ram": (f"RAM free {snap.ram_free_gb:.1f} / {snap.ram_total_gb:.0f} GB"
                if snap.ram_total_bytes else "RAM unknown"),
    }


def router_candidates(snap: Snapshot) -> list[LocalModel]:
    """Models a router may consider: every discovered one, from any selection.

    Unrated models stay candidates; the value rule (an unrated model never
    beats a rated one) is the router's, not this module's.
    """
    return list(snap.models)
