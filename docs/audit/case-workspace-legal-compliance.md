# Legal consult (Architect phase): case workspace S1

Status: DRAFT, 2026-11-18. Read-only consult by the legal-compliance role. **Not legal advice; not a
sign-off.** The agent is not a lawyer. Nothing here says the feature is lawful; it lists what a
qualified person must decide and what the spec should change first.

Inputs: `docs/specs/case-workspace.md` (S = spec line), ADRs 0005 and 0006, `docs/foundry/app-context.md`
(tier `public`, confirmed by the owner 2026-11-11), `docs/legal/register.md` (main checkout, partial,
one open trigger about cloud model providers), `services/portable_reset.py`, `providers/exposure_lookup.py`.
Playbook `docs/playbooks/legal-compliance.md`: `status: current`, last-reviewed 2026-11-11 (7 days, not stale).

**How rules were checked.** The GDPR and Irish DPA 2018 articles below are cited from the agent's
knowledge of the text and were **not re-read from EUR-Lex or the Irish Statute Book in this pass**
(irishstatutebook.ie returned 403; the DPC's Art. 35(4) list PDF was not machine-readable). They are
marked "rule not verified". Verified in this pass (web search, 2026-11-18): the Criminal Justice
(Offences Relating to Information Systems) Act 2017 structure (s.2 access without lawful authority,
s.6 tools/passwords/codes, s.8 penalties, giving effect to Directive 2013/40/EU).

## 0. Summary

No P0. The slice is local-first, has no processor and no network call (S 383, M5), which keeps most
transfer and processor questions out of S1. The weight is elsewhere: S1 turns scattered per-run files
into a **structured file on named third parties that the operator then hands to other people**. Main
findings:

- **P1** Delete case does not erase the same data: the originals stay in `data/chats/`, the run log and
  usage log (owner Q3, S 167), and the data map omits those rows (section 3, items 1-2).
- **P1** The export is the one outward disclosure and has no handling notice, no field-level
  minimisation, and "Selected evidence" does not remove the identifiers and timeline that name the same
  people (S 252-254; section 4).
- **P1** The default export folder `~/Documents/Sentinel cases/` can sync to iCloud, which contradicts
  the data map's "only in an export the operator makes" (S 379-382; section 3, item 3).
- **P1** DPIA screening is not negative: two DPC list items fit (indirect sourcing, combined datasets).
  Record it before real-person data goes in (section 2).
- **P1** The household/personal-activity exemption cannot be assumed once a report is handed to third
  parties or a bounty is involved (S 32; register row 1 already marks it unclear). Lawyer trigger.

## 1. Regimes

| # | Regime | Verdict (agent view) | Reason and evidence | Source, date | Status |
|---|---|---|---|---|---|
| 1 | GDPR, Art. 2(2)(c) household exemption | **Unclear; do not rely on it.** | The exemption covers processing "by a natural person in the course of a purely personal or household activity". The operator is a private individual (S 31-32), but: the purpose list includes a bounty target (S 32), the product's output is a report handed to a registrar, platform, bank or the police (S 32, S 109-118), and the case is a structured, retrievable file on named people. CJEU case law reads the exemption narrowly and excludes processing whose results go beyond the private sphere (Lindqvist C-101/01; Ryneš C-212/13; recital 18). Whether the owner's own-asset and phishing-aimed-at-me cases are personal activity and others are not is a lawyer's call. | GDPR Art. 2(2)(c), recital 18; Lindqvist, Ryneš: agent knowledge, rule not verified, 2026-11-18 | DRAFT |
| 2 | GDPR, general (if row 1 does not apply) | **Applies.** Owner = controller of case data; a recipient of a report becomes a controller of its copy. | Third-party personal data: WHOIS contacts, emails, handles, IPs, breach-index hits (S 380). Art. 5 (lawfulness, purpose limitation, minimisation, accuracy, storage limitation, integrity), 6, 9, 10, 12-14, 15-19, 25, 30, 32, 35. | GDPR as above, rule not verified | DRAFT |
| 3 | Irish Data Protection Act 2018 | **Applies alongside the GDPR if row 1 fails.** | Restrictions on data-subject rights (s.60), special categories and criminal-offence data safeguards, and the journalism/expression provision (s.43) are the sections to check; section numbers are from memory. S1 is not journalism and the agent does not propose relying on s.43. | DPA 2018 (Irish Statute Book): not verified (site returned 403) | DRAFT |
| 4 | Commercial activity turn | Not commercial today (tier `public`, nothing paid; app-context Distribution). A paid build, or use for paid bounty/consultancy work, ends any "personal" reading and engages the professional-use analysis. The register already records the owner's intent to sell (app-context 31-32). | app-context, register | DRAFT |
| 5 | ePrivacy (S.I. 336/2011) | **Does not apply to S1.** | Reg. 5 concerns storing or accessing information on a user's terminal equipment by a third party; Sentinel stores its own files on the operator's own Mac and sets no cookies. Direct marketing (reg. 13) not touched. | S.I. 336/2011: not re-read | DRAFT |
| 6 | Computer Misuse: Criminal Justice (Offences Relating to Information Systems) Act 2017 | **Not engaged by S1; keep it that way.** | S1 adds no lookup and no network call (S 51, 383), so it touches no system. The Act's s.2 (access without lawful authority) and s.6 (tools, passwords, codes made available for ss.2-5 offences) matter to the collecting agents (Trace, Bloodhound, Bug Spray), not to storing results. Breach-data handling: the providers read **metadata only** (breach database names, counts, data classes) and never passwords, hashes or record contents (`providers/exposure_lookup.py:31-37`, `:508-513`; Intelligence X index only, never the file/read endpoint, `:22-30`). Keep that as a tested invariant (below, change 9). | Act verified by search 2026-11-18 (revisedacts.lawreform.ie, s.2, s.6, s.8) | CHECKED (agent) for structure; application is a lawyer matter |
| 7 | Breach-data handling beyond the above | **Applies as a data-sensitivity point, not a separate statute found.** | That an email appears in a named breach can reveal sensitive facts (a dating, health or adult site). That is an Art. 9 inference risk and the report hands it on (change 8). | GDPR Art. 9: not verified | DRAFT |
| 8 | Harassment, defamation and "doxxing"-type exposure | **Possible, owner-side.** | A report that names a person as the source of a scam, built from public records that may belong to a namesake, is a statement of fact handed to others. Non-Fatal Offences Against the Person Act 1997 s.10 (harassment), Defamation Act 2009 and the Harassment, Harmful Communications and Related Offences Act 2020 are the Irish statutes to ask a lawyer about; the agent has not read them. This is why the "observed means returned, not true" copy (S 265) matters. | Acts named from memory, not verified | DRAFT |
| 9 | EU AI Act | S1 shows no model text (S 219). S2 adds `model_output` (S 69, 116); labelling is already specified. Deployer duties are the owner's if used professionally (Art. 2(10) exempts purely personal use). Belongs in `ai-act.md`, not here. | playbook EU AI Act | DRAFT |
| 10 | CRA, DSA, EAA, consumer law, NIS2 | **Do not apply** to S1: nothing sold, nobody else's content hosted, no service to consumers. Reopened by a tier raise. | app-context Distribution | DRAFT |
| 11 | Third-party data-source terms (WHOIS/RDAP, HIBP, Intelligence X, DeHashed, Snusbase, LeakCheck, DomainTools) | **Unverified.** Provider terms may limit redistribution of results to third parties; the report is a redistribution. Not read in this pass. Record in `licences.md`. | `providers/exposure_lookup.py`, `providers/email_lookup.py:12` | rule not verified | DRAFT |

## 2. DPIA screening (Art. 35)

Rule not verified; DPC list recalled from the 2018 publication (the PDF was not readable here): re-read
`dataprotection.ie` "List of types of data processing operations which require a DPIA" before relying on
the item numbers.

| Test | Result | Reasoning |
|---|---|---|
| Art. 35(3)(a) systematic, extensive automated evaluation with significant effects | No | No scoring or profiling in S1 (S 56). |
| Art. 35(3)(b) large-scale special-category or criminal data | No on scale; **Art. 9/10 content possible** | Small, operator-driven; but breach-index hits and "scam source" labels can carry Art. 9 inferences and allegations of offences. |
| Art. 35(3)(c) large-scale public monitoring | No | |
| DPC list: personal data **indirectly sourced** where transparency is not met (relying on impossibility/disproportionate effort) | **Yes, fits** | Every data subject is told nothing (Art. 14 is not performed; S has no notice). The operator would rely on Art. 14(5)(b) or a DPA 2018 restriction, which is exactly the list item. |
| DPC list: **combining or cross-referencing datasets** contributing to profiling or behavioural analysis | **Arguably yes** | A case links WHOIS, DNS, handle and breach data about one identifier; S3 adds links and S4 a graph. S1 alone stores, it does not link, but the purpose is to relate. |
| DPC list: tracking behaviour or location | No for S1 (S2's EXIF/GPS reopens it) | |
| WP248 criteria (two or more usually means a DPIA) | Matching/combining datasets; data subjects cannot exercise rights because they are unaware (Art. 14 gap); possibly sensitive data. **Two to three met.** | |

**Result: a "possible yes".** Per the playbook that is a lawyer trigger until a DPIA is done. The DPIA can
be short (one operator, local, no processor, no network). Record in `docs/legal/privacy.md` before Build
stores real-person data: purpose; necessity and proportionality (why a hashed, structured case rather than
the existing per-run files); risks to data subjects (inaccurate namesake, disclosure through the export,
no notice, no retention limit, sensitive inference); measures (the changes in section 7); the residual
risk; who decided. The agent does not decide that the residual risk is acceptable.

## 3. Data map review (S 376-383), row by row

Legend: basis options are the agent's candidates for a lawyer to choose, not a conclusion.

| Row | Lawful basis candidate | Purpose limitation | Minimisation | Retention |
|---|---|---|---|---|
| Case title, purpose note (operator text) | Art. 6(1)(f), legitimate interest, if row 1 fails; the purpose note is the owner's own record of it | The note is optional (S 200) and unverified by design. It is also the only evidence of purpose for Art. 5(1)(b) and a legitimate-interest assessment, so being optional weakens it. The title and note are exported verbatim and can name people. | Free text, no limit on the note; can hold the operator's own details, a victim's details or allegations. | Manual delete only (S 167). Art. 5(1)(e) needs a documented reason and a periodic review, not an expiry. |
| Evidence: parsed source records on third parties | Art. 6(1)(f); Art. 14 notice not given (14(5)(b) or DPA 2018 restriction would need a lawyer's view); Art. 9/10 possible, which Art. 6(1)(f) alone does not cover | Collected for one question about one target, now kept and combined. Reuse in a later case is a new purpose (Art. 5(1)(b), 6(4)); S1 shares nothing across cases (ADR 0005 decision 2), good. | Whole result stored, "nothing dropped" (S 311). Records about unrelated people (hosting-provider staff, other registrants on a shared IP, other crt.sh names) are kept in full. Breach hits are metadata only (verified, section 1 row 6). | As above; the originals in `data/chats/` have **no retention at all** today. |
| Entities (identifiers) | As evidence | Typed or from the run target only; S3 extraction is a later opt-in (S 143). | Person and Organisation are free-text labels; Phone excluded (S 169), good. | As above. |
| Exports | The owner discloses to a recipient: Art. 6(1)(f) for the disclosure, plus a recipient-side basis the owner cannot control; Art. 5(1)(f) once written | Handed to an abuse desk, platform, bank or police: that is the purpose, so it should be named in the export (change 5). Handing to anyone else is a different purpose. | "Selected evidence" is the only minimisation (S 251); identifiers, timeline and purpose note always go (S 251-252). | "Outside Sentinel's control once written" (S 382): true, and Art. 19 notification of erasure to recipients is then impossible unless the operator knows who got what. |

**Rows missing from the data map (each holds the same third-party data or its trace):**
1. **`data/chats/<run>.json`** (Trace result as `response`, `main.py:4683-4716`) and the Saved searches
   view. Same personal data, kept untouched after Delete case (S 167, 259), no retention. Highest-impact omission.
2. **`runs` table in `sentinel.db`**: target in `prompt_summary` (S 21), and `usage_log.json`.
   Both survive Delete case and Emergency Reset only in portable mode.
3. **`summary.json`**: derived cache of title, counts, last activity (S 289), personal if the title is.
4. **`data/cases/.exports-pending.json`** and the `.partial` staging folder at the destination (S 341, 326):
   paths, possibly the title, partial third-party data until cleaned at next start.
5. **`.deleting-<id>` folders** (S 347): a crashed delete leaves the case until the next start.
6. **Backups and sync not under Sentinel's control**: Time Machine and local snapshots, the owner's lab
   backup tooling, Spotlight index of `~/Documents`, macOS Recent Items, the clipboard (Copy hash,
   S 221). The data map says a case "only leaves in an export"; true for Sentinel's own code, not for the OS.
7. **iCloud Drive**. `~/Documents` syncs to Apple if "Desktop & Documents" is on. The default export
   folder is `~/Documents/Sentinel cases/` (S 252). Apple would then hold third-party data: a recipient and a
   transfer question the data map says does not exist. Whether it is on for this Mac was not checked.
8. **Trace's activity-log line** "Added 4 items to case <title>" (S 239): confirm it is not persisted with the
   title (15.6 says the log carries case id and counts only; the 14.7 message does not).
9. **Data subject categories and special-category flags**: not stated. Add "people named in public records
   (registrants, handle owners, breach-index subjects, namesakes) and the operator's own details".
10. **Controller, recipients of exports, and Art. 14 position** are absent.

## 4. What the export hands to third parties

- **Position.** The owner is controller of the case and of the report as written. A recipient (registrar abuse
  desk, platform, bank, police) receives personal data of people who were never told, and becomes an
  independent controller of its copy. If a recipient is outside the EEA the owner's disclosure is a transfer
  question (Art. 44-49); an abuse desk or platform is often US-based. Lawyer trigger (below). Rule not verified.
- **Contents that go out.** `report.md` carries title, purpose note, every entity, the whole timeline, and
  each evidence item's metadata (S 111, 253); `evidence/` carries the parsed source records in full;
  `manifest.json` carries `case_title` and `selected_seqs`. A `selected` export keeps the full identifier list
  and timeline (S 251-252), so the people behind unselected evidence stay named. The `export` event stores
  only the folder name (S 345), so the operator has no record of **who** got which export.
- **Redaction.** None exists. Options that fit the spec without breaking the hash design (evidence files are
  immutable and hashed, so redaction means *leaving items out*, not editing them): (a) a "leave out
  identifiers and timeline of unselected evidence" rule; (b) a tick "omit the purpose note and title";
  (c) a per-identifier "do not export" flag; (d) an export profile for "to a platform abuse desk" that
  includes only the evidence naming the abused asset. Editing a record inside an export breaks `SHA256SUMS`
  and should not be offered.
- **Notices.** The report should open with a fixed handling block: it contains personal data of third
  parties collected from public sources; it is shared for the stated purpose only; the operator's statement of
  purpose is unverified (already S 111); records may be inaccurate or concern a different person with the
  same name or handle; do not onward-disclose; contact route for correction or deletion. The owner's contact is
  the owner's choice and must not be added automatically (identity rule); the dialog should ask. The 14.9
  sentence "Share it only with whoever needs it" (S 253) is a start but sits in the dialog, not in the file
  the recipient reads.
- **Completeness claim.** Section 7.5 says what the hash does not show. It does not say that a case's stored
  copy can be deleted, rewritten as a whole, or that a *selected* export is a subset. A recipient could read
  an intact chain as "complete". Add one sentence (change 12).

## 5. Erasure: Delete case, Emergency Reset and the hash chain

**Tension with tamper-evidence.** The chain is per case (ADR 0006 decision 4) and evidence is not shared
across cases (ADR 0005 decision 2). Deleting a **whole case** therefore removes timeline, evidence and
summary together; nothing remains to break. There is no conflict for S1's whole-case delete. The conflict
appears in three places:

1. **Erasing one person from a case (Art. 17, or the operator's own correction).** Evidence is
   content-addressed and immutable and Verify reports a removed file as "Missing" and breaks the chain from
   that event (ADR 0006 decision 4, M2). The spec has no per-item erasure. The only tool is whole-case
   delete, which destroys the rest of the evidence too. In addition, `entity_added` and the request
   descriptor carry the identifier **in the clear in the timeline** (S 302, ADR 0006 decision 3), so even
   deleting an evidence file would leave the person's email in a log that Verify wants intact. Decide before
   S2/S3, not in S1: either (a) an `erased` tombstone event type that removes evidence bytes and the
   identifying fields and keeps only a salted hash of what was removed, with Verify treating a tombstoned
   item as "Erased by the operator", or (b) keep whole-case delete as the only erasure and say so.
   Do not let this become hand-deleting files, which Verify will read as tampering.
2. **Exports already written** are untouched (S 259 says so). The operator cannot honour Art. 19
   (notify recipients of erasure) without knowing who received what (section 4, change 6). The Delete
   dialog should list the case's earlier exports, with folder names, before it deletes the only record of them.
3. **Originals in `data/chats/`, `runs` and `usage_log.json`** survive (owner Q3). The `run_added` event
   already records the saved chat file name (S 353), so the case knows which originals it was built from.
   The owner decided to leave them (S 167); this consult flags that the delete dialog's reassurance ("Your
   Trace runs in Saved searches are not deleted", S 259) is a statement that the personal data **stays**. An
   unticked option "also delete the saved runs this case was built from" would honour Q3 by default and
   give an erasure route. It needs an owner yes.

**Whole-case delete mechanics (S 347).** Rename then `rmtree`. This is not a secure erase: APFS is
copy-on-write, snapshots and Time Machine keep copies, an SSD does not overwrite in place. The existing
README already says Emergency Reset "is a privacy reset, not a ... forensic wipe" (README ~157-166); the
Delete dialog needs the same honesty and must not use "erased" or "securely deleted". No record of the
deletion survives either (the timeline goes with the case): state that, so an accountability reader is not
surprised.

**Emergency Reset.** `erase_portable_user_data` works **only in portable mode** (`services/portable_reset.py`,
"available only in USB-portable mode") and erases the portable `data/` folder, so cases there go with it.
A normal or frozen install has **no** panic erase; its `data/cases/` lives under
`~/Library/Application Support/Sentinel/` or the checkout. Spec S 129 ("Emergency Reset erases data") and
S 380 ("also erased by Emergency Reset (portable)") are only true for portable; S 87 reads as universal.
The latch behaves as designed: `require_writes_allowed()` guards each write and the commit replace (S 294),
so a late Add to case cannot write into a folder being erased. Gaps: Delete case (S 347) is not stated to call
`require_writes_allowed()`; exports already outside `data/` survive a reset; 15.6 recovery runs at the next
start and would find nothing, which is correct. After a partial erase (`PartialEraseError`) a case folder may
be half present: Verify then reports missing evidence, which is the correct, honest output.

## 6. Retention, access and security points not in the data map

- **Retention.** The owner's choice (manual only, S 167) is allowed to stand; what is missing is a recorded
  reason and a review habit. Suggest showing "last activity" age and a gentle "not touched for N months"
  hint on closed cases (a reminder, never an auto-delete), recorded in `privacy.md`.
- **Access/rectification requests (Art. 15-16).** If a person asks the owner what is held, finding them means
  scanning every `timeline.jsonl` and `data/chats/` by hand; there is no cross-case search (ADR 0005
  consequences). Note a manual procedure in `privacy.md`; S3's derived index is the proper fix.
- **Security (Art. 32).** Case files and exports are plaintext JSON and Markdown. Local disk encryption
  (FileVault) is the owner's. A third-party hand-off by email is the operator's channel. The app already
  contains AES-GCM + scrypt code for site backups (app-context 94); an optional password-protected export
  is a reasonable P2.
- **Records of processing (Art. 30).** Likely a small-scale exemption, but Art. 30(5) excludes
  non-occasional processing and special-category/criminal data. A one-page record in `privacy.md` costs little.

## 7. Numbered changes to the spec or data map

| # | Sev | Change |
|---|---|---|
| 1 | P1 | **Data map 15.12**: add rows for `data/chats/` and Saved searches, `runs.prompt_summary`, `usage_log.json`, `summary.json`, `.exports-pending.json`, `.partial` staging, `.deleting-*`, Trace activity log, OS-level copies (Time Machine, Spotlight, iCloud, clipboard), data-subject categories, special-category flag, controller, recipients, lawful-basis and Art. 14 columns (section 3). |
| 2 | P1 | **Delete case (14.10, CW-8)**: say what stays. List the saved runs and earlier exports the case knows of (`run_added` chat file name, `export` events). Offer an unticked "also delete these saved runs" (owner yes needed against Q3). Never write "erased" or "securely"; add "Copies in backups, Time Machine and exports already handed on are not reached." |
| 3 | P1 | **Export default folder (14.9, S 252)**: stop defaulting to `~/Documents` if iCloud "Desktop & Documents" is on, or detect and warn like the git-repo warning; at minimum add "may sync to iCloud" to the warning list and correct the data map's "only in an export". |
| 4 | P1 | **Selected-evidence export (14.9, 15.10)**: define what a subset removes. Either drop identifiers, events and metadata that name only unselected evidence, or say in the dialog and `report.md` that the identifier list and timeline are always exported. Add the "omit title and purpose note" tick. |
| 5 | P1 | **Report handling block** (section 4): fixed text at the top of `report.md`, constant in `case_export.py`, pinned by the M8 copy test; the purpose of the disclosure field is the operator's (optional "Shared for:" line). |
| 6 | P2 | **Export dialog**: optional "Shared with" free-text (recipient name only), recorded in the `export` event, so Art. 19 and a later deletion decision have a trail. |
| 7 | P1 | **DPIA record** before Build writes real-person data: add a Build precondition "DPIA screening and short DPIA recorded in `docs/legal/privacy.md`" to the S1 backlog (a gate for CW-8 and the first real case, not for coding). |
| 8 | P2 | **Accuracy and sensitivity copy**: in the report block and the identifier view, "records may concern a different person with the same name or handle"; keep "Observed" meaning returned, never true (S 265); a breach hit is shown as "appears in a breach index", never as a claim about the person; no breach-listing text outside metadata. |
| 9 | P2 | **M7-style test for breach data**: a fixture where a source returns passwords, hashes or record contents and the case holds none (extends `exposure_lookup.py:31-37` into the case boundary). |
| 10 | P2 | **Purpose note**: keep it optional but offer a required choice of a short purpose category (own asset, abuse aimed at me, other) so Art. 5(1)(b) has a record. Keep the "not verified" label; never wording that makes a purpose look lawful (S 200, M8). |
| 11 | P2 | **Erasure design for S2/S3 (ADR addendum)**: choose between a tombstone event with salted hash and "whole-case delete only"; identifiers in the clear in the timeline (S 302) are the reason it cannot be improvised later. Record the decision in ADR 0006 or a new ADR. |
| 12 | P2 | **Section 7.5 text**: add one sentence "A case's stored copy can be deleted or replaced as a whole, and an export may be a subset, so an unbroken chain does not show completeness." The text is verbatim and pinned by M8: change spec, constant and test together. |
| 13 | P2 | **Correct spec 9 and 6**: Emergency Reset exists only in portable mode; a normal install has no panic erase, so Delete case is the only erase route there (S 87, 129, 380). |
| 14 | P2 | **Provider terms**: read each source's terms on redistribution of results to third parties (WHOIS/RDAP, HIBP, Intelligence X, DeHashed, Snusbase, LeakCheck, DomainTools) and record in `licences.md` before the export reaches users. Not verified. |
| 15 | P2 | **Retention hint** (section 6): last-activity age on the Cases list; no auto-delete. |
| 16 | P2 | **Optional encrypted export** (password-protected archive), later slice, reusing the existing AES-GCM code. |
| 17 | P3 | **Delete case calls `require_writes_allowed()`** and its 15.6 recovery is logged by id and count only (S 326, 347). |
| 18 | P3 | **Activity-log line** (S 239): confirm it is not persisted with the case title; if it is, use the case id. |
| 19 | P3 | **`privacy.md`** gets a manual procedure for access/rectification requests across `data/cases/` and `data/chats/`, and a one-page Art. 30 record. |
| 20 | P3 | **In-app first-case note**: one neutral line that a case stores personal data about third parties and should hold only what is needed. No wording on lawfulness. |

## 8. LAWYER triggers (ready to paste into `docs/legal/register.md`)

Each is backed by spec evidence; "rule not verified" is stated where the agent did not read the text.

- [ ] LAWYER: Decide whether GDPR Art. 2(2)(c) (household/personal-activity exemption) can cover the owner's case workspace, given that it keeps a structured file on named third parties (`docs/specs/case-workspace.md:61-87`), is used on bounty targets (`:32`) and produces a report handed to a registrar, platform, bank or the police (`:32`, `:109-118`); and, if it cannot, the lawful basis (Art. 6(1)(f)) and the Art. 14 position for people who are never told, including reliance on Art. 14(5)(b) or a DPA 2018 restriction. Rule not verified.
- [ ] LAWYER: DPIA screening is not negative for the case workspace (indirectly sourced data with no notice, and combined datasets; WP248 criteria met two to three times, `docs/audit/case-workspace-legal-compliance.md` section 2). Confirm whether a DPIA is required before real-person data is stored, and review the DPIA once drafted. DPC list items recalled, not re-read.
- [ ] LAWYER: Review special-category and criminal-offence exposure in stored and exported records: breach-index hits that reveal sensitive facts (`providers/exposure_lookup.py:22-45`) and entries labelling a handle or domain as a scam or phishing source (`docs/specs/case-workspace.md:32`); whether Art. 9/10 and DPA 2018 safeguards apply, and the defamation/harassment exposure of a report naming a person (Defamation Act 2009, Non-Fatal Offences Act 1997 s.10, Harassment, Harmful Communications and Related Offences Act 2020: not read).
- [ ] LAWYER: Review the report export as a disclosure to third parties (`docs/specs/case-workspace.md:109-118`, `:359-371`): the owner's position as controller and the recipient's, onward transfer where a recipient is outside the EEA (Art. 44-49), the handling notice and redaction options in `docs/audit/case-workspace-legal-compliance.md` section 4, and erasure duties towards recipients (Art. 17, 19) when the case is deleted (`:258-259`, `:347`). Rule not verified.
- [ ] LAWYER: Decide the erasure model for a hash-chained, append-only case log: whether whole-case delete alone is enough for a data-subject erasure request, or per-item erasure with a tombstone is required (`docs/adr/0006-case-canonical-json-and-hash-chain.md`, identifiers stored in the clear in the timeline). Needed before S2.

(These do not replace the existing register trigger on sending OSINT results to cloud model providers; S2's
`model_output` and the dossier re-persist that data in a case and should be read with it.)

## 9. What could not be checked

- The text of GDPR articles, the DPA 2018 sections and the DPC's Art. 35(4) list was not re-read (site 403,
  PDF unreadable); cited from knowledge and marked "rule not verified". Item numbers of the DPC list are not
  quoted for that reason.
- Whether iCloud Drive "Desktop & Documents" is on for the owner's Mac; whether Time Machine or
  `backup_manager` covers `data/cases/`.
- Provider terms on redistribution (section 1, row 11).
- No code of S1 exists yet (no Build); every point on `case_store`, `case_export` and `cases.py` is about the
  design in sections 14 and 15 of the spec, not a running feature.
