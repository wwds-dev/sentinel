# Sentinel versioning

Sentinel uses a single public version in the form **`vMAJOR.SEQUENCE`**. The
current release is **v2.002**. The checked-in [`VERSION`](../VERSION) file is
the only source of truth; the app sidebar, window title, thin launcher and
self-contained macOS bundle all read that value.

## Numbering policy

- `MAJOR` changes only for a deliberately incompatible product generation.
- `SEQUENCE` is a zero-padded, three-digit release sequence within that major
  version. It increases monotonically: `v2.001`, `v2.002`, …, `v2.999`.
- A release number is never reused, even if a build is withdrawn.
- Database schema versions, agent versions and portable-backup format versions
  are independent internal contracts and do not use the app release number.

The everyday thin launcher reads the live checkout, so a `VERSION` change is
visible on the next launch without reinstalling it. Its version tooltip also
shows the Git revision and reports local changes, making the exact development
checkout distinguishable. A self-contained or portable app is immutable and
shows the version embedded when it was built.

## Release procedure

1. Choose the next unused sequence and update `VERSION` in the same change as
   the release milestone.
2. Move the matching version-follow-up item in `TODO.md` to complete and add
   the next numbered follow-up. This keeps the Lab project monitor actionable.
3. Add a short entry to the release record below and update documentation that
   describes changed behaviour.
4. Run the Sentinel test suite. For a distributed build, also complete the
   packaged and portable acceptance checks in `docs/testing_roadmap.md`.
5. Build only after the version and tests are final. Confirm the sidebar label
   and the bundle's `CFBundleShortVersionString` agree with `VERSION`.

## Release record

| version | date | summary |
|---|---|---|
| **v2.001** | 2026-09-22 | Introduced canonical release numbering and visible in-app build identity. |
| **v2.002** | 2026-09-29 | Expanded live OSINT sources (Shodan InternetDB, IPinfo, Criminal IP for IPs; CourtListener court records; DeHashed breach exposure), a native Tunnel "Your IP & DNS" readout with a real bash.ws DNS-leak test, and an addy.io burner-alias mint helper in the OSINT Keys tab. |

