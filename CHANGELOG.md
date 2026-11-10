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

### Security
- **WireGuard: administrator rights run a checked copy, never your file.**
  Connect and Disconnect used to check your `.conf` when the password prompt
  opened and then let `wg-quick` read the file itself as root, so anything that
  changed the file in between chose what ran as administrator. Now Sentinel
  copies the file into a root-only folder, checks the copy against the hash you
  confirmed, and runs only the copy; a changed file is refused and nothing runs.
  While the tunnel is up, Disconnect uses the copy you connected with, and its
  confirmation says so. Root also starts with an empty environment now. What the
  copy does **not** protect: the `wg-quick` program and its neighbours, which on
  Apple silicon live in Homebrew's folder that your account owns; Tunnel warns when
  that is the case.
- **OpenVPN: administrator rights run checked copies of your profile and its
  files.** Connect used to check your `.ovpn` when the password prompt opened
  and then let `openvpn` read it, and the certificates, keys and password files
  it names, as root. Now Sentinel copies the profile and every file it names
  beside it into a root-only folder, checks each copy against the hash the
  review read, and starts `openvpn` on the copies in that folder; a changed file
  is refused and nothing runs. A file named by absolute path is accepted only if
  root owns it and every folder above it, and is checked in place just before
  start; one only root can read (a key in `/etc/openvpn`) is checked by
  ownership instead. The review lists each file (not its hash). Hook programs
  must be named by absolute path, owned by root all the way up, and not be a
  macOS system program, an interpreter or a wrapper, and a hook script's `#!`
  interpreter must be root-owned too; root checks the ownership of all of them
  again just before start. A profile line of 250 bytes or more is refused
  (OpenVPN would split it and could read hidden directives), and so is
  `show-pkcs11-ids`. Server profiles, `daemon`, `syslog` and directives that make
  root write files (`genkey`, `ifconfig-pool-persist`, `tls-export-cert`,
  `client-config-dir`, `auth-gen-token-secret`) are refused, `dev` must be a
  tun, tap or utun device and `dev-node` a utun device. **Behaviour change:** a
  relative file name in a profile now means the file beside the profile (it
  used to mean a file in `/`, so such profiles may start working); a hook that
  names a plugin by bare name, or runs `/bin/sh` or another system program, is
  now refused: name your own root-owned script or plugin by absolute path. A
  `capath` folder, or `crl-verify … dir`, is refused: put the certificates in
  one file or inline.
- **OpenVPN's pid, device and start-up log live in a root-only folder.**
  Anything running as you could edit the pid file in Sentinel's data folder
  and point Disconnect's `kill` at another process. Now root writes them under
  `/private/var/run/sentinel-vpn/ovpn/`, Sentinel only reads them and believes
  them only if root owns them, and root checks the process is Sentinel's
  openvpn again before it signals it. The tunnel device is taken only from a
  whole log line, so a server cannot name another device through a certificate
  or a pushed option. A tunnel an older Sentinel started is still found and
  stopped; its leftover files in your data folder are deleted by Connect or
  Disconnect once that process has gone. One OpenVPN Connect or Disconnect runs
  at a time.
- **A hook path with a hidden macOS prefix is refused, for WireGuard too.** A
  path starting with `/.` (`/.vol/…`, `/.nofollow/…`) can reach any file under
  another name, so a WireGuard or OpenVPN hook naming one is refused.
- **The administrator dialog gets nothing of Sentinel's environment.** macOS
  runs the approved command through a shell that read settings such as
  `SHELLOPTS` and `PS4` from Sentinel's environment, which another program
  running as you can set, and could run code as administrator before the
  command started. The dialog now gets only a fixed `PATH`. This covers every
  administrator action: VPN, kill switch and MAC address.
- **The `wg-quick` you reviewed is the one that runs.** If a different
  `wg-quick` appears first on the search path after you confirm, nothing runs
  and you are asked to review again.
- **A WireGuard file Tunnel cannot read as text no longer reaches root as
  "checked".** Disconnect then runs only the copy kept since Connect, or
  refuses.
- **One Connect or Disconnect per tunnel at a time.** A second one waits for the
  first, then reports that the tunnel is already up, instead of removing the copy
  the running tunnel needs to disconnect.

### Fixed
- **A tunnel record left behind by a crash no longer blocks WireGuard.** wg-quick's
  record without its tunnel used to make Connect say "already up" and kept the
  copy holding your private key; now Connect goes ahead and Disconnect removes
  the copy.
- **A WireGuard profile whose file is not named `<interface>.conf` is refused in
  the review**, including a bare name such as `wgnl`. It used to pass the review
  and fail after the dialog, every time.
- **Disconnect says what will run.** With no copy kept since Connect and a file
  that is missing or cannot be read, the confirmation now says nothing runs,
  instead of promising to check the file; for a relative or `~` path it says the
  file is checked and run.
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
  be given ordinary words, numbers or `%i`/`%I` as arguments (the macOS tools
  only at their own system paths). Any other program (a shell, `xcrun`,
  `dtrace`, your own script…) may only be given options and full paths
  (checked like the program itself), so `sh -c id`, `sh 1`, `xcrun tool`,
  `dtrace -c %I` or `SH -c tool` is refused. Program names are matched in any
  case, since macOS treats `/bin/SH` as `/bin/sh`.
- **wg-quick runs with nothing of yours around it.** Tunnel now starts
  wg-quick from `/` with an empty environment apart from `HOME=/var/root` and
  `PATH`. A hook such as `/bin/zsh /etc/wireguard/up.sh`, `/bin/bash -l`,
  `/usr/bin/python3` or `/usr/bin/make` passed the review but, as
  administrator, read your shell startup files, Python `.pth` files or a
  `Makefile` in Sentinel's folder.
- **OpenVPN runs with nothing of yours around it either.** Tunnel starts
  OpenVPN the same way: from `/`, with an empty environment apart from
  `HOME=/var/root` and a fixed `PATH`, and with every path made absolute. As
  administrator it used to see your environment (`OPENSSL_CONF` can load a
  library into it) and start its scripts in Sentinel's folder, so `up up.sh`
  ran a `./up.sh` from there. A relative file name in an `.ovpn` (`ca ca.crt`)
  now means a file in `/`: use full paths or inline blocks. The review shows the
  exact command instead of an abbreviated one.
- **An OpenVPN hook must name its program by full path.** `up up.sh`,
  `down ./down.sh` or `plugin ./p.so` passed the review with no finding, and
  OpenVPN runs such a word as written, from its working directory. They now
  stop a Connect. Files named by a relative path (`ca ca.crt`,
  `key ~/client.key`, a proxy's credentials file) draw a warning: OpenVPN
  looks for them in `/`. The review now reads the directives inside a
  `<connection>` block too, and accepts an inline key block nested in one,
  which it used to refuse as ambiguous.
- **OpenVPN directives that load code as administrator stop a Connect.**
  `engine` (OpenSSL loads an unknown name as a library), `replay-persist`
  (OpenVPN writes that file as administrator) and a `providers` line naming
  anything but OpenSSL's own providers (`default`, `base`, `legacy`, `null`,
  `fips`) are refused; `providers legacy default` still works. The libraries a
  `pkcs11-providers` line names are listed with the hooks and checked like a
  hook program: full path, owned by root all the way up.
- **A wildcard in DNS stops a Connect.** wg-quick expands `DNS = 10.64.0.*`, so
  which DNS server applies could not be known; the review said "system DNS
  stays in use". Search domains without a server are marked as not applied,
  and keys wg-quick ignores (`DNS_`, `D-NS`) no longer count as DNS.
- **The review's DNS line now matches what wg-quick sets.** `DNS # = 10.64.0.1`
  sets no DNS server (wg-quick cuts the line at `#` before it reads the key), but
  the review showed "DNS: 10.64.0.1". It now says system DNS stays in use and
  warns about the line; a comment after a value is no longer read as part of it.
  A name such as `DNS = corp.example` is a search domain to wg-quick, not a
  server, and is now shown as one; entries separated by spaces are split. An
  entry with a wildcard is listed as neither, with a warning, and a config full
  of bad entries gives a short list of warnings instead of thousands.
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
