"""Company OSINT provider using GLEIF's public legal-entity registry.

Only organization records are queried.  Trace deliberately does not use
people-search or data-broker services for person and phone targets.

Opt-in (``offshore_leaks=True``, used by Bloodhound):
  • ICIJ Offshore Leaks → entities, intermediaries and addresses named in the
    Panama, Paradise, Pandora and earlier leak investigations, matched by name
    through ICIJ's reconciliation API (no key)

Opt-in (``court_records=True``, used by Trace's company lookup and Bloodhound):
  • CourtListener (RECAP) → U.S. federal and state court dockets whose parties
    match the company name, from the Free Law Project's RECAP archive. Uses the
    ``type=d`` (dockets-only) search, which returns docket METADATA with no
    nested documents, so no filing text or PDF is ever fetched — Sentinel reports
    that a case exists, never its contents. Works anonymously; COURTLISTENER_API_KEY
    is optional and only raises the rate limit.

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
COURTLISTENER_KEY = os.getenv("COURTLISTENER_API_KEY", "")
COURTLISTENER_URL = "https://www.courtlistener.com/api/rest/v4/search/"
COURTLISTENER_SITE = "https://www.courtlistener.com"


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


def _court_url(path) -> str | None:
    """Turn a CourtListener relative path into an absolute docket-page URL."""
    if not path:
        return None
    path = str(path)
    return path if path.startswith("http") else COURTLISTENER_SITE + path


def _docket_record(item: dict) -> dict:
    """Whitelist docket METADATA from one search result.

    Deliberately reads only metadata fields and never touches
    ``recap_documents``, ``snippet``, ``plain_text`` or ``filepath_local`` — so
    no filing content or document PDF is ever surfaced, even if a caller passed a
    result that carries them. ``docket_url`` points at the docket page on
    CourtListener, not at any document.
    """
    parties = [p for p in (item.get("party") or []) if isinstance(p, str)][:10]
    return {
        "case_name": item.get("caseName") or item.get("case_name_full"),
        "court": item.get("court") or item.get("court_id"),
        "docket_number": item.get("docketNumber"),
        "date_filed": item.get("dateFiled"),
        "date_terminated": item.get("dateTerminated"),
        "nature_of_suit": item.get("suitNature"),
        "cause": item.get("cause"),
        "parties": parties,
        "docket_url": _court_url(item.get("docket_absolute_url")),
    }


def _court_records(company: str) -> dict:
    """CourtListener (RECAP) dockets whose parties match a company name.

    Uses ``type=d`` (dockets only): the search returns docket metadata with no
    nested documents, so document text and PDFs are structurally excluded and
    never fetched. Anonymous access works; the key only raises the rate limit.
    A name match is a lead, not proof the case concerns this organisation.
    """
    headers = {"User-Agent": "Sentinel-OSINT/2.0", "Accept": "application/json"}
    if COURTLISTENER_KEY:
        headers["Authorization"] = f"Token {COURTLISTENER_KEY}"
    try:
        response = requests.get(
            COURTLISTENER_URL,
            # type=d → dockets only (no recap_documents). Never highlight=on,
            # which would populate document snippets.
            params={"type": "d", "q": company, "order_by": "dateFiled desc"},
            timeout=20,
            headers=headers,
        )
        if response.status_code in (401, 403):
            return {"error": ("CourtListener rejected the API key"
                              if COURTLISTENER_KEY else
                              f"CourtListener refused anonymous access (HTTP {response.status_code})")}
        if response.status_code == 429:
            return {"error": "CourtListener rate limit reached; try again later"}
        if response.status_code != 200:
            return {"error": f"CourtListener HTTP {response.status_code}"}
        payload = response.json()
        results = payload.get("results") or []
        dockets = [_docket_record(item) for item in results[:15]]
        return {
            "total_matches": payload.get("count", len(dockets)),
            "dockets_shown": len(dockets),
            "dockets": dockets,
            "note": ("U.S. federal and state dockets from the RECAP archive, matched "
                     "by party name — metadata only, no filing text or PDFs. A name "
                     "match is a lead, not proof the case concerns this organisation."),
        }
    except requests.exceptions.Timeout:
        return {"error": "CourtListener did not respond within 20 seconds."}
    except Exception as error:
        return {"error": str(error)[:300]}


def lookup(company: str, *, offshore_leaks: bool = False, sanctions: bool = False,
           court_records: bool = False, on_progress=None, should_stop=None) -> dict:
    """Search GLEIF by company name and return compact legal-entity records.

    ``offshore_leaks=True`` also checks the name against ICIJ Offshore Leaks;
    ``sanctions=True`` also screens it with OpenSanctions (skipped without a key);
    ``court_records=True`` also lists matching U.S. court dockets from
    CourtListener (metadata only; works without a key).
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

    if court_records:
        if should_stop and should_stop():
            result["cancelled"] = True
            return result
        label = "CourtListener"
        if on_progress:
            on_progress(label, "checking")
        result["court_records"] = _court_records(company)
        status = "error" if result["court_records"].get("error") else "checked"
        result["sources_contacted"].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)
    return result
