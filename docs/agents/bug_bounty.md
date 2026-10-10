# BUG SPRAY — Bug bounty triage & reporting

`key: bug_bounty` · class: `agents/bug_spray/sentinel_chat_agent.py → BugBountyAgent` · panel: `ui/panels/bug_bounty.py → BugBountyPanel`

> ⚠️ Only analyse assets explicitly in-scope for an authorised program. Sentinel does not check this for you: Program and Scope Type are text you declare, and the target is not validated against them before Nmap runs or before the model is called. **Known limitation:** there is no scope gate or authorisation confirmation yet.

## What it does
The workspace has two parts that share a screen but not a code path.

- **Program radar** shows public bug bounty programs and recent changes from Bug Spray's saved database. A background scan of five platforms' public program directories (HackerOne, Bugcrowd, Intigriti, YesWeHack, Immunefi) refreshes it when due. The scan reads directory metadata only and never contacts a program's assets. Nothing is scanned until at least one platform is enabled (see below).
- **Report panel** turns the findings you paste into a vulnerability report and a submission draft, using the model you select. The prompt asks for a CWE-classified report with a CVSS v3.1 score and a draft worded for HackerOne or Bugcrowd; Sentinel does not compute or validate either value, so both are suggestions for human review. It also has a manual **Nmap Recon Scan** runner (a real local subprocess) that is separate from the directory scan and from the model call.

Nothing in Bug Spray can submit a report to a platform: you copy the draft and submit it yourself.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Program radar | Search saved programs and recent changes; open the live page or fill the Program field; **Scan now** refreshes the public listings. Details below. |
| Target URL / IP | The asset the report is about. Free text; not validated. |
| Program | Bug bounty program name. Free text; not verified. |
| Scope Type | Web Application · API / REST · Mobile (Android) · Mobile (iOS) · Network / Infrastructure · Source Code Review · Cloud Config · Other. Sent to the model as "Scope Type"; not checked against anything. |
| Severity Target | Critical (P1) … Informational. Sent to the model as an unverified "severity expectation" line; it does not change the Severity tile, which reads the model's reply. |
| Nmap Recon Scan | Optional, always shown (not collapsed): command field, **Run Nmap** (replaced by **Kill** while a scan runs) and an output box. Details below. |
| Findings / Burp Suite Output / Notes | Paste HTTP responses, Burp output, source snippets, recon notes. |
| Run bar | Workflow chip "Analysis", provider and model boxes, **Auto-route**, then **Analyse** (replaced by **Stop** while a request runs). Provider and model start on Sentinel's recommendation for this agent (baseline: anthropic / claude-sonnet-5, "capability baseline for security and coding analysis"). There is no separate "Model override" section; change them here. |
| Save Report / Clear | Always visible. Save is disabled until an analysis has completed. |

## Program radar
- **Layout:** two tabs, **Recent changes (N)** and **Programs (N)**, filtered by the search box, the platform menu and the watchlist. **Show all** ignores the watchlist. **Watchlist…** edits the platforms to scan and the keyword, tag and minimum-top-payout filters and saves them to `agents/bug_spray/config.json`; filters change only what is shown, every program is still stored. The status line reads `Last scan: <time> · N watched programs`, plus `· errors: <platform>` for a platform that failed in the last recorded scan.
- **Selecting a row** shows the saved scope (first two assets and a count), rewards and out-of-scope items. **Full details…** (or a double-click) shows the whole scope, per-severity rewards and tags. **Open program page** opens the platform URL (https only). **Use in report** fills **Program** (as "Platform — Name") and never **Target**. A saved scope is a lead, not authorisation; the panel says so.
- **Scan now** starts a scan at once; its menu has **Full re-scan**, which re-fetches every program's details instead of reusing unchanged ones (a few minutes). The button is disabled while a scan runs and a running scan cannot be cancelled from the app. **Known limitation:** during a full re-scan a program whose detail request fails has no stored copy to fall back on, is skipped, and can be reported as Gone.
- **Scanner:** a `QProcess` runs `agents/bug_spray/main.py scan --json` with `agents/bug_spray/.venv/bin/python`. If that interpreter is missing the status line says "Bug Spray Python environment is missing; see its README setup instructions" (see `agents/bug_spray/README.md`, Environment). The same settings drive the terminal commands run from that folder (`python main.py scan | list | show | feed`).
- **When it runs:** every time the workspace is shown, and then every 60 s while Sentinel stays open (the timer starts the first time the workspace is shown), the panel starts a scan if a platform is enabled, none is running, and either no scan has been recorded or the last one is older than `poll_interval_minutes` (`config.json`, default 60; not editable in the app). Nothing scans while Sentinel is closed.
- **First run:** `config.json` is git-ignored and does not exist in a fresh checkout, so no platform is enabled. Enable platforms in **Watchlist…** or copy `config.example.json` to `config.json`. **Known limitation:** until then automatic scans silently do nothing and **Scan now** (once the environment is in place) finishes with no message; the status stays "Last scan: never · 0 watched programs".
- **First scan per platform** stores a baseline and produces no change events, so **Recent changes** stays empty until a later scan finds a difference.
- **Scan outcome is mostly invisible.** The scanner's own summary (counts and warnings) is discarded. A failing platform's name appears as `errors:` after the next refresh; a scan that exits non-zero shows "Scan finished with errors." plus the tail of stderr, or "See the scan summary above." when stderr is empty, even though no summary is shown. Two safeguards warn only in the scanner's own output, which the panel does not normally display: a failed per-program detail request keeps the last stored copy, and when a platform lists fewer than half the programs it listed last time none are marked Gone. **Known limitation:** neither warning reaches the panel.
- **Retries:** a scan that records a result, even one where every platform failed, waits a full interval. A scan process that dies before recording is retried on the next 60 s check with no backoff.
- **Sources:** five real HTTP adapters, all anonymous (no account or token): HackerOne's website GraphQL directory, and the public JSON of Bugcrowd, Intigriti, YesWeHack and Immunefi. These are unofficial, unversioned endpoints that can change without notice; a platform that fails is reported as an error while the others still update. They are tested offline against recorded responses (`agents/bug_spray/tests`), so live behaviour is not covered by the tests. Requests keep at least 0.35 s apart per platform, send a User-Agent that names the tool, and retry a bounded number of times honouring Retry-After. Bug Spray does not interpret any platform's terms of use.
- **Coverage:** bounty-paying, publicly listed programs only; VDPs and invite-only programs are not fetched. An Intigriti program that needs a login is kept from the public listing with no scope (**Programs** shows "unpublished"), and one that becomes login-walled later is re-saved with an empty scope, which shows as every asset removed. The YesWeHack adapter marks every listed program open, so it can never report a program as paused; a program that stops being listed shows as Gone.
- **Storage:** `config.json`, `data/programs.sqlite3` and `data/scan.lock` (stops GUI and terminal scans overlapping) live inside `agents/bug_spray/`, not under Sentinel's runtime data folder, so Sentinel's data-folder features do not manage them. The packaging spec bundles no `agents/bug_spray` data, scanner script or venv, so a packaged build reports the environment missing: the radar's scanner is a source-checkout feature.

## Nmap Recon Scan (manual, local)
- **Run Nmap with an empty command field** builds `nmap -sV -sC -T4 --open <host>` from the Target (scheme and path removed; a `:port` is kept), writes it into the field and starts it in the same click, with no review step. `-sC` runs nmap's default scripts and `-T4` is nmap's "aggressive" timing; edit the command first for gentler traffic. For a target with a port, type the command yourself (nmap takes ports with `-p`).
- **Only nmap starts.** The field is split shell-style but no shell is involved. The first word must be `nmap` or a path ending in `nmap`; anything else is refused with "Only nmap can be run from here (got '…')". The binary used is always the nmap found on `PATH`, else `/opt/homebrew/bin/nmap`, else `/usr/local/bin/nmap`; a different path typed as the first word is ignored. If none exists the box says "nmap is not installed (brew install nmap)." and nothing starts.
- **Bounded:** a scan is killed after 10 minutes and when its output reaches 256 KB (counted in characters); the box says which. If nmap cannot start, **Run Nmap** comes back and the box prints "[Error] nmap could not be started …". **Kill** ends the process. Closing Sentinel kills a running Nmap scan and the radar's background scan (`shutdown()`); the window-level Stop only cancels the model request and does not reach the scan.
- **Output:** stdout and stderr are merged in the box, which also shows the panel's own `[Running]`, `[Done]` and `[Error]` lines. Only the scanner's text (capped as above) is kept for Analyse; the panel's markers are never sent to the model. A killed or capped scan still passes on what it printed.
- **No scope check, no confirmation, and outside the budget/authorisation guard.** It touches whatever host you give it.

## Outputs
The result is split into copyable cards (each has a **Copy** button): **Vulnerability report**, **Proof of concept**, **Remediation** and **Submission draft**. An empty section is not shown as a card; if no section is recognised the whole reply appears as one **Response** card. The unmodified reply stays behind a collapsed **Raw response · N words** toggle, shown whenever at least one card is. While tokens arrive the raw stream is shown in place of the cards.

The built-in prompt asks for the proof of concept and remediation as numbered bold items inside `## VULNERABILITY REPORT`, not as separate headings. The parser accepts both that form and `## Proof of Concept` / `## Remediation` headings, so with a model that follows the prompt the **Vulnerability report** card still holds the whole numbered report and the Proof of concept and Remediation cards repeat those parts. **Known limitation:** cards are cut by matching the model's wording; a `###` sub-heading ends a card early and a reply that departs from the layout can leave a card empty or short. Check the raw response when a card looks wrong.

Three tiles show values read from the reply by text matching:
- **Severity:** the first severity word (Critical, High, Medium, Low, Informational) on the same line after `**Severity**`.
- **CVSS Score:** the first number from 0 to 10 after the word "CVSS", after dropping version tokens such as `v3.1` or `version 3.1` and `CVSS:3.1/AV:…` vector strings. A version with or without a leading "v" (`CVSS 3.1: 7.5`) is skipped, as is a vector string; still check the number against the report.
- **Bounty Estimate:** a `$` amount or range that follows the word "bounty" on the same line. The prompt does not ask for an estimate, so this usually stays "—".

## How it works
`BugBountyAgent.build_messages(target, program, scope_type, findings, nmap_output, severity="")` composes only the evidence present (Scope Type is always present) and requests the fixed report + submission format. **Analyse** needs a Target, Findings or scanner output ("Enter a target, paste findings, or run a scan first.") and a selected model. It then:
1. Prices the whole assembled request (built-in instructions plus Program, Scope Type, Target, scanner output and Findings) against the budget caps; the Target is only the label. A cloud model also gets a "Confirm External API Request" dialog with the approximate tokens and estimated cost; a local Ollama model does not. A refused request leaves the status at "Blocked before sending." and the buttons usable.
2. Streams the reply into the panel, then builds the cards and tiles and enables **Save Report**.
3. Stores the run automatically: Saved Chats gets the full request text (including Findings and scanner output) and the reply, and the run log and usage history record it. Findings and scanner output go to the selected model verbatim, with no redaction.

**Stop** cancels the request and the status stays "Stopped.". The cancellation echo and a late reply from a provider that does not stream are ignored (abandoned, not shown, not recorded).

**Save Report** writes the raw model reply (not the cards) to a file you choose, default `~/Downloads/bb_report_<target>_<time>.md`, with Markdown and Text filters. **Clear** empties Target, Program, Findings, the Nmap command and output, the results and the tiles; it does not reset Scope Type, Severity Target or the radar. **Known limitation:** Clear does not stop a running analysis or Nmap scan, and output that arrives afterwards still appears.

The program feed reads `agents/bug_spray/data/programs.sqlite3`; the scan `QProcess` is owned by the radar, and Nmap uses another `QProcess`, both separate from the LLM `ChatWorker`.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/bug_spray/sentinel_chat_agent.py` | `BugBountyAgent` — report + submission spec. |
| `agents/bug_spray/bug_spray/` | Public-program scanner (`sources/` adapters, `store.py`, `changes.py`, `watchlist.py`, `config.py`), saved snapshots and change events. |
| `agents/bug_spray/main.py` | Terminal entry point (`scan`, `list`, `show`, `feed`, `--selftest`) and the scanner the radar launches. |
| `ui/panels/bug_spray_feed.py` | Saved program feed, Watchlist dialog and background scan lifecycle. |
| `ui/panels/bug_bounty.py` | Panel; bounded Nmap runner (`_nmap_argv`, `kill_nmap`, `shutdown`); `analyse`; result cards (`parse_sections`); tiles (`extract_cvss_score`); `save` / `clear`. |

## Extend it
- **Scope gate**: an authorised-program confirmation before Nmap and before Analyse does not exist; add one before any other active target tool, together with a rate-limit guard.
- **Auto-severity**: post-process the report to set the sidebar from the parsed CVSS.
- **Program templates**: branch the submission format on the Program field.

## Requirements
`nmap` installed locally (for example `brew install nmap`), needed only by the Nmap runner. For the radar, Bug Spray's own environment in `agents/bug_spray/.venv` (Python 3.11+ with `httpx` and `keyring`; setup commands are in `agents/bug_spray/README.md`) and at least one enabled platform. A provider key for a cloud model (a local Ollama model needs none). The report is only as good as the evidence pasted.
