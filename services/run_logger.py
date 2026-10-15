import time
import uuid
from datetime import datetime
from services.database import get_connection


class RunLogger:
    def __init__(self):
        self._active: dict[str, float] = {}  # run_id → monotonic start time

    def start(self, agent: str, tool: str, provider: str, model: str,
              mode: str, prompt_summary: str) -> str:
        run_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat(timespec="seconds")
        self._active[run_id] = time.monotonic()

        with get_connection() as conn:
            conn.execute("""
                INSERT INTO runs
                  (run_id, timestamp, agent, tool, provider, model, mode, prompt_summary, status)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (run_id, timestamp, agent, tool, provider, model, mode, prompt_summary[:200], "running"))
            conn.commit()

        return run_id

    def finish(self, run_id: str, status: str = "success",
               input_tokens: int = 0, output_tokens: int = 0,
               cost_eur: float = 0.0, error: str | None = None) -> None:
        started_at = self._active.pop(run_id, None)
        duration = round(time.monotonic() - started_at, 2) if started_at else 0.0

        with get_connection() as conn:
            conn.execute("""
                UPDATE runs
                SET status = ?, input_tokens = ?, output_tokens = ?,
                    cost_eur = ?, duration_sec = ?, error = ?
                WHERE run_id = ?
            """, (status, input_tokens, output_tokens, cost_eur, duration, error, run_id))
            conn.commit()

    def cancel(self, run_id: str) -> None:
        self.finish(run_id, status="cancelled")

    def add_late_cost(self, run_id: str, input_tokens: int, output_tokens: int,
                      cost_eur: float) -> None:
        """Record what a run already closed as cancelled cost after all.

        Only a cancelled row is touched, and its status and duration stay as
        they are (finish() would reset the duration to 0).
        """
        with get_connection() as conn:
            conn.execute("""
                UPDATE runs SET input_tokens = ?, output_tokens = ?, cost_eur = ?
                WHERE run_id = ? AND status = 'cancelled'
            """, (input_tokens, output_tokens, cost_eur, run_id))
            conn.commit()

    def load_recent(self, limit: int = 200) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))
