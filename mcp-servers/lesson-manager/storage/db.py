import sqlite3
import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path("E:/ai-tutor/data/tutor.db")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row_factory set."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        """Create tables from schema.sql if they don't exist."""
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.get_connection() as conn:
            conn.executescript(schema)
            conn.commit()

    def execute(self, query: str, params: tuple = ()) -> List[Dict]:
        """Execute query and return results as list of dicts."""
        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def execute_one(self, query: str, params: tuple = ()) -> Optional[Dict]:
        """Execute query and return single result as dict."""
        with self.get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row is not None else None

    def execute_write(self, query: str, params: tuple = ()) -> int:
        """Execute INSERT/UPDATE/DELETE. Return rowcount."""
        with self.get_connection() as conn:
            cur = conn.execute(query, params)
            conn.commit()
            return cur.rowcount
