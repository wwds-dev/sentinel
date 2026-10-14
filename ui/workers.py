"""Background worker threads.

Moved verbatim out of main.py (see docs/refactor_plan.md, phase 1). These are
standalone QThread subclasses: they take everything they need as constructor
arguments and touch no application state, which is why they move first.
"""
import re
import subprocess
import time

import shiboken6
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from services.local_file_search import FileSearchFilters, FileSearchReport, search_files
from services.remote_file_search import search_remote_files
from services.vpn_diagnostics import collect_vpn_diagnostics


# Workers whose owner is gone (a closed dialog, a quitting window) but whose
# thread is still inside a call that must not be cut short: a request in
# flight, or a privileged command behind the administrator dialog. They are
# parked here so the QThread is not garbage-collected while running, and each
# is released when its thread ends. Never terminate() one instead: a thread
# killed while it holds the GIL leaves every other Python thread blocked for
# good (docs/adr/0001-park-workers-and-leave-with-os-exit.md).
_DETACHED_WORKERS: list = []

# Objects that must outlive a parked thread with it (HealthMonitor's worker,
# moved into the thread), keyed by id() of the parked thread.
_PARKED_COMPANIONS: dict = {}

_RESULT_SIGNALS = ("finished_signal", "error_signal", "result_signal")


def _discard_detached_worker(worker) -> None:
    try:
        _DETACHED_WORKERS.remove(worker)
    except ValueError:
        pass
    _PARKED_COMPANIONS.pop(id(worker), None)


def _release_parked(thread) -> None:
    """Let go of a parked thread whose run() has returned.

    Runs on the UI thread (see _Parking). wait() returns at once because
    `finished` has fired; deleteLater() then hands the C++ objects to Qt's
    event loop, and only after that is the last Python reference dropped, so
    nothing ever deletes a running QThread.
    """
    if thread not in _DETACHED_WORKERS:
        return                                   # released already
    if not shiboken6.isValid(thread):
        _discard_detached_worker(thread)
        return
    if thread.isRunning():
        return                                   # never release a running thread
    thread.wait()
    for obj in (thread, *_PARKED_COMPANIONS.get(id(thread), ())):
        if isinstance(obj, QObject) and shiboken6.isValid(obj):
            obj.deleteLater()
    _discard_detached_worker(thread)


class _Parking(QObject):
    """Receives `finished` from parked threads on the UI thread.

    Created at import, in the main thread. A slot on a QObject that lives
    there is reached through a queued connection when `finished` is emitted
    by the worker thread, so the release runs on the UI thread after the
    thread has returned. Qt guarantees that for a slot; for a bare lambda it
    is PySide's choice (it happens to queue it in 6.11, see
    tests/test_thread_parking.py), so the release does not rely on one.
    """

    @Slot()
    def on_finished(self) -> None:
        thread = self.sender()
        if thread is not None:
            _release_parked(thread)


_PARKING = _Parking()


def park(thread, *companions) -> None:
    """Keep a thread (and companions) referenced until it finishes, then release.

    The thread is never terminated and never dropped while it runs.
    """
    if thread not in _DETACHED_WORKERS:
        _DETACHED_WORKERS.append(thread)
        thread.finished.connect(_PARKING.on_finished)
    if companions:
        kept = _PARKED_COMPANIONS.setdefault(id(thread), ())
        _PARKED_COMPANIONS[id(thread)] = kept + tuple(c for c in companions if c not in kept)
    if thread.isFinished():
        # It ended before the connection existed; release from the event loop.
        QTimer.singleShot(0, _PARKING, lambda t=thread: _release_parked(t))


def detach_worker(worker) -> None:
    """Let a running worker finish on its own instead of terminate()-ing it.

    QThread.terminate() stops a thread wherever it happens to be. For a
    request that leaves it in an undefined state; for a privileged command it
    can end between "the password was accepted" and "the result was recorded".
    The result slots are dropped first so a late finish cannot call into
    widgets that are being destroyed, then the worker is parked until its
    thread ends.
    """
    for name in _RESULT_SIGNALS:
        signal = getattr(worker, name, None)
        if signal is None:
            continue
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass
    park(worker)


def remaining_ms(deadline: float) -> int:
    """Milliseconds left until a time.monotonic() deadline, never negative."""
    return max(0, int((deadline - time.monotonic()) * 1000))


def shutdown_deadline(timeout_ms: int, deadline: float | None) -> float:
    """The shared quit deadline when one is given, else `timeout_ms` from now."""
    return deadline if deadline is not None else time.monotonic() + timeout_ms / 1000


def join_or_park(workers, deadline: float) -> list:
    """Cancel every worker, join each against one shared monotonic deadline,
    and park (detach_worker) whatever is still running when it passes.

    Returns the workers that were parked.
    """
    live = []
    for worker in workers:
        if worker is None:
            continue
        try:
            running = worker.isRunning()
        except RuntimeError:                     # its C++ object is gone
            continue
        if running:
            live.append(worker)
    for worker in live:
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
    parked = []
    for worker in live:
        if not hasattr(worker, "wait"):
            continue
        if not worker.wait(remaining_ms(deadline)):
            detach_worker(worker)
            parked.append(worker)
    return parked


def fence_parked() -> None:
    """Mark every parked object so a late result from it writes nothing.

    Emergency Reset calls this before it erases the data folder.
    """
    for thread in list(_DETACHED_WORKERS):
        for obj in (thread, *_PARKED_COMPANIONS.get(id(thread), ())):
            try:
                obj._sentinel_fenced = True
            except (AttributeError, RuntimeError):
                pass


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
                    # CourtListener has its own consent checkbox in Trace (off by
                    # default); the panel adds "courtlistener" to the sources only
                    # when it is ticked and named in the consent dialog.
                    court_records="courtlistener" in self.sources,
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
    subset of exposure_lookup.DEFAULT_SOURCES the user approved.
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
                selected_sources=self.sources,
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

    def __init__(self, collect, target: str, target_type: str, scope: str,
                 options: dict | None = None):
        super().__init__()
        self.options = dict(options or {})
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
                **self.options,
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
    # A request stopped after the provider had already answered (or while it
    # streamed): {"usage": provider counts or None, "response_text": what was
    # received}. At most once. Separate from usage_signal, which panels connect
    # to their own billing, so only Chat records a stopped request's cost.
    stopped_usage_signal = Signal(dict)

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

    def _report_stopped(self, usage, response_text) -> None:
        if getattr(self, "_stopped_reported", False):
            return
        self._stopped_reported = True
        self.stopped_usage_signal.emit({"usage": usage, "response_text": str(response_text or "")})

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
                        self._report_stopped(stream_usage, "".join(response_parts))
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
                if self._cancel_requested:
                    self._report_stopped(usage, response)
                    return

            # ===== NORMAL STRING RESPONSE =====
            else:
                response = result
                self._emit_as_tokens(response)
                if self._cancel_requested:
                    self._report_stopped(None, response)
                    return

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
    Disconnect go through vpn_execution's gate (re-review, post-change check).
    Nothing is logged.
    """

    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, action: str, profile: dict, confirmed=None):
        """``confirmed`` is the ExecutionReview the user approved in the dialog;
        connect/disconnect run only if a fresh review still matches it."""
        super().__init__()
        self._action = action
        self._profile = dict(profile or {})
        self._confirmed = confirmed
        self._cancel_requested = False

    def cancel(self) -> None:
        """Ask the worker to stop reporting.

        A privileged wg-quick/openvpn call cannot be interrupted safely once
        the authorisation dialog is up, so cancellation does not kill it; it
        tells the worker to drop its result instead of emitting it into a
        panel that is being torn down. VpnPanel.shutdown() gives it a moment,
        then detaches it (detach_worker): the thread is never terminate()d,
        and the window asks Wait / Quit anyway before closing at all while a
        privileged call runs. Before this method existed, shutdown() raised
        AttributeError on an in-flight Connect (D6).
        """
        self._cancel_requested = True

    def run(self) -> None:
        try:
            from services import vpn_connection, vpn_execution
            if self._action in ("connect", "disconnect"):
                result = vpn_execution.execute(
                    self._action, self._profile, confirmed=self._confirmed).as_result()
            elif self._action == "verify":
                state, message = vpn_connection.verify_killswitch()
                ok = state != "unknown"
                result = {"success": ok, "protocol": "Kill switch", "output": message,
                          "error": None if ok else message, "verify_state": state}
            elif self._action in ("arm", "disarm"):
                if self._action == "arm":
                    ok, message = vpn_connection.arm_killswitch(self._profile)
                else:
                    ok, message = vpn_connection.disarm_killswitch()
                result = {"success": ok, "protocol": "Kill switch",
                          "output": message, "error": None if ok else message}
            else:
                result = {"success": False, "error": f"Unknown action: {self._action}"}
            if not self._cancel_requested:
                self.finished_signal.emit(result)
        except Exception as exc:
            if not self._cancel_requested:
                self.error_signal.emit(str(exc))


class CallWorker(QThread):
    """Run one blocking companion call (Tor, MAC, proxy probe, deploy) off the UI thread.

    ``func`` returns anything; it is emitted as-is. Like the connection worker,
    cancel() cannot interrupt a privileged call; it makes the worker drop its
    result so a torn-down panel is never touched, and shutdown() joins it.
    """

    finished_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            result = self._func(*self._args, **self._kwargs)
            if not self._cancel_requested:
                self.finished_signal.emit(result)
        except Exception as exc:
            if not self._cancel_requested:
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


class KeyCheckWorker(QThread):
    """Check API keys against their own services, one after another, off the UI.

    ``items`` is a list of (tool_id, key). Each result is emitted as it lands, so
    a row updates while the rest are still being asked. Started only by the
    operator: a row's Check button, Save Key, or Check all keys.
    """

    result_signal = Signal(str, dict)

    def __init__(self, items):
        super().__init__()
        self.items = list(items)
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        from providers.key_check import check

        for tool_id, key in self.items:
            if self._cancel_requested:
                return
            self.result_signal.emit(tool_id, check(tool_id, key))


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

            from agents.sentry.sentry import collectors

            if self._persist:
                summary = run_watch(should_stop=lambda: self._cancel_requested)
                if not self._cancel_requested and not summary.get("cancelled"):
                    self.finished_signal.emit(summary)
                return
            # Dry run: compare against the stored baseline without writing.
            store = BaselineStore()
            baseline = store.load_baseline()
            current = collect_snapshot()
            errors = collectors.collection_errors()
            if self._cancel_requested:
                return                      # Stop: nothing to report, nothing saved
            counts = {
                "device_count": len(current.devices),
                "listener_count": len(current.listeners),
                "connection_count": len(current.connections),
                "taken_at": current.taken_at, "dry_run": True,
                "collector_errors": errors,
            }
            if baseline is None:
                self.finished_signal.emit({
                    "baseline_established": False, "no_baseline": True,
                    "findings": [], **counts,
                })
                return
            findings = diff(baseline, current)
            self.finished_signal.emit({
                "baseline_established": False,
                "findings": [f.as_dict() for f in findings], **counts,
            })
        except Exception as exc:
            if not self._cancel_requested:
                self.error_signal.emit(str(exc))


class ModelScanWorker(QThread):
    """Ask every provider with a key for its live model list, then refresh
    the public model ratings if the cached copy is more than a day old.

    One request after another inside this one thread, never in parallel:
    the calls are free, but a burst of connections is what the home router
    refuses (see services/model_watch.py). Only the network half runs here;
    folding the result into the watch's state happens back on the UI thread,
    so an Update click can never race a scan writing the same file.
    """
    # listings, then a RatingTable (fresh), a str (why not), or None (fresh enough)
    finished_signal = Signal(object, object)
    error_signal = Signal(str)

    def __init__(self, client_classes, ratings_cache=None):
        super().__init__()
        self._client_classes = dict(client_classes)
        self._ratings_cache = ratings_cache

    def run(self):
        try:
            from services.model_watch import list_live

            listings = list_live(self._client_classes)
        except Exception as e:
            self.error_signal.emit(str(e))
            return
        ratings = None
        if self._ratings_cache is not None:
            from services import benchmarks

            if benchmarks.is_stale(self._ratings_cache):
                try:
                    ratings = benchmarks.refresh(self._ratings_cache)
                except Exception as e:      # the service is often "loading"
                    benchmarks.note_attempt(self._ratings_cache)
                    ratings = str(e)
        self.finished_signal.emit(listings, ratings)
