from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./money_bot.db"
    base_currency: str = "UAH"
    monobank_api_url: str = "https://api.monobank.ua"
    ton_api_url: str = "https://tonapi.io/v2"
    fx_api_url: str = "https://api.exchangerate.host"
    gift_satellite_api_url: str = "https://gift-satellite.dev/api"
    gift_satellite_token: str = "d0fe37ef5ef2f13a85936e54aaf3a16153d1df97b01d3aefc4af241011245b7c"
    encryption_key: str = ""
    # Telegram channel/group where the bot uploads charts to obtain a file_id for
    # inline-mode photo results. Bot must be admin (or member with post rights) here.
    # Channel internal id 3903907255 → API form is -1003903907255.
    inline_storage_chat_id: int = -1003903907255

    # Public base URL for assets served by app.api (e.g. notification cards).
    # When set (https://your-domain or https://abcd.ngrok.app), the bot sends
    # photo URLs to Telegram and Telegram fetches them. When empty, the bot falls
    # back to rendering cards locally and shipping them as files (Telegram still
    # caches them via file_id under the hood, so the UX is identical).
    public_base_url: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
