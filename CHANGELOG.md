# Changelog

All notable changes to Sentinel, written for the person using it. The format
follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), adapted
to Sentinel's version numbers.

Sentinel does **not** use semantic versioning. A release is `vMAJOR.SEQUENCE`
(for example `v2.002`): `MAJOR` changes only for a deliberately incompatible
product generation, and `SEQUENCE` counts releases within it. The `VERSION` file
is the source of truth; see [`docs/versioning.md`](docs/versioning.md). Newest
first. This file starts at v2.002; earlier history is in `docs/versioning.md`
and the git log, and is not back-filled here.

## Unreleased

### Changed
- **Stop in Chat returns at once.** It used to freeze the window for up to two
  seconds. A model request cannot be interrupted part-way, so the model may still
  finish in the background; Sentinel never shows that reply, and the notice now
  says so: "Chat request stopped. The model may still finish in the background."
  For a cloud provider it adds that the provider may bill for it.
- **A stopped cloud request's cost is now recorded.** If its reply arrives while
  Sentinel is open, Cost history gets one row for it as a cancelled run
  (`cancelled-exact`, or `cancelled-estimated` when you stopped part-way through
  a reply and the provider's token counts never arrived), and the cancelled Run
  log row shows that cost. A local Ollama model may stay busy after Stop, so your
  next local request can wait behind it.
- **Quit is bounded.** With work running, quitting takes up to about 2.5
  seconds; whatever is still running then is abandoned and the app exits. A
  cloud request abandoned by Quit may still be billed by the provider without a
  record in Sentinel. Quitting during an administrator action (for example a
  Tunnel Connect) still asks **Wait** or **Quit anyway** first.
- **Emergency Reset (portable mode) stops and fences background work** before
  it erases, so a request or check that finishes late writes nothing into the
  folder being erased.

### Fixed
- **A WireGuard hook must now be plain commands with full paths.** wg-quick runs
  each hook through the shell as administrator, and the shell expands what the
  review read literally: `echo $(/tmp/x.sh)`, `/tm"p"/x.sh`, `/tm?/x.sh`,
  `. /tmp/x.sh`, `sh</tmp/x.sh` and similar ran a program nobody checked. A hook
  with shell syntax (`$`, a backtick, `\`, quotes, `* ? [ ]`, `{ }`, `< >`,
  `( )`, `!`, `~`, a pipe), a variable assignment, a shell keyword or builtin
  (`.`, `source`, `eval`, `exec`, `if`, `echo`…), a relative path, or a bare
  program name that is not in `/usr/bin`, `/bin`, `/usr/sbin` or `/sbin` now
  stops a Connect with "Tunnel cannot check what this hook runs; write plain
  commands with full paths." (on Disconnect it is a risk you confirm). Commands
  separated by `;`, `&&`, `||` or `&` are still allowed, and each one is checked.
  Only the network programs `route`, `ifconfig`, `networksetup`, `pfctl`,
  `scutil`, `killall`, `dscacheutil`, `wg`, `sysctl`, `ipconfig` and `logger` may
  be given ordinary words as arguments. Any other program (a shell, `xcrun`,
  `dtrace`, your own script…) may only be given options, numbers, full paths
  (checked like the program itself) and `%i`/`%I`, so `sh -c id`,
  `xcrun tool` or `SH -c tool` is refused. Program names are matched in any
  case, since macOS treats `/bin/SH` as `/bin/sh`.
- **The review's DNS line now matches what wg-quick sets.** `DNS # = 10.64.0.1`
  sets no DNS server (wg-quick cuts the line at `#` before it reads the key), but
  the review showed "DNS: 10.64.0.1". It now says system DNS stays in use and
  warns about the line; a comment after a value is no longer read as part of it.
  A name such as `DNS = corp.example` is a search domain to wg-quick, not a
  server, and is now shown as one; entries separated by spaces are split.
- **The hook review can no longer pause the window or skip a folder it cannot
  read.** A config packed with hook commands took about four seconds to review;
  more than 200 commands and paths in a config's hooks is now refused at once,
  and a hook gets one refusal in the dialog, not one per command. A folder on a
  hook program's path that cannot be read (permission denied) now makes the
  program unsafe instead of being skipped. OpenVPN scripts handed to `/bin/SH`,
  `perl5.34` and similar are now checked like those handed to `sh` and `perl`.
- **A WireGuard hook hidden from the review is now shown and refused.** wg-quick
  runs `PostUp # = command`, a bare `PreUp` line, and a line with a NUL byte in it
  (`Post\0Up = …`) as administrator, but the review read them as comments or
  skipped them and listed nothing. They are now listed and stop a Connect (on
  Disconnect they are a risk you confirm); any config containing a NUL byte is
  refused. A hook path using wg-quick's `%i` or `%I` is refused, since the program
  that runs is only known once the tunnel is up. A hook program reached through
  links is followed one step at a time, as the system does, checking every folder
  on the way; more than 40 links, an unreadable link, a path with a NUL byte or a
  path too long for the system is refused or skipped instead of stopping the
  review with an error, and a hook line over 4000 characters is refused without
  its paths being walked.
- **Hook review sees through two more tricks.** A WireGuard `SaveConfig = true`
  followed by a `# comment` is now refused like a plain one, and a hook program
  reached through a link (or a chain of links) is judged by where it really
  lives, not only by the folder the link sits in.
- Stop and Quit could, rarely, freeze the whole app (a blank window that ignored
  Close) or make it crash on exit. Sentinel no longer force-stops its background
  threads; it lets them finish on their own and stops listening to them.
- Tunnel's health checks now really run: every 30 seconds while the Tunnel panel
  is on screen, using only this Mac's own state. Before, they were scheduled but
  never ran. You may now see "TUNNEL DROPPED" or "DNS resolvers changed" warnings
  in the Tunnel status line that you have not seen before.
- An Emergency Reset that fails part-way now says that some data may already be
  erased. Check the `Sentinel Data` folder before using it again.
- **Tunnel › Privacy: Test chain tests exactly the hops you confirmed.** Before,
  adding, removing or reordering a hop while a test ran changed the route it
  used. The hop buttons and the chain mode are now off while any action runs.
- A saved proxy chain that Sentinel cannot read no longer stops the Tunnel panel
  from opening, and is no longer overwritten by your next edit. It is moved to
  `proxychain.json.unreadable` (then `-2`, `-3`, …) in the Tunnel data folder,
  made readable only by you where possible, and the Privacy tab says so under
  the chain until you edit it, on later launches too. A file that cannot even
  be moved, or one past the 1,000th copy, is left in place, and the tab warns
  that your next edit replaces it.
- Changing the chain mode no longer clears the selected hop, and Test chain is
  no longer switched on, or left on, against an empty or invalid chain.
- **Tunnel › Privacy opens even when Sentinel cannot read its record of changed
  hardware addresses** (for example when the Tunnel data folder's permissions
  were changed). Before, the tab was not built. It now says so in the Hardware
  address box and refuses to change an address until the record can be read,
  since that record may still hold an original address to put back; Restore
  hardware address still works. Emergency Reset's warning says the same.
- **Hardware addresses at quit and at launch.** When Sentinel quits while a
  change to an address is still waiting for your administrator password, it now
  says which interface it left alone, instead of skipping it silently; the change
  stays recorded and is offered again at the next launch. If Sentinel cannot
  read this Mac's boot ID, it no longer offers to put back a recorded address,
  which might date from before a restart; the record is kept for later.
- A Tunnel confirmation dialog now holds the Privacy tab while it is open, so
  nothing else in the tab can start behind it.
- Hardware addresses are now hidden from the launch log in every common form
  (dashes, dots, bare digits, unpadded), not only the colon form.

## v2.002 - 2026-09-29

### Added
- More live OSINT sources: Shodan InternetDB, IPinfo and Criminal IP for IP
  addresses; CourtListener court records; DeHashed breach exposure.
- A native Tunnel "Your IP & DNS" readout with a real bash.ws DNS-leak test.
- An addy.io burner-alias helper in the OSINT Keys tab.
