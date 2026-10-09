"""Canonical built-in agent roster for the Sentinel security hub.

Dynamic agents created by Forge live in SQLite and are deliberately not part of
this catalog. Retired names are kept only so existing installations can stop
showing agents that moved to another application; historical runs and chats are
never deleted.
"""

BUILTIN_AGENTS = {
    "chat": {
        "label": "Chat",
        "icon": "▸",
        "subtitle": "General reasoning, any provider",
        "tooltip": "General-purpose conversation. Pick a tool, pick a model, talk.",
        "description": "General-purpose chat agent.",
        "allowed_tools": ["General Chat", "Writing", "Coding", "Summarize", "Rewrite"],
        "budget_limit_eur": None,
    },
    "manager": {
        "label": "Forge",
        "icon": "✦",
        "subtitle": "Create reviewable agent scaffolds",
        "tooltip": "Describe a new agent in plain language and review a draft specification. Approval writes an inactive scaffold.",
        "description": "Drafts agent specifications and, after approval, writes an inactive scaffold.",
        "allowed_tools": None,
        "budget_limit_eur": None,
    },
    "osint": {
        "label": "Trace",
        "icon": "◈",
        "subtitle": "Open-source identity research",
        "tooltip": "Light OSINT — structured research queries.",
        "description": "Open-source intelligence research and investigation agent.",
        "allowed_tools": ["General Chat", "Summarize"],
        "budget_limit_eur": 2.0,
    },
    "osint_heavy": {
        "label": "Bloodhound",
        "icon": "◉",
        "subtitle": "Deep investigation and dossier",
        "tooltip": "Deep OSINT dossiers and read-only file search in folders you select, on this Mac or over SSH.",
        "description": "Deep investigation dossiers plus read-only, user-directed file discovery on this Mac or an SSH machine.",
        "allowed_tools": None,
        "budget_limit_eur": None,
    },
    "wifi": {
        "label": "Beacon",
        "icon": "≋",
        "subtitle": "Wireless reconnaissance",
        "tooltip": "Wireless reconnaissance, signal analysis, and authorised diagnostics.",
        "description": "Wi-Fi diagnostics and authorised wireless-security workflows.",
        "allowed_tools": None,
        "budget_limit_eur": None,
    },
    "sentry": {
        "label": "Sentry",
        "icon": "◎",
        "subtitle": "Network anomaly watch",
        "tooltip": "Read-only watch of your own network for new devices, address conflicts, gateway changes and new listening services.",
        "description": "Read-only network monitoring with baseline diffing, an optional background watch and an optional AI read of anomalies.",
        "allowed_tools": None,
        "budget_limit_eur": None,
    },
    "bug_bounty": {
        "label": "Bug Spray",
        "icon": "⌁",
        "subtitle": "Vulnerability triage",
        "tooltip": "Vulnerability triage and bug-bounty submission drafts.",
        "description": "Authorised vulnerability analysis and bug-bounty report generation.",
        "allowed_tools": None,
        "budget_limit_eur": None,
    },
    "vpn": {
        "label": "Tunnel",
        "icon": "⇄",
        "subtitle": "Gated VPN connect, checks & design",
        "tooltip": "Connect or disconnect a WireGuard/OpenVPN tunnel behind a confirmation gate, check the connection and DNS, preview changes, and design a self-hosted VPN.",
        "description": "Gated WireGuard/OpenVPN connect and disconnect with an optional pf kill switch, profile-aware status checks, safe action previews, self-hosted design, configuration, and troubleshooting.",
        "allowed_tools": None,
        "budget_limit_eur": None,
    },
}

BUILTIN_AGENT_ORDER = (
    "chat", "osint", "osint_heavy", "wifi", "sentry", "bug_bounty", "vpn", "manager",
)

# These were once Sentinel built-ins. Their application data is preserved in
# sibling projects; this list only retires their registry rows in Sentinel.
RETIRED_BUILTIN_AGENTS = frozenset({
    "audiobook", "author", "coding", "fiverr", "health", "investment",
    "manuscript", "music", "nfl_bet", "ops_identity", "roi", "webdesign",
    "writing",
})
