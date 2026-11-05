"""Shared gate for the Tunnel companion tabs (Privacy, Servers).

One pattern for every action that changes something or contacts something:
optional default-No confirmation and an off-thread worker. Nothing is logged,
not even a declined or refused attempt. Secrets are redacted before anything is
shown.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from services import vpn_execution
from ui.workers import CallWorker, detach_worker, park


class GatedTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._active = False
        self._read_only = False         # the running worker only reads (no admin dialog)

    @property
    def busy(self) -> bool:
        return self._active

    def _say(self, text: str) -> None:
        self.status_label.setText(text[:600])

    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(
            self, title, text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def _refuse(self, action: str, target: str, reason: str) -> None:
        self._say("Refused: " + reason)
        QMessageBox.warning(self, "Tunnel", reason + "\n\nNothing was changed.")

    def _gated(self, action: str, target: str, title: str, text: str | None,
               func, after=None) -> bool:
        """Confirm (when ``text``), then run ``func`` off-thread."""
        if self.busy:
            QMessageBox.information(self, "Busy", "A Tunnel action is already running.")
            return False
        if text is not None:
            # Reserved while the dialog is open: its nested event loop runs
            # timers, and one that found the tab free could start beside this.
            self._active = True
            try:
                confirmed = self._ask(title, text)
            finally:
                self._active = False
            if not confirmed:
                self._say("Not run: confirmation declined.")
                return False
        self._say(f"{title}…")
        self._set_enabled(False)
        worker = CallWorker(func)
        self._take_worker(worker)
        self._read_only = False
        self._active = True

        def done(result):
            ok, message = result if isinstance(result, tuple) else (bool(result), str(result))
            self._finish(f"{message}")
            if after:
                after(ok)

        def failed(error):
            self._finish(f"Failed: {error}")

        worker.finished_signal.connect(done)
        worker.error_signal.connect(failed)
        worker.start()
        return True

    def _take_worker(self, worker) -> None:
        """Make ``worker`` current. A finishing worker can still be returning
        from run() when its result slot starts the next one; dropping the last
        reference to a running QThread aborts the app, so park it instead."""
        old = self._worker
        if old is not None and old.isRunning():
            park(old)
        self._worker = worker

    def _read_off_thread(self, func, then) -> bool:
        """Run a read-only ``func`` off the UI thread, then ``then(result)`` here.

        Holds the tab busy meanwhile, so a second click is refused and the
        action buttons stay off; no confirmation, nothing privileged.
        """
        if self.busy:
            QMessageBox.information(self, "Busy", "A Tunnel action is already running.")
            return False
        self._set_enabled(False)
        worker = CallWorker(func)
        self._take_worker(worker)
        self._read_only = True
        self._active = True

        def release():
            self._active = False
            self._set_enabled(True)
            self._controls_changed()

        def done(result):
            release()
            then(result)

        def failed(error):
            release()
            self._say("Failed: " + vpn_execution.redact_secrets(str(error)))

        worker.finished_signal.connect(done)
        worker.error_signal.connect(failed)
        worker.start()
        return True

    def _finish(self, message: str) -> None:
        self._active = False
        self._say(vpn_execution.redact_secrets(message))
        self._set_enabled(True)
        self._after_finish()

    def _set_enabled(self, enabled: bool) -> None:
        """Subclasses disable their action buttons while a worker runs."""

    def _controls_changed(self) -> None:
        """Subclasses recompute which buttons should be on after ``_set_enabled(True)``
        switched them all on (a read that changed nothing must not enable what
        was off)."""

    def _after_finish(self) -> None:
        """Subclasses refresh what the action may have changed."""

    def has_privileged_work(self) -> bool:
        """True while an action is running. Whether a given tab's call is
        privileged (Tor, MAC) is not known here, so any running one counts, except a read-only one."""
        worker = self._worker
        return worker is not None and worker.isRunning() and not self._read_only

    def shutdown(self, timeout_ms: int = 2000) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(timeout_ms):
            # The call may be privileged (an administrator command in flight);
            # terminate() could cut it between approval and the recorded result.
            # Park the thread and let it finish on its own.
            detach_worker(worker)

