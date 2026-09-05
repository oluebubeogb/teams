import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Store DB under /app/data so Coolify can mount a single directory volume
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "teams.db"


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection():
    _ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn):
    """Add new columns / tables if missing (safe for existing DBs)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    if "title" not in cols:
        conn.execute("ALTER TABLE files ADD COLUMN title TEXT DEFAULT ''")

    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "accounts_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN accounts_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_accounts_id ON users(accounts_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_collaborators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE(file_id, email)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collab_file ON file_collaborators(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collab_user ON file_collaborators(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collab_email ON file_collaborators(email)")


def init_db():
    _ensure_data_dir()
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            bio TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            storage_path TEXT,
            url TEXT,
            title TEXT DEFAULT '',
            description TEXT DEFAULT '',
            extracted_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            view_count INTEGER DEFAULT 0,
            download_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id);
        CREATE INDEX IF NOT EXISTS idx_files_type ON files(file_type);
        CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);
        """)
        _migrate(conn)
