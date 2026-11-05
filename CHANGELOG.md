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
