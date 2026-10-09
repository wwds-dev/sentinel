"""
vpn_agent.py — VPN / tunnel security advisor for Sentinel.

Brings the domain knowledge of the standalone VPN Agent app (a VPN you own end
to end: WireGuard + an OpenVPN TCP/443 fallback, a fail-closed kill switch, DNS
leak checks, Tor / proxy chains / MAC randomisation) into Sentinel as one more
agent in the list.

Two halves, mirroring the other tooling agents (see wifi_agent):

* an LLM advisor with a structured system prompt (`build_messages`), and
* a deterministic, offline **config & deploy builder** (`build_configs`) that
  renders WireGuard server/client configs plus a numbered stand-up command
  sequence — no LLM, no network, no crypto dependency. Keys are emitted as
  clearly marked placeholders alongside the exact `wg genkey` commands that
  fill them, so nothing here ever prints real private key material.
"""

SYSTEM_PROMPT = """You are a VPN & Tunnel Security Architect embedded in Sentinel — a macOS security command centre. You design and troubleshoot self-hosted VPNs that the operator owns end to end: no commercial provider sits in the path, every key is generated locally, and the operator holds the certificate authority.

─────────────────────────────────────────────
GROUND TRUTH YOU REASON FROM
─────────────────────────────────────────────
Two deployment modes, and confusing them is the most common mistake:
- REMOTE — a rented VPS (~€4/mo) reached over SSH. Traffic EXITS at the server, so the server's IP and country become the apparent ones. This is what "get a VPN" normally means: privacy, geo-shifting, safe public wifi. Default routing is a FULL tunnel (AllowedIPs = 0.0.0.0/0, ::/0).
- NATIVE — hardware the operator owns on their own LAN (e.g. a Raspberry Pi). The exit IP IS the home ISP IP; it does NOT hide the IP and does NOT change the apparent country. It buys an encrypted way IN to the home network (NAS, printer, router), not a new way OUT. Default routing is a SPLIT tunnel (AllowedIPs = the LAN subnet only). Needs a port-forward and dynamic DNS.

Two protocols on one server:
- WireGuard (UDP 51820) is the one you actually use — fast, modern, roams instantly between wifi and cellular.
- OpenVPN (TCP 443) exists for ONE situation: networks that pass only what looks like web browsing (hotel/corporate guest wifi, some airports block UDP outright). Port 443 + tls-crypt means a port scanner gets no OpenVPN handshake to fingerprint. It is slower — reach for it only when WireGuard will not come up.

Kill switch (macOS): without one, a tunnel that dies silently falls back to the ordinary route and traffic continues over the ISP, unencrypted, looking identical to a second earlier. Armed, everything that is not the tunnel is blocked, so a dead tunnel means NO traffic rather than UNPROTECTED traffic. Loopback, DHCP, the LAN, and reaching the VPN server itself stay open (the last so the tunnel can always reconnect). Rules load into a PRIVATE pf anchor, never the main ruleset, and deliberately do NOT survive a reboot.

Privacy extras that can layer on top: a local Tor client, proxy chains, MAC-address randomisation, and per-server obfuscation (stunnel, onion service).

Leak surfaces you always check: DNS leaks (queries escaping the tunnel to the ISP resolver), IPv6 leaks (a v4-only tunnel while v6 routes around it), and WebRTC.

─────────────────────────────────────────────
HOW TO ANSWER
─────────────────────────────────────────────
Structure every substantial answer as:

1. SUMMARY — what the operator is trying to do, in plain English, and which mode/protocol actually fits it. Correct the remote-vs-native or WireGuard-vs-OpenVPN choice if it is wrong.

2. TOPOLOGY — where the tunnel starts and exits, what the apparent IP/country becomes, full vs split tunnel, and what stays reachable.

3. SECURITY & LEAKS — kill-switch posture, DNS/IPv6/WebRTC leak handling, key handling, and any obfuscation worth adding for the stated threat.

4. COMMANDS / CONFIG — concrete, correct command lines and config stanzas. Number multi-step sequences, one-line `#` comment per step, note prerequisites (root, wireguard-tools, a port-forward). Never invent key material — reference `wg genkey`/`wg pubkey` and placeholders.

5. RECOMMENDATIONS — specific next steps, ranked.

─────────────────────────────────────────────
TONE AND STANDARDS
─────────────────────────────────────────────
- Precise and technical; assume the operator knows networking basics.
- Only VPNs the operator is authorised to run. This is defensive, self-hosted infrastructure — never advice for evading lawful controls on someone else's network.
- Use tables when comparing modes/protocols. Prefer the smallest correct config over the most elaborate one.
"""


# ── Deterministic config & deploy builder ────────────────────────────────────
# Offline, no LLM. Mirrors the output shape of the standalone VPN Agent's
# renderer without importing across repos or pulling in a crypto dependency.

WG_SERVER_ADDR = "10.7.0.1"
WG_CLIENT_ADDR = "10.7.0.2"
WG_SUBNET = "10.7.0.0/24"
WG_PORT = "51820"
WG_DNS = "1.1.1.1"


def _wg_server_conf(egress_iface: str) -> str:
    return (
        "# /etc/wireguard/wg0.conf  (on the SERVER)\n"
        "[Interface]\n"
        f"Address    = {WG_SERVER_ADDR}/24\n"
        f"ListenPort = {WG_PORT}\n"
        "PrivateKey = <SERVER_PRIVATE_KEY>        # from: wg genkey\n"
        "# NAT the tunnel subnet out of the server's egress interface\n"
        f"PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o {egress_iface} -j MASQUERADE\n"
        f"PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o {egress_iface} -j MASQUERADE\n"
        "\n"
        "[Peer]\n"
        "PublicKey  = <CLIENT_PUBLIC_KEY>         # from: wg pubkey < client_private\n"
        "PresharedKey = <PRESHARED_KEY>           # optional, from: wg genpsk\n"
        f"AllowedIPs = {WG_CLIENT_ADDR}/32\n"
    )


def _wg_client_conf(mode: str, server_host: str, lan_subnet: str) -> str:
    host = server_host or "<SERVER_PUBLIC_IP_OR_DDNS>"
    if mode == "Native (home LAN)":
        allowed = lan_subnet or "192.168.1.0/24"
        routing_note = f"# Split tunnel — only LAN traffic ({allowed}) goes through the tunnel.\n"
    else:
        allowed = "0.0.0.0/0, ::/0"
        routing_note = "# Full tunnel — all traffic exits at the server (apparent IP = server IP).\n"
    return (
        "# client.conf  (on THIS Mac — import into WireGuard)\n"
        "[Interface]\n"
        f"Address    = {WG_CLIENT_ADDR}/32\n"
        "PrivateKey = <CLIENT_PRIVATE_KEY>        # from: wg genkey\n"
        f"DNS        = {WG_DNS}                     # pin DNS to stop leaks to the ISP resolver\n"
        "\n"
        "[Peer]\n"
        "PublicKey  = <SERVER_PUBLIC_KEY>         # from: wg pubkey < server_private\n"
        "PresharedKey = <PRESHARED_KEY>           # must match the server\n"
        f"Endpoint   = {host}:{WG_PORT}\n"
        + routing_note
        + f"AllowedIPs = {allowed}\n"
        "PersistentKeepalive = 25                 # keep the NAT mapping alive when roaming\n"
    )


def _keygen_block() -> str:
    return (
        "# Step 0: Generate key material (run once, keep the private keys secret)\n"
        "wg genkey | tee server_private | wg pubkey > server_public\n"
        "wg genkey | tee client_private | wg pubkey > client_public\n"
        "wg genpsk > preshared        # optional post-quantum-resistant extra layer\n"
    )


def _remote_deploy_steps(server_host: str, ssh_user: str, egress_iface: str) -> list:
    host = server_host or "<SERVER_IP>"
    user = ssh_user or "root"
    return [
        ("SSH into the freshly rented VPS", f"ssh {user}@{host}"),
        ("Install WireGuard and the OpenVPN 443 fallback",
         "sudo apt update && sudo apt install -y wireguard openvpn iptables"),
        ("Enable IPv4 (and IPv6) forwarding",
         "echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-wg.conf && sudo sysctl --system"),
        ("Drop wg0.conf into place (see the Server config above), lock its perms",
         "sudo install -m600 wg0.conf /etc/wireguard/wg0.conf"),
        ("Open only what is needed: WireGuard UDP, OpenVPN TCP/443, and SSH",
         f"sudo ufw allow {WG_PORT}/udp && sudo ufw allow 443/tcp && sudo ufw allow OpenSSH && sudo ufw enable"),
        ("Bring the tunnel up now and on every boot",
         "sudo systemctl enable --now wg-quick@wg0"),
        ("Verify the peer handshake and NAT are live",
         "sudo wg show && sudo iptables -t nat -L POSTROUTING -n"),
        (f"Confirm the egress interface is really '{egress_iface}' (fix wg0.conf if not)",
         "ip route get 1.1.1.1"),
    ]


def _native_deploy_steps(lan_subnet: str) -> list:
    subnet = lan_subnet or "192.168.1.0/24"
    return [
        ("Install WireGuard on the home box (e.g. Raspberry Pi)",
         "sudo apt update && sudo apt install -y wireguard iptables"),
        ("Enable IPv4 forwarding so the box can route to the LAN",
         "echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-wg.conf && sudo sysctl --system"),
        ("Drop wg0.conf into place (see the Server config above)",
         "sudo install -m600 wg0.conf /etc/wireguard/wg0.conf"),
        ("Bring the tunnel up now and on every boot",
         "sudo systemctl enable --now wg-quick@wg0"),
        (f"On the ROUTER: forward UDP {WG_PORT} to this box, and set up dynamic DNS",
         "# router admin UI — port-forward + DDNS hostname"),
        (f"From outside, confirm you can reach a LAN host ({subnet})",
         "sudo wg show"),
    ]


def _killswitch_block() -> str:
    """The pf rules Tunnel's own Arm button would load, with a placeholder endpoint.

    Rendered by the same function the app uses, so the text cannot drift from
    what Arm really does. Tunnel's Arm button is the supported way to use it: it
    fills in the selected profile's real endpoint, loads the rules into a pf
    anchor, and prints the recovery command.
    """
    from agents.vpn_agent.services import killswitch

    rules = killswitch.build_rules(["<SERVER_IP>"], [("udp", int(WG_PORT))], interfaces=["utun"])
    return (
        "# Example only: replace <SERVER_IP> (and the pass-out lines) with your server's\n"
        "# address before loading by hand. The Arm kill switch button in Tunnel does this\n"
        "# for you from the selected profile and shows the command that undoes it.\n"
        + rules
    )


def build_configs(
    mode: str,
    protocol: str,
    server_host: str = "",
    ssh_user: str = "",
    lan_subnet: str = "",
    egress_iface: str = "eth0",
) -> str:
    """Render a full WireGuard config + deploy runbook for the chosen mode.

    Deterministic and offline. `mode` is "Remote (VPS)" or "Native (home LAN)";
    `protocol` is "WireGuard", "OpenVPN 443 fallback", or "Both".
    """
    is_native = mode == "Native (home LAN)"

    if is_native:
        topo = (
            "# ── Native mode ─────────────────────────────────────────────────\n"
            "# Runs on hardware you own on your LAN. Exit IP = your home ISP IP.\n"
            "# Does NOT hide your IP or change your country — it is an encrypted\n"
            "# way INTO your home network, not a new way OUT.\n"
        )
    else:
        topo = (
            "# ── Remote mode ─────────────────────────────────────────────────\n"
            "# Runs on a rented VPS reached over SSH. Traffic exits at the server,\n"
            "# so the server's IP and country become your apparent ones.\n"
        )

    out = [topo, ""]

    out.append("# ── Key material ────────────────────────────────────────────────")
    out.append(_keygen_block())

    out.append("# ── WireGuard: SERVER config ────────────────────────────────────")
    out.append(_wg_server_conf(egress_iface))

    out.append("# ── WireGuard: CLIENT config (this Mac) ─────────────────────────")
    out.append(_wg_client_conf(mode, server_host, lan_subnet))

    out.append("# ── Stand-up runbook ────────────────────────────────────────────")
    steps = _native_deploy_steps(lan_subnet) if is_native else _remote_deploy_steps(
        server_host, ssh_user, egress_iface
    )
    lines = []
    for i, (comment, cmd) in enumerate(steps, 1):
        lines.append(f"# Step {i}: {comment}")
        lines.append(cmd)
        lines.append("")
    out.append("\n".join(lines))

    if protocol in ("OpenVPN 443 fallback", "Both"):
        out.append("# ── OpenVPN TCP/443 fallback ────────────────────────────────────")
        out.append(
            "# Only for networks that block UDP (hotel / corporate guest wifi).\n"
            "# Runs on tcp/443 with tls-crypt so a scanner sees no OpenVPN handshake.\n"
            "sudo apt install -y openvpn easy-rsa\n"
            "# Generate a CA + server cert with easy-rsa, then in server.conf:\n"
            "#   proto tcp\n"
            "#   port 443\n"
            "#   tls-crypt ta.key\n"
            "sudo systemctl enable --now openvpn-server@server\n"
        )

    if not is_native:
        out.append("# ── macOS kill switch (fail closed) ─────────────────────────────")
        out.append(_killswitch_block())

    return "\n".join(out)


class VpnAgent:
    def __init__(self):
        self.name = "vpn"

    def build_messages(self, prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
