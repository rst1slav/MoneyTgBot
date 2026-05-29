import asyncio
import contextlib
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

from app.bot.main import create_bot, create_dispatcher
from app.db.base import Base
from app.db.session import engine
from app.workers.scheduler import create_scheduler

# Initialise logging once at process start. Without this, root logger stays
# at WARNING and all log.info(...) calls (including wallet-derive diagnostics)
# silently disappear from journalctl.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Silence the noisiest libs so wallet diagnostics aren't drowned out.
for noisy in ("httpx", "httpcore", "aiogram.event", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


# Lightweight in-place migrations for additive changes (new columns) so that
# existing SQLite databases stay compatible without a full Alembic migration.
# Each entry: (table_name, column_name, sql_type_with_default).
_PENDING_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("users", "language", "VARCHAR(8) DEFAULT 'ru'"),
]


async def _ensure_columns(conn: AsyncConnection) -> None:
    for table, column, ddl in _PENDING_COLUMN_MIGRATIONS:
        existing = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        cols = {row[1] for row in existing}
        if column in cols:
            continue
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        except Exception:
            pass


async def _run_one_time_refresh(conn: AsyncConnection) -> None:
    """
    One-time chart-cache wipe + transaction recategorization.
    Idempotent: marker file & data-driven check make repeat runs no-ops.
    """
    import glob
    import os
    import re
    from pathlib import Path

    reports_dir = Path("reports")
    marker = reports_dir / ".style-v2-applied"

    # 1. Force regen of all cached chart images once.
    if reports_dir.exists() and not marker.exists():
        for pattern in ("pie_*.png", "user_*.png", "fx_*.png"):
            for f in glob.glob(str(reports_dir / pattern)):
                try:
                    os.remove(f)
                except Exception:
                    pass
        try:
            marker.touch()
        except Exception:
            pass

    # 2. Recategorize legacy transactions whose `category` is a raw MCC code or
    # one of the old generic placeholders ("card", "monobank", "").
    # Idempotent — once converted, the SELECT returns 0 rows.
    try:
        from app.services.monobank_service import mcc_to_category
    except Exception:
        return

    legacy = (await conn.execute(text(
        "SELECT id, tx_type, category, description FROM transactions "
        "WHERE category = '' OR category = 'card' OR category = 'monobank' "
        "   OR category GLOB '[0-9]*'"
    ))).fetchall()

    mcc_re = re.compile(r"^\d+$")
    for row in legacy:
        cat = row.category or ""
        is_income = row.tx_type == "income"
        if mcc_re.match(cat):
            try:
                new_cat = mcc_to_category(int(cat), is_income=is_income, fallback_desc=row.description)
            except Exception:
                continue
        else:
            new_cat = mcc_to_category(None, is_income=is_income, fallback_desc=row.description)
        try:
            await conn.execute(
                text("UPDATE transactions SET category = :c WHERE id = :i"),
                {"c": new_cat, "i": row.id},
            )
        except Exception:
            pass


async def init_db(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_columns(conn)
        await _run_one_time_refresh(conn)


async def run_bot() -> None:
    await init_db(engine)
    bot = create_bot()
    dispatcher = create_dispatcher()
    scheduler = create_scheduler()
    scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        with contextlib.suppress(Exception):
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
