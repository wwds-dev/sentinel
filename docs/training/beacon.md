# Beacon — authorised Wi-Fi assessment

**Goal:** diagnose your network and understand the lab-only Kali workflow. **Time:** 20 minutes.

![Beacon workspace](docs/training/images/beacon.png)

Use Beacon only on networks you own or have written permission to test.

## Modes

- **Interface Info** describes the Mac's network interfaces.
- **Scan Networks** lists nearby wireless networks visible to the device.
- **Signal Monitor** takes the same single snapshot as Scan Networks — despite
  the name, it does not poll or follow signal conditions over time. Run it
  again to refresh the reading.
- **Ping Test** checks reachability to the host you enter.
- **Kali Command Builder** prepares reviewed command sequences for a separate
  authorised Kali lab; it does not silently run them on the Mac.

The Interface field lists common names (`en0`, `en1`, …) but is informational
only: no mode currently reads it, so changing it has no effect on which
interface is diagnosed. Enter a target host only for Ping Test. **Detect
Adapters** identifies known USB adapters and reports whether their chipset is
expected to support monitor mode or injection. Detection is guidance; drivers
and operating-system support still matter.

## Adapter and internet setup

You do **not** need an external adapter for Interface Info, Scan Networks,
Signal Monitor or Ping Test on macOS. Those diagnostics can use the Mac's
built-in Wi-Fi. The external adapter is for monitor mode and packet injection,
which generally happen inside Kali rather than macOS.

Use **Run Preflight** before preparing Kali work. It only reads status: it lists
interfaces, marks the current default route as **Internet / control**, and shows
known USB monitor adapters. It never changes mode, disconnects Wi-Fi or runs an
intrusive command.

The recommended arrangement is two interfaces:

1. Keep the Mac's built-in Wi-Fi or Ethernet connected for internet, model
   access and control.
2. Dedicate the supported USB Wi-Fi adapter to Kali and monitor mode.

A single Wi-Fi adapter cannot stay associated with an access point in ordinary
managed mode while that same adapter is in monitor mode. If it is your only
connection, switching it can remove internet access. Preflight warns when it
cannot find a separate routed connection.

For a Kali virtual machine, select the USB adapter in the hypervisor's USB
passthrough menu. It will normally disappear from macOS while attached to the
guest. Kali must have a matching chipset driver, and some hypervisors, hubs and
Apple-silicon guests have USB or driver limitations. Confirm the adapter appears
in Kali before following generated commands. Make every mode change yourself;
Sentinel intentionally does not do it silently.

Kali mode requests an operation, adapter, BSSID, channel and ESSID. Intrusive
operations can disconnect users or disrupt service. Run them only in an
isolated lab or during an explicitly approved test window. Review every command
and confirm the target identifiers before using it outside Sentinel. A BSSID,
channel or ESSID that doesn't look like a real value is not rejected with a
message — it is silently swapped for a placeholder in the generated commands,
so check the command header rather than assuming your input was used. Note
too that Run Preflight is re-read automatically every time you generate Kali
commands, and that Save in Kali mode stores only the commands, not the
Preflight warning shown above them.

**AI interpretation** is optional and off by default because raw network output
may reveal device names, addresses and infrastructure details. The provider
dropdown still defaults to Sentinel's standard provider, which may be a paid
cloud model — switching to a local model or redacting details is something
you still have to do yourself; Beacon does not do either automatically before
sending a scan to whichever provider is selected.

## Exercise

Run Interface Info and a Ping Test against your own router. Explain the
difference between passive observation and an intrusive lab operation before
opening Kali Command Builder. Then run Preflight and confirm that you can explain
which interface keeps internet access and which would be dedicated to monitoring.
