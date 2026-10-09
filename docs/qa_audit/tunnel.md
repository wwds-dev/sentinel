# Requirements-traceability audit — Tunnel (`vpn`)

Repo: `/home/claude/wwds-dev/sentinel` (read-only audit; nothing was run). All paths below are relative to the repo root. Version audited: `VERSION` = 2.002.

Source tags used in the table: **R** = README.md, **V** = docs/agents/vpn.md, **T** = docs/training/tunnel.md, **U** = inline UI tooltip/label in ui/panels/vpn.py, **A** = services/agent_catalog.py. Note: `ui/tooltips.py` contains **no** Tunnel-specific tooltip keys at all (no `vpn.*` entries); the only Tunnel text it applies is the sidebar tooltip taken from `BUILTIN_AGENTS["vpn"]` (tooltips.py:46-49). All real Tunnel tooltips are set inline in `ui/panels/vpn.py`.

---

## 1. Verdict

The read-only half of Tunnel (Connection Check, profile comparison, Action Preview, Config Inspection, Build Config, Advisor wiring, the optional ipify/1.1.1.1 confirmation, Your IP & DNS) is implemented and matches its documentation closely, and the Connect/Disconnect gate (target review with blockers, default-No confirmation, off-thread privileged run, post-change check, 600-mode JSONL audit) is real and well covered by unit tests. The audit nevertheless finds **four headline promises that the code does not keep**: (1) the pf kill switch does not "block all but the tunnel endpoint" — `vpn_connection.arm_killswitch` passes `allow=[]`, so `killswitch.build_rules` emits only an ICMP pass for the endpoint and no UDP/TCP rule for the tunnel itself (and it does nothing useful for OpenVPN); (2) OpenVPN process identity (`_is_tracked_process`) compares a `shlex`-split `ps` line to the pid-file path, which contains a space in every packaged (`~/Library/Application Support/Sentinel`) and portable (`Sentinel Data`) build, so in those builds an OpenVPN tunnel can never be disconnected from Sentinel, double-connect is not blocked and the post-check reports "not verified" (and the privileged script calls bare `openvpn`, the same PATH problem that was already fixed for `wg-quick`); (3) app close / Emergency Reset "join the worker" fails for the Connect/Disconnect/kill-switch worker because `VpnConnectionWorker` has no `cancel()` (AttributeError inside `VpnPanel.shutdown`); (4) "every attempt, refused ones included" is audited only on the service path — every refusal the user can actually trigger in the UI (template, blocker, declined confirmation) returns before `execute()` and writes nothing. Secondary findings: privilege escalation tries cached `sudo -n` before the macOS dialog; `PreUp/PostUp/PostDown` hooks (root-executed by `wg-quick`) are neither inspected nor warned about; tool output is echoed into the audit log/Execution tab so a malformed key could be quoted; imported profiles and kill-switch state live in `~/Library/Application Support/VPN Agent` outside Sentinel's portable data and Emergency Reset; the in-app "Agent guide" (docs/agents/vpn.md) still describes Tunnel as preview-only and documents an "I want to…" chooser that does not exist; and about 5,100 lines of companion code (server provisioning, Tor, proxy chain, MAC changer, health monitor, profile_store) are unreachable from the UI and must not be treated as Sentinel promises.

## 2. Counts

Rows audited: 114 (each table row = one checkable promise).

| Status | Count |
|---|---|
| IMPLEMENTED | 77 |
| PARTIAL | 30 |
| STUB | 0 |
| MISSING | 5 |
| NOT CODE-VERIFIABLE | 2 |
| **Total rows** | **114** |

(Of the PARTIAL rows, the functional/safety defects rather than wording gaps are marked "(defect)" in the Note column: rows 38, 39, 53, 62, 66, 77, 83, 104 — row 105 shares row 104's cause. The two NOT CODE-VERIFIABLE rows, 8 and 101, are suspected defects that need a real Mac. MISSING rows: 84, 93, 94, 111, 112. No STUB: no promised control is a dead button.)

---

## 3. Traceability table

### 3.1 Connection Check (R ¶ "Tunnel's Connection Check", V §1/§How it works, T §Connection Check)

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 1 | Check is model-free: no provider, model, cost or request guard (R,V,T) | IMPLEMENTED | ui/panels/vpn.py:818-857 (no `authorize()`/`start_worker`); ui/workers.py:429-453; services/vpn_diagnostics.py:525-579 | Diagnostics worker only calls `collect_vpn_diagnostics`. |
| 2 | Read-only: never connects, disconnects, edits routes/firewall, asks for admin password (R,V,T) | IMPLEMENTED | Only subprocesses: `route -n get default` vpn_diagnostics.py:323-343; `wg show interfaces` / `latest-handshakes` / `transfer` 356-405; `pgrep -x openvpn` 346-353. No sudo/osascript in module | `wg show <if> latest-handshakes` prints peer public keys on stdout; only field[1] (timestamp) is kept (385-388). |
| 3 | Reports installed WireGuard app, wg, wg-quick, openvpn (R,T) | IMPLEMENTED | vpn_diagnostics.py:532-537 | |
| 4 | Reports detected tunnels (WG interfaces, OpenVPN process) (R,T) | PARTIAL | vpn_diagnostics.py:356-369, 346-353; agents/vpn_agent/services/wireguard_manager.py:141-158 | WG names come from `/var/run/wireguard/*.sock` (= `utunN`) plus `wg show interfaces`. OpenVPN detection is `pgrep -x openvpn` = ANY openvpn on the Mac, while the Connect gate only recognises Sentinel's tracked PID (inconsistent definition). |
| 5 | Recent WG handshake + transfer totals "when `wg` permits" (R,V,T) | IMPLEMENTED | vpn_diagnostics.py:372-405; errors surfaced 391-402, 500-502 | Degrades to "Checks that could not finish". Whether non-root `wg show` is permitted on macOS needs a real Mac. |
| 6 | Current default route (interface + gateway) (R,T) | IMPLEMENTED | vpn_diagnostics.py:323-343 | |
| 7 | Configured DNS servers (R,T) | PARTIAL | vpn_diagnostics.py:549-553 → agents/vpn_agent/services/dns_check.py:49-61 | dnspython `Resolver().nameservers` = `/etc/resolv.conf` only; macOS scoped/VPN-pushed resolvers (scutil) may be absent. On failure it returns `["Error: …"]`, which is then displayed/treated as a DNS server (dns_check.py:60-61). |
| 8 | Selected-profile comparison recognises the profile's interface; macOS utun caveat kept visible (T) | NOT CODE-VERIFIABLE | vpn_diagnostics.py:621-641 vs vpn_execution.py:55-76 | Diagnostics compares the friendly profile name (e.g. `nl`) to names from `.sock` listing (`utunN`); the execution module maps via `<name>.name` records but diagnostics does not. Risk: Connection Check says "Selected tunnel is not active" right after a *verified* Sentinel Connect. Verify on a Mac with a real tunnel started from Sentinel. |
| 9 | Profile comparison covers interface, endpoint, port, handshake, routing (R,T) | IMPLEMENTED | vpn_diagnostics.py:582-671 | |
| 10 | Endpoint/port checked only for usable values; live peer endpoint deliberately not queried (R,V,T) | IMPLEMENTED | vpn_diagnostics.py:643-657 | No network call occurs before the `include_external` branch (569-579). |
| 11 | Only non-secret profile fields enter the report; key-like/unknown fields dropped (T,V) | IMPLEMENTED | vpn_diagnostics.py:40, 47-53, 89-92, 563 | Allow-list: name, endpoint, port, interface, notes, protocol. `config_path` is therefore invisible to Compare. |
| 12 | Compare profile: "No profile comparison", saved active profile preselected, choosing does not activate/modify (V,T) | IMPLEMENTED | ui/panels/vpn.py:475-495 | No write path from the compare picker. |
| 13 | Reads live profile list else bundled seed; never seeds/edits (V,T) | IMPLEMENTED | vpn_diagnostics.py:78-101 | Live file is `~/Library/Application Support/VPN Agent/vpn_profiles.json` (agents/vpn_agent/server/paths.py:34-42, 98-107) — see rows 76, 108. |
| 14 | "Recommended next steps" card, prioritised (T) | IMPLEMENTED | vpn_diagnostics.py:674-688, 502-508 | Capped at 5. |
| 15 | Stop Check cancels and shows partial results (V,T) | IMPLEMENTED | vpn_diagnostics.py:542, 564, 574, 578; ui/workers.py:441-442; vpn.py:880-884 | Cancellation granularity = between steps; each subprocess ≤8 s. |
| 16 | "Include public IP and latency" off by default (R,V,T,U) | IMPLEMENTED | vpn.py:263-268 | |
| 17 | Separate confirmation naming `api.ipify.org` and `1.1.1.1`, default No (R,V,T) | IMPLEMENTED | vpn.py:827-838 | |
| 18 | Declining contacts neither destination (T) | IMPLEMENTED | vpn.py:837-838 (returns before worker starts) | |
| 19 | Public IP comes from api.ipify.org (T) | IMPLEMENTED | agents/vpn_agent/services/public_ip.py:25, 40-52; vpn_diagnostics.py:573 | |
| 20 | Latency to 1.1.1.1, TCP fallback when ping blocked (T) | IMPLEMENTED | agents/vpn_agent/services/latency.py:12, 25-82, 85-99, 102-139; vpn_diagnostics.py:577 | 2 pings (DEFAULT_COUNT=2); TCP/443 fallback. |
| 21 | Results state they are not proof of anonymity / full leak test (V,T) | IMPLEMENTED | vpn_diagnostics.py:509-515 | |

### 3.2 Your IP & DNS (R ¶ "Your IP & DNS", V inputs table, T §Your IP & DNS)

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 22 | Group always visible, independent of Connection Check (R,T) | IMPLEMENTED | vpn.py:133-183 | |
| 23 | Local: LAN + tunnel-interface addresses from `ifconfig`, no network contact, off UI thread (R,V,T) | IMPLEMENTED | public_ip.py:109-173; ui/workers.py:456-483; vpn.py:553-566 | `_primary_outbound_ip` `connect()`s a UDP socket to 8.8.8.8:80 (routing lookup, no packet sent). The read also runs automatically when the panel is constructed (vpn.py:183) — see (c). |
| 24 | Local readout refreshes automatically after connect/disconnect (R,V,T) | IMPLEMENTED | vpn.py:744-752 | |
| 25 | Check public IP → exit IP, location, network owner (R,V,T) | IMPLEMENTED | public_ip.py:55-106; vpn.py:571-599 | |
| 26 | IPinfo when `IPINFO_API_KEY` set, else ipapi.co (R,V,T) | IMPLEMENTED | public_ip.py:70-103 | |
| 27 | VPN/proxy/hosting flag only with IPinfo key on a plan with the privacy object; keyless ipapi.co reports none (R,V,T) | IMPLEMENTED | public_ip.py:89-94; vpn.py:548-550 | Flags also include `tor` and `relay` (not documented). Tooltip overclaims (row 34). |
| 28 | (security) IPinfo token never in URL/error text | IMPLEMENTED | public_ip.py:73-81 | Bearer header only. |
| 29 | Public IP re-checked after connect/disconnect only if already run successfully this session (V,T) | IMPLEMENTED | vpn.py:593-595, 749-751 | Latch only on a genuine success. |
| 30 | Run test = real DNS-leak test through bash.ws (R,V,T) | IMPLEMENTED | dns_check.py:115-237 (`bash.ws/id`, 12 probes, `bash.ws/dnsleak/test/<id>?json`); vpn.py:625-635; ui/workers.py:486-512 | Cancellation honoured between probes only (160-163). |
| 31 | Probe names resolved "with your configured resolvers"; "the resolver path traffic really takes" (R,T) | PARTIAL | dns_check.py:25-36 (`dns.resolver.resolve`) | Uses dnspython (reads `/etc/resolv.conf`), not `getaddrinfo`/mDNSResponder. Docstrings (lines 122-125, 129-130) still say "system's real resolver stack"/`socket.gethostbyname`. On macOS, VPN-pushed scoped DNS may not be exercised. |
| 32 | Reports which resolvers answered and whether any sit outside the tunnel's network (R,T) | PARTIAL | dns_check.py:188-225; vpn.py:603-623 | Verdict = bash.ws conclusion text match, else ASN ≠ exit-IP ASN heuristic: a public resolver reached through the tunnel (e.g. 1.1.1.1) reads as "possible leak" when its ASN differs from the VPS host. |
| 33 | Public-IP/latency checks "optional, name the external destinations, require a separate confirmation" (R last Tunnel ¶) | PARTIAL | Confirmation exists only for the Connection Check checkbox (vpn.py:827-838); `Check public IP` (571-581) and `Run test` (625-635) contact IPinfo/ipapi.co and bash.ws on a single click with no dialog | README sentence is ambiguous; docs/training do not claim a dialog for these two buttons. |
| 34 | (U) Tooltip: Check public IP "contacts api.ipify.org and IPinfo (or ipapi.co)" | PARTIAL | vpn.py:161-163 vs public_ip.py:55-106, 176-184 | `get_ip_snapshot` → `get_ip_details` never calls ipify. Tooltip is wrong; docs (R,V,T) are right. |
| 35 | (U) Tooltip: public label shows "whether it is flagged as a VPN/proxy/hosting range" | PARTIAL | vpn.py:156-158 | True only with an IPinfo key on a privacy plan (row 27). |

### 3.3 VPN Connection — Connect / Disconnect gate (R ¶ "VPN Connection", T §VPN Connection (live))

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 36 | WireGuard Connect runs `wg-quick up` (R,T) | IMPLEMENTED | services/vpn_connection.py:83-95, 112-146; agents/vpn_agent/services/privileged.py:39-67 | **Exact argv chain:** script = `PATH='<wg-quick dir>':"$PATH" '<abs wg-quick>' up '<config path or interface>'`; run as (a) `bash -c <script>` if euid 0 (privileged.py:47-48); else (b) `sudo -n bash -c <script>` (50); else (c) `osascript -e 'do shell script "<script, \ and " escaped>" with prompt "Sentinel needs administrator access to start the VPN." with administrator privileges'` (59-67). Timeout 30 s for the whole call. |
| 37 | WireGuard Disconnect runs `wg-quick down` (R,T) | IMPLEMENTED | vpn_connection.py:149-168 | Same chain, prompt text "…to stop the VPN." |
| 38 | OpenVPN Connect (R,T) | PARTIAL | services/openvpn_manager.py:71-109 (script 97-103) | (defect) **Exact script:** `openvpn --config '<cfg>' --daemon sentinel-ovpn --log '<user_data>/vpn/openvpn.log' --writepid '<user_data>/vpn/openvpn.pid' --verb 3` — bare `openvpn` with no absolute path/PATH prefix, unlike wg-quick (vpn_connection.py:83-95 explains osascript/`sudo` secure_path drop Homebrew's bin; `openvpn` lives in `/opt/homebrew/sbin`). Likely "command not found" on Homebrew Macs (needs a real Mac to confirm; asymmetry is certain). `auth-user-pass` unsupported (docs/honesty_audit.md:160-161 only). |
| 39 | OpenVPN Disconnect stops only the tracked PID (R,T) | PARTIAL | openvpn_manager.py:136-154 (`kill -TERM <pid>` via run_as_root), 112-133 | (defect) See row 62: in packaged/portable builds identity check always fails → Disconnect always refuses. |
| 40 | Target review names exact target and command (R,T) | PARTIAL | vpn_execution.py:214, 142-151 (WG exact); 276-277 (OpenVPN) | OpenVPN connect shows an elided command (`openvpn --config '…' --daemon sentinel-ovpn …`, `--log/--writepid/--verb` hidden); OpenVPN disconnect shows `kill -TERM <pid of Sentinel's tracked openvpn>` even though the PID is knowable. |
| 41 | Review shows config's non-secret routing and DNS intent, keys discarded (R) | PARTIAL | vpn_execution.py:226-249 | Only for WireGuard profiles with `config_path`, on Connect. None for OpenVPN (no inspector; V:76), none for interface-only WG profiles (starter catalog "Home Server"/"GL-iNet Flint 2"), none on Disconnect. `test_review_never_carries_key_material` covers the WG path (tests/test_vpn_execution.py:93). |
| 42 | Review shows warnings (R,T) | IMPLEMENTED | vpn_execution.py:244 (config warnings), 247-249 (split), 250-257 (world-readable config), 262-264, 297-303 (disconnect: clear traffic / armed kill switch) | |
| 43 | Review shows rollback steps (R,T) | IMPLEMENTED | vpn_execution.py:265-272, 288-291, 305 | WG rollback is a copy-pasteable `sudo wg-quick …`. |
| 44 | Blocker: template profile (R,T) | IMPLEMENTED | vpn_execution.py:208-210; vpn.py:671-676; vpn_connection.py:52-70, 115-116 | Triple layer: panel, review, service. Endpoints `""`, `0.0.0.0`, `<server_ip>`, `<server_public_ip_or_ddns>`, `server.example.com`, `example.com` or `placeholder: true`. Disconnect deliberately not blocked for templates (test_vpn_execution.py:145). |
| 45 | Blocker: missing `wg-quick` (R) | IMPLEMENTED | vpn_execution.py:216-218 | |
| 46 | Blocker: missing `openvpn` (R) | IMPLEMENTED | vpn_execution.py:279-281 | |
| 47 | Blocker: missing config file (R) | PARTIAL | vpn_execution.py:226-231, 284-285 | Checked only when `config_path` is set and only on Connect. Interface-only WG profiles (starter catalog) resolve to `/etc/wireguard/<iface>.conf` (or Homebrew path) at wg-quick time → admin prompt, then failure. |
| 48 | Blocker: config name `wg-quick` would reject (R) | PARTIAL | vpn_execution.py:44, 221-225 | Stem regex only; `.conf` suffix (also required by wg-quick) not checked; differs from `SAFE_INTERFACE` (vpn_diagnostics.py:38): allows leading `-`/`.`/`_` and `+`. Interface-only target `'-x'` would be passed to wg-quick as an option. |
| 49 | Blocker: interface already up (R) | IMPLEMENTED | vpn_execution.py:258-261 (WG via `.name`/`.sock` records 55-76); 286-287 (OpenVPN) | OpenVPN part sees only the tracked process (and is broken by row 62). |
| 50 | A blocked action runs nothing, no admin prompt (R,T) | IMPLEMENTED | vpn.py:699-707; vpn_execution.py:486-488 | |
| 51 | Review is the confirmation dialog; default No; Disconnect also confirms (R,T) | IMPLEMENTED | vpn.py:696-711, 689; vpn_execution.py:124-140 | `QMessageBox.question(..., Yes+No buttons, default No)`. |
| 52 | Execution tab opens with the review before the question (T) | IMPLEMENTED | vpn.py:696-698 | |
| 53 | Privileged run is "through the macOS authorisation dialog" (R); "macOS asks for your password" (T) | PARTIAL | privileged.py:47-67 | (defect-ish) Order is euid 0 → `sudo -n` (cached credentials, **silent, no dialog**) → osascript. Fallback to osascript fires for any failure whose output contains "password" or "sudo" (line 55), which would re-run a failed `wg-quick`/`openvpn` whose output merely mentions those words. 30 s `subprocess` timeout (22-36, 39) covers typing the password AND the command. |
| 54 | Runs off the interface thread (R,T) | IMPLEMENTED | vpn.py:720-728; ui/workers.py:392-426 | |
| 55 | Review re-run in the worker so a file changed after the dialog is caught (vpn_execution.py docstring) | IMPLEMENTED | vpn_execution.py:484 | Path is still passed to root by name (small TOCTOU between review and `wg-quick`). |
| 56 | Post-change check re-reads `/var/run/wireguard` records (R) | IMPLEMENTED | vpn_execution.py:55-76, 328-357 | Needs macOS to confirm `.name`/`.sock` layout and readability (module comment admits `.name` may be unreadable). |
| 57 | Post-change check re-reads Sentinel's tracked OpenVPN process (R) | PARTIAL | vpn_execution.py:358-370 | Process existence only; 3 probes × 0.5 s ≈ 1 s window, so a slow `kill -TERM` exit reads "still running"; wrong in packaged/portable builds (row 62). |
| 58 | For a full tunnel, post-check reads which interface the internet route uses (R) | IMPLEMENTED | vpn_execution.py:79-93, 346-356 | `route -n get 1.1.1.1` (no packets). "Full tunnel" = literal `0.0.0.0/0` or `::/0` string match (config_inspection.py:71-77): `0.0.0.0/1,128.0.0.0/1` reads as split, `::/0` alone reads as full. |
| 59 | Result reported as verified / not verified in an Execution tab (R,T) | IMPLEMENTED | vpn_execution.py:424-457; vpn.py:362-363, 730-737 | |
| 60 | Post-change check does not claim handshake/DNS/leak verification (R,T) | IMPLEMENTED | vpn_execution.py:138-139, 356, 424-435 | OpenVPN "verified" means only "a process exists". |
| 61 | Rollback guidance accompanies every review and outcome (T) | IMPLEMENTED | vpn_execution.py:161-162, 469 | |
| 62 | OpenVPN identity: refuse unless the PID is Sentinel's own openvpn (R; honesty_audit) | PARTIAL | openvpn_manager.py:112-133 | (defect) `ps -p <pid> -o command=` prints argv joined by spaces; `shlex.split` then breaks `--writepid /Users/x/Library/Application Support/Sentinel/vpn/openvpn.pid` (frozen) and `…/Sentinel Data/vpn/…` (portable) so `args[idx+1] != str(pid_file())` → `False` always. Consequences: Disconnect always refuses; `is_running()` False → duplicate Connect not blocked; post-check "not verified". Tests only use `/tmp/sentinel.pid` (tests/test_vpn_connection.py:187-201). `ps` truncation without `-ww` also unverified on macOS. |

### 3.4 Audit log (R, T stage 5)

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 63 | Audit file is `data/logs/tunnel_audit.jsonl` (R) | IMPLEMENTED | vpn_execution.py:48, 376-378 | `user_data_base()/data/logs`: dev=`<repo>/data/logs`; frozen=`~/Library/Application Support/Sentinel/data/logs`; portable=`Sentinel Data/data/logs` (erased by Emergency Reset). |
| 64 | Audit file mode 600 (R) | IMPLEMENTED | vpn_execution.py:388-391; asserted tests/test_vpn_execution.py:240 | `chmod` happens after the first write (file briefly created with default umask); `OSError` swallowed, so on exFAT (recommended for portable drives, docs/portable_mode.md) the mode silently stays permissive. |
| 65 | Audit contains no key material (R) | PARTIAL | vpn_execution.py:499-514 (fields), 493-496 and 513 (`output`/`error` ≤2000 chars) | Logged fields are non-secret, but `error` and the Execution "Tool output" section carry raw `wg-quick`/`wg` stderr. wg's `setconf` parse errors quote the offending value (e.g. a malformed `PrivateKey`/`PresharedKey`). Not verified against a live tool; add a canary test. Config path (username) and profile name are logged. |
| 66 | Every attempt, "refused ones included", is audited (R,T) | PARTIAL | vpn_execution.py:486-515 (only inside `execute()`); vpn.py:671-676, 699-707, 711 | (defect) UI refusals (template, review blockers) and a declined confirmation return before `execute()` is ever called → **no audit line**. A refusal is logged only if the review changes between dialog and worker. `test_a_blocked_action_runs_nothing_and_is_audited` calls `execute()` directly, so the UI gap is untested. |
| 67 | Every kill-switch change is audited (R) | PARTIAL | ui/workers.py:414-419 → vpn_execution.py:401-407 | Exceptions raised inside arm/disarm go to `error_signal` (workers.py:425-426) with no audit line; `arm()` writes its marker after pf is already loaded (killswitch.py:360) so an `OSError` there leaves pf armed with no record. `detail` keeps the full message incl. recovery command (≤2000 chars). |

### 3.5 OpenVPN shutdown, import, templates

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 68 | OpenVPN shutdown never stops processes by name (R) | IMPLEMENTED | openvpn_manager.py:136-154; grep for pkill or killall in live code = none | `pgrep -x openvpn` appears only in read-only diagnostics (vpn_diagnostics.py:351). `pkill -f "openvpn --cd"` exists only in unreachable server/bootstrap.py:627. |
| 69 | OpenVPN shutdown refuses when Sentinel cannot identify its tracked process (R) | IMPLEMENTED | openvpn_manager.py:140-145 | Gate does not pre-block (vpn_execution.py:290-291): the user is shown a Yes/No for an action that will then fail; audited as "failed" not "refused". |
| 70 | Import config… loads `.conf`/`.ovpn`, reads the real endpoint, stores a connectable profile (R,T) | IMPLEMENTED | vpn_connection.py:238-286, 289-303; vpn.py:645-662 | First `Endpoint`/`remote` only; IPv6 handled; no endpoint → marker `"imported"` (not a placeholder, so Connect is allowed and kill switch later refuses). A `.conf` that is really OpenVPN is typed WireGuard and is blocked later by "no peer endpoint". Imports keep the original path (`config_path`), not a copy. |
| 71 | Import selects the new profile (T) | IMPLEMENTED | vpn.py:657-662 | |
| 72 | Example country profiles ship as explicit templates marked "(template)" (R,T) | IMPLEMENTED | vpn_connection.py:306-333; vpn.py:498-516 | Five templates (NL, US, JP, DE, CH). Notes text says "or provision your own server" — no provisioning is reachable from Sentinel (see 3b). |
| 73 | Status line starts at "Connection state not checked" (T) | IMPLEMENTED | vpn.py:109 | |
| 74 | Amber banner states the boundary (T) | IMPLEMENTED | vpn.py:68-79 | |
| 75 | Tunnel never stores the admin password (T) | IMPLEMENTED | privileged.py (no persistence) | |

### 3.6 Kill switch (R, T §Kill switch, U)

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 76 | Optional pf kill switch, Arm/Disarm (R,T,U) | IMPLEMENTED | vpn.py:114-128, 759-816; services/vpn_connection.py:185-209; agents/vpn_agent/services/killswitch.py:314-392 | State under `~/Library/Application Support/VPN Agent/` (`killswitch.pf`, `killswitch.armed`) via server/paths.py — outside Sentinel Data/portable root. |
| 77 | "Blocks all traffic except the selected tunnel's endpoint" (R,T,U, arm dialog vpn.py:778-779) | PARTIAL | vpn_connection.py:202 (`ks.arm([endpoint], allow=[])`); killswitch.py:170-248 (loop 218-228) | (defect) With `allow=[]` no UDP/TCP pass rule to the endpoint is generated — only `pass out quick … proto icmp to <endpoint>` (227-228). The tunnel's own packets are blocked except pre-existing pf states; reconnect/re-arm-before-connect fails. Rules also leave open: loopback, DHCP, RFC1918 + link-local LAN, and the utun device present at arm time (so not "all except endpoint"). `tests/test_vpn_killswitch.py:77-83` asserts `allow == []` (locks it in); `build_rules` has no test. Real pf behaviour needs a Mac. |
| 78 | Refuses to arm for a template (R,T) | IMPLEMENTED | vpn.py:769-774; vpn_connection.py:199-200 | |
| 79 | Refuses to arm when the endpoint cannot be resolved (R; T says "cannot be exempted") | IMPLEMENTED | killswitch.py:125-164, 330-335 | Refuses only if NO endpoint resolves; `"imported"` marker refuses; hostnames resolved once at arm time. |
| 80 | Reports its recovery command on failure (R) | PARTIAL | killswitch.py:87-95, 356-358 (arm fail), 391 (disarm fail), 372-375 (success); UI vpn.py:807-811 | Service text includes `sudo pfctl -a vpn-agent-killswitch -F all && sudo pfctl -F all -f /etc/pf.conf`, but the panel shows `message[:300]` (fail) / `[:200]` (success) in a non-wrapping `QLabel`, so the trailing recovery line is clipped when pfctl output is long. Full text survives only in the audit log. |
| 81 | Needs administrator password (R,T,U) | IMPLEMENTED | killswitch.py:430-453 | Same `sudo -n` first / osascript fallback as row 53. |
| 82 | Disarm "restores ordinary traffic" (T) | PARTIAL | killswitch.py:378-392 | Flushes only the anchor's rules. `pfctl -E` (line 352) is never released, and the `/etc/pf.conf` anchor block plus `/etc/pf.conf.vpn-agent.bak` (405-424) are never removed — the main ruleset was rewritten and reloaded at first arm. Not mentioned in R/T/dialog. |
| 83 | Arm confirmation (R,T implicit) | PARTIAL | vpn.py:775-782 | (defect) `QMessageBox.question` with Yes+No and no default button → Enter = Yes, unlike Connect/Disconnect (default No). Dialog omits the `/etc/pf.conf` edit and the recovery command. Disarm has no confirmation (acceptable). |
| 84 | Kill switch for the "selected tunnel" including OpenVPN profiles (R,T generic) | MISSING | killswitch.py:101-122 (`active_tunnel_interfaces` reads only `/var/run/wireguard`); vpn.py:768-774 checks only "template" | Arming with an OpenVPN profile blocks the OpenVPN tun device too (no pass rule). UI neither refuses nor warns. |

### 3.7 Action Preview and Config Inspection

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 85 | Action Preview shows commands without running anything (R,V,T) | IMPLEMENTED | vpn_diagnostics.py:691-769 (pure string assembly, no subprocess); vpn.py:926-937 | |
| 86 | Connect/Disconnect/Restart; WireGuard only; OpenVPN, unknown protocol, unsafe/missing interface → no command (V,T) | IMPLEMENTED | vpn_diagnostics.py:697-728, 38 | |
| 87 | Preview names profile+interface, effects, checks, copyable commands (T) | IMPLEMENTED | vpn_diagnostics.py:142-158, 738-760 | |
| 88 | Disconnect preview warns traffic may resume without a kill switch (T) | IMPLEMENTED | vpn_diagnostics.py:743-746 | |
| 89 | Preview equals what the real action will do | PARTIAL | vpn_diagnostics.py:730-737, 754 vs vpn_connection.py:95 | Preview: `sudo wg-quick up <iface>` + "confirm /etc/wireguard/<iface>.conf exists". Real Connect: absolute `wg-quick`, PATH prefix, and the imported config **path**. Preview also reads the Compare picker (stripped profile without `config_path`, vpn.py:926-931), not the Server picker. |
| 90 | Inspect config reads one explicitly chosen WG file locally and discards PrivateKey/PresharedKey at parse time (R,V,T) | IMPLEMENTED | config_inspection.py:80-168 (secret lines `continue` at 123-125; allow-list extraction 126-137); vpn.py:886-907 | Key names normalised (case/space/underscore) at 121. Summary can only contain address/DNS/MTU/endpoint/AllowedIPs values. |
| 91 | Inspection bounded to 1 MiB, does not resolve endpoint, not sent to model/Saved Chats/run log (V,T) | IMPLEMENTED | config_inspection.py:16, 85-90; no network/model imports | Warnings contain line numbers and generic text only (115-153). |
| 92 | Compares file's full/split routing and DNS with last Connection Check (R,V,T) | IMPLEMENTED | vpn_diagnostics.py:218-288 | |
| 93 | (not promised, but implied safe) Config hooks `PreUp/PostUp/PreDown/PostDown/SaveConfig` that `wg-quick` runs as root | MISSING | config_inspection.py:126-137 ignores unknown keys silently; vpn_execution.py review never mentions hooks | A user can import a `.conf` containing `PostUp = <anything>` and the review/confirmation shows only routing/DNS. Same for OpenVPN `up`/`script-security`/`plugin`. |
| 94 | OpenVPN config inspection (V "Extend it": not delivered) | MISSING | docs/agents/vpn.md:76; no `.ovpn` inspector | Documented as not present. |

### 3.8 Self-hosted VPN design (V, T §Deployment choices/Config Builder/Advisor)

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 95 | Build Config is deterministic, offline, no LLM/network/crypto (R,V,T) | IMPLEMENTED | agents/vpn_agent/sentinel_chat_agent.py:179-247; vpn.py:940-952 | Pure string assembly. |
| 96 | Remote = full tunnel; Native = split tunnel default, native does not hide IP (V,T) | IMPLEMENTED | sentinel_chat_agent.py:91-98, 147-162, 194-206 | LAN subnet default `192.168.1.0/24`. |
| 97 | Keys are clearly marked placeholders beside `wg genkey` commands (V,T) | IMPLEMENTED | sentinel_chat_agent.py:79, 85, 103, 108, 116-122 | |
| 98 | Inputs map: host→Endpoint, LAN→AllowedIPs, egress→MASQUERADE (default eth0), SSH user→runbook (V) | IMPLEMENTED | sentinel_chat_agent.py:73-113, 125-144; vpn.py:942-949 | Values are interpolated unvalidated into displayed commands (display-only). |
| 99 | Placeholders for unset host/IP (`<SERVER_PUBLIC_IP_OR_DDNS>`, `<SERVER_IP>`) (V) | IMPLEMENTED | sentinel_chat_agent.py:92, 126, 169 | |
| 100 | Optional OpenVPN TCP/443 fallback (R,V) | PARTIAL | sentinel_chat_agent.py:230-241 | A 5-line outline (comments for server.conf); no client `.ovpn`/PKI. Choosing "OpenVPN 443 fallback" alone still emits the WireGuard server+client configs. |
| 101 | (remote mode) macOS kill-switch pf snippet (V) | NOT CODE-VERIFIABLE | sentinel_chat_agent.py:165-176, 243-245 | Differs from the real rules and looks unparseable: `pass on utun+ all` (real code deliberately avoids passing every utun, killswitch.py:51-54) and `pass out to <SERVER_IP> port 51820` has no `proto`. Validate with `pfctl -n -f` on macOS. |
| 102 | Advisor uses a structured prompt: remote vs native, WG vs OpenVPN-443, kill switch, DNS/IPv6/WebRTC; answers in 5 sections (V,T) | IMPLEMENTED | sentinel_chat_agent.py:19-59; vpn.py:400-432 | Prompt text is present; whether a model complies needs a live provider. Prompt also advertises Tor/proxy chains/MAC randomisation/stunnel/onion which Sentinel cannot do (see (c)). |
| 103 | Advisor is gated by the request guard; setup (mode/protocol/host/LAN) passed as context (V,T) | IMPLEMENTED | vpn.py:388-398, 418 | SSH user and egress iface are not included; route should be reviewed before sending host details to a cloud model (T says so). |

### 3.9 Lifecycle, packaging, privacy

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 104 | Normal app close joins active Tunnel workers (R,V,T) | PARTIAL | vpn.py:909-923; main.py:4778-4788; ui/dialogs.py:52-60 | (defect) `shutdown()` calls `worker.cancel()` on `_connection_worker` (915) but `VpnConnectionWorker` (ui/workers.py:392-426) has no `cancel()` → `AttributeError` whenever a Connect/Disconnect/Arm/Disarm is in flight; later panels' shutdown is skipped, and the `wait()` loop is never reached. Diagnostics/IP/DNS workers are fine (tests/test_ui_panels.py:3102-3109). Test fake `isRunning()` is always False (test_ui_panels.py:1290-1309). Also: after a 2 s timeout `terminate()` would hard-kill a thread inside a privileged `subprocess.run`. |
| 105 | Portable Emergency Reset finishes the active check before erasing (R,V,T) | PARTIAL | ui/dialogs.py:1100-1128 | Same cause as row 104; the handler catches only `OSError`/`PortableRuntimeError`, so the `AttributeError` aborts the reset silently (no dialog). |
| 106 | Packaged builds include the non-secret starter profile catalog (R,V) | IMPLEMENTED | Sentinel.spec:47; vpn_diagnostics.py:31-37, 81 | Frozen import of `agents.vpn_agent.*` (no `agents/vpn_agent/__init__.py`, namespace package; killswitch/privileged imported lazily) not exercised here — needs a PyInstaller build. |
| 107 | Never requests private key material (R,T) | IMPLEMENTED | grep for showconf / PrivateKey in live code: only config_inspection.py and docs text | Import (vpn_connection.py:246) and Inspect (config_inspection.py:92) read the whole file into memory but retain only non-secret fields; root `wg-quick` reads the key itself. |
| 108 | Imported profiles / kill-switch state are Sentinel-local and removed by Emergency Reset (R portable language; T "saves the profile locally") | PARTIAL | vpn_connection.py:289-303; server/paths.py:34-42; killswitch.py:83-84, 303, 360 | Written to `~/Library/Application Support/VPN Agent/` even in portable mode; Emergency Reset (portable_reset.py) does not touch it. Audit log and OpenVPN pid/log (openvpn_manager.py:39-50) are inside Sentinel data. |
| 109 | None of these paths incurs model cost (R) | IMPLEMENTED | vpn.py (only `run()` calls `authorize`) | Advisor is the only paid path. |
| 110 | (A) Sidebar tooltip/subtitle/description describe Tunnel | PARTIAL | services/agent_catalog.py:76-78 | "Profile-aware connection checks, safe action previews, and self-hosted VPN design" omits real Connect/Disconnect, Import, kill switch (R table row says "Real WireGuard/OpenVPN connect"). |
| 111 | (T) Tooltips explain every Tunnel control / ui/tooltips.py holds Tunnel tooltips | MISSING | grep `vpn` in ui/tooltips.py → none | ~17 inline tooltips exist (vpn.py:90-264, 307-309). None on Connect, Disconnect, Disarm, Protocol, Server host, SSH user, LAN subnet, Egress iface, Check Connection, Stop Check, Preview, Clear. |
| 112 | (V) "I want to…" chooser hides irrelevant controls (V inputs table; V Outputs "Workflow chooser") | MISSING | grep for "I want to", "workflow chooser", "Workflow" in ui/panels/vpn.py and ui/widgets.py → no match | All groups are always visible; only an "Advisor" chip exists (vpn.py:337). Training screenshot (docs/training/images/tunnel.png) also shows no chooser. |
| 113 | (T) Each result card has a Copy action | IMPLEMENTED | ui/widgets.py:~680-698 (`SectionCard` Copy) | |
| 114 | (T) Training screenshots show the current UI | PARTIAL | docs/training/images/tunnel.png | Shows `v2.001` and no "Your IP & DNS" group (VERSION is 2.002). |

---

### 3b. Reachability map: live vs dead companion code (not Sentinel promises)

Reachable from the Sentinel UI/app (verified by grepping every `vpn_agent` reference outside `agents/vpn_agent/` and `tests/`):

- `agents/vpn_agent/sentinel_chat_agent.py` (`VpnAgent`, `build_configs`) — main.py:72, 297; ui/panels/vpn.py:25
- `services/config_inspection.py`, `latency.py`, `public_ip.py` (`get_public_ip`, `get_ip_details`, `get_ip_snapshot`, `get_local_addresses`), `dns_check.py` (`get_system_dns_servers`, `run_dns_leak_test`), `wireguard_manager.py` (`is_wg_available`, `is_wg_quick_available`, `list_active_tunnels` only), `privileged.py` (`run_as_root`), `killswitch.py` (via `vpn_connection._killswitch()`), `server/paths.py`
- Sentinel-native: services/vpn_diagnostics.py, vpn_connection.py, vpn_execution.py, openvpn_manager.py

**Dead from the UI (no import path; treat as companion code, not promises):**

- `agents/vpn_agent/server/{backup,bootstrap,cli,deploy,export,keys,model,obfuscation,pki,provision,render,store}.py` (~3,800 lines: SSH deploy, key/PKI generation, stunnel/onion obfuscation; `bootstrap.py:627` contains `pkill -f "openvpn --cd"` in a remote-host script — never run by Sentinel).
- `agents/vpn_agent/services/{tor,proxychain,socks_client,macaddr,profile_store,health_monitor}.py` (~1,360 lines).
- Unused functions inside reachable modules: `wireguard_manager.{get_tunnel_status,connect,disconnect,restart}` (they `subprocess.run(["sudo", "wg-quick", …], timeout=30)` and would block a GUI thread on a password prompt — keep dead or delete), `dns_check.{check_dns_leak,resolve_test_domain,KNOWN_PUBLIC_RESOLVERS}`, `openvpn_manager.read_log` (the OpenVPN log is written but never shown), `vpn_connection.connection_status`.
- `agents/vpn_agent/config/settings.json` has no reader (latency target, ip URLs, log path are hard-coded constants instead).
- Consequence for docs: README.md:213 ("`server/` provisioning") and the template-profile/"or provision your own server" strings (vpn_connection.py:104-106, 326-330) imply a capability Sentinel does not expose.

---

## 4. Cross-document findings

### (a) Promises present in one place but not the other

**In UI / README / training but absent from docs/agents/vpn.md** (the file the in-app **Agent guide** button opens, main.py:4535-4550):
- The entire live path: VPN Connection (live) group, Server picker, Import config…, Connect/Disconnect, target review, confirmation (default No), Execution tab, audit log, template refusal, OpenVPN tracked-PID rule, kill switch Arm/Disarm, amber banner, "Connection state not checked", post-change verified/not-verified.
- vpn.md lists "five paths" and five tabs; the UI has an additional **Execution** tab and a live group. vpn.md's Under-the-hood table omits services/vpn_connection.py, vpn_execution.py, openvpn_manager.py and killswitch.py.
- The Your IP & DNS readout *is* documented in vpn.md (inputs table).

**In docs/agents/vpn.md but not in README/training (or not in code):**
- "I want to…" workflow chooser (V:36, V:68) — not in code (row 112).
- Remote-vs-Native comparison table, per-field Inputs table (SSH user/egress iface), Advisor 5-section output format, "Extend it" roadmap (real keys, OpenVPN inspection, "Controlled execution: if execution is added later"), "Verification state" ("26 focused Tunnel tests"), Restart preview.
- README never describes Advisor or Build Config beyond the roster row.

**In training but not README (or vice-versa):**
- Only README: audit file *mode 600 / no key material*; "OpenVPN shutdown refuses… never by name"; "Import config reads its real endpoint"; "packaged builds include a starter catalog". Training mentions the audit file but not mode/keys, and is silent on OpenVPN PID tracking and on the kill-switch recovery command.
- Only training: the six-stage gate list, "Treat arming, connecting, and verifying no-leak as three separate steps", 30-minute time box, exercises, "Copy an individual card".
- README says the kill switch refuses "when the endpoint cannot be resolved"; training says "cannot be exempted" (stronger than the code, which only checks resolvability).

**UI tooltips vs docs:** Check-public-IP tooltip names ipify (row 34); public label tooltip overclaims flags (row 35); kill-switch tooltip/dialog claim "all but the endpoint" (row 77); `ui/tooltips.py` has no Tunnel keys (row 111).

### (b) Contradictions

1. **README "Tunnel … real connect" vs stale docs that still say preview-only:** docs/agents/vpn.md:12, 57-58, 77 ("if execution is added later"); docs/testing_roadmap.md:63 ("non-executing Connect/Disconnect/Restart preview") and :79 ("exposes read-only checks and previews, not the companion's privileged actions"); docs/roadmap.md:28 and :48 (V3 item "Add Tunnel's separately confirmed WireGuard execution"); SUGGESTIONS.md:29 (#19 "PLANNED"); services/agent_catalog.py:76-78; docs/training/tunnel.md "follow up with the standalone VPN Agent's deeper checks" (stale), "Store private keys only in the VPN Agent's protected state location" (Sentinel generates no keys).
2. **"Every attempt, refused ones included" (R,T) vs code:** UI refusals never reach `execute()` (rows 50, 66).
3. **"Through the macOS authorisation dialog" (R,T) vs privileged.py:** cached `sudo -n` runs silently first (row 53).
4. **Kill switch "blocks all traffic except the selected tunnel's endpoint" (R,T,U) vs killswitch.py:** LAN, DHCP, loopback and tunnel device stay open, and the endpoint is exempt for ICMP only (row 77).
5. **Check-public-IP tooltip "api.ipify.org" vs docs/code "IPinfo or ipapi.co"** (row 34).
6. **Kill switch "survives"/state vs Portable promise:** docs/portable_mode.md says nothing leaves `Sentinel Data`; profiles and kill-switch state go to `~/Library/Application Support/VPN Agent` (row 108).
7. **tests/manual_test_cases.md:180 "The request is logged under `vpn` and Stop works" for Build Config:** `build_config()` (vpn.py:940-952) creates no run-log request and has no Stop.
8. **"26 focused Tunnel tests" (vpn.md:81) vs repo:** test_vpn_connection (17), diagnostics (14), execution (24), ip_readout (6), killswitch (12), public_ip (6), dns_check (6) + panel tests.
9. **Kill-switch module claims "rules live in a private anchor, never the main ruleset" (killswitch.py:14-17) vs behaviour:** `arm()` appends an anchor to `/etc/pf.conf` and reloads the main ruleset (405-424).
10. **User-facing strings that say the VPN module is not merged:** vpn_connection.py:195-196, 207 ("until the VPN module is merged into Sentinel (pending cleanup)") vs README (merged in-tree).
11. **dns_check docstring vs code** (system resolver/`socket.gethostbyname` vs dnspython) — row 31.
12. **Training screenshot v2.001, no Your IP & DNS group** vs shipped UI (row 114).

### (c) Undocumented behaviour

- **Privilege path:** euid-0 direct run; `sudo -n` cached-credential fast path; osascript fallback triggered by any output containing "password"/"sudo"; 30 s timeout shared by password entry and command.
- **Kill switch side effects:** appends an anchor to `/etc/pf.conf` (backup `/etc/pf.conf.vpn-agent.bak`), reloads `/etc/pf.conf`, runs `pfctl -E`, never reverts on Disarm; pins the utun present at arm time (a re-created tunnel on a different utun is blocked); keeps LAN/DHCP/loopback/link-local open; not disarmed on app quit or Emergency Reset; marker + rules in `~/Library/Application Support/VPN Agent/`; no OpenVPN support.
- **Profile storage:** Import writes into the standalone app's `vpn_profiles.json`; on a fresh machine the file is created containing only the imported profile (no seed copy, vpn_connection.py:289-303), so the starter profiles and `active_profile` vanish from both pickers after the first import. Same-name import silently replaces.
- **Starter catalog is connectable:** "Home Server" (192.168.1.1, wg0), "GL-iNet Flint 2" (192.168.8.1, wg1) are not templates; the first entry is the default selection, and Connect runs `wg-quick up wg0` against whatever `/etc/wireguard/wg0.conf` exists, with no config review (rows 41, 47).
- **Panel construction runs `ifconfig`** (and a route lookup to 8.8.8.8) automatically at launch (vpn.py:183).
- **Check public IP / Run test** need no confirmation; privacy flags include `tor`/`relay`; DNS test sends 12 probe lookups + 2 HTTPS calls to bash.ws with UA `Sentinel-OSINT/2.0`.
- **Hooks run as root:** `PreUp/PostUp/PreDown/PostDown/SaveConfig` (WireGuard) and `up/down/script-security/plugin` (OpenVPN) execute under root when the user clicks Yes; review/inspection ignore them.
- **OpenVPN limitations:** `auth-user-pass` without a file, pushed-DNS handling, status polling not supported (honesty_audit.md:160-161 only); the OpenVPN log is written (`<data>/vpn/openvpn.log`, root-owned) but never surfaced.
- **Diagnostics vs gate use different OpenVPN definitions** (`pgrep -x openvpn` vs tracked PID).
- **Tool output** (≤2000 chars) is shown in the Execution tab and stored in the audit log.
- **Advisor system prompt** treats Tor, proxy chains, MAC randomisation and stunnel/onion obfuscation as ground truth although Sentinel exposes none of them.
- **Disconnect is not blocked for templates** (deliberate, so a stuck tunnel can come down) and does not check that the config file still exists.
- **Imported file is referenced, not copied**; moving/deleting it later produces a "no longer exists" blocker.
- **`VpnPanel.is_running()` ignores the connection worker**, so the window-level Stop/agent-switch logic does not see an in-flight Connect.
- **Post-connect `Check public IP` auto-refresh** runs immediately on completion (before DNS/route settle).
- **Test-coverage gaps:** `killswitch.build_rules/arm` untested in-tree; `_is_tracked_process` untested with spaces; `VpnConnectionWorker` never exercised through `VpnPanel.shutdown`; no test that a UI refusal is audited.

---

## 5. Must fix before the test phase

Severity: **S1** = a stated promise is false or unsafe in shipped builds; **S2** = doc/UX that will mislead testers.

1. **S1 — Kill switch does not exempt the tunnel endpoint:** pass the profile's real `(proto, port)` (`("udp", port)` for WireGuard, `("tcp"/"udp", port)` for OpenVPN) as `allow` in `vpn_connection.arm_killswitch`, add `build_rules` unit tests asserting the UDP/TCP pass line, and refuse/warn when arming for OpenVPN (or add its tun device).
2. **S1 — `VpnConnectionWorker` has no `cancel()`:** add `cancel()` (flag/no-op) so `VpnPanel.shutdown()` joins it, never `terminate()` a thread inside a privileged call, and catch `Exception` (not only OSError) in the Emergency Reset handler; add a test with a real running worker.
3. **S1 — OpenVPN tracked-PID check fails for any path containing a space:** compare against the raw command string (`ps -ww -p PID -o command=`, test `f"--writepid {pid_file()}"` as a substring) or keep pid/log in a space-free directory, and add a test using `Application Support`/`Sentinel Data` paths.
4. **S1 — OpenVPN script uses bare `openvpn`:** build the command with `shutil.which("openvpn")` absolute path and a PATH prefix exactly as `wg_quick_command` does, and show the full command (not `…`) in the review.
5. **S1 — Refused attempts are not audited in the UI path:** call `vpn_execution.append_audit` (outcome "refused"/"declined") from `connect_vpn`/`_review_and_confirm` before returning, or change README/T to say only executed attempts are logged.
6. **S1 — Root-executed config hooks are invisible:** detect `PreUp/PostUp/PreDown/PostDown/SaveConfig` (WG) and `up/down/script-security/plugin` (OpenVPN) in `config_inspection`/review and show them as red warnings requiring an explicit second confirmation.
7. **S1 — Tool output may quote key material into the audit log/Execution tab:** redact base64-44 / long-token strings from `output`/`error` before display and `append_audit`, and add a truncated-PrivateKey canary test.
8. **S1 — "Macos authorisation dialog" is not what always happens:** either drop the `sudo -n` fast path for Sentinel's gated actions or document it everywhere; tighten the osascript fallback to the exact "a password is required" message so a failing command is never re-run.
9. **S2 — Arm confirmation:** default to No, state the `/etc/pf.conf` edit and the recovery command, and show the full result (recovery line included) in a wrapped label or the Execution tab; on Disarm release pf (`pfctl -X <token>`) or document that it stays enabled.
10. **S2 — Rewrite docs/agents/vpn.md** (the in-app Agent guide) for the live path, remove the nonexistent "I want to…" chooser, drop "controlled execution if added later"/"26 tests", and update docs/testing_roadmap.md:63/79, docs/roadmap.md:28/48, SUGGESTIONS.md:29, agent_catalog tooltip/subtitle, and refresh docs/training/images/tunnel.png (still v2.001).
11. **S2 — Tooltips:** remove "api.ipify.org" from the Check-public-IP tooltip, qualify the "flagged as VPN/proxy" tooltip (IPinfo key + privacy plan), add tooltips for Connect/Disconnect/Disarm/Check Connection/Preview/Clear, and either add `vpn.*` keys to ui/tooltips.py or state that inline is the convention.
12. **S2 — Storage location:** move profiles and kill-switch state under `user_data_base()` (so portable mode and Emergency Reset cover them) or document `~/Library/Application Support/VPN Agent`; make `save_profile` copy the seed first so the starter profiles do not disappear after the first import.
13. **S2 — Connection Check vs Sentinel-started tunnels (needs a Mac):** resolve the profile's interface through `/var/run/wireguard/<name>.name` in `_compare_profile` so a verified Connect is not reported as "not active"; verify on hardware.
14. **S2 — Missing-config blocker for interface-only WG profiles:** refuse Connect when no `wg-quick` config (`/etc/wireguard`, `/opt/homebrew/etc/wireguard`, `/usr/local/etc/wireguard`) exists for the interface, and require a `.conf` suffix for imports.
15. **S2 — DNS-leak test fidelity:** resolve probes with the OS resolver (`getaddrinfo`) or label the test as "resolv.conf resolvers", fix the stale docstrings, and soften the ASN-mismatch verdict.
16. **S2 — Build Config pf snippet:** reuse `killswitch.build_rules` text (or run `pfctl -n` on it in CI on macOS) instead of the hand-written snippet that likely fails to parse.
17. **S2 — Packaging check:** build the frozen app once and confirm `agents.vpn_agent.*` (namespace package without `__init__.py`), `killswitch`, `privileged` and `dns.resolver` import correctly; add `agents/vpn_agent/__init__.py` if not.
18. **S2 — Manual test plan:** extend tests/manual_test_cases.md §7 (currently only checks, preview, builder) with live Connect/Disconnect, Import, template refusal, kill switch arm/disarm/recovery, Execution tab, audit file mode, OpenVPN disconnect in a packaged build, close-during-connect.
