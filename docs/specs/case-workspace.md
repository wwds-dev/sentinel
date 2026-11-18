# Spec: case workspace for Trace and Bloodhound (2026-11-18)

Phase: **Define** approved 2026-11-18; **Design** for S1 done 2026-11-18 (section 14); **Architect** for S1 written 2026-11-18 (section 15, ADRs 0005-0006), Architect gate approved by the owner 2026-11-18 ("Approve Architect"). No code, tests or schema changed. Plan and Build have not started.
Source: `SUGGESTIONS.md` row #40 ("Case workspace for Trace and Bloodhound: an entity graph, a hashed evidence timeline and a report export", portfolio review 2026-11-18, effort XL, status IDEA). The review read docs only; section 2 checks its premise against the code on `main` at `03050e7`.
Playbook: `docs/playbooks/product-manager.md`, `status: current`, last-reviewed 2026-10-14 (35 days). Its EU AI Act Art. 50 practice touches this spec (section 9); its CRA practice does not (tier `public`, nothing sold).
In-flight check (`session_start.py --inflight`, 2026-11-18): no unreleased spec, `claude/*` branch or worktree covers cases, evidence or OSINT storage.
Priority proposal: **P2, after the testing phase** (section 12). The owner decides.

## 1. Problem
Trace and Bloodhound each answer one question about one target, then forget the context. An investigation of anything real (a phishing domain, a scam handle, an exposed company asset) is a chain of runs: a domain leads to an IP, the IP to a hosting network, a WHOIS record to an email, the email to a breach listing. Today the operator holds that chain in their head:

- Runs are saved one file per run and linked to nothing else. There is no way to say "these six runs are one investigation", to see what has been learned about each identifier, or to see in what order it was learned.
- A saved record can be changed without anyone noticing. Nothing records a hash, the capture time of each source or which tool version collected it, so a record cannot later be shown to be the one that was collected.
- The only report is a model's dossier as `.txt` (Bloodhound **Save Report**) or the current Chat output as `.txt` (**Export Report**). Neither one carries the collected records, their sources, their capture times or a way to check that they are intact.

## 2. What the code does today (premise check)
Verified by reading the code on 2026-11-18. The review's premise said "results in `data/sentinel.db`"; the code says otherwise.

| Premise | What the code does | Where |
|---|---|---|
| Trace and Bloodhound store results in `data/sentinel.db` | **No.** `sentinel.db` (schema v3) has `agents`, `tools`, `usage`, `runs`, `pricing`, `settings`, `projects`. Results are **one JSON file per run** in `data/chats/` (`HistoryStore.save_chat`), with keys `timestamp, agent, backend, model, command, messages, response`, optional `project`. The `runs` table keeps only a summary row (target in `prompt_summary`, status, cost). | `services/database.py:15-106`, `services/history_store.py:14-37`, `main.py:4683-4716` |
| Trace Live Research / Exposure Check results are saved | Yes. `record_external_research` writes the whole collected JSON as the `response` text of a chat file (`agent: "osint"`, backend `public-sources`) and a €0 run-log row. It sets **no project**. Per-source capture times, request details and raw response bodies are not kept; only the parsed, pretty-printed records. | `main.py:4683-4716`, `ui/panels/osint.py:902-913` |
| Bloodhound results are saved | **Only inside the dossier request.** The structured collection (`results` list) exists in memory at `_on_collection_finished` and goes straight into the model prompt. Saved Chats keeps the prompt text (collected JSON embedded in it) plus the dossier, and only if the model call **succeeds** (`record_request`, `main.py:4619-4654`). If the dossier fails or is stopped, nothing that was collected is saved. **Save Report** writes the dossier text only. | `ui/panels/osint_heavy.py:623-652`, `:989-1002`, `docs/agents/osint_heavy.md:40` |
| Chat Projects group investigations | Partly. A project is a row in `sentinel.db` `projects` (added by migration v3); a chat file carries `project: <id>`. Only Chat's own saves set it automatically (`pending_project`, `main.py:4846-4862`). Trace and Bloodhound saves are Unfiled, but **Saved Chats lists every agent's files** (with an agent filter) and **Assign** can file any of them under a project. So the operator can group runs today, by hand, after the fact, with no structure inside the group. | `main.py:5182-5300`, `:5324-5370` |
| Trace's Saved searches | A view over the same `data/chats/` files filtered to `agent == "osint"`. Bloodhound has no equivalent. | `main.py:5386-5404` |
| Hashing | None for stored results. `hashlib` appears in providers only to hash an email for Gravatar and to sign DomainTools requests. | `providers/email_lookup.py:134`, `providers/intel_sources.py:467-480` |
| Report export | `ReportExporter.export_text_report` writes the Chat output box to `data/reports/<timestamp>_<uuid>_<title>.txt`. | `services/report_exporter.py`, `main.py:5620-5631` |

**Found in passing (pre-existing, outside this spec, flagged for a TODO line):** `data/reports/` is **not** git-ignored (`git check-ignore data/reports/x.txt` returns nothing; `data/chats/*.json` and `*.db` are ignored). In a checkout run, **Export Report** therefore writes investigation output into an untracked, committable path. No such file exists in the main checkout today (0 files). A one-line `.gitignore` fix belongs in its own change, not in this feature.

## 3. User
One operator: the owner, on their own Mac, investigating identifiers they have a reason to look at (their own assets, a phishing or scam source aimed at them, a bounty target in declared scope). There is no team, no sharing inside the app and no second account. A report may leave the Mac only when the operator exports it and hands it on themselves (to a registrar abuse desk, a platform, a bank, the police).

## 4. Goal
The operator can open a **case**, send results from Trace and Bloodhound into it as **evidence**, see the **identifiers** the case touches and how they connect, see **what was learned when**, check that nothing stored has changed since capture, and export a **report** a third party can check against its own hashes.

## 5. Scope

### 5.1 In scope (the whole feature; the first slice is section 10)
1. **Cases**: create, rename, close, reopen, delete (with a confirmation that names how many evidence items go). A case has a title, an operator's purpose note, created and closed times, and a status (open, closed).
2. **Add to case** from Trace (Live Research, Exposure Check, Structure Query) and Bloodhound (the live collection, the dossier and image EXIF when the operator ticked it). Adding uses what was already collected; it never runs a lookup again.
3. **Evidence items**: immutable snapshots, hashed at capture (section 7).
4. **Entities**: the identifiers a case touches (section 6), each linked to the evidence that mentions it.
5. **Links** between entities, each one backed by evidence or marked as the operator's own assertion.
6. **Timeline**: every case event in order (evidence added, entity added or merged, link added, note, verify, export), append-only.
7. **Verify case**: recompute every hash and the timeline chain; report each item as intact, changed or missing.
8. **Report export** (section 8).
9. **Import an existing saved run** (from `data/chats/`) into a case, honestly labelled (section 7.4).

### 5.2 Non-goals
- **No new lookups and no new sources.** The case workspace stores and relates what Trace and Bloodhound already collect; it adds no network call. Pivoting from an entity ("look this IP up") opens the existing Trace or Bloodhound flow with its own validation and consent dialog.
- **No person or phone data-broker lookup**, now or as a later slice (app-context invariant). A Person entity is a label the operator types or that a collected record names; nothing enriches it.
- **No claim of scope, authorisation or lawfulness.** The purpose note is the operator's own statement; the app never checks it and the report says so (section 9).
- **No "forensically sound", "court-admissible" or "tamper-proof" claims.** Hashes show that stored bytes have not changed since capture inside this app; they do not prove what a source said, and the operator controls the machine (section 7.5).
- **No cloud sync, sharing, multi-user access or remote storage.** No third-party timestamping service (for example RFC 3161) in this feature; that would be a cloud call (open question Q6).
- **No automatic entity extraction by a model.** A model may *propose* entities only through the existing request path and gate; the operator accepts each one.
- **Not a replacement for Chat Projects.** Projects keep grouping chats and spend; a case is a different object (open question Q2 on whether a case should also be a project).
- **No visual graph editor in the first slices** (section 10); a graph view is a Design-phase decision.
- No change to Bug Spray, Beacon, Sentry or Tunnel.

## 6. Data model (entities and evidence; the Architect decides tables and files)

**Case** — id, title, purpose note (operator's text), status, created at, closed at.

**Evidence item** (immutable once written)
| Field | Meaning |
|---|---|
| id, case id | |
| kind | `source_record` (one source's answer from a live collection), `collection_summary` (the run's summary card), `model_output` (Structure Query or dossier text), `image_metadata` (EXIF block), `operator_note`, `imported_run` |
| origin | agent (`osint` / `osint_heavy`), action ("Live Research · Domain"), source name (e.g. "crt.sh"), run id when one exists |
| request descriptor | what was asked: target, target type, source, endpoint name; **never** a key, token or auth header |
| captured at | UTC time the source answered (or the model finished); `unknown` for imports |
| added at | UTC time it entered the case |
| tool version | Sentinel `VERSION` + build, so a record can be tied to the code that parsed it |
| content | the bytes stored (section 7.1) and their media type |
| sha256 | over the stored content bytes |
| provenance label | `observed` (a source returned it), `model` (a model wrote it; never shown as fact), `operator` (typed by the operator), `imported` |

**Entity** — id, case id, type, value (normalised), display value, first seen at, notes. Types: `person`, `organisation`, `domain`, `ip`, `handle` (with platform), `email`. Normalisation: lower-case domains and emails, IP in canonical form (IPv6 compressed), handles as `platform:name`. One entity per (case, type, normalised value). **Phone** is not a type in the first slices (Q4).

**Mention** — evidence item × entity: "this evidence names this entity", with the path or field where it does. Every entity other than an operator-typed one has at least one mention.

**Link** — entity × entity × relation (`resolves_to`, `registered_by`, `hosted_on`, `same_as`, `mentions`, `operator_linked`) × basis: one or more evidence ids, or `operator assertion` with a note. A link with no evidence is shown as the operator's assertion, never as a finding.

**Timeline event** — sequence number, case id, time, event type, subject ids, `prev_hash`, `event_hash` (section 7.3). Append-only: nothing is updated in place; a correction is a new event.

Where it lives: under `user_data_base()/data/` so dev, frozen and portable modes all work and Emergency Reset erases it with the rest of `data/` (`services/portable_reset.py:57-63`). Writes honour the reset latch (`require_writes_allowed`). If any of it goes in `sentinel.db`, it arrives as a **new numbered migration** (v4) through the existing `_apply_schema_migrations`, which backs up the file first (`_backup_before_migration`) and is additive; no existing table or row is altered or dropped, and `sentinel.db` is never replaced or deleted. Evidence bytes are better kept as files (content-addressed by hash) than as BLOBs in the app database, but that is the Architect's call (ADR, since it is state).

## 7. Evidence snapshotting and hashing

### 7.1 What is snapshotted
- For a **live collection** (Trace Live Research / Exposure Check, Bloodhound collection): one `source_record` per source, holding that source's result object exactly as the provider returned it to the panel (the parsed JSON Sentinel already has), serialised **canonically** (UTF-8, sorted keys, no insignificant whitespace, as RFC 8785 JCS or a documented subset), plus one `collection_summary`. Raw HTTP bodies are **not** kept today and are not added here (that would change every provider; Q5).
- For **model output**: the text as received, UTF-8, with provider and model in the descriptor. Labelled `model`.
- For **image metadata**: the EXIF block that the panel showed, not the image (the image never enters the case unless the operator adds the file deliberately; Q7).
- For a source that failed or was skipped: a `source_record` whose content says so. Gaps stay visible.

### 7.2 Hash
SHA-256 over the stored content bytes, computed once at capture, stored beside the item and never recomputed into the stored value. Verify recomputes and compares.

### 7.3 Timeline chain
Each timeline event stores `event_hash = SHA-256(prev_hash ‖ canonical(event fields, including the sha256 of any evidence it adds))`. The first event of a case chains from a fixed per-case value. A changed, removed or reordered event, or a changed evidence file, shows up in Verify as the first broken link. This is tamper-*evident* within the app, not tamper-proof (7.5).

### 7.4 Imports
Importing an old `data/chats/` run creates one `imported_run` item whose content is the original file's bytes, hashed **at import**. `captured at` is `unknown`, the file's own `timestamp` field is shown as "claimed by the saved file", and the report says the hash dates from the import, not the collection.

### 7.5 What the hash does and does not show (goes into the report verbatim)
It shows that the stored copy has not changed since Sentinel captured or imported it, and lets a reader check an exported copy. It does not show that the source really returned it (no source signature), that the clock was right, or that the operator did not alter the machine. Reports say this in plain words.

## 8. Report export
- **Output**: one folder (or one `.zip`) the operator places through a save dialog, defaulting **outside** any git checkout (`~/Documents` or the Application Support data dir, never `data/reports/` in a checkout). Contents:
  - `report.md` (and `report.html` rendered from it, with no remote assets): case title, purpose note labelled "Operator's statement, not verified by Sentinel", summary counts, the entity list, the link list with each link's basis, the timeline, each evidence item's metadata, and the section 7.5 text.
  - `evidence/<sha256>.<ext>`: each evidence item's exact bytes.
  - `manifest.json`: every file with its SHA-256, the case's last `event_hash`, Sentinel version, export time.
  - `SHA256SUMS`: the same list in `shasum -a 256 -c` format, so a reader with no Sentinel can check it.
- **Selection**: whole case, or a subset of evidence the operator ticks; the manifest says which.
- **Model text** in a report is set apart and labelled "written by a language model, not checked" (section 9).
- **No model call to write the report.** An optional model-written summary would go through the existing request path (permission, budget, consent, Local only) and is a later slice, not part of export.
- Export writes nothing into the case except a timeline event (`export`, with the manifest hash).

## 9. Invariants and rules touched
| Rule (source) | How this spec keeps it |
|---|---|
| Never replace or delete `data/sentinel.db`; schema changes via migrations preserving history (app-context) | Additive v4 migration through the existing backup-then-migrate path; a test upgrades a v3 fixture with rows and checks every row survives (M4). Existing chat files are read, never rewritten, by import. |
| Do not commit private investigation data (app-context) | Case data under an ignored path; export defaults outside the checkout; a test asserts `git check-ignore` covers the case folder (M6). The pre-existing `data/reports/` gap is flagged in section 2. |
| Never claim to verify scope or authorisation (app-context Product) | The purpose note is labelled as the operator's own unverified statement in the UI and the report; no "authorised", "in scope", "lawful" or "verified" wording in any case text; a copy test pins this. |
| Person/phone data-broker lookup stays excluded (app-context) | No lookup is added; Person is a label only; Phone is not an entity type (Q4). |
| Local-first; Local only never contacts a cloud provider; no paid call without permission, budget and consent (app-context) | Case operations make no network call (M5). Any model use stays on the existing gated path. Readiness item H3 (Local only fails open in agent panels) must be fixed before any slice lets a case trigger a model call. |
| Never print, copy or commit `.env` values (app-context) | Request descriptors exclude keys, tokens and auth headers; a test feeds a keyed source and checks no key bytes reach the case or the export. |
| Emergency Reset erases data and honours the write latch | Case data lives under `user_data_base()/data/`; writes call `require_writes_allowed()`. |
| Qt: no fetch or lock-wait on the UI thread; `shutdown()` names every QThread (lab AGENTS) | Hashing large evidence, Verify and export of a big case run off the UI thread if they can exceed a frame; the Architect sizes this. |
| EU AI Act Art. 50 (product-manager playbook) | Sentinel does not publish content or let a third party talk to a model, but an exported report can carry model-written text to a third party. Model text is labelled in the report; whether more is required is for the Comply consult, not this spec. |
| Personal data about third parties (app-context Distribution) | A case is a structured file on named people and handles, which raises the privacy weight of what Sentinel stores. Retention and deletion are open questions (Q3); the legal-compliance consult should read this spec at Define (AGENTS "Legal consults"). This spec gives no legal opinion. |

## 10. Smallest first slice (S1)
Goal: one investigation from Trace into a checkable report, with no graph.
1. Create, open, close a case (title + purpose note). List of cases.
2. **Add to case** on a finished Trace Live Research or Exposure Check run: one `source_record` per source + one `collection_summary`, canonical JSON, SHA-256, timeline events with the chain.
3. The run's target becomes one entity automatically; the operator can add entities by hand. No extraction from records yet, no links yet.
4. Timeline list view and **Verify case**.
5. Export: `report.md`, `evidence/`, `manifest.json`, `SHA256SUMS`. No HTML yet.
6. Storage, migration (if any) and ignore rules as in sections 6 and 9.

Later slices, in order: **S2** Bloodhound collection and dossier (saved at collection time, so a failed dossier no longer loses what was collected), Structure Query and import from Saved Chats; **S3** entity extraction from known record shapes (DNS A/AAAA, WHOIS registrant org and emails, crt.sh names, GitHub profile) as proposals the operator accepts, and links with evidence; **S4** graph view and HTML report (needs Design); **S5** optional model-written summary through the gated path.

## 11. Success measures (each a test or a number)
- **M1 capture.** From a fixture Trace Live Research result with 3 sources (one failed), Add to case creates 4 evidence items; recomputing SHA-256 over each stored file matches the stored hash; the failed source is present as a gap.
- **M2 tamper evidence.** Changing one byte of one evidence file makes Verify report exactly that item as changed and the chain broken from its event; deleting a file reports it missing; an untouched case reports all intact.
- **M3 independent check.** For an exported case, `shasum -a 256 -c SHA256SUMS` run in the export folder (a subprocess test) passes; altering one exported file makes it fail on that file.
- **M4 migration.** A v3 `sentinel.db` fixture with rows in every table upgrades with a backup written, all prior rows equal, `user_version` raised by one; an existing chat file is byte-identical after import into a case.
- **M5 no network.** Create, add, verify and export run with sockets blocked (a test that fails on any connect).
- **M6 not committable.** `git check-ignore` covers a file in the case folder and the default export location when it is inside the checkout.
- **M7 no keys.** With a keyed source's fixture, no key or auth header text appears in any evidence file, manifest or report.
- **M8 honest copy.** Report and UI strings contain the section 7.5 sentence and the purpose-note label, and none of "verified scope", "authorised", "forensically sound", "court-admissible", "tamper-proof" (test pins the list).
- **M9 owner check (manual).** The owner takes one real investigation of an asset they own (for example their own domain) from Trace into a case and out to a report, and a second person checks the export with `shasum` alone.
- Full suite green in CI modes, new logic mutation-checked (AGENTS definition of done).

## 12. Where this sits against the open "before testing" items
`TODO.md` → *v3 — later* → *Before testing* holds open P1s that block the testing phase whose goal (CLAUDE.md) is that every agent does 100% of what it promises: readiness B1 (cloud contacted at launch and in Local only), B2 (rebuild the venv), H3 (Local only fails open in agent panels), H4 (default mode vs docs), H1/H2 (kill-switch DNS), plus the Tunnel live checks and PR #52.
This feature is **new, XL and user-visible**: it adds promises (integrity, honest reports) that the testing phase would then also have to cover. Recommendation: approve or reject the spec now (cheap), then **park Design, Architect and Build until the testing phase has started on the current scope**, or at least until B1 and H3 are closed, because a case that can trigger a model call depends on Local only holding. If the owner wants it in the tested scope instead, S1 alone is the candidate; S2-S5 stay after testing.

## 13. Open questions for the owner (Define gate)
- **Q1 Priority and timing.** Park until after the testing phase (recommended), or bring S1 into the tested scope?
  **Owner, 2026-11-18:** Park until after the testing phase. Approve the spec now; Design, Architect and Build wait until testing has started on the current scope or B1 and H3 are closed.
- **Q2 Cases vs Chat Projects.** Keep cases as their own object (recommended: different shape, immutability), or make every case also a Chat Project so its spend and chats group under it?
  **Owner, 2026-11-18:** Separate object. A case is not a Chat Project.
- **Q3 Retention and deletion.** Should closed cases offer an expiry (for example delete after N days with a reminder), and should deleting a case also delete the original Saved Chats runs it was built from, or leave them?
  **Owner, 2026-11-18:** Manual delete only, no expiry. Deleting a case leaves the original Saved Chats runs untouched.
- **Q4 Phone numbers.** Trace accepts a phone target today. Allow `phone` as a passive entity type (a label, no lookup), or leave it out entirely?
  **Owner, 2026-11-18:** Leave phone out; it is not an entity type.
- **Q5 Raw responses.** Snapshot the parsed records Sentinel already holds (recommended for S1), or change providers to keep raw HTTP bodies too (closer to the source, touches every provider, bigger files)?
  **Owner, 2026-11-18:** Snapshot the parsed records Sentinel already holds; no raw HTTP bodies.
- **Q6 Trusted timestamps.** Is an optional third-party timestamp on the export manifest (a cloud call, off by default, per export) ever wanted? If not, it stays a non-goal.
  **Owner, 2026-11-18:** No third-party timestamps; stays a non-goal.
- **Q7 Images.** May the operator add the image file itself to a case (it may hold faces and GPS), or only its EXIF block?
  **Owner, 2026-11-18:** EXIF block only; the image file is not added.
- **Q8 Report formats.** Markdown + manifest for S1, HTML in S4: enough, or is PDF needed (a new dependency)?
  **Owner, 2026-11-18:** Markdown + manifest for S1, HTML in S4; no PDF.
- **Q9 The `data/reports/` gap.** Add the one-line `.gitignore` fix as its own TODO item now (independent of this feature)?
  **Owner, 2026-11-18:** Done (merged in 0985c81, `data/reports/` is git-ignored).

## 14. Design (S1 only, `ux-designer`, 2026-11-18)
Started while the spec is parked: the owner asked for Design now (2026-11-18). Covers S1 (section 10) only. No graph view (S4), no Bloodhound, Structure Query or import (S2), no extraction or links (S3). Playbook: `docs/playbooks/ux-designer.md`, `status: current`, last-reviewed 2026-11-11. The Architect decides storage, threads and file layout; this section says what the operator sees.

### 14.1 Where cases live
- A **Cases** row in the left rail footer, under the agent list, next to the existing History / Saved searches footer rows (`main.py:2285`). It is always visible, whichever agent is selected. Cases are not an agent and do not join `BUILTIN_AGENT_ORDER`.
- Clicking it opens the **Cases page** in the main area (it replaces the agent view the way an agent does; the agent rows lose their checked state). Selecting an agent leaves the Cases page; the page remembers the open case for the session.

### 14.2 Cases page: the list
One list, newest activity first. Columns: **Title**, **Status** (Open / Closed, as text), **Evidence** (count), **Last activity** (local time). A filter toggle above it: *Open* (default) / *All*. Buttons: **New case** (primary), **Open**, and on a selected row **Delete…**. Double-click or Return opens a case.

| State | What shows |
|---|---|
| No cases yet | "No cases yet. A case collects Trace results as evidence you can check and export." + **New case** button. |
| Only closed cases, filter on Open | "No open cases. Show all to see closed ones." with a link-style **Show all**. |
| Loading (only if the Architect finds it can exceed a frame) | The list area says "Loading cases…"; buttons disabled. |
| Case store unreadable | "Sentinel could not read the case store: <reason>. Nothing was changed." Buttons other than **New case** disabled; **New case** also disabled if the folder is unwritable. |

### 14.3 New case dialog
- **Title** (required, 1-120 characters, trimmed; inline error "Give the case a title.").
- **Purpose note** (optional, multi-line). Label above it: "Purpose (your own statement, not verified by Sentinel)". Placeholder: "Why you are looking into this, e.g. 'phishing domain impersonating my site'." No hint that a purpose makes anything lawful or in scope.
- Buttons **Create case** (default) / **Cancel**. Create opens the new case. The timeline's first event is "Case created".
- Duplicate titles are allowed (cases are told apart by created time); the list shows both.

### 14.4 Case view
Header: title, status badge (text "Open" / "Closed", not colour alone), created time, purpose note shown with the label "Operator's statement, not verified by Sentinel", and **Last check**: "Not checked yet" or "<time>: no changes found" / "<time>: 2 problems found". Header actions: **Verify case**, **Export report…**, **Close case** (or **Reopen case**), **Back to cases**.

Below, a two-pane split:
- **Left, tabs:** **Timeline** (default) and **Identifiers**.
- **Right, details pane** for whatever is selected; empty state: "Select an event or identifier to see its details."

Closed case: read-only. **Add identifier** is disabled with the tooltip "Reopen the case to change it". Verify and Export still work. Closing needs no confirmation (it is reversible) and adds a "Case closed" event. Reopening adds a "Case reopened" event.

### 14.5 Timeline tab
A table, oldest first (the chain order), never editable. Columns: **#** (sequence), **Time** (local; the tooltip shows UTC), **Event**, **Details**, **Check** (blank until a Verify has run, then the state for that row).

Event wording: "Case created", "Trace results added: Live Research · Domain · example.com (4 items)", "Evidence: crt.sh (observed)", "Evidence: run summary", "Identifier added: domain example.com (from the run target)" or "(added by you)", "Case closed", "Case reopened", "Integrity check: no changes found", "Integrity check: 2 problems found", "Report exported to <folder name> (12 files)".

Selecting an evidence row fills the details pane:
- **Source**, **Action**, **Captured** (UTC, with the local time beside it), **Added**, **Sentinel version**, **Origin label**: "Observed: a source returned this" or "Typed by you". S1 has no `model` or `imported` items.
- **SHA-256**: full hash in a monospace, selectable field with a **Copy hash** button.
- **Content**: the stored JSON in a read-only, monospace, scrollable viewer. A failed or skipped source shows its gap text ("crt.sh did not answer: timeout") with the same metadata. Gaps are not hidden or greyed out.
- No edit, delete or re-run control exists for an evidence item.

Empty timeline (case only just created): a hint line under the "Case created" row: "Add Trace results from a finished Live Research or Exposure Check with **Add to case**."

### 14.6 Identifiers tab
A list: **Type** (Person, Organisation, Domain, IP address, Handle, Email), **Value** (display form), **Source** ("run target" or "added by you"), **First seen**. Selecting one shows the evidence items that mention it (S1: the run's items for a run target, none for a hand-added one: "Added by you; no evidence names it yet.").

**Add identifier…** opens a small dialog: type drop-down (no Phone), value, optional note. Inline validation per type: "Not a valid IP address", "Not a valid email address", "Not a valid domain". A Handle asks for a platform (free text, e.g. "github"; "unknown" allowed). A duplicate after normalisation does not add a row; it says "Already in this case as example.com" and selects the existing one.

Empty state: "No identifiers yet. The target of each run you add appears here, or add one yourself."

### 14.7 Add to case from Trace
- An **Add to case…** button joins Trace's action row after **Stop**. It is enabled only when the panel shows a **finished** Live Research or Exposure Check result. It is disabled while a run is busy, after a Structure Query ("Structure Query results come to cases in a later version"), after a stopped run ("Only finished runs can be added; run it again to add it") and when no result is shown. A disabled button always carries the reason as its tooltip and accessible description.
- Clicking opens a picker: the open cases (most recent first), plus **New case…** at the top, which opens the 14.3 dialog and then adds to the new case. Closed cases are not listed. With no open case the picker goes straight to **New case…**.
- Before adding, the picker shows what will be stored: "4 evidence items: 3 source records (1 failed: crt.sh) and 1 run summary. Nothing is looked up again." Then **Add** / **Cancel**.
- Run target to identifier: Domain to domain, IP address to IP, Email to email, Company to organisation, Username to handle with platform "unknown" (the operator can add a more exact handle by hand). Live Research refuses Person and Phone targets today, so S1 never has to map them.
- **Same run twice:** if this run is already in the chosen case, the picker says "This run is already in <case title> (added <time>)." and offers only **Open case** / **Cancel**. Adding the same run to a *different* case is allowed.
- **Success:** a non-modal note in Trace's activity log, "Added 4 items to case <title>.", with **Open case**. The button changes to **Added to <title>** (disabled) for that result.
- **Failure:** nothing is partly added. The message says "Nothing was added: <reason>." The reasons are the case store being unwritable, the disk being full, or "Sentinel is in reset mode; writes are blocked" (the reset latch).

### 14.8 Verify case
- **Verify case** runs off the UI thread. The header shows "Checking 12 of 40…" with **Cancel**. While it runs, Add to case and Export for that case are disabled. A cancelled check adds no timeline event and changes no Check column.
- **Result banner** above the tabs (dismissible; it stays in **Last check**):
  - All intact: "No changes found. All 40 stored items match the hashes recorded when they were captured, and the timeline chain is unbroken." It is followed by the section 7.5 sentence in smaller text, with a **What this shows** disclosure that expands to the full 7.5 text.
  - Problems: "2 problems found. 1 item changed since capture (crt.sh, event #7). 1 item missing (run summary, event #8). The timeline chain is broken from event #7." Each item name selects its row.
- **Check column** per row: "Intact", "Changed", "Missing", "Chain broken". The state is the text plus an icon, never colour alone. "Changed" and "Missing" use the danger colour, "Intact" the normal text colour (no green "verified" look).
- Verify adds a timeline event with its outcome. Wording never uses "verified", "authentic", "proven" or "tamper-proof" about the evidence. The button keeps the spec's name, **Verify case**; results speak of "changes since capture".

### 14.9 Export report dialog
- **What to include:** *Whole case* (default) or *Selected evidence*. The second shows a checklist of evidence items (source, captured time) with Select all / none. The case summary, identifiers and timeline are always included, and the dialog says so next to the choice: "Identifiers and the timeline are always exported, including those that name people only in unselected evidence." (Legal consult P1-4, 2026-11-18: chosen over pruning, which would need derived-data rules; the Architect may revisit.) The manifest records which evidence was selected. A tick **Omit case title and purpose note** (off by default) leaves both out of `report.md` and the manifest.
- **Where:** a folder field plus **Choose…**. The default is `~/Documents/Sentinel cases/`. The export goes into a new subfolder `<case title> <YYYY-MM-DD HHMM>`, shown in full before export. If the chosen folder is inside a git repository, an inline warning appears: "This folder is inside a git repository. Exported case data could be committed by mistake." **Export** stays enabled, but the operator must tick "Export here anyway". The folder note also says, always: "Folders in Documents or Desktop may sync to iCloud, which copies the report off this Mac." (P1-3.)
- **What will be written:** a fixed list, `report.md`, `evidence/` (N files), `manifest.json`, `SHA256SUMS`, and one sentence: "The report names people, accounts and addresses. Share it only with whoever needs it." `report.md` opens with a fixed handling block (a constant in `case_export.py`, pinned by M8): the same sentence, "Records may concern a different person with the same name or handle", and an optional operator line "Shared for: …" (P1-5).
- **Integrity first:** Export runs the Verify check before writing. If it finds problems, the dialog says so ("2 stored items changed or missing since capture. The report will list them as such.") and offers **Export anyway** / **Cancel**. Changed items are exported with both hashes and marked in `report.md`. It is never silently exported as intact.
- **Progress / errors:** "Writing 12 of 40…" with **Cancel**. Cancel or failure leaves no partial folder (the Architect picks the mechanism). Error copy: "Export failed: <reason>. Nothing was left in <folder>."
- **Done:** "Report exported to <path>." with **Show in Finder** and a copyable check command: `cd "<path>" && shasum -a 256 -c SHA256SUMS`. Below it: "Anyone with the folder can run this to check every file, without Sentinel." The export adds a timeline event.

### 14.10 Delete case
Section 5.1 and Q3 (manual delete only) apply. The plan decides whether delete ships in S1 or S2. Design: **Delete…** on the case list only (not in the case view header, to keep it away from routine actions). The confirmation names the case and the counts: "Delete '<title>' and its 40 evidence items and 3 identifiers? This cannot be undone. Your Trace runs in Saved searches are not deleted. Exported reports already on disk are not touched. Copies in backups and Time Machine are not reached." The dialog lists the saved runs and earlier exports this case knows of (from `run_added` and `export` events) and offers an **unticked** "Also delete these saved runs" (Legal consult P1-2). Wording never says "erased" or "securely". **Owner decision needed:** the unticked option changes Q3's "manual delete only, originals left" answer into "left by default, deletable on request"; it is applied here at the owner's instruction of 2026-11-18 ("Apply the P1 fixes") and needs the owner's explicit yes at the next gate. Buttons: **Delete case** (danger style, not default) / **Cancel** (default, Escape).

### 14.11 Copy rules (M8)
- Purpose note label everywhere (header, export): "Operator's statement, not verified by Sentinel".
- The section 7.5 text appears in Verify's **What this shows** and in `report.md`, word for word.
- Banned in any case UI string and in the report template: "verified scope", "authorised"/"authorized", "in scope", "lawful", "forensically sound", "court-admissible", "tamper-proof", "proven", "authentic". The M8 copy test reads the case UI module(s) and the report template.
- "Observed" means a source returned it. It never means true. S1 shows no model text; when S2 adds it, its label is "Written by a language model, not checked".
- Plain verbs on buttons: New case, Add to case…, Add identifier…, Verify case, Export report…, Close case, Reopen case, Delete…. Ellipsis only where a dialog follows.

### 14.12 Accessibility (WCAG 2.2 AA by analogy, playbook)
- Everything works by keyboard. Tab order on the case view: header actions, tabs, table, details pane. Return opens or selects, Space ticks, Escape closes dialogs. The focus ring is visible on every control and never hidden behind the details pane (2.4.11).
- State is never colour alone: status badges, the Check column and the Verify banner carry text, and their icons have accessible names.
- Hashes, paths and the shasum command are selectable text, and each has a Copy button with an accessible name ("Copy SHA-256 of crt.sh evidence").
- Click targets are at least 24×24 px. Table rows are at least 28 px high, matching the existing rail rows (36 px).
- New colour uses reuse existing tokens: normal text `#a8b3ad`/`#e8ece9`, accent `#3cff88`, the existing DangerAction colour. Any new token goes into the contrast test (4.5:1 text, 3:1 indicators).
- Every disabled button has its reason in the tooltip and in `setAccessibleDescription`. The Verify and Export progress lines are announced (a live-region-like accessible name update).
- The Verify phase includes a five-minute VoiceOver pass over the Cases page, the case view and the three dialogs.

### 14.13 Open points for the Architect (not owner questions)
- Whether loading the case list or opening a large case can exceed a frame. If so, the "Loading…" states in 14.2 apply.
- The no-partial-output mechanism for Add to case and Export (14.7, 14.9).
- How "same run already in this case" is detected. The design assumes the run id or the saved chat file identifies a run.
- Whether "Export anyway" with changed items needs fields in the manifest (both hashes per changed item).

## 15. Architecture (S1 only, `architect`, 2026-11-18)
Started while the spec is parked: the owner asked for Architect now (2026-11-18). Covers S1 (section 10) and the Design in section 14. Decisions that are expensive to reverse are ADRs: **ADR 0005** (storage layout) and **ADR 0006** (canonical JSON, hash and chain). Radar check (`stack`): not run; S1 adds no dependency (stdlib `hashlib`, `json`, `os`, `uuid`). Legal consult at Architect (data map, retention, DPIA screening): **not run yet**; section 15.12 is its input.

### 15.1 Storage (ADR 0005)
- **No change to `sentinel.db`, no v4 migration.** A v4 would lock every older build out of `sentinel.db` (`services/database.py:169-174`) for state no other feature needs.
- One folder per case: `user_data_base()/data/cases/<case_id>/` with `timeline.jsonl` (source of truth), `evidence/<sha256>.json` (content-addressed, per case), `summary.json` (derived cache for the list) and `.pending/` (staging).
- Everything about a case is an **event**: `case_created` (title, purpose), `case_closed`, `case_reopened`, `run_added`, `evidence_added`, `entity_added`, `verify`, `export`. Current state is a replay of the timeline. Entities in S1 are add-only, so this costs nothing; S3's links and merges are more events.
- `.gitignore` gains `data/cases/`. M6 tests it with `git check-ignore`.
- **M4 needs rewording at this gate** (it is a Define-approved measure): proposed "every case operation leaves `sentinel.db` byte-identical with `user_version` 3"; the chat-file half moves to S2 (import).

### 15.2 Writes, reset latch, single writer
- `CaseStore` (15.4) calls `require_writes_allowed()` at the start of every write and again immediately before the final `os.replace` of a timeline.
- **The UI thread is the only writer of case state.** Create, close, reopen, add identifier and Add to case run synchronously in `CaseStore` on the UI thread: they are local file writes of KB-sized data, with no fetch and no lock. Verify and Export workers only *read* the case; their timeline event is appended by the UI thread when the worker's `finished` signal arrives. No lock is shared between threads, so the UI thread can never wait on one (lab rule).
- Build measures Add to case on the largest realistic Trace result (Passive DNS plus crt.sh for a busy domain). If it exceeds 100 ms, Add to case splits: a worker canonicalises, hashes and writes the files into `.pending/`, and the UI thread still does the commit (15.7). The commit rule and the on-disk format do not change.

### 15.3 Canonical JSON, hash, chain (ADR 0006)
- Serialisation `sentinel-json-v1`: sorted keys, `(",", ":")` separators, UTF-8, no ASCII escaping, finite floats as Python `repr`, NaN/±Inf as strings, unknown types raise. A golden test pins the bytes.
- Evidence `sha256` = SHA-256 of the exact stored bytes, recorded in its `evidence_added` event.
- `event_hash = SHA-256(canonical(event without event_hash))`, with `prev_hash` inside the body; the first event's `prev_hash` is `SHA-256("sentinel-case-v1:" + case_id)`.
- Evidence metadata (origin, request descriptor, captured at, added at, tool version, provenance, media type, sha256) lives **in the event**, not in the file. The chain therefore covers it, and identical bytes from two runs are stored once.
- **Captured at:** providers return no per-source time (section 2). S1 records the run's finish time with `captured_at_basis: "run_finished"`, and the details pane and report say "Run finished (UTC)" instead of "Captured". Per-source times need a provider change and stay out of S1.
- **Tool version:** `services/app_version.py` (VERSION + build), the string the window shows.

### 15.4 Module boundaries
| Module | Holds | Imports Qt? |
|---|---|---|
| `services/case_integrity.py` | `canonical()`, `sha256_hex()`, `genesis()`, `event_hash()`, `verify_timeline()` (pure: bytes in, result out; no I/O except reading evidence through a callable). The mutation-check target. | no |
| `services/case_store.py` | Paths, case id, replay into a `Case` dataclass, every write (15.7), recovery on open, list from `summary.json`, `find_run()` (15.8), entity normalisation and validation (domain, IP, email, handle; no phone). | no |
| `services/case_capture.py` | Turns a Trace live result dict into evidence items: one `source_record` per source in `sources_contacted` + `sources_skipped` (each with its result key or its gap text), plus one `collection_summary` holding everything else (`query`, `type`, `cancelled`, the source lists, any key not mapped to a source). Nothing in the dict is dropped. Builds the request descriptor from an **allow-list** of fields (target, target type, source label, action), so a key, token or header can never pass through (M7). | no |
| `services/case_export.py` | `report.md` template, manifest, `SHA256SUMS`, the staged-folder writer (15.7). Holds the 7.5 text and the purpose label once, as constants the UI imports too (M8). | no |
| `ui/panels/cases.py` | Cases page, case view, dialogs (14.2-14.10), `VerifyWorker`, `ExportWorker`, `shutdown(deadline)`. | yes |
| `ui/panels/osint.py` | The **Add to case…** button, its enable rules (14.7) and the picker. It hands `case_capture` the result dict it already holds plus a `capture_id`. | yes |
| `main.py` | The **Cases** rail-footer row (by `main.py:2285`), the page in the main stack, registration as `self.panels["cases"]` so `shutdown_panels()` reaches it, a `cases_store` attribute both panels use. | yes |

Boundaries: the three `services/case_*` modules import nothing from `ui/`, `providers/` or any network module; a test asserts this (supports M5). `case_capture` reads the result dict and nothing else; no lookup is re-run.

### 15.5 Threading
- **VerifyWorker** and **ExportWorker** are `QThread` subclasses in `ui/panels/cases.py`, following `ui/workers.py`: a `cancel()` flag checked between files, `progress(done, total)` and `finished(result)` signals, never `terminate()`.
- `CasesPanel.shutdown(deadline)` calls `join_or_park([self._verify_worker, self._export_worker], deadline)` by name; ADR 0001's park-and-`os._exit` rule applies. A parked export leaves its staging folder; 15.7 cleans it at next start.
- One Verify and one Export at a time, app-wide (buttons disabled while one runs). During either, Add to case and Export (or Verify) for that case are disabled (14.8). Close, reopen and add identifier stay allowed: the worker reads a snapshot (`timeline.jsonl` is opened once; `os.replace` never changes an open file's bytes) and records `head_seq` in its result.
- **List and open (14.13 point 1):** the list reads one `summary.json` per case (a few hundred bytes each) and stays synchronous. Opening a case replays `timeline.jsonl`; at 2,000 events (about 2 MB) that is a JSON parse of a few ms, so it is synchronous too, and the "Loading…" states of 14.2 are **not built in S1**. Build adds a benchmark test at 2,000 events that reports the time (not asserted in CI). If it exceeds 50 ms, opening moves to a worker and the loading states come back. Evidence content is read only when its row is selected, and the viewer shows at most the first 2 MB with a note.

### 15.6 Recovery on open
On first use per session `CaseStore` deletes every `data/cases/*/.pending/` and every `evidence/` file that no event references (a leftover of a write that never committed, 15.7). It also deletes folders named `data/cases/.deleting-*` and the staging folders listed in `data/cases/.exports-pending.json`. Each action is noted in the local log by case id and count only, never by content.

### 15.7 No partial output (14.13 point 2)
**Add to case (and every other case write)**:
1. `require_writes_allowed()`. Build every item and every new event in memory (canonicalise, hash, chain).
2. Write each new evidence file to `.pending/<txn>/<sha256>.json` (`open(..., "xb")`, write, `fsync`).
3. Write the new full timeline to `.pending/<txn>/timeline.jsonl`, `fsync`.
4. `os.replace` each pending evidence file into `evidence/` (skip if that hash already exists there), then `fsync(evidence/)`.
5. `require_writes_allowed()` again, then `os.replace` the pending timeline over `timeline.jsonl`, `fsync` the case folder. **This replace is the commit.**
6. Rewrite `summary.json` the same way (temp, replace). It is a cache: if this step fails, the next read rebuilds it.
7. Remove `.pending/<txn>/`.
A failure or crash before step 5 leaves the old timeline in force. The only leftovers are unreferenced files, which 15.6 removes, so the case shows nothing of the add ("Nothing was added: <reason>", 14.7). A crash after step 5 means the add happened in full. Evidence is in place before any event references it, so a committed case never points to a missing file.

**Export:**
1. Pick the final folder `<dest>/<case title> <YYYY-MM-DD HHMM>`, with the title sanitised (no `/`, `:`, leading `.`; at most 80 characters). Add ` (2)`, ` (3)`… if it exists.
2. Record the staging path in `data/cases/.exports-pending.json` (only when writes are allowed; otherwise the export is refused, because its timeline event could not be written either).
3. Write everything into `<dest>/.sentinel-export-<uuid>.partial/`, `fsync` each file.
4. `os.rename` the staging folder to the final name (same parent, so it is atomic). Then remove the pending record.
5. On cancel or error: `shutil.rmtree` the staging folder and remove the record. The export left nothing ("Nothing was left in <folder>", 14.9). If the process dies, 15.6 removes the staging folder at next start.
6. UI thread appends the `export` event (`data`: folder name, file count, selection, manifest sha256). If that append fails, the export on disk stands and the message says the case could not record it.

**Delete case** (whichever slice ships it, 14.10): `os.rename` the case folder to `data/cases/.deleting-<case_id>`, then `rmtree` it. The case vanishes in one step; a crash mid-delete is finished by 15.6.

### 15.8 Same-run detection (14.13 point 3)
The Trace panel holds no run id today: `record_external_research` (`main.py:4683`) mints one and does not return it, and a failed save leaves none. So:
- When a live lookup **finishes** (`_on_lookup_finished`), the panel mints `capture_id = uuid4().hex` and keeps it with the result it shows.
- `record_external_research` returns its `run_id` (one-line change; callers that ignore it are unaffected). The panel keeps it when there is one.
- `run_added` events carry `capture_id`, the `run_id` (or `null`) and the saved chat file name (or `null`). `CaseStore.find_run(case, capture_id)` scans that case's `run_added` events. A match produces the 14.7 "already in this case" state.
- A result re-shown from **Saved searches** (`_show_lookup_result(save=False)`) has no `capture_id`, so Add to case is disabled with "Saved searches come to cases with import, in a later version". Import from Saved Chats is S2. **Design addition**, flagged for the owner: section 14.7 did not list this disabled state.

### 15.9 Verify
`VerifyWorker` runs `case_integrity.verify_timeline` over a snapshot. It returns, per event, `intact | changed | missing | chain_broken`, the first broken `seq` or `None`, counts, `head_seq`, and the truncation note (ADR 0006). Hashing streams each file in 1 MB blocks and checks `cancel()` between files. Cancel returns nothing and writes nothing (14.8). The UI thread appends a `verify` event with the counts and first broken seq, and rewrites `summary.json` with "Last check".

### 15.10 Export contents and manifest (14.13 point 4)
Export first runs the same verify (14.9 "integrity first"), inside `ExportWorker`. For each selected evidence item it reads the stored bytes **once**, hashes them, and writes those same bytes. The exported file and its `SHA256SUMS` line therefore always agree, even if the stored file changed. Exported name: `evidence/<recorded_sha256>.json`. A changed item keeps the recorded name, and its SHA256SUMS line holds the hash of the bytes actually written, so `shasum -c` passes on the export while the manifest and report show the mismatch.

`manifest.json` (canonical `sentinel-json-v1`, so it is itself stable):
- `format: "sentinel-case-export/1"`, `sentinel_version`, `exported_at` (UTC), `case_id`, `case_title`
- `selection: "whole" | "selected"`, `selected_seqs` (evidence event numbers; all of them for whole)
- `timeline_head: {seq, event_hash}` and `chain: {state: "intact" | "broken", first_broken_seq}`
- `check: {intact, changed, missing}` counts from the pre-export verify
- `files`: one entry per exported file other than `manifest.json` and `SHA256SUMS`: `{path, sha256}`. For evidence also `{seq, kind, source, recorded_sha256, state}`, where `state` is `intact | changed`. **Changed items carry both hashes** (`sha256` = exported bytes, `recorded_sha256` = at capture).
- `missing`: `{seq, source, recorded_sha256}` per missing item. No file, and no SHA256SUMS line.
- `statement`: the section 7.5 text.

`SHA256SUMS` lists `report.md`, `manifest.json` and every evidence file in `shasum -a 256` format (`<hex>  <path>`, two spaces, relative paths, LF). `report.md` marks changed and missing items in the evidence table and the summary. **Not in S1:** `timeline.jsonl` in the export (a reader could then recheck the chain outside Sentinel). It is a small later addition, worth an owner yes because it carries every event.

### 15.11 Success measures touched
M1 to `case_capture` + `case_store` tests on a 3-source fixture (one error) giving 4 files whose recomputed hashes match. M2 to `verify_timeline` tests (byte flip, deleted file, edited event, reordered lines, truncation note). M3 to a subprocess `shasum -a 256 -c` test in a temp export (macOS and ubuntu CI both ship `shasum`). M4 rewording in 15.1. M5 to a socket-blocking fixture around create, add, verify and export, plus the import-boundary test (15.4). M6 to `git check-ignore data/cases/x/timeline.jsonl` and the git-repo warning, found by walking parents for `.git` without running `git`. M7 to a keyed-source fixture and the descriptor allow-list. M8 to a copy test over `ui/panels/cases.py` and `services/case_export.py`. New: a golden test for `sentinel-json-v1` and the chain (ADR 0006), and a crash-ordering test that fails `os.replace` at each step of 15.7 and asserts the case replays unchanged.

### 15.12 Data map (input for the legal-compliance consult)
**Amended 2026-11-18 (legal consult P1-1, P1-3):** the table below is incomplete. Rows to add before Build: `data/chats/` and Saved searches (the original runs; not removed by Delete case unless the operator ticks it), `runs.prompt_summary`, `usage_log.json`, `summary.json`, `.exports-pending.json`, `.partial` staging, `.deleting-*`, the Trace activity log, OS-level copies (Time Machine, Spotlight, iCloud, clipboard); plus columns for data-subject categories, special-category flag, controller, recipients, lawful basis and Art. 14. The Exports row's "only in an export" is wrong: a default folder under Documents may sync to iCloud. Full list: `docs/audit/case-workspace-legal-compliance.md` section 3. **P1-5 scope (owner decision relayed by the spec session, 2026-11-18: "accept all five, lawyer item blocks sharing only"):** the household/personal-activity exemption is a LAWYER trigger. It does not block S1 Build. It blocks only handing an exported report to a third party. **Precondition (P1-7):** a DPIA screening and a short DPIA are recorded in `docs/legal/privacy.md` before the first real case or Delete case ships (not before coding).
| Data | Where | Leaves the Mac? | Retention |
|---|---|---|---|
| Case title, purpose note (operator text) | `data/cases/<id>/timeline.jsonl` | only in an export the operator makes | until the operator deletes the case (owner Q3) |
| Evidence: parsed public-source records about third parties (WHOIS contacts, emails, handles, IPs, breach listings) | `data/cases/<id>/evidence/` | only in an export | as above; also erased by Emergency Reset (portable) |
| Entities (identifiers typed or taken from a run target) | timeline | only in an export | as above |
| Exports | operator-chosen folder, default `~/Documents/Sentinel cases/` | when the operator hands it on | outside Sentinel's control once written |
No processor, no network call, no model call in S1. The original Saved Chats runs are untouched (Q3).

### 15.13 Answers to section 14.13
1. **Can list or open exceed a frame?** The list does not (summary files). Opening is expected to stay under a frame up to about 2,000 events. The loading states are not built in S1; a benchmark decides (15.5).
2. **No-partial-output mechanism:** staged files, then an atomic timeline replace as the commit, plus recovery on open. Export uses a hidden staging folder renamed into place (15.7).
3. **Same run:** a `capture_id` minted when the lookup finishes, stored in `run_added`; `run_id` and the chat file name are recorded too (15.8).
4. **"Export anyway" fields:** yes. Per changed item, `sha256` (exported bytes) and `recorded_sha256` (at capture), plus `state`. Missing items go in `missing`. Chain state and the counts go at the top level (15.10).

### 15.14 Open points for the owner at this gate
**Owner, 2026-11-18:** "Approve Architect". The gate covered the points below: ADRs 0005 and 0006, the M4 rewording and the Saved-searches state. The legal consult is still open.
- Approve ADR 0005 (files, no migration) and ADR 0006 (format and chain).
- M4 rewording (15.1).
- The Saved-searches disabled state (15.8), a Design addition.
- Before Build: run the legal-compliance Architect consult on 15.12 (not run in this session).
