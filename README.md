# ATHAR | أَثَر

A source-first Islamic companion for Telegram: authentic adhkar, hadith references, personal reminders, bookmarks, and a Telegram Mini App.

## Current foundation

- Modular Python application using python-telegram-bot.
- FastAPI health endpoint and Telegram webhook receiver.
- Polling mode for local development; webhook mode for a public host.
- SQLite storage for users, preferences, bookmarks, reminders, groups, and delivery logs.
- Source-aware Arabic adhkar catalogue. Every published item carries a reference and source URL.
- Telegram Mini App shell with a responsive dark/emerald interface.
- Secret configuration through environment variables only.
- Basic automated tests and GitHub Actions.

## Safety and content policy

ATHAR must never invent a Qur'an verse, hadith, chain, grading, virtue, or scholarly ruling. New religious content must be checked against a named primary/reference source before publication. AI may assist with search and organization, but generated religious text is not published automatically.

The first seed entries reference Sunan Abi Dawud 5068, Sahih al-Bukhari 6405, and Sahih Muslim 1884. The database stores source details separately from explanatory text. Explanations remain empty until reviewed and approved.

## Requirements

- Python 3.12+
- A Telegram bot token created with BotFather
- Optional numeric Telegram admin IDs
- Optional public service URL for webhook mode

## Local setup

1. Copy .env.example to .env.
2. Set TELEGRAM_BOT_TOKEN in .env. Never commit .env.
3. Install dependencies with pip install -r requirements.txt.
4. Run python bot.py.
5. Open the bot in Telegram and send /start.

When WEBHOOK_BASE_URL is empty, the application uses long polling. The FastAPI server still serves the Mini App and health endpoint on port 8000.

## Hosting

The application listens on the PORT environment variable (default 8000). Set TELEGRAM_BOT_TOKEN as a secret environment variable. To use webhooks, also set WEBHOOK_BASE_URL to the public HTTPS service URL and WEBHOOK_SECRET to a random secret of 32+ characters.

The default database is local SQLite. Free ephemeral hosting can erase local files during restarts or redeploys. Before a public launch, attach durable storage or migrate the database layer to managed PostgreSQL. Do not treat an ephemeral SQLite file as production persistence.

## Useful endpoints

- GET /healthz — process and database health.
- GET /miniapp — Telegram Mini App.
- GET /api/adhkar — requires valid Telegram Mini App initData authorization.

## Project layout

- app/config.py — typed environment configuration.
- app/database.py — SQLite persistence.
- app/content.py — source-aware content catalogue.
- app/handlers.py — Telegram commands and callback routes.
- app/security.py — Telegram Mini App initData verification.
- app/server.py — FastAPI lifecycle, webhook, API and static app.
- miniapp/ — responsive Telegram Mini App.
- data/adhkar.json — reviewed seed content.

## Development checks

Run python -m unittest discover -s tests -v and python -m compileall -q app bot.py.

## Important

The existing main branch is retained. Development for this foundation happens on athar-v2-foundation. Revoke any Telegram token that has ever been committed to a public repository, then issue a new token and put it only in the host's secret environment settings.
