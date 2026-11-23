# Review: testing readiness fixes G1-G3 (2026-11-23)

Spec slug `testing-readiness-fixes`. Fixes for `docs/audit/testing-readiness-2026-11-23.md` §1/§9 G1, G2 (H3), G3 (H7).
Author: one Claude session; review: a separate read-only `code-reviewer` agent on the uncommitted diff.

## What changed

- **G1** `tests/test_bug_spray_layout.py`: the feed's `showEvent` re-reads saved data and cleared the rows the
  test had added, so on a checkout with no Bug Spray data the tree was empty (`sizeHintForRow(0) == -1`). The test
  now pins `config.load` and `read_feed` with 40 fake rows and asserts they loaded. The whole-rows and
  fills-the-tab checks are unchanged.
- **G2** `main.py` `cloud_allowed` / `cloud_refusal_text`; `authorize_request` refuses a cloud provider before any
  consent dialog when the mode allows no cloud (Local only, Privacy first, or a mode that cannot be read);
  `ui/panel_routing.py` `advise` stops a cloud job when the advisor raises in such a mode (a local job still runs);
  `_send_chat` refuses a cloud provider under Privacy first (review P1).
- **G3** `agents/vpn_agent/services/wireguard_manager.py` `list_active_tunnel_names` reads wg-quick's
  `<name>.name` records (a stale record whose utun socket is gone counts as down); `ui/panels/vpn.py`
  `_local_tunnels` adds those names, so the health monitor compares the profile name with names.

## Findings and what was done

| Sev | Finding | Disposition |
|---|---|---|
| P0 | none | |
| P1 | Chat under Privacy first still sent to a ticked cloud provider (`_send_chat` never checked the mode; the advisor only drops cloud *suggestions*) | **Fixed**: refusal in `_send_chat`; test `test_chat_lifecycle.py::test_privacy_first_blocks_a_ticked_cloud_provider_in_chat` |
| P2 | H7 fix rests on `/var/run/wireguard` being listable by the user; wg-quick may leave it 0700, and an unreadable `.name` record left by a crash counts as up | **Open, owner check O11** (live, not runnable here). CHANGELOG says so; test added for an unreadable folder (returns `[]`, no crash) |
| P2 | "Privacy first" now refuses every cloud model (as `allows_cloud` and README.md:103 say), while the menu label says "local where possible" | **Owner call**, TODO item added; overlaps the H4 default-mode decision |
| P3 | `list_active_tunnel_names` repeats `vpn_execution.wireguard_interface_state` and does not validate the device name like `killswitch.py` | TODO item added |
| P3 | A one-off listing error returns `[]`, which reads as a drop | TODO item added |

The reviewer also confirmed: no other panel route (failover redo, held-job resume, `_advice_cleared`) gets past
`authorize_request`; Hybrid with a broken advisor still reaches the consent dialog; other callers of
`list_active_tunnels` are unchanged.

## Verify

- Mutation check: 12 mutants, 12 killed (G1 2, G2 6 + Chat 1, G3 3). Script and output in the session scratchpad;
  mutants: each new branch removed or inverted, Privacy first ignored, fail-closed turned fail-open, stale and
  unreadable record handling changed, name records not added to the monitor's source.
- `tests/test_request_guard.py`: its shared window starts in whatever mode the machine gives it (Local only here,
  see H4), so the fixture now pins "Local, advise per job"; those tests are about the guard after the mode check.
- Full suite: see the PR description for the run on the final commit.
- Not verified: anything live (WireGuard on a real Mac, O11).
