from aiogram import Bot, Dispatcher

from app.bot.handlers.history import router as history_router
from app.bot.handlers.integrations import router as integrations_router
from app.bot.handlers.profile import router as profile_router
from app.bot.handlers.settings import router as settings_router
from app.bot.handlers.transactions import router as tx_router
from app.config import get_settings


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(tx_router)
    dp.include_router(profile_router)
    dp.include_router(history_router)
    dp.include_router(integrations_router)
    dp.include_router(settings_router)
    return dp


def create_bot() -> Bot:
    return Bot(token=get_settings().bot_token)
