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

## v2.002 - 2026-09-29

### Added
- More live OSINT sources: Shodan InternetDB, IPinfo and Criminal IP for IP
  addresses; CourtListener court records; DeHashed breach exposure.
- A native Tunnel "Your IP & DNS" readout with a real bash.ws DNS-leak test.
- An addy.io burner-alias helper in the OSINT Keys tab.
