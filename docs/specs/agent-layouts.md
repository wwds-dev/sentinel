# Spec: agent screen layouts (GUI review 2026-11-22)

Needs: define, design, build, review, verify, docs
Status: approved by the owner 2026-11-23 ("Approve, fold pickers"); the open question is answered: "Change model" folds the pickers into a menu. The visuals were
approved on 2026-11-23 ("visuals are approved"; `TODO.md`, row "GUI review of all eight agents").
This spec writes those mockups down, since they exist only in chat.

Phases: Define, Design (this file), Architect (short, below; no ADR: no new state, threads or
boundaries), Plan, Build one screen at a time. Secure is not required (no network, secrets or
privileged code changes); Comply is not required for a layout change on a `public` app.

## Problem
Every agent screen is a long scroll of equally weighted `QGroupBox`es. Optional extras are always
open, the run bar sits in a different place on each agent, and result metrics are tall, mostly
empty cards in a narrow right column (Bloodhound: four cards beside the dossier; Bug Spray: three).
Nothing on screen says what to do first. Direction: `docs/gui_redesign.md`, `TODO.md` Detail 7.

## Rules for every screen
- **Regroup, never remove.** Every control, label, tooltip, signal connection and attribute name
  that exists today stays reachable and keeps working. Tests and other code reach widgets by
  attribute (`self.investigate_btn`, `self.threat_label`, …): those names stay.
- **Honesty text is not weakened.** Warnings may get shorter (a badge plus one line) but must keep
  every fact they state today. Where the short form drops words, the full text goes into the
  tooltip of the badge or line.
- **One primary button per screen**, styled `PrimaryAction`; everything else is secondary.
- **One run-bar position:** the bottom of the input card, cost on the left, actions on the right.
- **No live VPN, kill-switch, MAC or Nmap action** in any test or render: mocks only.
- Fits the centre column at 1600x1000 without horizontal clipping
  (`test_bloodhound_fits_the_centre_column` is the pattern) and does not raise any panel's
  minimum width.

## Shared pieces (built with Bloodhound, reused after)
1. **`MetricStrip`** (`ui/widgets.py`): one horizontal row of compact cells, each a caption
   (11px, muted) over a value (15px, 500). A cell may carry a thin `Bar` (Threat). Cells wrap on a
   narrow window rather than setting a minimum width. Optional one-line note under the strip.
2. **Disclosure rows:** the existing `ProgressiveSection` (`ui/widgets.py`) with a right-aligned
   status summary, collapsed by default. No new widget unless the right-aligned status needs one.
3. **Run bar** (`AgentPanel.build_run_bar`, `ui/panels/base.py`) gains:
   - a cost line on the left: from `pricing_metadata(provider, model)`
     (`services/model_recommendations.py`). Local models read "Local · no charge". A cloud model
     with no known price reads "Price unknown", never "free" (zero price means unknown).
   - **"Change model"**: a secondary button that opens the provider and model pickers and
     Auto-route as a menu or popover, instead of three inline controls. The provider and model
     combos stay the same objects (`self.provider_box`, `self.model_box`) so routing, the model
     loader and the recommendation system keep working.
   - the primary button (and Stop, shown only while running) on the right.
   The change is opt-in per panel (a keyword argument) so the other agents keep today's bar until
   their own step.
4. **Muted placeholders:** placeholder text uses the muted caption colour from `ui/style.py`, not
   the accent green, app-wide (one stylesheet rule).

## Screen 1: Bloodhound (`ui/panels/osint_heavy.py`)
Layout, top to bottom:
- **Brief card** (the only card above the results): Target full width; Target type and Scope side
  by side; Objective.
- Three collapsed disclosure rows inside the card, each with its status on the right, each
  expanding in place to today's controls:
  - "Target image" — status "None", or the file name once chosen. Contains Browse, Clear image,
    the EXIF-to-model checkbox (still default off, same tooltip), EXIF display, image details.
  - "File discovery" — status "Off", or "N locations" once folders or a remote host are set.
    Contains everything `_build_file_discovery` builds today.
  - "Court records" — status "Organisations only". Contains the CourtListener checkbox (still
    default off).
- **Run bar** at the bottom of the card: cost on the left; "Change model" and the single primary
  "Investigate" on the right (Stop replaces nothing; it appears beside Investigate while running).
- **Results:** a header row "Dossier" with "Save report" and "Clear" on the right; under it a
  `MetricStrip` with Threat (AI est.), Confidence (AI est.), Sources contacted, Depth; then the
  dossier (`stream_box` / `SectionView`) at full width. The right-hand indicator column and its
  splitter go away.
- The sentence "Threat and Confidence are the AI's own estimate, not a measurement. Sources counts
  the public sources actually contacted by live collection." stays as the line under the strip.
- `status_label` stays under the dossier.

Success measure (tests in `tests/test_bloodhound_layout.py`, plus the existing Bloodhound tests
unchanged and green):
- The three disclosures are collapsed on open and show the statuses above; choosing an image or a
  folder updates the status; expanding shows the same widgets as before (by attribute).
- Exactly one `PrimaryAction` button is visible on the panel.
- `threat_label`, `conf_label`, `sources_label`, `depth_label` are inside the strip, which sits
  above the dossier; no `QSplitter` remains in the results.
- The AI-estimate sentence is present verbatim.
- The cost line never shows "free" for a cloud model with a zero or missing price.
- Fits 1600x1000 without clipping; minimum width not above today's 694 px.
- Before/after off-screen renders in `docs/training/` style, isolated database (pattern:
  `scripts/capture_training_screenshots.py`).

## Screen 2: Bug Spray (`ui/panels/bug_bounty.py`, `ui/panels/bug_spray_feed.py`)
- **Target bar** (always visible, top): the target, then program · scope type · severity as one
  summary line, a badge "Scope declared, not verified", and "Edit". Edit expands the current
  Target & Program fields in place (same widgets). Empty target: the bar opens expanded.
- Three step tabs under it:
  - "1 Radar": the program feed (`BugSprayFeed`), unchanged inside. Picking a program still fills
    the Program field.
  - "2 Recon": the Nmap command row, Run and Kill, the note, the output.
  - "3 Report": findings input on the left with the run bar and the single primary "Analyse";
    Severity, CVSS and Bounty estimate as a three-cell `MetricStrip` on the right; the draft report
    below with "Save report" and "Clear" in its header.
- The scope note and the Nmap note become a badge plus one line each. The short Nmap line must
  still say it runs on this machine without a scope check, outside the budget guard, and asks for
  confirmation and a Program; full current text in the tooltip.
- "Run Nmap" is the primary button only on the Recon tab; "Analyse" on the Report tab. One visible
  primary per tab.

- Defect to fix in the same step (added 2026-11-23 at the owner's request, relayed from the GUI
  review session; recorded in `TODO.md` "Two small defects from the 2026-11-22 GUI review"): the
  Recent changes table in `ui/panels/bug_spray_feed.py` shows six full rows and a seventh cut in
  half at 1600x1000. Its height must land on a whole row.

Success measure: the Recent changes table's visible height is a whole number of rows (header plus
N rows, a test computes it); tests show the target bar summary follows the fields; each tab holds the widgets
listed; the scope and Nmap facts above are present (pinned by substring); the existing Nmap
confirmation and Program-required tests stay green; one visible `PrimaryAction` per tab; render.

## Screen 3: Tunnel (`ui/panels/vpn.py`, `vpn_servers.py`, `vpn_privacy.py`, `vpn_apps_tab.py`)
- **Status card** (top): connection state with a status dot, the server picker, and one primary
  "Connect" (Disconnect replaces it while connected); Import config stays beside the picker.
  Under it four compact cells: Kill switch (state plus Arm / Disarm / Verify), Local IP (Refresh),
  Public IP (Check), DNS leak (Run test). The amber safety banner becomes one short line inside the
  card; its full text (including "traffic protection is not verified", the administrator password,
  templates not connecting, Diagnostics and Action Preview read-only) moves to its tooltip, and
  the short line keeps "traffic protection is not verified".
- **Five tabs** below: Diagnose (Connection check, Safe action preview, Config inspection,
  Execution), Servers (Deployment form and the servers tab), Privacy, VPN apps, Advisor (advisor
  input with its run bar, Config builder, Config & commands).
- Every control on today's screen maps to one place in this layout; a test lists them.

Success measure: a test walks every button and input that exists today (by attribute) and finds
it reachable in the new layout; Connect and Disconnect swap with a mocked connection state; the
kill-switch, DNS-leak and fail-closed tests stay green unchanged; the banner facts are present;
no live network or privileged call is made (mocks); render.

## Screen 4: lighter changes
- **Beacon** (`ui/panels/wifi.py`): preflight collapsed until a run; metrics as a `MetricStrip`.
- **Trace** (`ui/panels/osint.py`): Live Research and Exposure Check move into a menu on the run
  bar; Activity shows only while a run is going.
- **Sentry** (`ui/panels/sentry.py`): intro shortened to one line (full text as tooltip); baseline
  and background watch in one row.
- **Forge** (`ui/panels/manager.py`): remove the blank space in its run-bar box; Spec and Log show
  only when they have content.
Success measure per agent: a test for each change above, existing tests green, a render.

## Architect notes
UI-only: no new state, threads, storage or boundaries. No widget is created or destroyed at run
time beyond today's; disclosures toggle visibility. No call on the UI thread may fetch (the cost
line reads cached pricing only). Qt rules in the workspace `AGENTS.md` apply.

## Plan
Build order: shared pieces + Bloodhound → owner looks at the render → Bug Spray → Tunnel →
Beacon, Trace, Sentry, Forge. Each screen is its own commit set on `claude/agent-layouts`, with
tests alongside, the full suite green (`.venv/bin/python -m pytest -q`), a before/after render,
and a `CHANGELOG.md` line. The owner sees each render before the next screen starts.

## Out of scope
New colours, a command palette, animation, the Chat panel, changing what any agent does.

## Open question for the owner
"Change model" folds the provider and model pickers into a menu. If you would rather keep the two
pickers visible on the run bar, say so at approval; the rest of the spec is unaffected.
