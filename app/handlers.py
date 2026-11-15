from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.config import Settings
from app.content import ContentCatalogue
from app.database import Database


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("☀️ أذكار الصباح", callback_data="cat:morning"),
                InlineKeyboardButton("🌙 أذكار المساء", callback_data="cat:evening"),
            ],
            [
                InlineKeyboardButton("📿 أذكار متنوعة", callback_data="cat:general"),
                InlineKeyboardButton("📜 حديث نبوي", callback_data="cat:hadith"),
            ],
            [
                InlineKeyboardButton("⭐ محفوظاتي", callback_data="bookmarks"),
                InlineKeyboardButton("🌐 افتح المكتبة", callback_data="miniapp"),
            ],
            [InlineKeyboardButton("ℹ️ حول أَثَر", callback_data="about")],
        ]
    )


def register_handlers(application: Application, db: Database, content: ContentCatalogue, settings: Settings) -> None:
    application.bot_data["db"] = db
    application.bot_data["content"] = content
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(bookmarks_callback, pattern=r"^bookmarks$"))
    application.add_handler(CallbackQueryHandler(save_callback, pattern=r"^save:"))
    application.add_handler(CallbackQueryHandler(home_callback, pattern=r"^home$"))
    application.add_handler(CallbackQueryHandler(about_callback, pattern=r"^about$"))
    application.add_handler(CallbackQueryHandler(miniapp_callback, pattern=r"^miniapp$"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.effective_message is None:
        return
    db: Database = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]
    await db.upsert_user(user)

    if settings.required_channel:
        try:
            member = await context.bot.get_chat_member(settings.required_channel, user.id)
            if member.status not in ("member", "administrator", "creator"):
                channel = settings.required_channel.lstrip("@")
                await update.effective_message.reply_text(
                    "للاستفادة من البوت، اشترك أولاً في القناة ثم أرسل /start من جديد.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("الانتقال إلى القناة", url="https://t.me/" + channel)]]
                    ),
                )
                return
        except Exception:
            await update.effective_message.reply_text("تعذر التحقق من الاشتراك حالياً. حاول بعد قليل.")
            return

    await update.effective_message.reply_text(
        "أهلاً بك في أَثَر 🌿\n\nرفيقك للأذكار الصحيحة والمحتوى الموثّق.\nاختر قسماً للبدء:",
        reply_markup=home_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "الأوامر المتاحة:\n/start — القائمة الرئيسية\n/help — المساعدة\n/admin — إحصائيات الإدارة"
        )


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
        await query.edit_message_text("لا توجد عناصر منشورة في هذا القسم حالياً.", reply_markup=home_keyboard())
        return

    keyboard = []
    lines = [f"{category['icon']} {category['title']}", ""]
    for item in items:
        repeat = item.get("repeat")
        label = f"⭐ حفظ: {item.get('title', 'ذكر')}"
        if repeat:
            label += f" · {repeat}"
        keyboard.append([InlineKeyboardButton(label, callback_data="save:" + item["id"])])
        lines.append(item["text"])
        if repeat:
            lines.append(f"التكرار: {repeat}")
        lines.extend(
            [
                f"المصدر: {item['source']} — {item['reference']}",
                f"الحكم: {item.get('grading') or 'راجع المصدر'}",
            ]
        )
        if item.get("virtue"):
            lines.append("الفضل الوارد: " + item["virtue"])
        lines.append("المصدر: " + item["source_url"])
        lines.append("— — —")
    keyboard.append([InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="home")])
    await query.edit_message_text(
        "\n".join(lines)[:4000],
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


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
    await query.answer("أُضيف إلى المحفوظات ⭐" if saved else "أُزيل من المحفوظات", show_alert=True)


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
        await query.edit_message_text("لم تحفظ أي ذكر بعد.", reply_markup=home_keyboard())
        return
    lines = ["⭐ محفوظاتك", ""]
    keyboard = []
    for item in items:
        lines.extend([item["text"], f"{item['source']} — {item['reference']}", "— — —"])
        keyboard.append([InlineKeyboardButton("إزالة: " + item["title"], callback_data="save:" + item["id"])])
    keyboard.append([InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="home")])
    await query.edit_message_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(keyboard))


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("القائمة الرئيسية — اختر ما تحتاجه:", reply_markup=home_keyboard())


async def about_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "أَثَر | ATHAR\n\nمشروع للأذكار والمحتوى الإسلامي الموثّق.\nلا ننشر حديثاً بلا مصدر، ولا نعتمد الشروح الآلية غير المراجعة.",
            reply_markup=home_keyboard(),
        )


async def miniapp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    if not query:
        return
    if not settings.webhook_base_url:
        await query.answer("واجهة المكتبة تُفعّل بعد إعداد رابط الموقع العام.", show_alert=True)
        return
    await query.answer()
    url = settings.webhook_base_url + "/miniapp"
    await query.message.reply_text(
        "افتح مكتبة أَثَر:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("فتح المكتبة", web_app=WebAppInfo(url=url))]]),
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    settings: Settings = context.application.bot_data["settings"]
    if user.id not in settings.admin_ids:
        await message.reply_text("هذا الأمر مخصص للإدارة.")
        return
    db: Database = context.application.bot_data["db"]
    stats = await db.stats()
    await message.reply_text(
        "لوحة الإدارة — إحصائيات\n"
        f"المستخدمون: {stats['users']}\n"
        f"المحفوظات: {stats['bookmarks']}\n"
        f"التذكيرات: {stats['reminders']}\n"
        f"المجموعات: {stats['groups']}"
    )
