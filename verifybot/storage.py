"""SQLite persistence. One small file, no server needed."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    email        TEXT    NOT NULL,
    canonical    TEXT    NOT NULL,
    code_hash    TEXT    NOT NULL,
    expires_at   REAL    NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS sends (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    sent_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sends_user ON sends (guild_id, user_id, sent_at);

CREATE TABLE IF NOT EXISTS verified (
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    email        TEXT,
    canonical    TEXT,
    method       TEXT    NOT NULL,   -- 'email' or 'manual'
    verified_by  INTEGER,            -- admin user id for manual verifications
    note         TEXT,
    verified_at  REAL    NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
-- One email can only ever unlock one Discord account per server.
CREATE UNIQUE INDEX IF NOT EXISTS idx_verified_email
    ON verified (guild_id, canonical) WHERE canonical IS NOT NULL;
"""


@dataclass
class Pending:
    guild_id: int
    user_id: int
    email: str
    canonical: str
    code_hash: str
    expires_at: float
    attempts: int
    created_at: float


@dataclass
class Verified:
    guild_id: int
    user_id: int
    email: str | None
    canonical: str | None
    method: str
    verified_by: int | None
    note: str | None
    verified_at: float


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- pending codes ----
    def get_pending(self, guild_id: int, user_id: int) -> Pending | None:
        row = self.conn.execute(
            "SELECT * FROM pending WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        ).fetchone()
        return Pending(**dict(row)) if row else None

    def put_pending(self, p: Pending) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pending VALUES (?,?,?,?,?,?,?,?)",
            (p.guild_id, p.user_id, p.email, p.canonical, p.code_hash,
             p.expires_at, p.attempts, p.created_at),
        )
        self.conn.commit()

    def bump_attempts(self, guild_id: int, user_id: int) -> int:
        self.conn.execute(
            "UPDATE pending SET attempts = attempts + 1 WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        self.conn.commit()
        p = self.get_pending(guild_id, user_id)
        return p.attempts if p else 0

    def delete_pending(self, guild_id: int, user_id: int) -> None:
        self.conn.execute("DELETE FROM pending WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        self.conn.commit()

    # ---- send log (rate limiting) ----
    def log_send(self, guild_id: int, user_id: int, at: float) -> None:
        self.conn.execute("INSERT INTO sends VALUES (?,?,?)", (guild_id, user_id, at))
        self.conn.commit()

    def unlog_last_send(self, guild_id: int, user_id: int) -> None:
        self.conn.execute(
            "DELETE FROM sends WHERE rowid = (SELECT MAX(rowid) FROM sends WHERE guild_id=? AND user_id=?)",
            (guild_id, user_id),
        )
        self.conn.commit()

    def sends_since(self, guild_id: int, user_id: int, since: float) -> list[float]:
        rows = self.conn.execute(
            "SELECT sent_at FROM sends WHERE guild_id=? AND user_id=? AND sent_at>=? ORDER BY sent_at",
            (guild_id, user_id, since),
        ).fetchall()
        return [r[0] for r in rows]

    def prune_sends(self, before: float) -> None:
        self.conn.execute("DELETE FROM sends WHERE sent_at < ?", (before,))
        self.conn.commit()

    # ---- verified members ----
    def get_verified(self, guild_id: int, user_id: int) -> Verified | None:
        row = self.conn.execute(
            "SELECT * FROM verified WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        ).fetchone()
        return Verified(**dict(row)) if row else None

    def find_by_email(self, guild_id: int, canonical: str) -> Verified | None:
        row = self.conn.execute(
            "SELECT * FROM verified WHERE guild_id=? AND canonical=?", (guild_id, canonical)
        ).fetchone()
        return Verified(**dict(row)) if row else None

    def add_verified(self, v: Verified) -> None:
        """Raises sqlite3.IntegrityError if the email is already bound to someone else."""
        # Upsert only on the member's own row. (INSERT OR REPLACE would also "resolve"
        # an email clash by deleting the OTHER member's record — exactly what we must prevent.)
        self.conn.execute(
            """INSERT INTO verified VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (guild_id, user_id) DO UPDATE SET
                 email=excluded.email, canonical=excluded.canonical, method=excluded.method,
                 verified_by=excluded.verified_by, note=excluded.note, verified_at=excluded.verified_at""",
            (v.guild_id, v.user_id, v.email, v.canonical, v.method,
             v.verified_by, v.note, v.verified_at),
        )
        self.conn.commit()

    def remove_verified(self, guild_id: int, user_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM verified WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def count_verified(self, guild_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT method, COUNT(*) FROM verified WHERE guild_id=? GROUP BY method", (guild_id,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def count_pending(self, guild_id: int, now: float) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM pending WHERE guild_id=? AND expires_at>?", (guild_id, now)
        ).fetchone()[0]
