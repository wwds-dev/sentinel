"""Beacon Wi-Fi scanning on modern macOS (system_profiler, not the removed airport).

Covers the airport-removal fix (scan/signal now parse SPAirPortDataType) and the
USB VID/PID normaliser fix (system_profiler appends the vendor name to the id).
No subprocess is spawned — synthetic data stands in for system_profiler.
"""

from __future__ import annotations

import agents.wifi_agent as wifi


# Shape mirrors real `system_profiler SPAirPortDataType -json` (SSIDs are dummies).
SP_DATA = {
    "SPAirPortDataType": [{
        "spairport_airport_interfaces": [
            {
                "_name": "en0",
                "spairport_status_information": "spairport_status_connected",
                "spairport_current_network_information": {
                    "_name": "HomeNet",
                    "spairport_network_channel": "6 (2GHz, 20MHz)",
                    "spairport_security_mode": "spairport_security_mode_wpa2_personal",
                    "spairport_signal_noise": "-31 dBm / -99 dBm",
                    "spairport_network_phymode": "802.11ax",
                    "spairport_network_rate": 300,
                },
                "spairport_airport_other_local_wireless_networks": [
                    {"_name": "FarNet", "spairport_network_channel": "11",
                     "spairport_security_mode": "spairport_security_mode_wpa3_personal",
                     "spairport_signal_noise": "-80 dBm / -100 dBm"},
                    {"_name": "NearNet", "spairport_network_channel": "1",
                     "spairport_security_mode": "spairport_security_mode_none",
                     "spairport_signal_noise": "-55 dBm / -98 dBm"},
                ],
            },
            {"_name": "awdl0", "spairport_current_network_information": {
                "spairport_network_type": "spairport_network_type_p2p"}},
        ],
    }],
}


def test_report_shows_current_network_with_real_signal_and_security():
    report = wifi.format_wifi_report(SP_DATA, "Signal Monitor")
    assert "SSID: HomeNet" in report
    assert "Signal: -31 dBm (noise -99 dBm)" in report
    assert "Security: WPA2" in report
    assert "Interface en0 — connected" in report


def test_report_lists_nearby_networks_sorted_by_signal():
    report = wifi.format_wifi_report(SP_DATA, "Scan Networks")
    assert "Nearby networks (2)" in report
    # NearNet (-55) must appear before FarNet (-80)
    assert report.index("NearNet") < report.index("FarNet")
    assert "Open" in report          # none -> Open
    assert "WPA3" in report


def test_report_skips_non_wifi_interfaces_and_notes_no_bssid():
    report = wifi.format_wifi_report(SP_DATA)
    assert "awdl0" not in report
    assert "BSSID" in report          # the honesty note about the API limit


def test_empty_data_is_an_honest_error_not_fabricated_networks():
    assert wifi.format_wifi_report({}).startswith("[Error]")
    assert wifi.format_wifi_report(
        {"SPAirPortDataType": [{"spairport_airport_interfaces": []}]}
    ).startswith("[Error]")


def test_security_and_signal_helpers():
    assert wifi._security_label("spairport_security_mode_wpa3_transition") == "WPA3"
    assert wifi._security_label("spairport_security_mode_none") == "Open"
    assert wifi._security_label("spairport_security_mode_wep") == "WEP"
    assert wifi._signal_dbm("-31 dBm / -99 dBm") == (-31, -99)
    assert wifi._signal_dbm(None) == (None, None)


# ── USB VID/PID normalisation (the "missed" adapter-detection defect) ────────

def test_usb_ids_with_appended_vendor_name_still_match():
    # Real macOS shape: "0x0bda  (Realtek Semiconductor Corp.)"
    node = {"_items": [{
        "_name": "802.11ac NIC",
        "vendor_id": "0x0bda  (Realtek Semiconductor Corp.)",
        "product_id": "0x8812",
    }]}
    found: list[dict] = []
    wifi._walk_usb(node, found)
    assert len(found) == 1
    assert found[0]["name"] == "AWUS036ACH"
    assert found[0]["chipset"] == "RTL8812AU"


def test_plain_hex_ids_still_match():
    node = {"vendor_id": "0x0cf3", "product_id": "0x9271", "_name": "x"}
    found: list[dict] = []
    wifi._walk_usb(node, found)
    assert found and found[0]["name"] == "TL-WN722N"


def test_unknown_adapter_is_not_reported():
    node = {"vendor_id": "0xdead", "product_id": "0xbeef", "_name": "x"}
    found: list[dict] = []
    wifi._walk_usb(node, found)
    assert found == []


def test_scan_wifi_handles_a_missing_tool(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("no system_profiler")
    monkeypatch.setattr(wifi.subprocess, "run", _boom)
    assert wifi.scan_wifi().startswith("[Error]")


# ── Kali command builder: injection gate (QA audit must-fix #5) ─────────────
#
# `build_kali_commands()` must refuse to generate an injection-class step when
# the selected adapter cannot inject, for every operation that emits one —
# not just "Deauth Attack". "Handshake Capture" also forces a deauth
# (`aireplay-ng -0 10 ...`) to provoke the WPA handshake, so it needs the same
# gate; without it, a passive-only adapter (e.g. TL-WN725N V3) was handed a
# command sequence it cannot run.

NON_INJECT_ADAPTER = {
    "name": "TL-WN725N V3", "chipset": "RTL8188EU", "monitor": True, "inject": False,
    "kali_iface": "wlan0",
}
INJECT_ADAPTER = {
    "name": "TL-WN722N", "chipset": "AR9271", "monitor": True, "inject": True,
    "kali_iface": "wlan0",
}


def test_handshake_capture_refuses_a_non_injecting_adapter():
    cmds = wifi.build_kali_commands(
        "Handshake Capture", NON_INJECT_ADAPTER, "AA:BB:CC:DD:EE:FF", "6", "LabNet")
    assert "does not support packet injection" in cmds
    assert "aireplay-ng" not in cmds


def test_handshake_capture_runs_normally_on_an_injecting_adapter():
    cmds = wifi.build_kali_commands(
        "Handshake Capture", INJECT_ADAPTER, "AA:BB:CC:DD:EE:FF", "6", "LabNet")
    assert "aireplay-ng -0 10" in cmds
    assert "does not support packet injection" not in cmds


def test_deauth_attack_still_refuses_a_non_injecting_adapter():
    cmds = wifi.build_kali_commands(
        "Deauth Attack", NON_INJECT_ADAPTER, "AA:BB:CC:DD:EE:FF", "6", "LabNet")
    assert "does not support packet injection" in cmds
    assert "aireplay-ng" not in cmds
