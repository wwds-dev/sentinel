# Bloodhound — deep investigation and file discovery

**Goal:** build a structured dossier or find files without changing them. **Time:** 20 minutes.

![Bloodhound workspace](docs/training/images/bloodhound.png)

## Investigation dossier

Enter the target, target type, investigation scope and objective. Target type
includes **Crypto Address** alongside Person, Username, Email Address,
Domain / IP, Organisation and Phone Number; Auto-detect recognises a Bitcoin or
Ethereum address (by its format; the checksum is not verified), an email, an
IPv4 or IPv6 address (even written with a port, `/CIDR`, brackets or a scheme)
and a domain before falling back to a username, so a dotted handle such as
`john.doe` reads as a domain. **Quick Scan** requests a concise result;
**Standard Investigation** balances coverage and length; **Deep Dive** asks for
exhaustive treatment, usually needs a strong long-context model, and on a
username also checks several hundred sites (allow up to two minutes).

The provider and model are chosen in the run bar beside **Investigate**. Sentinel
pre-selects its recommendation for Bloodhound, normally a paid cloud model;
choose Ollama if the dossier step must stay on this Mac.

**Investigate** produces Overview, Digital Footprint, Infrastructure/Social
Profile, Risk & Red Flags, and Methodology & Tools. Threat and confidence scores
are model-generated indicators, not factual measurements. The prompt asks the
model to label confirmed facts, inferred patterns and speculation and to end
with a lawful-use disclaimer, but Sentinel does not check or add either, so
read the labels critically. Check cited sources and distinguish a shared name
from a verified match.

### Before anything is sent

1. **Permission check.** A paid provider asks you to confirm the request. The
   cost shown there is estimated from the target alone, so the real cost is
   higher.
2. **Public sources.** A **Contact public sources?** dialog lists every
   service the target will be sent to, and **No** is the default. Answer No and
   nothing is contacted and nothing is billed. Phone numbers and names with a
   space contact nothing and are not asked about. Sentinel's "Local only"
   execution mode does not skip this step, so this dialog is your control.
3. **Collection**, then a **budget check** of the whole request (instructions,
   collected data, image metadata) against your caps, then the model call.

**Stop** during collection cancels it and the request is never billed.
Stop during the model step is not clean: a streaming reply ends as an error
message, and a reply that arrives all at once is not cancelled, so it may still
be shown and billed.

### Image

An optional target image shows its EXIF metadata (date, device, software, GPS)
in the panel, with map, reverse-image and face-search links. The links only open
each service in your browser: Sentinel uploads nothing, and clicking a map link
sends those coordinates to that site. The metadata reaches the model only if you
tick **Include this image's metadata (EXIF, GPS) in the model prompt**. The box
is off by default and stays as you left it when you change or clear the image,
so check it before each run. The image file and its name are never sent.
Attaching an image does not prove identity, location or ownership. EXIF is read
from JPEG, PNG and WebP; for TIFF, BMP and HEIC the panel says "No EXIF data
found" even if the file has some.

### Live collection

For domain, IP, organisation, email, username and crypto-address targets,
Bloodhound first runs live public-source collection and feeds those records to
the model, which is told to treat them as confirmed facts (a service can still
be wrong or out of date): network, exposure and attack records for
infrastructure (the same collection Trace uses); the GLEIF registry plus
CourtListener court dockets, ICIJ Offshore Leaks and sanctions screening for
organisations; leak and dark-web exposure checks, with breach names, counts and
kinds of data from DeHashed, Snusbase and LeakCheck when their keys are set;
URLScan, GitHub and Keybase for usernames; and, for a Bitcoin or Ethereum
address, on-chain balance, transaction counts and recent activity from
Blockstream and Blockscout — no key needed, and the dossier is told that
on-chain history shows what an address did, not who controls it. It collects
records and metadata only: it never opens leaked files, court-document contents
or file contents. The panel does not show the collected records themselves, only
a **Sources** count, so they are visible only where the dossier restates them. A
source that fails does not stop the others, but the panel does not list failures
afterwards, so look for the gaps the dossier flags. Text taken from public profiles and pages reaches the
model as data it is told to trust, so verify anything that matters at its source.

## File Discovery

This separate mode uses no AI. Tick **File Discovery — selected locations only**
to open it, choose **This Mac** or **Remote SSH machine**, then explicitly add
the folders to search; nothing is searched until you press **Search Selected
Folders**. Filter by complete/partial name (case-insensitive), comma-separated
extensions (the last extension only), minimum/maximum size in MB and modified
date. Results show name, full path, type, size and modification time; click a
header to sort (the Size column sorts as text). Double-clicking a result opens its folder on this
Mac; for a remote result it copies the remote path instead.

Discovery reads metadata only, skips every symbolic link (files as well as
folders) and cannot modify files. Remote discovery uses your existing SSH
configuration, keys/agent and known-host verification over SFTP. The host must
already be in `~/.ssh/known_hosts`: connect once with your normal SSH client and
accept its key, otherwise Sentinel rejects it, and a changed key is rejected
too. From `~/.ssh/config` it uses HostName, User, Port and ProxyCommand (a User
or Port set there overrides the panel); IdentityFile and ProxyJump are ignored.
Sentinel stores no password or private key. **Open SSH Terminal** hands the host
to the normal system SSH client; it does not give the AI a remote shell.

Searches stop at safety limits (5,000 matches or 250,000 entries checked).
Narrow the folder or filters rather than trying to scan an entire device.
Permission errors mean the current account cannot read that location.
**Cancel**, the window's Stop button, and closing or quitting Sentinel all stop
a search; cancellation keeps matches found so far, but it cannot interrupt a
remote connection attempt or a single folder listing. A dropped SSH link or a
file with an impossible modified time ends the search with an error and no
partial results.

## Best practices

- Search the smallest relevant folder first.
- Use extensions without relying on them as proof of actual file content.
- Treat modification dates as filesystem metadata, not authorship evidence.
- Keep investigation files and exports in an access-controlled case folder.
  Saved Chats keeps each dossier request as sent (target, collected data and,
  if ticked, image metadata) with the dossier, and the Run log keeps the
  target.
- Never send paths, filenames or extracted metadata to a cloud model unless
  the disclosure is necessary and explicitly approved. Bloodhound follows the
  same rule: image metadata leaves your Mac only when you tick the box, and
  File Discovery results never go to a model, so copying them into Chat is
  your decision.

## Exercise

Create a test folder with several harmless files. Find only PDFs modified in a
chosen range, cancel a broader search, and verify that none of the files changed.
Then run a Quick Scan on a domain you own: read the **Contact public sources?**
list, answer **No**, and confirm that the status line reads "Cancelled before
any public source was contacted." (If a paid provider asks you to confirm the
request first, accept that and then answer No here.)
