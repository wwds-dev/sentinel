# 0006 — Case canonical JSON (`sentinel-json-v1`), evidence hash and timeline chain

Date: 2026-11-18 · Status: proposed (Architect gate for `docs/specs/case-workspace.md`, section 15)

## Context
Every evidence hash and every timeline `event_hash` is stored forever and printed in exported
reports that third parties keep. Once a case exists, its serialisation and chain rule can never
change for that case, or Verify would report every old case as broken. The spec (7.1-7.3) asks
for canonical JSON "as RFC 8785 JCS or a documented subset" and a chain of
`SHA-256(prev_hash ‖ canonical(event))`.

Full JCS needs ECMAScript number formatting, which Python's `json` does not produce (for
example `1.0` against JCS `1`, `1e+21` against `1e21`). Nobody outside Sentinel needs to
*re-canonicalise* anything: a reader checks the stored or exported **bytes** with `shasum`. So
the canonical form needs to be deterministic inside Sentinel and fully documented, not
JCS-exact.

## Decision
### 1. `sentinel-json-v1` (in `services/case_integrity.py`, the only implementation)
`canonical(obj) = json.dumps(normalise(obj), sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False).encode("utf-8")`, where `normalise`:
- keeps `None`, `bool`, `int`, `str`, `list`, `dict`; turns `tuple` into `list`;
- keeps finite `float` (Python `repr`, shortest round-trip); turns NaN and ±Infinity into the
  strings `"NaN"`, `"Infinity"`, `"-Infinity"` (provider data, not ours to drop);
- requires `str` dict keys; any other key type, or any other value type (`bytes`, `datetime`,
  `set`, objects), **raises** instead of being written as `repr`. Callers convert first;
- applies Unicode NFC to no string (bytes are kept as the provider gave them).
Output has no trailing newline. Every evidence item and every event records
`"canon": "sentinel-json-v1"`. A later format is a new name; v1 is never changed.

### 2. Evidence hash
`sha256 = SHA-256(stored bytes)`, lower-case hex, computed once at capture from the exact bytes
written, and recorded in the event that adds the item. The file is named `<sha256>.json`.

### 3. Timeline events
One event per line of `timeline.jsonl`: `canonical(event) + b"\n"`. Fields:
`v` (1), `canon`, `case_id`, `seq` (1, 2, 3… no gaps), `time` (UTC,
`YYYY-MM-DDTHH:MM:SS.ffffffZ`), `type`, `data` (type-specific; an evidence event carries the
item's full metadata **and its `sha256`**), `prev_hash`, `event_hash`.

### 4. Chain
- `genesis = SHA-256(b"sentinel-case-v1:" + case_id.encode("ascii"))` hex; event 1's
  `prev_hash` is the genesis.
- `event_hash = SHA-256(canonical(event without the "event_hash" key))` hex. `prev_hash` is
  inside the hashed body, which gives the spec's `prev_hash ‖ canonical(fields)` property
  with one rule instead of two.
- Verify walks the file in order and, for each line: parses it, checks `seq` is the previous
  plus one, `prev_hash` equals the previous `event_hash`, `event_hash` recomputes, and, for an
  evidence event, that `evidence/<sha256>.json` exists and hashes to the recorded value. The
  first event failing any check is "chain broken from #seq"; every later row shows
  "Chain broken". A changed or missing evidence file therefore breaks the chain from the
  event that added it (spec M2), and is also reported as that item "Changed" / "Missing".
- `summary.json` keeps the head `event_hash`. If the timeline's last event differs from it,
  Verify adds "The timeline ends earlier than Sentinel last recorded". This catches accidental
  truncation, not deliberate tampering (the operator controls both files; spec 7.5).

## Consequences
- The format is pinned by a golden test: fixed inputs (including a float, a non-ASCII string,
  a NaN and nested key order) map to fixed bytes and fixed hashes. Changing the function
  breaks that test on purpose. New logic here is the mutation-check target.
- Evidence bytes are what a third party checks; they never need this ADR to do so. Checking the
  *chain* outside Sentinel would need `timeline.jsonl` in the export; S1 does not export it
  (spec 15.10), the manifest carries the head hash only.
- Per-source capture times do not exist today (spec section 2), so S1's `captured_at` is the
  run's finish time, labelled as such in `data.captured_at_basis = "run_finished"`.

## Alternatives rejected
- **Full RFC 8785**: needs a hand-written ECMAScript number formatter or a new dependency, for
  no reader who needs it.
- **Hashing Python's pretty-printed JSON**: not stable across key insertion order.
- **Concatenating `prev_hash` outside the canonical body**: equivalent strength, one more
  rule to document and get wrong.
- **A Merkle tree or signatures**: no key management or third-party trust exists here
  (owner Q6), so a signature would prove nothing extra.
