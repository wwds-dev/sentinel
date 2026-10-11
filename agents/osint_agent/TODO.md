# Trace — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)
> agent `agent:<key>` (optional; only meaningful in a parent project's shared TODO.md — not needed here, this file already belongs to Trace alone)

---

## v1 — current

- [x] `P1` `bug` `@ai` Code-review fix (2026-09-29): `validate_target` raised `ValueError` from `urlsplit` ("Invalid IPv6 URL") on a target with an unbalanced `[`/`]` (e.g. `example[.com`), escaping into the Trace panel's Qt slot instead of returning an invalid result. `_domain_host` now catches it and treats an unparseable target as having no host.
- [ ] `P2` `docs` `@ai` State the public-source boundary in the panel, not only in the README. Trace is the agent most likely to be pointed at a real person, and the limit it observes should be visible where the query is typed.
- [ ] `P2` `feature` `security` `@ai` Public-source adapters, staged. Split out of the parent list's four-agent "staged specialist integrations" item. Public sources only; excludes denial of service, credential theft, stealth/persistence and uncontrolled exploitation. *(split out of sentinel/TODO.md)* 2026-09-28: Wayback Machine (domains) and Gravatar (emails, hash only) added to Live Research. 2026-09-28: dark-web **Exposure Check** added (`providers/exposure_lookup.py`) — ransomware.live + Ahmia (free) and Intelligence X (paid, `INTELX_API_KEY`), for domain/company/email; own consent dialog, text-only, no onion contact or downloads; also auto-run by Bloodhound's live collection. 2026-09-28: Team Cymru IP-to-ASN, Mnemonic passive DNS and SANS DShield (IPs/domains), GitHub and Keybase (usernames), and the OSINT Framework catalogue in Structure Query's source list; the catalogue selection has no dedicated unit tests yet. 2026-09-28: OpenSanctions screening for company Live Research when `OPENSANCTIONS_API_KEY` is set.
