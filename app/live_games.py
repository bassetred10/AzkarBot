"""Live group arena and native channel quizzes for ATHAR.

Group games use timed rounds with one answer per player per round.
Channel quizzes use Telegram's native non-anonymous quiz polls so answers
can be attributed to users when Telegram provides PollAnswer.user.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
import uuid
from typing import Any

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
    PollHandler,
)

from app.database import Database
from app.games import CATEGORY_ICONS, CATEGORY_LABELS, GameCatalogue

logger = logging.getLogger("athar.live_games")

LIVE_ROUND_SECONDS = 25
DEFAULT_ROUNDS = 5
MAX_ROUNDS = 10


class LiveGameStore:
    """Persistent state for live competitions; shares the app's SQLite connection."""

    def __init__(self, db: Database):
        self.db = db

    @property
    def conn(self) -> aiosqlite.Connection:
        if self.db._db is None:
            raise RuntimeError("Database is not connected")
        return self.db._db

    async def initialize(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_game_sessions (
                session_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL DEFAULT '',
                owner_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                current_round INTEGER NOT NULL,
                total_rounds INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                category_id TEXT NOT NULL DEFAULT 'all',
                round_seconds INTEGER NOT NULL DEFAULT 25,
                used_question_ids TEXT NOT NULL DEFAULT '[]',
                message_id INTEGER,
                round_ends_at REAL NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_live_game_per_chat
            ON live_game_sessions(chat_id) WHERE status = 'active';
            CREATE INDEX IF NOT EXISTS live_sessions_chat_idx
            ON live_game_sessions(chat_id, started_at DESC);
            CREATE TABLE IF NOT EXISTS live_game_answers (
                session_id TEXT NOT NULL REFERENCES live_game_sessions(session_id) ON DELETE CASCADE,
                round_no INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                choice INTEGER NOT NULL,
                is_correct INTEGER NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                answered_at REAL NOT NULL,
                PRIMARY KEY (session_id, round_no, telegram_id)
            );
            CREATE TABLE IF NOT EXISTS channel_quiz_polls (
                poll_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                is_anonymous INTEGER NOT NULL DEFAULT 0,
                total_voters INTEGER NOT NULL DEFAULT 0,
                correct_votes INTEGER NOT NULL DEFAULT 0,
                is_closed INTEGER NOT NULL DEFAULT 0,
                answer_index INTEGER,
                question_text TEXT
            );
            CREATE TABLE IF NOT EXISTS channel_quiz_answers (
                poll_id TEXT NOT NULL REFERENCES channel_quiz_polls(poll_id) ON DELETE CASCADE,
                telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                choice INTEGER NOT NULL,
                is_correct INTEGER NOT NULL,
                points INTEGER NOT NULL,
                answered_at REAL NOT NULL,
                PRIMARY KEY (poll_id, telegram_id)
            );
            """
        )
        # Migrate databases created by earlier releases without deleting user data.
        session_cursor = await self.conn.execute("PRAGMA table_info(live_game_sessions)")
        session_columns = {row["name"] for row in await session_cursor.fetchall()}
        if "category_id" not in session_columns:
            await self.conn.execute(
                "ALTER TABLE live_game_sessions ADD COLUMN category_id TEXT NOT NULL DEFAULT 'all'"
            )
        if "round_seconds" not in session_columns:
            await self.conn.execute(
                "ALTER TABLE live_game_sessions ADD COLUMN round_seconds INTEGER NOT NULL DEFAULT 25"
            )
        poll_cursor = await self.conn.execute("PRAGMA table_info(channel_quiz_polls)")
        poll_columns = {row["name"] for row in await poll_cursor.fetchall()}
        migrations = {
            "is_anonymous": "INTEGER NOT NULL DEFAULT 0",
            "total_voters": "INTEGER NOT NULL DEFAULT 0",
            "correct_votes": "INTEGER NOT NULL DEFAULT 0",
            "is_closed": "INTEGER NOT NULL DEFAULT 0",
            "answer_index": "INTEGER",
            "question_text": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in poll_columns:
                await self.conn.execute(
                    f"ALTER TABLE channel_quiz_polls ADD COLUMN {column} {definition}"
                )
        await self.conn.commit()

    async def active_for_chat(self, chat_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM live_game_sessions WHERE chat_id=? AND status='active' LIMIT 1",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def active_sessions(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM live_game_sessions WHERE status='active' ORDER BY started_at"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM live_game_sessions WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_session(
        self,
        session_id: str,
        chat_id: int,
        chat_title: str,
        owner_id: int,
        total_rounds: int,
        question_id: str,
        deadline: float,
        category_id: str = "all",
        round_seconds: int = LIVE_ROUND_SECONDS,
    ) -> bool:
        try:
            await self.conn.execute(
                """INSERT INTO live_game_sessions
                   (session_id, chat_id, chat_title, owner_id, status, current_round,
                    total_rounds, question_id, category_id, round_seconds,
                    used_question_ids, round_ends_at, started_at)
                   VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, chat_id, chat_title, owner_id, total_rounds,
                    question_id, category_id, round_seconds,
                    json.dumps([question_id]), deadline, time.time(),
                ),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            await self.conn.rollback()
            return False

    async def set_message_id(self, session_id: str, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE live_game_sessions SET message_id=? WHERE session_id=?",
            (message_id, session_id),
        )
        await self.conn.commit()

    async def record_answer(
        self,
        session_id: str,
        round_no: int,
        telegram_id: int,
        choice: int,
        is_correct: bool,
    ) -> str:
        session = await self.get_session(session_id)
        if not session:
            return "missing"
        if session["status"] != "active" or session["current_round"] != round_no:
            return "closed"
        now = time.time()
        deadline = float(session["round_ends_at"])
        if now >= deadline:
            return "closed"
        round_seconds = max(5, int(session.get("round_seconds") or LIVE_ROUND_SECONDS))
        bonus_step = round_seconds / 5
        speed_bonus = min(5, math.ceil(max(0.0, deadline - now) / bonus_step)) if is_correct else 0
        points = (10 + speed_bonus) if is_correct else 0
        cursor = await self.conn.execute(
            """INSERT OR IGNORE INTO live_game_answers
               (session_id, round_no, telegram_id, choice, is_correct, points, answered_at)
               SELECT session_id, ?, ?, ?, ?, ?, ?
               FROM live_game_sessions
               WHERE session_id=? AND status='active'
                 AND current_round=? AND round_ends_at>?""",
            (
                round_no, telegram_id, choice, int(is_correct), points, now,
                session_id, round_no, now,
            ),
        )
        await self.conn.commit()
        if cursor.rowcount:
            return "ok"
        cursor = await self.conn.execute(
            """SELECT 1 FROM live_game_answers
               WHERE session_id=? AND round_no=? AND telegram_id=?""",
            (session_id, round_no, telegram_id),
        )
        return "duplicate" if await cursor.fetchone() else "closed"

    async def round_stats(self, session_id: str, round_no: int) -> dict[str, int]:
        cursor = await self.conn.execute(
            """SELECT COUNT(*) AS answered,
                      COALESCE(SUM(is_correct), 0) AS correct
               FROM live_game_answers WHERE session_id=? AND round_no=?""",
            (session_id, round_no),
        )
        row = await cursor.fetchone()
        return {"answered": int(row["answered"]), "correct": int(row["correct"])}

    async def session_board(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """SELECT u.telegram_id, u.first_name, u.username,
                      SUM(a.points) AS score, SUM(a.is_correct) AS correct,
                      COUNT(*) AS answered
               FROM live_game_answers a
               JOIN users u ON u.telegram_id=a.telegram_id
               WHERE a.session_id=?
               GROUP BY u.telegram_id
               ORDER BY score DESC, correct DESC, answered DESC, u.telegram_id
               LIMIT ?""",
            (session_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def group_board(self, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """SELECT u.telegram_id, u.first_name, u.username,
                      SUM(scores.points) AS score,
                      SUM(scores.is_correct) AS correct,
                      COUNT(*) AS answered
               FROM (
                   SELECT a.telegram_id, a.points, a.is_correct
                   FROM live_game_answers a
                   JOIN live_game_sessions s ON s.session_id=a.session_id
                   WHERE s.chat_id=?
                   UNION ALL
                   SELECT a.telegram_id, a.points, a.is_correct
                   FROM channel_quiz_answers a
                   JOIN channel_quiz_polls p ON p.poll_id=a.poll_id
                   WHERE p.chat_id=? AND p.is_anonymous=0
               ) AS scores
               JOIN users u ON u.telegram_id=scores.telegram_id
               GROUP BY u.telegram_id
               ORDER BY score DESC, correct DESC, answered DESC, u.telegram_id
               LIMIT ?""",
            (chat_id, chat_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def advance_session(
        self,
        session_id: str,
        question_id: str,
        used_ids: list[str],
        deadline: float,
    ) -> bool:
        session = await self.get_session(session_id)
        if not session or session["status"] != "active":
            return False
        cursor = await self.conn.execute(
            """UPDATE live_game_sessions
               SET current_round=current_round+1, question_id=?,
                   used_question_ids=?, round_ends_at=?
               WHERE session_id=? AND status='active'""",
            (question_id, json.dumps(used_ids), deadline, session_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def finish_session(self, session_id: str, status: str = "finished") -> None:
        await self.conn.execute(
            """UPDATE live_game_sessions SET status=?, ended_at=?
               WHERE session_id=? AND status='active'""",
            (status, time.time(), session_id),
        )
        await self.conn.commit()

    async def register_poll(
        self,
        poll_id: str,
        chat_id: int,
        question_id: str,
        is_anonymous: bool = False,
        answer_index: int | None = None,
        question_text: str | None = None,
    ) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO channel_quiz_polls
               (poll_id, chat_id, question_id, created_at, is_anonymous,
                answer_index, question_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                poll_id, chat_id, question_id, time.time(),
                int(is_anonymous), answer_index, question_text,
            ),
        )
        await self.conn.commit()

    async def record_poll_state(self, poll: Any) -> bool:
        poll_id = getattr(poll, "id", None)
        if not poll_id:
            return False
        options = getattr(poll, "options", ()) or ()
        correct_ids = set(getattr(poll, "correct_option_ids", ()) or ())
        correct_votes = sum(
            int(getattr(option, "voter_count", 0) or 0)
            for index, option in enumerate(options)
            if index in correct_ids
        )
        cursor = await self.conn.execute(
            """UPDATE channel_quiz_polls
               SET total_voters=?, correct_votes=?, is_closed=?
               WHERE poll_id=? AND is_anonymous=1""",
            (
                int(getattr(poll, "total_voter_count", 0) or 0),
                correct_votes,
                int(bool(getattr(poll, "is_closed", False))),
                poll_id,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def channel_poll_board(self, limit: int = 10) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """SELECT p.poll_id, p.chat_id, p.question_id, p.question_text,
                      p.total_voters, p.correct_votes, p.is_closed, p.created_at,
                      COALESCE(t.title, CAST(p.chat_id AS TEXT)) AS chat_title
               FROM channel_quiz_polls p
               LEFT JOIN publish_targets t ON t.chat_id=p.chat_id
               WHERE p.is_anonymous=1
               ORDER BY p.created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def record_poll_answer(
        self, poll_id: str, telegram_id: int, choice: int, is_correct: bool
    ) -> bool:
        cursor = await self.conn.execute(
            "SELECT poll_id FROM channel_quiz_polls WHERE poll_id=?",
            (poll_id,),
        )
        if not await cursor.fetchone():
            return False
        cursor = await self.conn.execute(
            """INSERT OR IGNORE INTO channel_quiz_answers
               (poll_id, telegram_id, choice, is_correct, points, answered_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (poll_id, telegram_id, choice, int(is_correct), 10 if is_correct else 0, time.time()),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def channel_board(self, limit: int = 10) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """SELECT u.telegram_id, u.first_name, u.username,
                      SUM(a.points) AS score, SUM(a.is_correct) AS correct,
                      COUNT(*) AS answered
               FROM channel_quiz_answers a
               JOIN users u ON u.telegram_id=a.telegram_id
               GROUP BY u.telegram_id
               ORDER BY score DESC, correct DESC, answered DESC, u.telegram_id
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]


def _store(context: ContextTypes.DEFAULT_TYPE) -> LiveGameStore:
    return context.application.bot_data["live_store"]


def _games(context: ContextTypes.DEFAULT_TYPE) -> GameCatalogue:
    return context.application.bot_data["games"]


def _question_keyboard(session_id: str, round_no: int, options: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(options), 2):
        row = [
            InlineKeyboardButton(
                options[pos][:60],
                callback_data=f"lg:{session_id}:{round_no}:{pos}",
                style="primary",
            )
            for pos in range(index, min(index + 2, len(options)))
        ]
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _question_text(
    session: dict[str, Any],
    question: dict[str, Any],
    answered: int = 0,
    intro: str = "",
) -> str:
    header = (
        f"🎮 ساحة أَثَر | الجولة {session['current_round']}/{session['total_rounds']}\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )
    if intro:
        header = intro + "\n\n" + header
    round_seconds = max(5, int(session.get("round_seconds") or LIVE_ROUND_SECONDS))
    remaining = max(
        0,
        min(
            round_seconds,
            math.ceil(float(session["round_ends_at"]) - time.time()),
        ),
    )
    blocks = 10
    filled = max(0, min(blocks, math.ceil((remaining / round_seconds) * blocks)))
    progress = "🟩" * filled + "⬜" * (blocks - filled)
    return (
        header
        + f"🎯 {question['question']}\n\n"
        + "\n".join(f"{i + 1}️⃣ {option}" for i, option in enumerate(question["options"]))
        + f"\n\n⏳ المتبقي: {remaining} ثانية  {progress}"
        + f"\n👥 المشاركون: {answered}"
        + "\n🔒 تُكشف الإجابة والشرح عند انتهاء الجولة"
    )


def _display_name(row: dict[str, Any]) -> str:
    return row.get("first_name") or (
        "@" + row["username"] if row.get("username") else "مشارك"
    )


def _board_text(rows: list[dict[str, Any]], title: str) -> str:
    if not rows:
        return title + "\n━━━━━━━━━━━━━━━━━━\nلم تُسجّل مشاركات بعد."
    medals = ["🥇", "🥈", "🥉"]
    lines = [title, "━━━━━━━━━━━━━━━━━━"]
    for index, row in enumerate(rows):
        marker = medals[index] if index < 3 else f"{index + 1}."
        lines.append(
            f"{marker} {_display_name(row)} — {row['score']} نقطة "
            f"({row['correct']} صحيحة من {row['answered']})"
        )
    return "\n".join(lines)


def _result_intro(question: dict[str, Any], stats: dict[str, int]) -> str:
    answer = question["options"][question["answer"]]
    return (
        f"✅ انتهت الجولة\n"
        f"الإجابة الصحيحة: {answer}\n"
        f"✔️ إجابات صحيحة: {stats['correct']} · "
        f"👥 إجمالي المشاركين: {stats['answered']}\n"
        f"💡 {question.get('explanation', '')}\n"
        f"📚 {question.get('source', '')}"
    )


def _cancel_round_jobs(application: Application, session_id: str) -> None:
    job_queue = application.job_queue
    if not job_queue:
        return
    for suffix in ("round", "tick"):
        name = f"athar-live-{suffix}:{session_id}"
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()


def _schedule_round(application: Application, session_id: str, deadline: float) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is unavailable; install python-telegram-bot[job-queue]")
    _cancel_round_jobs(application, session_id)
    remaining = max(0.1, deadline - time.time())
    job_queue.run_once(
        live_round_timeout,
        when=remaining,
        data=session_id,
        name=f"athar-live-round:{session_id}",
    )
    if remaining > 1:
        job_queue.run_once(
            live_countdown_tick,
            when=min(5.0, remaining),
            data=session_id,
            name=f"athar-live-tick:{session_id}",
        )


async def live_countdown_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = context.job.data
    application = context.application
    store: LiveGameStore = application.bot_data["live_store"]
    session = await store.get_session(session_id)
    if not session or session["status"] != "active":
        return
    remaining = float(session["round_ends_at"]) - time.time()
    if remaining <= 1:
        return
    question = application.bot_data["games"].get(session["question_id"])
    if not question:
        return
    stats = await store.round_stats(session_id, session["current_round"])
    await _safe_edit(
        application,
        session,
        _question_text(session, question, stats["answered"]),
        _question_keyboard(session_id, session["current_round"], question["options"]),
    )
    job_queue = application.job_queue
    remaining = float(session["round_ends_at"]) - time.time()
    if job_queue and remaining > 1:
        job_queue.run_once(
            live_countdown_tick,
            when=min(5.0, max(0.5, remaining)),
            data=session_id,
            name=f"athar-live-tick:{session_id}",
        )


async def _safe_edit(application: Application, session: dict[str, Any], text: str, markup=None) -> bool:
    if not session.get("message_id"):
        return False
    try:
        await application.bot.edit_message_text(
            chat_id=session["chat_id"],
            message_id=session["message_id"],
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        return True
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return True
        logger.warning("Could not update live game message %s: %s", session["session_id"], exc)
        return False
    except TelegramError as exc:
        logger.warning("Telegram error updating live game %s: %s", session["session_id"], exc)
        return False


CATEGORY_ALIASES = {
    "all": "all", "quran": "quran", "قرآن": "quran", "القرآن": "quran",
    "seerah": "seerah", "sirah": "seerah", "سيرة": "seerah", "السيرة": "seerah",
    "hadith": "hadith", "حديث": "hadith", "الحديث": "hadith",
    "prophets": "prophets", "أنبياء": "prophets", "الأنبياء": "prophets",
    "worship": "worship", "عبادات": "worship", "العبادات": "worship",
    "companions": "companions", "صحابة": "companions", "الصحابة": "companions",
    "manners": "manners", "أخلاق": "manners", "الأخلاق": "manners",
}


async def _start_live_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category_id: str = "all",
    rounds: int = DEFAULT_ROUNDS,
    round_seconds: int = LIVE_ROUND_SECONDS,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text(
            "المنافسة المباشرة تبدأ داخل مجموعة. أضف أَثَر إلى مجموعتك ثم استخدم /livegame."
        )
        return
    if not 1 <= rounds <= MAX_ROUNDS:
        await message.reply_text("عدد الجولات يجب أن يكون من 1 إلى 10.")
        return
    if not 5 <= round_seconds <= 60:
        await message.reply_text("مدة الجولة يجب أن تكون بين 5 و60 ثانية.")
        return
    games = _games(context)
    if category_id != "all" and not games.by_category(category_id):
        await message.reply_text("لا توجد أسئلة في هذا الباب حالياً.")
        return
    store = _store(context)
    active = await store.active_for_chat(chat.id)
    if active:
        await message.reply_text(
            f"هناك منافسة جارية بالفعل: الجولة {active['current_round']}/{active['total_rounds']}."
            "\nاستخدم /gameboard أو /endgame."
        )
        return
    question = games.random_question(category_id)
    session_id = uuid.uuid4().hex[:10]
    deadline = time.time() + LIVE_ROUND_SECONDS
    created = await store.create_session(
        session_id, chat.id, chat.title or "مجموعة أَثَر",
        user.id, rounds, question["id"], deadline, category_id, round_seconds,
    )
    if not created:
        await message.reply_text("بدأت منافسة أخرى في هذه المجموعة للتو. استخدم /gameboard.")
        return
    session = await store.get_session(session_id)
    topic = "كل الأبواب" if category_id == "all" else CATEGORY_LABELS[category_id]
    try:
        posted = await context.bot.send_message(
            chat_id=chat.id,
            text=f"🏁 انطلقت بطولة أَثَر · {topic}\n"
                 f"🧩 {rounds} جولات · ⏱ {round_seconds} ثانية لكل جولة\n\n"
                 + _question_text(session, question),
            reply_markup=_question_keyboard(session_id, 1, question["options"]),
        )
        await store.set_message_id(session_id, posted.message_id)
        _schedule_round(context.application, session_id, deadline)
    except TelegramError as exc:
        await store.finish_session(session_id, "cancelled")
        logger.warning("Could not start live game in %s: %s", chat.id, exc)
        await message.reply_text("تعذر نشر الجولة. تأكد أن البوت يستطيع إرسال وتعديل رسائله.")
        return


async def live_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    category_id = "all"
    rounds = DEFAULT_ROUNDS
    for arg in context.args:
        if arg.isdigit():
            rounds = int(arg)
        elif arg in CATEGORY_ALIASES:
            category_id = CATEGORY_ALIASES[arg]
        else:
            await update.effective_message.reply_text(
                "الصيغة: /livegame [quran|seerah|hadith|prophets|worship|companions|manners] [1-10]"
            )
            return
    await _start_live_game(update, context, category_id, rounds)




async def speed_game_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    category_id = "all"
    rounds = DEFAULT_ROUNDS
    for arg in context.args:
        if arg.isdigit():
            rounds = int(arg)
        elif arg in CATEGORY_ALIASES:
            category_id = CATEGORY_ALIASES[arg]
        else:
            await update.effective_message.reply_text(
                "الصيغة: /speedgame [topic] [1-10] — كل جولة 12 ثانية."
            )
            return
    await _start_live_game(
        update, context, category_id=category_id, rounds=rounds, round_seconds=12
    )


async def live_category_game_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: str
) -> None:
    rounds = DEFAULT_ROUNDS
    if context.args:
        try:
            rounds = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("أدخل عدد الجولات من 1 إلى 10.")
            return
    await _start_live_game(update, context, category_id, rounds)


def make_category_command(category_id: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await live_category_game_command(update, context, category_id)
    return handler


async def start_live_category_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    try:
        category_id = query.data.split(":", 2)[2]
    except IndexError:
        await query.answer("اختر باباً صحيحاً.", show_alert=True)
        return
    if category_id != "all" and category_id not in CATEGORY_LABELS:
        await query.answer("هذا الباب غير متاح.", show_alert=True)
        return
    await query.answer("تجهيز البطولة...")
    await _start_live_game(update, context, category_id, DEFAULT_ROUNDS)




async def speed_arena_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    category_id = query.data.split(":", 2)[2]
    if category_id != "all" and category_id not in CATEGORY_LABELS:
        await query.answer("هذا الباب غير متاح.", show_alert=True)
        return
    await query.answer("⚡ انطلاق سباق السرعة...")
    await _start_live_game(
        update, context, category_id=category_id,
        rounds=DEFAULT_ROUNDS, round_seconds=12,
    )


async def true_false_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    category_id = "all"
    if context.args:
        raw = context.args[0]
        category_id = CATEGORY_ALIASES.get(raw, "")
        if not category_id:
            await message.reply_text("استخدم /truefalse [quran|seerah|hadith|prophets|worship|companions|manners]")
            return
    question = _games(context).random_question(category_id)
    correct_statement = random.choice((True, False))
    if correct_statement:
        statement_answer = question["answer"]
    else:
        statement_answer = random.choice([
            index for index in range(len(question["options"]))
            if index != question["answer"]
        ])
    statement = question["options"][statement_answer]
    prompt = f"صح أم خطأ؟\n{question['question']}\n\n{statement}"
    correct_index = 0 if correct_statement else 1
    poll = await context.bot.send_poll(
        chat_id=chat.id,
        question=prompt[:300],
        options=["✅ صح", "❌ خطأ"],
        is_anonymous=chat.type == "channel",
        allows_revoting=False,
        type="quiz",
        correct_option_ids=[correct_index],
        explanation=(
            f"الإجابة الصحيحة: {question['options'][question['answer']]}\n"
            f"{question.get('explanation', '')}\nالمصدر: {question['source']}"
        )[:200],
        open_period=60,
        hide_results_until_closes=True,
    )
    if poll.poll:
        await _store(context).register_poll(
            poll.poll.id,
            chat.id,
            question["id"],
            is_anonymous=chat.type == "channel",
            answer_index=correct_index,
            question_text=prompt,
        )
    await context.application.bot_data["db"].log_delivery(
        chat.id, "true_false_quiz", "sent"
    )


async def _is_chat_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False


async def end_live_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("استخدم هذا الأمر داخل المجموعة.")
        return
    session = await _store(context).active_for_chat(chat.id)
    if not session:
        await message.reply_text("لا توجد منافسة نشطة حالياً.")
        return
    if user.id != session["owner_id"] and not await _is_chat_admin(context, chat.id, user.id):
        await message.reply_text("إيقاف المنافسة متاح لمن بدأها أو لمشرفي المجموعة.")
        return
    await _store(context).finish_session(session["session_id"], "cancelled")
    _cancel_round_jobs(context.application, session["session_id"])
    board = await _store(context).session_board(session["session_id"], 5)
    final_text = "⏹ أُوقفت المنافسة بواسطة الإدارة.\n\n" + _board_text(board, "🏆 ترتيب هذه المنافسة")
    await _safe_edit(context.application, session, final_text)
    await message.reply_text("تم إيقاف المنافسة.")


async def live_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not query.message:
        return
    try:
        _, session_id, raw_round, raw_choice = query.data.split(":")
        round_no, choice = int(raw_round), int(raw_choice)
    except (ValueError, TypeError):
        await query.answer("هذا الزر غير صالح.", show_alert=True)
        return
    store = _store(context)
    session = await store.get_session(session_id)
    if not session or session["chat_id"] != query.message.chat_id:
        await query.answer("هذه الجولة غير متاحة.", show_alert=True)
        return
    if session["status"] != "active" or session["current_round"] != round_no:
        await query.answer("انتهى وقت هذه الجولة.", show_alert=True)
        return
    question = _games(context).get(session["question_id"])
    if not question or not 0 <= choice < len(question["options"]):
        await query.answer("الخيار لم يعد متاحاً.", show_alert=True)
        return
    await context.application.bot_data["db"].upsert_user(query.from_user)
    result = await store.record_answer(
        session_id, round_no, query.from_user.id, choice,
        choice == question["answer"],
    )
    if result == "duplicate":
        await query.answer("إجابتك مسجلة بالفعل لهذه الجولة.", show_alert=True)
        return
    if result != "ok":
        await query.answer("انتهى وقت الجولة. انتظر السؤال التالي.", show_alert=True)
        return
    await query.answer("✅ تم تسجيل اختيارك. سيتم تحديث عدّاد المشاركين مباشرة!")


async def live_round_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = context.job.data
    store: LiveGameStore = context.application.bot_data["live_store"]
    session = await store.get_session(session_id)
    if not session or session["status"] != "active":
        return
    games: GameCatalogue = context.application.bot_data["games"]
    question = games.get(session["question_id"])
    if not question:
        _cancel_round_jobs(context.application, session_id)
        await store.finish_session(session_id, "cancelled")
        return
    job_queue = context.application.job_queue
    if job_queue:
        for job in job_queue.get_jobs_by_name(f"athar-live-tick:{session_id}"):
            job.schedule_removal()
    stats = await store.round_stats(session_id, session["current_round"])
    intro = _result_intro(question, stats)
    if session["current_round"] >= session["total_rounds"]:
        _cancel_round_jobs(context.application, session_id)
        await store.finish_session(session_id)
        board = await store.session_board(session_id, 5)
        final_text = intro + "\n\n" + _board_text(board, "🏆 أبطال منافسة أَثَر")
        await _safe_edit(context.application, session, final_text)
        return

    used_ids = json.loads(session["used_question_ids"] or "[]")
    category_id = session.get("category_id") or "all"
    next_question = games.random_question(
        category_id=category_id,
        exclude_ids=set(used_ids),
    )
    if next_question["id"] in used_ids:
        used_ids = []
    used_ids.append(next_question["id"])
    next_deadline = time.time() + max(
        5, int(session.get("round_seconds") or LIVE_ROUND_SECONDS)
    )
    advanced = await store.advance_session(
        session_id, next_question["id"], used_ids, next_deadline
    )
    if not advanced:
        return
    fresh = await store.get_session(session_id)
    await _safe_edit(
        context.application,
        fresh,
        _question_text(fresh, next_question, 0, intro),
        _question_keyboard(session_id, fresh["current_round"], next_question["options"]),
    )
    _schedule_round(context.application, session_id, next_deadline)


async def restore_live_sessions(application: Application) -> None:
    """Re-arm timers after a service restart; the session state remains in SQLite."""
    store: LiveGameStore = application.bot_data["live_store"]
    for session in await store.active_sessions():
        if not session.get("message_id"):
            await store.finish_session(session["session_id"], "cancelled")
            continue
        _schedule_round(application, session["session_id"], float(session["round_ends_at"]))


async def group_board_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if chat.type in ("group", "supergroup"):
        rows = await _store(context).group_board(chat.id, 10)
        await message.reply_text(_board_text(rows, "🏆 ترتيب مجموعة أَثَر"))
    else:
        db: Database = context.application.bot_data["db"]
        await message.reply_text(await _global_board_text(db))


async def _global_board_text(db: Database) -> str:
    entries = await db.leaderboard(10)
    if not entries:
        return "🏆 لوحة أَثَر\nلا توجد نتائج بعد. ابدأ أول تحدٍ!"
    lines = ["🏆 لوحة أَثَر", "━━━━━━━━━━━━━━━━━━"]
    medals = ["🥇", "🥈", "🥉"]
    for index, item in enumerate(entries):
        mark = medals[index] if index < 3 else f"{index + 1}."
        name = item["first_name"] or (("@" + item["username"]) if item["username"] else "مشارك")
        lines.append(f"{mark} {name} — {item['score']} نقطة")
    return "\n".join(lines)


async def channel_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if user.id not in settings.admin_ids:
        await message.reply_text(
            "إدارة النشر العام غير مهيّأة بعد. أرسل /myid ثم أضف رقمك إلى متغير ADMIN_IDS في إعدادات Render أو ملف .env، وبعدها أعد تشغيل البوت."
            if not settings.admin_ids
            else "هذا الأمر مخصص لمالك البوت المضاف ضمن ADMIN_IDS."
        )
        return
    targets = await context.application.bot_data["db"].list_targets()
    channels = [target for target in targets if target["chat_type"] == "channel"]
    if not channels:
        await message.reply_text("لا توجد قنوات مسجلة. أضف البوت مشرفاً ثم استخدم /addtarget @channel.")
        return
    question = _games(context).random_question()
    store = _store(context)
    delivered = failed = 0
    for target in channels:
        try:
            poll_message = await context.bot.send_poll(
                chat_id=target["chat_id"],
                question=question["question"][:300],
                options=[str(option)[:100] for option in question["options"][:10]],
                is_anonymous=True,
                allows_revoting=False,
                type="quiz",
                correct_option_ids=[question["answer"]],
                explanation=(question.get("explanation") or "أحسنت على المشاركة.")[:200],
                open_period=90,
                hide_results_until_closes=True,
            )
            if poll_message.poll:
                await store.register_poll(
                    poll_message.poll.id, target["chat_id"], question["id"],
                    is_anonymous=True,
                )
            await context.application.bot_data["db"].log_delivery(
                target["chat_id"], "channel_quiz", "sent"
            )
            delivered += 1
        except TelegramError as exc:
            logger.warning("Channel quiz delivery failed for %s: %s", target["chat_id"], exc)
            await context.application.bot_data["db"].log_delivery(
                target["chat_id"], "channel_quiz", "failed", type(exc).__name__
            )
            failed += 1
    await message.reply_text(
        f"📣 تقرير تحدي القنوات\n✅ نُشر في: {delivered}\n⚠️ تعذّر: {failed}"
        "\nمدة التصويت: 90 ثانية. استخدم /channelboard لمشاهدة النتائج المسجلة."
    )


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    if not answer or not answer.user or not answer.option_ids:
        return
    store: LiveGameStore = context.application.bot_data["live_store"]
    # Custom modes may store their own answer index (for example, true/false).
    cursor = await store.conn.execute(
        "SELECT question_id, answer_index FROM channel_quiz_polls WHERE poll_id=?",
        (answer.poll_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return
    question = _games(context).get(row["question_id"])
    if not question:
        return
    expected = row["answer_index"]
    if expected is None:
        expected = question["answer"]
    await context.application.bot_data["db"].upsert_user(answer.user)
    await store.record_poll_answer(
        answer.poll_id,
        answer.user.id,
        answer.option_ids[0],
        answer.option_ids[0] == int(expected),
    )




async def poll_state_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    poll = update.poll
    if not poll:
        return
    store: LiveGameStore = context.application.bot_data["live_store"]
    await store.record_poll_state(poll)

async def channel_board_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    rows = await _store(context).channel_poll_board(10)
    if not rows:
        await update.effective_message.reply_text(
            "📣 إحصائيات مسابقات القنوات\n━━━━━━━━━━━━━━━━━━\n"
            "لم تُنشر اختبارات في القنوات بعد. استخدم /channelquiz."
        )
        return
    lines = ["📣 إحصائيات مسابقات القنوات", "━━━━━━━━━━━━━━━━━━"]
    for row in rows:
        question = _games(context).get(row["question_id"])
        title = row.get("question_text") or (
            question["question"] if question else "اختبار أَثَر"
        )
        status = "مغلقة" if row["is_closed"] else "مفتوحة"
        lines.extend([
            f"\n📍 {row['chat_title']} · {status}",
            title,
            f"👥 المشاركات: {row['total_voters']} · ✅ الصحيحة: {row['correct_votes']}",
        ])
    lines.append(
        "\n🔒 تصويت القنوات مجهول؛ لذلك نعرض إحصاءات إجمالية لا أسماء المصوّتين."
    )
    await update.effective_message.reply_text("\n".join(lines)[:3900])


async def game_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    chat_type = query.message.chat.type
    text = (
        "🎮 ساحة أَثَر | مركز الألعاب\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "اختر الباب الذي تحب اختباره. كل سؤال يعرض تفسيراً ومصدراً بعد الإجابة.\n\n"
        "⚡ المنافسة الجماعية: ابدأ /livegame أو اختر باباً من أزرار البطولة. "
        "5 جولات، 25 ثانية لكل جولة، ونقاط سرعة وترتيب مستقل للمجموعة.\n"
        "✅ صح أم خطأ: /truefalse\n"
        "📊 ترتيب المجموعة: /gameboard · الترتيب العام: /liveboard\n"
        "🎯 أوقف البطولة: /endgame"
    )
    rows = [
        [InlineKeyboardButton("🎯 سؤال عشوائي", callback_data="live:solo:all", style="success")],
        [
            InlineKeyboardButton("📖 القرآن", callback_data="live:solo:quran"),
            InlineKeyboardButton("🕌 السيرة", callback_data="live:solo:seerah"),
        ],
        [
            InlineKeyboardButton("📜 الحديث", callback_data="live:solo:hadith"),
            InlineKeyboardButton("🌟 الأنبياء", callback_data="live:solo:prophets"),
        ],
        [
            InlineKeyboardButton("🤲 العبادات", callback_data="live:solo:worship"),
            InlineKeyboardButton("🛡️ الصحابة", callback_data="live:solo:companions"),
        ],
        [InlineKeyboardButton("🌿 الأخلاق والآداب", callback_data="live:solo:manners")],
    ]
    if chat_type in ("group", "supergroup"):
        rows.extend([
            [InlineKeyboardButton("🏁 بطولة المجموعة · كل الأبواب", callback_data="arena:start:all", style="success")],
        [InlineKeyboardButton("⚡ سباق السرعة · 12 ثانية", callback_data="arena:speed:all", style="success")],
            [
                InlineKeyboardButton("بطولة القرآن", callback_data="arena:start:quran"),
                InlineKeyboardButton("بطولة السيرة", callback_data="arena:start:seerah"),
            ],
            [
                InlineKeyboardButton("بطولة الحديث", callback_data="arena:start:hadith"),
                InlineKeyboardButton("بطولة العبادات", callback_data="arena:start:worship"),
            ],
            [
                InlineKeyboardButton("بطولة الأنبياء", callback_data="arena:start:prophets"),
                InlineKeyboardButton("بطولة الصحابة", callback_data="arena:start:companions"),
            ],
            [InlineKeyboardButton("بطولة الأخلاق", callback_data="arena:start:manners")],
        ])
    rows.extend([
        [InlineKeyboardButton("🏆 لوحة المتصدرين", callback_data="leaderboard", style="primary")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
    ])
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise
    raise ApplicationHandlerStop


async def solo_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    parts = query.data.split(":")
    category_id = parts[2] if len(parts) > 2 else "all"
    if category_id != "all" and category_id not in CATEGORY_LABELS:
        await query.answer("هذا الباب غير متاح.", show_alert=True)
        return
    await query.answer()
    question = _games(context).random_question(category_id)
    topic = "عام" if category_id == "all" else CATEGORY_LABELS[category_id]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            option,
            callback_data=f"quiz:{question['id']}:{index}",
            style="primary",
        )]
        for index, option in enumerate(question["options"])
    ])
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"🎯 تحدي أَثَر · {topic}\n━━━━━━━━━━━━━━━━━━\n"
        + question["question"] + "\n\nاختر الإجابة الصحيحة:",
        reply_markup=keyboard,
    )


def register_live_game_handlers(application: Application) -> None:
    # Group -1 replaces the old static game menu before legacy callbacks see it.
    application.add_handler(
        CallbackQueryHandler(game_menu_callback, pattern=r"^game$"),
        group=-1,
    )
    application.add_handler(CommandHandler("livegame", live_game_command))
    application.add_handler(CommandHandler("speedgame", speed_game_command))
    application.add_handler(CommandHandler("sirahgame", make_category_command("seerah")))
    application.add_handler(CommandHandler("qurangame", make_category_command("quran")))
    application.add_handler(CommandHandler("hadithgame", make_category_command("hadith")))
    application.add_handler(CommandHandler("prophetsgame", make_category_command("prophets")))
    application.add_handler(CommandHandler("worshipgame", make_category_command("worship")))
    application.add_handler(CommandHandler("companionsgame", make_category_command("companions")))
    application.add_handler(CommandHandler("mannersgame", make_category_command("manners")))
    application.add_handler(CommandHandler("truefalse", true_false_command))
    application.add_handler(CommandHandler("endgame", end_live_game_command))
    application.add_handler(CommandHandler("gameboard", group_board_command))
    application.add_handler(CommandHandler("channelquiz", channel_quiz_command))
    application.add_handler(CommandHandler("channelboard", channel_board_command))
    application.add_handler(CommandHandler("liveboard", liveboard_command))
    application.add_handler(CommandHandler("publishlivequiz", publish_live_quiz_command))
    application.add_handler(CallbackQueryHandler(live_admin_stats_callback, pattern=r"^adm:stats$"), group=-1)
    application.add_handler(CallbackQueryHandler(live_admin_channels_callback, pattern=r"^adm:channels$"), group=-1)
    application.add_handler(CallbackQueryHandler(live_admin_publish_callback, pattern=r"^adm:quiz$"), group=-1)
    application.add_handler(CommandHandler("help", enhanced_help_command), group=-1)
    application.add_handler(CallbackQueryHandler(enhanced_admin_help_callback, pattern=r"^adm:help$"), group=-1)
    application.add_handler(CallbackQueryHandler(speed_arena_callback, pattern=r"^arena:speed:"))
    application.add_handler(CallbackQueryHandler(start_live_category_callback, pattern=r"^arena:start:"))
    application.add_handler(CallbackQueryHandler(live_answer_callback, pattern=r"^lg:"))
    application.add_handler(CallbackQueryHandler(solo_quiz_callback, pattern=r"^live:solo(?::|$)"))
    application.add_handler(PollAnswerHandler(poll_answer_handler))
    application.add_handler(PollHandler(poll_state_handler))


async def _publish_native_quiz_targets(
    context: ContextTypes.DEFAULT_TYPE,
    channel_only: bool = False,
) -> tuple[int, int]:
    db: Database = context.application.bot_data["db"]
    store: LiveGameStore = _store(context)
    targets = await db.list_targets()
    if channel_only:
        targets = [target for target in targets if target["chat_type"] == "channel"]
    question = _games(context).random_question()
    sent = failed = 0
    for target in targets:
        try:
            is_anonymous = target["chat_type"] == "channel"
            poll_message = await context.bot.send_poll(
                chat_id=target["chat_id"],
                question=question["question"][:300],
                options=[str(option)[:100] for option in question["options"][:10]],
                is_anonymous=is_anonymous,
                allows_revoting=False,
                type="quiz",
                correct_option_ids=[question["answer"]],
                explanation=(question.get("explanation") or "أحسنت على المشاركة.")[:200],
                open_period=90,
                hide_results_until_closes=True,
            )
            if poll_message.poll:
                await store.register_poll(
                    poll_message.poll.id,
                    target["chat_id"],
                    question["id"],
                    is_anonymous=is_anonymous,
                )
            await db.log_delivery(target["chat_id"], "native_quiz", "sent")
            sent += 1
        except TelegramError as exc:
            logger.warning("Native quiz delivery failed for %s: %s", target["chat_id"], exc)
            await db.log_delivery(
                target["chat_id"], "native_quiz", "failed", type(exc).__name__
            )
            failed += 1
    return sent, failed


async def publish_live_quiz_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user, message = update.effective_user, update.effective_message
    settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if user.id not in settings.admin_ids:
        await message.reply_text(
            "إدارة النشر العام غير مهيّأة بعد. أرسل /myid ثم أضف رقمك إلى متغير ADMIN_IDS في إعدادات Render أو ملف .env، وبعدها أعد تشغيل البوت."
            if not settings.admin_ids
            else "هذا الأمر مخصص لمالك البوت المضاف ضمن ADMIN_IDS."
        )
        return
    sent, failed = await _publish_native_quiz_targets(context)
    await message.reply_text(
        f"🎯 تقرير نشر المسابقة\n✅ أُرسلت إلى: {sent}\n⚠️ تعذّر: {failed}"
        "\nالتصويت مفتوح 90 ثانية: أسماء ونقاط في المجموعات، وإحصاءات مجهولة في القنوات."
    )


async def liveboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.effective_message:
        return
    store = _store(context)
    cursor = await store.conn.execute(
        """SELECT u.telegram_id, u.first_name, u.username,
                  SUM(scores.points) AS score,
                  SUM(scores.is_correct) AS correct,
                  COUNT(*) AS answered
           FROM (
               SELECT telegram_id, points, is_correct FROM live_game_answers
               UNION ALL
               SELECT telegram_id, points, is_correct FROM channel_quiz_answers
           ) AS scores
           JOIN users u ON u.telegram_id=scores.telegram_id
           GROUP BY u.telegram_id
           ORDER BY score DESC, correct DESC, answered DESC, u.telegram_id
           LIMIT 10"""
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    await update.effective_message.reply_text(
        _board_text(rows, "🌍 الترتيب العام — ساحة أَثَر")
    )


async def _live_stats(store: LiveGameStore) -> dict[str, int]:
    stats = {}
    queries = {
        "sessions": "SELECT COUNT(*) AS n FROM live_game_sessions",
        "active": "SELECT COUNT(*) AS n FROM live_game_sessions WHERE status='active'",
        "group_answers": "SELECT COUNT(*) AS n FROM live_game_answers",
        "polls": "SELECT COUNT(*) AS n FROM channel_quiz_polls",
        "poll_answers": "SELECT COUNT(*) AS n FROM channel_quiz_answers",
        "channel_votes": "SELECT COALESCE(SUM(total_voters), 0) AS n FROM channel_quiz_polls WHERE is_anonymous=1",
        "channel_correct": "SELECT COALESCE(SUM(correct_votes), 0) AS n FROM channel_quiz_polls WHERE is_anonymous=1",
        "managed_destinations": "SELECT COUNT(*) AS n FROM community_settings",
        "auto_morning": "SELECT COALESCE(SUM(morning_enabled), 0) AS n FROM community_settings",
        "auto_evening": "SELECT COALESCE(SUM(evening_enabled), 0) AS n FROM community_settings",
        "auto_quiz": "SELECT COALESCE(SUM(quiz_enabled), 0) AS n FROM community_settings",
        "deliveries_7d": "SELECT COUNT(*) AS n FROM delivery_logs WHERE created_at >= datetime('now', '-7 days')",
        "failed_7d": "SELECT COUNT(*) AS n FROM delivery_logs WHERE status='failed' AND created_at >= datetime('now', '-7 days')",
    }
    for key, sql in queries.items():
        cursor = await store.conn.execute(sql)
        stats[key] = int((await cursor.fetchone())["n"])
    return stats


async def live_admin_stats_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query:
        return
    settings = context.application.bot_data["settings"]
    if not query.from_user or query.from_user.id not in settings.admin_ids:
        await query.answer("غير مصرح لك بالدخول.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    store = _store(context)
    stats = await _live_stats(store)
    db_stats = await context.application.bot_data["db"].stats()
    text = (
        "📈 إحصائيات أَثَر — لوحة الإدارة\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 المستخدمون: {db_stats['users']}\n"
        f"⭐ المحفوظات: {db_stats['bookmarks']}\n"
        f"📢 وجهات النشر: {db_stats['targets']}\n"
        f"🎮 المنافسات الجماعية: {stats['sessions']}\n"
        f"🟢 نشطة الآن: {stats['active']}\n"
        f"🗳 إجابات الجولات: {stats['group_answers']}\n"
        f"📣 الاختبارات المنشورة: {stats['polls']}\n"
        f"👥 إجابات المسابقات المعرّفة: {stats['poll_answers']}\n"
        f"📊 تصويت القنوات المجهول: {stats['channel_votes']}\n"
        f"✅ الصحيحة في القنوات: {stats['channel_correct']}\n"
        f"🧠 إجابات فردية: {db_stats['game_answers']}\n"
        f"🏘 وجهات لها إعدادات: {stats['managed_destinations']}\n"
        f"☀️ جداول أذكار الصباح: {stats['auto_morning']}\n"
        f"🌙 جداول أذكار المساء: {stats['auto_evening']}\n"
        f"🎯 جداول سؤال اليوم: {stats['auto_quiz']}\n"
        f"📬 عمليات نشر آخر 7 أيام: {stats['deliveries_7d']}\n"
        f"⚠️ إخفاقات آخر 7 أيام: {stats['failed_7d']}"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 وجهات النشر", callback_data="adm:targets")],
        [InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="adm:help")],
    ]))
    raise ApplicationHandlerStop


async def live_admin_publish_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query:
        return
    settings = context.application.bot_data["settings"]
    if not query.from_user or query.from_user.id not in settings.admin_ids:
        await query.answer("غير مصرح لك بالنشر.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer("جارٍ نشر الاختبار...")
    sent, failed = await _publish_native_quiz_targets(context)
    text = (
        "📣 تقرير نشر الاختبار التفاعلي\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"✅ أُرسل إلى: {sent}\n"
        f"⚠️ تعذّر: {failed}\n"
        "⏳ مدة التصويت: 90 ثانية\n"
        "📊 تُجمع الإجابات المسجلة تلقائياً."
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 تحديث الإحصائيات", callback_data="adm:stats")],
            [InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="adm:help")],
        ]),
    )
    raise ApplicationHandlerStop


async def enhanced_help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "📖 دليل أَثَر | الألعاب والتحكم\n━━━━━━━━━━━━━━━━━━\n"
        "🎮 الألعاب الفردية:\n"
        "/quiz — سؤال عشوائي\n"
        "/truefalse — صح أم خطأ (ويمكن إضافة باب)\n\n"
        "🏁 بطولات المجموعات:\n"
        "/livegame — 5 جولات من كل الأبواب\n"
        "/livegame seerah 8 — 8 جولات في السيرة\n"
        "/speedgame — سباق سريع، 12 ثانية للسؤال\n"
        "/speedgame hadith 7 — سبع جولات حديث سريعة\n"
        "/qurangame · /sirahgame · /hadithgame\n"
        "/prophetsgame · /worshipgame\n"
        "/companionsgame · /mannersgame\n"
        "/gameboard — ترتيب هذه المجموعة\n"
        "/liveboard — ترتيب اللاعبين العام\n"
        "/endgame — إيقاف البطولة الحالية\n\n"
        "🏘 إعداد المجموعة (لمشرفيها):\n"
        "/groupadmin — مركز التحكم\n"
        "/groupstats — إحصائيات النشر\n"
        "/setmorning 07:00 · /setevening 18:00\n"
        "/setquiz 20:00 — توقيت الجزائر\n\n"
        "📣 القنوات (لمالك البوت):\n"
        "/channeladmin @channel — لوحة القناة\n"
        "/channelboard — إحصائيات التصويت المجهول\n"
        "/setchanneltime @channel morning 07:00\n\n"
        "/myid — رقم حسابك · /admin — إدارة المالك"
    )
    raise ApplicationHandlerStop


async def enhanced_admin_help_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    settings = context.application.bot_data["settings"]
    if query.from_user.id not in settings.admin_ids:
        await query.answer("غير مصرح لك بالدخول.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    text = (
        "🛡️ مركز إدارة أَثَر | المالك\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📣 الوجهات والنشر:\n"
        "/addtarget @channel — تسجيل قناة\n"
        "/targets — عرض الوجهات\n"
        "/deltarget CHAT_ID — حذف وجهة\n"
        "/publish — نشر رسالة يدوياً\n"
        "/publishlivequiz — اختبار مباشر لكل الوجهات\n"
        "/channelquiz — اختبار للقنوات فقط\n\n"
        "📅 النشر المجدول للقنوات:\n"
        "/channeladmin @channel — لوحة تفعيل الصباح والمساء والسؤال\n"
        "/setchanneltime @channel morning 07:00\n\n"
        "📊 /channelboard — إحصائيات القنوات\n"
        "📈 زر الإحصائيات في لوحة المالك يعرض نشاط النظام.\n"
        "إعدادات كل مجموعة يديرها مشرفوها عبر /groupadmin."
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 الإحصائيات", callback_data="adm:stats")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
    ]))
    raise ApplicationHandlerStop


async def live_admin_channels_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    settings = context.application.bot_data["settings"]
    if query.from_user.id not in settings.admin_ids:
        await query.answer("هذه اللوحة خاصة بمالك البوت.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    targets = await context.application.bot_data["db"].list_targets()
    channels = [item for item in targets if item["chat_type"] == "channel"]
    if not channels:
        await query.edit_message_text(
            "📣 إدارة القنوات\n━━━━━━━━━━━━━━━━━━\n"
            "لا توجد قنوات مسجلة بعد.\n"
            "أضف البوت مشرفاً مع صلاحية النشر، ثم استخدم /addtarget @channel.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ مركز المالك", callback_data="adm:help")]
            ]),
        )
        raise ApplicationHandlerStop
    rows = []
    for channel in channels[:40]:
        rows.append([
            InlineKeyboardButton(
                "📣 " + (channel["title"] or str(channel["chat_id"]))[:52],
                callback_data=f"community:target:refresh:{channel['chat_id']}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton("📈 إحصائيات النظام", callback_data="adm:stats")],
        [InlineKeyboardButton("↩️ مركز المالك", callback_data="adm:help")],
    ])
    await query.edit_message_text(
        "📣 قنوات أَثَر المسجلة\n━━━━━━━━━━━━━━━━━━\n"
        "اختر قناة لفتح لوحة النشر اليدوي والتلقائي. "
        "لكل قناة أوقات وإعدادات مستقلة.",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    raise ApplicationHandlerStop
