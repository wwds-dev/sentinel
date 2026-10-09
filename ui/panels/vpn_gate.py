"""Shared gate for the Tunnel companion tabs (Privacy, Servers).

One pattern for every action that changes something or contacts something:
optional default-No confirmation, an off-thread worker, and one audit line for
every attempt (declined and refused ones included). Secrets are redacted before
anything is shown or logged.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from services import vpn_execution
from ui.workers import CallWorker


class GatedTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._active = False

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
        vpn_execution.record_companion(action, "refused", reason, target=target)
        self._say("Refused: " + reason)
        QMessageBox.warning(self, "Tunnel", reason + "\n\nNothing was changed.")

    def _gated(self, action: str, target: str, title: str, text: str | None,
               func, after=None) -> bool:
        """Confirm (when ``text``), audit, run ``func`` off-thread, audit result."""
        if self.busy:
            QMessageBox.information(self, "Busy", "A Tunnel action is already running.")
            return False
        if text is not None and not self._ask(title, text):
            vpn_execution.record_companion(action, "declined",
                                           "Operator declined the confirmation.",
                                           target=target)
            self._say("Not run: confirmation declined.")
            return False
        self._say(f"{title}…")
        self._set_enabled(False)
        worker = CallWorker(func)
        self._worker = worker
        self._active = True

        def done(result):
            ok, message = result if isinstance(result, tuple) else (bool(result), str(result))
            vpn_execution.record_companion(
                action, "succeeded" if ok else "failed", message, target=target)
            self._finish(f"{message}")
            if after:
                after(ok)

        def failed(error):
            vpn_execution.record_companion(action, "failed", error, target=target)
            self._finish(f"Failed: {error}")

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

    def _after_finish(self) -> None:
        """Subclasses refresh what the action may have changed."""

    def shutdown(self, timeout_ms: int = 2000) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(timeout_ms):
            worker.terminate()
            worker.wait(500)

