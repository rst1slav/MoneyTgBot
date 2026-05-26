# Money Telegram Bot

Multi-user Telegram bot for personal finance tracking with:
- manual transactions (UAH/USD),
- Monobank sync,
- TON wallet sync,
- history filters,
- daily profile chart generation.

## Quick start

1. Copy `.env.example` to `.env` and fill values.
2. Install dependencies:
   - `pip install -e .[dev]`
3. Run bot (local SQLite DB is used by default):
   - `python -m app.main`
4. Optional API:
   - `uvicorn app.api:app --reload --port 8000`

## Local defaults (no extra keys)

- `DATABASE_URL` defaults to local SQLite file: `money_bot.db`
- `ENCRYPTION_KEY` is optional; if empty, app auto-creates `.secrets/fernet.key`
- Required manually only: `BOT_TOKEN`

## Required keys and tokens

- `BOT_TOKEN`: from `@BotFather`
- `ENCRYPTION_KEY`: optional override (auto-generated if empty)
- `MONOBANK` token: user-specific token used with `/link_mono <token> <card_id>`
- `TON` wallet address: used with `/link_ton <wallet_address>`
- FX API key is optional if you use a free endpoint compatible with `fx_api_url`

## Project layout

- `app/main.py` - starts bot polling and scheduler.
- `app/api.py` - FastAPI health/service API.
- `app/db/` - models, session and database access.
- `app/services/` - integrations and domain services.
- `app/bot/handlers/` - Telegram command handlers.
- `app/workers/scheduler.py` - periodic background jobs.
