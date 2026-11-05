# Independent review of 5a81ab9 (MAC changer review-1 P3-2: interface read off the UI thread)

Reviewer: Opus (read-only, `code-reviewer`), 2026-10-29. Verdict: **ready to merge**; no P0/P1.
Summary written from the reviewer's hand-back; the fixes are in the follow-up commit.

| # | Finding | Status |
|---|---|---|
| P2-1 | After a read, `release()` switched every button on (Tor Stop, New identity, Test chain were on when they should be off). Mutant: `release()` not re-enabling survived. | Fixed: `GatedTab._controls_changed` hook recomputes MAC, Tor and chain buttons; tests for declined and failed reads. |
| P3-1 | Quitting during a read-only listing claimed an administrator action was running. | Fixed: `_read_only` flag, `has_privileged_work` ignores a read; a running change still counts. |
| P3-2 | The `networksetup` error text was neither redacted nor length-pinned (mutant: no `[:200]` survived). | Fixed: `redact_addresses` plus `[:200]`; test. |
| P3-3 | The interface picker stayed usable during the read. | Fixed: picker is in `_set_enabled`. |
| P3-4 | `refresh_interfaces` still runs on the UI thread; `set_mac`/`restore` report a failed listing as "No such interface". | Open, outside this commit's scope. |

Checked and holding: no `QThread.terminate`; busy flag released on done and failed; `release()` runs before `then` (a swapped order is caught); the confirm is informational only because `set_mac` re-reads and records at run time; a failed listing is never reported as "gone"; `list_interfaces` unchanged for existing callers; no address in logs or stderr. 11 reviewer mutants, 9 caught; the 2 survivors are the P2-1 and P3-2 items above.
