"""Ask each provider whether Sentinel's recommended models still exist.

The test suite deliberately runs on every client's offline KNOWN_MODELS
(tests/conftest.py), so it cannot notice a provider renaming or retiring a
model -- which is how Trace's recommended deepseek-v4-flash quietly stopped
matching anything in Sep 2026. Run this by hand, before a release or when a
panel opens on the wrong model:

    .venv/bin/python scripts/check_live_models.py

It only *lists* models (free on every provider) and never sends a prompt.
Keys come from the same `.env` the app reads; a provider without one is
skipped. Matching is the app's own rule (`find_model`), so "found" here means
the panel would select it.

Exit status: 0 every recommended model found, 1 at least one missing, 2 none
missing but a provider with a key (or Ollama) could not be reached. Providers
skipped for want of a key do not affect it.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from services import settings_store  # noqa: E402
from services.runtime_paths import user_data_base  # noqa: E402

load_dotenv(user_data_base() / ".env")

from services.anthropic_client import AnthropicClientWrapper  # noqa: E402
from services.deepseek_client import DeepSeekClientWrapper  # noqa: E402
from services.gemini_client import GeminiClientWrapper  # noqa: E402
from services.kimi_client import KimiClientWrapper  # noqa: E402
from services.model_recommendations import (  # noqa: E402
    AGENT_RECOMMENDATIONS, TASK_RECOMMENDATIONS, find_model,
)
from services.ollama_client import OllamaClient  # noqa: E402
from services.openai_client import OpenAIClientWrapper  # noqa: E402
from services.qwen_client import QwenClientWrapper  # noqa: E402
from services.tool_catalog import BUILTIN_TOOLS  # noqa: E402

CLOUD_CLIENTS = {
    "anthropic": AnthropicClientWrapper,
    "deepseek": DeepSeekClientWrapper,
    "gemini": GeminiClientWrapper,
    "kimi": KimiClientWrapper,
    "openai": OpenAIClientWrapper,
    "qwen": QwenClientWrapper,
}


def recommended_models() -> dict[str, dict[str, list[str]]]:
    """provider -> model -> the places that recommend it."""
    wanted: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for agent, rec in AGENT_RECOMMENDATIONS.items():
        wanted[rec.provider][rec.model].append(f"agent {agent}")
    for task, rec in TASK_RECOMMENDATIONS.items():
        wanted[rec.provider][rec.model].append(f"task {task}")
    for tool, spec in BUILTIN_TOOLS.items():
        if spec.get("recommended_model"):
            wanted[spec["recommended_provider"]][spec["recommended_model"]].append(
                f"tool {tool}")
    base = user_data_base()
    settings = settings_store.load_settings(
        settings_store.defaults_path(base), settings_store.override_path(base))
    for key, model in settings.items():
        if key.startswith("default_model_") and model:
            wanted[key.removeprefix("default_model_")][model].append(
                "saved Chat default")
    return wanted


SKIPPED = "skipped"


def live_models(provider: str) -> tuple[list[str] | str | None, str]:
    """(models, note). None: could not be asked. SKIPPED: no key to ask with."""
    if provider == "ollama":
        try:
            return OllamaClient().list_models(), "installed locally"
        except RuntimeError:
            return None, "Ollama is not running"

    cls = CLOUD_CLIENTS.get(provider)
    if cls is None:
        return None, "no client for this provider"
    if not cls.key_available():
        return SKIPPED, "no API key, skipped"
    client = cls()
    offline = list(cls.KNOWN_MODELS)
    # Every client answers a failed or empty listing with self.KNOWN_MODELS.
    # Emptying it on this instance turns that fallback into an empty answer,
    # so "the API listed these" cannot be confused with "we fell back".
    client.KNOWN_MODELS = []
    models = client.list_models()
    if not models:
        return None, "API unreachable or listed nothing"
    stale = [m for m in offline if find_model(models, m) < 0]
    note = "live"
    if stale:
        note += "; offline list has entries the API no longer serves: " + ", ".join(stale)
    return models, note


def main() -> int:
    missing = unreachable = 0
    for provider, models in sorted(recommended_models().items()):
        available, note = live_models(provider)
        print(f"{provider}: {note}")
        if available is None:
            unreachable += 1
        for model, used_by in sorted(models.items()):
            where = ", ".join(used_by)
            if available is None or available == SKIPPED:
                print(f"  ?  {model}  ({where})")
                continue
            index = find_model(available, model)
            if index < 0:
                missing += 1
                hint = f"  run: ollama pull {model}" if provider == "ollama" else ""
                print(f"  MISSING  {model}  ({where}){hint}")
            elif available[index] != model:
                print(f"  ok  {model} -> {available[index]}  ({where})")
            else:
                print(f"  ok  {model}  ({where})")

    if missing:
        print(f"\n{missing} model(s) missing. For a cloud provider, update the "
              "recommendation and the client's KNOWN_MODELS together; for a saved "
              "Chat default, pick another model in Chat; for Ollama, pull it.")
        return 1
    if unreachable:
        print("\nNothing missing among the providers that could be asked.")
        return 2
    print("\nEvery recommended model is available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
