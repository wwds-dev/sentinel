# Forge — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)
> agent `agent:<key>` (optional; only meaningful in a parent project's shared TODO.md — not needed here, this file already belongs to Forge alone)

---

## v1 — current

- [x] `P2` `bug` `@ai` Code-review fix (2026-09-29): `parse_spec` used a greedy `\{.*\}` with DOTALL that matched to the last `}`, so trailing prose containing a brace made `json.loads` fail and discarded an otherwise-valid leading spec. It now uses `JSONDecoder().raw_decode` from the first `{`, reading one JSON value and ignoring trailing text.
- [ ] `P1` `testing` `@ai` Prove a generated scaffold actually imports and instantiates. Forge writes a Python draft and inactive registry rows. Only syntax is checked today: `AgentFactory.render_agent_source` runs `compile()` on the scaffold before anything is written. Nothing checks that the draft imports or instantiates, so a scaffold that fails there still reaches the human review instead of failing fast.
- [x] `P2` `security` `@ai` Validate the LLM's JSON spec before it becomes code. The spec drives a file write and DB inserts; a schema check (required fields, allowed provider names, no path traversal in the module name) belongs between the model and the filesystem. Done: `AgentFactory.validate_spec` checks required fields, the name pattern and collisions, label and description lengths, provider names and tools; it runs when the reply arrives (`ManagerPanel._on_finished`) and again inside `create_agent` on Approve.
- [ ] `P2` `docs` `@ai` Document the promotion path. Rows are inserted inactive on purpose, but nothing states what a human must verify before flipping one active, so "deliberately integrate" has no checklist behind it.
