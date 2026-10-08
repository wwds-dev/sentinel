"""Which pricing row bills a model: one answer for the bill and the router.

The bill (`UsageTracker.calculate_cost_eur`, which also prices the pre-flight
budget check) and the router (`model_recommendations.blended_price`, through
the lookup the app installs) used to resolve a model differently. The bill
took an exact row or the provider's `default`, so gemini-2.5-flash billed at
the Gemini default of 0/0 and gpt-4o at gpt-4o-mini's rate; the router saw
no price at all for the same models and called them "price unknown". Both
now ask `resolve_price`.

Order: the model's own row, then the row of the model it is a dated snapshot
or alias of (`gpt-4o-2024-08-06` bills as `gpt-4o`), then the provider's
`default`. Only rows with a positive input *and* output rate count; a zero
means unknown, never free. Each provider's `default` holds its dearest
current rate (services/database.py), so a model with no row of its own is
over- rather than under-estimated.

Same order as Imprint's `services/pricing_catalog.resolve_price_row`.
"""

from __future__ import annotations

from typing import Mapping

from services.model_recommendations import Price, PriceLookup
from services.model_watch import canonical


def _positive(row) -> bool:
    try:
        return (float(row["input_per_1m_usd"] or 0) > 0
                and float(row["output_per_1m_usd"] or 0) > 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _blended(row) -> float:
    return (3 * float(row["input_per_1m_usd"]) + float(row["output_per_1m_usd"])) / 4


def resolve_price(rows: Mapping[str, Mapping], model: str):
    """The row in `rows` (one provider's {model: row}) that bills `model`.

    Returns (row, source): source is "exact", "alias" or "default", or
    (None, "unknown") when the provider has no usable row at all.

    Among aliases, the row named by the canonical id itself wins (a dated
    `gpt-4o-2024-08-06` bills as `gpt-4o`, which is what the undated alias
    points at); failing that, the dearest of the snapshots that share it.
    """
    usable = {name: row for name, row in rows.items() if _positive(row)}
    if model in usable:
        return usable[model], "exact"
    base = canonical(model) if model else ""
    if base:
        siblings = {name: row for name, row in usable.items()
                    if name != "default" and canonical(name) == base}
        for name, row in siblings.items():
            if name.lower() == base:
                return row, "alias"
        if siblings:
            name = max(sorted(siblings), key=lambda n: _blended(siblings[n]))
            return siblings[name], "alias"
    if "default" in usable:
        return usable["default"], "default"
    return None, "unknown"


def resolve_price_row(conn, provider: str, model: str):
    """`resolve_price` over the pricing table, for one provider."""
    rows = conn.execute(
        "SELECT model, input_per_1m_usd, cached_input_per_1m_usd, "
        "output_per_1m_usd FROM pricing WHERE backend = ?",
        (provider,),
    ).fetchall()
    return resolve_price({r["model"]: r for r in rows}, model)


def table_lookup(table: Mapping[str, Mapping]) -> PriceLookup:
    """A router price lookup over `UsageTracker.load_pricing()`'s table.

    Read once and held: the router asks for a price for every candidate on
    every route, and the table changes only when Settings → Pricing does.
    """
    def lookup(provider: str, model: str):
        provider_rows = table.get(provider)
        if not isinstance(provider_rows, Mapping):
            return None
        row, source = resolve_price(provider_rows, model)
        if row is None:
            return None
        return Price(row["input_per_1m_usd"], row["output_per_1m_usd"], source)

    return lookup
