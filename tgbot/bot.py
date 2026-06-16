"""Telegram command handlers (single-user personal agent)."""
from __future__ import annotations
from datetime import datetime, timezone as _tz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import Owner


def build_application(*, token: str, owner: Owner, scheduler, service) -> Application:
    app = Application.builder().token(token).build()

    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Chào! Đây là digest agent cá nhân.\n"
            "Cấu hình token/giờ/kênh trong file .env.\n"
            "/digest (xem ngay) · /pause /resume")

    async def digest(update: Update, _: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Đang tạo digest...")
        await service.run_for(owner, now=datetime.now(_tz.utc))

    async def pause(update: Update, _: ContextTypes.DEFAULT_TYPE):
        scheduler.pause()
        await update.message.reply_text("Đã tạm dừng. /resume để bật lại.")

    async def resume(update: Update, _: ContextTypes.DEFAULT_TYPE):
        scheduler.resume()
        await update.message.reply_text("Đã bật lại.")

    for name, fn in [("start", start), ("digest", digest), ("pause", pause), ("resume", resume)]:
        app.add_handler(CommandHandler(name, fn))
    return app
