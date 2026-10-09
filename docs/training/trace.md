# Trace — public-source identity research

**Goal:** structure and run a lawful public-source search. **Time:** 15 minutes.

![Trace workspace](docs/training/images/trace.png)

The capture above predates the **Exposure Check** button, which now sits beside
**Live Research** in the run bar; **Stop** replaces the three action buttons
while a request runs.

## Choose a target

Enter a name, username, email, domain, company, phone number or IP address in
the Target box. There is no separate context field: the box holds the
identifier only, and it must fit the type you choose, so an email or domain
with extra words around it is rejected before anything is sent. Choose the
type or leave **Auto-detect** selected. Auto-detection happens locally, and it
is a guess: a handle with a dot (john.smith) reads as a domain, a Bitcoin or
Ethereum address reads as a username, and it never picks Company, so choose
**Company** yourself for an organisation. Choose the type yourself before a
live lookup whenever the guess could matter.

## Structure Query versus Live Research

**Structure Query** asks the selected model to create an investigation plan.
It does not contact research sources. With Ollama, the target remains local.
With a cloud model, the prompt is sent to that provider after you confirm the
request. Separately, Sentinel refreshes a public list of OSINT tools from
GitHub at most once a week; that request carries no target. **Stop** cancels
a plan: the status reads "Stopped.", the partial text stays on screen and
nothing is saved.

**Live Research** contacts supported public sources after showing exactly what
will be shared. It calls no model, so Sentinel bills nothing for it (a keyed
service may still spend its own quota or credits). Domains and IPs draw on
WHOIS, DNS, passive DNS and network-owner records, with certificate
transparency (crt.sh) and the Wayback Machine added for domains only; an IP
additionally reports attack history (DShield) and known exposure (Shodan
InternetDB). Every threat-intelligence service whose key you saved in
Settings → OSINT Keys is added and named in the confirmation: for an IP,
IPinfo (geolocation and privacy flags), Criminal IP (a reputation score),
AbuseIPDB, GreyNoise, VirusTotal, AlienVault OTX, Shodan and Censys; for a
domain, VirusTotal, OTX, SecurityTrails, DomainTools, Shodan, URLScan and
Hunter (role addresses and a count of named people, never their addresses).
Usernames go to URLScan, GitHub and Keybase. Companies use the GLEIF
legal-entity registry and U.S. court dockets from CourtListener, which is part
of every company lookup (metadata only — never document text or PDFs), and,
with a key, sanctions and watchlist screening. Email services are chosen one by
one in a dialog: EmailRep and Gravatar start ticked, Have I Been Pwned and
BreachDirectory start unticked, and Hunter is ticked once its key is saved.
Person and phone targets remain planning-only to avoid data-broker and
reverse-phone disclosure; that rests on the type you choose, so a personal name
entered as a Company is sent to GLEIF and CourtListener.

A separate **Exposure Check** (domain, company or email) asks which leak and
dark-web indexes may receive the target: Ransomware.live and Ahmia are free,
Intelligence X needs a key (a free account's works, within its limits), and
DeHashed, Snusbase and LeakCheck need paid ones. All stay metadata-only: the
breach services report which breaches a target appears in and, for some, when
and what kinds of data leaked, and never return leaked passwords or hashes. The dialog ticks
every source whose key is saved, so untick any you do not want to receive the
target before pressing OK. For an email address, only its domain goes to
Ransomware.live and Ahmia; the full address goes to the key-gated services you
tick.

## Read the result

The Activity trail distinguishes validation, contacted sources, skipped
sources, partial failures, model work and cancellation. Domain and IP lookups
list a keyed service whose key is not saved as skipped, because it is never
contacted.
A source returning no match is not proof that the subject does not exist. Treat
model summaries as interpretation and live-source records as evidence that
still needs context. Live results are plain-text (JSON) records, one card per
source, with a Research summary card first.

Know the rough edges before you rely on a result:

- The Exposure verdict reads "Incomplete" or "Not checked", not "No exposure
  found", when a source failed or you pressed Stop first; a "direct victim
  match" is a whole-word name match or the same domain, so still verify each
  listing.
- An IP lookup sends private and loopback addresses like any other; its DNS
  step asks for the reverse (PTR) record.
- BreachDirectory is reduced to breach names and counts; whether its keyless
  endpoint still works has not been confirmed.

Saved searches (the **Saved searches** toggle near the bottom of the left rail
while Trace is selected) can be reopened with a click, renamed with a
double-click, filtered or deleted. Reopening never sends anything and restores
the query type; the trail line says whether the entry is a stored plan or a
stored live record. A saved live record keeps everything the sources returned,
such as WHOIS contact emails, until you delete it.

## Best practices

- Start with the least sensitive identifier and the narrowest goal.
- Confirm spelling and target type before contacting a source.
- Record URLs and dates; public records can change.
- Separate confirmed facts, likely matches and speculation.
- Do not use Trace for harassment, stalking or decisions about someone's
  eligibility, employment, housing, credit or insurance. Trace cannot check
  your purpose; keeping to this rule is yours to do.

## Exercise

Use a domain you own. Run Structure Query locally, review its plan, then start
Live Research and read the confirmation screen before deciding whether to
proceed. Decline once and read the status line, then run it and reopen the
result from Saved searches.
