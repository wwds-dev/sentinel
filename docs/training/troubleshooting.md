# Troubleshooting

**Goal:** resolve common problems without losing work. **Time:** as needed.

| Symptom | What it usually means | What to do |
|---|---|---|
| Provider is not enabled | Sentinel has no permission for that API | Review the route and enable the provider only if you intend to send the data |
| API key missing | No credential was detected | Add the key as described in [API keys and new models](api_keys.md), quit and restart Sentinel, then check the API KEYS card |
| Insufficient balance / HTTP 402 | The provider account cannot fund the request | Add provider credit or deliberately choose another route; never assume Auto-route changes providers silently |
| Budget exceeded | A Sentinel cap blocked the request | Review Cost history, then change the cap only if the spend is intended |
| Ollama unavailable | The local service is stopped or the model is absent | Start Ollama, install/select an available model, then refresh models |
| LM Studio models read *on disk · server off* | LM Studio's server is not running, or `LMSTUDIO_URL` points at an address it is not on | Press **Start LM Studio server** (shown while LM Studio is selected) or **Start server** in Settings → Local models; without the `lms` tool, start the server in LM Studio. Sentinel follows the server to its real port only when `LMSTUDIO_URL` is not set |
| App feels slow | A local model or scan is using resources | Check Inspector, wait, stop the task or select a smaller model |
| No live research result | Source failed, rate-limited or had no match | Read Trace Activity; keep successful partial results and retry only when appropriate |
| File discovery permission error | The current local/SSH account cannot read a folder | Choose an accessible folder or correct account permissions outside Sentinel |
| Unknown SSH host key | The machine is not trusted in `known_hosts` | Verify the fingerprint independently before adding it with normal SSH tools |
| Tunnel's kill switch will not arm: "another VPN app is active" | The Mullvad or ProtonVPN app is connected, or Mullvad's state could not be read | Use the app's own kill switch setting, or disconnect the app and arm against a profile you connect from Tunnel |
| VPN Apps → Mullvad Connect refuses | Mullvad does not confirm you are logged in, or its daemon did not answer | Open the Mullvad app, log in there (Tunnel never takes the account number), then press **Refresh** and try again |
| VPN Apps → ProtonVPN does not reach Connected | ProtonVPN's macOS VPN service ignored the start request or needs the app open or signed in | Connect from the ProtonVPN app instead; Tunnel still shows its state |
| A Tunnel Advisor answer is missing from Saved Chats or Run log | Tunnel keeps no log, the Advisor included | Copy anything you need from the Advisor tab before you ask again or close Sentinel; Cost history still lists the request's cost |
| Stop appears ineffective | A provider/process may already be finishing | Wait briefly and check Run log; cancellation cannot recall data already sent |
| Two Sentinel icons in the Dock, one marked *Running in Background* | Seen with the older fork-and-exec launcher, which left its own bundle registered after it started Sentinel. Current builds run Sentinel inside the bundle and should show one tile | Update to a current build and use the tile with the window. If you still see two on a current build, quit and relaunch, and report it with your macOS version |
| Portable volume unavailable/read-only/low-space | The USB drive was ejected, mounted without write access, or has under 256 MiB free | Stop, reconnect or repair the volume, free space, then restart; Sentinel will not redirect portable data elsewhere |
| Emergency Reset is missing | Sentinel is running from the Lab checkout or a normal installed build | The reset is intentionally shown only in a marked portable distribution |
| Emergency Reset was cancelled | The exact phrase or second confirmation was not accepted | Nothing was erased; repeat only after backing up anything you need |

## Preserve evidence

Before retrying, save useful partial output, copy the exact error, record the
agent, route and time, and inspect Run log. Do not repeatedly send a failing
paid request. Restart the app after interface updates because an already-running
process cannot load changed Python code.

## Escalation information

When asking for help, provide the visible error, reproduction steps, selected
agent/provider/model, whether the task works locally, and the relevant Run log
entry. Remove API keys, passwords and private target information first.

Emergency Reset cannot recall requests already sent to a provider or delete
provider, DNS, router, macOS, crash, swap or security logs. Contact the relevant
service administrator when those records require handling under an applicable
retention or deletion policy.
