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
Network). The screen states this plainly: **program and scope are declared by
you and are not verified or enforced.** Bug Spray drafts a report from what you
enter; staying inside a program you are authorised to test is your
responsibility, not a check the tool performs. Paste evidence into Findings:
relevant requests/responses, behaviour, reproduction notes and observed impact.
Remove secrets and unrelated user data.

The optional Nmap section runs a real local process. As the caption under it
warns, it **runs the command on your machine — the first word is the program to
launch — with no scope check and outside the budget or authorisation guard.**
Confirm that hosts and ports are allowed by the program, use conservative timing,
and stop if the service becomes unstable. Scanner output alone is not a
vulnerability.

## Analyse and review

**Analyse** sends the supplied evidence and optional reconnaissance output to
the selected model. The result separates the vulnerability, proof-of-concept
draft, remediation and submission draft. CVSS and CWE suggestions require
human review. Bug Spray should not invent missing evidence or claim impact you
did not demonstrate safely.

Before submission, reproduce once within scope, remove destructive steps,
verify affected versions, check duplicate-policy rules, and edit the report for
clarity. Never paste session cookies, private keys or personal customer data.

## Worked example

**Scenario:** you have permission to test a bug-bounty program and want to turn
one reflected-input observation into a submittable report.

**You have:**

- Target: `http://localhost:8080/search?q=hello` (a local training app you run)
- Program: `Acme Bug Bounty` · Scope type: `Web Application` · Severity: `Medium`
- Finding: the `q` parameter is echoed into the page unencoded, so
  `?q=<b>hello</b>` renders bold.

**Do this:**

1. In **Program radar**, filter for the program if you track it, or just type the
   name into **Program** below. Radar never contacts the program's assets.
2. Under **Target & Program**, enter the target, program, scope type and severity.
   Remember the caption: scope is declared by you and is not enforced.
3. Leave **Nmap Recon Scan** empty for this example; it runs on your machine
   outside the guard and this finding needs no port scan.
4. In **Findings**, paste the request and the reflected response, the exact
   payload (`?q=<b>hello</b>`), and one sentence of observed impact. Remove any
   real cookies or personal data.
5. Leave the route on **Auto-route**, or pick a provider, then select **Analyse**.

**You should see:** a result split into vulnerability, proof-of-concept draft,
remediation and submission draft, with the **Severity**, **CVSS Score** and
**Bounty Estimate** tiles filled in. CVSS and CWE are suggestions marked for your
review. Bug Spray should describe only what your evidence shows — if it claims an
impact you did not demonstrate, cut it before submitting.

**Why it's safe / what it costs:** radar and typing are local and free. Only
**Analyse** leaves the device — it sends your evidence to the selected model, and
a cloud route costs money (watch the Inspector's cost line). Nothing scans the
target unless you run Nmap yourself.

## Exercise

Repeat the worked example against a different intentionally vulnerable local
training application, then identify which claims in the report are observed facts
and which are model interpretation.
