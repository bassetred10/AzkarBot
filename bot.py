import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ChatMemberHandler, ContextTypes

# إعدادات التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# الإعدادات المدمجة
TOKEN = "8849395576:AAFoHh3Y4vyf3K8lqs50Qxrm4flXbk1gUT0"
OWNER_ID = 6799059686
CHANNEL_USERNAME = "@uec_u"
CHANNEL_ID = -1004304853750

DATA_FILE = "groups.json"

# الأذكار والبيانات
AZKAR = {
    "صباح": ["اللهم بك أصبحنا وبك أمسينا", "سبحان الله وبحمده", "اللهم إني أسألك العافية"],
    "مساء": ["اللهم بك أمسينا وبك أصبحنا", "بسم الله الذي لا يضر مع اسمه شيء", "أعوذ بكلمات الله التامات"],
    "نوم": ["باسمك اللهم أموت وأحيا", "سبحان الله (33) والحمد لله (33) والله أكبر (34)"],
    "معوذات": ["سورة الإخلاص", "سورة الفلق", "سورة الناس", "آية الكرسي"],
    "متنوع": ["لا إله إلا الله وحده لا شريك له", "أستغفر الله العظيم", "اللهم صل على محمد"]
}

GROUP_AZKAR = [
    "﴿اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ﴾",
    "سبحان الله وبحمده، سبحان الله العظيم",
    "لا إله إلا الله وحده لا شريك له، له الملك وله الحمد",
    "اللهم صل وسلم على نبينا محمد",
    "أستغفر الله العظيم وأتوب إليه"
]

group_index = 0

def load_groups():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_groups(groups):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

async def is_subscribed(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def azkar_menu():
    keyboard = [
        [InlineKeyboardButton("🌅 أذكار الصباح", callback_data="zk_sabah")],
        [InlineKeyboardButton("🌇 أذكار المساء", callback_data="zk_masaa")],
        [InlineKeyboardButton("🌙 أذكار قبل النوم", callback_data="zk_nawm")],
        [InlineKeyboardButton("📿 المعوذتين والآيات", callback_data="zk_moaw")],
        [InlineKeyboardButton("🕌 أذكار متنوعة", callback_data="zk_mtnw")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private":
        return

    sub = await is_subscribed(context.bot, user.id)
    if not sub:
        await update.message.reply_text(f"يرجى الاشتراك في القناة أولاً: {CHANNEL_USERNAME}")
        return

    await update.message.reply_text("أهلاً بك في بوت الأذكار، اختر ذكراً:", reply_markup=azkar_menu())

async def show_azkar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.replace("zk_", "")
    
    category_map = {"sabah": "صباح", "masaa": "مساء", "nawm": "نوم", "moaw": "معوذات", "mtnw": "متنوع"}
    items = AZKAR.get(category_map.get(data), [])
    
    text = "\n\n".join(items)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back")]]))

async def on_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.my_chat_member.chat
    if chat.type in ("group", "supergroup"):
        groups = load_groups()
        if chat.id not in groups:
            groups.append(chat.id)
            save_groups(groups)
            await context.bot.send_message(chat.id, "تم تفعيل بوت الأذكار! سنرسل ذكراً كل ساعتين.")

async def group_scheduler(bot: Bot):
    global group_index
    while True:
        await asyncio.sleep(7200) # ساعتين
        groups = load_groups()
        if groups:
            zikr = GROUP_AZKAR[group_index % len(GROUP_AZKAR)]
            group_index += 1
            for gid in groups:
                try: await bot.send_message(gid, f"🕌 ذكر الصدقة الجارية:\n\n{zikr}")
                except: pass

async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(show_azkar, pattern="^zk_"))
    app.add_handler(ChatMemberHandler(on_bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
    
    asyncio.create_task(group_scheduler(app.bot))
    print("🚀 البوت يعمل الآن...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
