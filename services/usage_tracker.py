from datetime import datetime
from services.database import get_connection
from services.price_resolution import resolve_price_row
from services.provider_catalog import CLOUD_PROVIDERS


def estimate_prompt_tokens(text: str) -> int:
    """About four characters to a token, and never zero.

    The one estimate the pre-flight cost check and the router's
    context-window filter share, so a prompt is measured the same way when it
    is priced as when it is matched to a model that can hold it.
    """
    return max(1, len(text) // 4)


class UsageTracker:
    def estimate_tokens(self, prompt_text: str, response_text: str) -> tuple[int, int]:
        return estimate_prompt_tokens(prompt_text), estimate_prompt_tokens(response_text)

    def normalize_usage(self, usage: dict | None, prompt_text: str, response_text: str) -> tuple[int, int, str]:
        if not usage:
            i, o = self.estimate_tokens(prompt_text, response_text)
            return i, o, "estimated"

        override = usage.get("cost_type_override")

        i = int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("prompt_token_count") or 0)
        o = int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("candidates_token_count") or 0)

        if i <= 0 or o <= 0:
            ei, eo = self.estimate_tokens(prompt_text, response_text)
            return i or ei, o or eo, override or "mixed"

        return i, o, override or "exact"

    @staticmethod
    def cached_input_tokens(usage: dict | None, input_tokens: int) -> int:
        """Read cache-hit input tokens and clamp them to the reported input.

        Kimi documents ``cached_tokens`` at the top of ``usage``.  Accept the
        normalized wrapper key and OpenAI-compatible nested details as well so
        an SDK representation change cannot over-discount a request.
        """
        if not usage:
            return 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if not isinstance(details, dict):
            details = {}
        raw = (
            usage.get("cached_input_tokens")
            if usage.get("cached_input_tokens") is not None
            else usage.get("cached_tokens")
        )
        if raw is None:
            raw = (
                details.get("cached_tokens")
                if details.get("cached_tokens") is not None
                else details.get("cache_read_input_tokens", 0)
            )
        try:
            return min(max(0, int(raw)), max(0, int(input_tokens)))
        except (TypeError, ValueError):
            return 0

    def load_pricing(self) -> dict:
        result = {"eur_per_usd": 0.92}
        with get_connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'eur_per_usd'").fetchone()
            if row:
                result["eur_per_usd"] = float(row["value"])

            for r in conn.execute(
                "SELECT backend, model, input_per_1m_usd, "
                "cached_input_per_1m_usd, output_per_1m_usd FROM pricing"
            ):
                backend = r["backend"]
                result.setdefault(backend, {})
                result[backend][r["model"]] = {
                    "input_per_1m_usd": r["input_per_1m_usd"],
                    "cached_input_per_1m_usd": r["cached_input_per_1m_usd"],
                    "output_per_1m_usd": r["output_per_1m_usd"],
                }
        return result

    def calculate_cost_eur(
        self,
        backend: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_input_tokens: int = 0,
    ) -> float:
        if backend == "ollama":
            return 0.0

        with get_connection() as conn:
            eur_row = conn.execute("SELECT value FROM settings WHERE key = 'eur_per_usd'").fetchone()
            eur_per_usd = float(eur_row["value"]) if eur_row else 0.92

            # Exact row, else the row of the model this one is a dated
            # snapshot or alias of, else the provider default — the same
            # resolution the router prices candidates with.
            row, _source = resolve_price_row(conn, backend, model)

        if not row:
            return 0.0

        total_input = max(0, int(input_tokens))
        cached_input = min(max(0, int(cached_input_tokens)), total_input)
        uncached_input = total_input - cached_input
        # No cached rate, or zero, means unknown: bill the slice at full rate.
        cached_rate = row["cached_input_per_1m_usd"] or row["input_per_1m_usd"]
        input_usd = (
            (uncached_input / 1_000_000) * row["input_per_1m_usd"]
            + (cached_input / 1_000_000) * cached_rate
        )
        output_usd = (output_tokens / 1_000_000) * row["output_per_1m_usd"]
        return round((input_usd + output_usd) * eur_per_usd, 6)

    def log_request(self, agent: str, backend: str, model: str,
                    prompt_text: str, response_text: str, usage: dict | None = None,
                    project: str | None = None) -> dict:
        input_tokens, output_tokens, cost_type = self.normalize_usage(usage, prompt_text, response_text)
        cached_tokens = self.cached_input_tokens(usage, input_tokens)
        cost_eur = self.calculate_cost_eur(
            backend,
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens=cached_tokens,
        )
        timestamp = datetime.now().isoformat(timespec="seconds")

        with get_connection() as conn:
            conn.execute("""
                INSERT INTO usage
                  (timestamp, agent, backend, model, project, input_tokens,
                   cached_input_tokens, output_tokens, total_tokens, cost_eur,
                   cost_type, cloud)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                timestamp, agent, backend, model,
                project, input_tokens, cached_tokens, output_tokens,
                input_tokens + output_tokens,
                cost_eur, cost_type,
                1 if backend in CLOUD_PROVIDERS else 0,
            ))
            conn.commit()

        return {
            "timestamp": timestamp,
            "agent": agent,
            "backend": backend,
            "model": model,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_eur": cost_eur,
            "estimated_cost": cost_eur,
            "cost_type": cost_type,
            "cloud": backend in CLOUD_PROVIDERS,
            "project": project,
        }

    def get_today_total(self) -> float:
        today = datetime.now().date().isoformat()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_eur), 0.0) AS total FROM usage WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()
        return round(float(row["total"]), 6)

    def get_total_requests_today(self) -> int:
        today = datetime.now().date().isoformat()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM usage WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()
        return int(row["cnt"])

    def get_project_today_total(self, project: str | None) -> float:
        if not project:
            return 0.0
        today = datetime.now().date().isoformat()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_eur), 0.0) AS total FROM usage "
                "WHERE project = ? AND timestamp LIKE ?",
                (project, f"{today}%"),
            ).fetchone()
        return round(float(row["total"]), 6)

    def load_log(self) -> list:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM usage ORDER BY id ASC"
            ).fetchall()
        return [dict(r) for r in rows]
