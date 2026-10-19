"""Provider registry — one spec per provider, shared by Sentinel and Imprint.

This file exists twice in the lab workspace, byte for byte:

    sentinel/services/providers.py
    imprint/services/providers.py

`tests/test_providers_drift.py` in each repo fails the build if the copies
differ, so a change here lands in both in the same session.

Why it exists: the apps used to ask "is this Ollama?" by comparing the
provider name to the string "ollama" in about sixty places. That made a second
local runtime impossible — it would have been priced as unknown, scored as a
cloud call and put behind the paid-request dialog. Every such check now asks
the spec (`is_local(name)`), so a provider is local because its spec says so.

The rules the registry carries:

* A local provider's price is known and zero. A zero price for a **cloud**
  provider still means *unknown* and is never treated as free.
* Local never means "no key needed to be careful": nothing here ever sends a
  cloud key to a local endpoint (`key_name` is empty for local providers).
* An unknown provider name is neither local nor cloud — `is_local` is False.

Pure functions only, standard library only, no Qt, no network.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

LOCAL = "local"
CLOUD = "cloud"

PROTOCOL_OPENAI = "openai"     # OpenAI-compatible /v1 chat completions
PROTOCOL_OLLAMA = "ollama"     # Ollama's own /api/chat
PROTOCOL_NATIVE = "native"     # a vendor SDK with its own shape

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_LMSTUDIO_URL = "http://localhost:1234/v1"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str                  # LOCAL or CLOUD
    protocol: str              # PROTOCOL_*
    base_url: str              # default endpoint; "" when an SDK owns it
    key_name: str              # environment variable of the API key; "" if none
    label: str = ""

    @property
    def is_local(self) -> bool:
        return self.kind == LOCAL

    @property
    def is_cloud(self) -> bool:
        return self.kind == CLOUD


REGISTRY: dict[str, ProviderSpec] = {s.name: s for s in (
    ProviderSpec("ollama", LOCAL, PROTOCOL_OLLAMA, DEFAULT_OLLAMA_HOST, "",
                 "Ollama"),
    ProviderSpec("lmstudio", LOCAL, PROTOCOL_OPENAI, DEFAULT_LMSTUDIO_URL, "",
                 "LM Studio"),
    ProviderSpec("openai", CLOUD, PROTOCOL_OPENAI, "https://api.openai.com/v1",
                 "OPENAI_API_KEY", "OpenAI"),
    ProviderSpec("deepseek", CLOUD, PROTOCOL_OPENAI, "https://api.deepseek.com",
                 "DEEPSEEK_API_KEY", "DeepSeek"),
    ProviderSpec("kimi", CLOUD, PROTOCOL_OPENAI, "https://api.moonshot.ai/v1",
                 "KIMI_API_KEY", "Kimi"),
    ProviderSpec("gemini", CLOUD, PROTOCOL_NATIVE, "", "GEMINI_API_KEY",
                 "Gemini"),
    ProviderSpec("anthropic", CLOUD, PROTOCOL_NATIVE, "",
                 "ANTHROPIC_API_KEY", "Anthropic"),
    ProviderSpec("qwen", CLOUD, PROTOCOL_OPENAI, "", "DASHSCOPE_API_KEY",
                 "Qwen"),
)}

# Display / iteration order: local runtimes first, as the pickers always had.
SUPPORTED: tuple[str, ...] = tuple(REGISTRY)
LOCAL_PROVIDERS: tuple[str, ...] = tuple(
    n for n, s in REGISTRY.items() if s.is_local)
CLOUD_PROVIDERS: frozenset[str] = frozenset(
    n for n, s in REGISTRY.items() if s.is_cloud)


def get(name: str | None) -> ProviderSpec | None:
    """The spec for a provider name (case-insensitive), or None if unknown."""
    return REGISTRY.get(str(name or "").strip().casefold())


def is_local(name: str | None) -> bool:
    spec = get(name)
    return spec is not None and spec.is_local


def is_cloud(name: str | None) -> bool:
    spec = get(name)
    return spec is not None and spec.is_cloud


def protocol(name: str | None) -> str | None:
    spec = get(name)
    return spec.protocol if spec else None


def expand_allowed(allowed) -> set[str]:
    """An allowed_providers list, with local runtimes treated as one group.

    Agents and tools list "ollama" to mean "this can run on the user's own
    machine". A second local runtime is the same permission, so a list that
    names any local provider admits every local provider. Cloud providers are
    never expanded: each stays an explicit choice.
    """
    names = {str(a).strip().casefold() for a in (allowed or [])}
    if names & set(LOCAL_PROVIDERS):
        names |= set(LOCAL_PROVIDERS)
    return names


def ollama_host(environ=None) -> str:
    """Where the Ollama daemon is, honouring OLLAMA_HOST like `ollama` does.

    OLLAMA_HOST may be `host`, `host:port`, `:port` or a full URL.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get("OLLAMA_HOST", "") or "").strip()
    if not raw:
        return DEFAULT_OLLAMA_HOST
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "http://" + raw
    rest = raw.split("://", 1)[1]
    hostport = rest.split("/", 1)[0]
    if ":" not in hostport:
        raw = raw.rstrip("/") + ":11434"
    elif hostport.startswith(":"):
        raw = raw.replace("://:", "://127.0.0.1:", 1)
    # 0.0.0.0 is what a server binds to, not an address a client can dial.
    return raw.replace("//0.0.0.0", "//127.0.0.1", 1).rstrip("/")


def lmstudio_url(environ=None) -> str:
    """LM Studio's OpenAI-compatible base URL (LMSTUDIO_URL overrides)."""
    env = os.environ if environ is None else environ
    return (str(env.get("LMSTUDIO_URL", "") or "").strip().rstrip("/")
            or DEFAULT_LMSTUDIO_URL)


def base_url(name: str, environ=None) -> str:
    """The endpoint to use for a provider, after environment overrides."""
    if name == "ollama":
        return ollama_host(environ)
    if name == "lmstudio":
        return lmstudio_url(environ)
    spec = get(name)
    return spec.base_url if spec else ""


# ── Uncensored / abliterated tag ─────────────────────────────────────────────
# Models with their safety tuning removed are offered in every agent (the
# owner's decision, 2026-10-19) and must ALWAYS be tagged wherever a model is
# named. The tag is derived from the model's name and, where a runtime reports
# one, its publisher — never typed in by hand, so a new download is tagged
# without anyone remembering to.

ABLITERATED = "abliterated"
UNCENSORED = "uncensored"

_ABLITERATED_WORDS = re.compile(
    r"abliterat|ablit(?![a-z])|lorablat|heretic", re.I)
_UNCENSORED_WORDS = re.compile(
    r"uncensor|uncen(?![a-z])|unfilter|unalign|jailbr|nsfw|\bdolphin|"
    r"\blexi\b|\bhermes[-_ ]?\d*[-_ ]?uncen", re.I)
# Publishers whose releases are uncensored by design.
_UNCENSORED_PUBLISHERS = frozenset({
    "cognitivecomputations", "orenguteng", "failspy", "huihui-ai",
})


def uncensored_tag(model: str | None, publisher: str | None = None) -> str | None:
    """'abliterated', 'uncensored' or None for a model id / file name.

    Abliterated wins when both apply — it says *how* the safety tuning was
    removed, which the plain word does not.
    """
    text = str(model or "")
    if _ABLITERATED_WORDS.search(text):
        return ABLITERATED
    if _UNCENSORED_WORDS.search(text):
        return UNCENSORED
    pub = str(publisher or "").strip().casefold().rsplit("/", 1)[-1]
    if pub in _UNCENSORED_PUBLISHERS:
        # huihui-ai and failspy publish abliterations; the others do not
        # name the method, so only the first two earn the stronger word.
        return ABLITERATED if pub in {"huihui-ai", "failspy"} else UNCENSORED
    return None


def tagged(model: str, publisher: str | None = None) -> str:
    """The model name as a picker shows it: with its tag, if it has one."""
    tag = uncensored_tag(model, publisher)
    return f"{model} · {tag.upper()}" if tag else model


# ── Does a local model fit in memory? ────────────────────────────────────────

OVERHEAD_FACTOR = 1.2          # weights plus KV cache and runtime
HEADROOM_GB = 3.0              # left for the OS and the app itself


def fit_check(size_bytes: float, available_bytes: float,
              total_bytes: float) -> dict:
    """Weigh a model file against this machine's memory.

    level 'ok'       fits in the memory that is free right now;
    level 'tight'    fits on this machine but not in what is free now
                     ("too big right now" — close other apps);
    level 'too_big'  would not fit even with everything else closed.
    """
    needed = size_bytes * OVERHEAD_FACTOR
    if needed > total_bytes - HEADROOM_GB * 1e9:
        level = "too_big"
    elif needed > available_bytes:
        level = "tight"
    else:
        level = "ok"
    return {
        "level": level,
        "needed_gb": needed / 1e9,
        "available_gb": available_bytes / 1e9,
        "total_gb": total_bytes / 1e9,
    }
