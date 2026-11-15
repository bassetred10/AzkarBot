# ATHAR | أَثَر

An Arabic-first Islamic companion for Telegram: source-aware adhkar, interactive learning games, live group competitions, per-group control panels, channel publishing, and scheduled content.

## Current feature set

### Islamic learning and games

- 100 question records across seven topics: Qur'an, Prophetic biography, Hadith, Prophets, worship, Companions, and manners.
- Questions include an explanation and a source reference.
- Private multiple-choice questions by topic.
- True/False quiz polls with real Telegram quiz voting and stored answer keys.
- Live group championships with 1–10 rounds, one answer per player per round, a visual timer, participant counts, speed points, explanations, and a final leaderboard.
- Rapid-fire championships use 12 seconds per round; normal championships use 25 seconds.
- Group leaderboards combine live championship points and named quiz-poll scores. Channel poll totals remain anonymous and are shown as aggregate statistics.
- Sessions and scores are persisted in SQLite.

### Group control center

Group administrators can use /groupadmin inside their own group to:
- Enable or disable morning adhkar, evening adhkar, daily quiz, and new-member welcome messages.
- Publish a dhikr or quiz immediately using inline controls.
- Set separate posting times using /setmorning, /setevening, and /setquiz.
- Review group-only publishing statistics.
- Use /groupstats to view delivery totals.

Schedules are disabled by default, independently stored for each group, and use Africa/Algiers time by default. Schedules are restored after a normal service restart and paused when the bot is removed from a group.

### Channel control center

The bot owner can use /channeladmin @channel in private after registering the channel. The bot must be an administrator with permission to post messages.

Each channel has its own:
- Morning adhkar schedule.
- Evening adhkar schedule.
- Daily quiz schedule.
- Immediate publish buttons and delivery statistics.

Use /setchanneltime @channel morning 07:00 to change a channel schedule. Channel quizzes use anonymous Telegram polls; the bot reports aggregate vote and correct-answer counts without identifying voters.

### Owner administration

- /admin opens the owner dashboard.
- /addtarget @channel registers a channel or /addtarget -100... registers a chat.
- /targets and /deltarget manage registered destinations.
- /publish copies a replied-to message to registered destinations.
- /publishlivequiz publishes native quiz polls to registered groups and channels.
- /channelquiz publishes a quiz poll to registered channels.
- Owner statistics include active games, quiz activity, enabled schedules, and delivery health over the last seven days.

The owner is configured through ADMIN_IDS. Group administrators do not receive global owner access.

## Commands

### Everyday and game commands

- /start — main menu
- /help — full command guide
- /quiz — private multiple-choice question
- /truefalse — True/False quiz poll; optionally add a topic
- /livegame — five-round group championship
- /livegame seerah 8 — eight rounds from the Prophetic biography topic
- /speedgame — five rapid-fire rounds, 12 seconds each
- /speedgame hadith 7 — seven rapid-fire Hadith rounds
- /qurangame, /sirahgame, /hadithgame
- /prophetsgame, /worshipgame, /companionsgame, /mannersgame
- /gameboard — current group's leaderboard
- /liveboard — overall named-player leaderboard
- /endgame — stop the active group championship
- /channelboard — anonymous channel-poll statistics
- /myid — show your Telegram numeric ID

### Group administrator commands

Use inside the group:
- /groupadmin — group control center
- /groupstats — group publishing statistics
- /setmorning 07:00
- /setevening 18:00
- /setquiz 20:00

### Owner-only commands

Use in private unless the command targets a registered destination:
- /admin — owner dashboard
- /addtarget @channel
- /targets
- /deltarget CHAT_ID
- /channeladmin @channel — open a channel control panel
- /setchanneltime @channel morning 07:00
- /channelquiz — publish to registered channels
- /publishlivequiz — publish to all registered destinations
- /publish — copy a replied-to message to all registered destinations

## Owner setup

1. Send /myid to the bot in private.
2. Add your numeric Telegram ID to ADMIN_IDS in the Render environment or local .env file.
3. Restart the service.
4. Use /admin. Group administrators can separately use /groupadmin in their own group.
5. Add the bot as an administrator in each channel and grant permission to post.
6. Register the channel using /addtarget @channel, then open its panel with /channeladmin @channel.

If ADMIN_IDS is empty, global publishing and owner controls stay locked. The bot does not automatically grant owner privileges to whoever asks.

## Local setup

Copy .env.example to .env and set TELEGRAM_BOT_TOKEN and ADMIN_IDS. Then:

```bash
pip install -r requirements.txt
python -m compileall -q app bot.py
python -m unittest discover -s tests -v
python bot.py
```

Polling requests all Telegram update types so that group membership events and poll-result updates can be processed. When WEBHOOK_BASE_URL is set, the application uses the configured webhook.

The FastAPI service exposes /healthz, /miniapp, and the authenticated /api/adhkar endpoint.

## Persistence and production notes

SQLite is suitable for local testing and a single service instance. Use durable storage or managed PostgreSQL before production hosting; ephemeral filesystems may reset between deployments. Run a single polling instance for a given bot token to avoid competing getUpdates consumers.

## Security

Never commit .env. Keep Telegram tokens private and out of logs. Channel and group publishing only targets destinations explicitly registered by the owner. Group settings are scoped to that group; channel schedules are controlled by the owner.
