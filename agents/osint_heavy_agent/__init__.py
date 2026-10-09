SYSTEM_PROMPT = """You are a senior OSINT analyst and intelligence researcher. You produce deep, structured investigation dossiers based on the target information provided. Your output is methodical, evidence-based, and rigorously organised.

You do not fabricate findings. When real data is not supplied, you describe what WOULD be found using standard OSINT tradecraft, explain which tools to run and why, and note every gap explicitly. You treat uncertainty as intelligence — absence of data is itself a signal.

─────────────────────────────────────────────
LIVE OSINT DATA
─────────────────────────────────────────────
When the user message contains a LIVE OSINT DATA block, treat every value in it as a confirmed, verified fact collected by automated tooling moments before this analysis. You must:
  • Weave the real data directly into sections 1–4 (do not relegate it to an appendix).
  • Attribute each finding to its source: (WHOIS), (DNS), (crt.sh), (Wayback), (EmailRep), (Gravatar), (HIBP), (BreachDirectory), (URLScan), (WhatsMyName), (GLEIF), (Ransomware.live), (Ahmia), (Intelligence X).
  • A Ransomware.live hit tagged match "victim-name" or "domain" means the target itself was posted on a ransomware group's leak site — treat that as a probable breach and lead section 4 with it. A "description-only" match is a mention in another victim's post, not the target's own exposure. Ahmia and Intelligence X hits are dark-web / leak index matches to corroborate, not confirmed compromises.
  • WhatsMyName hits mean a profile URL answered with that site's "account exists" signature. That is an account with the same name, not proof it belongs to the target: present hits as leads to corroborate, and never merge them into one identity without a linking fact (shared avatar, bio, link, or email). A missing hit proves nothing when the sweep carries a network_warning or many inconclusive sites — say so.
  • A Gravatar profile is published by its owner and keyed by the email hash, so its name, links and verified accounts are a strong pivot — but still self-declared.
  • Also cite (Team Cymru), (Passive DNS), (DShield), (GitHub), (Keybase), (ICIJ Offshore Leaks), (OpenSanctions), (Blockstream) and (Blockscout) when their data appears.
  • OpenSanctions matches are text matches: compare country, registration and dates before treating one as the target, and distinguish listed=true (the entity itself is sanctioned or on a watchlist) from a mere relation to a listed entity. A skipped OpenSanctions check means no API key, not a clean result.
  • For a CRYPTO ADDRESS target, the on-chain data shows what the address did, never who controls it. Exchange and custodial addresses pool many users' funds. Do not name an owner unless a source outside the chain links them (an ENS name is self-chosen, a public tag is the explorer's label), and use sections 2–3 for flows, counterparties and timing rather than personal details.
  • Keybase verified_accounts are cryptographically signed proofs that one person controls those accounts: the strongest identity link in the data. A GitHub profile with the same handle is only as linked as its own fields (name, blog, email) make it.
  • ICIJ Offshore Leaks matches are name similarity, not identity. Being named in the leaks is not evidence of wrongdoing; report a match as a lead, with its similarity score and source leak, never as a finding against the target.
  • Passive DNS and DShield describe infrastructure history. A shared-hosting or CDN address carries thousands of unrelated names and reports, so do not attribute them to the target.
  • Where a field is null or missing, note it as a confirmed gap requiring manual follow-up.
  • Where a field shows an "error" value, flag it as a collection failure and recommend the equivalent manual tool.
  • Never contradict the live data with speculation.

─────────────────────────────────────────────
RESPONSE FORMAT (follow exactly — the UI parser depends on these headers)
─────────────────────────────────────────────

## 1. OVERVIEW
- Target summary and entity profile
- Key confirmed facts
- Investigation scope and objectives
- THREAT LEVEL: X/10  ← always include this exact line
- CONFIDENCE: X%      ← always include this exact line (0–100)
- SOURCES REFERENCED: X ← always include this exact line (estimated number of source categories checked)

## 2. DIGITAL FOOTPRINT
- Online presence mapping (social media, forums, public records, directories)
- Account identifiers and usernames found or inferred
- Data breach and credential exposure assessment
- Public record exposure (court records, voter rolls, business filings, property)
- Dark web and paste site mentions
- For domains/IPs: hosting history, registrant data, subdomains, certificates

## 3. INFRASTRUCTURE / SOCIAL PROFILE
For PERSON / USERNAME targets:
- Social graph: known associations, relationships, co-mentions
- Platform activity patterns and posting behaviour
- Profile timeline and account age analysis
- Employment, education, location history (from public data)
- Imagery and reverse image search opportunities

For DOMAIN / IP / ORGANISATION targets:
- DNS records, subdomains, mail configuration
- Certificate history (crt.sh, Censys)
- Open ports and exposed services (Shodan, Censys)
- Hosting provider, ASN, geolocation
- Historical WHOIS and registrant pivots
- Web technology stack (Netcraft, Wappalyzer, URLScan)
- Related infrastructure and IP neighbours

## 4. RISK & RED FLAGS
- Anomalies, inconsistencies, and suspicious patterns
- Indicators of deception, fraud, or malicious activity
- Threat assessment narrative
- Data points requiring urgent follow-up
- Legal or ethical considerations relevant to this investigation

## 5. METHODOLOGY & TOOLS
List the specific OSINT tools and resources most relevant to THIS target type. For each, include the URL and a one-line description of what to run or check.

Use the following tool reference library (select the most relevant ~15–20 for the target type):

PEOPLE / IDENTITY:
- Pipl: https://pipl.com — deep people search across social, professional, and public records
- Spokeo: https://spokeo.com — reverse phone, email, address, username lookups
- BeenVerified: https://beenverified.com — background check and contact data
- TruePeopleSearch: https://truepeoplesearch.com — free US people search with address history
- Whitepages: https://whitepages.com — phone, address, and identity verification
- Intelius: https://intelius.com — background checks, relatives, criminal records
- ZabaSearch: https://zabasearch.com — free people finder with address history
- FamilyTreeNow: https://familytreenow.com — genealogy-based relationship mapping

USERNAME / ACCOUNT OSINT:
- Sherlock: https://github.com/sherlock-project/sherlock — username presence across 300+ platforms
- WhatsMyName: https://whatsmyname.app — live username enumeration across social platforms
- Namechk: https://namechk.com — username availability check across platforms
- UserSearch.org: https://usersearch.org — username search across social networks
- KnowEm: https://knowem.com — username presence on 500+ social networks
- socialscan: https://github.com/iojw/socialscan — accurate email and username enumeration
- Holehe: https://github.com/megadose/holehe — email-to-account registration checker

EMAIL OSINT:
- Hunter.io: https://hunter.io — email finder and domain email harvesting
- Have I Been Pwned: https://haveibeenpwned.com — breach and paste exposure check
- EmailRep.io: https://emailrep.io — email reputation, age, and risk scoring
- Epieos: https://epieos.com — email to Google account and social profile pivot
- GHunt: https://github.com/mxrch/GHunt — Google account OSINT (Calendar, Maps, Drive activity)
- Phonebook.cz: https://phonebook.cz — email, domain, and URL intelligence database
- IntelX: https://intelx.io — email, domain, IP, and credential search across dark web and leaks
- Snov.io: https://snov.io — email finder and verification

DOMAIN / IP / INFRASTRUCTURE:
- Shodan: https://shodan.io — internet-wide device and service scanner
- Censys: https://search.censys.io — host and certificate intelligence
- SecurityTrails: https://securitytrails.com — DNS history, subdomain enumeration, WHOIS history
- DNSdumpster: https://dnsdumpster.com — DNS recon and domain mapping
- URLScan.io: https://urlscan.io — website scan, screenshot, and network activity
- VirusTotal: https://virustotal.com — domain, IP, and file reputation
- Robtex: https://robtex.com — DNS and IP relationship mapping
- Netcraft: https://sitereport.netcraft.com — web technology stack and hosting history
- crt.sh: https://crt.sh — certificate transparency logs (subdomain discovery)
- MXToolbox: https://mxtoolbox.com — MX, SPF, DMARC, blacklist check
- IPinfo.io: https://ipinfo.io — IP geolocation, ASN, and organisation data
- AbuseIPDB: https://abuseipdb.com — IP abuse and threat reporting database
- GreyNoise: https://greynoise.io — IP noise classification (scanner vs targeted)
- WHOIS: https://who.is — domain registration history and registrant data
- Whoxy: https://www.whoxy.com — WHOIS history and reverse WHOIS by registrant name, email, or company
- DNSDB Scout: https://scout.dnsdb.info — passive DNS history (what a name resolved to, and when)
- Pulsedive: https://pulsedive.com — IOC enrichment: risk score, linked indicators, threat feeds
- abuse.ch Hunting: https://hunting.abuse.ch — search URLhaus, ThreatFox and MalwareBazaar for a domain, IP, or hash
- urlDNA: https://urldna.io — sandboxed URL scan with screenshot, certificate, and page DOM

BREACH DATA / LEAKED CREDENTIALS:
- Have I Been Pwned: https://haveibeenpwned.com — breach exposure by email
- Dehashed: https://dehashed.com — leaked credential search (email, username, IP, name)
- Snusbase: https://snusbase.com — breach database search
- IntelX: https://intelx.io — comprehensive leak and dark web search
- LeakCheck: https://leakcheck.io — credential and breach lookup
- BreachDirectory: https://breachdirectory.org — open breach data search

PHONE NUMBER:
- PhoneInfoga: https://github.com/sundowndev/phoneinfoga — phone number OSINT framework
- NumVerify: https://numverify.com — phone validation and carrier lookup
- Truecaller: https://truecaller.com — crowd-sourced caller ID and spam detection
- CallerIDTest: https://calleridtest.com — caller ID verification

IMAGE / FACE OSINT:
- TinEye: https://tineye.com — reverse image search with date history
- Google Images: https://images.google.com — reverse image search
- Yandex Images: https://yandex.com/images — powerful reverse image search (faces)
- PimEyes: https://pimeyes.com — facial recognition across public web
- FaceCheck.ID: https://facecheck.id — face search across social media

WEB ARCHIVE:
- Wayback Machine: https://web.archive.org — historical website snapshots
- CachedView: https://cachedview.nl — Google/Bing cached page viewer
- TimeTravel: https://timetravel.mementoweb.org — multi-archive web time travel

GEOLOCATION:
- GeoSpy: https://geospy.web.app — AI-powered image geolocation
- WiGLE: https://wigle.net — Wi-Fi network (SSID/BSSID) sightings on a map; places a device or network
- SunCalc: https://suncalc.org — sun angle analysis for image time/location
- Bellingcat Toolkit: https://bellingcat.gitbook.io/toolkit — geolocation and verification tools

SOCIAL MEDIA SEARCH:
- Social Searcher: https://social-searcher.com — multi-platform social media monitoring
- Reddit Search: https://www.reddit.com/search — Reddit post and user search
- Twitter/X Advanced: https://twitter.com/search-advanced — advanced Twitter search

DUE DILIGENCE / SANCTIONS / NETWORKS:
- OpenSanctions: https://www.opensanctions.org — sanctions lists, politically exposed persons, and wanted lists in one search
- OCCRP Aleph: https://aleph.occrp.org — leaked documents, company registries, and court records from investigative journalism
- LittleSis: https://littlesis.org — who-knows-who map of board seats, donors, and lobbying ties
- Transparenzregister: https://www.transparenzregister.de — German beneficial-ownership register (access requires justified interest)

CRYPTOCURRENCY:
- Blockchain.com Explorer: https://www.blockchain.com/explorer — Bitcoin address balance and transaction history
- Etherscan: https://etherscan.io — Ethereum address, token, and contract activity
- WalletExplorer: https://www.walletexplorer.com — Bitcoin address clustering into wallets and named services
- BlockCypher: https://live.blockcypher.com — multi-chain explorer (BTC, LTC, DOGE, DASH)
- Chainabuse: https://www.chainabuse.com — community scam and fraud reports by wallet address

NEWS & EVENTS:
- GDELT: https://www.gdeltproject.org — global news-event database; search coverage of a person, company, or place over time

GOOGLE DORKS & SEARCH OPERATORS:
- Google: https://google.com — use operators: site:, filetype:, inurl:, intitle:, "exact phrase"
- GHDB: https://www.exploit-db.com/google-hacking-database — Google Hacking Database
- DuckDuckGo: https://duckduckgo.com — privacy-respecting search with operators
- Bing: https://bing.com — alternative index, sometimes indexes content Google misses

OSINT FRAMEWORKS:
- OSINT Framework: https://osintframework.com — categorised OSINT tool directory
- SpiderFoot: https://spiderfoot.net — automated OSINT platform
- Maltego: https://maltego.com — visual link analysis and entity mapping
- Recon-ng: https://github.com/lanmaster53/recon-ng — web reconnaissance framework
- theHarvester: https://github.com/laramies/theHarvester — email, subdomain, and name harvesting

─────────────────────────────────────────────
DEPTH GUIDANCE
─────────────────────────────────────────────
Quick Scan: 3–5 bullet points per section. Focus on what is immediately apparent and highest priority tools only.
Standard Investigation: 8–12 bullet points per section. Cover all major angles with specific tool recommendations.
Deep Dive: Exhaustive coverage per section. Map all infrastructure, trace all platform presence, enumerate all pivot points. Surface every notable anomaly.

─────────────────────────────────────────────
TONE AND STANDARDS
─────────────────────────────────────────────
- Be direct and specific. Avoid vague placeholders.
- Quantify everything you can: counts, dates, confidence scores.
- Distinguish between confirmed facts, inferred patterns, and speculation. Label each clearly.
- Always include a disclaimer: ⚠️ This report is for authorised investigative and research purposes only. Misuse of OSINT techniques may violate privacy laws. Always operate within your legal jurisdiction.
"""


import ipaddress as _ipaddress
import json as _json
import re as _re

from services import osint_catalog as _catalog

try:
    from providers import domain_lookup    as _domain_prov
    from providers import email_lookup     as _email_prov
    from providers import username_lookup  as _username_prov
    from providers import company_lookup   as _company_prov
    from providers import exposure_lookup  as _exposure_prov
    from providers import crypto_lookup    as _crypto_prov
    _PROVIDERS_OK = True
except ImportError:
    _PROVIDERS_OK = False


_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = _re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}$")
_SCHEME_RE = _re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_BRACKET_RE = _re.compile(r"^\[([^\]]+)\](?::\d+)?$")


def _extract_ip(value: str) -> str | None:
    """Return the bare IPv4/IPv6 address in ``value``, or None if it is not an IP.

    Tolerates, in any combination: a scheme, ``[..]``-bracketed IPv6, a ``:port``
    (numeric only), a ``/CIDR`` suffix, and an IPv6 ``%zone``.
    """
    v = _SCHEME_RE.sub("", (value or "").strip())  # drop scheme
    v = v.split("/", 1)[0]                          # drop /CIDR (or path)
    match = _BRACKET_RE.match(v)
    if match:
        v = match.group(1)                          # [IPv6] or [IPv6]:port
    elif v.count(":") == 1:                         # host:port for IPv4/hostname
        head, port = v.rsplit(":", 1)
        if port.isdigit():
            v = head
    v = v.split("%", 1)[0]                           # drop IPv6 %zone
    try:
        return str(_ipaddress.ip_address(v))
    except ValueError:
        return None


def _looks_like_ip(value: str) -> bool:
    """True for an IPv4/IPv6 address (see _extract_ip). The domain provider
    resolves IPs, so an auto-detected address must route there, not to the
    username sweep."""
    return _extract_ip(value) is not None


def _detect_from_target(target: str) -> str:
    """Best-effort canonical type for an auto-detect / unknown label."""
    text = (target or "").strip()
    if not text:
        return "unknown"
    # Before the username fallback: a Bitcoin address is also a valid handle.
    if _PROVIDERS_OK and _crypto_prov.chain_of(text):
        return "crypto"
    if _EMAIL_RE.match(text):
        return "email"
    # IPs (v4/v6, incl. :port, /CIDR, [..], %zone) resolve via the domain
    # provider; check before the username fallback so they are not mis-routed.
    if _looks_like_ip(text):
        return "domain"
    host = text.split("//")[-1].split("/")[0].split("@")[-1].split(":")[0]
    if _DOMAIN_RE.match(host):
        return "domain"
    if " " not in text and "@" not in text:
        return "username"
    return "person"


def _normalize_target_type(target: str, target_type: str) -> str:
    """Map a UI label (e.g. "Email Address", "Domain / IP", "Auto-detect") or a
    bare token to the canonical dispatch key used by ``_run_providers``.

    The Bloodhound panel passes the combobox label verbatim, so without this
    normalisation "Email Address" and "Domain / IP" never matched the old
    lowercase tokens and live collection silently no-opped.
    """
    raw = (target_type or "").strip().lower()
    compact = _re.sub(r"[^a-z]", "", raw)  # "domain / ip" -> "domainip"
    if not compact or "auto" in compact:
        return _detect_from_target(target)
    if "email" in compact:
        return "email"
    if "username" in compact:
        return "username"
    if "domain" in compact or compact == "ip" or "ipaddress" in compact:
        return "domain"
    if "organisation" in compact or "organization" in compact or "company" in compact or compact == "org":
        return "organisation"
    if "person" in compact or "people" in compact:
        return "person"
    if "phone" in compact:
        return "phone"
    if "crypto" in compact or "wallet" in compact or "bitcoin" in compact:
        return "crypto"
    return _detect_from_target(target)


def real_source_count(live_results: list[dict]) -> int:
    """Number of public sources actually contacted during live collection.

    This is a real count derived from each provider's ``sources_contacted``
    list — never the model's self-declared "SOURCES REFERENCED" estimate.
    """
    total = 0
    for result in live_results or []:
        if isinstance(result, dict):
            total += len(result.get("sources_contacted") or [])
    return total


def _run_providers(target: str, target_type: str, scope: str = "", *,
                   on_progress=None, should_stop=None) -> list[dict]:
    """
    Dispatch live lookups based on target_type.

    Returns a list of provider result dicts (errors captured inside each dict,
    never raised — collection must not crash on provider failures). Accepts
    either a UI label ("Email Address", "Domain / IP", "Auto-detect", …) or a
    bare canonical token; both are normalised first.

    A Deep Dive on a username also sweeps the WhatsMyName site list (several
    hundred profile URLs, up to a minute); lighter scopes leave it out.
    ``on_progress(message)`` receives one human-readable line per step.
    """
    if not _PROVIDERS_OK:
        return []

    collected: list[dict] = []
    tt = _normalize_target_type(target, target_type)
    deep = scope == "Deep Dive"

    def report(message: str) -> None:
        if on_progress:
            on_progress(message)

    def source_progress(source: str, status: str) -> None:
        report(f"{source}: {status}")

    def sweep_progress(done: int, total: int) -> None:
        report(f"WhatsMyName: {done} of {total} sites checked")

    tracking = {"on_progress": source_progress, "should_stop": should_stop}

    def username_lookup(handle: str) -> dict:
        return _username_prov.lookup(
            handle, whatsmyname=deep, on_sweep_progress=sweep_progress, **tracking)

    def exposure_lookup(kind_label: str) -> None:
        """Append a dark-web exposure check (leak / ransomware sites) for a
        domain, organisation or email. Free sources always run; Intelligence X
        self-skips unless INTELX_API_KEY is set."""
        try:
            collected.append(_exposure_prov.lookup(target, kind_label, **tracking))
        except Exception as exc:
            collected.append({"type": "exposure", "query": target,
                               "error": f"provider exception: {exc}"})

    # ── Domain / IP ────────────────────────────────────────────────────────
    if tt == "domain":
        try:
            collected.append(_domain_prov.lookup(target, **tracking))
        except Exception as exc:
            collected.append({"type": "domain", "query": target,
                               "error": f"provider exception: {exc}"})
        exposure_lookup("Domain")

    # ── Organisation — use the legal-entity registry, not domain WHOIS ──────
    elif tt == "organisation":
        try:
            collected.append(_company_prov.lookup(
                target, offshore_leaks=True, sanctions=True, court_records=True,
                **tracking))
        except Exception as exc:
            collected.append({"type": "company", "query": target,
                               "error": f"provider exception: {exc}"})
        exposure_lookup("Company")

    # ── Email ──────────────────────────────────────────────────────────────
    elif tt == "email":
        try:
            collected.append(_email_prov.lookup(target, **tracking))
        except Exception as exc:
            collected.append({"type": "email", "query": target,
                               "error": f"provider exception: {exc}"})
        exposure_lookup("Email")

    # ── Username ───────────────────────────────────────────────────────────
    elif tt == "username":
        try:
            collected.append(username_lookup(target))
        except Exception as exc:
            collected.append({"type": "username", "query": target,
                               "error": f"provider exception: {exc}"})

    # ── Person — heuristic sub-dispatch ───────────────────────────────────
    elif tt == "person":
        # If the target string looks like an email, check EmailRep
        if "@" in target and "." in target.split("@")[-1]:
            try:
                collected.append(_email_prov.lookup(target, **tracking))
            except Exception as exc:
                collected.append({"type": "email", "query": target,
                                   "error": f"provider exception: {exc}"})
        # If it looks like a standalone username (no spaces, no @), try URLScan
        # (and, on a Deep Dive, the WhatsMyName sweep)
        if " " not in target and "@" not in target:
            try:
                collected.append(username_lookup(target))
            except Exception as exc:
                collected.append({"type": "username", "query": target,
                                   "error": f"provider exception: {exc}"})

    # ── Cryptocurrency address ────────────────────────────────────────────
    elif tt == "crypto":
        try:
            collected.append(_crypto_prov.lookup(target, **tracking))
        except Exception as exc:
            collected.append({"type": "crypto", "query": target,
                               "error": f"provider exception: {exc}"})

    # ── Phone / other ──────────────────────────────────────────────────────
    # No zero-cost provider available (people-search / phone brokers are
    # deliberately not used); return empty so the LLM falls back to its
    # methodology guidance and the Sources gauge honestly shows 0 contacted.

    return collected


#: How many catalogue tools each scope adds to the built-in library.
CATALOG_LIMITS = {"Quick Scan": 10, "Standard Investigation": 25, "Deep Dive": 45}


def _catalog_kind(target: str, target_type: str) -> str | None:
    canonical = _normalize_target_type(target, target_type)
    if canonical == "domain":
        # Reuse the same IP extraction as detection, so a decorated address
        # (:port, /CIDR, [..], %zone) selects the IP catalogue, not the domain one.
        return "ip" if _extract_ip(target) else "domain"
    return {"organisation": "company", "email": "email", "username": "username",
            "phone": "phone", "person": "person", "crypto": "crypto"}.get(canonical)


def catalog_block(target: str, target_type: str, scope: str) -> str:
    """Current tools from the cached OSINT Framework catalogue for this target.

    Cache only — never the network — so build_messages stays offline. Tools
    marked ACTIVE contact or scan the target; the prompt tells the model to
    say so wherever it recommends one.
    """
    kind = _catalog_kind(target, target_type)
    if kind is None:
        return ""
    picks = _catalog.select(
        _catalog.cached_tools(), kind, audience="bloodhound",
        limit=CATALOG_LIMITS.get(scope, 25),
        exclude_hosts=_catalog.hosts_in(SYSTEM_PROMPT),
    )
    if not picks:
        return ""
    return (
        "\n\n─────────────────────────────────────────────\n"
        f"CURRENT CATALOGUE — {_catalog.ATTRIBUTION}, live and non-deprecated tools for "
        "this target type, in addition to the library above.\n"
        "─────────────────────────────────────────────\n"
        "Tags give pricing, whether an account is needed, and API availability. A tool "
        "tagged ACTIVE interacts with the target (scans, uploads, or visits it) and may be "
        "noticed: whenever you recommend one in section 5, say so and why it is worth the "
        "exposure.\n" + _catalog.format_block(picks)
    )


class OsintHeavyAgent:
    """Deep OSINT investigation agent — structured multi-section dossier with curated tool methodology."""

    def __init__(self):
        self.name = "osint_heavy"
        self.last_live_results: list[dict] = []
        self.last_source_count: int = 0

    def collect_live(self, target: str, target_type: str, scope: str = "", *,
                     on_progress=None, should_stop=None) -> list[dict]:
        """Run the real live lookups for this target and remember the outcome.

        Separated from ``build_messages`` so that (a) message construction stays
        offline and unit-testable without network access, and (b) the panel can
        drive the Sources gauge from the real number of sources contacted rather
        than the model's self-declared estimate. The panel runs this on a
        worker thread; ``should_stop`` lets its Stop button end a long sweep.
        """
        results = _run_providers(target, target_type, scope,
                                 on_progress=on_progress, should_stop=should_stop)
        self.last_live_results = results
        self.last_source_count = real_source_count(results)
        return results

    def build_messages(
        self,
        target: str,
        target_type: str,
        scope: str,
        objective: str,
        image_metadata: str = "",
        live_results: list[dict] | None = None,
    ) -> list[dict]:
        scope_hint = {
            "Quick Scan": "This is a Quick Scan — be concise, 3–5 points per section, highest-priority tools only.",
            "Standard Investigation": "This is a Standard Investigation — thorough coverage across all sections.",
            "Deep Dive": "This is a Deep Dive — exhaustive analysis, enumerate every lead, surface every pivot point.",
        }.get(scope, "Standard Investigation.")

        # ── Live OSINT data (collected separately via collect_live) ───────
        # build_messages is intentionally offline: it injects whatever real
        # results the caller supplies but never performs network lookups here.
        if live_results is None:
            live_results = []

        user_parts = [
            f"TARGET IDENTIFIER: {target}",
            f"TARGET TYPE: {target_type}",
            f"INVESTIGATION SCOPE: {scope}",
        ]
        if objective.strip():
            user_parts.append(f"OBJECTIVE / CONTEXT: {objective.strip()}")

        # Inject live data block when at least one provider returned data
        if live_results:
            live_json = _json.dumps(live_results, indent=2, ensure_ascii=False)
            user_parts.append(
                "\n─── LIVE OSINT DATA (auto-collected) ───────────────────────────────\n"
                + live_json
                + "\n────────────────────────────────────────────────────────────────────\n"
                "Treat every value above as a confirmed fact. Attribute each finding to its "
                "source tag (WHOIS / DNS / crt.sh / Wayback / EmailRep / Gravatar / HIBP / "
                "BreachDirectory / URLScan / WhatsMyName / GLEIF / Ransomware.live / Ahmia / "
                "Intelligence X / DeHashed / Snusbase / LeakCheck / AbuseIPDB / GreyNoise / "
                "VirusTotal / AlienVault OTX / Shodan / Censys / SecurityTrails / DomainTools / "
                "Hunter). Breach sources report breach names, counts and kinds of leaked "
                "data only, never the leaked values; do not speculate about them. A "
                "GreyNoise \"noise\" verdict means the IP scans the whole internet, which "
                "is context, not evidence of targeting. A Ransomware.live \"exposure\" block with a direct victim "
                "match means the target was posted on a ransomware leak site — surface it "
                "prominently. Any field showing an \"error\" value is a collection gap — "
                "recommend the manual equivalent."
            )

        if image_metadata.strip():
            user_parts.append(
                f"\nTARGET IMAGE PROVIDED — extracted metadata below. Incorporate this into your "
                f"analysis, especially in sections 1 (Overview), 2 (Digital Footprint), and 4 (Risk & Red Flags). "
                f"Comment on what the metadata reveals or conceals about the subject.\n\n"
                f"IMAGE METADATA:\n{image_metadata.strip()}"
            )
        user_parts.append(f"\n{scope_hint}")
        user_parts.append(
            "\nProduce the full investigation dossier following the five-section format exactly. "
            "Include THREAT LEVEL, CONFIDENCE, and SOURCES REFERENCED on the exact lines specified."
        )

        return [
            {"role": "system",
             "content": SYSTEM_PROMPT + catalog_block(target, target_type, scope)},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
