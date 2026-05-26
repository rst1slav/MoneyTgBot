from typing import List
from aiogram import Bot
from aiogram.types import OwnedGiftRegular, OwnedGiftUnique

class TelegramScraperService:
    def __init__(self):
        pass

    def _normalize_slug(self, raw_name: str) -> str:
        name = (raw_name or "").strip()
        name = name.replace("Gift: ", "").replace("Подарок: ", "")
        name = name.replace(" #", "-").replace("#", "-").replace(" ", "")
        return name

    async def get_nft_gifts_for_user(self, bot: Bot, user_id: int) -> List[str]:
        """
        Gets the user's unique (NFT) gifts via the Telegram Bot API.
        Returns a list of slugs formatted for Gift Satellite (e.g. "PlushPepe-123").
        """
        gifts_slugs: list[str] = []
        try:
            offset: str | None = None
            while True:
                owned_gifts = await bot.get_user_gifts(user_id=user_id, offset=offset, limit=100)
                if not getattr(owned_gifts, "gifts", None):
                    break

                for owned in owned_gifts.gifts:
                    if not (isinstance(owned, OwnedGiftUnique) or getattr(owned, "type", "") == "unique"):
                        continue
                    gift_info = owned.gift
                    base_name = getattr(gift_info, "base_name", "")
                    number = getattr(gift_info, "number", "")
                    name = getattr(gift_info, "name", "")

                    if name:
                        slug = self._normalize_slug(name)
                    else:
                        slug = self._normalize_slug(f"{base_name}-{number}")

                    gifts_slugs.append(slug)
                offset = getattr(owned_gifts, "next_offset", None)
                if not offset:
                    break
        except Exception:
            return gifts_slugs

        # Preserve order and remove duplicates if same gift appears in pages.
        return list(dict.fromkeys(gifts_slugs))

    async def get_regular_gift_counts_for_user(self, bot: Bot, user_id: int) -> dict[str, int]:
        """
        Gets user's regular (non-upgraded) gifts and returns count by gift.id.
        """
        counts: dict[str, int] = {}
        try:
            offset: str | None = None
            while True:
                owned_gifts = await bot.get_user_gifts(user_id=user_id, offset=offset, limit=100)
                if not getattr(owned_gifts, "gifts", None):
                    break

                for owned in owned_gifts.gifts:
                    if not isinstance(owned, OwnedGiftRegular):
                        continue
                    if getattr(owned, "was_refunded", False):
                        continue
                    gift = getattr(owned, "gift", None)
                    gift_id = str(getattr(gift, "id", "")).strip()
                    if not gift_id:
                        continue
                    counts[gift_id] = counts.get(gift_id, 0) + 1

                offset = getattr(owned_gifts, "next_offset", None)
                if not offset:
                    break
        except Exception:
            return counts
        return counts

    async def get_unique_slug_by_regular_gift_id_for_user(self, bot: Bot, user_id: int) -> dict[str, str]:
        """
        Returns mapping: regular gift_id -> one owned unique slug from this base gift.
        """
        result: dict[str, str] = {}
        try:
            offset: str | None = None
            while True:
                owned_gifts = await bot.get_user_gifts(user_id=user_id, offset=offset, limit=100)
                if not getattr(owned_gifts, "gifts", None):
                    break
                for owned in owned_gifts.gifts:
                    if not (isinstance(owned, OwnedGiftUnique) or getattr(owned, "type", "") == "unique"):
                        continue
                    gift_info = getattr(owned, "gift", None)
                    if not gift_info:
                        continue
                    gift_id = str(getattr(gift_info, "gift_id", "")).strip()
                    if not gift_id or gift_id in result:
                        continue
                    name = getattr(gift_info, "name", "")
                    base_name = getattr(gift_info, "base_name", "")
                    number = getattr(gift_info, "number", "")
                    if name:
                        slug = self._normalize_slug(name)
                    else:
                        slug = self._normalize_slug(f"{base_name}-{number}")
                    if slug:
                        result[gift_id] = slug
                offset = getattr(owned_gifts, "next_offset", None)
                if not offset:
                    break
        except Exception:
            return result
        return result

    def parse_gift_name_to_slug(self, name: str) -> str:
        """
        Fallback parser if needed, though get_nft_gifts_for_user returns slugs directly.
        """
        return self._normalize_slug(name)

