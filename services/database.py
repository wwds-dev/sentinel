import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.runtime_paths import user_data_base
from services.agent_catalog import BUILTIN_AGENTS, RETIRED_BUILTIN_AGENTS
from services.provider_catalog import CLOUD_PROVIDERS
from services.tool_catalog import BUILTIN_TOOLS

# Writable base: project root in dev, ~/Library/Application Support/Sentinel when frozen.
BASE_DIR = user_data_base()
DB_PATH = BASE_DIR / "data" / "sentinel.db"
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name                TEXT PRIMARY KEY,
    label               TEXT NOT NULL DEFAULT '',
    enabled             INTEGER NOT NULL DEFAULT 1,
    version             TEXT NOT NULL DEFAULT '1.0',
    allowed_providers   TEXT NOT NULL DEFAULT '[]',
    allowed_tools       TEXT,
    budget_limit_eur    REAL,
    requires_approval   INTEGER NOT NULL DEFAULT 0,
    description         TEXT NOT NULL DEFAULT '',
    log_path            TEXT NOT NULL DEFAULT 'data/logs/runs.jsonl',
    auto_generated      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tools (
    name                 TEXT PRIMARY KEY,
    label                TEXT NOT NULL DEFAULT '',
    enabled              INTEGER NOT NULL DEFAULT 1,
    version              TEXT NOT NULL DEFAULT '1.0',
    allowed_providers    TEXT NOT NULL DEFAULT '[]',
    budget_limit_eur     REAL,
    requires_approval    INTEGER NOT NULL DEFAULT 0,
    description          TEXT NOT NULL DEFAULT '',
    system_prompt        TEXT NOT NULL DEFAULT '',
    recommended_provider TEXT NOT NULL DEFAULT 'ollama',
    recommended_model    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    agent         TEXT NOT NULL DEFAULT '',
    backend       TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    project       TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_eur      REAL NOT NULL DEFAULT 0.0,
    cost_type     TEXT NOT NULL DEFAULT 'estimated',
    cloud         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL UNIQUE,
    timestamp      TEXT NOT NULL,
    agent          TEXT NOT NULL DEFAULT '',
    tool           TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    mode           TEXT NOT NULL DEFAULT '',
    prompt_summary TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'running',
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_eur       REAL NOT NULL DEFAULT 0.0,
    duration_sec   REAL NOT NULL DEFAULT 0.0,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS pricing (
    backend          TEXT NOT NULL,
    model            TEXT NOT NULL,
    input_per_1m_usd REAL NOT NULL DEFAULT 0.0,
    cached_input_per_1m_usd REAL,
    output_per_1m_usd REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (backend, model)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    instructions     TEXT NOT NULL DEFAULT '',
    default_agent    TEXT NOT NULL DEFAULT 'chat',
    default_provider TEXT NOT NULL DEFAULT 'ollama',
    default_model    TEXT NOT NULL DEFAULT '',
    budget_eur       REAL,
    archived         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp  ON runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_run_id     ON runs(run_id);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def save_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, value)
        )
        conn.commit()


def init_db() -> None:
    """Initialize current Sentinel tables without dropping legacy user data.

    Older databases may still contain publishing tables from before the app
    split. SQLite leaves those tables untouched; new Sentinel installations no
    longer create them.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not DB_PATH.exists()
    conn = get_connection()
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    if integrity != "ok":
        conn.close()
        raise RuntimeError(f"Database integrity check failed: {integrity}")
    installed_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if installed_version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"Database schema version {installed_version} is newer than this "
            f"Sentinel build supports ({SCHEMA_VERSION})."
        )
    if not is_new and installed_version < SCHEMA_VERSION:
        _backup_before_migration(conn, installed_version)
    conn.executescript(SCHEMA)
    _apply_schema_migrations(conn, installed_version)
    if is_new:
        _migrate_from_json(conn)
    _seed_missing_pricing(conn)
    _seed_cached_input_pricing(conn)
    _correct_stale_pricing(conn)
    _seed_default_agents(conn)
    _seed_default_tools(conn)
    _retire_moved_agents(conn)
    _sync_agent_labels(conn)
    _sync_usage_cloud_flags(conn)
    conn.close()


def _backup_before_migration(conn: sqlite3.Connection, installed_version: int) -> Path:
    """Create a WAL-safe snapshot before changing an existing database."""
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / (
        f"sentinel.v{installed_version}.before-v{SCHEMA_VERSION}.{stamp}.{uuid4().hex[:8]}.db"
    )
    destination = sqlite3.connect(backup_path)
    try:
        conn.backup(destination)
        result = destination.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Migration backup integrity check failed: {result}")
    finally:
        destination.close()
    return backup_path


def _apply_schema_migrations(conn: sqlite3.Connection, installed_version: int) -> None:
    """Apply ordered, additive migrations and record each completed version."""
    migrations = {
        1: _migrate_schema_v1,
        2: _migrate_schema_v2,
        3: _migrate_schema_v3,
    }
    for version in range(installed_version + 1, SCHEMA_VERSION + 1):
        migration = migrations[version]
        with conn:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {version}")


def _migrate_schema_v1(conn: sqlite3.Connection) -> None:
    """Bring pre-versioned Sentinel databases up to the maintained schema.

    Columns are additive so older user databases and newer legacy/superset
    databases both remain valid.  No user rows or legacy domain tables are
    removed here.
    """
    required_columns = {
        "agents": {
            "label": "TEXT NOT NULL DEFAULT ''",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "version": "TEXT NOT NULL DEFAULT '1.0'",
            "allowed_providers": "TEXT NOT NULL DEFAULT '[]'",
            "allowed_tools": "TEXT",
            "budget_limit_eur": "REAL",
            "requires_approval": "INTEGER NOT NULL DEFAULT 0",
            "description": "TEXT NOT NULL DEFAULT ''",
            "log_path": "TEXT NOT NULL DEFAULT 'data/logs/runs.jsonl'",
            "auto_generated": "INTEGER NOT NULL DEFAULT 0",
        },
        "usage": {
            "agent": "TEXT NOT NULL DEFAULT ''",
            "backend": "TEXT NOT NULL DEFAULT ''",
            "model": "TEXT NOT NULL DEFAULT ''",
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "total_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cost_eur": "REAL NOT NULL DEFAULT 0.0",
            "cost_type": "TEXT NOT NULL DEFAULT 'estimated'",
            "cloud": "INTEGER NOT NULL DEFAULT 0",
        },
        "tools": {
            "label": "TEXT NOT NULL DEFAULT ''",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "version": "TEXT NOT NULL DEFAULT '1.0'",
            "allowed_providers": "TEXT NOT NULL DEFAULT '[]'",
            "budget_limit_eur": "REAL",
            "requires_approval": "INTEGER NOT NULL DEFAULT 0",
            "description": "TEXT NOT NULL DEFAULT ''",
            "system_prompt": "TEXT NOT NULL DEFAULT ''",
            "recommended_provider": "TEXT NOT NULL DEFAULT 'ollama'",
            "recommended_model": "TEXT NOT NULL DEFAULT ''",
        },
        "runs": {
            "agent": "TEXT NOT NULL DEFAULT ''",
            "tool": "TEXT NOT NULL DEFAULT ''",
            "provider": "TEXT NOT NULL DEFAULT ''",
            "model": "TEXT NOT NULL DEFAULT ''",
            "mode": "TEXT NOT NULL DEFAULT ''",
            "prompt_summary": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'running'",
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cost_eur": "REAL NOT NULL DEFAULT 0.0",
            "duration_sec": "REAL NOT NULL DEFAULT 0.0",
            "error": "TEXT",
        },
        "pricing": {
            "input_per_1m_usd": "REAL NOT NULL DEFAULT 0.0",
            "output_per_1m_usd": "REAL NOT NULL DEFAULT 0.0",
        },
    }
    for table, columns in required_columns.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _migrate_schema_v2(conn: sqlite3.Connection) -> None:
    """Add Kimi cache-token accounting without rewriting existing rows."""
    additions = {
        "usage": {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        },
        "pricing": {
            # NULL deliberately means "no distinct cache rate"; billing then
            # falls back to the normal input rate rather than assuming free.
            "cached_input_per_1m_usd": "REAL",
        },
    }
    for table, columns in additions.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _migrate_schema_v3(conn: sqlite3.Connection) -> None:
    """Add chat projects and project attribution for spend accounting."""
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(usage)")
    }
    if "project" not in existing:
        conn.execute("ALTER TABLE usage ADD COLUMN project TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            default_agent TEXT NOT NULL DEFAULT 'chat',
            default_provider TEXT NOT NULL DEFAULT 'ollama',
            default_model TEXT NOT NULL DEFAULT '',
            budget_eur REAL,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_project_timestamp "
        "ON usage(project, timestamp)"
    )


def _sync_usage_cloud_flags(conn: sqlite3.Connection) -> None:
    """Repair the derived local/cloud flag for current provider identifiers."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(usage)")
    }
    # Some early v1 development databases legitimately predate these derived
    # fields. Their additive v2/v3 migrations preserve the rows; there is
    # simply nothing to repair until both columns exist.
    if not {"backend", "cloud"}.issubset(columns):
        return
    conn.execute("UPDATE usage SET cloud = 0 WHERE backend = 'ollama'")
    placeholders = ",".join("?" for _ in CLOUD_PROVIDERS)
    conn.execute(
        f"UPDATE usage SET cloud = 1 WHERE backend IN ({placeholders})",
        tuple(sorted(CLOUD_PROVIDERS)),
    )
    conn.commit()


def _sync_agent_labels(conn: sqlite3.Connection) -> None:
    """Ensure built-in agents' DB labels match the current brand names shown in the GUI."""
    for name, definition in BUILTIN_AGENTS.items():
        conn.execute(
            "UPDATE agents SET label = ? WHERE name = ?",
            (definition["label"], name),
        )
    conn.commit()


def _correct_stale_pricing(conn: sqlite3.Connection) -> None:
    """One-time repair of pricing rows that were seeded at the wrong rate.

    _seed_missing_pricing uses INSERT OR IGNORE, so it can add new models but
    never fixes a row that already exists. These three were wrong: the Opus
    4.6/4.7 rows carried the old Opus 4.1 rate of 15/75 when those models
    actually bill at 5/25, and Haiku 4.5 was seeded a notch low.

    Guarded by a settings flag so it runs once and never overwrites a price the
    user has since edited in Settings -> Pricing.
    """
    flag = conn.execute(
        "SELECT value FROM settings WHERE key = 'pricing_correction_2026_08'"
    ).fetchone()
    if flag:
        return

    corrections = [
        ("anthropic", "claude-opus-4-6",            5.00, 25.00, 15.00, 75.00),
        ("anthropic", "claude-opus-4-7",            5.00, 25.00, 15.00, 75.00),
        ("anthropic", "claude-haiku-4-5-20251001",  1.00,  5.00,  0.80,  4.00),
    ]
    for backend, model, new_in, new_out, old_in, old_out in corrections:
        # Only touch rows still holding the original wrong value.
        conn.execute(
            "UPDATE pricing SET input_per_1m_usd = ?, output_per_1m_usd = ? "
            "WHERE backend = ? AND model = ? "
            "AND input_per_1m_usd = ? AND output_per_1m_usd = ?",
            (new_in, new_out, backend, model, old_in, old_out),
        )

    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('pricing_correction_2026_08', 'done')"
    )
    conn.commit()


def _seed_missing_pricing(conn: sqlite3.Connection) -> None:
    """Insert default pricing rows that may not exist yet (e.g. new providers)."""
    # Anthropic list prices per 1M tokens, from the official pricing table.
    # Note the Opus 4.5-and-later tier is 5/25, NOT the 15/75 that Opus 4/4.1
    # charged — seeding those at 15/75 overstated every estimate threefold.
    defaults = [
        ("anthropic", "claude-fable-5",            10.00,  50.00),
        ("anthropic", "claude-opus-5",              5.00,  25.00),
        ("anthropic", "claude-sonnet-5",            2.00,  10.00),
        ("anthropic", "claude-opus-4-8",            5.00,  25.00),
        ("anthropic", "claude-opus-4-7",            5.00,  25.00),
        ("anthropic", "claude-opus-4-6",            5.00,  25.00),
        ("anthropic", "claude-opus-4-5-20251101",   5.00,  25.00),
        ("anthropic", "claude-opus-4-1-20250805",  15.00,  75.00),
        ("anthropic", "claude-sonnet-4-6",          3.00,  15.00),
        ("anthropic", "claude-sonnet-4-5-20250929", 3.00,  15.00),
        ("anthropic", "claude-haiku-4-5-20251001",  1.00,   5.00),
        ("anthropic", "claude-3-5-sonnet-20241022", 3.00,  15.00),
        ("anthropic", "claude-3-5-haiku-20241022",  0.80,   4.00),
        ("anthropic", "claude-3-opus-20240229",    15.00,  75.00),
        ("anthropic", "claude-3-haiku-20240307",    0.25,   1.25),
        ("anthropic", "default",                    3.00,  15.00),
        # Qwen via Alibaba Model Studio. Pricing is regional — these are the
        # Frankfurt/EU rates (Singapore is dearer at 2.00 / 6.00).
        ("qwen", "qwen3.8-max",                     1.65,   4.951),
        ("qwen", "qwen3-max",                       1.65,   4.951),
        ("qwen", "default",                         1.65,   4.951),
        # OpenAI image models: text input and image output per 1M tokens,
        # from the official pricing page (2026-09-28). A text prompt bills
        # only text-input tokens; the image comes back as output tokens.
        ("openai", "gpt-image-2",                   5.00,  30.00),
        ("openai", "gpt-image-1.5",                 5.00,  32.00),
        ("openai", "gpt-image-1",                   5.00,  40.00),
        ("openai", "gpt-image-1-mini",              2.00,   8.00),
    ]
    for backend, model, inp, out in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO pricing (backend, model, input_per_1m_usd, output_per_1m_usd) VALUES (?,?,?,?)",
            (backend, model, inp, out),
        )
    conn.commit()


def _seed_cached_input_pricing(conn: sqlite3.Connection) -> None:
    """Add Kimi cache-hit rates while preserving user-edited pricing.

    The USD values mirror this project's rounded USD conversion of Kimi's
    official CNY rates.  Kimi does not publish a separate high-speed cache
    price; that row follows the existing high-speed 2x multiplier.
    """
    rates = {
        "default": 0.19,
        "kimi-k2.7-code": 0.19,
        "kimi-k2.7-code-highspeed": 0.38,
        "kimi-k2.6": 0.16,
        "kimi-k3": 0.30,
    }
    for model, rate in rates.items():
        conn.execute(
            "UPDATE pricing SET cached_input_per_1m_usd = ? "
            "WHERE backend = 'kimi' AND model = ? "
            "AND cached_input_per_1m_usd IS NULL",
            (rate, model),
        )
    conn.commit()


def _seed_default_agents(conn: sqlite3.Connection) -> None:
    """Insert built-in agents that may not exist in the DB yet (new agents added in updates)."""
    for name, definition in BUILTIN_AGENTS.items():
        conn.execute("""
            INSERT OR IGNORE INTO agents
              (name, label, enabled, version, allowed_providers, allowed_tools,
               budget_limit_eur, requires_approval, description, log_path, auto_generated)
            VALUES (?,?,1,'1.0',?,?,?,?,?,?,?)
        """, (
            name, definition["label"], json.dumps([]),
            json.dumps(definition["allowed_tools"])
            if definition["allowed_tools"] is not None else None,
            definition["budget_limit_eur"], 0, definition["description"],
            "data/logs/runs.jsonl", 0,
        ))
    conn.commit()


def _seed_default_tools(conn: sqlite3.Connection) -> None:
    """Restore missing built-ins without changing user-controlled policy fields."""
    for name, definition in BUILTIN_TOOLS.items():
        conn.execute(
            """INSERT OR IGNORE INTO tools
               (name, label, enabled, version, allowed_providers,
                budget_limit_eur, requires_approval, description, system_prompt,
                recommended_provider, recommended_model)
               VALUES (?, ?, 1, '1.0', '[]', NULL, 0, ?, ?, ?, ?)""",
            (
                name, name, definition["description"], definition["system"],
                definition["recommended_provider"], definition["recommended_model"],
            ),
        )
    conn.commit()


def _retire_moved_agents(conn: sqlite3.Connection) -> None:
    """Remove only obsolete built-in registry rows, preserving all history.

    Usage, runs, chats, and domain tables are intentionally untouched. Forge
    agents (`auto_generated = 1`) are also protected even if a user happened to
    choose a formerly built-in name.
    """
    placeholders = ",".join("?" for _ in RETIRED_BUILTIN_AGENTS)
    conn.execute(
        f"DELETE FROM agents WHERE auto_generated = 0 AND name IN ({placeholders})",
        tuple(sorted(RETIRED_BUILTIN_AGENTS)),
    )
    conn.commit()


# ──────────────────────────────────────────────────────────────
# Migration
# ──────────────────────────────────────────────────────────────

def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _migrate_from_json(conn: sqlite3.Connection) -> None:
    print("[DB] First run — migrating JSON files to SQLite...")

    _migrate_registry(conn)
    _migrate_tool_prompts(conn)
    _migrate_pricing(conn)
    _migrate_usage_log(conn)
    _migrate_runs(conn)
    _migrate_settings(conn)

    conn.commit()
    print("[DB] Migration complete.")


def _migrate_registry(conn: sqlite3.Connection) -> None:
    path = BASE_DIR / "config" / "registry.json"
    data = _load_json(path, {"agents": [], "tools": []})

    # Built-ins are seeded exclusively from agent_catalog. Only explicitly
    # generated legacy entries may be recovered from an old JSON file.
    for a in data.get("agents", []):
        name = a.get("name", "")
        if not a.get("auto_generated", False):
            continue
        if name in BUILTIN_AGENTS or name in RETIRED_BUILTIN_AGENTS:
            continue
        conn.execute("""
            INSERT OR IGNORE INTO agents
              (name, label, enabled, version, allowed_providers, allowed_tools,
               budget_limit_eur, requires_approval, description, log_path, auto_generated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            name,
            a.get("label", ""),
            1 if a.get("enabled", True) else 0,
            a.get("version", "1.0"),
            json.dumps(a.get("allowed_providers", [])),
            json.dumps(a.get("allowed_tools")) if a.get("allowed_tools") is not None else None,
            a.get("budget_limit_eur"),
            1 if a.get("requires_approval", False) else 0,
            a.get("description", ""),
            a.get("log_path", "data/logs/runs.jsonl"),
            1 if a.get("auto_generated", False) else 0,
        ))

    for t in data.get("tools", []):
        conn.execute("""
            INSERT OR IGNORE INTO tools
              (name, label, enabled, version, allowed_providers,
               budget_limit_eur, requires_approval, description)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            t.get("name", ""),
            t.get("name", ""),
            1 if t.get("enabled", True) else 0,
            t.get("version", "1.0"),
            json.dumps(t.get("allowed_providers", [])),
            t.get("budget_limit_eur"),
            1 if t.get("requires_approval", False) else 0,
            t.get("description", ""),
        ))


def _migrate_tool_prompts(conn: sqlite3.Connection) -> None:
    path = BASE_DIR / "config" / "tool_prompts.json"
    data = _load_json(path, {})

    for name, cfg in data.items():
        conn.execute("""
            INSERT INTO tools (name, label, system_prompt, recommended_provider, recommended_model)
            VALUES (?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
              system_prompt        = excluded.system_prompt,
              recommended_provider = excluded.recommended_provider,
              recommended_model    = excluded.recommended_model
        """, (
            name,
            name,
            cfg.get("system", ""),
            cfg.get("recommended_provider", "ollama"),
            cfg.get("recommended_model", ""),
        ))


def _migrate_pricing(conn: sqlite3.Connection) -> None:
    path = BASE_DIR / "config" / "pricing.json"
    data = _load_json(path, {})

    eur_per_usd = data.get("eur_per_usd", 0.92)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("eur_per_usd", str(eur_per_usd))
    )

    for backend, models in data.items():
        if backend == "eur_per_usd" or not isinstance(models, dict):
            continue
        for model, prices in models.items():
            if not isinstance(prices, dict):
                continue
            conn.execute("""
                INSERT OR REPLACE INTO pricing
                  (backend, model, input_per_1m_usd, cached_input_per_1m_usd,
                   output_per_1m_usd)
                VALUES (?,?,?,?,?)
            """, (
                backend,
                model,
                float(prices.get("input_per_1m_usd", 0.0)),
                (
                    float(prices["cached_input_per_1m_usd"])
                    if "cached_input_per_1m_usd" in prices
                    else None
                ),
                float(prices.get("output_per_1m_usd", 0.0)),
            ))


def _migrate_usage_log(conn: sqlite3.Connection) -> None:
    path = BASE_DIR / "data" / "usage_log.json"
    entries = _load_json(path, [])

    for e in entries:
        conn.execute("""
            INSERT INTO usage
              (timestamp, agent, backend, model, input_tokens, output_tokens,
               cached_input_tokens, total_tokens, cost_eur, cost_type, cloud)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            e.get("timestamp", ""),
            e.get("agent", ""),
            e.get("backend", ""),
            e.get("model", ""),
            int(e.get("input_tokens", 0)),
            int(e.get("output_tokens", 0)),
            int(e.get("cached_input_tokens", e.get("cached_tokens", 0))),
            int(e.get("total_tokens", 0)),
            float(e.get("cost_eur", e.get("estimated_cost", 0.0))),
            e.get("cost_type", "estimated"),
            1 if e.get("cloud", False) else 0,
        ))


def _migrate_runs(conn: sqlite3.Connection) -> None:
    path = BASE_DIR / "data" / "logs" / "runs.jsonl"
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            conn.execute("""
                INSERT OR IGNORE INTO runs
                  (run_id, timestamp, agent, tool, provider, model, mode,
                   prompt_summary, status, input_tokens, output_tokens,
                   cost_eur, duration_sec, error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                e.get("run_id", ""),
                e.get("timestamp", ""),
                e.get("agent", ""),
                e.get("tool", ""),
                e.get("provider", ""),
                e.get("model", ""),
                e.get("mode", ""),
                e.get("prompt_summary", ""),
                e.get("status", "success"),
                int(e.get("input_tokens", 0)),
                int(e.get("output_tokens", 0)),
                float(e.get("cost_eur", 0.0)),
                float(e.get("duration_sec", 0.0)),
                e.get("error"),
            ))
        except Exception:
            pass


def _migrate_settings(conn: sqlite3.Connection) -> None:
    path = BASE_DIR / "config" / "settings.json"
    data = _load_json(path, {})

    for key, value in data.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value))
        )
