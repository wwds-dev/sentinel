"""Provider identifiers and billing metadata shared by runtime and UI.

The registry itself is `services/providers.py` (a byte-identical copy lives in
Imprint). This module keeps the names the rest of Sentinel already imports and
derives them from the registry, so there is one list of providers to edit.
"""

from dataclasses import dataclass

from services import providers

SUPPORTED_PROVIDERS = providers.SUPPORTED

# The local runtime used when nothing else is chosen. Other local providers
# (lmstudio) are listed in LOCAL_PROVIDERS; ask `providers.is_local(name)`
# rather than comparing against this.
LOCAL_PROVIDER = "ollama"
LOCAL_PROVIDERS = providers.LOCAL_PROVIDERS
# What Local only mode runs when the user has not saved a default model.
DEFAULT_LOCAL_MODEL = "deepseek-r1:8b"

CLOUD_PROVIDERS = providers.CLOUD_PROVIDERS


@dataclass(frozen=True)
class ProviderMetadata:
    location: str                 # local, cloud, or unknown
    pricing_status: str           # free_local, paid, or unknown


# Billing state is explicit.  In particular, a missing price must never be
# interpreted as free: cloud APIs are metered even when an exact model rate is
# unavailable or depends on the customer's provider agreement.
PROVIDER_METADATA = {
    name: ProviderMetadata(spec.kind, "free_local" if spec.is_local else "paid")
    for name, spec in providers.REGISTRY.items()
}


def provider_metadata(provider: str) -> ProviderMetadata:
    """Return conservative metadata for an arbitrary provider identifier."""
    return PROVIDER_METADATA.get(provider, ProviderMetadata("unknown", "unknown"))
