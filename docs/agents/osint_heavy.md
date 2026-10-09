# BLOODHOUND — Deep OSINT investigation

`key: osint_heavy` · class: `agents/osint_heavy_agent/__init__.py → OsintHeavyAgent` · panel: `ui/panels/osint_heavy.py → OsintHeavyPanel`

## What it does
Produces a research-grade, five-section intelligence dossier on a target, with an embedded threat score, confidence score, and a curated tradecraft tool library (~80 tools grouped by target type: people, username, email, domain/IP, breach, phone, image, archive, geolocation, social, due diligence and sanctions, cryptocurrency, news and events, plus Google dorks and OSINT frameworks), topped up from the OSINT Framework catalogue. Accepts an optional **image**: its EXIF metadata is shown in the panel and joins the model prompt only if you tick **Include this image's metadata**. It also provides **File Discovery**, a separate read-only search for files in folders the user deliberately selects, on this Mac or on an SSH machine. File-search results are never sent to an AI provider or uploaded; a remote search connects to the SSH host you entered.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Target | Person / username / email / domain / IP / organisation / phone / crypto address. |
| Target type | `Person`, `Username`, `Email Address`, `Domain / IP`, `Organisation`, `Phone Number`, `Crypto Address`, `Auto-detect`. Guides which tool families and pivots are emphasised and which public sources are contacted. **Crypto Address** takes a Bitcoin or Ethereum address. **Auto-detect** tries, in order: crypto address, email, IP (IPv4/IPv6, even with a scheme, port, `/CIDR`, brackets or zone), domain, then username (no space, no `@`), else person. Detection is by format only: the checksum of a Bitcoin or Ethereum address is not verified, and a dotted handle such as `john.doe` reads as a domain. |
| Scope | `Quick Scan` (3–5 pts/section, 10 catalogue tools), `Standard Investigation` (8–12 pts/section, 25 tools), or `Deep Dive` (exhaustive, 45 tools; on a username it also runs the WhatsMyName sweep). The point counts are a request to the model; the tool counts and the sweep are enforced by the code. |
| Objective | Free-text investigation goal; sent to the model with the target. |
| Provider / model (run bar) | The run bar holds the provider and model pickers, **Auto-route**, Stop and Investigate. Sentinel pre-selects its recommendation for Bloodhound, a long-context reasoning model (the built-in baseline is Anthropic `claude-opus-5-5`; loaded model ratings can change the pick), which is a paid cloud model; pick Ollama to keep the dossier step on this Mac. **Auto-route** re-picks the provider and model from the text of every enabled field in the panel (including the File Discovery fields while that section is open); the router runs on this Mac and sends nothing to a provider. There is no separate "Model override" section. |
| Target Image | An always-visible group box (not collapsed): **Browse…**, **Clear Image**, an EXIF summary line and, once an image is chosen, a details pane. See Image and EXIF. |
| Include this image's metadata (EXIF, GPS) in the model prompt | Off by default. Gates whether the EXIF block is sent to the selected model. The box keeps its state when you clear or change the image. |
| File Discovery — selected locations only | A tick-to-open group box, closed by default. See File Discovery. |
| Investigate / Stop | Investigate runs; while a request is active it is replaced by Stop. |
| Save Report / Clear | **Save Report** is disabled until a dossier exists and writes the model's dossier text to a `.txt` file (default name `osint_dossier_<target>_<timestamp>.txt`); it does not include the collected records, the EXIF block or any footer. **Clear** is always available and empties the target, objective, results and image; it does not cancel a request in flight and does not touch File Discovery. |

## Before anything is sent

**Investigate** runs these steps in order; each can end the request before the next begins.

1. **Permission check** (the shared request guard). A paid provider shows *Confirm External API Request* (provider, model, approximate tokens, estimated cost), and provider permissions and budget caps are checked; Ollama shows no dialog. A request the guard refuses never sends the target anywhere. The cost in that dialog is estimated from the target text alone, so it understates the real request.
2. **Public-source consent.** *Contact public sources?* names every source the target will be sent to for this target type and scope (the key-gated extras for domains, IPs and exposure checks are named only when their key is saved) and defaults to **No**. Declining closes the request as cancelled: nothing is contacted and nothing is billed. Target types that contact nothing (a Phone Number, or a person name containing a space) ask nothing. The execution mode is not consulted: "Local only" does not skip this step or the collection, so this dialog is the control.
3. **Live collection** on a worker thread (see Live collection).
4. **Budget re-check on the real prompt.** The assembled request (system prompt, catalogue lines, collected records and, if ticked, the image metadata) is checked against the per-agent, session and daily budget caps. Over a cap, it is blocked ("Request Blocked"), the request is abandoned as blocked and the model is never called. This re-checks the caps only; there is no second confirmation dialog.
5. **Model call**, streamed where the provider streams.

> Known limitation: the consent list is built from the target type, not from the lookups themselves, so it can differ from what runs. For an IP address it names crt.sh and the Wayback Machine (not queried for IPs) and omits SANS DShield and Shodan InternetDB (queried). For an email it names HIBP and Hunter even when no key is saved (they then skip themselves). For a Crypto Address it names both explorers although only the one matching the address is queried.

**Stop.** During collection, Stop cancels it, closes the request unbilled and never calls the model; providers check for Stop between sources, so the source already in flight keeps running in the background until it finishes or times out, after the panel has been released.

> Known limitation: Stop during the model call is not clean. A streaming reply ends as an error (`[Error] Request cancelled by user.`; the request is abandoned and logged as an error, not as cancelled). A reply that arrives all at once is not cancelled at all: it is still recorded, billed and shown when it finishes.

**What reaches a model.** The selected model receives the system prompt (tool library plus catalogue lines), the target, target type, scope, objective, the collected records as JSON (including per-source errors) and, only if the box is ticked and an image is attached, the EXIF block. It never receives the image file or the image's file name, and nothing from File Discovery.

**What is kept.** The Run log keeps the target (first 200 characters), provider, model, status and cost. Saved Chats keeps the request text as sent (instructions, target, collected data and the image-metadata block if included) together with the dossier. File Discovery is not logged and its host, user and folders are not saved.

## Image and EXIF
- **Parsing.** Pillow's `_getexif()`, which exists only for JPEG, PNG and WebP. The file dialog also offers TIFF, BMP and HEIC.
  > Known limitation: for those three the panel shows "No EXIF data found" (and, in the details pane, that the image "may have been stripped") even when the file has EXIF; HEIC also needs a decoder (`pillow-heif`) that Sentinel does not install.
- **Shown in the panel (never leaves the Mac).** A summary line (date, device, software, GPS) and a details pane: the EXIF table (DateTimeOriginal, DateTime, DateTimeDigitized, Make, Model, Software, LensMake, LensModel, ImageWidth, ImageLength, Orientation, Flash, FocalLength), decimal GPS with Google Maps, OpenStreetMap and SunCalc links, reverse-image links (TinEye, Google Images, Yandex Images, Bing Visual Search) and face-search links (PimEyes, FaceCheck.ID, Lenso.ai). The links open each service in your browser; Sentinel uploads nothing, and clicking a map link sends those coordinates to that site.
- **Sent to the model, only when the box is ticked.** DateTimeOriginal, DateTime, Make, Model, Software, LensMake, LensModel, ImageWidth, ImageLength, Orientation, Flash, FocalLength; and, when GPS is present, decimal latitude/longitude, a Google Maps URL, altitude and camera direction. Not forwarded: the file name, MakerNote, UserComment, Artist, Copyright, thumbnails and other tags. If nothing could be read, the model is sent the line "No EXIF metadata could be extracted (data may have been stripped)." The prompt asks the model to comment on what the metadata reveals or conceals; an image proves nothing about identity, location or ownership.
  > Known limitation: a GPS tag that cannot be read is turned into `0.0, 0.0` (with a Maps link) rather than omitted.
  > Known limitation: the details pane builds HTML from the file name and EXIF text without escaping it, and its links open in the browser, so a crafted file name or EXIF value can add clickable links to the pane.

## File Discovery

Tick **File Discovery — selected locations only** to open it. It starts closed, there are no default folders, and nothing is searched until you press **Search Selected Folders**. Choose **This Mac** and add folders with **Add Folder…** (an added folder is resolved to its real path first), or choose **Remote SSH machine** and enter the SSH host / IP address / `~/.ssh/config` alias, SSH user (required), port (22 by default) and absolute remote folders, comma-separated (so a folder name containing a comma cannot be entered). Remote discovery uses SFTP, your existing SSH agent/keys, and strict `known_hosts` verification. Sentinel does not store passwords or private keys, and it authenticates only with SSH access you already hold: use it on machines you own or are authorised to access.

Searches can match a file name (**Contains** or **Exact**, case-insensitive, no wildcards), a comma- or semicolon-separated list of extensions (the final suffix only, so `.tar.gz` is matched as `.gz`), minimum/maximum size in MB (1 MB = 1,048,576 bytes), and an optional modified-date range (**Modified after** and **Before**, each active only when its box is ticked and starting at today minus one year and today; the range runs from the start of the first day to the end of the last, in this Mac's time zone, remote files included). The result table shows name, full path, type (the lower-cased extension, or a dash), size, and modified time, and has **Clear Results**. Click a column header to sort.
> Known limitation: the Size column sorts by its displayed text, so "10.0 KB" sorts before "2.0 KB".

Double-clicking a result: for a result on this Mac it opens the containing folder (the file is not selected; if it has gone, you get "This file is no longer available"). For a remote result it copies the remote path to the clipboard, because Sentinel cannot open a remote folder; use **Open SSH Terminal** to go there.

**What the search does and does not do.** The search is deliberately separate from dossier generation. Local and remote searches read file metadata only (directory listings and `stat`/SFTP attributes), never read file contents, and never upload paths or results. Every symbolic link is skipped, to a folder or to a file, so linked files do not appear in the results. Hidden files and package contents are searched. The search code contains no call that writes, renames or deletes; the SFTP session itself is not read-only at protocol level, so this is enforced by the code, not by the server. Arbitrary remote commands are not executed inside Sentinel or by its AI worker.

**Remote connection.** The host key is checked strictly: Sentinel loads `~/.ssh/known_hosts` and rejects any host not in it (`paramiko.RejectPolicy`), and a host whose key has changed is refused too. Nothing is ever added to `known_hosts` automatically, so a new host has to be accepted once with your normal SSH client (for example `ssh user@host` in a terminal) before Sentinel will search it. Authentication uses your SSH agent (found through `SSH_AUTH_SOCK`) and the default private-key files in `~/.ssh` (a key that needs a passphrase is skipped unless the agent holds it); the connection is made with `password=None`. Connect, banner and authentication each time out after 12 s. `~/.ssh/config` is read for the host you typed: HostName, User, Port and ProxyCommand are used (a ProxyCommand runs as a local process); IdentityFile, IdentityAgent, ProxyJump and UserKnownHostsFile are ignored.
> Known limitation: a User or Port set for that host in `~/.ssh/config` (including through a `Host *` block) overrides the User and Port typed in the panel.

**Limits, Cancel and errors.** A search stops when it has 5,000 matches or has inspected 250,000 entries (files and folders together; the count shown can read 250,001). It keeps what it found and reports "Safety limit reached … Narrow the folder or filters for more specific results." **Cancel** keeps the matches found so far; it is checked between folders and between entries, so it cannot interrupt a remote connection attempt or a single folder listing (there is no SFTP timeout). The window's **Stop** button, closing or quitting Sentinel (window close or the menu-bar Quit) and **Emergency Reset** also cancel a running search; closing waits up to two seconds for each worker and then force-stops it. The menu-bar item reads "Working — Bloodhound" while a search runs. A folder that cannot be read is listed in a warning (the first 12) and the search carries on.
> Known limitation: anything other than a per-folder read error (a dropped SSH link or SFTP protocol error, or a file with an impossible modified time) ends the whole search with an error dialog and no partial results.

**Open SSH Terminal** hands `ssh://user@host:port`, built from the host, user and port typed in the panel, to the operating system's SSH handler for an interactive session; it needs an `ssh://` handler to be registered, and says so if there is none.

## Live collection

After you confirm the source list, and before the model is called, Bloodhound collects real public-source data for the target: WHOIS, DNS, Team Cymru IP-to-ASN, Mnemonic passive DNS, crt.sh and the
Wayback Machine for domains (for IPs: SANS DShield attack history and Shodan
InternetDB exposure instead of crt.sh and Wayback, plus IPinfo geolocation and
Criminal IP reputation when their keys are set). WHOIS and DNS are live queries, not archived data: the registry or registrar sees the WHOIS query and the domain's own name servers see the DNS lookup (through your resolver). Each saved threat-intelligence
key (Settings → OSINT Keys, written to `.env`) adds its source: AbuseIPDB, GreyNoise, VirusTotal, AlienVault OTX, Shodan and
Censys for IPs; VirusTotal, OTX, SecurityTrails, DomainTools, Shodan DNS, URLScan
and Hunter (email pattern and role addresses only) for domains. For emails, EmailRep, HIBP (with a key), BreachDirectory and Hunter's
mail-server check (with a key) receive the address itself; Gravatar receives only its hash. DeHashed, Snusbase and LeakCheck add breach metadata when their keys are set — breach names, counts and kinds of
leaked data, never the leaked values. Domain, organisation and email targets also get a dark-web exposure check: ransomware.live and Ahmia (a Tor search index; onion sites are never opened), plus Intelligence X when its key is set (its search index only; nothing is downloaded). Usernames go to URLScan, GitHub and Keybase. Organisations go to GLEIF, ICIJ Offshore Leaks, CourtListener court dockets (docket metadata only, no filing text; a docket lists up to 10 party names, which can be private individuals) and, with a key, OpenSanctions
screening. Phone targets and person names containing a space contact nothing. A Person target written as a single word is treated as a username (URLScan, GitHub, Keybase, plus WhatsMyName on a Deep Dive), and a Person target containing `@` and a dot after it is treated as an email address. The prompt treats Keybase's signed proofs as the strongest
identity link, Offshore Leaks matches as name similarity (being named in the
leaks is not evidence of wrongdoing), and shared-hosting passive DNS and
DShield history as not attributable to the target. Collection runs on a worker thread
with progress in the status line (one line, overwritten as each source reports).

The prompt tells the model to treat every collected value as a confirmed fact, while also asking it to present WhatsMyName hits, Offshore Leaks matches and shared-hosting data as leads. Third-party text in those records (profile bios, page titles, snippets, party names) is passed in the same JSON, so a hostile profile can steer the dossier.

A provider that fails does not stop the others: its error is kept in the collected record and the model is told to flag any `error` value as a collection gap. The panel does not list the collected records, per-provider results or failures (the status line shows a source's state only while it runs); the only product of collection it keeps is the **Sources** count, so records are visible only if the dossier restates them.
> Known limitation: if the collection step itself fails (an error outside any single provider), the dossier is still requested with no collected data, the model is not told, and the Sources gauge shows the previous run's count.
> Known limitation: BreachDirectory's answer is passed on as the service returns it (first 10 entries), not filtered to breach names and counts.

A **Deep Dive** on a username also sweeps the
[WhatsMyName](https://github.com/WebBreacher/WhatsMyName) site list (CC BY-SA
4.0): about 600 profile URLs requested directly from this Mac (each site sees your IP address and a Chrome-style User-Agent), up to 12 at a
time, with a 120-second budget. The list is downloaded once a week into
`data/cache/wmn-data.json` (in the Sentinel data folder), with a stale copy used if the download fails. Sites
the list marks invalid, sites behind bot protection (their challenge pages
would make every answer a guess), and its NSFW category are left out. A hit
needs the site's exact "exists" status code **and** its marker text (a list entry with no marker text matches on the status code alone); everything
else is a confirmed miss or counted as inconclusive, with the top reasons
reported.

Routers with flood protection refuse new connections when one device opens
them too fast, and this sweep does exactly that. So a refused connection is
treated as the network pushing back, not as an answer: when 5 of the last 20
answers are refusals, the sweep halves how many requests it keeps in flight
(never below 2) and pauses 3 s, then adds one back each time a window of 20
answers has fewer than 5 refusals. Refused sites get one retry at the end, at most 4 at a time, and
the result's `backoff` block reports slow-downs, retries and recoveries. Field note
(one router, not covered by tests): on a router that refused 218–293 of ~600 connections per sweep, this removed the
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
10 for a Quick Scan, 25 for Standard, 45 for a Deep Dive. Only tools the
catalogue marks live and not deprecated are used, shadow libraries are
blocked, and hosts already in the built-in library are skipped. Each line is
tagged with pricing, account and API needs; tools marked **ACTIVE** interact
with the target, and the prompt requires the dossier to say so wherever it
recommends one. The catalogue shares Trace's weekly cache in
`data/cache/osint-framework.json` (in the Sentinel data folder); prompt building never downloads it. The panel starts one background refresh per app session when it is first shown (it downloads only when the cache is missing or a week old, and sends no target data).

## Outputs — the dossier (exact section headers the parser keys off)
`## 1. OVERVIEW` (with `THREAT LEVEL: X/10`, `CONFIDENCE: X%`, `SOURCES REFERENCED: X`) · `## 2. DIGITAL FOOTPRINT` · `## 3. INFRASTRUCTURE / SOCIAL PROFILE` · `## 4. RISK & RED FLAGS` · `## 5. METHODOLOGY & TOOLS`. Each becomes a card with a Copy button; if the model misses the headers, the text appears as a single "Response" card, and a **Raw response** toggle shows the model's text as received. Cards and the raw view show the text as plain characters; markdown and HTML are not rendered. Sidebar indicators are regex-parsed from the exact lines: Threat Level and Confidence are the model's own estimates (markdown bold between the label and the number, as in `THREAT LEVEL: **7/10**`, defeats the parser and that gauge stays at —); **Sources** is the real count of public sources contacted by live collection (a source that errored still counts as contacted; skipped ones do not), never the model's `SOURCES REFERENCED` figure; **Depth** shows the scope.

The prompt asks the model to label confirmed facts, inferred patterns and speculation, to include a legal and ethical considerations point in section 4, and to end with an authorised-use disclaimer. Those are requests: the panel has no separate evidence / inference / unknowns sections, does not check the labels, and adds no lawful-use footer of its own (the image details pane carries its own "authorised investigative use only" line).

## How it works
`OsintHeavyAgent.planned_sources(target, target_type, scope)` names the sources for the consent dialog; `OsintHeavyAgent.collect_live(target, target_type, scope, on_progress=, should_stop=)` runs the live lookups on `LiveCollectionWorker`; `OsintHeavyAgent.build_messages(target, target_type, scope, objective, image_metadata, live_results=)` then assembles the target + scope hint + collected data + optional EXIF block, offline. The system prompt carries the full tool library and the strict section format the UI depends on.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/osint_heavy_agent/__init__.py` | `OsintHeavyAgent` + the tool library + section spec; `planned_sources()` (the consent list) and `_run_providers()` (the lookups); both switch on the same normalised target type, but the two lists are written separately. |
| `ui/panels/osint_heavy.py` | Panel, optional image workflow, structured dossier cards, and indicators. |
| `providers/crypto_lookup.py` | Crypto Address targets: Blockstream (Bitcoin) and Blockscout (Ethereum). |
| `providers/whatsmyname.py` | Deep Dive username sweep: site-list cache, per-site check, bounded parallel sweep. |
| `services/osint_catalog.py` | OSINT Framework catalogue: weekly cached download, filtering, and per-agent tool selection. |
| `ui/workers.py: LiveCollectionWorker` | Runs live collection without freezing the interface. |
| `services/local_file_search.py` | Bounded, read-only metadata search and filters. |
| `services/remote_file_search.py` | Strict-host-key SFTP traversal for authenticated machines. |
| `ui/workers.py: LocalFileSearchWorker`, `RemoteFileSearchWorker` | Run file discovery without freezing the interface. |
| `ui/panels/osint_heavy.py: investigate()` | Reads form + EXIF opt-in, authorises, asks consent, runs live collection on a worker, re-checks the budget on the assembled prompt (`reauthorize`), then fires `ChatWorker`. |
| `ui/panels/osint_heavy.py: is_running()`, `stop()`, `shutdown()` | Let the window Stop, close/quit and Emergency Reset reach a running file search and collection. |
| `ui/panels/osint_heavy.py: save()` | Saves the dossier to `.txt`. |

## Extend it
- **New tool family**: add a block to the tool library in the system prompt (keep the `Name: URL — description` format).
- **New indicator**: emit a new `KEY: value` line in section 1 and parse it in the panel's indicator update.
- **Live pivots**: add a source to the matching `providers/*_lookup.py` (or a new provider dispatched from `_run_providers()`); report it in `sources_contacted` so the Sources gauge counts it, and add its label to `planned_sources()` so the consent dialog names it.
- Keep section headers verbatim — the parser matches them exactly.

## Requirements
A large-context reasoning model is recommended automatically; the dossier needs a provider (a cloud key or Ollama), File Discovery needs neither. Optional OSINT API keys, saved on Settings → OSINT Keys (written to `.env`). Pillow handles EXIF (JPEG, PNG and WebP only). Local search uses the Python standard library. Remote search requires Paramiko and an existing SSH identity.
