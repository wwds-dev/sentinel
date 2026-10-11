"""Trace's advisory target-type classifier: pure, offline, never re-routes."""

from __future__ import annotations

import pytest

from agents.osint_agent import (
    OSINTAgent, TypeHint, classify_target, crypto_address_chain,
)

BTC_LEGACY = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"        # genesis block (P2PKH)
BTC_P2SH = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
BTC_BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
BTC_TAPROOT = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"
ETH = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


@pytest.mark.parametrize("address,chain", [
    (BTC_LEGACY, "Bitcoin"), (BTC_P2SH, "Bitcoin"), (BTC_BECH32, "Bitcoin"),
    (BTC_BECH32.upper(), "Bitcoin"), (BTC_TAPROOT, "Bitcoin"), (ETH, "Ethereum"),
    ("0x" + "ab" * 20, "Ethereum"),
    ("LdP8Qox1VAhCzLJNqrr74YovaWYyNBUWvL", "Litecoin"),
    ("TJRabPrwbZy45sbavfcjinPJC18kjpRTv8", "Tron"),
])
def test_valid_crypto_addresses_are_recognised(address, chain):
    assert crypto_address_chain(address) == chain
    for resolved in ("Username", "Domain", "Person"):
        hint = classify_target(address, resolved)
        assert isinstance(hint, TypeHint) and hint.suggested == "Crypto Address"
        assert "crypto address" in hint.message.lower() or chain in hint.message


@pytest.mark.parametrize("value", [
    "alice", "alice_dev", "bob123", "Jane-Doe_99", "@handle", "x" * 40,
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb",          # bad checksum: not reported
    "0x1234", "0x" + "g" * 40, "bc1invalid", "1" * 30,
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",
])
def test_ordinary_usernames_and_near_misses_are_not_flagged(value):
    assert classify_target(value, "Username") is None


@pytest.mark.parametrize("value", ["example.com", "https://example.com/path", "sub.example.co.uk",
                                   "acme.io", "acme.test"])
def test_real_looking_domains_suggest_domain_when_typed_as_something_else(value):
    for resolved in ("Username", "Person", "Company"):
        hint = classify_target(value, resolved)
        assert hint is not None and hint.suggested == "Domain"
        assert "use Domain" in hint.message
    assert classify_target(value, "Domain") is None          # already right


@pytest.mark.parametrize("value", ["john.smith", "anna.mueller", "jane.q.public"])
def test_dotted_names_that_are_not_plausible_domains_are_hinted(value):
    hint = classify_target(value, "Domain")
    assert hint is not None and hint.suggested == "Username or Person"
    assert "not a domain" in hint.message
    assert classify_target(value, "Username") is None        # already a handle


def test_dotted_name_with_a_real_ending_is_a_domain_not_a_name():
    assert classify_target("john.com", "Domain") is None
    assert classify_target("anna.de", "Domain") is None


@pytest.mark.parametrize("value,resolved", [
    ("", "Username"), ("   ", "Domain"), ("a@b.com", "Email"), ("8.8.8.8", "IP Address"),
    ("+49 30 123456", "Phone"), ("John Smith", "Person"), ("Acme Ltd", "Company"),
])
def test_other_input_gets_no_hint(value, resolved):
    assert classify_target(value, resolved) is None


def test_classifier_is_pure_and_does_not_touch_validation():
    before = OSINTAgent.validate_target("john.smith", "Auto-detect")
    classify_target("john.smith", before.query_type)
    after = OSINTAgent.validate_target("john.smith", "Auto-detect")
    assert before == after and after.query_type == "Domain"      # still resolved as before
    assert OSINTAgent.validate_target(ETH, "Auto-detect").query_type == "Username"
