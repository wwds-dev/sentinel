# Support

Sentinel is a local-first macOS command centre for security, investigation and controlled AI-assisted work, maintained by one person in their own time. There is no paid
support and no guaranteed response time.

## Where to ask

| You want | Go to |
|---|---|
| Help running it, or a question about how it works | the README and `docs/` first, then a [GitHub issue](https://github.com/wwds-dev/sentinel/issues) |
| To report a bug | a GitHub issue with the version beside **SENTINEL** in the app (for example `v2.002`), your macOS version, what you did, what you expected and what happened. Do not paste API keys or the contents of `.env`, VPN or server credentials, investigation results, or anything about a real person or network |
| To report a security problem | [SECURITY.md](SECURITY.md), privately, never in a public issue |
| To suggest a feature | a GitHub issue; `SUGGESTIONS.md` lists what is already considered |

## What to expect

Issues are read **about once a week**; a reply within 7 days is the target, not a promise.
Fixes land when the maintainer has time.

## What Sentinel is not

- Help using Sentinel against people, systems or networks you are not authorised to investigate or test. Bug Spray records the scope you declare; it does not verify it, and neither does anyone here.
- Legal advice about OSINT, Wi-Fi, VPN, Tor or bug-bounty rules where you live.
- A managed VPN service: Tunnel runs WireGuard and OpenVPN with configurations you supply or build yourself.

## Known limits

- macOS only (developed on Apple Silicon); published as source, with no binary release. `scripts/build_app.sh` can build a copy, but only the source on `main` is supported.
- VPN, kill-switch and network features ask for administrator approval through macOS each time; they do nothing without it.
- Local models need Ollama or LM Studio running; cloud providers are used only when you enable them, with your own keys.
- OSINT and threat-intelligence sources change or rate-limit without notice; a failing source is the most common fault.
