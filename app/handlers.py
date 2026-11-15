import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes,
)

from app.config import Settings
from app.content import ContentCatalogue
from app.database import Database
from app.games import GameCatalogue

logger = logging.getLogger("athar.handlers")


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☀️ أذكار الصباح", callback_data="cat:morning"),
            InlineKeyboardButton("🌙 أذكار المساء", callback_data="cat:evening"),
        ],
        [
            InlineKeyboardButton("📿 أذكار متنوعة", callback_data="cat:general"),
            InlineKeyboardButton("📜 حديث اليوم", callback_data="cat:hadith"),
        ],
        [
            InlineKeyboardButton("🎮 العب وتعلّم", callback_data="game"),
            InlineKeyboardButton("🏆 لوحة المتصدرين", callback_data="leaderboard"),
        ],
        [
            InlineKeyboardButton("⭐ محفوظاتي", callback_data="bookmarks"),
            InlineKeyboardButton("🌿 مكتبتي", callback_data="miniapp"),
        ],
        [InlineKeyboardButton("📊 نقاطي", callback_data="my_score"),
         InlineKeyboardButton("ℹ️ عن أَثَر", callback_data="about")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 الإحصائيات", callback_data="adm:stats"),
         InlineKeyboardButton("📢 وجهات النشر", callback_data="adm:targets")],
        [InlineKeyboardButton("🎯 نشر سؤال للمجموعات", callback_data="adm:quiz"),
         InlineKeyboardButton("📋 طريقة النشر", callback_data="adm:help")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
    ])


def register_handlers(
    application: Application,
    db: Database,
    content: ContentCatalogue,
    games: GameCatalogue,
    settings: Settings,
) -> None:
    application.bot_data.update(db=db, content=content, games=games, settings=settings)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myid", my_id))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("addtarget", add_target_command))
    application.add_handler(CommandHandler("deltarget", delete_target_command))
    application.add_handler(CommandHandler("targets", targets_command))
    application.add_handler(CommandHandler("publish", publish_command))
    application.add_handler(CommandHandler("publishquiz", publish_quiz_command))
    application.add_handler(ChatMemberHandler(on_bot_membership, ChatMemberHandler.MY_CHAT_MEMBER))

    application.add_handler(CallbackQueryHandler(category_callback, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(bookmarks_callback, pattern=r"^bookmarks$"))
    application.add_handler(CallbackQueryHandler(save_callback, pattern=r"^save:"))
    application.add_handler(CallbackQueryHandler(home_callback, pattern=r"^home$"))
    application.add_handler(CallbackQueryHandler(about_callback, pattern=r"^about$"))
    application.add_handler(CallbackQueryHandler(miniapp_callback, pattern=r"^miniapp$"))
    application.add_handler(CallbackQueryHandler(game_menu_callback, pattern=r"^game$"))
    application.add_handler(CallbackQueryHandler(game_next_callback, pattern=r"^game:next$"))
    application.add_handler(CallbackQueryHandler(quiz_answer_callback, pattern=r"^quiz:"))
    application.add_handler(CallbackQueryHandler(leaderboard_callback, pattern=r"^leaderboard$"))
    application.add_handler(CallbackQueryHandler(my_score_callback, pattern=r"^my_score$"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))
    application.add_error_handler(error_handler)


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    db: Database = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]
    await db.upsert_user(user)

    if settings.required_channel:
        try:
            member = await context.bot.get_chat_member(settings.required_channel, user.id)
            if member.status not in ("member", "administrator", "creator"):
                channel = settings.required_channel.lstrip("@")
                await message.reply_text(
                    "أهلاً بك في أَثَر 🌿\nاشترك في القناة أولاً ثم أرسل /start من جديد.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("الانتقال إلى القناة", url="https://t.me/" + channel)]
                    ]),
                )
                return
        except TelegramError:
            await message.reply_text("تعذر التحقق من الاشتراك الآن. حاول بعد قليل.")
            return

    await message.reply_text(
        f"أهلاً {user.first_name or 'بك'} في أَثَر 🌿\n"
        "مساحتك للذكر والتعلّم والمسابقات النافعة.\n\n"
        "اختر ما تحب من القائمة:",
        reply_markup=home_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "دليل أَثَر\n/start — القائمة الرئيسية\n/quiz — سؤال اليوم\n"
            "/leaderboard — المتصدرون\n/myid — معرفة رقم حسابك\n"
            "/admin — لوحة الإدارة للمصرّح لهم"
        )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_message:
        await update.effective_message.reply_text(f"رقم حسابك في تيليجرام:\n{update.effective_user.id}")


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    category_id = query.data.split(":", 1)[1]
    content: ContentCatalogue = context.application.bot_data["content"]
    category = next((item for item in content.categories if item["id"] == category_id), None)
    items = content.by_category(category_id)
    if not category or not items:
        await query.edit_message_text("هذا القسم قيد الإعداد، وسيُضاف إليه محتوى مراجع قريباً.", reply_markup=home_keyboard())
        return

    lines = [f"{category['icon']} {category['title']}", "━━━━━━━━━━━━━━"]
    rows = []
    for item in items:
        lines.extend(["", item.get("title", "ذكر"), item["text"]])
        if item.get("repeat"):
            lines.append(f"🔁 التكرار: {item['repeat']}")
        lines.append(f"📚 {item['source']} · رقم {item['reference']}")
        if item.get("grading"):
            lines.append(f"درجة الحديث: {item['grading']}")
        if item.get("virtue"):
            lines.append(f"الفضل الوارد: {item['virtue']}")
        rows.append([InlineKeyboardButton("⭐ حفظ هذا المحتوى", callback_data="save:" + item["id"])])
    rows.extend([
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="home")],
    ])
    body = "\n".join(lines)
    if len(body) > 3900:
        body = body[:3850] + "\n\nافتح المكتبة لرؤية بقية المحتوى."
    await query.edit_message_text(body, reply_markup=InlineKeyboardMarkup(rows), disable_web_page_preview=True)


async def save_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    content_id = query.data.split(":", 1)[1]
    db: Database = context.application.bot_data["db"]
    content: ContentCatalogue = context.application.bot_data["content"]
    if content.get(content_id) is None:
        await query.answer("المحتوى غير موجود.", show_alert=True)
        return
    saved = await db.toggle_bookmark(query.from_user.id, content_id)
    await query.answer("أُضيف إلى محفوظاتك ⭐" if saved else "أُزيل من محفوظاتك", show_alert=True)


async def bookmarks_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    db: Database = context.application.bot_data["db"]
    content: ContentCatalogue = context.application.bot_data["content"]
    ids = await db.get_bookmarks(query.from_user.id)
    items = [content.get(item_id) for item_id in ids]
    items = [item for item in items if item is not None]
    if not items:
        await query.edit_message_text("قائمة محفوظاتك فارغة حالياً.", reply_markup=home_keyboard())
        return
    lines = ["⭐ محفوظاتك", "━━━━━━━━━━━━━━"]
    rows = []
    for item in items[:20]:
        lines.extend(["", item.get("title", "ذكر"), item["text"], f"📚 {item['source']} · {item['reference']}"])
        rows.append([InlineKeyboardButton("إزالة: " + item.get("title", "ذكر"), callback_data="save:" + item["id"])])
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    await query.edit_message_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(rows))


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.edit_message_text("القائمة الرئيسية — اختر ما تحتاجه:", reply_markup=home_keyboard())
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise


async def about_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        text = (
            "أَثَر | ATHAR 🌿\n"
            "━━━━━━━━━━━━━━\n"
            "محتوى إسلامي وأذكار ومسابقات نافعة.\n"
            "نحرص على ذكر المراجع، ومراجعة المحتوى قبل نشره."
        )
        try:
            await query.edit_message_text(text, reply_markup=home_keyboard())
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise


async def miniapp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    if not query:
        return
    if not settings.webhook_base_url:
        await query.answer("المكتبة التفاعلية تحتاج رابط الاستضافة العام.", show_alert=True)
        return
    await query.answer()
    url = settings.webhook_base_url + "/miniapp"
    await query.message.reply_text(
        "مكتبة أَثَر 🌿",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("فتح المكتبة", web_app=WebAppInfo(url=url))]]),
    )


async def game_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 ابدأ سؤالاً جديداً", callback_data="game:next")],
        [InlineKeyboardButton("🏆 المتصدرون", callback_data="leaderboard"),
         InlineKeyboardButton("📊 نقاطي", callback_data="my_score")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
    ])
    await query.edit_message_text(
        "🎮 ساحة أَثَر\n━━━━━━━━━━━━━━\n"
        "أسئلة خفيفة في القرآن والسيرة والعبادات.\n"
        "كل حساب يجيب عن السؤال نفسه مرة واحدة في اليوم.",
        reply_markup=keyboard,
    )


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await send_quiz(update.effective_message, context)


async def send_quiz(message, context: ContextTypes.DEFAULT_TYPE, chat_id: int | None = None):
    games: GameCatalogue = context.application.bot_data["games"]
    question = games.random_question()
    rows = []
    for index, option in enumerate(question["options"]):
        rows.append([InlineKeyboardButton(option, callback_data=f"quiz:{question['id']}:{index}")])
    keyboard = InlineKeyboardMarkup(rows)
    target = chat_id if chat_id is not None else message.chat_id
    await context.bot.send_message(
        chat_id=target,
        text="🎯 تحدي أَثَر\n━━━━━━━━━━━━━━\n" + question["question"] + "\n\nاختر الإجابة الصحيحة:",
        reply_markup=keyboard,
    )


async def quiz_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("انتهت صلاحية السؤال.", show_alert=True)
        return
    question = context.application.bot_data["games"].get(parts[1])
    try:
        choice = int(parts[2])
    except ValueError:
        await query.answer("اختيار غير صالح.", show_alert=True)
        return
    if not question or not 0 <= choice < len(question["options"]):
        await query.answer("السؤال غير متاح.", show_alert=True)
        return

    db: Database = context.application.bot_data["db"]
    await db.upsert_user(query.from_user)
    correct = choice == question["answer"]
    result = await db.record_game_answer(query.from_user.id, question["id"], correct)
    if result is None:
        await query.answer("أجبت عن هذا السؤال اليوم من قبل. جرّب سؤالاً آخر!", show_alert=True)
        return
    stats = await db.game_stats(query.from_user.id)
    verdict = "✅ إجابة صحيحة! أحسنت." if correct else "❌ ليست الإجابة الصحيحة."
    explanation = question.get("explanation", "")
    source = question.get("source", "")
    popup = f"{verdict}\n{explanation}\nالمرجع: {source}\nنقاطك: {stats['correct']}"
    await query.answer(popup[:195], show_alert=True)


async def leaderboard_text(db: Database) -> str:
    entries = await db.leaderboard(10)
    if not entries:
        return "🏆 لوحة المتصدرين\nلا توجد نتائج بعد. كن أول المشاركين!"
    lines = ["🏆 لوحة المتصدرين", "━━━━━━━━━━━━━━"]
    medals = ["🥇", "🥈", "🥉"]
    for index, item in enumerate(entries):
        name = item["first_name"] or (("@" + item["username"]) if item["username"] else "مشارك")
        prefix = medals[index] if index < 3 else f"{index + 1}."
        lines.append(f"{prefix} {name} — {item['score']} نقطة")
    return "\n".join(lines)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        db: Database = context.application.bot_data["db"]
        await update.effective_message.reply_text(await leaderboard_text(db))


async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        db: Database = context.application.bot_data["db"]
        await query.edit_message_text(await leaderboard_text(db), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 العب الآن", callback_data="game")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
        ]))


async def my_score_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    db: Database = context.application.bot_data["db"]
    stats = await db.game_stats(query.from_user.id)
    accuracy = round((stats["correct"] / stats["played"]) * 100) if stats["played"] else 0
    await query.edit_message_text(
        f"📊 إحصائياتك\n━━━━━━━━━━━━━━\n"
        f"الأسئلة المجاب عنها: {stats['played']}\n"
        f"الإجابات الصحيحة: {stats['correct']}\n"
        f"نسبة الإجابات الصحيحة: {accuracy}%",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 سؤال جديد", callback_data="game:next")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
        ]),
    )


async def game_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await send_quiz(query.message, context)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("⛔ هذه المنطقة خاصة بإدارة أَثَر.")
        return
    await message.reply_text(
        "🛡️ مركز إدارة أَثَر\n━━━━━━━━━━━━━━\n"
        "إدارة النشر والوجهات ومراجعة النشاط من مكان واحد.",
        reply_markup=admin_keyboard(),
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not is_admin(query.from_user.id, settings):
        await query.answer("غير مصرح لك بالدخول.", show_alert=True)
        return
    await query.answer()
    action = query.data.split(":", 1)[1]
    db: Database = context.application.bot_data["db"]
    if action == "stats":
        stats = await db.stats()
        text = (
            "📈 إحصائيات أَثَر\n━━━━━━━━━━━━━━\n"
            f"المستخدمون: {stats['users']}\n"
            f"المحفوظات: {stats['bookmarks']}\n"
            f"التذكيرات: {stats['reminders']}\n"
            f"وجهات النشر: {stats['targets']}\n"
            f"إجابات المسابقة: {stats['game_answers']}"
        )
    elif action == "targets":
        targets = await db.list_targets()
        text = "📢 وجهات النشر\n━━━━━━━━━━━━━━\n"
        text += "\n".join(f"• {t['title']} ({t['chat_type']})" for t in targets) if targets else "لا توجد وجهات بعد."
        text += "\n\nإضافة: /addtarget @channel\nحذف: /deltarget CHAT_ID"
    elif action == "quiz":
        success, failed = await publish_quiz_to_targets(context)
        text = f"🎯 تقرير نشر السؤال\n✅ وصل إلى: {success}\n⚠️ تعذّر: {failed}"
        await query.edit_message_text(text, reply_markup=admin_keyboard())
        return
    else:
        text = (
            "📋 أوامر الإدارة\n━━━━━━━━━━━━━━\n"
            "/myid — معرفة رقم حسابك\n"
            "/admin — فتح لوحة الإدارة\n"
            "/addtarget @channel — إضافة قناة أو مجموعة بعد منح البوت صلاحية النشر\n"
            "/deltarget CHAT_ID — إزالة وجهة\n"
            "/targets — عرض الوجهات\n"
            "للنشر: أرسل منشوراً للبوت ثم أرسل /publish رداً عليه."
        )
    await query.edit_message_text(text, reply_markup=admin_keyboard())


async def targets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("⛔ هذا الأمر للإدارة فقط.")
        return
    targets = await context.application.bot_data["db"].list_targets()
    if not targets:
        await message.reply_text("لا توجد وجهات نشر. أضف قناة أو مجموعة عبر /addtarget @channel")
        return
    lines = ["📢 وجهات النشر المسجلة:"]
    lines.extend(f"• {t['title']} — {t['chat_id']} ({t['chat_type']})" for t in targets)
    await message.reply_text("\n".join(lines))


async def add_target_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("⛔ هذا الأمر للإدارة فقط.")
        return
    if not context.args:
        await message.reply_text("استخدم: /addtarget @channel أو /addtarget -100123456789")
        return
    try:
        chat = await context.bot.get_chat(context.args[0])
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat.id, me.id)
        if member.status not in ("administrator", "creator"):
            await message.reply_text("يجب ترقية البوت إلى مشرف في القناة أو المجموعة أولاً.")
            return
        if chat.type == "channel" and not getattr(member, "can_post_messages", False):
            await message.reply_text("البوت مشرف، لكنه لا يملك صلاحية نشر الرسائل في هذه القناة.")
            return
        await context.application.bot_data["db"].add_target(chat.id, chat.title or str(chat.id), chat.type)
        await message.reply_text(f"✅ تمت إضافة الوجهة: {chat.title or chat.id}")
    except TelegramError as exc:
        await message.reply_text(f"تعذر إضافة الوجهة. تحقق من المعرّف وصلاحيات البوت.\n{str(exc)[:250]}")


async def delete_target_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("⛔ هذا الأمر للإدارة فقط.")
        return
    if not context.args:
        await message.reply_text("استخدم: /deltarget CHAT_ID")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await message.reply_text("معرّف الوجهة يجب أن يكون رقماً.")
        return
    removed = await context.application.bot_data["db"].remove_target(chat_id)
    await message.reply_text("✅ حُذفت الوجهة." if removed else "لم أجد هذه الوجهة.")


async def publish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("⛔ النشر الجماعي متاح للإدارة فقط.")
        return
    if not message.reply_to_message:
        await message.reply_text("أرسل المحتوى إلى البوت، ثم أرسل /publish رداً على الرسالة التي تريد نشرها.")
        return
    db: Database = context.application.bot_data["db"]
    targets = await db.list_targets()
    if not targets:
        await message.reply_text("لا توجد وجهات نشر. أضف قناة أو مجموعة أولاً عبر /addtarget.")
        return
    success = failed = 0
    for target in targets:
        try:
            await context.bot.copy_message(
                chat_id=target["chat_id"],
                from_chat_id=message.chat_id,
                message_id=message.reply_to_message.message_id,
            )
            success += 1
            await db.log_delivery(target["chat_id"], "admin_broadcast", "sent")
        except TelegramError as exc:
            failed += 1
            await db.log_delivery(target["chat_id"], "admin_broadcast", "failed", type(exc).__name__)
    await message.reply_text(f"📨 انتهى النشر\n✅ نجح: {success}\n⚠️ تعذّر: {failed}")


async def publish_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if not user or not message:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("⛔ هذا الأمر للإدارة فقط.")
        return
    success, failed = await publish_quiz_to_targets(context)
    await message.reply_text(f"🎯 نُشر السؤال\n✅ وصل إلى: {success}\n⚠️ تعذّر: {failed}")


async def publish_quiz_to_targets(context: ContextTypes.DEFAULT_TYPE) -> tuple[int, int]:
    db: Database = context.application.bot_data["db"]
    targets = await db.list_targets()
    success = failed = 0
    for target in targets:
        try:
            await send_quiz(None, context, chat_id=target["chat_id"])
            success += 1
            await db.log_delivery(target["chat_id"], "quiz_publish", "sent")
        except TelegramError as exc:
            failed += 1
            await db.log_delivery(target["chat_id"], "quiz_publish", "failed", type(exc).__name__)
    return success, failed


async def on_bot_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    if not change:
        return
    chat = change.chat
    status = change.new_chat_member.status
    db: Database = context.application.bot_data["db"]
    if chat.type in ("group", "supergroup"):
        if status in ("member", "administrator", "creator"):
            await db.add_target(chat.id, chat.title or str(chat.id), chat.type)
            logger.info("Registered group as a publishing target: %s", chat.id)
        elif status in ("left", "kicked"):
            await db.remove_target(chat.id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if isinstance(error, BadRequest) and "Message is not modified" in str(error):
        return
    logger.error("Unhandled Telegram update error: %s", error, exc_info=error)


async def game_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await send_quiz(query.message, context)


