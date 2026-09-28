"""
Cryptocurrency address provider — public blockchain state for one address.

Zero-cost stack (no keys; public rate limits apply):
  • Blockstream Esplora → Bitcoin: balance, amounts received and sent,
                          transaction count, most recent activity
  • Blockscout          → Ethereum: ETH balance, transaction and token-transfer
                          counts, ENS name, contract flag, and Blockscout's
                          public tags and scam flag

A blockchain shows what an address did, not who controls it. The results
say so, and Bloodhound's prompt is told not to infer ownership from them.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

HEADERS = {"User-Agent": "Sentinel-OSINT/2.0"}
SATS_PER_BTC = 100_000_000
WEI_PER_ETH = 10 ** 18

_BTC = re.compile(r"^(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")
_ETH = re.compile(r"^0x[0-9a-fA-F]{40}$")

OWNERSHIP_NOTE = (
    "Blockchain data shows what this address did, not who controls it. Exchange "
    "and custodial addresses mix many users' funds."
)


def chain_of(address: str) -> str | None:
    """"bitcoin", "ethereum", or None when the text is neither address form."""
    text = (address or "").strip()
    if _ETH.fullmatch(text):
        return "ethereum"
    if _BTC.fullmatch(text):
        return "bitcoin"
    return None


def _btc_amount(sats: int) -> str:
    return f"{sats / SATS_PER_BTC:.8f}"


def _bitcoin(address: str) -> dict:
    try:
        resp = requests.get(f"https://blockstream.info/api/address/{address}",
                            headers=HEADERS, timeout=15)
        if resp.status_code == 400:
            return {"error": f"Blockstream rejected the address ({resp.text[:80]})"}
        if resp.status_code != 200:
            return {"error": f"Blockstream HTTP {resp.status_code}"}
        data = resp.json()
        chain = data.get("chain_stats") or {}
        pending = data.get("mempool_stats") or {}
        received = chain.get("funded_txo_sum", 0)
        sent = chain.get("spent_txo_sum", 0)
        result = {
            "explorer_url": f"https://blockstream.info/address/{address}",
            "balance_btc": _btc_amount(received - sent),
            "total_received_btc": _btc_amount(received),
            "total_sent_btc": _btc_amount(sent),
            "transactions": chain.get("tx_count", 0),
            "unconfirmed_transactions": pending.get("tx_count", 0),
            "unconfirmed_change_btc": _btc_amount(
                pending.get("funded_txo_sum", 0) - pending.get("spent_txo_sum", 0)),
            "used": bool(chain.get("tx_count") or pending.get("tx_count")),
        }
        if result["used"]:
            result["latest_activity"] = _btc_latest(address)
        return result
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _btc_latest(address: str) -> str | None:
    """Date of the newest confirmed transaction (the API lists newest first)."""
    try:
        resp = requests.get(f"https://blockstream.info/api/address/{address}/txs",
                            headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        for tx in resp.json():
            stamp = (tx.get("status") or {}).get("block_time")
            if stamp:
                return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _ethereum(address: str) -> dict:
    base = f"https://eth.blockscout.com/api/v2/addresses/{address}"
    try:
        resp = requests.get(base, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return {"used": False, "explorer_url": f"https://eth.blockscout.com/address/{address}"}
        if resp.status_code != 200:
            return {"error": f"Blockscout HTTP {resp.status_code}"}
        data = resp.json()
        wei = int(data.get("coin_balance") or 0)
        result = {
            "explorer_url": f"https://eth.blockscout.com/address/{address}",
            "balance_eth": f"{wei / WEI_PER_ETH:.6f}",
            "ens_name": data.get("ens_domain_name"),
            "is_contract": bool(data.get("is_contract")),
            "contract_name": data.get("name"),
            "flagged_as_scam": bool(data.get("is_scam")),
            "public_tags": [
                tag.get("display_name") or tag.get("label")
                for tag in data.get("public_tags") or [] if isinstance(tag, dict)
            ],
        }
        counters = requests.get(f"{base}/counters", headers=HEADERS, timeout=15)
        if counters.status_code == 200:
            counts = counters.json()
            result["transactions"] = int(counts.get("transactions_count") or 0)
            result["token_transfers"] = int(counts.get("token_transfers_count") or 0)
        result["used"] = bool(wei or result.get("transactions") or result.get("token_transfers"))
        return result
    except Exception as exc:
        return {"error": str(exc)[:300]}


def lookup(address: str, *, on_progress=None, should_stop=None) -> dict:
    """Public on-chain facts for a Bitcoin or Ethereum address."""
    address = (address or "").strip()
    chain = chain_of(address)
    result: dict = {
        "type": "crypto", "query": address, "chain": chain,
        "sources_contacted": [], "note": OWNERSHIP_NOTE,
    }
    if chain is None:
        result["error"] = ("Not a recognised Bitcoin or Ethereum address — "
                           "skipping live lookup.")
        return result
    if should_stop and should_stop():
        result["cancelled"] = True
        return result
    label, fetch = (("Blockstream (Bitcoin)", _bitcoin) if chain == "bitcoin"
                    else ("Blockscout (Ethereum)", _ethereum))
    if on_progress:
        on_progress(label, "checking")
    result["on_chain"] = fetch(address)
    status = "error" if result["on_chain"].get("error") else "checked"
    result["sources_contacted"].append({"source": label, "status": status})
    if on_progress:
        on_progress(label, status)
    return result
