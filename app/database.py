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
            CREATE TABLE IF NOT EXISTS publish_targets (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                chat_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS game_answers (
                telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                question_id TEXT NOT NULL,
                day TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (telegram_id, question_id, day)
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
            await self._db.execute("DELETE FROM bookmarks WHERE telegram_id = ? AND content_id = ?", (telegram_id, content_id))
            saved = False
        else:
            await self._db.execute("INSERT OR IGNORE INTO bookmarks (telegram_id, content_id) VALUES (?, ?)", (telegram_id, content_id))
            saved = True
        await self._db.commit()
        return saved

    async def get_bookmarks(self, telegram_id: int) -> list[str]:
        assert self._db is not None
        cursor = await self._db.execute("SELECT content_id FROM bookmarks WHERE telegram_id = ? ORDER BY created_at DESC", (telegram_id,))
        return [row["content_id"] for row in await cursor.fetchall()]

    async def add_target(self, chat_id: int, title: str, chat_type: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO publish_targets (chat_id, title, chat_type, enabled)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, chat_type=excluded.chat_type, enabled=1""",
            (chat_id, title, chat_type),
        )
        await self._db.commit()

    async def remove_target(self, chat_id: int) -> bool:
        assert self._db is not None
        cursor = await self._db.execute("DELETE FROM publish_targets WHERE chat_id=?", (chat_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_targets(self) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute("SELECT chat_id, title, chat_type, enabled FROM publish_targets ORDER BY title")
        return [dict(row) for row in await cursor.fetchall()]

    async def log_delivery(
        self,
        chat_id: int,
        kind: str,
        status: str,
        error_code: str | None = None,
        telegram_id: int | None = None,
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO delivery_logs
               (telegram_id, chat_id, kind, status, error_code)
               VALUES (?, ?, ?, ?, ?)""",
            (telegram_id, chat_id, kind, status, error_code),
        )
        await self._db.commit()

    async def record_game_answer(self, telegram_id: int, question_id: str, is_correct: bool) -> bool | None:
        assert self._db is not None
        cursor = await self._db.execute(
            """INSERT OR IGNORE INTO game_answers (telegram_id, question_id, day, is_correct)
               VALUES (?, ?, date('now'), ?)""",
            (telegram_id, question_id, int(is_correct)),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            return None
        return is_correct

    async def game_stats(self, telegram_id: int) -> dict[str, int]:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT COUNT(*) AS played, COALESCE(SUM(is_correct),0) AS correct FROM game_answers WHERE telegram_id=?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return {"played": int(row["played"]), "correct": int(row["correct"])}

    async def leaderboard(self, limit: int = 10) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT u.first_name, u.username, a.telegram_id,
                      COUNT(*) AS played, SUM(a.is_correct) AS score
               FROM game_answers a JOIN users u ON u.telegram_id=a.telegram_id
               GROUP BY a.telegram_id
               ORDER BY score DESC, played DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def stats(self) -> dict[str, int]:
        assert self._db is not None
        result: dict[str, int] = {}
        for table, key in (
            ("users", "users"), ("bookmarks", "bookmarks"),
            ("reminders", "reminders"), ("groups", "groups"),
            ("publish_targets", "targets"), ("game_answers", "game_answers"),
        ):
            cursor = await self._db.execute(f"SELECT COUNT(*) AS n FROM {table}")
            result[key] = int((await cursor.fetchone())["n"])
        return result
