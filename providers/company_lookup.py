"""Company OSINT provider using GLEIF's public legal-entity registry.

Only organization records are queried.  Trace deliberately does not use
people-search or data-broker services for person and phone targets.

Opt-in (``offshore_leaks=True``, used by Bloodhound):
  • ICIJ Offshore Leaks → entities, intermediaries and addresses named in the
    Panama, Paradise, Pandora and earlier leak investigations, matched by name
    through ICIJ's reconciliation API (no key)

Key-gated (set in .env; skipped and reported as skipped without it):
  • OpenSanctions → OPENSANCTIONS_API_KEY — sanctions lists, politically
    exposed persons and other watchlists in one search. Every API call needs a
    key; the data is free for non-commercial use, commercial use needs their
    licence. Opt-in (``sanctions=True``).
"""

import os

import requests
from dotenv import load_dotenv

from services.runtime_paths import user_data_base

load_dotenv(user_data_base() / ".env", override=False)
OPENSANCTIONS_KEY = os.getenv("OPENSANCTIONS_API_KEY", "")
OPENSANCTIONS_URL = "https://api.opensanctions.org/search/default"


GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
ICIJ_URL = "https://offshoreleaks.icij.org/api/v1/reconcile"


def _name(value) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return value if isinstance(value, str) else None


def _address(value) -> dict:
    value = value if isinstance(value, dict) else {}
    return {
        "lines": value.get("addressLines") or [],
        "city": value.get("city"),
        "region": value.get("region"),
        "postal_code": value.get("postalCode"),
        "country": value.get("country"),
    }


def _record(item: dict) -> dict:
    attributes = item.get("attributes") or {}
    entity = attributes.get("entity") or {}
    registration = attributes.get("registration") or {}
    other_names = entity.get("otherNames") or []
    return {
        "legal_name": _name(entity.get("legalName")),
        "other_names": [name for name in (_name(value) for value in other_names) if name][:10],
        "lei": attributes.get("lei") or item.get("id"),
        "entity_status": entity.get("status"),
        "registration_status": registration.get("status"),
        "jurisdiction": entity.get("jurisdiction"),
        "registered_as": entity.get("registeredAs"),
        "registered_at": (entity.get("registeredAt") or {}).get("id"),
        "legal_address": _address(entity.get("legalAddress")),
        "headquarters_address": _address(entity.get("headquartersAddress")),
        "initial_registration_date": registration.get("initialRegistrationDate"),
        "last_update_date": registration.get("lastUpdateDate"),
        "next_renewal_date": registration.get("nextRenewalDate"),
    }


def _offshore_leaks(company: str) -> dict:
    """ICIJ Offshore Leaks name matches, best first.

    A match is a similar name in leaked records, not proof it is the same
    organisation — and being named in the leaks is not itself wrongdoing.
    """
    try:
        response = requests.post(
            ICIJ_URL,
            json={"queries": {"q0": {"query": company}}},
            timeout=20,
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
        )
        if response.status_code not in (200, 201):
            return {"error": f"ICIJ Offshore Leaks HTTP {response.status_code}"}
        matches = ((response.json().get("q0") or {}).get("result")) or []
        return {
            "matches_shown": min(len(matches), 10),
            "matches": [{
                "name": match.get("name"),
                "kind": ", ".join(t.get("name", "") for t in match.get("types") or []),
                "source": match.get("description"),
                "similarity": round(match.get("score") or 0),
                "exact": bool(match.get("match")),
                "url": f"https://offshoreleaks.icij.org/nodes/{match.get('id')}",
            } for match in matches[:10]],
            "note": ("Name similarity only. Appearing in the leaks is not evidence of "
                     "wrongdoing; many offshore structures are legal."),
        }
    except Exception as error:
        return {"error": str(error)[:300]}


def _opensanctions(company: str) -> dict:
    """OpenSanctions watchlist matches for a name, best first."""
    if not OPENSANCTIONS_KEY:
        return {"status": "skipped",
                "detail": "OPENSANCTIONS_API_KEY is not set (opensanctions.org/api)."}
    try:
        response = requests.get(
            OPENSANCTIONS_URL,
            params={"q": company, "limit": 10},
            timeout=15,
            headers={"Authorization": f"ApiKey {OPENSANCTIONS_KEY}",
                     "User-Agent": "Sentinel-OSINT/2.0"},
        )
        if response.status_code in (401, 403):
            return {"error": "OpenSanctions rejected the API key"}
        if response.status_code == 429:
            return {"error": "OpenSanctions rate limit reached; try again later"}
        if response.status_code != 200:
            return {"error": f"OpenSanctions HTTP {response.status_code}"}
        payload = response.json()
        results = payload.get("results") or []
        total = payload.get("total") or {}
        return {
            "total_matches": total.get("value", len(results)) if isinstance(total, dict) else total,
            "matches": [{
                "name": item.get("caption"),
                "kind": item.get("schema"),
                "listed": bool(item.get("target")),
                "datasets": (item.get("datasets") or [])[:8],
                "countries": ((item.get("properties") or {}).get("country") or [])[:5],
                "url": f"https://www.opensanctions.org/entities/{item.get('id')}/",
            } for item in results[:10]],
            "note": ("A text match, not a confirmed identity: compare country, "
                     "registration and dates before treating a hit as the target. "
                     "listed=true means the entity itself is on a sanctions or "
                     "watch list; false means it is only related to one."),
        }
    except Exception as error:
        return {"error": str(error)[:300]}


def lookup(company: str, *, offshore_leaks: bool = False, sanctions: bool = False,
           on_progress=None, should_stop=None) -> dict:
    """Search GLEIF by company name and return compact legal-entity records.

    ``offshore_leaks=True`` also checks the name against ICIJ Offshore Leaks;
    ``sanctions=True`` also screens it with OpenSanctions (skipped without a key).
    """
    company = company.strip()
    result = {
        "type": "company",
        "query": company,
        "sources_contacted": [],
        "sources_skipped": [],
    }
    if not company:
        result["error"] = "Empty company name — skipping live lookup."
        return result
    if should_stop and should_stop():
        result["cancelled"] = True
        return result

    label = "GLEIF Legal Entity Index"
    if on_progress:
        on_progress(label, "checking")
    try:
        response = requests.get(
            GLEIF_URL,
            params={"filter[fulltext]": company, "page[size]": 10},
            timeout=12,
            headers={
                "Accept": "application/vnd.api+json",
                "User-Agent": "Sentinel-OSINT/2.0",
            },
        )
        if response.status_code == 200:
            payload = response.json()
            records = [_record(item) for item in (payload.get("data") or [])[:10]]
            pagination = (payload.get("meta") or {}).get("pagination") or {}
            result["legal_entities"] = {
                "total_matches": pagination.get("total", len(records)),
                "records_shown": len(records),
                "records": records,
                "coverage_note": (
                    "GLEIF covers legal entities with an LEI; absence is not proof "
                    "that an organization does not exist."
                ),
            }
        elif response.status_code == 429:
            result["error"] = "GLEIF is temporarily rate-limiting searches. Try again later."
        else:
            result["error"] = f"GLEIF returned HTTP {response.status_code}."
    except requests.exceptions.Timeout:
        result["error"] = "GLEIF did not respond within 12 seconds."
    except Exception as error:
        result["error"] = str(error)[:300]

    status = "error" if result.get("error") else "checked"
    result["sources_contacted"].append({"source": label, "status": status})
    if on_progress:
        on_progress(label, status)

    if sanctions:
        if should_stop and should_stop():
            result["cancelled"] = True
            return result
        label = "OpenSanctions"
        if on_progress:
            on_progress(label, "checking")
        result["sanctions"] = _opensanctions(company)
        status = result["sanctions"].get("status") or (
            "error" if result["sanctions"].get("error") else "checked")
        destination = "sources_skipped" if status == "skipped" else "sources_contacted"
        result[destination].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)

    if offshore_leaks:
        if should_stop and should_stop():
            result["cancelled"] = True
            return result
        label = "ICIJ Offshore Leaks"
        if on_progress:
            on_progress(label, "checking")
        result["offshore_leaks"] = _offshore_leaks(company)
        status = "error" if result["offshore_leaks"].get("error") else "checked"
        result["sources_contacted"].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)
    return result
