"""Copy every statically seeded price from services/database.py into config/pricing.json.

database._seeded_prices() is the source of truth for the rows it lists;
pricing.json also carries rows the seed does not (gpt-4o-mini, ...), which
this leaves alone. Rows whose rate depends on the date
(database.DATE_DEPENDENT_PRICES) stay in the seed only: a number written here
would go stale the day the price changes. tests/test_price_resolution.py fails
when the two disagree; run this to fix it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import database  # noqa: E402

PATH = ROOT / "config" / "pricing.json"


def main() -> int:
    data = json.loads(PATH.read_text())
    added = 0
    for backend, model, inp, out in database._seeded_prices():
        if (backend, model) in database.DATE_DEPENDENT_PRICES:
            continue
        row = data.setdefault(backend, {}).setdefault(model, {})
        new = {"input_per_1m_usd": inp, "output_per_1m_usd": out}
        if {k: row.get(k) for k in new} != new:
            row.update(new)
            added += 1
    PATH.write_text(json.dumps(data, indent=2) + "\n")
    print(f"{added} row(s) written to {PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
