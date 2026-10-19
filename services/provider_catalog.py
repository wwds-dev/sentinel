"""Provider identifiers and billing metadata shared by runtime and UI."""

from dataclasses import dataclass

SUPPORTED_PROVIDERS = (
    "ollama", "openai", "deepseek", "kimi", "gemini", "anthropic", "qwen",
)

LOCAL_PROVIDER = "ollama"
# What Local only mode runs when the user has not saved a default model.
DEFAULT_LOCAL_MODEL = "deepseek-r1:8b"

CLOUD_PROVIDERS = frozenset(provider for provider in SUPPORTED_PROVIDERS if provider != "ollama")


@dataclass(frozen=True)
class ProviderMetadata:
    location: str                 # local, cloud, or unknown
    pricing_status: str           # free_local, paid, or unknown


# Billing state is explicit.  In particular, a missing price must never be
# interpreted as free: cloud APIs are metered even when an exact model rate is
# unavailable or depends on the customer's provider agreement.
PROVIDER_METADATA = {
    "ollama": ProviderMetadata("local", "free_local"),
    **{
        provider: ProviderMetadata("cloud", "paid")
        for provider in CLOUD_PROVIDERS
    },
}


def provider_metadata(provider: str) -> ProviderMetadata:
    """Return conservative metadata for an arbitrary provider identifier."""
    return PROVIDER_METADATA.get(provider, ProviderMetadata("unknown", "unknown"))
