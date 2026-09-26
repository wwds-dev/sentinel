# Bug Spray — authorised website assessment

**Goal:** turn evidence from an in-scope asset into a useful report. **Time:** 20 minutes.

![Bug Spray workspace](docs/training/images/bug-spray.png)

Use Bug Spray only for assets you own or that an authorised program lists as in
scope. A public website is not automatically permission to scan it.

## Program radar

Open **Bug Spray** in Sentinel to see saved bounty programs and recent changes.
Search by name or platform, select a program to review its saved scope and
rewards, or open its live page. **Use in report** fills the program name in the
form below; it does not choose a target for you. The first scan after opening
starts in the background if the last scan is older than the interval set in
Bug Spray's `config.json` (60 minutes by default). **Scan now** starts one
immediately. Sentinel checks for another scan while it remains open. The
public-directory scan does not contact any program's assets.

The saved scope is a lead. Read the current live program page and its rules
before testing. Automatic scans stop when Sentinel is closed.

## Inputs

Enter the exact target, program name and scope type (for example Web, API or
Network). Paste evidence into Findings: relevant requests/responses, behaviour,
reproduction notes and observed impact. Remove secrets and unrelated user data.

The optional Nmap section runs a real local process. Confirm that hosts and
ports are allowed by the program, use conservative timing, and stop if the
service becomes unstable. Scanner output alone is not a vulnerability.

## Analyse and review

**Analyse** sends the supplied evidence and optional reconnaissance output to
the selected model. The result separates the vulnerability, proof-of-concept
draft, remediation and submission draft. CVSS and CWE suggestions require
human review. Bug Spray should not invent missing evidence or claim impact you
did not demonstrate safely.

Before submission, reproduce once within scope, remove destructive steps,
verify affected versions, check duplicate-policy rules, and edit the report for
clarity. Never paste session cookies, private keys or personal customer data.

## Exercise

Use an intentionally vulnerable local training application. Paste a harmless
finding, generate a report, and identify which claims are observed facts versus
model interpretation.

