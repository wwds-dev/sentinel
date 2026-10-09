"""
health_monitor.py — Background health monitoring for VPN Agent.

Runs periodic checks on public IP, DNS resolvers, and WireGuard tunnel state.
Emits Qt signals when unexpected changes are detected so the GUI can warn the user.
All checks run in background threads — never blocks the GUI.
"""

from PySide6.QtCore import QObject, QTimer, QThread, Signal


class _Worker(QObject):
    """Minimal inline worker — runs a function in a thread."""
    result = Signal(object)
    finished = Signal()

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self):
        try:
            self.result.emit(self._fn(*self._args))
        except Exception as e:
            self.result.emit({"_worker_error": str(e)})
        self.finished.emit()


class HealthMonitor(QObject):
    """
    Periodically checks VPN health and emits signals on unexpected changes.

    Signals:
        warning(str)         — something is wrong, show it prominently
        info(str)            — non-critical state change
        tunnel_dropped(str)  — interface name of the dropped tunnel
        tunnel_up(str)       — interface name of a newly active tunnel

    Usage:
        monitor = HealthMonitor(interval_seconds=30)
        monitor.set_watched_interfaces(["wg0", "wg2"])
        monitor.warning.connect(my_handler)
        monitor.start()
    """

    warning = Signal(str)
    info = Signal(str)
    tunnel_dropped = Signal(str)
    tunnel_up = Signal(str)

    def __init__(self, interval_seconds: int = 30, parent=None, *,
                 check_public_ip: bool = False, tunnel_source=None):
        """``check_public_ip`` is off by default: it contacts an outside service every
        interval, which Sentinel only does when the person asks. ``tunnel_source`` lets
        the host report tunnels the WireGuard run directory cannot see (OpenVPN)."""
        super().__init__(parent)
        self._check_public_ip = check_public_ip
        self._tunnel_source = tunnel_source
        self._interval_ms = interval_seconds * 1000
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._run_checks)

        # Tracked state — None means "not yet seen"
        self._last_ip: str | None = None
        self._last_dns: list | None = None
        self._last_tunnels: set = set()
        self._watched_interfaces: list[str] = []
        self._threads: list = []

    def set_watched_interfaces(self, interfaces: list[str]) -> None:
        """Tell the monitor which WireGuard interfaces should stay up."""
        self._watched_interfaces = interfaces

    def start(self) -> None:
        self._timer.start(self._interval_ms)

    def stop(self) -> None:
        """Stop the timer and hard-kill any in-flight check threads."""
        self._timer.stop()
        for thread in list(self._threads):
            if thread.isRunning():
                thread.quit()
                if not thread.wait(1500):
                    thread.terminate()
                    thread.wait()
        self._threads.clear()

    def force_check(self) -> None:
        """Run an immediate check outside the normal interval."""
        self._run_checks()

    # ── Internal ─────────────────────────────────

    def _spawn(self, fn, *args, on_result=None) -> None:
        """Run fn(*args) in a background QThread, call on_result in main thread."""
        thread = QThread()
        worker = _Worker(fn, *args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        if on_result:
            worker.result.connect(on_result)
        self._threads.append(thread)
        thread.finished.connect(
            lambda: self._threads.remove(thread) if thread in self._threads else None
        )
        thread.start()

    def _run_checks(self) -> None:
        # Import here to avoid circular imports at module load time
        from agents.vpn_agent.services import dns_check, wireguard_manager
        from agents.vpn_agent.services import public_ip
        if self._check_public_ip:
            self._spawn(public_ip.get_public_ip, on_result=self._on_ip)
        self._spawn(dns_check.get_system_dns_servers, on_result=self._on_dns)
        self._spawn(self._tunnel_source or wireguard_manager.list_active_tunnels,
                    on_result=self._on_tunnels)

    def _on_ip(self, current) -> None:
        if isinstance(current, dict):
            return  # worker error
        # Ignore transient failures — only warn on real IP changes
        transient = ("Error", "Timeout", "No connection", "Failed")
        if any(t in str(current) for t in transient):
            return
        if self._last_ip is None:
            self._last_ip = current
            return
        if current != self._last_ip:
            self.warning.emit(
                f"Public IP changed: {self._last_ip} → {current}  "
                f"(VPN may have dropped or switched)"
            )
            self._last_ip = current

    def _on_dns(self, servers) -> None:
        if not isinstance(servers, list):
            return
        if self._last_dns is None:
            self._last_dns = servers
            return
        if set(servers) != set(self._last_dns):
            self.warning.emit(
                f"DNS resolvers changed: {self._last_dns} → {servers}  "
                f"(Check for DNS leak)"
            )
            self._last_dns = servers

    def _on_tunnels(self, active_list) -> None:
        if not isinstance(active_list, list):
            return
        current = set(active_list)

        if not self._watched_interfaces:
            # No profile selected — just track state silently
            self._last_tunnels = current
            return

        for iface in self._watched_interfaces:
            was_up = iface in self._last_tunnels
            is_up = iface in current
            if was_up and not is_up:
                self.tunnel_dropped.emit(iface)
                self.warning.emit(
                    f"TUNNEL DROPPED — {iface} went offline unexpectedly! "
                    f"Your traffic is now unprotected. Press Connect to restore."
                )
            elif not was_up and is_up:
                self.tunnel_up.emit(iface)
                self.info.emit(f"Tunnel active: {iface}")

        self._last_tunnels = current
