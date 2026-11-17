import os
import sqlite3
from datetime import datetime


class TraccyMemory:
    def __init__(self, db_path: str = "data/traccy.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_tables()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal TEXT NOT NULL,
                    plan_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    project_dir TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def save_message(self, role: str, content: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (role, content, timestamp) VALUES (?, ?, ?)",
                (role, content, datetime.now().isoformat()),
            )

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def save_plan(self, goal: str, plan_text: str, project_dir: str, status: str = "PENDING_APPROVAL") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO plans (goal, plan_text, status, project_dir, created_at) VALUES (?, ?, ?, ?, ?)",
                (goal, plan_text, status, project_dir, datetime.now().isoformat()),
            )
            return cur.lastrowid

    def update_plan_status(self, plan_id: int, status: str):
        with self._connect() as conn:
            conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan_id))

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default