# Bug Spray — authorised website assessment

**Goal:** turn evidence from an in-scope asset into a useful report. **Time:** 20 minutes.

![Bug Spray workspace](docs/training/images/bug-spray.png)

Use Bug Spray only for assets you own or that an authorised program lists as in
scope. A public website is not automatically permission to scan it.

## Program radar

Open **Bug Spray** in Sentinel to see saved bounty programs and recent changes.
Search by name or platform, select a program to review its saved scope and
rewards, or use **Open program page** for its live page. **Use in report** fills
the **Program** field in the form below; it does not choose a target for you.

**First-time setup.** The radar only shows what a scan has saved, and out of the
box nothing is scanned: no platform is enabled until you choose some. Open
**Watchlist…**, tick the platforms to scan and save (this writes Bug Spray's
`config.json` in the `agents/bug_spray` folder), then press **Scan now**. The
scanner runs in Bug Spray's own Python environment; if it is missing, the
radar says "Bug Spray Python environment is missing", and the setup commands are
in `agents/bug_spray/README.md`. The radar belongs to a source checkout: the
packaged app does not include the scanner or that environment.

Once a platform is enabled, the first scan after you open the workspace starts
in the background if the last scan is older than the interval set in Bug Spray's
`config.json` (`poll_interval_minutes`, 60 by default; it cannot be changed in
the app). **Scan now** starts one immediately; the button is disabled while a
scan runs and a running scan cannot be cancelled. While Sentinel stays open it
checks again every minute whether a scan is due. Nothing scans once Sentinel is
closed. The scan reads public program directories and does not contact any
program's assets.

What to expect from the radar:

- The first scan of each platform only stores a baseline, so **Recent changes**
  stays empty until a later scan finds a difference.
- The status line reads `Last scan: … · N watched programs`, with
  `errors: <platform>` when a platform failed. After a failed scan it may show
  only "Scan finished with errors."; the scanner's own summary is not displayed
  in the app.
- The five platforms (HackerOne, Bugcrowd, Intigriti, YesWeHack, Immunefi) are
  read anonymously from unofficial endpoints, not official APIs. They can change
  without notice. If one breaks, that platform reports an error and the others
  still update. The readers are tested against recorded responses, not live
  platforms.
- A **Programs** row whose scope says "unpublished" has no public scope saved,
  typically because the platform shows it only to logged-in researchers.

Narrow the list with the platform menu, or tick **Show all** to ignore the
watchlist. **Watchlist…** edits which platforms are scanned and the keyword,
tag and minimum-payout filters; filters only change what is shown, every
program is still saved. From the `agents/bug_spray` folder, `python main.py scan`,
`list` and `show` use the same settings. **Full details…**, or a double-click on
a row, shows the whole saved scope, the reward range per severity, and the
program's tags. The arrow next to **Scan now** offers **Full re-scan**, which
re-downloads every program's details instead of reusing unchanged ones. It takes
a few minutes. **Known limitation:** during a full re-scan a program whose
download fails can be reported as Gone; treat a Gone after a full re-scan with
suspicion until the next ordinary scan.

The saved scope is a lead. Read the current live program page and its rules
before testing.

## Inputs

Enter the exact **Target URL / IP**, the **Program** name and the **Scope Type**
(for example Web Application, API / REST or Network / Infrastructure). The
screen states this plainly: **program and scope are declared by you and are not
verified or enforced.** Bug Spray does not check the target against the
program's scope, not before Nmap runs and not before the model is called, and it
asks for no confirmation that you are authorised. It drafts a report from what
you enter; staying inside a program you are authorised to test is your
responsibility, not a check the tool performs. Paste evidence into **Findings /
Burp Suite Output / Notes**: relevant requests/responses, behaviour,
reproduction notes and observed impact. Remove secrets and unrelated user data;
Bug Spray does not redact anything before your text goes to the model you chose.

The form also has a **Severity Target** menu. Your choice is sent to the model
as an unverified expectation; the severity in the report and in the Severity tile
still comes from the model's reading of your evidence, so do not treat the menu
as a result.

The **Nmap Recon Scan** section is optional and always visible. It runs a real
local process. As the caption under it warns, it runs on your machine with no
scope check and outside the budget or authorisation guard, and it touches
whatever host you give it. How it behaves:

- With the command field empty, **Run Nmap** builds `nmap -sV -sC -T4 --open <host>`
  from your Target (the scheme and path are removed; a `:port` is kept), puts it
  in the field and starts it in the same click, with no chance to review it.
  To review or change a command first, type it yourself. For a target with a
  port, type the command and give the port with `-p`.
- `-sV -sC` probes service versions and runs nmap's default scripts, and `-T4` is
  nmap's "aggressive" timing, not a conservative one. Confirm that hosts and
  ports are allowed by the program, and if the rules ask for gentle traffic,
  replace `-T4` with `-T2` ("polite") or lower before you press **Run Nmap**.
  Stop if the service becomes unstable.
- Only nmap starts. Anything else in the first word is refused, and if nmap is
  not installed the box says so and nothing runs.
- A scan stops by itself after 10 minutes or 256 KB of output, and the box says
  why. **Kill** (which replaces **Run Nmap** while a scan runs) ends it sooner.
  The main **Stop** button only cancels the model request, not the scan.
- The box also shows `[Running]`, `[Done]` and `[Error]` lines; they are only for
  you. The model receives the scanner's text alone.

Scanner output alone is not a vulnerability.

## Analyse and review

**Analyse** sends the supplied evidence and optional Nmap output to the selected
model. It needs a Target, some Findings or scan output, and a selected model.
Before anything is sent, Sentinel prices the whole request (the built-in
instructions plus your Program, Scope Type, Target, Nmap output and Findings),
checks your budget caps, and for a cloud model asks you to confirm the estimated
cost; a local model skips the confirmation. If the request is refused the status
reads "Blocked before sending." Provider and model sit in the bar next to
**Analyse**; press **Auto-route** to let Sentinel choose from your input.

The result is split into cards, each with a **Copy** button: **Vulnerability
report**, **Proof of concept**, **Remediation** and **Submission draft**. The
unedited reply is under **Raw response**. Because the built-in instructions ask
for the proof of concept and remediation inside the report, the Vulnerability
report card still contains the whole report and the other two cards repeat those
parts for copying. **Known limitation:** cards are cut by matching the model's
wording, so a `###` sub-heading can end a card early and a reply that departs
from the layout can leave a card empty or short. Check **Raw response** when a
card looks wrong.

Three tiles beside the report are read from the reply by simple text matching:
**Severity**, **CVSS Score** (the first number from 0 to 10 after the word
"CVSS", skipping a version such as `v3.1` and vector strings) and **Bounty
Estimate** (a dollar amount after the word "bounty"). The instructions do not
ask for a bounty estimate, so that tile usually stays "—". A version
written with or without a "v" (`CVSS v3.1: 7.5`, `CVSS 3.1: 7.5`) is skipped, as
is a vector string; still check the number in the report.

CVSS and CWE suggestions require human review. Sentinel does not compute or
check either, and nothing on screen marks them for review, so that step is yours.
The built-in instructions tell the model to use only the evidence you supply and
to avoid speculation, but nothing checks the reply: cut any claim your evidence
does not show.

**Stop** cancels the request and the status stays "Stopped.". The worker's
"Request cancelled" echo and a reply that was already on its way are ignored:
they are not shown, saved or recorded as a finished analysis.

Every completed analysis is also saved automatically to Saved Chats, with the
full request text (including your Findings and any Nmap output) and the reply,
and is counted in the run log and usage history. **Save Report** is separate: it
writes the raw reply to a Markdown or text file you choose (default
`bb_report_<target>_<time>.md` in Downloads). **Clear** empties the fields, Nmap
output, results and tiles; it does not stop a running analysis or Nmap scan.

Before submission, reproduce once within scope, remove destructive steps,
verify affected versions, check duplicate-policy rules, and edit the report for
clarity. Never paste session cookies, private keys or personal customer data.

## Worked example

**Scenario:** you have permission to test a bug-bounty program and want to turn
one reflected-input observation into a submittable report.

**You have:**

- Target: `http://localhost:8080/search?q=hello` (a local training app you run)
- Program: `Acme Bug Bounty` · Scope type: `Web Application`
- Finding: the `q` parameter is echoed into the page unencoded, so
  `?q=<b>hello</b>` renders bold.

**Do this:**

1. In **Program radar**, filter for the program if you track it, or just type the
   name into **Program** below. Radar never contacts the program's assets.
2. Under **Target & Program**, enter the target, program and scope type. Set
   **Severity Target** to what you expect (it is only a hint to the model). Remember the caption: scope is
   declared by you and is not enforced.
3. Leave **Nmap Recon Scan** empty and do not press **Run Nmap** for this example;
   it runs on your machine outside the guard and this finding needs no port scan.
4. In **Findings**, paste the request and the reflected response, the exact
   payload (`?q=<b>hello</b>`), and one sentence of observed impact. Remove any
   real cookies or personal data.
5. Check the provider and model beside **Analyse** (or press **Auto-route**), then
   select **Analyse** and confirm the cost dialog if you picked a cloud model.

**You should see:** a result split into vulnerability report, proof-of-concept,
remediation and submission draft cards, with the **Severity** and **CVSS Score**
tiles filled in from the model's reply (**Bounty Estimate** usually stays "—").
CVSS and CWE are the model's suggestions: check them yourself. Bug Spray should
describe only what your evidence shows. If it claims an impact you did not
demonstrate, cut it before submitting.

**Why it's safe / what it costs:** typing and the saved program list stay on your
machine. The radar's scans fetch public program directories from the five
platforms and cost nothing; they send none of your evidence. Only **Analyse**
sends your evidence to the selected model, and a cloud route costs money: the
confirmation dialog shows the estimate first, and the Inspector's cost readout
shows what was billed. Nothing scans the target unless you run Nmap yourself.

## Exercise

Repeat the worked example against a different intentionally vulnerable local
training application, then identify which claims in the report are observed facts
and which are model interpretation.
