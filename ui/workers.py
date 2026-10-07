"""Background worker threads.

Moved verbatim out of main.py (see docs/refactor_plan.md, phase 1). These are
standalone QThread subclasses: they take everything they need as constructor
arguments and touch no application state, which is why they move first.
"""
import re
import subprocess
import time

from PySide6.QtCore import QThread, Signal

from services.local_file_search import FileSearchFilters, FileSearchReport, search_files
from services.remote_file_search import search_remote_files
from services.vpn_diagnostics import collect_vpn_diagnostics


class DomainLookupWorker(QThread):
    """Run consented domain/IP public-source checks off the UI thread."""

    progress_signal = Signal(str, str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, target: str):
        super().__init__()
        self.target = target
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            from providers.domain_lookup import lookup

            result = lookup(
                self.target,
                on_progress=lambda source, status: self.progress_signal.emit(
                    source, status
                ),
                should_stop=lambda: self._cancel_requested,
            )
            self.finished_signal.emit(result)
        except Exception as error:
            self.error_signal.emit(str(error))


class IdentityLookupWorker(QThread):
    """Run a consented username, email, or company public-source lookup."""

    progress_signal = Signal(str, str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, target: str, query_type: str, sources=()):
        super().__init__()
        self.target = target
        self.query_type = query_type
        self.sources = tuple(sources)
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            progress = lambda source, status: self.progress_signal.emit(source, status)
            stopped = lambda: self._cancel_requested
            if self.query_type == "Username":
                from providers.username_lookup import lookup
                result = lookup(
                    self.target, on_progress=progress, should_stop=stopped
                )
            elif self.query_type == "Email":
                from providers.email_lookup import lookup
                result = lookup(
                    self.target,
                    selected_sources=self.sources,
                    on_progress=progress,
                    should_stop=stopped,
                )
            elif self.query_type == "Company":
                from providers.company_lookup import lookup
                result = lookup(
                    self.target,
                    sanctions="opensanctions" in self.sources,
                    # CourtListener is free, keyless and metadata-only, so Trace
                    # runs it for every company lookup, like the GLEIF base search.
                    court_records=True,
                    on_progress=progress, should_stop=stopped,
                )
            else:
                raise ValueError(f"Unsupported identity lookup type: {self.query_type}")
            self.finished_signal.emit(result)
        except Exception as error:
            self.error_signal.emit(str(error))


class ExposureLookupWorker(QThread):
    """Run a consented dark-web exposure check (leak / ransomware sites) off the UI thread.

    The Intelligence X poll and the ransomware.live/Ahmia calls together take a
    few seconds, so this must not run on the interface thread. ``sources`` is the
    subset of {"ransomware_live", "ahmia", "intelx", "dehashed"} the user approved.
    """

    progress_signal = Signal(str, str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, target: str, target_type: str, sources=()):
        super().__init__()
        self.target = target
        self.target_type = target_type
        self.sources = tuple(sources)
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            from providers.exposure_lookup import lookup

            result = lookup(
                self.target,
                self.target_type,
                selected_sources=self.sources or None,
                on_progress=lambda source, status: self.progress_signal.emit(
                    source, status
                ),
                should_stop=lambda: self._cancel_requested,
            )
            self.finished_signal.emit(result)
        except Exception as error:
            self.error_signal.emit(str(error))


class LiveCollectionWorker(QThread):
    """Run Bloodhound's live public-source collection off the UI thread.

    A Deep Dive on a username sweeps several hundred sites and can take a
    minute; run on the UI thread, that froze the whole window.
    ``collect(target, target_type, scope, on_progress=, should_stop=)`` must
    return the list of provider results.
    """

    progress_signal = Signal(str)
    finished_signal = Signal(list)
    error_signal = Signal(str)

    def __init__(self, collect, target: str, target_type: str, scope: str):
        super().__init__()
        self.collect = collect
        self.target = target
        self.target_type = target_type
        self.scope = scope
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            results = self.collect(
                self.target, self.target_type, self.scope,
                on_progress=self.progress_signal.emit,
                should_stop=lambda: self._cancel_requested,
            )
            self.finished_signal.emit(list(results or []))
        except Exception as error:
            self.error_signal.emit(str(error))


class ChatWorker(QThread):
    token_signal = Signal(str)
    status_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)

    def __init__(self, run_backend_func, backend: str, model: str, messages: list, prompt: str):
        super().__init__()
        self.run_backend_func = run_backend_func
        self.backend = backend
        self.model = model
        self.messages = messages
        self.prompt = prompt
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def _emit_as_tokens(self, text: str):
        for part in re.split(r"(\s+)", text):
            if self._cancel_requested:
                return
            self.token_signal.emit(part)
            time.sleep(0.006)

    def run(self):
        try:
            self.status_signal.emit("Model processing started...")
            result = self.run_backend_func(
                self.backend,
                self.model,
                self.messages,
                self.prompt,
            )

            usage = None
            response_parts = []

            # ===== STREAMING CASE =====
            if hasattr(result, "__iter__") and not isinstance(result, (str, tuple, dict)):
                self.status_signal.emit("Streaming response...")

                stream_usage = None
                for token in result:
                    if self._cancel_requested:
                        self.error_signal.emit("Request cancelled by user.")
                        return

                    # A dict yielded mid/after the text stream is a usage
                    # sentinel carrying the provider's real token counts, not
                    # a token to display.
                    if isinstance(token, dict):
                        stream_usage = token.get("__usage__") or stream_usage
                        continue

                    response_parts.append(token)
                    self.token_signal.emit(token)

                response = "".join(response_parts)

                # Prefer the provider's real usage; fall back to the honest
                # char/4 estimate only when no counts were reported.
                usage = stream_usage or {"cost_type_override": "stream-estimated"}

            # ===== TUPLE (response, usage) =====
            elif isinstance(result, tuple):
                response, usage = result
                self._emit_as_tokens(response)

            # ===== NORMAL STRING RESPONSE =====
            else:
                response = result
                self._emit_as_tokens(response)

            if usage:
                self.usage_signal.emit(usage)

            self.finished_signal.emit(response)

        except Exception as e:
            self.error_signal.emit(str(e))


class SubprocessWorker(QThread):
    finished_signal = Signal(str)
    error_signal = Signal(str)

    def __init__(self, cmd: list):
        super().__init__()
        self._cmd = cmd
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            result = subprocess.run(self._cmd, capture_output=True, text=True, timeout=30)
            if self._cancelled:
                return
            output = result.stdout.strip()
            if result.stderr.strip():
                output += f"\n\n[stderr]\n{result.stderr.strip()}"
            self.finished_signal.emit(output or "[No output returned]")
        except subprocess.TimeoutExpired:
            self.error_signal.emit("Command timed out after 30 seconds.")
        except FileNotFoundError as e:
            self.error_signal.emit(f"Command not found: {e}")
        except Exception as e:
            self.error_signal.emit(str(e))


class ModelPullWorker(QThread):
    """Downloads an Ollama model off the UI thread.

    progress_signal carries (status, completed_bytes, total_bytes); total is 0
    until Ollama has resolved the manifest.
    """
    progress_signal = Signal(str, int, int)
    finished_signal = Signal(str)
    error_signal = Signal(str)

    def __init__(self, client, model: str):
        super().__init__()
        self._client = client
        self._model = model

    def run(self):
        try:
            self._client.pull_model(
                self._model,
                on_progress=lambda status, done, total: self.progress_signal.emit(
                    status, int(done or 0), int(total or 0)
                ),
            )
            self.finished_signal.emit(self._model)
        except Exception as e:
            self.error_signal.emit(str(e))


class LocalFileSearchWorker(QThread):
    """Search user-selected folders without blocking the interface."""

    progress_signal = Signal(int, int)
    finished_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, roots: list[str], filters: FileSearchFilters):
        super().__init__()
        self._roots = roots
        self._filters = filters
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            report: FileSearchReport = search_files(
                self._roots,
                self._filters,
                should_cancel=lambda: self._cancel_requested,
                on_progress=lambda checked, found: self.progress_signal.emit(
                    checked, found
                ),
            )
            self.finished_signal.emit(report)
        except Exception as exc:
            self.error_signal.emit(str(exc))


class RemoteFileSearchWorker(QThread):
    """Search an authenticated SSH host through its read-only SFTP channel."""

    progress_signal = Signal(int, int)
    finished_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(
        self,
        host: str,
        username: str,
        port: int,
        roots: list[str],
        filters: FileSearchFilters,
    ):
        super().__init__()
        self._host = host
        self._username = username
        self._port = port
        self._roots = roots
        self._filters = filters
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            report = search_remote_files(
                self._host,
                self._username,
                self._port,
                self._roots,
                self._filters,
                should_cancel=lambda: self._cancel_requested,
                on_progress=lambda checked, found: self.progress_signal.emit(
                    checked, found
                ),
            )
            self.finished_signal.emit(report)
        except Exception as exc:
            self.error_signal.emit(str(exc))


class VpnConnectionWorker(QThread):
    """Bring a VPN tunnel up or down off the UI thread.

    The privileged step raises the macOS authorisation dialog inside
    vpn_connection, so this must not run on the interface thread. Connect and
    Disconnect go through vpn_execution's gate (re-review, post-change check,
    local audit); kill-switch changes are audited too.
    """

    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, action: str, profile: dict):
        super().__init__()
        self._action = action
        self._profile = dict(profile or {})

    def run(self) -> None:
        try:
            from services import vpn_connection, vpn_execution
            if self._action in ("connect", "disconnect"):
                result = vpn_execution.execute(self._action, self._profile).as_result()
            elif self._action in ("arm", "disarm"):
                if self._action == "arm":
                    ok, message = vpn_connection.arm_killswitch(self._profile)
                else:
                    ok, message = vpn_connection.disarm_killswitch()
                vpn_execution.record_killswitch(self._action, ok, message)
                result = {"success": ok, "protocol": "Kill switch",
                          "output": message, "error": None if ok else message}
            else:
                result = {"success": False, "error": f"Unknown action: {self._action}"}
            self.finished_signal.emit(result)
        except Exception as exc:
            self.error_signal.emit(str(exc))


class VpnDiagnosticsWorker(QThread):
    """Collect Tunnel's read-only local snapshot off the interface thread."""

    finished_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, include_external: bool = False, selected_profile=None):
        super().__init__()
        self._include_external = include_external
        self._selected_profile = dict(selected_profile) if selected_profile else None
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            report = collect_vpn_diagnostics(
                include_external=self._include_external,
                selected_profile=self._selected_profile,
                should_cancel=lambda: self._cancel_requested,
            )
            self.finished_signal.emit(report)
        except Exception as exc:
            self.error_signal.emit(str(exc))


class IpSnapshotWorker(QThread):
    """Read the operator's own local and (optionally) public IP off the UI thread.

    Local addresses are read from ``ifconfig`` with no network contact; the
    public IP and its detail are fetched only when ``include_public`` is set, so
    the panel can show the LAN/tunnel view without reaching out.
    """

    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, include_public: bool = True):
        super().__init__()
        self._include_public = include_public

    def cancel(self) -> None:
        """No-op for shutdown compatibility; each request is timeout-bounded, so
        ``wait()`` joins this worker within a few seconds on its own."""

    def run(self) -> None:
        try:
            from agents.vpn_agent.services import public_ip

            self.finished_signal.emit(
                public_ip.get_ip_snapshot(include_public=self._include_public)
            )
        except Exception as exc:
            self.error_signal.emit(str(exc))


class DnsLeakWorker(QThread):
    """Run bash.ws's real egress DNS-leak test off the UI thread.

    The test resolves a dozen probe hostnames and makes two HTTP calls, so it
    takes a few seconds and must not block the interface. ``cancel`` is honoured
    between probes.
    """

    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            from agents.vpn_agent.services import dns_check

            self.finished_signal.emit(
                dns_check.run_dns_leak_test(should_stop=lambda: self._cancel_requested)
            )
        except Exception as exc:
            self.error_signal.emit(str(exc))


class AliasMintWorker(QThread):
    """Create one addy.io alias off the UI thread (a single bounded POST).

    Triggered only by the operator's explicit "Mint alias" action in the OSINT
    Keys tab.
    """

    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, description: str = ""):
        super().__init__()
        self.description = description

    def cancel(self) -> None:
        """No-op for shutdown compatibility; the POST is timeout-bounded."""

    def run(self) -> None:
        try:
            from providers.alias_mint import mint_alias

            self.finished_signal.emit(mint_alias(self.description))
        except Exception as exc:
            self.error_signal.emit(str(exc))


class SentryWatchWorker(QThread):
    """Run one Sentry watch pass (read-only network snapshot + diff) off the UI."""

    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, persist: bool = True):
        super().__init__()
        self._persist = persist
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            from agents.sentry.sentry.baseline import BaselineStore
            from agents.sentry.sentry.engine import collect_snapshot, diff, run_watch

            if self._persist:
                self.finished_signal.emit(run_watch())
                return
            # Dry run: compare against the stored baseline without writing.
            store = BaselineStore()
            baseline = store.load_baseline()
            current = collect_snapshot()
            if baseline is None:
                self.finished_signal.emit({
                    "baseline_established": True, "findings": [],
                    "device_count": len(current.devices),
                    "listener_count": len(current.listeners),
                    "connection_count": len(current.connections),
                    "taken_at": current.taken_at, "dry_run": True,
                })
                return
            findings = diff(baseline, current)
            self.finished_signal.emit({
                "baseline_established": False,
                "findings": [f.as_dict() for f in findings],
                "device_count": len(current.devices),
                "listener_count": len(current.listeners),
                "connection_count": len(current.connections),
                "taken_at": current.taken_at, "dry_run": True,
            })
        except Exception as exc:
            self.error_signal.emit(str(exc))


class ModelScanWorker(QThread):
    """Ask every provider with a key for its live model list.

    One provider after another inside this one thread, never in parallel:
    the listing calls are free, but a burst of connections is what the home
    router refuses (see services/model_watch.py). Only the network half runs
    here; folding the result into the watch's state happens back on the UI
    thread, so an Adopt click can never race a scan writing the same file.
    """
    finished_signal = Signal(object)   # list[services.model_watch.ProviderListing]
    error_signal = Signal(str)

    def __init__(self, client_classes):
        super().__init__()
        self._client_classes = dict(client_classes)

    def run(self):
        try:
            from services.model_watch import list_live

            self.finished_signal.emit(list_live(self._client_classes))
        except Exception as e:
            self.error_signal.emit(str(e))
