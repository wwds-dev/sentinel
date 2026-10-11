# BLOODHOUND — Deep OSINT investigation

_One of Sentinel's built-in agents (`~/Documents/lab/active/sentinel/agents/osint_heavy_agent/`). Split out into its own project on 2026-09-14 — see the parent project's README.md for how Sentinel's agent roster fits together._

`key: osint_heavy` · class: `agents/osint_heavy_agent/__init__.py → OsintHeavyAgent` · panel: `ui/panels/osint_heavy.py → OsintHeavyPanel`

## What it does
Produces a research-grade, five-section intelligence dossier on a target, with an embedded threat score, confidence score, and a curated tradecraft tool library (~80 tools grouped by target type: people, username, email, domain/IP, breach, phone, image, archive, geolocation, social, due diligence and sanctions, cryptocurrency, news and events), topped up from the OSINT Framework catalogue. Accepts an optional **image** and folds its EXIF metadata into the analysis. It also provides **Local File Discovery**, a separate read-only search for files in folders the user deliberately selects. File-search metadata never enters an AI prompt or leaves the device.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Target identifier | Person / username / email / domain / IP / organisation. |
| Target type | Guides which tool families and pivots are emphasised. **Crypto Address** takes a Bitcoin or Ethereum address. **Auto-detect** tries, in order: crypto address, email, IP address, domain, then username (no spaces) or person. An IPv4 or IPv6 address is recognised even with a scheme, `:port` (numeric only), `/CIDR`, `[..]` brackets or a `%zone` (`_extract_ip`), so it goes to the domain/IP provider and picks the IP catalogue rather than the username sweep. |
| Scope | `Quick Scan` (3–5 pts/section), `Standard Investigation`, or `Deep Dive` (exhaustive). |
| Objective / context | Free-text investigation goal. |
| Add target image | Optional collapsed section; EXIF is parsed and injected into the prompt. |
| Model override | Optional provider/model change; a long-context reasoning model is selected by default. |
| Investigate / Stop | Run, or cancel while a request is active. Save and Clear appear with results. |

## File Discovery

Expand **File Discovery — selected locations only**. Choose **This Mac** and add
folders, or choose **Remote SSH machine** and enter an owned machine's hostname,
IP address, or `~/.ssh/config` alias, SSH user, port, and absolute remote paths.
Remote discovery uses SFTP, your existing SSH agent/keys, and strict
`known_hosts` verification. Sentinel does not store passwords or private keys.

Searches can match a full or partial file name, a comma-separated list
of extensions, minimum/maximum size in MB, and an optional modified-date range.
The result table shows name, full path, type, size, and modified time.
Double-clicking a result reveals its containing folder.

The search is deliberately separate from dossier generation. Local and remote
searches read file
metadata only, never reads file contents, never uploads paths or results, never
follows directory symbolic links, and cannot change files. Cancellation keeps
matches already found. Searches stop after 5,000 matches or 250,000 inspected
entries; select a smaller folder or narrower filters to continue. **Open SSH
Terminal** opens the operating system's SSH handler for an interactive session;
arbitrary remote commands are not executed inside Sentinel or by its AI worker.

## Live collection

Before the model is called, Bloodhound collects real public-source data for the
target: WHOIS, DNS, Team Cymru IP-to-ASN, Mnemonic passive DNS, crt.sh and the
Wayback Machine for domains (for IPs, SANS DShield and Shodan InternetDB
instead of crt.sh and Wayback, plus IPinfo with `IPINFO_API_KEY` and Criminal
IP with `CRIMINALIP_API_KEY` — each omitted without its key); EmailRep,
Gravatar (by address hash), HIBP (with a key) and BreachDirectory for emails;
URLScan, GitHub and Keybase for usernames; GLEIF, ICIJ Offshore Leaks and
CourtListener court dockets for organisations. CourtListener works without a
key (`COURTLISTENER_API_KEY` only raises the rate limit) and returns docket
metadata only, never filing text or PDFs; a name match is a lead, not proof the
case concerns the target. The prompt treats Keybase's signed
proofs as the strongest identity link, Offshore Leaks matches as name
similarity (being named in the leaks is not evidence of wrongdoing), and
shared-hosting passive DNS and DShield history as not attributable to the
target. For domain, organisation and
email targets it also runs a **dark-web exposure check** (`providers/exposure_lookup.py`)
— ransomware.live and Ahmia are free and always run; Intelligence X runs only
when `INTELX_API_KEY` is set and DeHashed (breach names and counts for emails
and domains, never leaked passwords or hashes) only when `DEHASHED_API_KEY` is
set; each reports itself as skipped otherwise. Text metadata only; no
onion site is contacted and nothing is downloaded. Phone and person-name targets
contact nothing. The permission check runs **first**, so a request the guard
refuses never sends the target anywhere, and collection runs on a worker thread
with progress in the status line. Stop during collection cancels it, closes the
request unbilled, and never calls the model.

A **Deep Dive** on a username also sweeps the
[WhatsMyName](https://github.com/WebBreacher/WhatsMyName) site list (CC BY-SA
4.0): about 600 profile URLs requested directly from this Mac, up to 12 at a
time, with a 120-second budget. The list is downloaded once a week into
`data/cache/wmn-data.json`, with a stale copy used if the download fails. Sites
the list marks invalid, sites behind bot protection (their challenge pages
would make every answer a guess), and its NSFW category are left out. A hit
needs the site's exact "exists" status code **and** marker text; everything
else is a confirmed miss or counted as inconclusive, with the top reasons
reported.

Routers with flood protection refuse new connections when one device opens
them too fast, and this sweep does exactly that. So a refused connection is
treated as the network pushing back, not as an answer: when 5 of the last 20
answers are refusals, the sweep halves how many requests it keeps in flight
(never below 2) and pauses 3 s, then climbs back one step after each clean
run of 20. Refused sites get one retry at the end, at most 4 at a time, and
the result's `backoff` block reports slow-downs, retries and recoveries. On a
router that refused 218–293 of ~600 connections per sweep, this removed the
refusals entirely and found more accounts, at about 56 s instead of 20–25 s.
If more than a quarter of sites still could not be reached, the result
carries a `network_warning`, and a missing hit proves nothing. The
prompt tells the model to treat hits as same-name accounts to corroborate, not
as one identity.

**Crypto Address** targets (Bitcoin legacy, P2SH or bech32 addresses, and
Ethereum `0x…` addresses; Auto-detect recognises them before trying a
username) go to **Blockstream** for Bitcoin (balance, amounts received and
sent, transaction count, latest activity) or **Blockscout** for Ethereum (ETH
balance, transaction and token-transfer counts, ENS name, contract flag, and
Blockscout's public tags and scam flag). Neither needs a key. The prompt tells
the model that on-chain data shows what an address did, never who controls it,
and that exchange addresses pool many users' funds. Organisation targets are
also screened with **OpenSanctions** when `OPENSANCTIONS_API_KEY` is set; a
skipped check is reported as "no key", not as a clean result.

## Catalogue tools

After the built-in library, the system prompt adds tools from the **OSINT
Framework catalogue** (osintframework.com, MIT licence) for the target type:
10 for a Quick Scan, 25 for a Standard Investigation, 45 for a Deep Dive. Only tools the
catalogue marks live and not deprecated are used, shadow libraries are
blocked, and hosts already in the built-in library are skipped. Each line is
tagged with pricing, account and API needs; tools marked **ACTIVE** interact
with the target, and the prompt requires the dossier to say so wherever it
recommends one. The catalogue shares Trace's weekly cache in
`data/cache/osint-framework.json`; prompt building never downloads it.

## Outputs — the dossier (exact section headers the parser keys off)
`## 1. OVERVIEW` (with `THREAT LEVEL: X/10`, `CONFIDENCE: X%`, `SOURCES REFERENCED: X`) · `## 2. DIGITAL FOOTPRINT` · `## 3. INFRASTRUCTURE / SOCIAL PROFILE` · `## 4. RISK & RED FLAGS` · `## 5. METHODOLOGY & TOOLS`. Sidebar indicators (threat bar, confidence, sources) are regex-parsed from those exact lines.

## How it works
Live collection and message-building are separate calls. `OsintHeavyAgent.collect_live(target, target_type, scope, on_progress=, should_stop=)` runs the real provider lookups first, on the panel's `LiveCollectionWorker` thread and only after the permission check — a free, no-model step — and remembers both the raw results (`last_live_results`) and how many public sources were actually contacted (`last_source_count`, from `real_source_count()`). The panel then calls `OsintHeavyAgent.build_messages(target, target_type, scope, objective, image_metadata, live_results=...)`, which stays intentionally offline: it only injects whatever `live_results` it's handed, never performs network lookups itself. This split keeps `build_messages` unit-testable without network access, and lets the UI's Sources gauge show the real contacted-source count instead of the model's self-declared "SOURCES REFERENCED" line.

`target_type` from the panel is a UI label ("Email Address", "Domain / IP", "Auto-detect", …), not the lowercase token the dispatcher used to compare against directly — `_normalize_target_type()` now maps either form to the right provider (a real bug fix: live collection previously silently no-opped for any UI label other than a bare lowercase token). An "Organisation" target now resolves through `providers/company_lookup.py` (a legal-entity registry) rather than being folded into the domain/WHOIS provider.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/osint_heavy_agent/__init__.py` | `OsintHeavyAgent`: `collect_live()`, `build_messages()`, the tool library + section spec, target-type normalisation, and `real_source_count()`. |
| `providers/crypto_lookup.py` | Crypto Address targets: Blockstream (Bitcoin) and Blockscout (Ethereum). |
| `providers/whatsmyname.py` | Deep Dive username sweep: site-list cache, per-site check, bounded parallel sweep. |
| `services/osint_catalog.py` | OSINT Framework catalogue: weekly cached download, filtering, and per-agent tool selection. |
| `ui/workers.py: LiveCollectionWorker` | Runs live collection without freezing the interface. |
| `ui/panels/osint_heavy.py` | Panel, optional image workflow, structured dossier cards, and indicators. |
| `services/local_file_search.py` | Bounded, read-only metadata search and filters. |
| `services/remote_file_search.py` | Strict-host-key SFTP traversal for authenticated machines. |
| `ui/workers.py: LocalFileSearchWorker` | Runs file discovery without freezing the interface. |
| `ui/panels/osint_heavy.py: investigate()` | Reads form + EXIF, authorises, runs live collection on a worker, then fires `ChatWorker`. |
| `main.py: osint_heavy_save()` | Saves dossier to `.txt`. |

## Extend it
- **New tool family**: add a block to the tool library in the system prompt (keep the `Name: URL — description` format).
- **New indicator**: emit a new `KEY: value` line in section 1 and parse it in the panel's indicator update.
- **Live pivots**: add a source to the matching `providers/*_lookup.py` (or a new provider dispatched from `_run_providers()`); report it in `sources_contacted` so the Sources gauge counts it.
- Keep section headers verbatim — the parser matches them exactly.

## Requirements
A large-context reasoning model is recommended automatically. Optional OSINT API keys in `.env`. Pillow (bundled) handles EXIF. Local search uses the Python standard library. Remote search requires Paramiko and an existing SSH identity; neither mode requires an AI model or API key.
