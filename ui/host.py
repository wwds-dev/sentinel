"""The interface an agent panel needs from the application.

This protocol records the shared application capabilities that independently
testable agent panels may use: providers, logging, budgets, history, and common
request controls.

Panels talk to the host through this protocol rather than reaching into `GodAI`,
which is what lets a panel move to its own module (phase 4) and eventually to a
different application without carrying the whole window with it.

Runtime-checkable so a test can assert a stand-in satisfies it; `GodAI` conforms
structurally and does not inherit from it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentHost(Protocol):
    """What a panel may assume exists. Nothing else is fair game."""

    # ── Provider clients ────────────────────────────────────────────────
    # A panel picks one by name; it never constructs a client itself, because
    # timeouts and retries are configured centrally (services/api_limits.py).
    ollama: Any
    openai: Any
    deepseek: Any
    kimi: Any
    gemini: Any
    anthropic: Any
    qwen: Any

    # ── Agent instances ─────────────────────────────────────────────────
    agent_instances: dict[str, Any]

    # Writes a new agent's module and registry entries. A panel asks for it
    # rather than owning it: it edits `agents/` and `config/` for the whole
    # application (Forge's Approve button is the only caller today).
    agent_factory: Any

    def run_backend(self, backend: str, model: str, messages: list,
                    prompt: str) -> Any:
        """Execute one request. Panels hand this to a ChatWorker."""
        ...

    # ── The request guard ───────────────────────────────────────────────
    # Every panel that can spend money goes through these. Bypassing them is
    # how the budget caps, the spend counters, the confirmation prompt and
    # Saved Chats all came to ignore twelve of thirteen agents (TODO #1).

    def authorize_request(self, agent: str, provider: str, model: str,
                          prompt: str, tool: str | None = None,
                          label: str | None = None,
                          request_id: str | None = None) -> bool:
        """False means the request must not be sent."""
        ...

    def record_request(self, agent: str, response: str,
                       messages: list | None = None,
                       request_id: str | None = None) -> None:
        """Bill, save and close out an authorised request."""
        ...

    def abandon_request(self, agent: str, reason: str = "error",
                        request_id: str | None = None) -> None:
        """Drop a failed request so it is not billed."""
        ...

    def note_request_error(self, agent: str, error: str,
                           request_id: str | None = None) -> None:
        """Why the request is about to be abandoned (shown in the Run log)."""
        ...

    def note_request_usage(self, agent: str, usage: dict,
                           request_id: str | None = None) -> None:
        """Real token counts, when the worker reports them."""
        ...

    def record_external_research(self, *, agent: str, target: str,
                                 query_type: str, response: str,
                                 cancelled: bool = False) -> None:
        """Save a consented zero-model public-source run and its run-log entry."""
        ...

    # ── Shared services ─────────────────────────────────────────────────
    def load_models_into(self, provider_box, model_box, context: str,
                         empty_placeholder: bool = False) -> None:
        """Fill a model box from the provider selected next to it."""
        ...

    def register_model_loader(self, agent_key: str, loader) -> None:
        """Publish a panel's reload function.

        The recommendation system selects a provider programmatically and then
        needs the model box repopulated before it can pick the recommended model
        in it. It calls back through here rather than knowing which panel owns
        which combo.
        """
        ...

    def _note_failure(self, context: str, exc: Exception, widget=None) -> None:
        """Record a swallowed exception instead of discarding it."""
        ...

    def show_agent_docs(self) -> None:
        """Open the capability sheet for the current agent."""
        ...
