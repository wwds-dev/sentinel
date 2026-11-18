# 0005 — Case store is plain files under `data/cases/`, not `sentinel.db`

Date: 2026-11-18 · Status: proposed (Architect gate for `docs/specs/case-workspace.md`, section 15)

## Context
The case workspace (spec `case-workspace`, slice S1) adds durable state: cases, immutable
evidence bytes, entities and an append-only, hash-chained timeline. The spec (section 6)
leaves the choice to the Architect: tables in `data/sentinel.db` through a v4 migration, or
files under `user_data_base()/data/`.

Facts that decide it:
- `init_db()` refuses to open a database whose `user_version` is newer than the build
  (`services/database.py:169-174`). A v4 migration therefore stops every older build,
  including one the owner rolls back to during the testing phase, from opening
  `sentinel.db` at all, for a feature that the rest of the app does not need.
- Evidence must be byte-exact and checkable with `shasum` alone (M3). Bytes kept as BLOBs must
  be written back out to be checked; bytes kept as files are already in that form.
- Atomicity across "evidence bytes + timeline" needs an ordering rule whichever store holds
  the timeline, because evidence stays outside a SQLite transaction if it is files.
- One writer: Sentinel is single-instance (`QLocalServer`), and section 15.2 makes the UI
  thread the only writer of case state.
- Volume is small: tens of cases, hundreds of events each, evidence in KB to a few MB.
- Emergency Reset erases `user_data_base()/data/` as a whole; all writes there must call
  `require_writes_allowed()`.

## Decision
1. **No change to `sentinel.db`.** No v4 migration; `SCHEMA_VERSION` stays 3.
2. **One folder per case**, `user_data_base()/data/cases/<case_id>/`, `case_id` = `uuid4().hex`:
   - `timeline.jsonl` — the source of truth: one canonical-JSON event per line (ADR 0006).
     Case header (title, purpose), status, entities and evidence metadata are all *events*;
     current state is derived by replaying them.
   - `evidence/<sha256>.<ext>` — content-addressed evidence bytes (`.json` in S1). Identical
     content within one case is stored once and referenced by every event that adds it.
     No sharing across cases, so deleting one case never touches another.
   - `summary.json` — a derived cache (title, status, counts, last activity, head
     `event_hash`) for the case list. Never trusted: rebuilt from the timeline when it is
     missing, unreadable or its head hash differs from the timeline's last event.
   - `.pending/` — staging for a write in progress (section 15.7); never read as case data.
3. **Atomic replace for the timeline**: every change writes the whole new `timeline.jsonl` to a
   temporary file in the same folder, `fsync`s it, `os.replace`s it over the old one and
   `fsync`s the folder. Evidence files are in place *before* the timeline that references them
   (15.7). No in-place append, so a torn last line cannot happen.
4. Every write path calls `require_writes_allowed()` before touching the folder.
5. `data/cases/` is git-ignored (one `.gitignore` line, tested with `git check-ignore`, M6).

## Consequences
- Older builds keep opening `sentinel.db`; they ignore `data/cases/`. No backup-and-migrate step.
- Spec success measure **M4** (migration) loses its migration half. Proposed replacement for
  the owner's gate: "all case operations leave `sentinel.db` byte-identical and its
  `user_version` at 3"; the chat-file half moves to S2 with import.
- Whole-file timeline rewrite is O(events) per change; at the expected size (≤ a few thousand
  events, ≤ a few MB) it stays in the low milliseconds. If a case ever exceeds that, a segment
  scheme (sealed `timeline.NNNN.jsonl` files) can be added without changing ADR 0006's format.
- No SQL queries across cases (for example "which cases mention this IP"). S3 can add a
  derived, rebuildable index file or table; the timeline stays the source of truth.
- Cases are not Chat Projects (owner Q2) and do not appear in Saved Chats.

## Alternatives rejected
- **Tables in `sentinel.db` (v4) with evidence as BLOBs**: transactions for free, but the
  downgrade lockout, the backup-per-migration cost, and BLOBs that must be exported to be
  checked.
- **Tables in `sentinel.db` with evidence as files**: still needs the file-before-row ordering
  rule, adds the downgrade lockout, and splits one case across two stores.
- **A separate `data/cases/cases.db`**: avoids the lockout, but adds a second schema and its
  migrations for data that is append-only and small; the hash chain would still be computed
  over a serialisation, not over the rows.
- **Append to `timeline.jsonl` in place**: cheaper per write, but a crash can leave a torn
  last line, and recovering it would mean editing the append-only log.
