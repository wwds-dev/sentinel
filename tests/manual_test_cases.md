# Sentinel — Manual Acceptance Checklist

Use a disposable test database and non-sensitive prompts. Do not send paid requests unless the matching provider permission is enabled and the cost confirmation is understood. Security tests must use systems and networks you own or are explicitly authorised to assess.

## 1. Built-in roster and navigation

- [ ] The sidebar shows exactly: Chat, Trace, Bloodhound, Beacon, Sentry, Bug Spray, Tunnel, Forge.
- [ ] Each button opens the matching workspace and highlights the selected agent.
- [ ] Writing, Coding, Router, Narrator, and moved/deleted product agents do not appear as sidebar agents.
- [ ] The saved-chat filter offers "All agents" plus only the agents that have saved chats.
- [ ] Switching agents does not display another panel's controls.

## 2. Chat

### General conversation

1. Select **Chat** and **General Chat**.
2. Use a local model and ask: `Explain the difference between hashing and encryption in plain language.`

- [ ] A relevant response streams into the output area.
- [ ] Stop cancels an in-progress response without freezing the app (try it during a local streamed reply and a cloud one); the text received so far stays and a "stopped by user" notice follows.
- [ ] The completed request appears in run history and saved chats.
- [ ] Usage and cost indicators update appropriately for the selected provider.
- [ ] After two messages in one conversation, History shows one entry (not two). Rename it, send a third message, and the name and entry count are unchanged.
- [ ] Choose Writing, send a message, then choose Coding and send another: the SYSTEM block at the top of the transcript now shows the Coding prompt and the second answer follows it.
- [ ] Press Stop during a reply, then send another message: the "stopped by user" notice stays in the transcript, and the next reply still follows the Tool's instructions.
- [ ] While a reply is streaming, press New chat: the request stops and the transcript clears.
- [ ] In Local only mode, pick a cloud provider and press Run: the request runs on Ollama, the cost beside Run ends with "local only", and the Run log shows provider ollama.
- [ ] In Hybrid allowed with that provider unticked: typing raises no error and the cost reads "blocked · see route"; Run shows "Request blocked" naming the checkbox.
- [ ] Hover the BEST FIT entry and a cloud entry in the open dropdown: the reason and "Cloud route — this one costs money." appear.
- [ ] Options → Export current report writes a plain-text .txt of the whole transcript into data/reports and shows its path.

### Writing tool

1. Keep **Chat** selected and choose **Writing** from the Tool selector.
2. Submit: `Rewrite this clearly and professionally: We fixed the thing and it should be okay now.`

- [ ] The response improves clarity and tone without inventing facts.
- [ ] The run is recorded under Chat with Writing as the tool.
- [ ] No standalone Writing agent is selected or created.

### Coding tool

1. Keep **Chat** selected and choose **Coding**.
2. Submit: `Explain the bug and provide a corrected version: def first(items): return items[1]`

- [ ] The response identifies the indexing issue and discusses empty-input handling.
- [ ] The run is recorded under Chat with Coding as the tool.
- [ ] No standalone Coding agent is selected or created.

## 3. Trace (`osint`)

1. Select **Trace**.
2. Enter a domain you own or a reserved example domain and choose an appropriate query type.
3. Run the analysis with an allowed provider.

- [ ] Empty or invalid targets are rejected before a paid request.
- [ ] The response provides a focused research plan, useful source types, and clear next steps.
- [ ] Claims are framed as leads to verify rather than unsupported facts.
- [ ] The request is logged under `osint`. Stop cancels a Structure Query at the next streamed token; the status reads "Stopped.", the partial text stays on screen, and nothing is saved or costed.
- [ ] The Activity trail remains visible after completion and states that Structure Query contacted no research source. The weekly OSINT Framework catalogue download carries no target, is not recorded in the trail, and is not skipped in Local only mode (known limitation).
- [ ] A completed run appears under Saved Searches and can be reopened without issuing another model or network request.
- [ ] Auto-detect resolves a leading @ to Username, anything containing @ to Email, then IP, Phone and Domain (any dotted name), and otherwise Person if the text has a space, else Username; it never picks Company. Structure Query's trail records the resolved type with "(auto-detected)"; Live Research and Exposure Check print it without that note. Malformed typed email, domain, phone, username, or IP input is blocked before authorization. Known limitations: john.smith resolves to Domain, a Bitcoin or Ethereum address to Username, and bücher.de fails domain validation.
- [ ] Live Research for a domain names WHOIS, DNS, Team Cymru IP-to-ASN, Mnemonic passive DNS, crt.sh and the Wayback Machine (plus each keyed service whose key is saved) in the Confirm Live Research dialog, and contacts nothing when it is declined; the status line reads "Live Research cancelled before any lookup."
- [ ] The Activity trail records each source actually contacted, retains successful results if another source fails, and saves the collected record under Saved Searches.
- [ ] Stopping Live Research preserves completed source results and marks the run as partial/cancelled.
- [ ] Username Live Research names URLScan, GitHub and Keybase before confirmation and records those three as contacted. No WhatsMyName sweep runs (that is Bloodhound's Deep Dive).
- [ ] Email Live Research sends the address only to ticked services. EmailRep and Gravatar start ticked; Have I Been Pwned and BreachDirectory start unticked; HIBP and Hunter cannot be ticked without a saved key, and Hunter starts ticked once its key is saved. Gravatar receives only a hash of the address. Ticking nothing shows "No Sources Selected" and contacts nothing.
- [ ] Company Live Research names GLEIF and CourtListener before confirmation (CourtListener is part of every company run; OpenSanctions is added only when its key is saved), shows the LEI coverage limitation, and saves the results in Saved Searches. Court results are docket metadata only.
- [ ] Person and Phone Live Research contacts nothing and explains that Trace does not use people-search, reverse-phone, or data-broker services.
- [ ] A skipped email, company or exposure service is recorded as skipped-before-contact rather than contacted or failed. A keyed domain or IP service with no saved key is recorded the same way: the summary card lists it under "Skipped before contact" and it is not contacted.
- [ ] IP Live Research names WHOIS, DNS, Team Cymru IP-to-ASN, SANS DShield, Shodan InternetDB and Mnemonic passive DNS, and skips crt.sh and the Wayback Machine. DNS asks for the address's reverse (PTR) record; an address with none reads "No reverse (PTR) record" and is not an error. A private address such as 192.168.1.1 is accepted and sent (known limitation).
- [ ] Saving a key in Settings → OSINT Keys (for example IPinfo, VirusTotal or Hunter) adds that service to the next domain or IP confirmation without a restart; removing it takes it out again.
- [ ] Domain Live Research with a Hunter key shows role addresses and a count of named people, never named people's addresses.
- [ ] Company Live Research with OPENSANCTIONS_API_KEY saved names OpenSanctions in the confirmation; without the key OpenSanctions is neither named nor contacted.
- [ ] Exposure Check accepts Domain, Company and Email only. The Choose Exposure Check Sources dialog ticks Ransomware.live, Ahmia and every source whose key is saved (Intelligence X, DeHashed, Snusbase, LeakCheck); an unkeyed source cannot be ticked; Cancel contacts nothing. For an email, only the domain goes to Ransomware.live and Ahmia. For a company, DeHashed, Snusbase and LeakCheck are recorded as skipped.
- [ ] The Exposure verdict reads "On a ransomware leak site", "Possible exposure" or "No exposure found in the sources that were queried". When a source errored or Stop was pressed it reads "Incomplete" (or "Not checked" if no source answered) instead; the Research summary card's Errors line shows the failures.
- [ ] Stop during Live Research or Exposure Check lets the source already running finish, keeps completed results, reads "Stopped — partial results retained." in the status line, and saves the run as cancelled.
- [ ] Reopening a saved Live Research from Saved searches sends nothing, restores the target and the stored JSON, restores the query type box (an Exposure Check save as Domain, Company or Email), and the trail says "stored live-source record". Double-click renames; Delete selected removes the saved record.
- [ ] Settings → OSINT Keys: Save Key writes `.env` and checks the key; Check reads Works, Rejected, Malformed, Limited, Offline or Error. Only HaveIBeenPwned and Intelligence X can read Malformed from a local shape test (URLScan can answer HTTP 400); a wrong-length VirusTotal key reads Rejected. Check all keys leaves out AbuseIPDB, DeHashed and Snusbase and names them.
- [ ] The OSINT Framework catalogue is downloaded at most once a week by a background thread when the Trace panel is first shown; Structure Query reads only the cached copy, and shadow-library tools never appear among the catalogue picks.

## 4. Bloodhound (`osint_heavy`)

1. Select **Bloodhound** and use a lawful, non-sensitive test target.
2. Configure a small investigation and start collection.

- [ ] For a target type with public sources, a "Contact public sources?" dialog names the sources (No is the default); the status line then shows progress one source at a time, and the Sources gauge shows the number of sources contacted. (The panel does not list provider results; they are visible only where the dossier restates them.)
- [ ] A failed or unavailable provider does not discard the other providers' results; the dossier flags the gap (the panel itself does not list failures).
- [ ] The prompt asks the dossier to label confirmed facts, inferred patterns and speculation; judge the labels by reading the dossier (the panel does not enforce them).
- [ ] The dossier carries the model's legal/ethical point and disclaimer (a request in the prompt, not added by the panel), and the run is logged under `osint_heavy`.
- [ ] Stop during collection cancels it and the request is never billed. (Stop during the model call is a known gap: a streaming reply ends as "Error", a non-streaming reply is not cancelled.)

3. Tick **File Discovery — selected locations only**, leave **This Mac** selected, add a small test folder, and search by a
   partial name, extension, size, and modified date.

- [ ] Bloodhound searches only folders explicitly listed by the user.
- [ ] Results show name, path, type, size, and modified time and can be sorted (the Size column sorts as text; known gap).
- [ ] Cancel, the window's Stop button, and quitting Sentinel each stop a large search without freezing the interface, and Cancel keeps the matches found so far.
- [ ] Inaccessible folders produce a clear warning while readable folders continue.
- [ ] Reaching the safety limit asks the user to narrow the search.
- [ ] Double-clicking a result on This Mac opens its containing folder and changes no files.

4. Select **Remote SSH machine** and enter an owned test host already present in
   `known_hosts`, an SSH-agent-backed user, and a small absolute remote folder.

- [ ] A host that is not in `~/.ssh/known_hosts`, or whose key has changed, is rejected instead of silently trusted; after one normal `ssh` login the same host is accepted.
- [ ] The search uses SFTP and returns the same metadata columns and filters.
- [ ] Authentication and offline errors are clear and do not expose credentials.
- [ ] Open SSH Terminal hands the destination to the system SSH application.
- [ ] Double-clicking a remote result copies its remote path and says so in the status line.
- [ ] Image metadata: attach a JPEG with GPS; the panel shows it, and with "Include this image's metadata" unticked the request (Saved Chats entry) contains no EXIF block; ticked, it does, and the file name never does.
- [ ] Budget: set a very low per-request or daily cap; Investigate is blocked with "Request Blocked" after collection, before the model is called.
- [ ] Quit Sentinel (and Emergency Reset on a portable build) during a remote file search; the process exits cleanly.

## 5. Beacon (`wifi`)

1. Select **Beacon** on a machine with no external Wi-Fi adapter attached.
2. Run Connection Preflight and adapter detection, then request diagnostic guidance for an owned test network.

- [ ] Adapter state is reported accurately and absence does not crash the panel.
- [ ] Guidance separates local macOS diagnostics from Kali/aircrack-ng commands.
- [ ] Preflight labels the default route as internet/control and performs no mode or connection changes.
- [ ] With no separate routed interface, Kali planning visibly warns that monitor mode could remove internet access.
- [ ] VM guidance explains USB passthrough detachment and guest-driver limitations.
- [ ] Offensive commands include an explicit authorisation warning.
- [ ] Generated commands identify placeholders and are not executed automatically.
- [ ] The request is logged under `wifi`.
- [ ] The AI Analysis box is unticked on first open; with it unticked, a scan result or a generated command sequence is shown without any model request.

### Portable USB acceptance

1. Build to a writable test volume with `scripts/build_portable.sh`.
2. Add fictional settings/history to `Sentinel Data`, rebuild to the same destination, and launch again.

- [ ] The app, marker, launcher and explicit data folder are present.
- [ ] Existing data survives the upgrade and the source `.env` was not copied.
- [ ] No portable run creates state in Application Support or the Lab checkout.
- [ ] Read-only, unavailable and under-256-MiB volumes produce clear errors.
- [ ] Quitting followed by Finder eject leaves the volume cleanly removable.
- [ ] Emergency Reset is hidden in normal/dev mode and visible only in portable mode.
- [ ] A wrong confirmation phrase or second-stage cancellation changes nothing.
- [ ] A confirmed reset removes the portable database, histories, logs, settings and `.env`, preserves unrelated USB files, quits, and does not recreate window preferences.

## 5a. Sentry (`sentry`)

Use only a network you own. Start with the background watch off and **Explain findings with AI** unticked.

1. Select **Sentry**. Press **Reset baseline** if a baseline exists, then **Run watch pass**.
2. Join a spare device you own to the network and let your Mac talk to it (AirDrop, or `ping` from Terminal). Press **Dry run**, then **Run watch pass** twice.
3. Tick **Explain findings with AI**, choose an Ollama model and repeat step 2 with another spare device; then choose a cloud model and read the confirmation dialog.

- [ ] The AI box is unticked on first open and its hover text names LAN IPs, MAC addresses and process names.
- [ ] The first pass says "Baseline recorded." and flags nothing; nothing is sent to a model.
- [ ] Dry run is headed "dry run, nothing saved"; with no baseline it says so and does not say "Baseline recorded".
- [ ] The new device is a NOTICE on the dry run and on the first saved pass; the second saved pass shows "No new anomalies." (unless unrelated connections changed).
- [ ] With the AI box ticked only a pass that has findings starts a request; a cloud route shows the external-API confirmation (provider, model, tokens, cost - not the content); the request and the reply appear in Saved Chats and the run log under `sentry`.
- [ ] Stop during a pass restores the buttons, saves nothing and starts no model call.
- [ ] `PATH=/usr/bin .venv/bin/python agents/sentry/main.py selftest` (so `arp`, `ndp`, `route` and `lsof` are not found) exits 1 and lists the commands it could not find; in the app, a pass with a missing collector shows an amber [COLLECTOR FAILED] line and a first baseline is refused.
- [ ] Reset baseline removes the baseline and the recorded findings with no confirmation (record this).
- [ ] Background watch: Install, quit Sentinel, wait two intervals; `watch.log` in the Sentry state directory has new entries; restart Sentinel and the findings pane lists what the watch recorded. `find ~ -name baseline.json -path '*sentry*'` finds exactly one file (panel and watcher share a directory).
- [ ] Remove deletes `~/Library/LaunchAgents/com.sentinel.sentry.watch.plist` and the status line reads "off".
- [ ] Owned-lab checks (record the result, do not assume): poison a client's and the gateway's ARP entry from a second machine and note which findings fire; enable Remote Login and compare the pass with `sudo lsof -nP -iTCP -sTCP:LISTEN` (does unprivileged `lsof` see the root-owned listener?); on a Mac with Wi-Fi off, note whether the established-connections collector shows [COLLECTOR FAILED] (lsof exits 1 when it lists nothing).
- [ ] Self-contained build only: press Install from the built .app and record what the plist launches (the build ships no `agents/sentry/main.py` file).

## 6. Bug Spray (`bug_bounty`)

1. Select **Bug Spray**.
2. Provide a fictional or explicitly in-scope program, target, and harmless sample finding.
3. Generate triage or report output.

- [ ] With Target, Findings and scan output all empty, Analyse stops with "Enter a target, paste findings, or run a scan first." and sends nothing. An empty Program does not warn: scope is declared by the user and not enforced (the on-screen caption says so).
- [ ] The response distinguishes evidence from assumptions and does not claim unperformed exploitation.
- [ ] The report includes reproducible steps, impact and remediation (the prompt does not request evidence placeholders).
- [ ] The workflow keeps the authorised-program boundary visible (caption under Target & Program: "declared by you and are not verified or enforced"; caption under Nmap Recon Scan: no scope check, outside the budget/authorisation guard).
- [ ] The run appears under `bug_bounty` in the run log and in Saved Chats (request text including Findings, plus the reply); Stop restores the Analyse button (after a streamed cancel the status may read "Error." with "[Error] Request cancelled by user.": known limitation).
- [ ] A reply in the prompt's own layout (numbered bold items inside ## VULNERABILITY REPORT) fills the Proof of concept and Remediation cards; the CVSS tile shows the score, not the version ("CVSS v3.1 score 7.5" gives 7.5).
- [ ] Typing `curl http://x` in the nmap box is refused with "Only nmap can be run from here"; with nmap absent the box says "nmap is not installed (brew install nmap)." and Run Nmap stays enabled.
- [ ] A running scan is stopped by Kill and by quitting Sentinel; the window-level Stop does not stop it. The model never receives the [Running]/[Done] lines.
- [ ] Pasting a multi-kilobyte Finding raises the confirmation dialog's approximate token count compared with a one-line Finding (the whole request is priced).
- [ ] Program radar on a fresh checkout (no `config.json`): opening the workspace starts no scan; after ticking a platform in Watchlist… and saving, Scan now runs (needs `agents/bug_spray/.venv`) and the first scan leaves Recent changes empty (baseline).

## 7. Tunnel (`vpn`)

1. Select **Tunnel** with no VPN active and run **Check Connection** while
   **Include public IP and latency** is off.

- [ ] The result has structured cards for summary, installed tools, detected tunnels, local route/DNS, and interpretation.
- [ ] No provider permission or model-cost confirmation appears.
- [ ] The check does not request an administrator password or change network state.
- [ ] Missing `wg`, `wg-quick`, OpenVPN, or route access is reported without crashing.
- [ ] Stop requests cancellation and leaves the panel usable.

2. Select a saved profile and run the local check again.

- [ ] The active VPN Agent profile is preselected when it exists; choosing it in Sentinel does not modify the profile file.
- [ ] The comparison card shows the profile name, interface, endpoint and port without displaying keys.
- [ ] Matching interfaces, handshakes and routes are described as ready; mismatches produce plain-language next steps.
- [ ] Endpoint/port findings state that saved values are present but the live peer endpoint is not verified.
- [ ] A different default interface is described as potentially normal for a split tunnel rather than an automatic failure.
- [ ] Closing Sentinel or committing Portable Emergency Reset during a check cancels and joins the Tunnel worker before the app exits or erases data.

3. Enable **Include public IP and latency**, decline its confirmation, and run again.

- [ ] The confirmation names `api.ipify.org` and `1.1.1.1`.
- [ ] Declining starts no worker and contacts no external destination.

4. On a VPN you own, accept the optional check and compare results before and
   after connecting.

- [ ] WireGuard status shows peer count, handshake age, and aggregate transfer totals without displaying key material.
- [ ] Results clearly state that an active interface or configured DNS list is not proof that all traffic is protected.

5. Preview Connect, Disconnect and Restart for the selected profile.

- [ ] Each preview names the selected profile and interface, expected effects, checks and proposed commands.
- [ ] Previewing requests no provider authorization, admin password, subprocess, or network-state change.
- [ ] A missing or unsafe interface produces no command.
- [ ] Disconnect warns that ordinary traffic may resume when no kill switch is active.

6. Test both remote VPS and owned-LAN/native modes with placeholder values.
7. Generate a plan or configuration without deploying it.

- [ ] Remote and native modes explain their different traffic and exit-IP behaviour.
- [ ] Native mode defaults to appropriate split-tunnel guidance unless explicitly changed.
- [ ] Output marks keys, addresses, interfaces, and hostnames that require replacement.
- [ ] Kill-switch, firewall, DNS, and rollback considerations are included where relevant.
- [ ] Nothing is deployed or executed automatically.
- [ ] Build Config renders instantly, creates no run-log request and has no Stop.

8. Ask Advisor a question with a model selected.

- [ ] The request goes through the guard, is logged under `vpn`, and Stop cancels it.

The next steps need macOS, an owned WireGuard config and an administrator account; run them on a lab network you can recover.

9. Your IP & DNS. Open Tunnel.

- [ ] Local fills in without any dialog and contacts nothing; Refresh works.
- [ ] Check public IP and Run test start immediately (no confirmation); the Run test label says which resolvers answered; no flag appears without an `IPINFO_API_KEY`.
- [ ] After a connect or disconnect, Local refreshes; Public re-checks only if it had succeeded earlier this session.

10. Import and templates (no administrator access needed).

- [ ] Choose "Example — Netherlands (template)" and press Connect: a dialog explains it is a template; no password is requested; the Execution tab shows no command run; a `refused` line is appended to `data/logs/tunnel_audit.jsonl`.
- [ ] Press Disconnect on the same template: it is not blocked by the template rule.
- [ ] Import config... a WireGuard .conf: the profile is selected, its endpoint matches the file, and the file is not copied. Move the file and press Connect: refused ("no longer exists"), logged `refused`.
- [ ] Import a .conf whose name has a space or is longer than 15 characters: Connect is refused with the rename message.
- [ ] Import an .ovpn: it is typed OpenVPN. Reload the Compare profile picker to see it there (Import does not refresh it).
- [ ] The starter profiles Home Server / GL-iNet Flint 2 are connectable by interface name: their review shows no routing, DNS or hook lines (record this; first import on a clean state also hides the starter profiles).

11. Gated Connect / Disconnect (WireGuard, a config you wrote).

- [ ] Connect fills the Execution tab (target, exact command, routing/DNS intent, warnings, rollback) before any question; the question defaults to No; pressing No logs `declined` and runs nothing.
- [ ] Yes raises the macOS authorisation dialog even if a sudo session was used in Terminal moments earlier; the password is not stored; cancelling the dialog is reported as Failed and logged.
- [ ] After success the status line and the Post-change check say verified or NOT verified; a full tunnel also reports which interface the public route uses; the text says handshake/DNS/leak are not verified.
- [ ] Pressing Connect again while the interface is up is refused (blocker), logged `refused`.
- [ ] Disconnect warns about clear traffic (or the armed kill switch), runs after Yes, and the post-check reports the interface no longer recorded as up.
- [ ] `tunnel_audit.jsonl` has one line per attempt, mode 600, with no key text. Use a config whose PrivateKey is a recognisable fake value and confirm it appears nowhere in the Execution tab, status line or audit file, including after forcing a wg-quick parse error.
- [ ] A config with a PostUp line (harmless command): the Connect review lists it in "Runs commands as administrator", the confirmation repeats it, a second dialog asks again with default No, and declining either is logged `declined`. The same lines are NOT listed on Disconnect or in Inspect config.
- [ ] Record whether Connection Check, run right after a verified Connect, reports the selected profile as active (the interface may appear as a utunN name).

12. OpenVPN (packaged build, so the data path contains a space; and a portable build).

- [ ] Connect with an .ovpn starts one tracked process; a second Connect is refused (blocker).
- [ ] Disconnect stops that process and the post-check agrees. Start a different openvpn by hand: Disconnect refuses to touch it ("Cannot verify a Sentinel-owned OpenVPN process") and logs `failed`.
- [ ] The review shows an abbreviated command and no routing/DNS lines; Connection Check counts any openvpn process.

13. Kill switch (WireGuard profile, tunnel up).

- [ ] Arm shows a confirmation that defaults to No, names the protocol/port and endpoint, and prints the recovery command; No logs `declined` and changes nothing.
- [ ] Arm for a template or an unresolvable endpoint is refused.
- [ ] Armed with the tunnel up, the tunnel keeps working and the endpoint stays reachable; with the tunnel dropped, other traffic is blocked; LAN access still works.
- [ ] Arm before the tunnel exists: the result warns that nothing is exempt; connect, then re-arm.
- [ ] Disarm restores traffic; the recovery command from the dialog also restores traffic. Check `/etc/pf.conf` and pf state afterwards and record them (the anchor block and `pfctl -E` stay).
- [ ] Arming with an OpenVPN profile: record that the OpenVPN tunnel's own traffic is blocked (known limitation).
- [ ] Quit Sentinel while armed: the rules are still loaded (Sentinel does not disarm on quit).
- [ ] The label next to the buttons starts at "Kill switch not armed." after a restart even if rules are loaded (known limitation).

14. Close during a connection.

- [ ] With the macOS password prompt open, close Sentinel: no crash or Python error; reopen and run Connection Check to learn the real state.
- [ ] In a portable build, start a Connect, then run Settings > General > Emergency Reset: the reset either completes or shows "Reset refused" with the reason; it never fails silently.
- [ ] Record whether the interrupted attempt appears in the audit log.

## 8. Forge (`manager`)

1. Select **Forge** and describe a harmless agent that summarizes local text supplied by the user.
2. Analyze the idea, inspect the generated specification, then cancel before approval.

- [ ] Forge produces a structured, reviewable specification.
- [ ] Cancelling (Reject, Clear, No in the confirmation, or Stop) creates no `agents/<name>_agent.py` and no row in the `agents` or `tools` tables. The analysis itself still leaves a saved chat plus `runs` and `usage` rows under `manager`.

Repeat with a disposable agent name and approve the reviewed specification.

- [ ] The generated key is valid and does not collide with a built-in key.
- [ ] Approval creates exactly one `agents/<name>_agent.py`, one disabled `agents` row (`auto_generated = 1`) and one disabled `tools` row, and nothing else.
- [ ] The UI clearly says the result is an inactive scaffold and is not added to the sidebar automatically.
- [ ] The `agents` and `tools` rows carry exactly the approved providers, budget and approval flag; the scaffold file contains no imports; Settings → Tools shows the new row unticked.
- [ ] The Analyze request is logged under `manager` (one saved chat, one `runs` row, one `usage` row). Approval and rejection are not logged anywhere persistent.
- [ ] A spec the factory would refuse (for example name `chat`, a duplicate label, or an unknown provider) shows a "Cannot be created as written" card with the reason, `[Invalid]` in the Creation Log, and Approve disabled.
- [ ] A description containing triple quotes or a trailing backslash is accepted and the generated `agents/<name>_agent.py` compiles (`python -m py_compile`).
- [ ] Text such as `<b>hi</b>` or `<!-- hidden -->` in the system prompt appears literally on the review card.
- [ ] Stop during Analyze re-enables Analyze and writes nothing; the log shows [Stopped] and [Error].
- [ ] A reply whose `allowed_tools` is `null` leaves "No results yet." with Approve and Reject disabled (known limitation; the expected result changes if the panel is hardened).
- [ ] The confirmation does not mention Chat or Settings → Tools; ticking the generated tool in Settings → Tools and restarting shows it in Chat's tool selector, but sending is refused with "does not permit tool" (known limitation).

## 9. Shared permissions, budgets, and persistence

- [ ] A disabled cloud-provider permission blocks the request before network use.
- [ ] Local Ollama requests do not require a cloud permission.
- [ ] Session and daily budget limits block requests that would exceed them.
- [ ] Cancelling or failing a request closes its run with the correct status.
- [ ] Restarting the app preserves settings, saved chats, usage, and run history.
- [ ] Historical records for retired agent keys remain readable without adding those keys to the active roster.
- [ ] Settings and registry views agree with the eight built-in agents. Forge-generated agents are absent from the sidebar and the catalog; in Settings → Agents and Tools they are listed alphabetically among the built-ins, unticked and without a marker (known limitation).
