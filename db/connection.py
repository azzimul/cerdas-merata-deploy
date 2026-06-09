"""
DB connection — PostgreSQL (default) or SQLite (USE_SQLITE=1 for offline demo).
"""

import os

USE_SQLITE = os.getenv("USE_SQLITE", "0") == "1"

if USE_SQLITE:
    import sqlite3

    _DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cerdas_merata_demo.db")

    def get_conn():
        conn = sqlite3.connect(_DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_sqlite():
        """Create SQLite tables equivalent to the PostgreSQL schema."""
        conn = get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL DEFAULT (datetime('now','+1 day'))
            );
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                nama_pendaftar TEXT NOT NULL,
                pendapatan_ortu INTEGER NOT NULL,
                jumlah_tanggungan INTEGER NOT NULL,
                tagihan_listrik INTEGER NOT NULL,
                wattage_listrik INTEGER NOT NULL,
                ipk REAL NOT NULL,
                status_ortu TEXT NOT NULL,
                pekerjaan_ortu TEXT NOT NULL,
                bantuan_lain INTEGER NOT NULL DEFAULT 0,
                kondisi_khusus TEXT,
                status_aplikasi TEXT NOT NULL DEFAULT 'pending',
                queue_rank INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expire_at TEXT NOT NULL DEFAULT (datetime('now','+30 days'))
            );
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                total_skor INTEGER NOT NULL,
                skor_per_kategori TEXT NOT NULL,
                reasoning_trace TEXT NOT NULL,
                status_keputusan TEXT NOT NULL,
                is_anomaly INTEGER NOT NULL DEFAULT 0,
                anomaly_reasons TEXT,
                admin_override INTEGER NOT NULL DEFAULT 0,
                override_reason TEXT,
                disqualify_reason TEXT,
                processed_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS appeals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                alasan_banding TEXT NOT NULL,
                status_banding TEXT NOT NULL DEFAULT 'open',
                catatan_admin TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS rank_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                rank_lama INTEGER NOT NULL,
                rank_baru INTEGER NOT NULL,
                triggered_by INTEGER NOT NULL REFERENCES applications(id),
                admin_id TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Seed system_config defaults if not present
        conn.execute("INSERT OR IGNORE INTO system_config (key, value) VALUES ('results_announced', 'false')")
        conn.execute("INSERT OR IGNORE INTO system_config (key, value) VALUES ('quota', '50')")

        # Add user_id column to applications if it doesn't exist yet (upgrade path)
        try:
            conn.execute("ALTER TABLE applications ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        except Exception:
            pass  # Column already exists

        conn.commit()
        conn.close()

else:
    import psycopg2
    import psycopg2.extras

    _DSN = os.getenv("DATABASE_URL") or (
        f"host={os.getenv('DB_HOST','localhost')} "
        f"port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','cerdas_merata')} "
        f"user={os.getenv('DB_USER','postgres')} "
        f"password={os.getenv('DB_PASS','')}"
    )

    def get_conn():
        conn = psycopg2.connect(_DSN)
        psycopg2.extras.register_default_jsonb(conn)
        return conn

    def init_postgres():
        """Apply schema.sql to the PostgreSQL database (idempotent — safe to run on every startup)."""
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path) as f:
            sql = f.read()

        # Split into individual statements, strip comment-only chunks
        statements = []
        for chunk in sql.split(";"):
            lines = [l for l in chunk.splitlines() if not l.strip().startswith("--")]
            stmt = "\n".join(lines).strip()
            if stmt:
                statements.append(stmt)

        conn = get_conn()
        try:
            cur = conn.cursor()
            for stmt in statements:
                cur.execute(stmt)
            conn.commit()
        finally:
            conn.close()


