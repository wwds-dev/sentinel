# Portable USB mode — carry Sentinel safely

**Goal:** create and operate an isolated portable copy on macOS. **Time:** 15 minutes plus build time.

Portable mode is a self-contained PyInstaller build. It runs without the Lab source checkout and stores settings, chat/history database, logs and the API-key file in `Sentinel Data` on the removable drive. It never silently falls back to the Mac's Application Support folder.

## Prepare the drive

Use an SSD when possible. Choose **APFS** for Mac-only use, preferably encrypted when the data is sensitive. Choose **exFAT** only when Windows compatibility matters; it is less resilient to unsafe removal. Avoid FAT32. Keep substantially more than the enforced 256 MiB free-space minimum.

From the canonical Lab checkout, build directly to the mounted volume:

```bash
./scripts/build_portable.sh "/Volumes/YOUR_DRIVE/Sentinel Portable"
```

The result contains the app, a portable marker, a launcher and the persistent data folder. The builder seeds `.env` from the blank `.env.example`; it never copies real source secrets. Add keys only to the portable data folder if you want them carried on the drive.

## Start, update and stop

Open **Start Sentinel.command**. The launcher checks that the drive still exists, is writable and has enough free space. Directly opening the app also recognizes the adjacent marker. Clear errors replace silent fallback when the volume is read-only, missing or nearly full.

To update, run the same build command against the same destination. It replaces the generated app but preserves `Sentinel Data`. Existing `Sentinel Fork Data` — the name the app shipped under before 2026-09-12 — is renamed in place on first launch. Back up the entire data folder before an important upgrade.

Quit Sentinel and wait for scans, model requests and saves to finish. Then eject the volume in Finder before unplugging it. Unsafe removal can corrupt SQLite history or settings.

## Emergency Reset

**Settings → General → Emergency Reset** is a last-resort privacy reset available only in portable mode. It requires typing `ERASE SENTINEL DATA` and approving a second warning. It erases Sentinel's portable chats, history, database, logs, settings, reports stored in the data folder and `.env` API keys, then quits. It does not format the drive, touch unrelated files, or delete reports you deliberately exported elsewhere.

This is not an amnesic or forensic-erasure feature. Flash media can retain remapped blocks, and macOS, networks and model providers may keep their own records. Use encrypted APFS from the beginning when strong data-at-rest protection matters.

## Moving between Macs

A native build matches the CPU architecture on which it was made unless it was explicitly built universal. Intel builds may need Rosetta on Apple silicon. A second Mac may apply Gatekeeper quarantine: use Finder's **Open** context menu and Privacy & Security only for a build you trust. Never clear quarantine on an unknown app. USB storage may launch and save more slowly than an internal disk.

**Completion check:** locate `Sentinel Data/.env` and `data/sentinel.db` on the removable volume, confirm no new Sentinel data appeared under Application Support, quit the app, and eject safely. If you installed Sentry's background watch, it is a launch agent on the host Mac (not on the volume) that points into the volume; press Remove in Sentry before you eject.
