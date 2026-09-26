# BUG SPRAY — Bug bounty triage & reporting

`key: bug_bounty` · class: `agents/bug_spray/sentinel_chat_agent.py → BugBountyAgent` · panel: `ui/panels/bug_bounty.py → BugBountyPanel`

> ⚠️ Only analyse assets explicitly in-scope for an authorised program.

## What it does
Shows public bug bounty programs and recent changes from Bug Spray's saved database,
with a background directory scan when due. The report panel turns raw findings
into a vulnerability report and a paste-ready submission draft. It also has
a manual **nmap runner** (real subprocess); that is separate from the public
directory scan. Output is CWE-classified with a CVSS v3.1 score.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Program radar | Search saved programs and recent changes; open the live page or fill the Program field. **Scan now** refreshes public listings. |
| Target | In-scope asset (endpoint/host/component). |
| Program | Bug bounty program name. |
| Scope type | Web / Mobile / API / Network, etc. |
| Findings box | Paste HTTP responses, Burp output, source snippets, recon notes. |
| Optional reconnaissance | Collapsed Nmap controls; local scan output feeds the analysis. |
| Model override | Optional provider/model change; a strong security-reasoning model is selected by default. |
| Analyse / Stop | Run, or cancel while a request is active. Save and Clear appear with results. |

## Outputs
The result is split into copyable cards: **Vulnerability Report**, **Proof of
Concept**, **Remediation** and **Submission Draft**. The unmodified model reply
remains available behind a collapsed raw-output disclosure. Side indicators
show parsed severity, CVSS score and any stated bounty estimate.

## How it works
`BugBountyAgent.build_messages(target, program, scope_type, findings, nmap_output)` composes only the evidence present (no fabrication) and requests the fixed report + submission format. The program feed reads `agents/bug_spray/data/programs.sqlite3`; a separate `QProcess` runs its scanner through the nested repo's venv when the configured interval has elapsed while Sentinel is open. Nmap uses another `QProcess`, separate from the LLM `ChatWorker`.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/bug_spray/sentinel_chat_agent.py` | `BugBountyAgent` — report + submission spec. |
| `agents/bug_spray/bug_spray/` | Public-program scanner, saved snapshots and change events. |
| `ui/panels/bug_spray_feed.py` | Saved program feed and background scan lifecycle. |
| `ui/panels/bug_bounty.py` | Panel, optional Nmap lifecycle, structured result cards, analysis, and indicators. |
| `ui/panels/bug_bounty.py` | Export / reset. |

## Extend it
- **More recon tools**: require a scope-confirmation and rate-limit guard before adding any active target tool.
- **Auto-severity**: post-process the report to set the sidebar from the parsed CVSS.
- **Program templates**: branch the submission format on the Program field.

## Requirements
`nmap` installed locally for the scanner. Provider key for analysis. Report is only as good as the evidence pasted.
