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
    _apply_scheduled_price_changes(conn)
    _seed_cached_input_pricing(conn)
    _correct_stale_pricing(conn)
    _correct_pricing_2026_10(conn)
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


# Each provider's `default` row bills every model that has no row of its own
# (services/price_resolution.py), so it holds the provider's dearest current
# rate: a model the table does not know yet is over-estimated against the
# budget caps, never under. These are the same figures Imprint ships. Sources
# are the provider pages cited beside each model's own rows below.
DEFAULT_PRICES = {
    "anthropic": (10.00, 50.00),   # Claude Fable 5.1
    "openai":    (10.00, 50.00),   # gpt-6-astra; the -pro and o1 rows that
                                   # cost more have rows of their own
    "gemini":    (4.00, 18.00),    # Gemini 3.1 Pro, prompts over 200K
    "deepseek":  (1.32, 3.96),     # deepseek-v4-pro, peak hours
    "kimi":      (3.00, 15.00),    # kimi-k3
    "qwen":      (2.00, 6.00),     # qwen3.8-max, Singapore
}


# (backend, model, (old input, old output, old cached or None),
#                  (new input, new output, new cached or None))
# Rows shipped at a wrong or stale rate before 2026-10-08. Only a row still
# holding exactly the old value is touched, so a rate edited in Settings →
# Pricing is the user's. An old cached rate of None is not checked, and a new
# one of None leaves the column alone.
PRICING_CORRECTIONS_2026_10 = [
    # Defaults become the provider's dearest current rate (DEFAULT_PRICES).
    # Before this, gemini-2.5-flash/-pro billed at the Gemini default of 0/0,
    # gpt-4o at gpt-4o-mini's rate (~1/16), deepseek-v4-pro at the retired
    # deepseek-chat rate (~1/11) and claude-fable-5-1 at Sonnet 4.6's (~1/3).
    ("openai", "default", (0.15, 0.60, None), (*DEFAULT_PRICES["openai"], None)),
    ("anthropic", "default", (3.00, 15.00, None), (*DEFAULT_PRICES["anthropic"], None)),
    ("deepseek", "default", (0.14, 0.28, None), (*DEFAULT_PRICES["deepseek"], None)),
    ("gemini", "default", (0.0, 0.0, None), (*DEFAULT_PRICES["gemini"], None)),
    ("kimi", "default", (0.95, 4.00, 0.19), (*DEFAULT_PRICES["kimi"], 0.30)),
    # Singapore, not Frankfurt: the client's default endpoint is dashscope-intl.
    ("qwen", "default", (1.65, 4.951, None), (*DEFAULT_PRICES["qwen"], None)),
    ("qwen", "qwen3.8-max", (1.65, 4.951, None), (2.00, 6.00, None)),
    ("qwen", "qwen3-max", (1.65, 4.951, None), (1.20, 6.00, None)),
]


def _correct_pricing_2026_10(conn: sqlite3.Connection) -> None:
    """One-time repair of rows shipped at a stale rate (see the list above).

    config/pricing.json is read only into a new database, and
    _seed_missing_pricing only ever INSERT OR IGNOREs, so a corrected price
    never reaches a database that already holds the old row. Also: a shipped
    model holding a 0/0 placeholder takes its seeded rate (a zero was never a
    free-tier declaration here), and the retired gemini-1.5 rows, seeded at
    0/0, are removed. Guarded by a settings flag.
    """
    flag = conn.execute(
        "SELECT value FROM settings WHERE key = 'pricing_correction_2026_10'"
    ).fetchone()
    if flag:
        return
    for backend, model, old, new in PRICING_CORRECTIONS_2026_10:
        old_in, old_out, old_cached = old
        new_in, new_out, new_cached = new
        sets, params = ["input_per_1m_usd = ?", "output_per_1m_usd = ?"], [new_in, new_out]
        if new_cached is not None:
            sets.append("cached_input_per_1m_usd = ?")
            params.append(new_cached)
        query = (f"UPDATE pricing SET {', '.join(sets)} WHERE backend = ? AND model = ? "
                 "AND abs(input_per_1m_usd - ?) < 1e-9 "
                 "AND abs(output_per_1m_usd - ?) < 1e-9")
        params += [backend, model, old_in, old_out]
        if old_cached is not None:
            query += " AND abs(cached_input_per_1m_usd - ?) < 1e-9"
            params.append(old_cached)
        conn.execute(query, params)
    for backend, model, inp, out in _seeded_prices():
        conn.execute(
            "UPDATE pricing SET input_per_1m_usd = ?, output_per_1m_usd = ? "
            "WHERE backend = ? AND model = ? "
            "AND input_per_1m_usd = 0 AND output_per_1m_usd = 0",
            (inp, out, backend, model),
        )
    conn.execute(
        "DELETE FROM pricing WHERE backend = 'gemini' "
        "AND model IN ('gemini-1.5-flash', 'gemini-1.5-pro') "
        "AND input_per_1m_usd = 0 AND output_per_1m_usd = 0"
    )
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) "
        "VALUES ('pricing_correction_2026_10', 'done')"
    )
    conn.commit()


# Gemini 3.8 Flash is on an introductory price until the end of 2026 and
# doubles on 2027-01-01 (ai.google.dev pricing page, read 2026-10-07).
GEMINI_38_FLASH_INTRO = (0.75, 3.75)
GEMINI_38_FLASH_LIST = (1.50, 7.50)
GEMINI_38_FLASH_PRICE_CHANGE = "2027-01-01"


def _gemini_38_flash_rate(today=None) -> tuple[float, float]:
    from datetime import date

    today = today or date.today()
    if today.isoformat() >= GEMINI_38_FLASH_PRICE_CHANGE:
        return GEMINI_38_FLASH_LIST
    return GEMINI_38_FLASH_INTRO


def _apply_scheduled_price_changes(conn: sqlite3.Connection, today=None) -> None:
    """Move a row to its announced new price once the date arrives.

    Only a row still holding the introductory rate is changed, so a price
    the user typed into Settings → Pricing is left alone.
    """
    if _gemini_38_flash_rate(today) != GEMINI_38_FLASH_LIST:
        return
    conn.execute(
        "UPDATE pricing SET input_per_1m_usd = ?, output_per_1m_usd = ? "
        "WHERE backend = 'gemini' AND model = 'gemini-3.8-flash' "
        "AND input_per_1m_usd = ? AND output_per_1m_usd = ?",
        (*GEMINI_38_FLASH_LIST, *GEMINI_38_FLASH_INTRO),
    )
    conn.commit()


def _seeded_prices() -> list[tuple[str, str, float, float]]:
    """(backend, model, input, output) USD per 1M for every shipped row."""
    # Anthropic list prices per 1M tokens, from the official pricing table.
    # Note the Opus 4.5-and-later tier is 5/25, NOT the 15/75 that Opus 4/4.1
    # charged — seeding those at 15/75 overstated every estimate threefold.
    return [
        # From platform.claude.com/docs/en/about-claude/pricing (2026-10-08).
        ("anthropic", "claude-fable-5-1",          10.00,  50.00),
        ("anthropic", "claude-fable-5",            10.00,  50.00),
        # Opus 5.5 is cheaper than Opus 5 (4/20 against 5/25); checked on
        # platform.claude.com's pricing page on 2026-10-07.
        ("anthropic", "claude-opus-5-5",            4.00,  20.00),
        ("anthropic", "claude-sonnet-5-5",          2.00,  10.00),
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
        ("anthropic", "default",           *DEFAULT_PRICES["anthropic"]),
        # Qwen via Alibaba Model Studio. Pricing is regional; these are the
        # Singapore (international) rates, because dashscope-intl is the
        # endpoint services/qwen_client.py calls. From
        # alibabacloud.com/help/en/model-studio/model-pricing (2026-10-08).
        # The first input-length tier: qwen3-max is 2.40/12.00 over 32K and
        # 3.00/15.00 over 128K; qwen-plus 1.20/3.60 and qwen-flash 0.25/2.00
        # over 256K. qwen-plus is the non-thinking rate, which is how the
        # client calls it.
        ("qwen", "qwen3.8-max",                     2.00,   6.00),
        ("qwen", "qwen3-max",                       1.20,   6.00),
        ("qwen", "qwen-plus",                       0.40,   1.20),
        ("qwen", "qwen-flash",                      0.05,   0.40),
        ("qwen", "default",                    *DEFAULT_PRICES["qwen"]),
        # OpenAI image models: text input and image output per 1M tokens,
        # from the official pricing page (2026-09-28). A text prompt bills
        # only text-input tokens; the image comes back as output tokens.
        ("openai", "gpt-image-2",                   5.00,  30.00),
        ("openai", "gpt-image-1.5",                 5.00,  32.00),
        ("openai", "gpt-image-1",                   5.00,  40.00),
        ("openai", "gpt-image-1-mini",              2.00,   8.00),
        # From developers.openai.com/api/docs/pricing (2026-10-07). gpt-5.5
        # charges 10/45 for a prompt over 272K tokens; the estimate uses the
        # standard rate.
        ("openai", "gpt-5.5",                       5.00,  30.00),
        ("openai", "gpt-5.4-mini",                  0.75,   4.50),
        # From developers.openai.com/api/docs/pricing (2026-10-08). The dated
        # gpt-4o-2024-05-13 bills dearer than the gpt-4o alias it shares a
        # name with, so it needs its own row. The -pro, o1 and gpt-4-0613
        # rows cost more than the provider default and would otherwise be
        # under-billed at it.
        ("openai", "gpt-4o",                        2.50,  10.00),
        ("openai", "gpt-4o-2024-05-13",             5.00,  15.00),
        ("openai", "gpt-5.5-pro",                  30.00, 180.00),
        ("openai", "gpt-5.4-pro",                  30.00, 180.00),
        ("openai", "gpt-5.2-pro",                  21.00, 168.00),
        ("openai", "gpt-5-pro",                    15.00, 120.00),
        ("openai", "o1-pro",                      150.00, 600.00),
        ("openai", "o3-pro",                       20.00,  80.00),
        ("openai", "o1",                           15.00,  60.00),
        ("openai", "gpt-4-0613",                   30.00,  60.00),
        ("openai", "default",              *DEFAULT_PRICES["openai"]),
        # From ai.google.dev/gemini-api/docs/pricing (2026-10-07).
        # 3.1 Pro charges 4/18 above 200K tokens of prompt.
        ("gemini", "gemini-3.1-pro-preview",        2.00,  12.00),
        ("gemini", "gemini-3.8-flash", *_gemini_38_flash_rate()),
        # Same page, checked 2026-10-08. 2.5 Pro charges 2.50/15.00 above
        # 200K tokens of prompt.
        ("gemini", "gemini-2.5-flash",              0.30,   2.50),
        ("gemini", "gemini-2.5-pro",                1.25,  10.00),
        ("gemini", "default",              *DEFAULT_PRICES["gemini"]),
        # From api-docs.deepseek.com/quick_start/pricing (2026-10-08),
        # cache-miss input. DeepSeek bills half these rates off-peak; the
        # peak rate is seeded so the budget caps never undercount. The two
        # retired V4-Flash names are still accepted and billed as Flash.
        ("deepseek", "deepseek-flash",              0.30,   1.20),
        ("deepseek", "deepseek-v4-pro",             1.32,   3.96),
        ("deepseek", "deepseek-v4-flash",           0.30,   1.20),
        ("deepseek", "deepseek-v4-flash-vision-exp", 0.30,  1.20),
        ("deepseek", "default",          *DEFAULT_PRICES["deepseek"]),
        # From platform.kimi.ai/docs/pricing/chat (2026-10-08).
        ("kimi", "default",                  *DEFAULT_PRICES["kimi"]),
    ]


def _seed_missing_pricing(conn: sqlite3.Connection) -> None:
    """Insert default pricing rows that may not exist yet (e.g. new providers)."""
    for backend, model, inp, out in _seeded_prices():
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
        "default": 0.30,
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
    from services import settings_store

    data = settings_store.load_settings(
        settings_store.defaults_path(BASE_DIR), settings_store.override_path(BASE_DIR)
    )

    for key, value in data.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value))
        )
