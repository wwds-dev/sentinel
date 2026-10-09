# Bloodhound — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)
> agent `agent:<key>` (optional; only meaningful in a parent project's shared TODO.md — not needed here, this file already belongs to Bloodhound alone)

---

## v1 — current

- [x] `P1` `bug` `@ai` Code-review fix (2026-09-29): auto-detect routed IPv6 addresses and `:port`/`/CIDR`-decorated IPv4 to the username / WhatsMyName sweep instead of the domain provider (which resolves IPs). Added `_looks_like_ip` (via `ipaddress`, tolerating scheme, brackets, port, CIDR and `%zone`) and check it before the username fallback.
- [x] `P2` `bug` `@ai` Review follow-up (2026-09-30, `d44c867`): `_looks_like_ip` now delegates to `_extract_ip`, which strips a scheme, `/CIDR`, a numeric `:port`, `[..]` brackets and `%zone` in any combination (so `http://[2001:db8::1]:443` and `[fe80::1%en0]` are IPs, while `192.0.2.1:abc` is not), and `_catalog_kind` reuses it so a decorated address selects the IP catalogue instead of the domain one.
- [ ] `P3` `testing` `@ai` Add unit tests for `_extract_ip` / `_detect_from_target` / `_catalog_kind` covering the decorated-address cases above (bracketed IPv6 with scheme and port, `%zone`, `/CIDR`, non-numeric port). Bloodhound's tests live in the parent suite (`tests/test_osint_heavy_collection.py`), which only exercises a plain IPv4 address today.
- [ ] `P2` `design` `@ai` The threat and confidence scores need their derivation shown. A dossier carrying a number a reader cannot trace back to evidence invites more trust than it has earned.
- [ ] `P2` `feature` `security` `@ai` Metadata and rule matching, staged. Was one line of a four-agent "staged specialist integrations" item in the parent list, which is not a thing anyone works on. Excludes denial of service, credential theft, stealth/persistence and uncontrolled exploitation. *(split out of sentinel/TODO.md)*
