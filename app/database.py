from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, path: str):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._db is not None:
            return
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")

    async def initialize(self) -> None:
        await self.connect()
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                language_code TEXT,
                timezone TEXT NOT NULL DEFAULT 'Africa/Algiers',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                content_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (telegram_id, content_id)
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                local_time TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'Africa/Algiers',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (telegram_id, kind)
            );
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                chat_id INTEGER,
                kind TEXT NOT NULL,
                content_id TEXT,
                status TEXT NOT NULL,
                error_code TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                content_id TEXT NOT NULL,
                note TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def upsert_user(self, user: Any) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, language_code, last_seen_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                language_code = excluded.language_code,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (user.id, user.username, user.first_name or "", user.language_code),
        )
        await self._db.commit()

    async def toggle_bookmark(self, telegram_id: int, content_id: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM bookmarks WHERE telegram_id = ? AND content_id = ?",
            (telegram_id, content_id),
        )
        exists = await cursor.fetchone()
        if exists:
            await self._db.execute(
                "DELETE FROM bookmarks WHERE telegram_id = ? AND content_id = ?",
                (telegram_id, content_id),
            )
            saved = False
        else:
            await self._db.execute(
                "INSERT OR IGNORE INTO bookmarks (telegram_id, content_id) VALUES (?, ?)",
                (telegram_id, content_id),
            )
            saved = True
        await self._db.commit()
        return saved

    async def get_bookmarks(self, telegram_id: int) -> list[str]:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT content_id FROM bookmarks WHERE telegram_id = ? ORDER BY created_at DESC",
            (telegram_id,),
        )
        rows = await cursor.fetchall()
        return [row["content_id"] for row in rows]

    async def stats(self) -> dict[str, int]:
        assert self._db is not None
        result: dict[str, int] = {}
        for table, key in (("users", "users"), ("bookmarks", "bookmarks"), ("reminders", "reminders"), ("groups", "groups")):
            cursor = await self._db.execute(f"SELECT COUNT(*) AS n FROM {table}")
            row = await cursor.fetchone()
            result[key] = int(row["n"])
        return result
