from dataclasses import dataclass
import math
from decimal import Decimal


def parse_money(raw: str, *, field: str, minimum: float = 0.0,
                allow_blank: bool = False) -> float | None:
    """A finite, non-negative number from a settings field, or ValueError.

    float() alone accepted "nan", "inf" and negatives, which then reached the
    Decimal budget maths and the pricing table: a nan budget compared as never
    exceeded, an inf rate priced every request as infinite, a negative cap
    blocked everything. ``minimum`` is inclusive; blank is allowed only where
    the field is optional (a per-agent budget means "no cap" when empty).
    """
    text = (raw or "").strip().replace(",", ".")
    if not text:
        if allow_blank:
            return None
        raise ValueError(f"{field}: enter a number.")
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{field}: '{raw}' is not a number.") from None
    if not math.isfinite(value):
        raise ValueError(f"{field}: '{raw}' is not a finite number.")
    if value < minimum:
        raise ValueError(f"{field}: must be at least {minimum:g}.")
    return value
from services.registry import Registry


@dataclass
class ValidationResult:
    allowed: bool
    reason: str


class Validator:
    """
    Central permission gate. Called before any agent/tool runs.
    All checks return ValidationResult(allowed, reason).
    """

    def __init__(self, registry: Registry):
        self.registry = registry

    @staticmethod
    def _money(value: float | int | str) -> Decimal:
        """Convert display/config money without carrying binary-float noise."""
        return Decimal(str(value))

    def validate(
        self,
        agent_name: str,
        tool_name: str,
        provider: str,
        api_permissions: dict[str, bool],
        session_cost: float,
        session_budget: float,
        daily_cost: float,
        daily_budget: float,
        estimated_cost: float,
    ) -> ValidationResult:
        # 1. Agent enabled?
        if not self.registry.is_agent_enabled(agent_name):
            return ValidationResult(False, f"Agent '{agent_name}' is disabled in the registry.")

        # 2. Tool enabled? Agent panels without a registry tool pass no name.
        if tool_name and not self.registry.is_tool_enabled(tool_name):
            return ValidationResult(False, f"Tool '{tool_name}' is disabled in the registry.")

        # 3. Provider allowed for this agent?
        if not self.registry.agent_allows_provider(agent_name, provider):
            return ValidationResult(
                False,
                f"Agent '{agent_name}' does not permit provider '{provider}'."
            )

        # 4. Provider allowed for this tool?
        if tool_name and not self.registry.tool_allows_provider(tool_name, provider):
            return ValidationResult(
                False,
                f"Tool '{tool_name}' does not permit provider '{provider}'."
            )

        # 5. Tool allowed for this agent?
        if tool_name and not self.registry.agent_allows_tool(agent_name, tool_name):
            return ValidationResult(
                False,
                f"Agent '{agent_name}' does not permit tool '{tool_name}'."
            )

        # 6. API checkbox permission (free pass for ollama)
        if provider != "ollama":
            perm_key = f"allow_{provider}"
            if not api_permissions.get(perm_key, False):
                return ValidationResult(
                    False,
                    f"API access for '{provider}' is not enabled. Tick it under Chat → Options → Paid provider access."
                )

        budget = self.validate_budget(
            agent_name=agent_name, tool_name=tool_name, provider=provider,
            session_cost=session_cost, session_budget=session_budget,
            daily_cost=daily_cost, daily_budget=daily_budget,
            estimated_cost=estimated_cost,
        )
        if not budget.allowed:
            return budget

        # 11. Approval required?
        if self.registry.agent_requires_approval(agent_name):
            return ValidationResult(
                False,
                f"Agent '{agent_name}' requires manual approval before running."
            )

        if tool_name and self.registry.tool_requires_approval(tool_name):
            return ValidationResult(
                False,
                f"Tool '{tool_name}' requires manual approval before running."
            )

        return ValidationResult(True, "OK")

    def validate_budget(
        self,
        agent_name: str,
        tool_name: str | None,
        provider: str,
        session_cost: float,
        session_budget: float,
        daily_cost: float,
        daily_budget: float,
        estimated_cost: float,
    ) -> ValidationResult:
        """Steps 7-10 of validate(): the four budget caps, nothing else.

        Panels that assemble their prompt after the first authorisation (live
        collection, EXIF, scan output) call this again with the text that is
        really being sent, so the caps are checked against the real size and
        not the few characters of the target string.
        """
        # 7. Per-agent budget (daily)
        agent_budget = self.registry.get_agent_budget(agent_name)
        if agent_budget is not None and provider != "ollama":
            if estimated_cost > agent_budget:
                return ValidationResult(
                    False,
                    f"Agent '{agent_name}' has a budget cap of €{agent_budget:.2f} per paid request. "
                    f"This request is estimated at €{estimated_cost:.4f}."
                )

        # 8. Per-tool budget. Local requests remain free and bypass cost caps.
        tool_budget = self.registry.get_tool_budget(tool_name) if tool_name else None
        if tool_budget is not None and provider != "ollama":
            if estimated_cost > tool_budget:
                return ValidationResult(
                    False,
                    f"Tool '{tool_name}' has a budget cap of €{tool_budget:.2f} per paid request. "
                    f"This request is estimated at €{estimated_cost:.4f}."
                )

        # 9. Session budget
        if provider != "ollama":
            session_remaining = self._money(session_budget) - self._money(session_cost)
            if self._money(estimated_cost) > session_remaining:
                return ValidationResult(
                    False,
                    f"Session budget exceeded. "
                    f"Remaining: €{session_remaining:.4f}, request: ~€{estimated_cost:.4f}."
                )

        # 10. Daily budget
        if provider != "ollama":
            daily_remaining = self._money(daily_budget) - self._money(daily_cost)
            if self._money(estimated_cost) > daily_remaining:
                return ValidationResult(
                    False,
                    f"Daily budget exceeded. "
                    f"Remaining: €{daily_remaining:.4f}, request: ~€{estimated_cost:.4f}."
                )

        return ValidationResult(True, "OK")
