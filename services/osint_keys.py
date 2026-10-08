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
  KEY_OPTIONAL — the source runs without a key; a key only lifts a limit.
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
    KEY_OPTIONAL: "Works without a key; a key only raises the rate limit.",
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
                 "Bloodhound search it for a username's footprint, anonymously, "
                 "at about 100 searches a day.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_UNREAD,
    },
    "virustotal": {
        "about": "Checks files, URLs, domains and IPs against dozens of antivirus "
                 "engines and reputation feeds.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "otx": {
        "about": "AlienVault Open Threat Exchange: community-shared indicators of "
                 "compromise, such as malicious IPs, domains and file hashes, "
                 "grouped into threat reports.",
        "agents": {},
        "key": KEY_UNREAD,
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
                 "spam and port scanning, with a confidence score.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "greynoise": {
        "about": "Separates internet background noise, such as mass scanners and "
                 "crawlers, from activity aimed at you.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "censys": {
        "about": "A search engine over continuous scans of internet-facing hosts: "
                 "open services, TLS certificates and the software behind them.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "criminalip": {
        "about": "IP reputation score, VPN, proxy, Tor and hosting flags, open "
                 "ports and the network owner. Each lookup spends credits.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "securitytrails": {
        "about": "Historical DNS and WHOIS records, subdomains and related domains.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "hunter": {
        "about": "Finds the professional email addresses published for a company "
                 "domain, and checks whether an address can receive mail.",
        "agents": {},
        "key": KEY_UNREAD,
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
        "about": "A search engine for internet-connected devices and their open "
                 "ports. Sentinel uses only Shodan's free InternetDB lookup, which "
                 "needs no key.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_UNREAD,
    },
    "dehashed": {
        "about": "Breach database search. Sentinel reads only which breaches contain "
                 "an email or domain and how many records, never the leaked "
                 "contents. Each search spends credits.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "snusbase": {
        "about": "Breach database search by email, username, IP address or hash.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "leakcheck": {
        "about": "Breach and leak search by email, username or domain.",
        "agents": {},
        "key": KEY_UNREAD,
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
                 "files. Paid; free keys were discontinued.",
        "agents": {TRACE: EXTRA, BLOODHOUND: EXTRA},
        "key": KEY_NEEDED,
    },
    "domaintools": {
        "about": "Commercial WHOIS history, passive DNS and domain risk scoring.",
        "agents": {},
        "key": KEY_UNREAD,
    },
    "courtlistener": {
        "about": "The Free Law Project's archive of U.S. court dockets. Sentinel "
                 "reports that a case exists, never its filings.",
        "agents": {TRACE: ESSENTIAL, BLOODHOUND: ESSENTIAL},
        "key": KEY_OPTIONAL,
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
