import asyncio
import time as _time
from datetime import datetime
from decimal import Decimal
from typing import Optional


class _RateLimiter:
    """Serializes calls so consecutive requests honor the per-endpoint rate limit."""

    def __init__(self, rate_per_sec: float) -> None:
        self._interval = 1.0 / rate_per_sec
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = _time.monotonic()
            wait = self._last_call + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = _time.monotonic()

import httpx
from aiogram import Bot
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Account, AccountType, BalanceSnapshot, Currency, GiftItem
from app.services.telegram_scraper_service import TelegramScraperService

# Markets that support /search/{market}/{collection} with attribute query params.
# Telegram (`tg`) is included but rate-limited to 1 req/1.5s — handled by best-effort timeout.
_SEARCH_MARKETS = ("portals", "tonnel", "mrkt", "getgems", "tg")

# Backdrop pricing policy (per user). Some backdrops add real value, most don't.
# Names are normalized (lowercase, no spaces) for comparison.
_PRIMARY_BACKDROPS_NORM = {"black", "onyxblack"}        # always factor into pricing
_SECONDARY_BACKDROPS_NORM = {"gunmetal", "cyberpunk"}    # factor in, but cap below
_SECONDARY_PREMIUM_CAP = Decimal("1.30")  # ≤30% over model-only floor

class GiftSatelliteService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.scraper_service = TelegramScraperService()
        # Per-endpoint rate limiters, lazy-initialized on first use (Lock binds to a loop).
        self._market_limiters: dict[str, _RateLimiter] | None = None
        self._history_limiter: _RateLimiter | None = None
        self._gift_limiter: _RateLimiter | None = None

    def _ensure_limiters(self) -> None:
        if self._market_limiters is not None:
            return
        # Per docs: portals/tonnel/mrkt/getgems = 2 req/s. tg = 1 req / 1.5s.
        self._market_limiters = {
            "portals": _RateLimiter(2.0),
            "tonnel": _RateLimiter(2.0),
            "mrkt": _RateLimiter(2.0),
            "getgems": _RateLimiter(2.0),
            "tg": _RateLimiter(1.0 / 1.5),
        }
        self._history_limiter = _RateLimiter(2.0)  # POST /history/{collection}
        self._gift_limiter = _RateLimiter(4.0)     # /gift/by-slug

    async def get_user_gifts_value(self, telegram_id: int) -> Optional[Decimal]:
        """
        Fetches the total value of Telegram gifts.
        We'll try multiple fields from /user/me as fallback if /user/stats fails.
        """
        headers = {"Authorization": f"Token {self.settings.gift_satellite_token}"}
        
        async with httpx.AsyncClient(timeout=15) as client:
            # Try /user/stats first (market value of gifts)
            try:
                resp = await client.get(f"{self.settings.gift_satellite_api_url}/user/stats", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    hold_vol = data.get("holdVolume")
                    if hold_vol is not None:
                        if float(hold_vol) > 0:
                            return Decimal(str(hold_vol))
            except Exception:
                pass

            # Fallback to /user/me if stats is 0 or failed
            try:
                resp = await client.get(f"{self.settings.gift_satellite_api_url}/user/me", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # Check common balance fields
                    for field in ["tonBalance", "volume", "balance"]:
                        val = data.get(field)
                        if val is not None and float(val) > 0:
                            return Decimal(str(val))
            except Exception:
                pass

        return Decimal("0")

    async def get_token_info(self) -> Optional[dict]:
        """
        Returns info about the token owner.
        """
        headers = {"Authorization": f"Token {self.settings.gift_satellite_token}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.settings.gift_satellite_api_url}/user/me", headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None

    async def get_user_purchases(self) -> list[dict]:
        """
        Fetches the list of user purchases (gifts) from Gift Satellite.
        """
        headers = {"Authorization": f"Token {self.settings.gift_satellite_token}"}
        all_purchases = []
        page = 0
        
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                try:
                    resp = await client.get(
                        f"{self.settings.gift_satellite_api_url}/user/purchases",
                        headers=headers,
                        params={"page": page, "hideFilled": "true"} # Only unsold gifts
                    )
                    if resp.status_code != 200:
                        break
                    
                    data = resp.json()
                    purchases = data.get("purchases", [])
                    
                    if not purchases:
                        break
                    
                    all_purchases.extend(purchases)
                    if page >= data.get("totalPages", 1) - 1:
                        break
                    page += 1
                except Exception:
                    break
        return all_purchases

    @staticmethod
    def _safe_decimal(value) -> Decimal:
        if value is None:
            return Decimal("0")
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    @classmethod
    def _pick(cls, item: dict, *names: str) -> Decimal:
        """
        Tries multiple keys, returns first positive Decimal found.
        Also looks inside a nested 'floors' object if present.
        """
        for name in names:
            d = cls._safe_decimal(item.get(name))
            if d > 0:
                return d
        nested = item.get("floors")
        if isinstance(nested, dict):
            for name in names:
                # Try the nested key without common suffix variations.
                short = name.replace("Floor", "").replace("_floor", "")
                for variant in (name, short, cls._norm(short)):
                    d = cls._safe_decimal(nested.get(variant))
                    if d > 0:
                        return d
        return Decimal("0")

    @staticmethod
    def _norm(s: str | None) -> str:
        """Normalize attribute name for comparison: lowercase, strip whitespace."""
        if not s:
            return ""
        return "".join(str(s).lower().split())

    @classmethod
    def _listing_attr(cls, entry: dict, *keys: str) -> str | None:
        """
        Pulls an attribute from a listing entry. Supports:
        - flat fields by any of the given keys (case-insensitive)
        - nested 'attributes' / 'traits' list with {trait_type, value} pairs
        """
        norm_keys = {cls._norm(k) for k in keys}
        for k, v in entry.items():
            if cls._norm(k) in norm_keys and v not in (None, ""):
                return str(v)
        for container_key in ("attributes", "traits"):
            container = entry.get(container_key)
            if isinstance(container, list):
                for attr in container:
                    if not isinstance(attr, dict):
                        continue
                    name = attr.get("trait_type") or attr.get("name") or attr.get("type")
                    if name and cls._norm(name).rstrip("name") in {nk.rstrip("name") for nk in norm_keys}:
                        v = attr.get("value")
                        if v:
                            return str(v)
        return None

    @classmethod
    def _extract_attrs(cls, item: dict) -> tuple[str | None, str | None, str | None]:
        """Returns (collection, model, backdrop). Pattern/symbol intentionally ignored."""
        collection = cls._listing_attr(item, "collectionName", "collection", "collection_name")
        model = cls._listing_attr(item, "modelName", "model", "model_name")
        backdrop = cls._listing_attr(item, "backdropName", "backdrop", "backdrop_name")
        return collection, model, backdrop

    async def _gift_attrs(
        self, client: httpx.AsyncClient, slug: str
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (collection, model, backdrop) for a slug via /gift/by-slug."""
        self._ensure_limiters()
        await self._gift_limiter.acquire()
        resp = await self._get_with_retry(
            client, f"{self.settings.gift_satellite_api_url}/gift/by-slug/{slug}",
        )
        if not resp or resp.status_code != 200:
            return None, None, None
        try:
            data = resp.json()
        except Exception:
            return None, None, None
        return (
            data.get("collectionName"),
            data.get("modelName"),
            data.get("backdropName"),
        )

    async def _get_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict | None = None,
        retries: int = 2,
    ) -> httpx.Response | None:
        """GET with simple backoff retry on 429 / transient errors."""
        headers = {"Authorization": f"Token {self.settings.gift_satellite_token}"}
        delay = 0.6
        for attempt in range(retries + 1):
            try:
                resp = await client.get(url, headers=headers, params=params or None)
                if resp.status_code == 429 and attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return resp
            except Exception:
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None
        return None

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        json_body: dict,
        retries: int = 2,
    ) -> httpx.Response | None:
        headers = {"Authorization": f"Token {self.settings.gift_satellite_token}"}
        delay = 0.6
        for attempt in range(retries + 1):
            try:
                resp = await client.post(url, headers=headers, json=json_body)
                if resp.status_code == 429 and attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return resp
            except Exception:
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None
        return None

    @classmethod
    def _first_positive_price(cls, listings: list) -> Decimal:
        """Find the first listing with a positive price; tolerates leading 0/null prices."""
        for entry in listings:
            if not isinstance(entry, dict):
                continue
            p = cls._safe_decimal(entry.get("normalizedPrice") or entry.get("price"))
            if p > 0:
                return p
        return Decimal("0")

    async def _market_floor(
        self,
        client: httpx.AsyncClient,
        collection: str,
        models: list[str] | None,
        backdrops: list[str] | None,
    ) -> Decimal:
        """
        Lowest active listing price across markets for the given attribute filter.
        Uses server-side filtering via `models=` / `backdrops=` query params.
        Listings come back sorted by price ASC, so the first positive-priced item is
        each market's floor.
        """
        params: dict[str, str] = {}
        if models:
            params["models"] = ",".join(models)
        if backdrops:
            params["backdrops"] = ",".join(backdrops)

        self._ensure_limiters()

        async def fetch_one(market: str) -> Decimal:
            await self._market_limiters[market].acquire()
            resp = await self._get_with_retry(
                client,
                f"{self.settings.gift_satellite_api_url}/search/{market}/{collection}",
                params=params,
            )
            if not resp or resp.status_code != 200:
                return Decimal("0")
            try:
                listings = resp.json()
            except Exception:
                return Decimal("0")
            if not isinstance(listings, list):
                return Decimal("0")
            return self._first_positive_price(listings)

        results = await asyncio.gather(*[fetch_one(m) for m in _SEARCH_MARKETS])
        valid = [r for r in results if r > 0]
        return min(valid) if valid else Decimal("0")

    async def _last_sale_price(
        self,
        client: httpx.AsyncClient,
        collection: str,
        model: str | None,
        backdrop: str | None,
    ) -> Decimal:
        """
        Median of the most recent sales matching attributes via POST /history/{collection}.
        Median is more robust than 'latest' (one-off outlier) or 'lowest' (stale lowballs).
        Pass model=None and backdrop=None to get the collection-wide recent sale median.
        """
        body: dict = {
            "models": [model] if model else [],
            "backdrops": [backdrop] if backdrop else [],
            "sortBy": "date",
            "pageSize": 10,
        }
        self._ensure_limiters()
        await self._history_limiter.acquire()
        resp = await self._post_with_retry(
            client,
            f"{self.settings.gift_satellite_api_url}/history/{collection}",
            json_body=body,
        )
        if not resp or resp.status_code != 200:
            return Decimal("0")
        try:
            payload = resp.json()
        except Exception:
            return Decimal("0")
        content = payload.get("content") if isinstance(payload, dict) else None
        if not content:
            return Decimal("0")
        prices = sorted(
            p for p in (self._safe_decimal(it.get("normalizedPrice")) for it in content)
            if p > 0
        )
        if not prices:
            return Decimal("0")
        return prices[len(prices) // 2]  # median

    async def _resolve_combo_price(
        self,
        client: httpx.AsyncClient,
        collection: str,
        model: str | None,
        backdrop: str | None,
        floor_cache: dict[tuple, Decimal],
    ) -> Decimal:
        """
        Compute price for a (collection, model, backdrop) combination, with caching.

        Backdrop policy (per user):
        - Primary backdrops (Black, Onyx Black): always factor into the price.
        - Secondary backdrops (Gunmetal, Cyberpunk): factor in, but cap the result at
          ≤30% above the model-only floor. Some secondary listings are aggressively
          overpriced; this prevents inflated valuations.
        - Other backdrops: ignore entirely (treat as no backdrop). Many backdrops are
          common/cheap and shouldn't drive pricing.

        Algorithm (pattern/symbol intentionally ignored):
        1. Active floor for exact combo (model + effective backdrop).
        2. Recent-sale median for same combo.
        3. MAX(model-only floor, backdrop-only floor) — both attributes gate the gift's
           value, so it's worth at least the higher of the two single-attribute floors.
        4. Collection floor (active + history).
        """
        cache_key = ("price", collection, model, backdrop)
        if cache_key in floor_cache:
            return floor_cache[cache_key]

        # Backdrop categorization.
        bd_norm = self._norm(backdrop) if backdrop else ""
        is_primary = bd_norm in _PRIMARY_BACKDROPS_NORM
        is_secondary = bd_norm in _SECONDARY_BACKDROPS_NORM
        # "Other" backdrops are dropped from the pricing entirely.
        eff_backdrop: str | None = backdrop if (is_primary or is_secondary) else None

        # 1. Exact combo floor.
        if model and eff_backdrop:
            mb_key = ("mb", collection, model, eff_backdrop)
            if mb_key not in floor_cache:
                floor_cache[mb_key] = await self._market_floor(
                    client, collection, [model], [eff_backdrop]
                )
            if floor_cache[mb_key] > 0:
                capped = await self._cap_value(
                    client, floor_cache[mb_key], is_secondary,
                    collection, model, floor_cache,
                )
                floor_cache[cache_key] = capped
                return capped

        # 2. Recent-sale median for exact combo.
        if model and eff_backdrop:
            past_key = ("past", collection, model, eff_backdrop)
            if past_key not in floor_cache:
                floor_cache[past_key] = await self._last_sale_price(
                    client, collection, model, eff_backdrop
                )
            if floor_cache[past_key] > 0:
                capped = await self._cap_value(
                    client, floor_cache[past_key], is_secondary,
                    collection, model, floor_cache,
                )
                floor_cache[cache_key] = capped
                return capped

        # 3. MAX(model-only, backdrop-only) — independent pricing signals.
        # For "other" backdrops eff_backdrop is None, so b_floor is skipped (model-only).
        m_floor = Decimal("0")
        b_floor = Decimal("0")
        if model:
            m_key = ("m", collection, model)
            if m_key not in floor_cache:
                floor_cache[m_key] = await self._market_floor(
                    client, collection, [model], None
                )
            m_floor = floor_cache[m_key]
        if eff_backdrop:
            b_key = ("b", collection, eff_backdrop)
            if b_key not in floor_cache:
                floor_cache[b_key] = await self._market_floor(
                    client, collection, None, [eff_backdrop]
                )
            b_floor = floor_cache[b_key]

        best_attr = max(m_floor, b_floor)
        if best_attr > 0:
            capped = await self._cap_value(
                client, best_attr, is_secondary, collection, model, floor_cache
            )
            floor_cache[cache_key] = capped
            return capped

        # 4. Active collection floor.
        c_key = ("c", collection)
        if c_key not in floor_cache:
            floor_cache[c_key] = await self._market_floor(client, collection, None, None)
        if floor_cache[c_key] > 0:
            floor_cache[cache_key] = floor_cache[c_key]
            return floor_cache[c_key]

        # 5. Collection-wide recent-sale median — useful for collections with no live
        # listings but historical activity.
        c_hist_key = ("c_hist", collection)
        if c_hist_key not in floor_cache:
            floor_cache[c_hist_key] = await self._last_sale_price(
                client, collection, None, None
            )
        floor_cache[cache_key] = floor_cache[c_hist_key]
        return floor_cache[c_hist_key]

    async def _cap_value(
        self,
        client: httpx.AsyncClient,
        value: Decimal,
        is_secondary: bool,
        collection: str,
        model: str | None,
        floor_cache: dict[tuple, Decimal],
    ) -> Decimal:
        """
        Cap secondary-backdrop prices at ≤30% above the model-only floor.
        Fetches the model-only floor if not yet cached.
        """
        if not is_secondary or value <= 0 or not model:
            return value
        m_key = ("m", collection, model)
        if m_key not in floor_cache:
            floor_cache[m_key] = await self._market_floor(
                client, collection, [model], None
            )
        m_only = floor_cache[m_key]
        if m_only > 0 and value > m_only * _SECONDARY_PREMIUM_CAP:
            return m_only
        return value

    async def get_market_price_by_slug(self, slug: str) -> Decimal:
        """Fetch attributes for a slug, then resolve its price."""
        async with httpx.AsyncClient(timeout=20) as client:
            collection, model, backdrop = await self._gift_attrs(client, slug)
            if not collection:
                return Decimal("0")
            return await self._resolve_combo_price(client, collection, model, backdrop, {})

    async def get_collection_floor_by_slug(self, slug: str) -> Decimal:
        """Returns collection floor (TON) for a gift slug."""
        async with httpx.AsyncClient(timeout=20) as client:
            collection, _, _ = await self._gift_attrs(client, slug)
            if not collection:
                return Decimal("0")
            return await self._market_floor(client, collection, None, None)

    async def scrape_gift_slugs(self, bot: Bot, telegram_id: int) -> list[str]:
        """Just fetch the user's NFT gift slugs from Telegram. No pricing."""
        return await self.scraper_service.get_nft_gifts_for_user(bot, telegram_id)

    async def calculate_external_gifts_value(
        self,
        slugs: list[str],
        *,
        previous_prices: dict[str, Decimal] | None = None,
    ) -> tuple[Decimal, list[tuple[str, Decimal]]]:
        """
        Calculates total value for a list of gift slugs.
        Returns (total_ton, [(slug, price), ...]) sorted by price descending.

        Strategy:
        - Fetch attributes for every unique slug via /gift/by-slug (cheap, 4 req/s).
        - Resolve price per unique (collection, model, backdrop) combo, caching across slugs.
        - If the live algorithm produced 0, fall back to the user's purchase floors
          (stale but better than 0 when a collection has no current liquidity).
        """
        # User's own purchase records contain stale-but-real floor fields per slug.
        purchase_by_slug: dict[str, dict] = {}
        try:
            purchases = await self.get_user_purchases()
            for p in purchases:
                slug = p.get("slug")
                if slug:
                    purchase_by_slug[str(slug)] = p
        except Exception:
            purchase_by_slug = {}

        items: list[tuple[str, Decimal]] = []
        floor_cache: dict[tuple, Decimal] = {}

        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Fetch attributes for all slugs in chunks (rate limit 4 req/s).
            attrs_by_slug: dict[str, tuple[str | None, str | None, str | None]] = {}
            for chunk_start in range(0, len(slugs), 4):
                chunk = slugs[chunk_start : chunk_start + 4]
                results = await asyncio.gather(
                    *[self._gift_attrs(client, s) for s in chunk]
                )
                for slug, attrs in zip(chunk, results):
                    attrs_by_slug[slug] = attrs

            # 2. Resolve price per slug. Combos are cached, so duplicates are free.
            for slug in slugs:
                collection, model, backdrop = attrs_by_slug.get(slug, (None, None, None))
                price = Decimal("0")
                if collection:
                    price = await self._resolve_combo_price(
                        client, collection, model, backdrop, floor_cache
                    )

                # 3. Fallback to user's stale purchase floors when live API has nothing.
                if price == 0:
                    purchase = purchase_by_slug.get(slug)
                    if purchase:
                        for key in (
                            "modelBackdropFloor",
                            "modelFloor",
                            "backdropFloor",
                            "collectionFloor",
                            "price",
                        ):
                            v = self._safe_decimal(purchase.get(key))
                            if v > 0:
                                price = v
                                break

                # 4. Final fallback: previously-known price for this slug. Preserves
                # data when a recalc cycle fails (rate limits, network blips, transient
                # API errors) so the user never sees a regression to 0.
                if price == 0 and previous_prices:
                    prev = previous_prices.get(slug)
                    if prev and prev > 0:
                        price = prev

                items.append((slug, price))

        items.sort(key=lambda pair: pair[1], reverse=True)
        total = sum((p for _, p in items), Decimal("0"))
        return total, items

    async def ensure_gifts_account(self, db: AsyncSession, user_id: int) -> Account:
        account = (
            await db.execute(
                select(Account).where(
                    and_(
                        Account.user_id == user_id,
                        Account.account_type == AccountType.TELEGRAM_GIFTS,
                        Account.is_active.is_(True),
                    )
                )
            )
        ).scalars().first()
        if account:
            return account

        account = Account(
            user_id=user_id,
            account_type=AccountType.TELEGRAM_GIFTS,
            display_name="Telegram Gifts",
            external_ref="telegram_profile",
            is_active=True,
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return account

    async def store_gifts_snapshot(self, db: AsyncSession, account_id: int, user_id: int, balance_ton: Decimal) -> None:
        snapshot = BalanceSnapshot(
            user_id=user_id,
            account_id=account_id,
            balance=balance_ton,
            currency=Currency.TON,
            snapshot_at=datetime.utcnow(),
        )
        db.add(snapshot)
        await db.commit()

    async def load_persisted_items(
        self, db: AsyncSession, user_id: int
    ) -> list[tuple[str, Decimal]]:
        """Reads previously-saved gift items for a user, sorted by price desc."""
        rows = (
            await db.execute(
                select(GiftItem.slug, GiftItem.price_ton)
                .where(GiftItem.user_id == user_id)
                .order_by(GiftItem.price_ton.desc())
            )
        ).all()
        return [(slug, Decimal(price or 0)) for slug, price in rows]

    async def persist_items(
        self,
        db: AsyncSession,
        user_id: int,
        items: list[tuple[str, Decimal]],
    ) -> None:
        """Replaces the user's gift items with the provided list."""
        # Simple replace: delete then insert.
        await db.execute(GiftItem.__table__.delete().where(GiftItem.user_id == user_id))
        now = datetime.utcnow()
        for slug, price in items:
            db.add(
                GiftItem(
                    user_id=user_id,
                    slug=slug,
                    price_ton=Decimal(price or 0),
                    priced_at=now,
                )
            )
        await db.commit()

    async def latest_synced_balance(self, db: AsyncSession, user_id: int) -> Decimal | None:
        stmt = (
            select(BalanceSnapshot.balance)
            .join(Account, Account.id == BalanceSnapshot.account_id)
            .where(
                BalanceSnapshot.user_id == user_id,
                Account.account_type == AccountType.TELEGRAM_GIFTS,
                Account.is_active.is_(True),
            )
            .order_by(BalanceSnapshot.snapshot_at.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if not row:
            return None
        return Decimal(row[0] or 0)

    async def sync_gifts_balance(
        self, db: AsyncSession, bot: Bot, user_id: int, telegram_id: int
    ) -> tuple[Decimal, list[tuple[str, Decimal]]]:
        account = await self.ensure_gifts_account(db, user_id)
        slugs = await self.scraper_service.get_nft_gifts_for_user(bot, telegram_id)
        if not slugs:
            previous = await self.latest_synced_balance(db, user_id)
            if previous is not None:
                return previous, []
            await self.store_gifts_snapshot(db, account.id, user_id, Decimal("0"))
            return Decimal("0"), []

        total_ton, items = await self.calculate_external_gifts_value(slugs)
        fallback = await self.get_user_gifts_value(telegram_id)
        if fallback and fallback > total_ton:
            total_ton = fallback

        await self.store_gifts_snapshot(db, account.id, user_id, total_ton)
        return total_ton, items
