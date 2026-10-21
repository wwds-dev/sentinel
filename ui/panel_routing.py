"""The routing advisor for the specialist panels (Trace, Bloodhound, Beacon,
Sentry, Forge, Bug Spray, Tunnel).

Chat holds a job on its own banner (main.py `_advise_job`); a panel holds one
here, on a banner at the top of the panel. Both ask the same question of
services/routing_advisor.py and store the answers the same way, per agent and
kind of work, in data/routing_rules.json. Every panel reaches this through
`AgentPanel.authorize`, so a new panel is advised without being touched.

The advisor only ever suggests. A paid choice still meets the app's own
consent dialog and budget caps in `authorize_request`, which runs after.
"""

from __future__ import annotations

from services import routing_advisor as ra
from services.providers import is_local as is_local_provider
from ui.routing_advice import AdviceBanner


class PanelRouting:
    def __init__(self, host) -> None:
        self.host = host
        self.held: dict[str, dict] = {}       # agent -> the job on its banner
        self.jobs: dict[str, dict] = {}       # agent -> the job now running
        self.failed: dict[str, dict] = {}     # agent -> the job a failover offers to redo

    # ── Banner ──────────────────────────────────────────────────────────────
    def banner(self, panel) -> AdviceBanner:
        banner = getattr(panel, "_advice_banner", None)
        if banner is None:
            banner = AdviceBanner(panel)
            layout = panel.layout()
            if layout is not None:
                layout.insertWidget(0, banner)
            agent = panel.agent_key
            banner.switch_requested.connect(lambda: self.switch(panel))
            banner.always_requested.connect(lambda: self.always(panel))
            banner.keep_requested.connect(lambda: self.keep(panel))
            banner.dismissed.connect(lambda: self.dismiss(agent))
            panel._advice_banner = banner
        return banner

    # ── Before the job ──────────────────────────────────────────────────────
    def advise(self, panel, prompt: str, *, tool: str = "", resume=None) -> bool:
        """True: carry on with the panel's provider and model (which may have
        just been changed by a rule). False: the job is held on the banner or
        refused there; the panel stops, as it does for any blocked request."""
        agent = panel.agent_key
        self.held.pop(agent, None)
        self.failed.pop(agent, None)
        self.jobs.pop(agent, None)
        banner = self.banner(panel)
        banner.clear()
        host = self.host
        try:
            context = host._advisor_context(agent, tool, prompt, panel.provider,
                                            panel.model, request_text=prompt)
        except Exception as exc:  # noqa: BLE001 - advice must never block a run
            host._note_failure("routing advisor", exc)
            return True
        advice = context.advice
        if advice.action == ra.RUN:
            target = advice.run
            if target is not None and not target.same(context.selected):
                self._point(panel, target)
                self._status(panel, advice.message)
            else:
                target = context.selected
            self._start(agent, context, target, accepted=advice.rule is not None,
                        resume=resume)
            return True
        if advice.action == ra.SUGGEST or advice.action == ra.REFUSE:
            self.held[agent] = {"context": context, "resume": resume, "prompt": prompt}
            banner.show_advice(advice, context.job, context.selected)
            if advice.action == ra.REFUSE:
                self.held.pop(agent, None)
            return False
        return True

    def _start(self, agent, context, target, *, accepted=False, resume=None) -> None:
        self.jobs[agent] = {"context": context, "provider": target.provider,
                            "model": target.model, "resume": resume,
                            "accepted": bool(accepted) and not target.local}

    @staticmethod
    def _status(panel, text: str) -> None:
        label = getattr(panel, "status_label", None)
        if text and label is not None:
            label.setText(text)

    def _point(self, panel, target) -> None:
        """Show the panel's provider and model boxes on `target`.

        Changing the provider box normally reloads the model box, which lists
        the provider's models over the network; this runs on the UI thread, so
        the signals are held and the box is filled from the last scan instead.
        """
        provider_box, model_box = panel.provider_box, panel.model_box
        if provider_box is None or model_box is None:
            return
        for box in (provider_box, model_box):
            box.blockSignals(True)
        try:
            changed = provider_box.currentText() != target.provider
            if provider_box.findText(target.provider) >= 0:
                provider_box.setCurrentText(target.provider)
            listed = (list(self.host._cached_models(target.provider)) if changed
                      else [model_box.itemText(i) for i in range(model_box.count())])
            if target.model not in listed:
                listed.insert(0, target.model)
            model_box.clear()
            model_box.addItems(listed)
            model_box.setCurrentText(target.model)
        finally:
            for box in (provider_box, model_box):
                box.blockSignals(False)

    # ── The user's answer ───────────────────────────────────────────────────
    def _resume(self, panel, target=None, *, accepted=False) -> None:
        held = self.held.pop(panel.agent_key, None)
        if held is None:
            return
        context = held["context"]
        target = target or context.selected
        if not target.same(context.selected):
            self._point(panel, target)
        resume = held.get("resume")
        self._start(panel.agent_key, context, target, accepted=accepted, resume=resume)
        if resume is None:
            return
        panel._advice_cleared = True        # authorize() must not ask a second time
        try:
            resume()
        finally:
            panel._advice_cleared = False

    def switch(self, panel) -> None:
        offer = self.banner(panel).offer
        if offer is not None:
            self._redo(panel, offer.cloud)
            return
        held = self.held.get(panel.agent_key)
        if held is None or held["context"].advice.suggestion is None:
            return
        target = held["context"].advice.suggestion
        self._resume(panel, target, accepted=not target.local)

    def always(self, panel) -> None:
        held = self.held.get(panel.agent_key)
        if held is None or held["context"].advice.suggestion is None:
            return
        context, target = held["context"], held["context"].advice.suggestion
        try:
            self.host.routing_rules.add(context.job.agent, context.job.kind, target.provider,
                                        target.model, context.job.label)
        except OSError as exc:
            self.host._note_failure("routing rules: save", exc)
        self._resume(panel, target, accepted=not target.local)

    def keep(self, panel) -> None:
        offer = self.banner(panel).offer
        if offer is not None:
            self._redo(panel, offer.local)
            return
        self._resume(panel)

    def dismiss(self, agent: str) -> None:
        self.held.pop(agent, None)
        self.failed.pop(agent, None)

    # ── After the job ───────────────────────────────────────────────────────
    def offer_failover(self, panel, error) -> None:
        """A run failed: offer the router's next candidate. Never sends."""
        agent = panel.agent_key
        job = self.jobs.pop(agent, None)
        self.failed.pop(agent, None)
        if job is None:
            return
        context = job["context"]
        failed = next((c for c in context.fallbacks + [context.selected]
                       if c.key == (job["provider"], job["model"])), None)
        if failed is None:
            return
        offer = ra.failover(failed, context.fallbacks, mode=context.mode,
                            error=str(error)[:160], privacy=context.privacy,
                            job=context.job, budget_left_eur=self.host._budget_left_eur())
        if offer.empty:
            return
        self.failed[agent] = {"job": job, "resume": job.get("resume")}
        self.banner(panel).show_failover(offer)

    def _redo(self, panel, target) -> None:
        agent = panel.agent_key
        failed = self.failed.pop(agent, None)
        self.banner(panel).clear()
        if failed is None or target is None:
            return
        job = failed["job"]
        self.held[agent] = {"context": job["context"], "resume": failed["resume"], "prompt": ""}
        self._resume(panel, target, accepted=not target.local)

    def finished(self, agent: str, cost_eur) -> None:
        """Count what a finished job saved (local) or spent (accepted)."""
        job = self.jobs.pop(agent, None)
        if job is None:
            return
        context, host = job["context"], self.host
        try:
            if is_local_provider(job["provider"]):
                host.savings_tally.record_local_run(context.saved_eur)
            elif job.get("accepted"):
                host.savings_tally.record_accepted(cost_eur)
        except OSError as exc:
            host._note_failure("routing savings: save", exc)
