# Trace — public-source identity research

**Goal:** structure and run a lawful public-source search. **Time:** 15 minutes.

![Trace workspace](docs/training/images/trace.png)

## Choose a target

Enter a name, username, email, domain, company, phone number or IP address.
Choose the type or leave **Auto-detect** selected. Auto-detection happens
locally. Add only the context needed to distinguish the target.

## Structure Query versus Live Research

**Structure Query** asks the selected model to create an investigation plan.
It does not contact research sources. With Ollama, the target remains local.
With a cloud model, the prompt is sent to that provider after permission.

**Live Research** contacts supported public sources after showing exactly what
will be shared. Domains and IPs draw on WHOIS, DNS, passive DNS and
network-owner records, with certificate transparency (crt.sh) and the Wayback
Machine added for domains only; an IP additionally reports attack history
(DShield) and known exposure (Shodan InternetDB), plus — when their keys are set
— geolocation and privacy flags (IPinfo) and a reputation score (Criminal IP).
Usernames can use URLScan; companies use the GLEIF legal-entity registry and can
add U.S. court dockets from CourtListener (metadata only — never document text or
PDFs) and, with a key, sanctions and watchlist screening. A separate **Exposure
Check** asks which leak and dark-web indexes may receive the target:
Ransomware.live and Ahmia are free, while Intelligence X and DeHashed need paid
keys and stay metadata-only — DeHashed reports which breach databases a target
appears in and never returns leaked passwords or hashes. Email services are
chosen individually, breach sources are never enabled without explicit consent,
and person and phone targets remain planning-only to avoid data-broker and
reverse-phone disclosure.

## Read the result

The Activity trail distinguishes validation, contacted sources, skipped
sources, partial failures, model work and cancellation. A source returning no
match is not proof that the subject does not exist. Treat model summaries as
interpretation and live-source records as evidence that still needs context.

Saved Searches can be reopened, renamed, filtered or deleted. Reopening never
reruns the search automatically.

## Best practices

- Start with the least sensitive identifier and the narrowest goal.
- Confirm spelling and target type before contacting a source.
- Record URLs and dates; public records can change.
- Separate confirmed facts, likely matches and speculation.
- Do not use Trace for harassment, stalking or decisions about someone's
  eligibility, employment, housing, credit or insurance.

## Exercise

Use a domain you own. Run Structure Query locally, review its plan, then start
Live Research and inspect the consent screen before deciding whether to proceed.

