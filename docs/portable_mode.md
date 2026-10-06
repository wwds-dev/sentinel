# Sentinel portable mode (macOS)

Portable mode runs the self-contained PyInstaller app from removable storage and keeps its writable state beside it. It does not use the Lab checkout or `~/Library/Application Support/Sentinel`.

## Build or upgrade

From the canonical source checkout, run:

```bash
./scripts/build_portable.sh "/Volumes/YOUR_DRIVE/Sentinel Portable"
```

The folder contains `Sentinel.app`, `.sentinel-portable`, `Start Sentinel.command`, and `Sentinel Data`. Re-running the command replaces only the generated app and support files. It preserves the complete data folder, including settings, history, logs and `.env`. The builder copies `.env.example` only; it never copies the source checkout's real `.env`. An existing `Sentinel Fork Data` folder — the name the app shipped under before 2026-09-12 — is renamed in place on first launch.

## Storage and compatibility

- Prefer **APFS** for Mac-only use. **exFAT** is suitable when the drive must also be readable on Windows, but has weaker metadata and crash resilience. Avoid FAT32 because of file-size and permission limitations.
- Builds are architecture-specific unless produced as a universal app. An Apple-silicon build needs Apple silicon; an Intel build may require Rosetta on Apple silicon.
- USB flash storage is slower and less durable than an SSD. Keep at least 256 MiB free; substantially more is recommended for histories and logs.
- On another Mac, Gatekeeper may quarantine an unsigned/local build. Use Finder's **Open** context-menu and approve it in Privacy & Security only when you trust the build. Do not remove quarantine from unknown applications.

## Use and safety

Start with `Start Sentinel.command`, or open the app directly while it remains beside the marker. The app validates that the volume exists, is writable and has enough free space. Failure is explicit; it never silently falls back to the Mac's normal data folder.

Quit Sentinel, wait for all activity to stop, and use Finder's **Eject** before removing the drive. Removing it while running can corrupt SQLite history or settings. Back up the entire `Sentinel Data` folder regularly, especially before upgrades. Treat its `.env`, chat history and logs as sensitive; use encrypted APFS storage when appropriate.

## Emergency Reset

In portable mode, open **Settings → General → Emergency Reset**. The control requires the exact phrase `ERASE SENTINEL DATA` and a second confirmation. It stops active Sentinel work, permanently deletes the contents of `Sentinel Data`—including chats, history, settings, logs, reports stored there, database and `.env` API keys—and quits without writing window preferences back. Files you deliberately exported elsewhere are outside this boundary and are not deleted.

The reset is narrowly guarded: it works only beside a valid portable marker and never formats the USB drive or deletes files outside Sentinel's data folder. Flash storage may remap blocks, so ordinary file deletion is not a reliable forensic secure erase. macOS metadata, crash/system logs, swap, routers, DNS services and AI providers may retain separate records. For disposal-level assurance, use an encrypted APFS volume from the start and erase its encryption key or reformat the volume with Disk Utility after independently backing up any unrelated files.
