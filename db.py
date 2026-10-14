import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    discord_id   INTEGER NOT NULL,
    platform     TEXT NOT NULL,
    account      TEXT NOT NULL,
    display_name TEXT NOT NULL,
    linked_at    INTEGER NOT NULL,
    PRIMARY KEY (discord_id, platform),
    UNIQUE (platform, account)
);

CREATE TABLE IF NOT EXISTS pending_verifications (
    discord_id   INTEGER NOT NULL,
    platform     TEXT NOT NULL,
    account      TEXT NOT NULL,
    display_name TEXT NOT NULL,
    code         TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (discord_id, platform)
);
"""


@dataclass(frozen=True)
class Link:
    platform: str
    account: str
    display_name: str


@dataclass(frozen=True)
class PendingVerification:
    account: str
    display_name: str
    code: str
    created_at: float


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def get_links(self, discord_id: int) -> list[Link]:
        rows = self.conn.execute(
            "SELECT platform, account, display_name FROM links WHERE discord_id = ? ORDER BY platform",
            (discord_id,),
        ).fetchall()
        return [Link(row["platform"], row["account"], row["display_name"]) for row in rows]

    def linked_user_ids(self) -> list[int]:
        rows = self.conn.execute("SELECT DISTINCT discord_id FROM links").fetchall()
        return [row["discord_id"] for row in rows]

    def link_owner(self, platform: str, account: str) -> int | None:
        row = self.conn.execute(
            "SELECT discord_id FROM links WHERE platform = ? AND account = ?",
            (platform, account),
        ).fetchone()
        return row["discord_id"] if row else None

    def set_link(self, discord_id: int, platform: str, account: str, display_name: str) -> bool:
        """Create or replace this user's link for a platform. Returns False if another user owns the account."""
        owner = self.link_owner(platform, account)
        if owner is not None and owner != discord_id:
            return False

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO links (discord_id, platform, account, display_name, linked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (discord_id, platform) DO UPDATE SET
                    account = excluded.account,
                    display_name = excluded.display_name,
                    linked_at = excluded.linked_at
                """,
                (discord_id, platform, account, display_name, int(time.time())),
            )
        return True

    def delete_link(self, discord_id: int, platform: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM links WHERE discord_id = ? AND platform = ?",
                (discord_id, platform),
            )
        return cursor.rowcount > 0

    def get_pending(self, discord_id: int, platform: str) -> PendingVerification | None:
        row = self.conn.execute(
            "SELECT account, display_name, code, created_at FROM pending_verifications "
            "WHERE discord_id = ? AND platform = ?",
            (discord_id, platform),
        ).fetchone()
        if row is None:
            return None
        return PendingVerification(row["account"], row["display_name"], row["code"], row["created_at"])

    def set_pending(self, discord_id: int, platform: str, account: str, display_name: str, code: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO pending_verifications (discord_id, platform, account, display_name, code, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (discord_id, platform) DO UPDATE SET
                    account = excluded.account,
                    display_name = excluded.display_name,
                    code = excluded.code,
                    created_at = excluded.created_at
                """,
                (discord_id, platform, account, display_name, code, time.time()),
            )

    def delete_pending(self, discord_id: int, platform: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM pending_verifications WHERE discord_id = ? AND platform = ?",
                (discord_id, platform),
            )
