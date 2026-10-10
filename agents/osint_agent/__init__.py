from __future__ import annotations

from dataclasses import dataclass
import hashlib
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


# ── Target-type hints (advisory only) ───────────────────────────────────────
#
# Trace never re-routes a target: the type the user picked (or Auto-detect
# resolved) is what the lookup uses. These pure helpers only notice input that
# is probably aimed at the wrong type, so the consent dialog can say so.

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
#: base58check version bytes -> chain (a 25-byte payload: version + 20 + 4 checksum).
_B58_VERSIONS = {
    0x00: "Bitcoin", 0x05: "Bitcoin", 0x30: "Litecoin", 0x32: "Litecoin",
    0x1E: "Dogecoin", 0x16: "Dogecoin", 0x41: "Tron",
}
#: Plausible domain endings. A dotted pair whose last label is not here (and is
#: not a two-letter country code in _CCTLDS) is more likely a name or handle.
_GTLDS = frozenset(
    "com net org info biz edu gov mil int name pro mobi asia tel travel jobs museum coop "
    "aero xxx app dev ai xyz online site tech store shop blog cloud page link live news "
    "media email space top club agency digital network systems solutions services company "
    "group team world today life one academy works zone studio design art bank church "
    "city center community consulting education energy events expert finance fund global "
    "health help host io law legal money partners press red school software support "
    "tools university vip website wiki work zip "
    "test example invalid localhost local internal lan home corp intranet onion".split()
)
_CCTLDS = frozenset(
    "ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo br "
    "bs bt bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do "
    "dz ec ee eg er es et eu fi fj fk fm fo fr ga gd ge gf gg gh gi gl gm gn gp gq gr gs gt "
    "gu gw gy hk hm hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn "
    "kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mg mh mk ml mm mn mo mp mq "
    "mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl "
    "pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sk sl sm sn so sr ss st "
    "su sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc "
    "ve vg vi vn vu wf ws ye yt za zm zw".split()
)


@dataclass(frozen=True)
class TypeHint:
    """A one-line suggestion that the input suits a different target type."""

    suggested: str   # "Crypto Address", "Domain", "Username or Person"
    message: str


def _base58_chain(value: str) -> str:
    """The chain of a base58check address (checksum verified), or ""."""
    if not 26 <= len(value) <= 35 or any(ch not in _B58 for ch in value):
        return ""
    number = 0
    for ch in value:
        number = number * 58 + _B58.index(ch)
    raw = number.to_bytes(25, "big") if number < 1 << 200 else b""
    if len(raw) != 25:
        return ""
    # Leading '1's encode leading zero bytes; the 25-byte form already has them.
    if hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] != raw[21:]:
        return ""
    return _B58_VERSIONS.get(raw[0], "")


def _bech32_valid(value: str) -> bool:
    """Bech32 / Bech32m checksum of a segwit-style address (single case)."""
    if value != value.lower() and value != value.upper():
        return False
    value = value.lower()
    sep = value.rfind("1")
    if sep < 1 or sep + 7 > len(value) or len(value) > 90:
        return False
    if any(ch not in _BECH32 for ch in value[sep + 1:]):
        return False
    hrp = value[:sep]
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    values += [_BECH32.index(c) for c in value[sep + 1:]]
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    check = 1
    for item in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ item
        for bit in range(5):
            check ^= generator[bit] if (top >> bit) & 1 else 0
    return check in (1, 0x2BC830A3)


def crypto_address_chain(value: str) -> str:
    """Name the chain when ``value`` is a Bitcoin, Ethereum, Litecoin, Dogecoin
    or Tron address, else "". Pure: no lookup. Base58 and bech32 addresses must
    pass their checksum, so an ordinary handle that only resembles one is not
    reported; an Ethereum address is 0x plus 40 hex digits."""
    text = value.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", text):
        return "Ethereum"
    lowered = text.lower()
    if lowered.startswith(("bc1", "ltc1")) and _bech32_valid(text):
        return "Bitcoin" if lowered.startswith("bc1") else "Litecoin"
    return _base58_chain(text)


def _plausible_domain(host: str) -> bool:
    labels = host.lower().split(".")
    last = labels[-1]
    return len(labels) >= 2 and (last in _GTLDS or last in _CCTLDS)


def classify_target(value: str, resolved_type: str) -> TypeHint | None:
    """Suggest a better target type for ``value`` — never change the type.

    Returns None for ordinary input. ``resolved_type`` is the type Trace will
    actually use; the hint is shown next to it and the lookup is unaffected.
    """
    text = value.strip()
    if not text or resolved_type == "Crypto Address":
        return None
    chain = crypto_address_chain(text)
    if chain:
        return TypeHint(
            "Crypto Address",
            f"Looks like {'an' if chain[0] in 'AEIOU' else 'a'} {chain} address - "
            "Trace has no wallet type; "
            "use Bloodhound's Crypto Address.",
        )
    if resolved_type in {"Email", "IP Address", "Phone"} or "@" in text:
        return None
    host = OSINTAgent._domain_host(text)
    dotted_name = re.fullmatch(r"[a-z]{2,}(?:\.[a-z]+)?\.[a-z]{2,}", host)
    if resolved_type == "Domain":
        if dotted_name and "://" not in text and not _plausible_domain(host):
            return TypeHint(
                "Username or Person",
                f"Looks like a name or username, not a domain ('.{host.rsplit('.', 1)[-1]}' "
                "is not a known domain ending) - use Username or Person.",
            )
        return None
    if OSINTAgent._DOMAIN.fullmatch(host) and _plausible_domain(host) and " " not in text:
        return TypeHint("Domain", "Looks like a domain - use Domain.")
    return None


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
    def non_public_ip_reason(value: str) -> str:
        """Why ``value`` is not a publicly routable address, or "" if it is.

        Called before Live Research contacts a real WHOIS/DNS/threat-intel
        service for an "IP Address" target: a private, loopback, link-local,
        reserved, multicast or unspecified address has no meaningful public
        footprint, and pasting one (e.g. from an internal network) should not
        silently go out to third-party services.
        """
        try:
            addr = ipaddress.ip_address(value.strip().strip("[]"))
        except ValueError:
            return ""
        if addr.is_loopback:
            return "a loopback address"
        if addr.is_link_local:
            return "a link-local address"
        if addr.is_private:
            return "a private (RFC 1918 / RFC 4193) address"
        if addr.is_multicast:
            return "a multicast address"
        if addr.is_unspecified:
            return "an unspecified address"
        if addr.is_reserved:
            return "a reserved address"
        return ""

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
