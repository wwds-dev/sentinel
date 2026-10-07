# Sentinel Learning Centre

The Learning Centre contains detailed, task-based training for people using the
application, rather than developer reference material. It covers every agent,
shared controls, settings, privacy/cost decisions, portable operation,
troubleshooting and combined-agent workflows. Open it from Sentinel's
**More (•••) → Learning Centre** menu.

![Learning Centre lesson browser](docs/training/images/learning-centre.png)

## Complete curriculum

### Foundations

- [Quick Start](quick_start.md)
- [API keys and new models](api_keys.md)
- [Workspace tour](workspace.md)
- [Controls and settings](controls_settings.md)
- [Portable USB mode](portable.md)
- [Privacy and cost](privacy_cost.md)
- [Troubleshooting](troubleshooting.md)

### Agents

- [Chat](chat.md)
- [Trace](trace.md)
- [Bloodhound](bloodhound.md)
- [Beacon](beacon.md)
- [Sentry](sentry.md)
- [Bug Spray](bug_spray.md)
- [Tunnel](tunnel.md)
- [Forge](forge.md)

### Across Sentinel

- [Agent workflows](workflows.md)
- [Testing roadmap](../testing_roadmap.md)
- [Advanced tools and v3 direction](advanced_tools.md)

## Lesson standard

Each lesson should state its goal, estimated time, steps, expected result,
privacy or cost implications, best practices and a simple completion check.
Screenshots must use fictional data, hide secrets and personal paths, include
alternative text, and record the interface version they show.

### Worked examples

Every agent lesson carries at least one **Worked example**: a complete, named
scenario a reader can follow end to end. A worked example has a fixed shape so
the lessons stay comparable:

1. **Scenario** — one sentence naming the situation and the authorised context.
2. **You have** — the exact fictional inputs (target, identifier, file, profile).
   Use only invented data; never a real person, host, key or path.
3. **Do this** — the numbered click path, naming the actual buttons and fields.
4. **You should see** — the concrete expected result, specific enough to check
   against, with a screenshot where one exists.
5. **Why it's safe / what it costs** — which steps are local, which touch a
   model or the network, and where the reader must stop and review.

Keep the inputs consistent with the sample data the capture script uses, so a
future screenshot of the scenario matches the prose without editing. A worked
example teaches one realistic task; broader capability still belongs in the
surrounding sections.

The nine-screen screenshot set is reproducible with
`scripts/capture_training_screenshots.py`. Re-capture it after a material UI
change instead of keeping misleading historical images.
Pass one or more screen keys (for example,
`scripts/capture_training_screenshots.py vpn`) to refresh only the affected
lesson and avoid unrelated image churn.

The focused Tunnel capture writes both `tunnel.png` and the deterministic
`tunnel-action-preview.png`; it uses the sample profile catalog, never live VPN
profile names.
