"""What each service on the Settings → OSINT Keys tab is, and who uses it.

The tab lists signup links and key fields; this module says, per service, what
it does and which agents call it. The map is checked against the code by
``tests/test_osint_keys.py``: a key marked as read must be read by a provider
or an agent, and a key marked unread must not be, so the tab cannot drift into
promising a key does something it does not.

Roles, per agent:
  ESSENTIAL — part of that agent's default run. It needs nothing from you: no
              key, no tick in a consent dialog.
  EXTRA     — an add-on source. The agent works without it; it runs once you
              save a key, or tick it when the agent asks which services may
              receive the target.

Key states:
  KEY_NONE     — the service needs no key, and the tab shows no key field.
  KEY_NEEDED   — the source is skipped until a key is saved.
  KEY_OPTIONAL — the source runs without a key; a key adds data or lifts a limit.
  KEY_UNREAD   — nothing in Sentinel reads this key yet. Saving one does no harm
                 and does nothing until a provider uses it.
"""

ESSENTIAL = "essential"
EXTRA = "extra"

KEY_NONE = "none"
KEY_NEEDED = "needed"
KEY_OPTIONAL = "optional"
KEY_UNREAD = "unread"

# Not an agent: the "Mint addy.io alias" button at the top of the tab.
ALIAS_BUTTON = "alias_button"

USER_LABELS = {ALIAS_BUTTON: "Alias button"}

KEY_STATE_TEXT = {
    KEY_NONE: "No key needed.",
    KEY_NEEDED: "Skipped until a key is saved.",
    KEY_OPTIONAL: "Works without a key; saving one adds more.",
    KEY_UNREAD: "No part of Sentinel reads this key yet, so saving one changes nothing for now.",
}

TRACE = "osint"
BLOODHOUND = "osint_heavy"
TUNNEL = "vpn"

OSINT_TOOL_INFO = {
    "emailrep": {
        "about": "Reputation for an email address: how old and trusted it looks, "
                 "whether it appears in breaches, and linked public profiles. "
                 "About 10 lookups a day without a key.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_NONE,
    },
    "urlscan": {
        "about": "An archive of real browser scans of public web pages. Trace and "
                 "Bloodhound search it for a username's footprint. With a key, "
                 "domain lookups also list the recent public scans of the domain.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_OPTIONAL,
        "note": "Without a key, searches are anonymous and capped at about 100 a day.",
    },
    "virustotal": {
        "about": "Verdicts from dozens of antivirus engines and reputation feeds for "
                 "an IP address or domain, plus community votes and categories.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
        "note": "The free key allows 4 lookups a minute and 500 a day.",
    },
    "otx": {
        "about": "AlienVault Open Threat Exchange: community threat reports, called "
                 "pulses, that name an IP address or domain, with malware families "
                 "and adversaries where known.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "ipinfo": {
        "about": "Where an IP address is and who runs its network. Paid plans add "
                 "VPN, proxy, hosting and Tor flags. Anonymous API use is refused.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA, TUNNEL: EXTRA},
        "key": KEY_NEEDED,
        "note": "Tunnel falls back to ipapi.co for your public IP without it.",
    },
    "abuseipdb": {
        "about": "Community reports of abusive IP addresses, such as brute forcing, "
                 "spam and port scanning, with a 0 to 100 confidence score.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "greynoise": {
        "about": "Says whether an IP address is mass-scanning the whole internet, "
                 "or is a known benign service such as a CDN, so you can tell "
                 "background noise from activity aimed at you.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "censys": {
        "about": "A search engine over continuous scans of internet-facing hosts. "
                 "For an IP address: its open services, the software behind them "
                 "and its certificates.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "criminalip": {
        "about": "IP reputation score, VPN, proxy, Tor and hosting flags, open "
                 "ports and the network owner. Each lookup spends credits.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
        "key_hint": "Platform personal access token",
        "note": ("Use a Censys Platform personal access token. The free account allows "
                 "100 lookups a month. Paid organisations also set CENSYS_ORG_ID in .env."),
    },
    "securitytrails": {
        "about": "Current DNS records of a domain, when each was first seen, and "
                 "how many subdomains SecurityTrails knows about.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
        "note": "The free plan allows 50 lookups a month; each domain lookup uses one.",
    },
    "hunter": {
        "about": "A company domain's email pattern and its role addresses, such as "
                 "security@, plus a mail-server check for an email address. Sentinel "
                 "reports how many named people's addresses Hunter knows, never who.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "addyio": {
        "about": "Anonymous email forwarding. Sentinel uses it only for the Mint "
                 "addy.io alias button above, which creates a burner address for "
                 "your operational email.",
        "agents": {ALIAS_BUTTON: ESSENTIAL},
        "key": KEY_NEEDED,
    },
    "breachdirectory": {
        "about": "A free search of public breach data that reports which breaches "
                 "an email address appears in.",
        "agents": {TRACE: EXTRA, BLOODHOUND: ESSENTIAL},
        "key": KEY_NONE,
        "note": "Trace leaves breach services unticked until you choose them.",
    },
    "hibp": {
        "about": "Have I Been Pwned: which known breaches and pastes contain an "
                 "email address, and what kind of data leaked.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "shodan": {
        "about": "A search engine for internet-connected devices. Without a key, "
                 "Sentinel uses Shodan's free InternetDB summary of open ports and "
                 "known vulnerabilities. With a key, IP lookups get the full host "
                 "record with service banners, and domain lookups list the "
                 "subdomains Shodan has seen.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_OPTIONAL,
        "note": "A domain lookup with a key spends one Shodan query credit.",
    },
    "dehashed": {
        "about": "Breach database search. Sentinel reads only which breaches contain "
                 "an email or domain and how many records, never the leaked "
                 "contents. Each search spends credits.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "snusbase": {
        "about": "Breach database search. Sentinel reports which breaches hold an "
                 "email or domain, how many records and what kinds of data leaked, "
                 "never the leaked values.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "leakcheck": {
        "about": "Breach and leak search. Sentinel reports which breaches hold an "
                 "email or domain, when they happened and what kinds of data leaked, "
                 "never the leaked values.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "ransomware_live": {
        "about": "Tracks the victims ransomware gangs post on their leak sites. "
                 "Free, at about one request a minute.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_NONE,
    },
    "ahmia": {
        "about": "A clearnet search engine for indexed .onion sites, with abuse "
                 "content filtered out by Ahmia. Sentinel reads the result text "
                 "and never opens an onion site.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_NONE,
    },
    "intelx": {
        "about": "Intelligence X: a searchable archive of leaks, pastes and dark-web "
                 "material. Sentinel queries the index only and never downloads "
                 "files. A paid key, or a free account's key within the free "
                 "tier's limits.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "domaintools": {
        "about": "Commercial domain intelligence: the domain's registration profile "
                 "and DomainTools' risk score for it.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
        "key_hint": "username:api_key",
        "note": ("Save it as username:api_key so each request is signed and the key "
                 "itself is never sent. Each product is used only if your plan has it."),
    },
    "courtlistener": {
        "about": "The Free Law Project's archive of U.S. court dockets. Sentinel "
                 "reports that a case exists, never its filings.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_OPTIONAL,
        "note": "A key only raises the rate limit.",
    },
    "opensanctions": {
        "about": "Sanctions lists, politically exposed persons and other watchlists "
                 "in one search, for company lookups. Free for non-commercial use; "
                 "commercial use needs their licence.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
}


def user_label(user: str) -> str:
    """Display name of an agent id, or of the alias button."""
    if user in USER_LABELS:
        return USER_LABELS[user]
    from services.agent_catalog import BUILTIN_AGENTS
    return BUILTIN_AGENTS.get(user, {}).get("label", user)


def explain(tool_id: str) -> str:
    """The hover text for a service: what it is, who uses it, what the key does."""
    info = OSINT_TOOL_INFO.get(tool_id)
    if info is None:
        return ""
    lines = [info["about"], ""]
    agents = info["agents"]
    if agents:
        for user, role in agents.items():
            meaning = ("essential, part of its default run" if role == ESSENTIAL
                       else "extra, an add-on source it can work without")
            lines.append(f"{user_label(user)}: {meaning}.")
    else:
        lines.append("No agent uses this service yet.")
    lines.append(KEY_STATE_TEXT[info["key"]])
    if info.get("note"):
        lines.append(info["note"])
    return "\n".join(lines)
