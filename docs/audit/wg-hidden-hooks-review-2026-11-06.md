# Review: #32 (`fix/wg-hidden-hooks`, `bc67826`), 2026-11-06

Independent read-only review (code-reviewer, Opus). Playbook `code-reviewer.md` last
reviewed 2026-10-14, current. Saved by the session that fixed the findings; the status
of each is at the end.

**Verdict at review time: NOT READY.** The `#` forms and the bare-key form were fixed
correctly, but a NUL byte still hid a WireGuard hook that wg-quick runs as root.

## Evidence base
- wg-quick `darwin.bash`/`linux.bash` (git.zx2c4.com): lines 60-79 of `darwin.bash` as
  in the brief (`stripped`, trimmed key, hooks use `unstripped_value`, `nocasematch`);
  hooks expand `%i` -> `$REAL_INTERFACE` (utunN) and `%I` -> `$INTERFACE` (lines
  412-418); wg-quick refuses bash < 4 (line 42).
- bash `builtins/read.def` (master) lines 837-838: `if (c == '\0' && delim != '\0')
  continue; /* skip NUL bytes in input */`.
- `osascript -e 'do shell script "locale"'`: `LC_CTYPE="C"`, so `[[:space:]]` and
  `nocasematch` are ASCII-only in the privileged shell (non-admin run; assumed the same
  with administrator privileges).
- Tests in a `git archive` copy: 154 passed. The real tree was not touched.

## P0-1: a NUL byte inside the key hides a root hook completely
`_scan` (WireGuard) kept NULs; bash drops them. `Post\0Up = /tmp/evil` gave
`allowed=True`, `root_hooks=()`, `blockers=()`; `Save\0Config = true` likewise bypassed
the SaveConfig rule; Disconnect the same. Not run on a real bash 5 / wg-quick (not
installed; the system bash 3.2 truncates at NUL, but wg-quick refuses 3.2).

## P1-1: a NUL before the command word skips the program check
`PostUp = \0/tmp/evil` was listed, but `_path_word` saw no leading `/` and the
bare-name rule did not match: allowed, while bash runs `/tmp/evil` as root.

## P2-1: the link walk misses a folder a link target enters and leaves by `..`
`S/tool -> "../W/b/../../R/x"` with `W` writable: the checked chain had no `W`.

## P2-2: `%i` / `%I` in a hook path are checked literally
`PostUp = /opt/vpn/%i/up.sh`: only a "does not exist" warning; wg-quick runs
`/opt/vpn/utun5/up.sh`, which can sit in a user-writable folder. Pre-existing.

## P3-1: new fail-closed branches untested
Unreadable link (`OSError` -> refusal) and `ValueError` from `stat` (unreachable:
`realpath` raises first). Mutants M4 and M5 survived.

## P3-2: no test reaches a link only after a `..`
Mutant M1 (ignore `..`) survived.

## P3-3: `docs/agents/vpn.md` said a link is not followed

## P3-4: TODO and CHANGELOG claimed the class closed (untrue for NUL)

## Checked and fine (summary)
`#` forms, bare `PreUp`, CR/tab trimming, empty `PostUp =`, duplicate hooks, hooks in
`[Peer]` (conservatively checked), `;`/`#` lines, `Table # = off`, `SaveConfig # =
true`, `Address … # note`, BOM; no false positive on a normal config; link walk
handles relative/absolute targets, `..` after a link, `.`, trailing and doubled
slashes, final-component links and links to folders; it terminates; the 40-link cap is
above macOS's 32.

## Mutants at review time
M1 `..` ignored: survived (P3-2). M2, M3, M6: killed. M4 unreadable link fails open:
survived (P3-1). M5 ValueError from stat skipped: survived (unreachable). M7 / M8 (drop
final `realpath` / walk result): survived, equivalent in practice.

## Status after the fixes (`fix/wg-hidden-hooks`)
- P0-1, P1-1: fixed. WireGuard text is scanned with NULs removed (as bash reads it),
  so the hook is listed and checked; any config containing a NUL is refused (a risk on
  Disconnect). OpenVPN profiles with a NUL are refused too.
- P2-1: fixed in `73a4ab4` (every folder passed is checked).
- P2-2: fixed. A hook path word containing `%i` or `%I` is refused with a request to
  write the path out; `%i` as an argument is not affected.
- P3-1: unreadable-link test added in `73a4ab4`; the unreachable `ValueError` branch
  removed.
- P3-2: fixed (test: `S/p -> "../T/l"`, `T` writable).
- P3-3: fixed in `73a4ab4`. P3-4: TODO and CHANGELOG corrected.
- Mutants for these fixes: no NUL strip, no NUL blocker, no placeholder check, `..`
  ignored: 4/4 killed.
