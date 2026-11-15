# ATHAR | أَثَر

An Arabic-first Islamic companion for Telegram with a polished bot menu, source-aware adhkar, learning games, leaderboards, personal bookmarks, and controlled channel/group publishing.

## Features in this foundation

- Modular Telegram bot and FastAPI service.
- Source-aware adhkar and hadith catalogue. The UI displays source names and reference numbers without exposing raw URLs in the reading flow.
- Interactive Islamic knowledge quiz with one answer per user/question/day, personal scores, and leaderboard.
- Automatic registration of groups when the bot is added.
- Admin-only management panel, destination registration/removal, message broadcast, and quiz publishing.
- Channel publishing requires the bot to be an administrator with posting permission.
- Telegram Mini App shell.
- SQLite storage, secret environment configuration, GitHub Actions checks, and Telegram Mini App initData verification.

## Admin setup

1. DM the bot with /myid and copy the numeric ID.
2. Set ADMIN_IDS to that number in the host's environment (or local .env).
3. Restart the service. Use /admin to open the private administration panel.
4. Add the bot as an administrator in a channel or group. For channels, allow posting messages.
5. Use /addtarget @channel to register a channel. Groups are registered automatically when the bot is added; /addtarget can also register them explicitly.
6. Use /targets to review destinations and /deltarget CHAT_ID to remove one.
7. To publish a post, send the content to the bot in a private chat, reply to that message with /publish. The bot copies it to registered destinations.
8. Use /publishquiz to send an interactive question to all destinations.

## User commands

- /start — main menu
- /quiz — a learning question
- /leaderboard — quiz leaders
- /myid — your Telegram numeric ID
- /help — command guide

Quiz scoring is for learning and does not involve money, prizes, or wagering. Each user can answer a given question once per calendar day.

## Local setup

Copy .env.example to .env, set TELEGRAM_BOT_TOKEN and ADMIN_IDS, then:

pip install -r requirements.txt
python bot.py

Polling runs when WEBHOOK_BASE_URL is empty. The HTTP service listens on PORT (default 8000) and exposes /healthz and /miniapp.

## Security

Never commit .env. Rotate any Telegram token that has appeared in logs, screenshots, chats, or public repositories. HTTP client request logging is reduced to WARNING because Telegram API request paths contain the bot token.

For a channel destination, the bot must be an administrator with can_post_messages permission. For groups, the bot must be allowed to send messages. Publishing is performed only to destinations explicitly registered by an administrator.

## Persistence

SQLite is suitable for local testing. Use durable storage or managed PostgreSQL before public production hosting; ephemeral host filesystems may be reset.
