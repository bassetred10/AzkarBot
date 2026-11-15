"""Live group arena and native channel quizzes for ATHAR.

Group games use timed rounds with one answer per player per round.
Channel quizzes use Telegram's native non-anonymous quiz polls so answers
can be attributed to users when Telegram provides PollAnswer.user.
"""
from __future__ import annotations

import json
import logging
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
)

from app.database import Database
from app.games import GameCatalogue

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
                created_at REAL NOT NULL
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
    ) -> bool:
        try:
            await self.conn.execute(
                """INSERT INTO live_game_sessions
                   (session_id, chat_id, chat_title, owner_id, status, current_round,
                    total_rounds, question_id, used_question_ids, round_ends_at, started_at)
                   VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?, ?, ?)""",
                (
                    session_id, chat_id, chat_title, owner_id, total_rounds,
                    question_id, json.dumps([question_id]), deadline, time.time(),
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
        speed_bonus = min(5, max(0, int((deadline - now) // 5))) if is_correct else 0
        points = (10 + speed_bonus) if is_correct else 0
        cursor = await self.conn.execute(
            """INSERT OR IGNORE INTO live_game_answers
               (session_id, round_no, telegram_id, choice, is_correct, points, answered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, round_no, telegram_id, choice, int(is_correct), points, now),
        )
        await self.conn.commit()
        return "ok" if cursor.rowcount else "duplicate"

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
                      SUM(a.points) AS score, SUM(a.is_correct) AS correct,
                      COUNT(*) AS answered
               FROM live_game_answers a
               JOIN live_game_sessions s ON s.session_id=a.session_id
               JOIN users u ON u.telegram_id=a.telegram_id
               WHERE s.chat_id=?
               GROUP BY u.telegram_id
               ORDER BY score DESC, correct DESC, answered DESC, u.telegram_id
               LIMIT ?""",
            (chat_id, limit),
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

    async def register_poll(self, poll_id: str, chat_id: int, question_id: str) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO channel_quiz_polls (poll_id, chat_id, question_id, created_at) VALUES (?, ?, ?, ?)",
            (poll_id, chat_id, question_id, time.time()),
        )
        await self.conn.commit()

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
    return (
        header
        + f"🎯 {question['question']}\n\n"
        + "\n".join(f"{i + 1}️⃣ {option}" for i, option in enumerate(question["options"]))
        + f"\n\n⏳ أمامك {LIVE_ROUND_SECONDS} ثانية"
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


def _schedule_round(application: Application, session_id: str, deadline: float) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is unavailable; install python-telegram-bot[job-queue]")
    name = f"athar-live-round:{session_id}"
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    job_queue.run_once(
        live_round_timeout,
        when=max(0.1, deadline - time.time()),
        data=session_id,
        name=name,
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


async def live_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text(
            "اللعب الجماعي يكون داخل مجموعة أو مجموعة فائقة. أضف أَثَر إلى مجموعتك ثم أرسل /livegame."
        )
        return
    rounds = DEFAULT_ROUNDS
    if context.args:
        try:
            rounds = int(context.args[0])
        except ValueError:
            await message.reply_text("استخدم /livegame أو /livegame 5 (من 1 إلى 10 جولات).")
            return
    if not 1 <= rounds <= MAX_ROUNDS:
        await message.reply_text("عدد الجولات يجب أن يكون بين 1 و10.")
        return
    store = _store(context)
    active = await store.active_for_chat(chat.id)
    if active:
        await message.reply_text(
            f"هناك منافسة جارية بالفعل: الجولة {active['current_round']}/{active['total_rounds']}."
            "\nاستخدم /gameboard للترتيب أو /endgame لإيقافها."
        )
        return
    games = _games(context)
    question = games.random_question()
    session_id = uuid.uuid4().hex[:10]
    deadline = time.time() + LIVE_ROUND_SECONDS
    created = await store.create_session(
        session_id,
        chat.id,
        chat.title or "مجموعة أَثَر",
        user.id,
        rounds,
        question["id"],
        deadline,
    )
    if not created:
        await message.reply_text("بدأت منافسة أخرى في هذه المجموعة للتو. استخدم /gameboard.")
        return
    session = await store.get_session(session_id)
    try:
        posted = await context.bot.send_message(
            chat_id=chat.id,
            text=_question_text(session, question),
            reply_markup=_question_keyboard(session_id, 1, question["options"]),
        )
        await store.set_message_id(session_id, posted.message_id)
        _schedule_round(context.application, session_id, deadline)
    except TelegramError as exc:
        await store.finish_session(session_id, "cancelled")
        logger.warning("Could not start live game in %s: %s", chat.id, exc)
        await message.reply_text("تعذر نشر الجولة. تأكد أن البوت يستطيع إرسال الرسائل في المجموعة.")
        return


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
    job_queue = context.application.job_queue
    if job_queue:
        for name in (
            f"athar-live-round:{session['session_id']}",
        ):
            for job in job_queue.get_jobs_by_name(name):
                job.schedule_removal()
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
    await query.answer("✅ تم تسجيل اختيارك. انتظر كشف النتيجة!")
    stats = await store.round_stats(session_id, round_no)
    fresh = await store.get_session(session_id)
    if fresh:
        await _safe_edit(
            context.application,
            fresh,
            _question_text(fresh, question, stats["answered"]),
            _question_keyboard(session_id, round_no, question["options"]),
        )


async def live_round_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = context.job.data
    store: LiveGameStore = context.application.bot_data["live_store"]
    session = await store.get_session(session_id)
    if not session or session["status"] != "active":
        return
    games: GameCatalogue = context.application.bot_data["games"]
    question = games.get(session["question_id"])
    if not question:
        await store.finish_session(session_id, "cancelled")
        return
    stats = await store.round_stats(session_id, session["current_round"])
    intro = _result_intro(question, stats)
    if session["current_round"] >= session["total_rounds"]:
        await store.finish_session(session_id)
        board = await store.session_board(session_id, 5)
        final_text = intro + "\n\n" + _board_text(board, "🏆 أبطال منافسة أَثَر")
        await _safe_edit(context.application, session, final_text)
        return

    used_ids = json.loads(session["used_question_ids"] or "[]")
    remaining = [item for item in games.questions if item["id"] not in used_ids]
    if not remaining:
        remaining = list(games.questions)
        used_ids = []
    next_question = random.choice(remaining)
    used_ids.append(next_question["id"])
    next_deadline = time.time() + LIVE_ROUND_SECONDS
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
        await message.reply_text("هذا الأمر مخصص لإدارة أَثَر.")
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
                is_anonymous=False,
                type="quiz",
                correct_option_ids=[question["answer"]],
                explanation=(question.get("explanation") or "أحسنت على المشاركة.")[:200],
                open_period=90,
                hide_results_until_closes=True,
            )
            if poll_message.poll:
                await store.register_poll(poll_message.poll.id, target["chat_id"], question["id"])
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
    # Poll metadata keeps the expected answer server-side; only the option index is received.
    cursor = await store.conn.execute(
        "SELECT question_id FROM channel_quiz_polls WHERE poll_id=?",
        (answer.poll_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return
    question = _games(context).get(row["question_id"])
    if not question:
        return
    await context.application.bot_data["db"].upsert_user(answer.user)
    await store.record_poll_answer(
        answer.poll_id,
        answer.user.id,
        answer.option_ids[0],
        answer.option_ids[0] == question["answer"],
    )


async def channel_board_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    rows = await _store(context).channel_board(10)
    await update.effective_message.reply_text(_board_text(rows, "📣 لوحة متسابقي القنوات"))


async def game_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    text = (
        "🎮 ساحة أَثَر — لعب وتعلّم\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ تحدٍ فردي: أرسل /quiz في الخاص.\n\n"
        "👥 منافسة المجموعات: أضف البوت إلى مجموعتك ثم اكتب /livegame. "
        "تبدأ 5 جولات، لكل جولة 25 ثانية، مع نقاط للسرعة والإجابة الصحيحة.\n\n"
        "📣 تحديات القنوات: تنشر الإدارة مسابقات Telegram Quiz أصلية "
        "بوقت تصويت ونتائج قابلة للإحصاء.\n\n"
        "🏆 /gameboard — ترتيب المجموعة\n🌍 /liveboard — الترتيب العام للمنافسات والقنوات\n"
        "⏹ /endgame — إيقاف المنافسة (للبادئ أو مشرفي المجموعة)"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 سؤال فردي", callback_data="live:solo")],
        [InlineKeyboardButton("🏆 الترتيب العام", callback_data="leaderboard")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
    ])
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise
    # Prevent the legacy game-menu handler from replacing the upgraded arena screen.
    raise ApplicationHandlerStop


async def solo_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    question = _games(context).random_question()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(option, callback_data=f"quiz:{question['id']}:{index}")]
        for index, option in enumerate(question["options"])
    ])
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🎯 تحدي أَثَر\n━━━━━━━━━━━━━━━━━━\n"
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
    application.add_handler(CommandHandler("endgame", end_live_game_command))
    application.add_handler(CommandHandler("gameboard", group_board_command))
    application.add_handler(CommandHandler("channelquiz", channel_quiz_command))
    application.add_handler(CommandHandler("channelboard", channel_board_command))
    application.add_handler(CommandHandler("liveboard", liveboard_command))
    application.add_handler(CommandHandler("publishlivequiz", publish_live_quiz_command))
    application.add_handler(CallbackQueryHandler(live_admin_stats_callback, pattern=r"^adm:stats$"), group=-1)
    application.add_handler(CallbackQueryHandler(live_admin_publish_callback, pattern=r"^adm:quiz$"), group=-1)
    application.add_handler(CallbackQueryHandler(live_answer_callback, pattern=r"^lg:"))
    application.add_handler(CallbackQueryHandler(solo_quiz_callback, pattern=r"^live:solo$"))
    application.add_handler(PollAnswerHandler(poll_answer_handler))


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
            poll_message = await context.bot.send_poll(
                chat_id=target["chat_id"],
                question=question["question"][:300],
                options=[str(option)[:100] for option in question["options"][:10]],
                is_anonymous=False,
                type="quiz",
                correct_option_ids=[question["answer"]],
                explanation=(question.get("explanation") or "أحسنت على المشاركة.")[:200],
                open_period=90,
                hide_results_until_closes=True,
            )
            if poll_message.poll:
                await store.register_poll(
                    poll_message.poll.id, target["chat_id"], question["id"]
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
        await message.reply_text("هذا الأمر مخصص لإدارة أَثَر.")
        return
    sent, failed = await _publish_native_quiz_targets(context)
    await message.reply_text(
        f"🎯 تقرير نشر المسابقة\n✅ أُرسلت إلى: {sent}\n⚠️ تعذّر: {failed}"
        "\nالتصويت مفتوح 90 ثانية، وتُجمع نتائج المشاركين تلقائياً."
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
        f"📣 اختبارات القنوات: {stats['polls']}\n"
        f"👥 أصوات القنوات: {stats['poll_answers']}\n"
        f"🧠 إجابات فردية: {db_stats['game_answers']}"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 وجهات النشر", callback_data="adm:targets")],
        [InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="adm:help")],
    ]))
    raise ApplicationHandlerStop
