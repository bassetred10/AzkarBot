import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from telegram import Update
from telegram.ext import Application

from app.config import ROOT_DIR, get_settings
from app.content import ContentCatalogue
from app.database import Database
from app.games import GameCatalogue
from app.handlers import register_handlers
from app.security import validate_telegram_init_data


settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# HTTP client URLs contain the bot API path; never write them to logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("athar")


@asynccontextmanager
async def lifespan(api: FastAPI):
    db = Database(settings.database_path)
    await db.initialize()
    content = ContentCatalogue()
    content.load()
    games = GameCatalogue()
    games.load()

    telegram_app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .connect_timeout(30)
        .read_timeout(45)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )
    register_handlers(telegram_app, db, content, games, settings)
    await telegram_app.initialize()
    await telegram_app.start()

    polling = not bool(settings.webhook_base_url)
    if polling:
        await telegram_app.updater.start_polling(drop_pending_updates=False)
        logger.info("ATHAR Telegram bot started in polling mode")
    else:
        await telegram_app.bot.set_webhook(
            url=settings.webhook_base_url + "/telegram/webhook",
            secret_token=settings.webhook_secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
        logger.info("ATHAR Telegram webhook configured")

    api.state.db = db
    api.state.content = content
    api.state.games = games
    api.state.telegram_app = telegram_app
    api.state.polling = polling
    yield

    if polling and telegram_app.updater and telegram_app.updater.running:
        await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()
    await db.close()


api = FastAPI(title="ATHAR API", version="0.2.0", lifespan=lifespan)


@api.get("/healthz")
async def healthz(request: Request):
    db = getattr(request.app.state, "db", None)
    if db is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    return {"status": "ok", "service": "athar", "database": "connected"}


@api.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not settings.webhook_base_url:
        raise HTTPException(status_code=404, detail="Webhook mode is disabled")
    if not x_telegram_bot_api_secret_token or x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    telegram_app: Application = request.app.state.telegram_app
    try:
        payload = await request.json()
        update = Update.de_json(payload, telegram_app.bot)
        await telegram_app.process_update(update)
    except Exception:
        logger.exception("Failed to process Telegram update")
        raise HTTPException(status_code=400, detail="Invalid update")
    return {"ok": True}


@api.get("/api/adhkar")
async def list_adhkar(request: Request, authorization: str | None = Header(default=None)):
    token = authorization[4:] if authorization and authorization.startswith("tma ") else ""
    try:
        validate_telegram_init_data(token, settings.telegram_bot_token)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Open this app from Telegram")
    return request.app.state.content.public_payload()


@api.get("/")
async def root():
    return {"service": "ATHAR", "message": "Islamic companion API is running", "health": "/healthz", "miniapp": "/miniapp"}


@api.get("/miniapp")
async def miniapp():
    return FileResponse(Path(ROOT_DIR) / "miniapp" / "index.html")


@api.get("/miniapp/{asset_path:path}")
async def miniapp_assets(asset_path: str):
    base = (Path(ROOT_DIR) / "miniapp").resolve()
    candidate = (base / asset_path).resolve()
    if base not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(candidate)


def run() -> None:
    import os
    import uvicorn
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("app.server:api", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
