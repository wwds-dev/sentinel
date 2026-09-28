# TRACE — Light OSINT

`key: osint` · class: `agents/osint_agent.py → OSINTAgent` · panel: `ui/panels/osint.py → OsintPanel`

## What it does
A fast, lightweight open-source-intelligence assistant. Given a target (name, username, email, domain, org) plus optional context, it structures a research query, suggests public sources and search operators, and summarises what to look for. It is a **reasoning/planning layer** — it does not perform live lookups itself.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Query / target box | The subject to research. Structured types are validated locally before authorization. |
| Query type | Auto-detect, Person, Username, Email, Domain, Company, Phone, or IP Address. Auto-detect records the resolved type in the activity trail. |
| Model override | Optional provider/model change; the task recommendation is selected by default. |
| Structure Query | Generate a model-based investigation plan without contacting research sources. |
| Live Research | After explicit confirmation, query WHOIS, DNS, crt.sh and the Wayback Machine for domains (WHOIS and DNS for IPs), URLScan for usernames, individually selected email services, or GLEIF for company legal-entity records. Person and phone targets remain local-only. |
| Stop | Request cancellation; completed source results remain visible as a partial result. |

## Outputs
The persistent **Activity** trail explains validation, local/cloud execution,
model processing, completion, cancellation, and errors. It explicitly states
whether external sources were queried and remains visible after completion.
Results are shown as readable cards, with the raw streamed response visible
while generation is in progress.

Successful runs appear in Trace's **Saved Searches** rail. A saved search can be
filtered, reopened, renamed, deleted, or used as the starting point for a new
search. Reopening restores the target, query type, provider/model where
available, and structured response without performing another request.

**Live Research** results are collected records rather than model
inferences. The Activity trail names each source as it is contacted, records
success or failure independently, and lists the sources actually contacted at
completion. One failed source does not discard successful results.

For email targets, the complete address is sent only to sources selected in the
confirmation dialog. EmailRep and Gravatar are selected by default; Have I Been
Pwned and BreachDirectory are off by default. HIBP cannot be selected without a
configured API key. Gravatar never receives the address: it is looked up by the
SHA-256 hash of the trimmed, lower-cased address, and returns the public profile
its owner published (display name, location, verified accounts) or "no profile".
A service skipped before contact is recorded separately and is not reported as
contacted.

For domain targets, the **Wayback Machine** card gives the earliest and latest
archived snapshot and a link to every capture. It uses the availability API
twice rather than the CDX search API, which gives full capture counts but
routinely takes longer than 30 seconds. IP targets skip crt.sh and the archive.

**Structure Query**'s public-source section draws on a curated reference list
in the system prompt: free, no-login tools per target type (web-check, ViewDNS,
CentralOps, archive.today, WhatsMyName, OpenCorporates, the German company
registers, Das Örtliche, and similar), picked from Bruno Mortier's OSINT
framework (start.me/p/ZME8nR/osint) on 2026-09-28. Deeper, key-gated or
investigative tools belong to Bloodhound's library instead.

For company targets, the complete company name is sent only to the **GLEIF Legal
Entity Index** after the user confirms that exact destination. Results contain
legal-entity identifiers and registration reference data. GLEIF covers entities
with an LEI, so no match is not proof that an organization does not exist.

Trace intentionally performs no live collection for **Person** or **Phone**
targets. It does not send those personal identifiers to people-search,
reverse-phone, or data-broker services. Structure Query remains available for a
local planning-only workflow.

## How it works
`OSINTAgent.validate_target()` validates and classifies the target entirely
offline. `build_messages()` then wraps the accepted target in a system prompt
tuned for defensive, legal OSINT. Requests run through the shared `ChatWorker`,
request guard, cost tracking, history, and run logger.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/osint_agent.py` | `OSINTAgent` — system prompt + message builder. |
| `ui/panels/osint.py` | Panel, workflow state, result presentation, and request lifecycle. |
| `main.py` | Routing, authorization, Saved Searches, history, and provider execution. |
| `providers/domain_lookup.py` | Consented live WHOIS, DNS, certificate-transparency, and Wayback Machine snapshot collection for domains/IPs. |
| `providers/username_lookup.py` | Consented URLScan search for public pages containing a username. |
| `providers/email_lookup.py` | Per-source EmailRep, Gravatar (hash only), HIBP, and BreachDirectory collection with breach services opt-in. |
| `providers/company_lookup.py` | Consented company-name search against GLEIF's public legal-entity records. |

## Extend it
- **Person/phone enrichment**: intentionally local-only. Do not add people-search, reverse-phone, or data-broker collectors without a new privacy review and explicit source-specific consent design.
- **Escalation**: hand results to **Bloodhound** (`osint_heavy`) for a full dossier.
- Edit the system prompt in `agents/osint_agent.py` to change tradecraft focus.

## Requirements
Any model provider (API key and consent for cloud; Ollama is local and free).
HIBP requires `HIBP_API_KEY`; its checkbox is unavailable without one. EmailRep,
Gravatar, BreachDirectory, URLScan, GLEIF, WHOIS, the configured DNS resolver,
crt.sh, and the Wayback Machine can be used without a configured application key, subject to their own limits
and availability. Structure Query never contacts these services.
