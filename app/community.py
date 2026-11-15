"""Per-group controls and scheduled publishing for ATHAR.

Group administrators configure only their own group. Scheduled posts are opt-in,
persisted in SQLite, and restored when the bot restarts.
"""
from __future__ import annotations

import logging
import random
import re
from datetime import time as day_time
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger("athar.community")
DEFAULT_TIMEZONE = "Africa/Algiers"
SCHEDULES = {
    "morning": ("morning_enabled", "morning_time", "☀️ ذكر الصباح", "07:00"),
    "evening": ("evening_enabled", "evening_time", "🌙 ذكر المساء", "18:00"),
    "quiz": ("quiz_enabled", "quiz_time", "🎯 سؤال اليوم", "20:00"),
    "hadith": ("hadith_enabled", "hadith_time", "📜 حديث اليوم", "14:00"),
}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class CommunityStore:
    def __init__(self, db):
        self.db = db

    @property
    def conn(self):
        if self.db._db is None:
            raise RuntimeError("Database is not connected")
        return self.db._db

    async def initialize(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS community_settings (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                morning_enabled INTEGER NOT NULL DEFAULT 0,
                evening_enabled INTEGER NOT NULL DEFAULT 0,
                quiz_enabled INTEGER NOT NULL DEFAULT 0,
                hadith_enabled INTEGER NOT NULL DEFAULT 0,
                welcome_enabled INTEGER NOT NULL DEFAULT 1,
                morning_time TEXT NOT NULL DEFAULT '07:00',
                evening_time TEXT NOT NULL DEFAULT '18:00',
                quiz_time TEXT NOT NULL DEFAULT '20:00',
                hadith_time TEXT NOT NULL DEFAULT '14:00',
                timezone TEXT NOT NULL DEFAULT 'Africa/Algiers',
                updated_by INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns_cursor = await self.conn.execute("PRAGMA table_info(community_settings)")
        columns = {row["name"] for row in await columns_cursor.fetchall()}
        if "hadith_enabled" not in columns:
            await self.conn.execute(
                "ALTER TABLE community_settings ADD COLUMN hadith_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "hadith_time" not in columns:
            await self.conn.execute(
                "ALTER TABLE community_settings ADD COLUMN hadith_time TEXT NOT NULL DEFAULT '14:00'"
            )
        await self.conn.commit()

    async def ensure_group(self, chat_id: int, title: str = "") -> None:
        await self.conn.execute(
            """INSERT INTO community_settings (chat_id, title)
               VALUES (?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE title END""",
            (chat_id, title),
        )
        await self.conn.commit()

    async def get(self, chat_id: int) -> dict[str, Any]:
        cursor = await self.conn.execute(
            "SELECT * FROM community_settings WHERE chat_id=?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        await self.ensure_group(chat_id)
        cursor = await self.conn.execute(
            "SELECT * FROM community_settings WHERE chat_id=?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row)

    async def all(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM community_settings ORDER BY title, chat_id"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def deactivate_group(self, chat_id: int) -> None:
        await self.conn.execute(
            """UPDATE community_settings
               SET morning_enabled=0, evening_enabled=0,
                   quiz_enabled=0, hadith_enabled=0, welcome_enabled=0,
                   updated_at=CURRENT_TIMESTAMP
               WHERE chat_id=?""",
            (chat_id,),
        )
        await self.conn.commit()

    async def toggle(self, chat_id: int, key: str, actor_id: int) -> dict[str, Any]:
        allowed = {
            "morning": "morning_enabled",
            "evening": "evening_enabled",
            "quiz": "quiz_enabled",
            "hadith": "hadith_enabled",
            "welcome": "welcome_enabled",
        }
        column = allowed.get(key)
        if not column:
            raise ValueError("Unknown group setting")
        current = await self.get(chat_id)
        next_value = 0 if current[column] else 1
        await self.conn.execute(
            f"UPDATE community_settings SET {column}=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
            (next_value, actor_id, chat_id),
        )
        await self.conn.commit()
        return await self.get(chat_id)

    async def set_time(self, chat_id: int, key: str, value: str, actor_id: int) -> dict[str, Any]:
        columns = {
            "morning": "morning_time",
            "evening": "evening_time",
            "quiz": "quiz_time",
            "hadith": "hadith_time",
        }
        column = columns.get(key)
        if not column or not TIME_RE.fullmatch(value):
            raise ValueError("Time must be HH:MM")
        await self.get(chat_id)
        await self.conn.execute(
            f"UPDATE community_settings SET {column}=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
            (value, actor_id, chat_id),
        )
        await self.conn.commit()
        return await self.get(chat_id)

    async def group_stats(self, chat_id: int) -> dict[str, int]:
        stats = {}
        queries = {
            "sent": "SELECT COUNT(*) AS n FROM delivery_logs WHERE chat_id=? AND status='sent'",
            "failed": "SELECT COUNT(*) AS n FROM delivery_logs WHERE chat_id=? AND status='failed'",
            "polls": "SELECT COUNT(*) AS n FROM channel_quiz_polls WHERE chat_id=?",
            "answers": """SELECT COUNT(*) AS n FROM channel_quiz_answers a
                          JOIN channel_quiz_polls p ON p.poll_id=a.poll_id
                          WHERE p.chat_id=?""",
        }
        for name, sql in queries.items():
            cursor = await self.conn.execute(sql, (chat_id,))
            stats[name] = int((await cursor.fetchone())["n"])
        return stats


def _store(context: ContextTypes.DEFAULT_TYPE) -> CommunityStore:
    return context.application.bot_data["community_store"]


async def _is_group_admin(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> bool:
    settings = context.application.bot_data["settings"]
    if user_id in settings.admin_ids:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False


def _panel_text(settings: dict[str, Any]) -> str:
    def state(enabled: int) -> str:
        return "🟢 مفعّل" if enabled else "⚪ متوقف"

    return (
        f"🛡️ مركز تحكم المجموعة\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏘 {settings.get('title') or 'مجموعتك'}\n"
        f"🌍 التوقيت: {settings['timezone']}\n\n"
        f"☀️ ذكر الصباح: {state(settings['morning_enabled'])} · {settings['morning_time']}\n"
        f"🌙 ذكر المساء: {state(settings['evening_enabled'])} · {settings['evening_time']}\n"
        f"🎯 سؤال اليوم: {state(settings['quiz_enabled'])} · {settings['quiz_time']}\n"
        f"👋 ترحيب الأعضاء: {state(settings['welcome_enabled'])}\n\n"
        "تغيير المواعيد: /setmorning 07:00 · /setevening 18:00 · /setquiz 20:00\n"
        "التوقيت الافتراضي: الجزائر. النشر التلقائي متوقف حتى تفعّله بنفسك."
    )


def _panel_keyboard(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    def label(title: str, enabled: int) -> str:
        return ("🟢 " if enabled else "⚪ ") + title

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                label("ذكر الصباح", settings["morning_enabled"]),
                callback_data="community:toggle:morning",
                style="success" if settings["morning_enabled"] else None,
            ),
            InlineKeyboardButton(
                label("ذكر المساء", settings["evening_enabled"]),
                callback_data="community:toggle:evening",
                style="success" if settings["evening_enabled"] else None,
            ),
        ],
        [
            InlineKeyboardButton(
                label("سؤال اليوم", settings["quiz_enabled"]),
                callback_data="community:toggle:quiz",
                style="success" if settings["quiz_enabled"] else None,
            ),
            InlineKeyboardButton(
                label("ترحيب الأعضاء", settings["welcome_enabled"]),
                callback_data="community:toggle:welcome",
                style="success" if settings["welcome_enabled"] else None,
            ),
        ],
        [
            InlineKeyboardButton("📿 انشر ذكر الآن", callback_data="community:post:morning"),
            InlineKeyboardButton("🌙 انشر ذكر مساء", callback_data="community:post:evening"),
        ],
        [
            InlineKeyboardButton("🎯 انشر سؤالاً الآن", callback_data="community:post:quiz", style="primary"),
            InlineKeyboardButton("📊 إحصائيات المجموعة", callback_data="community:stats"),
        ],
        [
            InlineKeyboardButton("🎮 ساحة الألعاب", callback_data="game", style="success"),
            InlineKeyboardButton("🔄 تحديث اللوحة", callback_data="community:refresh"),
        ],
    ])


def _channel_panel_text(settings: dict[str, Any]) -> str:
    def state(enabled: int) -> str:
        return "🟢 مفعّل" if enabled else "⚪ متوقف"

    return (
        f"📣 مركز تحكم القناة\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📍 {settings.get('title') or 'قناتك'}\n"
        f"🌍 التوقيت: {settings['timezone']}\n\n"
        f"☀️ ذكر الصباح: {state(settings['morning_enabled'])} · {settings['morning_time']}\n"
        f"🌙 ذكر المساء: {state(settings['evening_enabled'])} · {settings['evening_time']}\n"
        f"🎯 سؤال اليوم: {state(settings['quiz_enabled'])} · {settings['quiz_time']}\n\n"
        "تغيير الموعد: /setchanneltime @channel morning 07:00\n"
        "التوقيت الافتراضي: الجزائر. التلقائي متوقف حتى تفعّله."
    )


def _channel_panel_keyboard(
    settings: dict[str, Any], chat_id: int
) -> InlineKeyboardMarkup:
    def button(title: str, key: str, kind: str) -> InlineKeyboardButton:
        enabled = bool(settings[key])
        return InlineKeyboardButton(
            ("🟢 " if enabled else "⚪ ") + title,
            callback_data=f"community:target:toggle:{kind}:{chat_id}",
            style="success" if enabled else None,
        )

    return InlineKeyboardMarkup([
        [
            button("ذكر الصباح", "morning_enabled", "morning"),
            button("ذكر المساء", "evening_enabled", "evening"),
        ],
        [button("سؤال اليوم", "quiz_enabled", "quiz")],
        [
            InlineKeyboardButton("📿 انشر ذكر الآن", callback_data=f"community:target:post:morning:{chat_id}"),
            InlineKeyboardButton("🌙 انشر ذكر مساء", callback_data=f"community:target:post:evening:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🎯 انشر سؤالاً", callback_data=f"community:target:post:quiz:{chat_id}", style="primary"),
            InlineKeyboardButton("📊 إحصائيات", callback_data=f"community:target:stats:{chat_id}"),
        ],
        [InlineKeyboardButton("🔄 تحديث", callback_data=f"community:target:refresh:{chat_id}")],
    ])


async def group_control_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("افتح مركز التحكم من داخل المجموعة بالأمر /groupadmin.")
        return
    if not await _is_group_admin(context, chat.id, user.id):
        await message.reply_text("مركز التحكم متاح لمشرفي هذه المجموعة فقط.")
        return
    await _store(context).ensure_group(chat.id, chat.title or "")
    config = await _store(context).get(chat.id)
    await message.reply_text(
        _panel_text(config),
        reply_markup=_panel_keyboard(config),
        disable_web_page_preview=True,
    )


async def _reschedule_group(
    application: Application, chat_id: int, config: dict[str, Any]
) -> None:
    queue = application.job_queue
    if queue is None:
        raise RuntimeError("JobQueue is unavailable")
    for kind, (enabled_key, time_key, _label, _default) in SCHEDULES.items():
        name = f"athar-community:{chat_id}:{kind}"
        for job in queue.get_jobs_by_name(name):
            job.schedule_removal()
        if not config[enabled_key]:
            continue
        hour, minute = map(int, config[time_key].split(":"))
        timezone = ZoneInfo(config.get("timezone") or DEFAULT_TIMEZONE)
        queue.run_daily(
            scheduled_community_post,
            time=day_time(hour=hour, minute=minute, tzinfo=timezone),
            days=tuple(range(7)),
            data={"chat_id": chat_id, "kind": kind},
            name=name,
        )


async def _send_dhikr(
    chat_id: int, kind: str, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    content = context.application.bot_data["content"]
    category = "morning" if kind == "morning" else "evening"
    items = content.by_category(category)
    if not items:
        return False
    item = random.choice(items)
    title = "☀️ أذكار الصباح" if kind == "morning" else "🌙 أذكار المساء"
    lines = [
        f"🌿 أَثَر | {title}",
        "━━━━━━━━━━━━━━━━━━",
        item.get("title", "ذكر"),
        "",
        item["text"],
    ]
    if item.get("repeat"):
        lines.append(f"\n🔁 التكرار: {item['repeat']}")
    lines.extend([
        "",
        f"📚 {item['source']} · {item['reference']}",
    ])
    if item.get("grading"):
        lines.append(f"درجة الحديث: {item['grading']}")
    if item.get("virtue"):
        lines.append(f"\n✨ {item['virtue']}")
    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        disable_web_page_preview=True,
    )
    return True


async def _send_group_quiz(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    games = context.application.bot_data["games"]
    db = context.application.bot_data["db"]
    targets = await db.list_targets()
    target = next((item for item in targets if item["chat_id"] == chat_id), None)
    if not target:
        raise RuntimeError("This destination is not registered with ATHAR")
    is_anonymous = target["chat_type"] == "channel"
    question = games.random_question()
    poll = await context.bot.send_poll(
        chat_id=chat_id,
        question=question["question"][:300],
        options=[str(option)[:100] for option in question["options"][:10]],
        is_anonymous=is_anonymous,
        allows_revoting=False,
        type="quiz",
        correct_option_ids=[question["answer"]],
        explanation=(question.get("explanation") or "راجع المصدر المذكور.")[:200],
        open_period=90,
        hide_results_until_closes=True,
    )
    live_store = context.application.bot_data["live_store"]
    if poll.poll:
        await live_store.register_poll(
            poll.poll.id, chat_id, question["id"], is_anonymous=is_anonymous
        )
    return True


async def _deliver(
    chat_id: int, kind: str, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if kind in ("morning", "evening"):
        return await _send_dhikr(chat_id, kind, context)
    if kind == "quiz":
        return await _send_group_quiz(chat_id, context)
    raise ValueError("Unknown community delivery kind")


async def scheduled_community_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = context.job.data or {}
    chat_id = int(payload.get("chat_id", 0))
    kind = payload.get("kind")
    if not chat_id or kind not in SCHEDULES:
        return
    store = _store(context)
    config = await store.get(chat_id)
    enabled_key = SCHEDULES[kind][0]
    if not config.get(enabled_key):
        return
    try:
        sent = await _deliver(chat_id, kind, context)
        await context.application.bot_data["db"].log_delivery(
            chat_id, f"scheduled_{kind}", "sent" if sent else "skipped"
        )
    except TelegramError as exc:
        logger.warning("Scheduled %s post failed in %s: %s", kind, chat_id, exc)
        await context.application.bot_data["db"].log_delivery(
            chat_id, f"scheduled_{kind}", "failed", type(exc).__name__
        )


async def _refresh_panel(query, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    config = await _store(context).get(chat_id)
    await query.edit_message_text(
        _panel_text(config),
        reply_markup=_panel_keyboard(config),
        disable_web_page_preview=True,
    )


async def community_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user or not query.message:
        return
    chat = query.message.chat
    parts = query.data.split(":")
    if len(parts) >= 4 and parts[1] == "target":
        settings = context.application.bot_data["settings"]
        if query.from_user.id not in settings.admin_ids:
            await query.answer("مركز القناة خاص بمالك البوت.", show_alert=True)
            return
        action = parts[2]
        kind = parts[3] if action in ("toggle", "post") else ""
        try:
            target_id = int(parts[4] if kind else parts[3])
        except (ValueError, IndexError):
            await query.answer("وجهة غير صالحة.", show_alert=True)
            return
        targets = await context.application.bot_data["db"].list_targets()
        target = next(
            (item for item in targets if item["chat_id"] == target_id and item["chat_type"] == "channel"),
            None,
        )
        if not target:
            await query.answer("القناة غير مسجلة. أضفها أولاً عبر /addtarget.", show_alert=True)
            return
        await _store(context).ensure_group(target_id, target["title"])
        if action == "toggle" and kind in SCHEDULES:
            config = await _store(context).toggle(target_id, kind, query.from_user.id)
            await _reschedule_group(context.application, target_id, config)
            await query.answer("تم تحديث إعداد القناة.")
            await query.edit_message_text(
                _channel_panel_text(config),
                reply_markup=_channel_panel_keyboard(config, target_id),
            )
            return
        if action == "post" and kind in SCHEDULES:
            await query.answer("جارٍ النشر...")
            try:
                sent = await _deliver(target_id, kind, context)
                if sent:
                    await context.application.bot_data["db"].log_delivery(
                        target_id, f"manual_{kind}", "sent", telegram_id=query.from_user.id
                    )
                    await query.message.reply_text("✅ نُشر المحتوى في القناة.")
            except TelegramError as exc:
                await query.message.reply_text(
                    f"تعذّر النشر. تحقق من صلاحية البوت للنشر في القناة: {type(exc).__name__}"
                )
            return
        if action == "stats":
            await query.answer()
            stats = await _store(context).group_stats(target_id)
            await query.edit_message_text(
                f"📊 إحصائيات {target['title']}\n━━━━━━━━━━━━━━━━━━\n"
                f"📨 منشورات ناجحة: {stats['sent']}\n"
                f"⚠️ متعثرة: {stats['failed']}\n"
                f"🎯 اختبارات: {stats['polls']}\n"
                f"👥 إجابات مسجلة: {stats['answers']}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ مركز القناة", callback_data=f"community:target:refresh:{target_id}")]
                ]),
            )
            return
        if action == "refresh":
            await query.answer()
            config = await _store(context).get(target_id)
            await query.edit_message_text(
                _channel_panel_text(config),
                reply_markup=_channel_panel_keyboard(config, target_id),
            )
            return
        await query.answer("طلب غير معروف.", show_alert=True)
        return
    if chat.type not in ("group", "supergroup"):
        await query.answer("افتح مركز التحكم من داخل المجموعة.", show_alert=True)
        return
    if not await _is_group_admin(context, chat.id, query.from_user.id):
        await query.answer("هذه الأزرار لمشرفي المجموعة فقط.", show_alert=True)
        return
    await _store(context).ensure_group(chat.id, chat.title or "")
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "open":
        await query.answer()
        await _refresh_panel(query, context, chat.id)
        return
    if action == "toggle" and len(parts) == 3:
        kind = parts[2]
        config = await _store(context).toggle(chat.id, kind, query.from_user.id)
        if kind in SCHEDULES:
            await _reschedule_group(context.application, chat.id, config)
        await query.answer("تم تحديث إعداد المجموعة.")
        await _refresh_panel(query, context, chat.id)
        return
    if action == "post" and len(parts) == 3:
        kind = parts[2]
        await query.answer("جارٍ النشر...")
        try:
            sent = await _deliver(chat.id, kind, context)
            if sent:
                await context.application.bot_data["db"].log_delivery(
                    chat.id, f"manual_{kind}", "sent", telegram_id=query.from_user.id
                )
                await query.message.reply_text("✅ نُشر المحتوى في المجموعة.")
            else:
                await query.message.reply_text("لا يوجد محتوى في هذا القسم حالياً.")
        except TelegramError as exc:
            await query.message.reply_text(f"تعذّر النشر. تحقق من صلاحيات البوت: {type(exc).__name__}")
        return
    if action == "stats":
        await query.answer()
        stats = await _store(context).group_stats(chat.id)
        text = (
            f"📊 إحصائيات {chat.title or 'المجموعة'}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📨 المنشورات الناجحة: {stats['sent']}\n"
            f"⚠️ المنشورات المتعثرة: {stats['failed']}\n"
            f"🎯 الاختبارات المنشورة: {stats['polls']}\n"
            f"👥 الإجابات المسجلة: {stats['answers']}"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ مركز التحكم", callback_data="community:refresh")]
        ]))
        return
    if action == "refresh":
        await query.answer("تم التحديث.")
        await _refresh_panel(query, context, chat.id)
        return
    await query.answer("طلب غير معروف.", show_alert=True)


async def _set_group_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    kind: str,
) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("هذا الأمر يعمل داخل المجموعة فقط.")
        return
    if not await _is_group_admin(context, chat.id, user.id):
        await message.reply_text("تغيير المواعيد متاح لمشرفي المجموعة فقط.")
        return
    if not context.args or not TIME_RE.fullmatch(context.args[0]):
        await message.reply_text(f"الصيغة: /set{kind} HH:MM — بتوقيت الجزائر.")
        return
    config = await _store(context).set_time(chat.id, kind, context.args[0], user.id)
    if config[SCHEDULES[kind][0]]:
        await _reschedule_group(context.application, chat.id, config)
    await message.reply_text(
        f"✅ تم ضبط موعد {SCHEDULES[kind][2]} إلى {context.args[0]} بتوقيت الجزائر."
    )


async def set_morning_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_group_time(update, context, "morning")


async def set_evening_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_group_time(update, context, "evening")


async def set_quiz_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_group_time(update, context, "quiz")


async def channel_control_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    settings = context.application.bot_data["settings"]
    if not message or not user or not chat:
        return
    if chat.type != "private":
        await message.reply_text("أرسل /channeladmin @channel في الخاص لفتح إعدادات القناة بأمان.")
        return
    if user.id not in settings.admin_ids:
        await message.reply_text(
            "إدارة القنوات غير مهيّأة. أرسل /myid ثم أضف رقمك إلى ADMIN_IDS في إعدادات Render أو ملف .env."
            if not settings.admin_ids
            else "مركز القنوات خاص بمالك البوت."
        )
        return
    if not context.args:
        await message.reply_text("الصيغة: /channeladmin @channel")
        return
    try:
        target = await context.bot.get_chat(context.args[0])
        if target.type != "channel":
            await message.reply_text("هذا ليس معرّف قناة.")
            return
        me = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(target.id, me.id)
        if bot_member.status not in ("administrator", "creator") or not getattr(bot_member, "can_post_messages", False):
            await message.reply_text("يجب أن يكون البوت مشرفاً في القناة مع صلاحية نشر الرسائل.")
            return
        registered = await context.application.bot_data["db"].list_targets()
        if not any(item["chat_id"] == target.id and item["chat_type"] == "channel" for item in registered):
            await message.reply_text("سجّل القناة أولاً عبر /addtarget @channel.")
            return
        await _store(context).ensure_group(target.id, target.title or str(target.id))
        config = await _store(context).get(target.id)
        await message.reply_text(
            _channel_panel_text(config),
            reply_markup=_channel_panel_keyboard(config, target.id),
        )
    except TelegramError as exc:
        await message.reply_text(f"تعذر فتح إعدادات القناة. تحقق من المعرّف والصلاحيات: {type(exc).__name__}")


async def set_channel_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    settings = context.application.bot_data["settings"]
    if not message or not user or not chat:
        return
    if chat.type != "private" or user.id not in settings.admin_ids:
        await message.reply_text("هذا الأمر مخصص لمالك البوت في الخاص.")
        return
    if len(context.args) != 3:
        await message.reply_text("الصيغة: /setchanneltime @channel morning|evening|quiz HH:MM")
        return
    channel_ref, kind, value = context.args
    try:
        target = await context.bot.get_chat(channel_ref)
        if target.type != "channel":
            await message.reply_text("هذا ليس معرّف قناة.")
            return
        targets = await context.application.bot_data["db"].list_targets()
        if not any(item["chat_id"] == target.id and item["chat_type"] == "channel" for item in targets):
            await message.reply_text("سجّل القناة أولاً عبر /addtarget @channel.")
            return
        config = await _store(context).set_time(target.id, kind, value, user.id)
        if config[SCHEDULES[kind][0]]:
            await _reschedule_group(context.application, target.id, config)
        await message.reply_text(f"✅ تم ضبط {SCHEDULES[kind][2]} إلى {value} بتوقيت الجزائر.")
    except (TelegramError, ValueError, KeyError) as exc:
        await message.reply_text(f"تعذر ضبط الوقت: {type(exc).__name__}. استخدم HH:MM.")
    return


async def community_stats_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("استخدم /groupstats من داخل المجموعة.")
        return
    if not await _is_group_admin(context, chat.id, user.id):
        await message.reply_text("إحصائيات المجموعة متاحة لمشرفيها فقط.")
        return
    stats = await _store(context).group_stats(chat.id)
    await message.reply_text(
        f"📊 إحصائيات {chat.title or 'المجموعة'}\n━━━━━━━━━━━━━━━━━━\n"
        f"📨 منشورات ناجحة: {stats['sent']}\n"
        f"⚠️ منشورات متعثرة: {stats['failed']}\n"
        f"🎯 اختبارات: {stats['polls']}\n"
        f"👥 إجابات مسجلة: {stats['answers']}"
    )


async def welcome_new_members(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    change = update.chat_member
    if not change:
        return
    old_status = change.old_chat_member.status
    new_member = change.new_chat_member
    if new_member.status not in ("member", "administrator", "creator"):
        return
    if old_status in ("member", "administrator", "creator"):
        return
    chat = change.chat
    if chat.type not in ("group", "supergroup"):
        return
    config = await _store(context).get(chat.id)
    if not config["welcome_enabled"]:
        return
    user = new_member.user
    if user.is_bot:
        return
    await context.application.bot_data["db"].upsert_user(user)
    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"أهلاً بك {user.first_name or 'صديقنا'} في {chat.title or 'المجموعة'} 🌿\n"
            "نسأل الله أن يجعل اجتماعنا على خير. ابدأ رحلتك مع أَثَر عبر /start."
        ),
        disable_web_page_preview=True,
    )


async def restore_community_jobs(application: Application) -> None:
    store: CommunityStore = application.bot_data["community_store"]
    for config in await store.all():
        await _reschedule_group(application, config["chat_id"], config)


def register_community_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("groupadmin", group_control_command))
    application.add_handler(CommandHandler("channeladmin", channel_control_command))
    application.add_handler(CommandHandler("setchanneltime", set_channel_time))
    application.add_handler(CommandHandler("groupstats", community_stats_command))
    application.add_handler(CommandHandler("setmorning", set_morning_time))
    application.add_handler(CommandHandler("setevening", set_evening_time))
    application.add_handler(CommandHandler("setquiz", set_quiz_time))
    application.add_handler(
        CallbackQueryHandler(community_callback, pattern=r"^community:")
    )
    application.add_handler(
        ChatMemberHandler(welcome_new_members, ChatMemberHandler.CHAT_MEMBER)
    )
