"""Crypto-address provider: which addresses are recognised and how answers are read.

Offline: every request is faked per URL.
"""

from __future__ import annotations

import pytest

from providers import crypto_lookup as crypto

BTC_LEGACY = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC_P2SH = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
BTC_BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
ETH = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


class Response:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def serve(monkeypatch, routes):
    """Fake requests.get: the first route whose key ends the URL answers."""
    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        for suffix, response in routes.items():
            if url.endswith(suffix):
                return response
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(crypto.requests, "get", fake_get)
    return seen


@pytest.mark.parametrize("text,chain", [
    (BTC_LEGACY, "bitcoin"), (BTC_P2SH, "bitcoin"), (BTC_BECH32, "bitcoin"),
    (ETH, "ethereum"), (ETH.lower(), "ethereum"),
    ("alice", None), ("0x1234", None), ("example.com", None), ("", None),
])
def test_address_forms_are_recognised(text, chain):
    assert crypto.chain_of(text) == chain


def test_a_non_address_contacts_nothing(monkeypatch):
    monkeypatch.setattr(crypto.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request")))
    result = crypto.lookup("not-an-address")
    assert "error" in result and result["sources_contacted"] == []


def test_bitcoin_balance_is_received_minus_sent(monkeypatch):
    serve(monkeypatch, {
        f"/address/{BTC_BECH32}": Response({
            "chain_stats": {"funded_txo_sum": 17991522, "spent_txo_sum": 14293, "tx_count": 117},
            "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0, "tx_count": 0},
        }),
        f"/address/{BTC_BECH32}/txs": Response([
            {"status": {"confirmed": True, "block_time": 1790000000}},
        ]),
    })
    progress = []
    result = crypto.lookup(BTC_BECH32, on_progress=lambda s, st: progress.append((s, st)))
    chain = result["on_chain"]
    assert (chain["balance_btc"], chain["total_received_btc"], chain["total_sent_btc"]) == (
        "0.17977229", "0.17991522", "0.00014293")
    assert chain["transactions"] == 117 and chain["used"] is True
    assert chain["latest_activity"] == "2026-09-21"
    assert result["sources_contacted"] == [
        {"source": "Blockstream (Bitcoin)", "status": "checked"}]
    assert progress[0] == ("Blockstream (Bitcoin)", "checking")
    assert "not who controls it" in result["note"]


def test_an_unused_bitcoin_address_skips_the_history_request(monkeypatch):
    seen = serve(monkeypatch, {f"/address/{BTC_LEGACY}": Response({
        "chain_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0, "tx_count": 0},
        "mempool_stats": {},
    })})
    result = crypto.lookup(BTC_LEGACY)
    assert result["on_chain"]["used"] is False
    assert "latest_activity" not in result["on_chain"]
    assert len(seen) == 1


def test_a_rejected_bitcoin_address_is_an_error(monkeypatch):
    serve(monkeypatch, {f"/address/{BTC_LEGACY}": Response(status_code=400, text="base58 error")})
    result = crypto.lookup(BTC_LEGACY)
    assert "base58 error" in result["on_chain"]["error"]
    assert result["sources_contacted"][0]["status"] == "error"


def test_ethereum_reads_balance_counts_ens_and_labels(monkeypatch):
    serve(monkeypatch, {
        f"/addresses/{ETH}": Response({
            "coin_balance": "5721681785331996621", "ens_domain_name": "vitalik.eth",
            "is_contract": True, "is_scam": False, "name": None,
            "public_tags": [{"display_name": "Known wallet"}],
        }),
        f"/addresses/{ETH}/counters": Response({
            "transactions_count": "78361", "token_transfers_count": "403472",
        }),
    })
    chain = crypto.lookup(ETH)["on_chain"]
    assert chain["balance_eth"] == "5.721682"
    assert chain["ens_name"] == "vitalik.eth"
    assert (chain["transactions"], chain["token_transfers"]) == (78361, 403472)
    assert chain["public_tags"] == ["Known wallet"]
    assert chain["used"] is True and chain["flagged_as_scam"] is False


def test_an_unknown_ethereum_address_is_unused_not_an_error(monkeypatch):
    serve(monkeypatch, {f"/addresses/{ETH}": Response(status_code=404)})
    result = crypto.lookup(ETH)
    assert result["on_chain"]["used"] is False
    assert result["sources_contacted"][0]["status"] == "checked"
