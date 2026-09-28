# BLOODHOUND — Deep OSINT investigation

`key: osint_heavy` · class: `agents/osint_heavy_agent.py → OsintHeavyAgent` · panel: `ui/panels/osint_heavy.py → OsintHeavyPanel`

## What it does
Produces a research-grade, five-section intelligence dossier on a target, with an embedded threat score, confidence score, and a curated tradecraft tool library (~80 tools grouped by target type: people, username, email, domain/IP, breach, phone, image, archive, geolocation, social, due diligence and sanctions, cryptocurrency, news and events), topped up from the OSINT Framework catalogue. Accepts an optional **image** and folds its EXIF metadata into the analysis. It also provides **Local File Discovery**, a separate read-only search for files in folders the user deliberately selects. File-search metadata never enters an AI prompt or leaves the device.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Target identifier | Person / username / email / domain / IP / organisation. |
| Target type | Guides which tool families and pivots are emphasised. |
| Scope | `Quick Scan` (3–5 pts/section), `Standard`, or `Deep Dive` (exhaustive). |
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
Wayback Machine for domains (SANS DShield instead of crt.sh and Wayback for
IPs); EmailRep, Gravatar (by address hash), HIBP (with a key) and
BreachDirectory for emails; URLScan, GitHub and Keybase for usernames; GLEIF
and ICIJ Offshore Leaks for organisations. Phone and person-name targets
contact nothing. The prompt treats Keybase's signed proofs as the strongest
identity link, Offshore Leaks matches as name similarity (being named in the
leaks is not evidence of wrongdoing), and shared-hosting passive DNS and
DShield history as not attributable to the target. The permission check runs **first**, so a request the guard
refuses never sends the target anywhere, and collection runs on a worker thread
with progress in the status line. Stop during collection cancels it, closes the
request unbilled, and never calls the model.

A **Deep Dive** on a username also sweeps the
[WhatsMyName](https://github.com/WebBreacher/WhatsMyName) site list (CC BY-SA
4.0): about 600 profile URLs requested directly from this Mac, 12 at a time,
with a 90-second budget. The list is downloaded once a week into
`data/cache/wmn-data.json`, with a stale copy used if the download fails. Sites
the list marks invalid, sites behind bot protection (their challenge pages
would make every answer a guess), and its NSFW category are left out. A hit
needs the site's exact "exists" status code **and** marker text; everything
else is a confirmed miss or counted as inconclusive, with the top reasons
reported. When more than a quarter of sites could not be reached at all, the
result carries a `network_warning`. A VPN or firewall connection-rate limit
produces exactly that pattern, and it means a missing hit proves nothing. The
prompt tells the model to treat hits as same-name accounts to corroborate, not
as one identity.

## Catalogue tools

After the built-in library, the system prompt adds tools from the **OSINT
Framework catalogue** (osintframework.com, MIT licence) for the target type:
10 for a Quick Scan, 25 for Standard, 45 for a Deep Dive. Only tools the
catalogue marks live and not deprecated are used, shadow libraries are
blocked, and hosts already in the built-in library are skipped. Each line is
tagged with pricing, account and API needs; tools marked **ACTIVE** interact
with the target, and the prompt requires the dossier to say so wherever it
recommends one. The catalogue shares Trace's weekly cache in
`data/cache/osint-framework.json`; prompt building never downloads it.

## Outputs — the dossier (exact section headers the parser keys off)
`## 1. OVERVIEW` (with `THREAT LEVEL: X/10`, `CONFIDENCE: X%`, `SOURCES REFERENCED: X`) · `## 2. DIGITAL FOOTPRINT` · `## 3. INFRASTRUCTURE / SOCIAL PROFILE` · `## 4. RISK & RED FLAGS` · `## 5. METHODOLOGY & TOOLS`. Sidebar indicators (threat bar, confidence, sources) are regex-parsed from those exact lines.

## How it works
`OsintHeavyAgent.collect_live(target, target_type, scope, on_progress=, should_stop=)` runs the live lookups on `LiveCollectionWorker`; `OsintHeavyAgent.build_messages(target, target_type, scope, objective, image_metadata, live_results=)` then assembles the target + scope hint + collected data + optional EXIF block, offline. The system prompt carries the full tool library and the strict section format the UI depends on.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/osint_heavy_agent.py` | `OsintHeavyAgent` + the tool library + section spec. |
| `ui/panels/osint_heavy.py` | Panel, optional image workflow, structured dossier cards, and indicators. |
| `providers/whatsmyname.py` | Deep Dive username sweep: site-list cache, per-site check, bounded parallel sweep. |
| `services/osint_catalog.py` | OSINT Framework catalogue: weekly cached download, filtering, and per-agent tool selection. |
| `ui/workers.py: LiveCollectionWorker` | Runs live collection without freezing the interface. |
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
