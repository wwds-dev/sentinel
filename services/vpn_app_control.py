"""Start and stop the Mullvad app's tunnel through its official command-line tool.

``vpn_apps`` only looks. This module acts for Mullvad only, and only on what the
provider's own tool allows: ``mullvad connect``, ``disconnect`` and ``relay set location``.
Rules it keeps:

* **No credentials.** Login stays in the Mullvad app or CLI. Sentinel never logs
  in, never passes an account number, and never keeps one: ``mullvad account
  get`` prints the number, so its output is reduced to "logged in until <date>"
  and any long digit run is hidden.
* **No connect without a login.** Mullvad blocks all traffic when told to
  connect while logged out, which would cut off this Mac (and any remote
  session). Connect refuses unless the account is verifiably logged in.
* **Arguments are validated and passed as an argv list**, never through a shell.

Every command is injected (``run``) so tests never touch a real Mullvad.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from services import vpn_apps

Runner = vpn_apps.Runner
Sleep = Callable[[float], None]

_PART = re.compile(r"[a-z0-9-]{2,32}")
_DIGIT_RUN = re.compile(r"\b\d[\d ]{9,}\d\b")
CONNECT_WAIT_ATTEMPTS = 15          # x 1 s


def hide_account_numbers(text: str) -> str:
    return _DIGIT_RUN.sub("[account number hidden]", text or "")


# ── Account (read-only, number never kept) ──────────────────────────────────

def mullvad_account(run: Runner = vpn_apps._run) -> tuple[bool | None, str]:
    """``(logged_in, summary)``; ``None`` when it cannot be told."""
    cli = vpn_apps.mullvad_cli()
    if not cli:
        return None, "Mullvad's command-line tool was not found."
    code, out = run([cli, "account", "get"], 3.0)
    lowered = out.lower()
    if "not logged in" in lowered:
        return False, "Not logged in. Log in with the Mullvad app first."
    if code != 0 or not out.strip():
        return None, "Mullvad's daemon did not answer (is the Mullvad app running?)."
    match = re.search(r"expires?\s*(?:at|on)?\s*:?\s*(.+)", out, re.I)
    if match:
        return True, "Logged in; account time runs until " + hide_account_numbers(match.group(1).strip())[:60] + "."
    return None, "Mullvad answered, but its account state could not be understood."


# ── Locations ───────────────────────────────────────────────────────────────

_COUNTRY = re.compile(r"^(?P<name>\S.*?) \((?P<code>[a-z]{2})\)\s*$")
_CITY = re.compile(r"^\s+(?P<name>\S.*?) \((?P<code>[a-z0-9]{2,6})\)\s+@")


def parse_locations(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """``[(label, args)]`` from ``mullvad relay list``: countries and cities."""
    out: list[tuple[str, tuple[str, ...]]] = []
    country_name = country_code = ""
    for line in text.splitlines():
        match = _COUNTRY.match(line)
        if match:
            country_name, country_code = match.group("name"), match.group("code")
            out.append((country_name, (country_code,)))
            continue
        match = _CITY.match(line)
        if match and country_code:
            out.append((f"{country_name} › {match.group('name')}",
                        (country_code, match.group("code"))))
    return out


def mullvad_locations(run: Runner = vpn_apps._run) -> list[tuple[str, tuple[str, ...]]]:
    cli = vpn_apps.mullvad_cli()
    if not cli:
        return []
    code, out = run([cli, "relay", "list"], 5.0)
    return parse_locations(out) if code == 0 else []


def valid_location(parts: tuple[str, ...] | list[str]) -> bool:
    return 1 <= len(parts) <= 3 and all(_PART.fullmatch(str(p)) for p in parts)


# ── Actions ─────────────────────────────────────────────────────────────────

def _state(run: Runner) -> vpn_apps.VpnApp:
    return vpn_apps.detect_mullvad(run)


def mullvad_connect(location: tuple[str, ...] | None = None, *, run: Runner = vpn_apps._run,
                    sleep: Sleep = time.sleep) -> tuple[bool, str]:
    cli = vpn_apps.mullvad_cli()
    if not cli:
        return False, "Mullvad's command-line tool was not found. Install the Mullvad app."
    logged_in, account = mullvad_account(run)
    if logged_in is not True:
        return False, ("Not connecting: " + account + " Connecting while logged out makes "
                       "Mullvad block all traffic on this Mac.")
    if _state(run).state == vpn_apps.CONNECTED and not location:
        return True, "Mullvad VPN is already connected."
    if location:
        if not valid_location(location):
            return False, "That location is not valid."
        code, out = run([cli, "relay", "set", "location", *location], 5.0)
        if code != 0:
            return False, "Mullvad refused that location: " + hide_account_numbers(out.strip())[:200]
    code, out = run([cli, "connect"], 10.0)
    if code != 0:
        return False, "Mullvad could not start connecting: " + hide_account_numbers(out.strip())[:200]
    app = _state(run)
    for attempt in range(CONNECT_WAIT_ATTEMPTS):
        if app.state in (vpn_apps.CONNECTED, vpn_apps.BLOCKED):
            break
        if attempt + 1 < CONNECT_WAIT_ATTEMPTS:
            sleep(1.0)
            app = _state(run)
    if app.state == vpn_apps.CONNECTED:
        return True, "Mullvad VPN: connected" + (f" — {app.detail}" if app.detail else "") + ". Confirm the exit address under Your IP & DNS."
    return False, (f"Mullvad VPN reports {app.state}" + (f" — {app.detail}" if app.detail else "")
                   + ". Traffic protection is not verified.")


def mullvad_disconnect(*, run: Runner = vpn_apps._run, sleep: Sleep = time.sleep) -> tuple[bool, str]:
    cli = vpn_apps.mullvad_cli()
    if not cli:
        return False, "Mullvad's command-line tool was not found."
    code, out = run([cli, "disconnect"], 10.0)
    if code != 0:
        return False, "Mullvad could not disconnect: " + hide_account_numbers(out.strip())[:200]
    app = _state(run)
    for attempt in range(5):
        if app.state == vpn_apps.DISCONNECTED:
            break
        sleep(1.0)
        app = _state(run)
    if app.state == vpn_apps.DISCONNECTED:
        return True, ("Mullvad VPN: disconnected. If its lockdown mode is on, traffic stays blocked "
                      "until you reconnect.")
    return False, f"Mullvad VPN still reports {app.state}."
