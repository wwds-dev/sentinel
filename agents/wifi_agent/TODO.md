# Beacon — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)
> agent `agent:<key>` (optional; only meaningful in a parent project's shared TODO.md — not needed here, this file already belongs to Beacon alone)

---

## v1 — current

- [x] `P1` `security` `bug` `@ai` Code-review fix (2026-09-29): the BSSID and channel from the panel's text fields were interpolated verbatim into the generated command lines the UI tells the operator to run as root, and the ESSID field was read but never used. Each field is now validated (only a well-formed MAC / digit channel / metacharacter-free ESSID passes, else the safe placeholder), and the ESSID is shown in the sequence header rather than silently dropped. The executable steps are unchanged.
- [ ] `P2` `docs` `@ai` Make the passive/active split explicit in the generated Kali sequences. The panel already refuses to run them, but a reviewable command list should say which lines require authorisation before anyone pastes them.
- [ ] `P2` `feature` `security` `@ai` Passive analysis, staged. Split out of the parent list's four-agent "staged specialist integrations" item. Passive only — no deauthentication, no injection, no credential capture. *(split out of sentinel/TODO.md)*
