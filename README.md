# ATHAR | أَثَر

An Arabic-first Islamic companion for Telegram with source-aware adhkar, personal bookmarks, a Telegram Mini App, private learning quizzes, live group competitions, and native channel quiz polls.

## Features

- Modular Telegram bot and FastAPI service.
- Source-aware adhkar and hadith catalogue. The reading interface shows source names and reference numbers without exposing raw URLs.
- Private learning quiz with personal progress and a general leaderboard.
- **Live group arena:** 1–10 timed rounds, a 25-second answer window per round, one answer per player per round, live participation counts, speed bonuses, answer explanations, and a final podium.
- **Channel quiz publishing:** native Telegram quiz polls with a 90-second voting window. Non-anonymous poll answers are attributed when Telegram supplies the voter's identity, allowing the bot to build a channel-quiz leaderboard.
- Group results are scoped to each group; channel-poll scores have a separate leaderboard.
- Live sessions and answers are stored in SQLite. Active round timers are restored after a normal service restart.
- Admin-only destination management, message publishing, and quiz publishing.
- Telegram Mini App shell, SQLite persistence, and Telegram Mini App initData verification.

## Admin setup

1. DM the bot with /myid and copy the numeric ID.
2. Set ADMIN_IDS to that number in the host's environment (or local .env).
3. Restart the service. Use /admin to open the private administration panel.
4. Add the bot as an administrator in each channel, with permission to post messages.
5. Use /addtarget @channel to register a channel. Groups are registered automatically when the bot is added; /addtarget can also register them explicitly.
6. Use /targets to review destinations and /deltarget CHAT_ID to remove one.
7. To publish a post, send the content to the bot, then send /publish as a reply to that message.
8. Use /channelquiz to publish a native timed quiz poll to registered channels. Use /channelboard to see the channel-poll leaderboard.
9. Use /publishquiz for the existing interactive question broadcast across registered destinations.

## User commands

- /start — main menu
- /quiz — private learning question
- /leaderboard — general quiz leaderboard
- /livegame — start a five-round live competition in a group
- /livegame 3 — start a competition with a chosen number of rounds (1–10)
- /gameboard — leaderboard for the current group, or general leaderboard in private
- /endgame — stop the active group competition (starter or group admins only)
- /channelboard — channel-poll leaderboard
- /myid — show your Telegram numeric ID
- /help — command guide

Group games award 10 points for a correct answer and up to 5 extra speed points. Each player can answer once per round. Scoring is for learning and does not involve money, prizes, or wagering.

## Local setup

Copy .env.example to .env, set TELEGRAM_BOT_TOKEN and ADMIN_IDS, then:

pip install -r requirements.txt
python bot.py

Polling runs when WEBHOOK_BASE_URL is empty. The HTTP service listens on PORT (default 8000) and exposes /healthz and /miniapp.

## Security

Never commit .env. Rotate any Telegram token that has appeared in logs, screenshots, chats, or public repositories. HTTP client request logging is reduced to WARNING because Telegram API request paths contain the bot token.

For a channel destination, the bot must be an administrator with can_post_messages permission. For groups, the bot must be allowed to send and edit its own messages. Publishing is performed only to destinations explicitly registered by an administrator.

## Persistence

SQLite is suitable for local testing. Use durable storage or managed PostgreSQL before public production hosting; ephemeral host filesystems may be reset.
