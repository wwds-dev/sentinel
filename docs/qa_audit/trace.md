# Requirements-traceability audit: Trace (`osint`)

Repo: `/home/claude/wwds-dev/sentinel` (read-only; nothing run, no pytest). Audit date 2026-10-09, release v2.002.
Promise sources read in full: `README.md`, `docs/agents/osint.md`, `docs/training/trace.md`, `docs/training/api_keys.md`, `.env.example`, Trace section of `ui/tooltips.py` (plus `tests/manual_test_cases.md` section 3 and `TODO.md` as corroboration).
Implementation read in full: everything on the brief's list, plus `ui/panels/base.py`, the Saved-Searches and authorisation code in `main.py`, `services/validator.py`, `services/agent_catalog.py`.

Status key: IMPLEMENTED / PARTIAL / STUB / MISSING / NCV (not code-verifiable: model-output quality or live network). No STUB, TODO or NotImplementedError exists anywhere in the audited Trace code.

---

## 1. Verdict

Trace's core promise holds in code: Structure Query is genuinely offline (apart from the chosen model), every Live Research and Exposure Check path is behind a consent dialog that returns before any worker starts when declined, key-gated sources are left out until their key is saved, Person and Phone Live Research is blocked outright, and Saved Searches reopen from stored JSON without any request. The weaknesses are scope, accuracy of the docs, and a handful of real defects. **Scope:** the brief describes Trace as also doing WhatsMyName sweeps, ICIJ Offshore Leaks and Blockstream/Blockscout crypto lookups; those three adapters exist in `providers/` but are only wired to Bloodhound, so Trace cannot reach them (and the README sentence "providers/ ... Trace and Bloodhound call" overstates this). Conversely Trace does things the brief and several docs omit (GitHub and Keybase for usernames, Hunter, eleven key-gated threat-intel sources, Snusbase, LeakCheck, OpenSanctions, an always-on CourtListener lookup). **Defects:** Stop on Structure Query is unreliable (a local Ollama request, which is non-streaming, finishes, is recorded and shown as "Done" after Stop; a cloud stream ends in a red ERROR box); the DNS source for an IP target always errors (no reverse lookup); the "Summary and next steps" card keeps the header remnant "& NEXT STEPS"; BreachDirectory output is passed through unfiltered despite the metadata-only rule; the OSINT Keys tab shows Censys's hint and note on the Criminal IP row; `api_keys.md` promises a "Malformed, nothing sent" check for VirusTotal that the code does not do; and the Exposure verdict can read "No exposure found" when every source errored. **Docs:** about a dozen contradictions between sources (see section 4b), the most test-relevant being that the manual test case says a username lookup contacts "only URLScan" while code contacts URLScan, GitHub and Keybase.

## 2. Counts

Rows in the table (section 3): **114**

| Status | Count |
|---|---|
| IMPLEMENTED | 82 |
| PARTIAL | 24 |
| STUB | 0 |
| MISSING | 4 (rows 7, 63, 78, 79: optional context input, WhatsMyName, ICIJ, crypto) |
| NOT CODE-VERIFIABLE | 4 (rows 13, 14, 113, 114) |

(Defects and undocumented behaviours found outside any single promise are listed in section 4c and are not counted.)

---

## 3. Traceability table

Source abbreviations: README, OSM = `docs/agents/osint.md`, TRN = `docs/training/trace.md`, KEYS = `docs/training/api_keys.md`, ENV = `.env.example`, TIP = `ui/tooltips.py`, MAN = `tests/manual_test_cases.md`. Files: PANEL = `ui/panels/osint.py`, WRK = `ui/workers.py`, AGENT = `agents/osint_agent/__init__.py`, DOM = `providers/domain_lookup.py`, INTEL = `providers/intel_sources.py`, EML = `providers/email_lookup.py`, USR = `providers/username_lookup.py`, CMP = `providers/company_lookup.py`, EXP = `providers/exposure_lookup.py`, KC = `providers/key_check.py`, DLG = `ui/dialogs.py`, OK = `services/osint_keys.py`, CAT = `services/osint_catalog.py`.

### 3.1 Roster, inputs, validation, Structure Query

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 1 | Trace is key `osint`, focused open-source research and planning (README roster; OSM header) | IMPLEMENTED | `services/agent_catalog.py:28-36`; `PANEL:35` | Catalog `allowed_tools` and a EUR 2.00 per-paid-request cap are not documented (see 4c). |
| 2 | Query type choices: Auto-detect, Person, Username, Email, Domain, Company, Phone, IP Address (OSM Inputs) | IMPLEMENTED | `PANEL:77-80` | Matches exactly. |
| 3 | Structured types validated locally before authorisation (OSM Inputs) | IMPLEMENTED | `AGENT:107-147`; `PANEL:172-183` | Validation runs before `authorize()`. Live Research never calls `authorize()` (see 4c). |
| 4 | Empty or invalid target rejected before a paid request (MAN s3) | IMPLEMENTED | `PANEL:165-178`; `AGENT:111-117, 171-224` | Email, domain, IP, phone (7-15 digits), username (2-64), name/company (2+ chars with a letter) each have a validator. |
| 5 | Auto-detect resolves the type offline (OSM, TRN: "happens locally") | IMPLEMENTED | `AGENT:119-132` | Order: leading @ then email then IP then phone then domain then Person/Username. Never resolves to Company; dotted handles resolve to Domain; crypto addresses resolve to Username (see 4c). |
| 6 | Auto-detect records the resolved type in the Activity trail (OSM Inputs; MAN s3) | PARTIAL | `PANEL:190-193` (Structure Query, "(auto-detected)"); `PANEL:390-393`, `PANEL:507-510` | Live Research and Exposure Check print the resolved type but never say it was auto-detected, and the consent dialogs do not show the resolved type, only the sources. |
| 7 | Optional context alongside the target (OSM "plus optional context"; TRN "Add only the context needed to distinguish the target") | MISSING | `AGENT:226-235` (`build_messages(target, query_type)` only); `PANEL:68-81` (one Target field) | Grepped for `context`, `objective`, `notes` in `PANEL` and `AGENT`: no input exists. Free text typed into the Target box must still pass the type validator (an email or domain with extra words fails). |
| 8 | Model override: optional provider/model, task recommendation selected by default (OSM Inputs) | PARTIAL | `PANEL:113-118`; `ui/panels/base.py:165-240`; `services/model_recommendations.py:567` | Provider/Model dropdowns sit in the run bar; there is no control labelled "Model override" (`build_model_override`, base.py:263, is defined but unused by Trace). Default recommendation is DeepSeek flash. |
| 9 | Auto-route button beside run controls (README Using the app) | IMPLEMENTED | `ui/panels/base.py:212-221, 242-261` | Shared control. |
| 10 | Structure Query generates a model plan and contacts no research source (OSM, TRN) | IMPLEMENTED | `PANEL:161-222` (only `start_worker`); `AGENT:69-89` (cache-only); trail text `PANEL:202-205, 242-245` | Catalogue background download is separate (row 24). |
| 11 | Ollama keeps the target local; cloud sends the prompt only after permission (TRN) | IMPLEMENTED | `PANEL:183, 194-201`; `main.py:3556-3636` (`authorize_request`, `confirm_external_api_request` at 3463) | |
| 12 | Runs through shared ChatWorker, request guard, cost tracking, history, run logger (OSM How it works) | IMPLEMENTED | `PANEL:183, 217, 239`; `main.py:3556-3685` | |
| 13 | Output is exactly four sections, 8-12 dorks, 8-12 sources, 3-5 next steps (system prompt) | NCV | `AGENT:11-57` | Needs sample model runs; parser tolerates missing sections (`PANEL:853-866`). |
| 14 | Prompt forbids fabricated results and keeps to lawful public-source work (OSM; README Safety) | NCV | `AGENT:56-57` | Prompt-level only; needs adversarial prompts against a real model. |
| 15 | Result shown as readable section cards; raw stream visible during generation (OSM Outputs) | PARTIAL | `PANEL:224-247, 841-851`; `ui/widgets.py:700-790`; live cards `PANEL:631-634` | Structure Query cards are text sections as promised. Live Research and Exposure cards are pretty-printed JSON dumps (`json.dumps(indent=2)`), so "readable" is generous; see also row 16. |
| 16 | "Summary and next steps" card contains the summary text | PARTIAL | `PANEL:861` (`##\s*SUMMARY.*?(.*?)$`) | The real header is "## SUMMARY & NEXT STEPS"; the lazy `.*?` consumes nothing, so the card body begins "& NEXT STEPS". The unit test uses the header "## SUMMARY" and so misses it (`tests/test_ui_panels.py:2073-2080`). |
| 17 | Curated reference list in the prompt (web-check, ViewDNS, CentralOps, archive.today, WhatsMyName, OpenCorporates, German registers, Das Oertliche) (OSM) | IMPLEMENTED | `AGENT:33-48` | Provenance claim (Bruno Mortier list, 2026-09-28) is NCV. |
| 18 | Appends up to 15 OSINT Framework tools for the type (OSM) | IMPLEMENTED | `AGENT:60-89` (`CATALOG_LIMIT = 15`) | |
| 19 | Catalogue keeps only live, non-deprecated tools (OSM) | IMPLEMENTED | `CAT:84-109` (line 92) | |
| 20 | Catalogue keeps only free, no-account, passive tools (OSM) | IMPLEMENTED | `CAT:193-199` | Also silently drops `local_install` tools (undocumented). TODO.md:80 says no unit tests exist for this module. |
| 21 | People-search and dating sites never suggested (OSM) | PARTIAL | `CAT:51, 189-191` | Exclusion is by whole branch name only ("People Search Engines", "Dating"). "Dating" is not in `TARGET_BRANCHES` at all; people-search tools listed under other branches (Username, Social Networks, Public Records) are not caught. No per-host list as for shadow libraries. |
| 22 | Shadow libraries blocked outright (OSM) | IMPLEMENTED | `CAT:55, 194-195` | Applies to catalogue picks only, not to the model's own suggestions. |
| 23 | Building the prompt reads only the cache; no cache means built-in list only (OSM) | IMPLEMENTED | `CAT:115-128`; `AGENT:69-89` | |
| 24 | Catalogue downloaded at most weekly to `data/cache/osint-framework.json` by a background thread at first panel show (OSM) | IMPLEMENTED | `CAT:33, 80-82, 131-171`; `PANEL:50-54` | Real, unconsented network request to raw.githubusercontent.com; never shown in the Activity trail (see 4c). |

### 3.2 Live Research: consent, Stop, activity trail, persistence

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 25 | Live Research starts only after explicit confirmation that shows the target and sources (OSM, TRN: "showing exactly what will be shared") | IMPLEMENTED | `PANEL:375-386` (default button No); email `PANEL:410-470` | Email consent is the source picker itself (no second confirm). |
| 26 | A declined or cancelled consent skips every call | IMPLEMENTED | `PANEL:384-386, 336-338, 453-454, 466-470, 598-599, 614-618` | All return before any worker is created. Nothing network-touching precedes consent (`keyed_labels` and key reads are env-only). |
| 27 | Live Research available for Domain, IP, Username, Email, Company only | IMPLEMENTED | `PANEL:316-331` | |
| 28 | Person and Phone: no live collection, explanatory message (OSM, README, TRN, MAN) | IMPLEMENTED | `PANEL:316-331`; `tests/test_ui_panels.py:1977-1989` | |
| 29 | Nothing in Person/Phone mode contacts the network except the chosen model | IMPLEMENTED | `PANEL:316-331, 490-497` | Only Structure Query (model) and the weekly catalogue download (row 24) can run. Bypass: choosing Company or Username for a personal name (see 4c). |
| 30 | Live Research results are collected records, not model inferences (OSM, TRN) | IMPLEMENTED | `PANEL:652-653`; no `authorize`/ChatWorker on the lookup path | |
| 31 | Activity trail names each source as it is contacted (OSM Outputs) | IMPLEMENTED | `PANEL:620-628`; per-provider `on_progress("...", "checking")` | |
| 32 | Success or failure recorded per source; one failure does not discard others (OSM) | IMPLEMENTED | `DOM:530-541`; `USR:203-220`; `CMP:302-305`; `PANEL:641-652` | |
| 33 | Trail lists the sources actually contacted at completion (OSM) | IMPLEMENTED | `PANEL:764-767` | List includes sources that errored (including local failures such as a missing library). |
| 34 | A service skipped before contact is recorded separately and is not reported as contacted (OSM, TRN, MAN) | PARTIAL | Email `EML:254`; company `CMP:317`; exposure `EXP:791`; panel `PANEL:625-626, 650-651, 768-772`. Domain/IP: `DOM:513-517` | Domain/IP have no `sources_skipped`; key-gated sources without a key are omitted silently, and the summary card says "Skipped before contact: none". |
| 35 | Stop requests cancellation; completed source results remain visible as a partial result (OSM Inputs) | IMPLEMENTED | `PANEL:295-299, 654-655, 792-795`; cancel checks `DOM:531-533`, `EML:239-241`, `USR:136-137, 210-212`, `CMP:263-265, 308-310, 336-338`, `EXP:782-784` | Cancellation is only checked between sources; an in-flight source (up to 15-20 s) finishes. WhatsMyName not in play. |
| 36 | After Stop the UI is safe to reuse | PARTIAL | `PANEL:299, 301-307` | Stop re-enables Live Research and Exposure Check at once while the old worker is still running; a second lookup replaces `self.worker` (`PANEL:407`), leaving an un-referenced running QThread (crash risk). No `is_running()` guard in `live_research`. |
| 37 | Stop on Structure Query stops the request cleanly | PARTIAL | `ui/panels/base.py:439-445`; `WRK:215-254`; `main.py:3733-3745`; `services/ollama_client.py:194-211` | Ollama `chat` is non-streaming: after Stop the thread still calls `finished_signal` (WRK:251-254), so the answer is recorded, saved and the status becomes "Done." Cloud streams emit an error instead (WRK:221-223): `_on_error` replaces the partial text with "ERROR - Request cancelled by user." and sets "Error." (PANEL:249-268). Partial tokens are dropped either way. |
| 38 | Live Research saved to Saved Searches with no model billing (README/OSM Outputs) | IMPLEMENTED | `PANEL:774-791`; `main.py:3694-3731` | Saved only when at least one source was contacted or skipped; cancelled runs are saved with status "cancelled". Whole JSON, including WHOIS contact data, is stored in chat history. |
| 39 | Activity trail is persistent and still visible after completion (OSM) | IMPLEMENTED | `PANEL:123-134, 242-246, 814-833` | |
| 40 | Trail explains validation, local/cloud execution, model processing, completion, cancellation and errors, and says whether external sources were queried (OSM) | IMPLEMENTED | `PANEL:175, 190-209, 230, 243-245, 297, 254` | Stop wording "No further processing was performed" is not strictly true for the in-flight source or a non-streaming model call. |

### 3.3 Domain and IP

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 41 | Domain: WHOIS (README, OSM) | IMPLEMENTED | `DOM:82-100, 508` | Returns registrar, dates, name servers, emails, org, country. For IP targets the output depends on python-whois (see row 114). |
| 42 | Domain: DNS records (README, OSM) | IMPLEMENTED | `DOM:103-117` | A, AAAA, MX, NS, TXT, SOA via the system resolver, 5 s each. |
| 43 | IP: DNS (README, OSM list DNS for IPs) | PARTIAL | `DOM:103-117, 506-509` | `_dns(ip)` asks for A/AAAA/MX/NS/TXT/SOA of the dotted-quad string; none resolve, so it returns `{"error": "no records resolved"}` and the trail logs a DNS error for every IP lookup. No PTR/reverse lookup anywhere in `DOM`. |
| 44 | Team Cymru IP-to-ASN, asked over DNS (README, OSM) | IMPLEMENTED | `DOM:187-237` | A domain uses its first IPv4 only; AAAA-only domains return an error. |
| 45 | Mnemonic passive DNS; partial results dated by created/updated (README, OSM) | IMPLEMENTED | `DOM:246-283` | 25 records max. |
| 46 | crt.sh, domains only (README, OSM, TRN) | IMPLEMENTED | `DOM:120-143, 523-525` | Subdomain sample capped at 30. |
| 47 | Wayback: earliest and latest snapshot via the availability API twice, plus a link to every capture (OSM) | IMPLEMENTED | `DOM:155-184` | The "link to every capture" is one calendar URL (`all_captures`), not a list of capture links. |
| 48 | IP targets skip crt.sh and the archive (OSM) | IMPLEMENTED | `DOM:523-525` | |
| 49 | SANS DShield for IPs: attacks, SSH brute force, web probing (README, OSM) | IMPLEMENTED | `DOM:286-325, 511` | |
| 50 | Shodan InternetDB for IPs, keyless (README, OSM, ENV) | IMPLEMENTED | `DOM:328-355, 512` | 404 reported as "no record", not an error. |
| 51 | IPinfo key-gated, skipped without key (README, ENV, TRN) | IMPLEMENTED | `DOM:358-403, 518-519`; `PANEL:674-678` | Omitted from the run without a key, and `_ipinfo` itself refuses without a key. Token sent in Authorization header. |
| 52 | Criminal IP key-gated, skipped without key (README, ENV, TRN) | IMPLEMENTED | `DOM:406-467, 520-521`; `PANEL:679-683` | |
| 53 | IP + key: AbuseIPDB, GreyNoise, VirusTotal, OTX, Shodan, Censys (OSM, ENV) | IMPLEMENTED | `INTEL:57-69, 88-90, 173-419`; `DOM:526-528`; cards `PANEL:690-695` | |
| 54 | Domain + key: VirusTotal, OTX, SecurityTrails, DomainTools, Shodan DNS, URLScan, Hunter (OSM, ENV) | IMPLEMENTED | `INTEL:70-79, 254-290, 345-362, 424-456, 485-566, 576-613` | |
| 55 | Hunter reports role addresses only and a count of named people (OSM, ENV) | IMPLEMENTED | `INTEL:576-613` (`type=generic`, second filter at 608-609) | |
| 56 | Domain/IP consent text names every key-gated service from the same list the lookup runs (ENV, TODO.md:65) | IMPLEMENTED | `PANEL:348-365`; `DOM:472-485` | Single source of truth (`configured()`); parity test at `tests/test_ui_panels.py:1634-1678`. |
| 57 | "Every source ... self-skips without its key when one is required" (README Architecture) | PARTIAL | `INTEL:88-95` (gate is `configured()` in the orchestrator); `INTEL:173-343` (functions send an empty key header if called directly); self-skip inside `DOM:368-370, 417-419`, `EML:34-40`, `CMP:127-130`, `EXP:386-393` | Safe via `DOM.lookup`; Bloodhound is the other caller. The 11 `intel_sources` functions do not self-skip. |
| 58 | Domain/IP result cards per source (README/OSM Outputs) | IMPLEMENTED | `PANEL:658-695` | JSON text per card (see row 15). |
| 59 | Consent text names where the target goes ("WHOIS, DNS, ..."), so the user knows what is shared (TRN: "showing exactly what will be shared") | IMPLEMENTED | `PANEL:348-365`; `DOM:103-117, 207-237` | The DNS step (and `_asn`, which resolves the name again) queries through the user's resolver, so the target's own name servers can see the lookup. The docs never say this (see 4c-19). |

### 3.4 Username, email, company, crypto

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 60 | Username: URLScan search (README, OSM, TRN) | IMPLEMENTED | `USR:112-205` | Keyless; a saved URLSCAN key lifts the quota (`USR:30-37`, error text 186-195). |
| 61 | Username: GitHub public profile for the exact handle (OSM) | IMPLEMENTED | `USR:40-70, 208-220` | TRN and MAN do not mention it. 60 req/h limit surfaced. |
| 62 | Username: Keybase profile plus verified proofs (OSM) | IMPLEMENTED | `USR:73-109, 208-220` | Only `state == 1` proofs kept. |
| 63 | Username: WhatsMyName sweep (brief) | MISSING | `USR:222-236` (opt-in `whatsmyname=True`); `WRK:70-74` (not passed); `PANEL:696-701` (no card); used only at `agents/osint_heavy_agent/__init__.py:358` | Grepped Trace panel, worker and agent for `whatsmyname`/`wmn`: Structure Query merely suggests whatsmyname.app. Bloodhound Deep Dive only. No Trace promise source names it as a live source. |
| 64 | Username consent names URLScan (and GitHub, Keybase) before confirmation (MAN: "names URLScan ... records only URLScan as contacted") | PARTIAL | `PANEL:353`; `USR:208-220` | Consent names URLScan, GitHub and Keybase and all three are contacted; MAN s3 and TRN understate this (see 4b). |
| 65 | Email: services chosen individually in a dialog (OSM, TRN) | IMPLEMENTED | `PANEL:410-470`; `WRK:75-82`; `EML:228-238` | |
| 66 | Email: full address sent only to selected services (OSM) | IMPLEMENTED | `EML:236-257` | |
| 67 | EmailRep and Gravatar default on; HIBP and BreachDirectory default off (OSM, MAN) | IMPLEMENTED | `PANEL:424-432` | Hunter is also pre-ticked whenever its key exists (`PANEL:435-440`), not documented. |
| 68 | HIBP unselectable without a key (OSM, MAN) | IMPLEMENTED | `PANEL:430-431` | |
| 69 | Gravatar looked up by SHA-256 of the trimmed lower-cased address; address never sent (OSM) | IMPLEMENTED | `EML:105-142` (hash at 112) | Returns display name, location, verified accounts, or `found: false`. |
| 70 | Hunter mail-server check for an email, key-gated (OSM, ENV) | IMPLEMENTED | `EML:191-201`; `INTEL:616-643`; `PANEL:435-442` | |
| 71 | HIBP breach and paste records (ENV, KEYS) | IMPLEMENTED | `EML:32-78` | Names, data classes, paste count only. Self-skips as `skipped` without a key. |
| 72 | BreachDirectory is an open search needing no key and metadata-only (OSM, README "every source is metadata-only") | PARTIAL | `EML:81-102` (line 98: `"sources": sources[:10]` raw) | Provider response entries are returned verbatim with no field whitelist (DeHashed/Snusbase/LeakCheck do whitelist). Whether the live endpoint is still keyless and what fields it returns is NCV; if it returns password/hash fields they reach the card and the Saved Search. |
| 73 | Email provider default: breach sources off unless chosen | PARTIAL | `EML:204, 228` | `selected_sources` empty or None falls back to `DEFAULT_SOURCES`, which includes HIBP and BreachDirectory. The panel guards against an empty pick, but any other caller of `lookup(email)` runs everything. |
| 74 | Company: GLEIF legal entity search with LEI coverage caveat (OSM, TRN) | IMPLEMENTED | `CMP:267-305` | Page size 10. |
| 75 | Company: CourtListener dockets, metadata only, no PDFs (OSM, TRN, ENV) | IMPLEMENTED | `CMP:167-241`; `WRK:88-91` | `type=d`, field whitelist. Live API behaviour (anonymous access) is NCV. |
| 76 | CourtListener is "opt-in" (README Architecture, TODO.md:79) / "can add" (TRN) | PARTIAL | `WRK:88-91` (`court_records=True` always); `PANEL:355` | Not a separate choice: it runs on every Company Live Research once the single consent is given. The consent text does name it. |
| 77 | OpenSanctions: key-gated, named in consent only with a key, otherwise neither named nor contacted (OSM) | IMPLEMENTED | `PANEL:366-374`; `WRK:85-90`; `CMP:125-164` | Matches show `listed` flag. Live behaviour unverified by the author (OSM says so). |
| 78 | ICIJ Offshore Leaks, opt-in (README Architecture; brief) | MISSING | `CMP:322-333` (needs `offshore_leaks=True`); `WRK:83-92` (never passed); `PANEL:714-728` (no card); only caller `agents/osint_heavy_agent/__init__.py:383` | Grepped `offshore` across `PANEL`, `WRK`, `AGENT`: no Trace path and no UI choice. OSM's providers table also lists ICIJ under Trace. |
| 79 | Crypto: Blockstream (Bitcoin) and Blockscout (Ethereum) (README Architecture; brief) | MISSING | `providers/crypto_lookup.py:129-153`; callers only `agents/osint_heavy_agent/__init__.py:265, 428` | Grepped `crypto` in `PANEL`, `WRK`, `AGENT`: no query type, no validator, no card. A pasted BTC/ETH address auto-detects as Username (see 4c). |
| 80 | Company name sent "only to GLEIF" (OSM paragraph, "Company targets") | PARTIAL | `OSM:75-76` vs `PANEL:355, 372-374`; `WRK:90` | The name also goes to CourtListener (and OpenSanctions with a key). OSM:15 and the consent dialog are right; the paragraph is stale. |

### 3.5 Exposure Check

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 81 | Exposure Check: separate button for Domain, Company or Email (README, TRN, TIP/PANEL tooltip) | IMPLEMENTED | `PANEL:97-105, 482-525` | Not listed in OSM's Inputs table (see 4a). |
| 82 | Ransomware.live, keyless (README, TRN, ENV) | IMPLEMENTED | `EXP:162-236` | Email target sends the domain only (`EXP:129-131`). |
| 83 | Ahmia via clearnet; onion sites never contacted (README, tooltip) | IMPLEMENTED | `EXP:267-313` | Two GETs to ahmia.fi, using a desktop-browser User-Agent (`EXP:86-89`). |
| 84 | Intelligence X key-gated, index only, nothing downloaded (README, TRN, ENV) | IMPLEMENTED | `EXP:379-478` (no file/read call); `PANEL:552-558` | Tries paid host then free host. |
| 85 | DeHashed key-gated, metadata only, never passwords or hashes (README, TRN, ENV) | IMPLEMENTED | `EXP:483-571` | Company targets are skipped with a recorded reason (line 512-516). |
| 86 | Snusbase count-only endpoint (OSM, ENV) | IMPLEMENTED | `EXP:607-660` | |
| 87 | LeakCheck breach names, dates, kinds of data, no values (OSM, ENV) | IMPLEMENTED | `EXP:663-733`, whitelist `EXP:583-595` | |
| 88 | Breach sources never enabled without explicit consent (TRN) | PARTIAL | `PANEL:552-586` | Consent is the dialog, but IntelX, DeHashed, Snusbase and LeakCheck are pre-ticked when their keys exist, unlike the Email dialog where breach services start unticked. |
| 89 | Exposure verdict is trustworthy ("No exposure found in the sources that were queried") | PARTIAL | `PANEL:729-747`; `EXP:812-828`; direct-match logic `EXP:199-206` | Verdict ignores errors and cancellation: if every source fails or Stop is pressed first, the headline still reads "No exposure found". "Direct victim match" is a substring test on the SLD label, so "apple.com" matches a victim named "Pineapple ..." and raises the red "likely breach" headline. |
| 90 | Exposure tooltip describes the sources (PANEL tooltip) | PARTIAL | `PANEL:99-104` | Lists only ransomware.live, Ahmia, Intelligence X; omits DeHashed, Snusbase, LeakCheck. |

### 3.6 Saved Searches

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 91 | Successful runs appear in the Saved Searches rail (OSM, TRN) | IMPLEMENTED | `ui/panels/base.py:389-394`; `main.py:3639-3685, 4294-4316` | Cancelled live runs are also saved. |
| 92 | Saved searches can be filtered (OSM, TIP) | IMPLEMENTED | `main.py:1862-1865, 4297-4313` | Matches title or response text. |
| 93 | Saved searches can be renamed (OSM, TRN) | IMPLEMENTED | `main.py:1869, 4359-4378` | Double-click only; no button or tooltip says so. |
| 94 | Saved searches can be deleted (OSM, TRN, TIP) | IMPLEMENTED | `main.py:4380-4394` | Confirmation dialog. |
| 95 | Used as the starting point for a new search (OSM) | IMPLEMENTED | `main.py:4318-4357, 4396-4398` | Reopening fills target, type, provider/model; **New search** clears the panel. |
| 96 | Reopening restores target, query type, provider/model where available, structured response (OSM) | PARTIAL | `main.py:4335-4338` | `command` for a Live Research save is "Live Research - Domain" etc., which is not an item in the type box, so the type is not restored (the box keeps its previous value). Structure Query saves restore the resolved type (not "Auto-detect"). |
| 97 | Reopening performs no request (OSM, TRN, MAN) | IMPLEMENTED | `main.py:4318-4357` | No worker, no `authorize`, only JSON parse and rendering. |
| 98 | Reopen trail text is accurate | PARTIAL | `main.py:4349-4352` | Always says "stored query-planning result; no external sources were queried", also for a stored Live Research record. The saved activity lines are not restored (the summary card still lists sources). |

### 3.7 Tooltips

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 99 | Trace tooltips exist for target, type, provider, model, analyse, stop (TIP:102-110) | IMPLEMENTED | `ui/tooltips.py:102-110`; `main.py:464-481` (dotted names resolve to panel attrs); `PANEL:69, 76, 83, 107` | |
| 100 | Saved-search tooltips (TIP:53-57) | IMPLEMENTED | `ui/tooltips.py:53-57`; `main.py:1862-1879` | |
| 101 | Live Research tooltip describes what it checks | PARTIAL | `PANEL:90-94` | Says "WHOIS, DNS, certificate-transparency and web-archive sources"; wrong for username, email and company, and silent on the threat-intel sources. |

### 3.8 OSINT Keys tab and key checks

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 102 | Keys can be entered in Settings, OSINT Keys, Save Key; written to the same `.env`; work without restart (KEYS) | IMPLEMENTED | `DLG:637-669, 986-996`; `main.py:2949-2975`; providers read env at call time (`INTEL:83-85`, `EML:27-29`, `CMP:41-46`, `EXP:64-79`) | Save Key persists even if Settings is then cancelled. `CENSYS_ORG_ID` and `DOMAINTOOLS_API_USERNAME` have no UI field (documented as `.env` only). |
| 103 | Save Key checks the key straight away; Check button repeats it (KEYS) | IMPLEMENTED | `DLG:994, 978-979, 855-873` | |
| 104 | Button states Works / Rejected / Malformed / Limited / Offline (KEYS) | IMPLEMENTED | `KC:35-43`; `DLG:362-371, 386-402` | Also "? Error" and "No key" states (undocumented). |
| 105 | Malformed = shape cannot be right, "for example a VirusTotal key that is not 64 hexadecimal characters; nothing is sent" (KEYS) | PARTIAL | `KC:405-406` (VirusTotal has `hint` only); `KC:418, 425` (only HIBP and Intelligence X have an enforced `shape`); `KC:441-443, 449-452` | A wrong-length VirusTotal key is sent (inside the URL path, `KC:121`) and reported Rejected with a "usually 64 hex characters" hint. Only HIBP (32 hex), Intelligence X (UUID) and URLScan (via HTTP 400) can show Malformed. |
| 106 | Hover shows plan and what is left (KEYS) | IMPLEMENTED | `DLG:393-397`; detail strings in `KC:119-395` | |
| 107 | Check all keys runs only free checks, names the ones it skips (KEYS) | IMPLEMENTED | `DLG:1029-1068`; costly: AbuseIPDB, DeHashed, Snusbase (`KC:409, 420, 421`) | Tooltips state the cost (`DLG:391-392`). |
| 108 | A check asks only about the key, never about a target (KEYS) | IMPLEMENTED | `KC:136-142, 267-272, 335-341, 380-386` | Four checks send a placeholder query (example.com, example@example.com, a fake entity id), not the user's target. |
| 109 | Last result remembered until the key changes; key itself not stored (KEYS) | IMPLEMENTED | `DLG:393, 883-886`; `KC:60-63, 453-454` | Stores a 12-hex SHA-256 fingerprint. |
| 110 | "Used by" chips: filled = essential, outlined = extra; Explain on hover (KEYS) | PARTIAL | `DLG:334-356, 1086-1095`; `OK:48-223` | Works as described, but the Criminal IP row carries Censys text: `OK:105-113` gives `criminalip` the `key_hint` "Platform personal access token" and the Censys note, while `censys` has neither; the Criminal IP key field's placeholder (`DLG:964-965`) and hover therefore show Censys wording. Shodan is labelled "$49/mo" (main.py list) though Trace uses it keyless, so it sits under the Paid filter. |
| 111 | addy.io alias minting is user-triggered only, the one write-capable helper (README Architecture; ENV) | IMPLEMENTED | `DLG:712-755`; `WRK:515-538`; `providers/alias_mint.py:72-130` | Only caller is the button; self-skips without a key. |
| 112 | ENV comments (key sources, "skipped until key set", Intelligence X) | PARTIAL | `.env.example:22-26` vs `EXP:373-378, 396-413`, `OK:192-199`, TRN:29 | ENV says Intelligence X is "Paid API (its free public keys were discontinued)"; code, OK (`services/osint_keys.py`) and TRN all say a free account's key works on free.intelx.io. ENV header also names `Sentinel Fork` (stale). Remaining ENV statements verified. Pricing and quota statements in ENV are NCV. |

### 3.9 Not-code-verifiable promises (kept as separate rows for the test plan)

| # | Promise | Status | Evidence | Note |
|---|---|---|---|---|
| 113 | Trace "should be used lawfully"; training: do not use for harassment, stalking, or eligibility, employment, housing, credit or insurance decisions (README Safety, TRN Best practices) | NCV | Prompt `AGENT:56-57`; no UI or prompt text mentions eligibility or insurance | Policy statement. Nothing in the UI, consent text or prompt reminds the user; relevant for an insurance-operations user group. |
| 114 | Live APIs behave as coded: Gravatar v3 keyless, Mnemonic, DShield, crt.sh, CourtListener anonymous search, Ahmia token scrape, BreachDirectory keyless endpoint, OpenSanctions (OSM admits it was never run live) | NCV | Respective provider functions | Verify with one run per source on a test domain/IP/email/company; a mocked suite cannot show provider drift (README Testing says the same for models). |


---

## 4. Findings outside the traceability table

### 4a. Promises in UI, tooltips or training docs that are not in `docs/agents/osint.md` (and vice versa)

In UI/training but absent from OSM:
- **Exposure Check button** (README providers paragraph and TRN describe it; OSM Inputs and Outputs sections never mention the control, only the provider file in the Under-the-hood table).
- **Clear button**, **Auto-route**, **Saved searches "New search"** and the rename-by-double-click gesture.
- **Hunter pre-ticked when keyed** in the Email dialog; Exposure Check keyed sources pre-ticked.
- TRN says nothing about GitHub, Keybase, the eleven key-gated threat-intel sources, or CourtListener being automatic.
- KEYS describes check states but not the extra "? Error" / "No key" states, the Operational email field, the "Registered" tick and progress bar, the **Register** button (which also copies the operational email to the clipboard, `DLG:948-955`), the **Mint addy.io alias** button, or the filter chips.
- Manual test (MAN s3) lacks cases for Hunter, Exposure Check, key-gated IP/domain sources and OpenSanctions.

In OSM but absent from TRN/README/tooltips: Mnemonic/Cymru/DShield details, the OSINT Framework catalogue, partial-result dating, and the analytical caveat that GLEIF absence is not proof.

Stale in OSM: header and Under-the-hood name `agents/osint_agent.py`; the actual code is the package `agents/osint_agent/__init__.py` (README gets this right).

### 4b. Contradictions between sources

1. OSM:15 and the consent dialog (GLEIF **and** CourtListener) vs OSM:75-76 ("complete company name is sent only to the GLEIF Legal Entity Index").
2. README Architecture and OSM providers table (Trace calls `crypto_lookup`, ICIJ, WhatsMyName via `username_lookup`) vs the Trace panel and worker, which never reach them (Bloodhound only).
3. MAN s3 "Username Live Research names URLScan ... and records only URLScan as contacted" and TRN "Usernames can use URLScan" vs code and OSM (URLScan **and GitHub and Keybase**).
4. KEYS "a VirusTotal key that is not 64 hexadecimal characters ... nothing is sent" vs `KC:405-406` (hint only, key is sent).
5. ENV "Intelligence X ... Paid API (its free public keys were discontinued)" vs `services/osint_keys.py:192-199`, TRN:29 and `EXP:373-378` (free-account key accepted on free host).
6. README Architecture / TODO.md:79 "opt-in ICIJ Offshore Leaks and CourtListener" vs `WRK:88-91` (CourtListener always on for Trace; ICIJ never) vs TRN "can add".
7. TRN "Email services are chosen individually, breach sources are never enabled without explicit consent" vs Exposure Check dialog pre-ticking keyed breach sources.
8. README "Every source is metadata-only" vs BreachDirectory raw passthrough (`EML:98`).
9. OSM "Reopening restores ... query type" vs Live Research saves (`main.py:4335-4338`).
10. Panel Live Research and Exposure tooltips vs the sources actually contacted (rows 90, 101).
11. ENV header path "Sentinel Fork" vs README and KEYS ("Sentinel").
12. `services/osint_keys.py` Criminal IP/Censys hint and note swapped vs ENV/INTEL docs (Censys is the Platform personal access token).
13. README/OSM "Auto-detect records the resolved type" vs Live Research trail lacking the auto-detected flag (minor).

### 4c. Undocumented behaviour a user (or tester) would want to know

Behaviour and risk:
1. **Unconsented background download.** Showing the Trace panel starts a once-per-process, weekly-throttled GET of the OSINT Framework JSON from raw.githubusercontent.com (`PANEL:50-54`, `CAT:131-171`). Disclosed in OSM, but not in the Activity trail and not tied to the "Structure Query contacts nothing" trail line; also happens in Local-only mode.
2. **Live Research bypasses the request guard.** No `authorize_request`, so it ignores the registry (agent disabled, provider rules) and logs only via `record_external_research`. Trace's registry entry carries `allowed_tools = ["General Chat","Summarize"]` and a EUR 2.00 per-paid-request cap (`agent_catalog.py:33-35`, `validator.py:87-93`); both apply to Structure Query only and are documented nowhere in the Trace docs.
3. **No private-address filter.** Any syntactically valid IP, including 10.x, 192.168.x, 127.0.0.1 and link-local, is sent to Mnemonic, DShield, InternetDB and every keyed IP service (`AGENT:194-200`, `DOM:497-528`).
4. **Auto-detect misclassification.** A handle with a dot ("john.smith") resolves to Domain and goes to WHOIS/DNS/crt.sh/Wayback (`AGENT:160-163`); BTC/ETH addresses resolve to Username and are sent to URLScan, GitHub and Keybase (`AGENT:105, 132`); Company is never auto-detected (an organisation name becomes Person and Live Research is refused); internationalised domains (bücher.de) fail the ASCII-only domain regex (`AGENT:101-104`).
5. **Person-local-only is a UI choice, not a guarantee.** Person and Company share one validator (`AGENT:140-141, 218-224`). Selecting Company with a personal name sends it to GLEIF, CourtListener (party-name search) and, with a key, OpenSanctions (PEP screening).
6. **Stop (live) leaves a running thread.** Buttons re-enable immediately (row 36); a second Live Research replaces `self.worker`, risking "QThread destroyed while running". Cancelled cloud streams record no cost although the provider may have billed (WRK:221-223 returns before `usage_signal`).
7. **DNS for IP targets always fails** (row 43), so every IP run shows a DNS error and "Errors: DNS" in the summary.
8. **Summary card parse defect** (row 16).
9. **Exposure heuristics.** Substring "direct victim" matching and a reassuring headline when all sources errored (row 89).
10. **Local persistence of third-party data.** Saved Live Research stores the full JSON, including WHOIS contact emails and org (`DOM:93-95`), GitHub public email and location (`USR:53-68`) and Gravatar details, as plain JSON in the chat history; deleting from Saved Searches is the only removal path.
11. **Email Exposure Check sends only the domain** to Ransomware.live and Ahmia although the dialog says the target is sent to the selected services (`EXP:128-131`); the full address goes to IntelX, DeHashed, Snusbase, LeakCheck.
12. **Ahmia is fetched with a desktop-browser User-Agent**; every other source uses `Sentinel-OSINT/2.0` (`EXP:86-89`). The latter identifies Sentinel to all contacted services.
13. **Key handling details.** VirusTotal key check puts the key in the URL path (`KC:121`); Shodan lookups and its check put the key in the query string (`INTEL:306-307, 349-350`; `KC:160`). Errors are scrubbed (`INTEL:103-107`), but proxies or server logs may retain URLs.
14. **Dead code.** `providers/result_normalizer.py` (5 lines) has no caller.
15. **Rename discoverability.** Saved-search rename exists only as a double-click.
16. **Bloodhound-only adapters.** `crypto_lookup`, `whatsmyname`, ICIJ and the Deep-Dive username sweep exist and are tested (`tests/test_crypto_lookup.py`, `tests/test_whatsmyname.py`) but are not Trace features; test cases for Trace must not expect them.
17. **Catalogue unit tests absent** (TODO.md:80), so the Trace filter promises (rows 19-23) rest on hand-checking.
18. **Long runs.** A domain run with several keyed sources executes sequentially with no progress bar; worst case is minutes, and Stop only acts between sources.
19. **Domain DNS reaches the target's name servers.** WHOIS and DNS are not "passive" in the sense the catalogue uses the word; the DNS step queries the target name through the user's resolver (twice with the Cymru step) and WHOIS contacts registry servers.

---

## 5. Must fix before test phase

| # | Fix (one line each) | Rows |
|---|---|---|
| 1 | Make Stop on Structure Query consistent: in `ChatWorker` return without `finished_signal` after cancel on every branch, and in `OsintPanel._on_error` treat "Request cancelled by user." as a stop (keep partial text, status "Stopped."). | 37 |
| 2 | Guard `live_research`/`exposure_check` with `is_running()` (or keep buttons disabled until the worker's `finished` fires) so a second run cannot replace a live QThread. | 36 |
| 3 | Fix `OsintPanel.parse_sections` summary pattern to `##\s*SUMMARY[^\n]*\n(.*)$` and add a test using the real header "## SUMMARY & NEXT STEPS". | 16 |
| 4 | Give `_dns` an IP branch (PTR via `dns.reversename`) or drop the DNS source for IP targets, and fix the consent text to match. | 43 |
| 5 | Whitelist BreachDirectory fields in `_breachdirectory` (names/count only) before returning, or remove it; align README "metadata-only". | 72 |
| 6 | Swap the `key_hint`/`note` from `criminalip` to `censys` in `services/osint_keys.py`; add a test that notes mention the right vendor. | 110 |
| 7 | Correct `api_keys.md` (VirusTotal key is hinted, not blocked) or give `virustotal` a `shape=` in `key_check.CHECKS`. | 105 |
| 8 | Reconcile the docs: OSM:75-76 company sentence; OSM providers table and README Architecture (Trace does not call crypto/ICIJ/WhatsMyName); MAN s3 and TRN username sources (URLScan, GitHub, Keybase); ENV Intelligence X wording; ENV header path; OSM file paths; add the Exposure Check control to OSM Inputs. | 63, 64, 78-80, 112, 4a/4b |
| 9 | Decide and document CourtListener: either add a consent checkbox (matching "opt-in") or change README/TODO/TRN to "always included with Company Live Research". | 76 |
| 10 | Make the Exposure verdict honest: say "not checked / incomplete" when sources errored or the run was cancelled, and tighten "direct victim" to whole-label or domain equality. | 89 |
| 11 | Restore the query type on reopening a Live Research save (parse the type after "Live Research - ") and change the reopen trail line to describe stored live records. | 96, 98 |
| 12 | Record key-gated domain/IP sources omitted for lack of a key as `sources_skipped` (or state "keyed sources not configured" in the summary) so the "skipped-before-contact" promise holds for domains and IPs. | 34 |
| 13 | Make `email_lookup.lookup` and `exposure_lookup.lookup` treat an empty `selected_sources` as "nothing selected" instead of "all". | 73 |
| 14 | Add a private/loopback/link-local/reserved-IP check to Trace's IP validation (or a consent warning) before anything is sent. | 4c-3 |
| 15 | Show the resolved type (and "auto-detected") in the Live Research and Exposure consent dialogs; reject or reroute crypto-looking and dotted-handle inputs, and add a Company auto-detect hint. | 6, 4c-4 |
| 16 | Update the Live Research and Exposure tooltips to the real source lists. | 90, 101 |
