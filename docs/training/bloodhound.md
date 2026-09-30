# Bloodhound — deep investigation and file discovery

**Goal:** build a structured dossier or find files without changing them. **Time:** 20 minutes.

![Bloodhound workspace](docs/training/images/bloodhound.png)

## Investigation dossier

Enter the target, target type, investigation scope and objective. Target type
includes **Crypto Address** alongside Person, Username, Email, Domain/IP,
Organisation and Phone Number; Auto-detect recognises a Bitcoin or Ethereum
address before falling back to a username. **Quick Scan**
requests a concise result; **Standard** balances coverage and length; **Deep
Dive** asks for exhaustive treatment and usually needs a strong long-context
model. An optional target image can contribute locally extracted EXIF metadata.
Attaching an image does not prove identity, location or ownership.

**Investigate** produces Overview, Digital Footprint, Infrastructure/Social
Profile, Risk & Red Flags, and Methodology & Tools. Threat and confidence scores
are model-generated indicators, not factual measurements. Check cited sources
and distinguish a shared name from a verified match.

For domain, IP, organisation, email and crypto-address targets, Bloodhound first
runs live public-source collection and feeds those verified records to the
model as facts: network, exposure and attack records for infrastructure (the
same collection Trace uses); the GLEIF registry plus CourtListener court dockets,
ICIJ Offshore Leaks and sanctions screening for organisations; leak/dark-web
exposure checks, including DeHashed breach metadata when its key is set; and, for
a Bitcoin or Ethereum address, on-chain balance, transaction counts and recent
activity from Blockstream and Blockscout — no key needed, and the dossier is told
that on-chain history shows what an address did, not who controls it. It only ever
collects text metadata — it never downloads leaked material, court-document
contents or file contents.

## File Discovery

This separate mode uses no AI. Choose **This Mac** or **Remote SSH machine**,
then explicitly add the folders to search. Filter by complete/partial name,
comma-separated extensions, minimum/maximum size and modified date. Results
show name, full path, type, size and modification time.

Local discovery reads metadata only, does not follow directory links and
cannot modify files. Remote discovery uses your existing SSH configuration,
keys/agent and known-host verification over SFTP. Sentinel stores no password
or private key. **Open SSH Terminal** hands the host to the normal system SSH
client; it does not give the AI a remote shell.

Searches stop at safety limits. Narrow the folder or filters rather than trying
to scan an entire device. Permission errors mean the current account cannot
read that location. Cancellation keeps matches found so far.

## Best practices

- Search the smallest relevant folder first.
- Use extensions without relying on them as proof of actual file content.
- Treat modification dates as filesystem metadata, not authorship evidence.
- Keep investigation files and exports in an access-controlled case folder.
- Never send paths, filenames or extracted metadata to a cloud model unless
  the disclosure is necessary and explicitly approved.

## Exercise

Create a test folder with several harmless files. Find only PDFs modified in a
chosen range, cancel a broader search, and verify that none of the files changed.

