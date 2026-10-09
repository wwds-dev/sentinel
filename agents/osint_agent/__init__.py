from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from urllib.parse import urlsplit

from services import osint_catalog


SYSTEM_PROMPT = """You are a light OSINT analysis assistant. Your role is to help structure queries, \
suggest search strategies, and summarise what public sources are likely to reveal — without \
performing live lookups or inventing data.

Given a target (name, username, email, domain, company, phone, or IP), produce exactly four \
sections in this order, using these exact headers:

## QUERY STRUCTURE
Identify the query type, break the target into searchable components (first name, last name, \
handle variations, domain registrar clues, etc.), and note any ambiguities or aliases to consider.

## GOOGLE DORKS
List 8–12 ready-to-paste Google search strings relevant to this target. One per line. \
Use advanced operators: site:, inurl:, intitle:, filetype:, "@", "-", etc. \
Include at least one Pastebin/GitHub/LinkedIn/social-platform dork where applicable.

## PUBLIC SOURCES
List the top 8–12 public sources or databases to check for this query type. \
For each source give: name, URL hint (e.g. "whois.domaintools.com"), and a one-line note \
on what it reveals. Tailor the list to the query type — don't give domain sources for a \
username query. Prefer free, no-login sources, and draw on this reference list where it fits \
(it is a starting point, not a limit):
- Domain / IP: web-check.as93.net (one-page site, DNS, TLS and header report); \
viewdns.info (reverse IP, IP history, reverse WHOIS); centralops.net (domain dossier: WHOIS, \
DNS, traceroute); dnslytics.com (ASN, IP neighbours, shared hosting); mxtoolbox.com (MX, SPF, \
DMARC, blacklists); crt.sh (certificate transparency subdomains).
- Web archives: web.archive.org (Wayback snapshots); archive.ph (archive.today snapshots, \
often of pages Wayback missed); cachedview.nl (cached copies from several engines).
- Username: whatsmyname.app (handle across ~600 sites); namechk.com and instantusername.com \
(availability across platforms, which hints at taken handles).
- Email: gravatar.com (public profile tied to the address); email-format.com (a company's \
address pattern); hunter.io (addresses published for a domain).
- Company: search.gleif.org (legal entity identifiers); opencorporates.com (company registers \
worldwide); unternehmensregister.de and handelsregister.de (German filings and register \
entries); northdata.com (German/EU officers, filings and company networks).
- Phone (Germany): dasoertliche.de and dastelefonbuch.de (listed numbers, including reverse \
search where the subscriber allowed it).
- Social: social-searcher.com (public mentions across platforms).

## SUMMARY & NEXT STEPS
Summarise what a typical OSINT trace on this target would likely surface, \
what information is probably unavailable or redacted, and give 3–5 prioritised \
next steps the investigator should take (in order of likely yield). \
Keep this section concise and actionable.

Do not fabricate results, real data, or live lookups. Stay within legal, \
public-source intelligence only."""


#: Trace query type → OSINT Framework catalogue target kind.
CATALOG_KINDS = {
    "Domain": "domain", "IP Address": "ip", "Email": "email",
    "Username": "username", "Company": "company", "Phone": "phone",
    "Person": "person",
}
CATALOG_LIMIT = 15


def catalog_block(query_type: str) -> str:
    """Extra public-source suggestions from the cached OSINT Framework catalogue.

    Reads the local cache only (never the network) and returns "" when there
    is no cache or no fitting tool, so Structure Query stays offline.
    """
    kind = CATALOG_KINDS.get(query_type)
    if kind is None:
        return ""
    picks = osint_catalog.select(
        osint_catalog.cached_tools(), kind, audience="trace",
        limit=CATALOG_LIMIT, exclude_hosts=osint_catalog.hosts_in(SYSTEM_PROMPT),
    )
    if not picks:
        return ""
    return (
        f"\n\nMORE {query_type.upper()} SOURCES — from the {osint_catalog.ATTRIBUTION} "
        "catalogue; each is live, free, needs no account, and is passive (the target "
        "is not contacted). Choose from these and the reference list above; never "
        "invent a URL:\n" + osint_catalog.format_block(picks)
    )


@dataclass(frozen=True)
class TargetValidation:
    valid: bool
    query_type: str
    message: str = ""


class OSINTAgent:
    _EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[A-Za-z]{2,63}$")
    _DOMAIN = re.compile(
        r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"[A-Za-z]{2,63}$"
    )
    _USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")

    @classmethod
    def validate_target(cls, target: str, query_type: str = "Auto-detect") -> TargetValidation:
        """Validate locally and resolve Auto-detect without performing a lookup."""
        value = target.strip()
        if not value:
            return TargetValidation(False, query_type, "Enter a target before running Trace.")
        if len(value) > 512 or any(ord(char) < 32 for char in value):
            return TargetValidation(
                False, query_type,
                "The target contains unsupported characters or is too long.",
            )

        resolved = query_type
        if query_type == "Auto-detect":
            if value.startswith("@"):
                resolved = "Username"
            elif "@" in value:
                resolved = "Email"
            elif cls._looks_like_ip(value):
                resolved = "IP Address"
            elif cls._looks_like_phone(value):
                resolved = "Phone"
            elif cls._looks_like_domain(value):
                resolved = "Domain"
            else:
                resolved = "Person" if " " in value else "Username"

        validators = {
            "Email": cls._validate_email,
            "Domain": cls._validate_domain,
            "IP Address": cls._validate_ip,
            "Phone": cls._validate_phone,
            "Username": cls._validate_username,
            "Person": cls._validate_named_subject,
            "Company": cls._validate_named_subject,
        }
        validator = validators.get(resolved)
        if validator is None:
            return TargetValidation(False, resolved, f"Unsupported query type: {resolved}.")
        message = validator(value)
        return TargetValidation(not message, resolved, message)

    @staticmethod
    def _looks_like_ip(value: str) -> bool:
        candidate = value.strip("[]")
        try:
            ipaddress.ip_address(candidate)
            return True
        except ValueError:
            return bool(
                re.fullmatch(r"[0-9.]+", candidate) and candidate.count(".") == 3
            )

    @classmethod
    def _looks_like_domain(cls, value: str) -> bool:
        host = cls._domain_host(value)
        return "." in host and " " not in host

    @staticmethod
    def _looks_like_phone(value: str) -> bool:
        digits = re.sub(r"\D", "", value)
        return bool(re.fullmatch(r"\+?[0-9() .-]+", value) and len(digits) >= 7)

    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not cls._EMAIL.fullmatch(value):
            return "Enter a complete email address, such as name@example.com."
        return ""

    @staticmethod
    def _domain_host(value: str) -> str:
        candidate = value.strip().lower()
        try:
            parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        except ValueError:
            # urlsplit raises ValueError ("Invalid IPv6 URL") on an unbalanced
            # '[' or ']'. validate_target must return a result, never raise, so
            # treat an unparseable target as having no host.
            return ""
        return (parsed.hostname or "").rstrip(".")

    @classmethod
    def _validate_domain(cls, value: str) -> str:
        if not cls._DOMAIN.fullmatch(cls._domain_host(value)):
            return "Enter a valid domain, such as example.com."
        return ""

    @staticmethod
    def _validate_ip(value: str) -> str:
        try:
            ipaddress.ip_address(value.strip("[]"))
            return ""
        except ValueError:
            return "Enter a valid IPv4 or IPv6 address."

    @staticmethod
    def _validate_phone(value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if not re.fullmatch(r"\+?[0-9() .-]+", value) or not 7 <= len(digits) <= 15:
            return "Enter a phone number containing 7 to 15 digits."
        return ""

    @classmethod
    def _validate_username(cls, value: str) -> str:
        if not cls._USERNAME.fullmatch(value.lstrip("@")):
            return (
                "Use 2 to 64 letters, numbers, dots, underscores, or hyphens "
                "for a username."
            )
        return ""

    @staticmethod
    def _validate_named_subject(value: str) -> str:
        if len(value) < 2 or not any(char.isalpha() for char in value):
            return "Enter a name or organisation containing at least two characters."
        if "@" in value or "://" in value:
            return "This target does not match the selected name or company type."
        return ""

    def build_messages(self, target: str, query_type: str = "Auto-detect") -> list[dict]:
        type_hint = "" if query_type == "Auto-detect" else f" (query type: {query_type})"
        user_content = (
            f"Target{type_hint}: {target}\n\n"
            "Produce the four sections as specified."
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT + catalog_block(query_type)},
            {"role": "user", "content": user_content},
        ]
