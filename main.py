"""Entry point. Default: Telegram bot + scheduler. --once: build one digest now and exit
(delivered per DELIVERY_CHANNEL — e.g. straight into your Telegram inbox)."""
from __future__ import annotations
import argparse, asyncio, logging
from datetime import datetime, timezone as _tz
from openai import AsyncOpenAI
from config import AppConfig, Owner
from connectors.gitlab import GitLabAdapter
from connectors.jira import JiraAdapter
from delivery.base import BothDelivery
from delivery.html import HtmlDelivery
from delivery.telegram import TelegramDelivery
from scheduler import AgentScheduler, DigestService
logging.basicConfig(level=logging.INFO)
CFG = AppConfig()


def _llm() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=CFG.llm_base_url or None, api_key=CFG.llm_api_key or "not-needed")


def _make_service(make_delivery) -> DigestService:
    return DigestService(llm=_llm(), model=CFG.llm_model,
        gitlab_base=CFG.gitlab_base_url, jira_base=CFG.jira_base_url, report_dir=CFG.report_dir,
        make_gitlab=lambda url, tok: GitLabAdapter(url, tok, window_days=CFG.fetch_window_days),
        make_jira=lambda url, tok: JiraAdapter(url, tok, window_days=CFG.fetch_window_days),
        make_delivery=make_delivery, stale_after=CFG.stale_after_days)


def _delivery_for(owner: Owner, bot, *, open_browser: bool):
    """Build the DeliveryPort for `owner` per its delivery_channel."""
    html = HtmlDelivery(CFG.report_dir, owner.chat_id or "local", open_browser=open_browser)
    if owner.delivery_channel == "html":
        return html
    tg = TelegramDelivery(bot, owner.chat_id)
    if owner.delivery_channel == "both":
        return BothDelivery(tg, html)
    return tg


async def run_once() -> None:
    owner = CFG.owner()
    needs_tg = owner.delivery_channel in ("html", "telegram", "both")
    bot = None
    if needs_tg:
        from telegram import Bot
        bot = Bot(CFG.telegram_bot_token)
    service = _make_service(lambda o: _delivery_for(o, bot, open_browser=True))
    now = datetime.now(_tz.utc)
    if bot is not None:
        async with bot:
            await service.run_for(owner, now=now)
    else:
        await service.run_for(owner, now=now)


def run_bot() -> None:
    owner = CFG.owner()
    bot_holder: dict = {}
    service = _make_service(lambda o: _delivery_for(o, bot_holder["bot"], open_browser=False))
    scheduler = AgentScheduler(owner=owner, service=service, timezone=CFG.timezone)

    from tgbot.bot import build_application
    app = build_application(token=CFG.telegram_bot_token, owner=owner,
                            scheduler=scheduler, service=service)
    bot_holder["bot"] = app.bot

    async def _post_init(application):
        scheduler.schedule_owner(); scheduler.start()
    app.post_init = _post_init
    app.run_polling()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="build one digest now, deliver per DELIVERY_CHANNEL, then exit")
    args = parser.parse_args()
    if args.once:
        asyncio.run(run_once())
    else:
        run_bot()
