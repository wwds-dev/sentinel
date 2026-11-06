# Review: #30 (SaveConfig comment rule, link walk in a hook path), 2026-11-05

Independent read-only review (code-reviewer, Opus) of `75787a7` on `main`. Saved by
the session that fixed it; the fixes are on `fix/wg-hidden-hooks` (see the end).

**Verdict at review time: #30's own changes correct; the hook review NOT READY**
because of a pre-existing P0 in the same comment-parsing class.

Evidence base: wg-quick's parser (`wireguard-tools` master, `src/wg-quick/darwin.bash`
lines 60-84), run under `/bin/bash` in a simulator. wg-quick is not installed on the
dev Mac.

```
stripped="${line%%\#*}"
key="${stripped%%=*}"            (trimmed)
value="${stripped#*=}"           (SaveConfig uses this)
unstripped_value="${line#*=}"    (PreUp/PostUp/PreDown/PostDown use THIS)
```
Matching is case-insensitive (`nocasematch`).

## P0: a hook hidden behind `#` before `=` runs as root, invisible to the review
`services/vpn_execution.py` `_scan` (WireGuard branch) took the key from everything
before the first `=` of the raw line; wg-quick takes the key from the comment-stripped
line but the hook command from after the first `=` of the raw line.
`PostUp # = /tmp/evil.sh arg`: wg-quick runs `/tmp/evil.sh arg` as root; Sentinel read
the key `postup #`, listed no hook and raised no blocker. Second form: a hook key with
no `=` (`PreUp`) makes wg-quick eval the key text itself, found through PATH (which
ends with wg-quick's user-writable Homebrew folder); Sentinel skipped it.

## P2: `_paths_touched` loses links before a `..`
`normpath` collapsed `x/..` as text, but the kernel follows link `x` first. Reproduced:
`S/prog -> "x/../y"`, `S/x -> W/hop` (in writable `W`), `W/hop -> R2/d`: the old code
returned no reason, though a user could re-point `W/hop` after review.

## P2: tests do not cover multi-hop or relative links
Mutants that survived: resolve relative targets from the cwd; stop after one link;
never follow a pending path. Both link tests used absolute links and one hop.

## P3: a NUL byte in a hook path raises ValueError
`PostUp = /bin/ech\x00o hi` made `_hook_findings` raise from `os.path.realpath` /
`os.stat` instead of refusing.

## Checked and fine
SaveConfig cut at the first `#`, trimming, case-insensitivity; `;` is not a comment for
wg-quick (it then fails closed); hooks keep `#` (unstripped value); link loops end; the
40-link cap; long and odd paths do not crash. Known residual: bare OpenVPN hook
commands are not resolved through PATH (pre-existing).

## Fixes (`fix/wg-hidden-hooks`)
- P0: `_scan` reads WireGuard lines as wg-quick does (key from the line cut at `#`,
  hook value after the first `=` of the uncut line, or the whole line with no `=`); a
  hook line with no `=` before its first `#` is a blocker on Connect and a risk on
  Disconnect, and the hook is listed in the review.
- P2: `_paths_touched` resolves one component at a time, each link before any `..`
  after it, adding every link and its folders.
- P2: tests for a three-link chain, a relative link (cwd moved to `/`), the `..` case,
  a link loop.
- P3: a NUL byte (ValueError) or an unreadable link is reported as unsafe.
- Mutants: 6/6 killed (old scan, no blocker, one link only, cwd-relative resolve, no
  ValueError handling, link not added); 7 new tests fail on `main`'s code.
