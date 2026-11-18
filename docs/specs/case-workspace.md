# Spec: case workspace for Trace and Bloodhound (2026-11-18)

Phase: **Define** only. No code, tests or schema changed. Design, Architect and Build have not started.
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
- **Q2 Cases vs Chat Projects.** Keep cases as their own object (recommended: different shape, immutability), or make every case also a Chat Project so its spend and chats group under it?
- **Q3 Retention and deletion.** Should closed cases offer an expiry (for example delete after N days with a reminder), and should deleting a case also delete the original Saved Chats runs it was built from, or leave them?
- **Q4 Phone numbers.** Trace accepts a phone target today. Allow `phone` as a passive entity type (a label, no lookup), or leave it out entirely?
- **Q5 Raw responses.** Snapshot the parsed records Sentinel already holds (recommended for S1), or change providers to keep raw HTTP bodies too (closer to the source, touches every provider, bigger files)?
- **Q6 Trusted timestamps.** Is an optional third-party timestamp on the export manifest (a cloud call, off by default, per export) ever wanted? If not, it stays a non-goal.
- **Q7 Images.** May the operator add the image file itself to a case (it may hold faces and GPS), or only its EXIF block?
- **Q8 Report formats.** Markdown + manifest for S1, HTML in S4: enough, or is PDF needed (a new dependency)?
- **Q9 The `data/reports/` gap.** Add the one-line `.gitignore` fix as its own TODO item now (independent of this feature)?
