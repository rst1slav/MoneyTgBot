from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import matplotlib.pyplot as plt
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    AccountType,
    GeneratedReport,
    ProfileSnapshot,
    Transaction,
    TransactionType,
)

PIE_CACHE_HOURS = 24

# Outer "card frame" colors / dimensions for the blue-on-white chart style.
_CARD_BG = (78, 163, 232)   # #4ea3e8
_CARD_PADDING = 70          # blue padding around the white card, in pixels
_CARD_RADIUS = 45           # rounded corner radius


def _render_pie_to_card(
    out_path,
    *,
    labels: list[str],
    values: list[float],
    title: str,
    colors: list[str] | None = None,
) -> None:
    """Renders a pie chart on a white canvas, then composites onto the blue card."""
    from io import BytesIO

    palette = colors or [
        "#4ea3e8", "#2e7d32", "#c62828", "#6f42c1", "#00897b",
        "#ef6c00", "#5e35b1", "#0277bd", "#7cb342", "#e64a19",
    ]
    pie_colors = [palette[i % len(palette)] for i in range(len(values))]

    fig, ax = plt.subplots(figsize=(8, 5.4), facecolor="white", dpi=140)
    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        colors=pie_colors,
        wedgeprops={"edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 10, "color": "#222"},
        pctdistance=0.78,
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontweight("bold")
        at.set_fontsize(9)
    ax.axis("equal")
    ax.set_title(title, fontsize=15, fontweight="bold", color="#222", pad=12)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", facecolor="white", dpi=140)
    plt.close(fig)
    buf.seek(0)
    _composite_blue_card(buf, out_path)


def _composite_blue_card(white_card_buf, out_path) -> None:
    """
    Wraps a matplotlib PNG (white background) into a blue-padded card with rounded
    corners, then writes the result to `out_path`. Uses PIL for the rounded shape
    so the corners survive Telegram's photo encoding.
    """
    from PIL import Image, ImageDraw

    inner = Image.open(white_card_buf).convert("RGB")
    iw, ih = inner.size
    pad = _CARD_PADDING
    out_w = iw + 2 * pad
    out_h = ih + 2 * pad

    # Solid blue canvas.
    canvas = Image.new("RGB", (out_w, out_h), _CARD_BG)

    # Round the corners of the inner card by masking against a rounded rectangle.
    mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, iw - 1, ih - 1),
        radius=_CARD_RADIUS,
        fill=255,
    )
    canvas.paste(inner, (pad, pad), mask=mask)
    canvas.save(out_path, "PNG", optimize=True)


class ReportService:
    def __init__(self) -> None:
        self.reports_dir = Path("reports")
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    async def generate_profile_chart(
        self,
        db: AsyncSession,
        user_id: int,
        period: str = "week",
        lang: str = "ru",
        currency_code: str = "USD",
    ) -> Path:
        from matplotlib import gridspec
        from app.i18n import t

        now = datetime.utcnow()
        if period == "week":
            start = now - timedelta(days=7)
            step = timedelta(days=1)
            bucket = lambda dt: dt.strftime("%d.%m")
        elif period == "month":
            start = now - timedelta(days=30)
            step = timedelta(days=1)
            bucket = lambda dt: dt.strftime("%d.%m")
        else:
            start = now - timedelta(days=365)
            step = timedelta(days=15)
            bucket = lambda dt: dt.strftime("%m.%y")

        # ---- Build x-axis ----
        all_keys: list[str] = []
        cur = start
        while cur <= now:
            k = bucket(cur)
            if not all_keys or all_keys[-1] != k:
                all_keys.append(k)
            cur += step

        # ---- Income / expense per bucket from transactions ----
        txs = (
            await db.execute(
                select(Transaction).where(
                    and_(Transaction.user_id == user_id, Transaction.created_at >= start)
                )
            )
        ).scalars().all()
        income = defaultdict(Decimal)
        expense = defaultdict(Decimal)
        for tx in txs:
            k = bucket(tx.created_at)
            if tx.tx_type == TransactionType.INCOME:
                income[k] += Decimal(tx.amount)
            else:
                expense[k] += Decimal(tx.amount)

        # ---- Running balances from ProfileSnapshot ----
        snapshots = (
            await db.execute(
                select(ProfileSnapshot).where(
                    and_(
                        ProfileSnapshot.user_id == user_id,
                        ProfileSnapshot.snapshot_at >= start,
                    )
                ).order_by(ProfileSnapshot.snapshot_at.asc())
            )
        ).scalars().all()
        snap_by_key: dict[str, ProfileSnapshot] = {}
        for s in snapshots:
            snap_by_key[bucket(s.snapshot_at)] = s

        income_pts: list[float] = []
        expense_pts: list[float] = []
        balance_pts: list[float] = []
        card_pts: list[float] = []
        crypto_pts: list[float] = []
        last_total = last_card = last_crypto = 0.0
        for k in all_keys:
            income_pts.append(float(income.get(k, Decimal("0"))))
            expense_pts.append(float(expense.get(k, Decimal("0"))))
            s = snap_by_key.get(k)
            if s:
                last_total = float(s.total_usd or 0)
                last_card = float(s.mono_usd or 0)
                last_crypto = float(s.ton_usd or 0)
            balance_pts.append(last_total)
            card_pts.append(last_card)
            crypto_pts.append(last_crypto)

        # ---------------- Render the white inner card ----------------
        income_color = "#2e7d32"
        expense_color = "#c62828"
        balance_color = "#6f42c1"
        card_line_color = "#00897b"
        crypto_line_color = "#ef6c00"

        # All chart content lives on a fully-white figure (the "card"). PIL adds
        # the blue padded background with rounded corners afterwards.
        fig = plt.figure(figsize=(11, 6.2), facecolor="white", dpi=140)
        gs = gridspec.GridSpec(
            3, 3, figure=fig,
            height_ratios=[0.6, 4.5, 1.1], hspace=0.4, wspace=0.18,
            left=0.06, right=0.96, top=0.96, bottom=0.06,
        )

        # Title row (spans full width)
        ax_title = fig.add_subplot(gs[0, :])
        ax_title.axis("off")
        days_map = {"week": 7, "month": 30, "year": 365}
        n_days = days_map.get(period, 7)
        ax_title.text(0.0, 0.5, t("chart.title", lang),
                      fontsize=20, fontweight="bold", color="#222",
                      transform=ax_title.transAxes, va="center")
        ax_title.text(1.0, 0.5,
                      f"{currency_code}  ·  {t('chart.days_label', lang, n=n_days)}",
                      fontsize=11, color="#888",
                      transform=ax_title.transAxes, va="center", ha="right")

        # Main plot
        ax = fig.add_subplot(gs[1, :])
        ax.set_facecolor("white")

        has_data = any(p > 0 for p in income_pts + expense_pts + balance_pts
                                 + card_pts + crypto_pts)
        if has_data:
            ax.fill_between(all_keys, 0, income_pts, color=income_color, alpha=0.10)
            ax.fill_between(all_keys, 0, expense_pts, color=expense_color, alpha=0.10)
            ax.plot(all_keys, income_pts, color=income_color, linewidth=2.4,
                    marker="o", markersize=4, label=t("chart.income", lang))
            ax.plot(all_keys, expense_pts, color=expense_color, linewidth=2.4,
                    marker="o", markersize=4, label=t("chart.expense", lang))
            ax.plot(all_keys, balance_pts, color=balance_color, linewidth=2.6,
                    marker="o", markersize=4, label=t("chart.balance", lang))
            if any(card_pts):
                ax.plot(all_keys, card_pts, color=card_line_color, linewidth=1.8,
                        marker="o", markersize=3, alpha=0.85,
                        label=t("chart.card_balance", lang))
            if any(crypto_pts):
                ax.plot(all_keys, crypto_pts, color=crypto_line_color, linewidth=1.8,
                        marker="o", markersize=3, alpha=0.85,
                        label=t("chart.crypto_balance", lang))
            ax.axhline(0, color="#aaa", linewidth=0.6, linestyle="--", alpha=0.5)
            ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2)
        else:
            ax.text(0.5, 0.5, t("chart.no_data", lang),
                    ha="center", va="center", transform=ax.transAxes,
                    color="#888", fontsize=12)
            ax.set_xticks([])
            ax.set_yticks([])

        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#ddd")
        ax.tick_params(axis="x", rotation=45, colors="#888", labelsize=9)
        ax.tick_params(axis="y", colors="#888", labelsize=9)
        if has_data:
            ax.grid(True, alpha=0.18, linestyle="--", linewidth=0.6)

        # Bottom stat boxes (still on white)
        max_inc = max(income_pts) if income_pts else 0.0
        max_exp = max(expense_pts) if expense_pts else 0.0
        min_bal = min(balance_pts) if balance_pts else 0.0
        boxes = [
            (t("chart.max_income", lang), max_inc, income_color, "+"),
            (t("chart.max_expense", lang), max_exp, expense_color, ""),
            (t("chart.min_balance", lang), min_bal, balance_color,
             "+" if min_bal >= 0 else ""),
        ]
        for i, (label, val, color, sign) in enumerate(boxes):
            ax_box = fig.add_subplot(gs[2, i])
            ax_box.axis("off")
            ax_box.set_facecolor("white")
            ax_box.text(0.5, 0.75, label, ha="center", va="center",
                        fontsize=10, color="#888", transform=ax_box.transAxes)
            value_str = f"{sign}{val:,.0f} {currency_code}".replace(",", " ")
            ax_box.text(0.5, 0.25, value_str, ha="center", va="center",
                        fontsize=14, fontweight="bold", color=color,
                        transform=ax_box.transAxes)

        # Save white-card image to a buffer, then composite via PIL.
        from io import BytesIO
        buf = BytesIO()
        plt.savefig(buf, format="png", facecolor="white", dpi=140)
        plt.close(fig)
        buf.seek(0)

        file_path = self.reports_dir / f"user_{user_id}_{period}_{lang}.png"
        _composite_blue_card(buf, file_path)

        db.add(GeneratedReport(user_id=user_id, period=period, file_path=str(file_path)))
        await db.commit()
        return file_path

    async def _generate_pie(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        account_id: int | None,
        cache_filename: str,
    ) -> Path | None:
        file_path = self.reports_dir / cache_filename
        if file_path.exists():
            mtime = datetime.utcfromtimestamp(file_path.stat().st_mtime)
            if datetime.utcnow() - mtime < timedelta(hours=PIE_CACHE_HOURS):
                return file_path

        since = datetime.utcnow() - timedelta(days=30)
        where_clauses = [
            Transaction.user_id == user_id,
            Transaction.tx_type == TransactionType.EXPENSE,
            Transaction.created_at >= since,
        ]
        if account_id is not None:
            where_clauses.append(Transaction.account_id == account_id)

        rows = (
            await db.execute(select(Transaction).where(and_(*where_clauses)))
        ).scalars().all()

        by_cat: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for tx in rows:
            by_cat[tx.category or "other"] += Decimal(tx.amount)

        if not by_cat:
            return None

        total = sum(by_cat.values())
        threshold = total * Decimal("0.02")
        big = {k: v for k, v in by_cat.items() if v >= threshold}
        small_sum = sum(v for k, v in by_cat.items() if v < threshold)
        if small_sum > 0:
            big["Прочее"] = big.get("Прочее", Decimal("0")) + small_sum

        sorted_items = sorted(big.items(), key=lambda kv: kv[1], reverse=True)
        labels = [k for k, _ in sorted_items]
        values = [float(v) for _, v in sorted_items]

        _render_pie_to_card(
            file_path,
            labels=labels,
            values=values,
            title="Расходы за последние 30 дней",
        )
        return file_path

    async def generate_account_pie_chart(
        self, db: AsyncSession, user_id: int, account_id: int
    ) -> Path | None:
        """Pie chart of expense categories for the past 30 days for one account."""
        return await self._generate_pie(
            db, user_id, account_id=account_id,
            cache_filename=f"pie_acc_{account_id}.png",
        )

    async def generate_currency_chart(
        self,
        *,
        base: str,
        target: str,
        prices: list[tuple[datetime, float]],
        lang: str = "ru",
    ) -> Path | None:
        """
        Generates a price-history chart for `base/target` pair (always for 1 unit).
        `prices` — list of (datetime, target_per_one_base) tuples sorted ASC by time.
        Uses a real datetime x-axis so dense data (e.g. CoinGecko's 5-minute points)
        renders as a smooth line instead of stacking onto categorical day labels.
        """
        if not prices:
            return None

        from io import BytesIO
        import matplotlib.dates as mdates

        xs = [p[0] for p in prices]            # actual datetimes
        ys = [p[1] for p in prices]
        change = ys[-1] - ys[0] if len(ys) >= 2 else 0
        line_color = "#2e7d32" if change >= 0 else "#c62828"

        fig = plt.figure(figsize=(11, 5.6), facecolor="white", dpi=140)
        gs = plt.matplotlib.gridspec.GridSpec(
            2, 1, figure=fig,
            height_ratios=[1, 4], hspace=0.25,
            left=0.07, right=0.95, top=0.95, bottom=0.12,
        )

        # Header: "1 BASE = LATEST TARGET" + delta% over period
        ax_h = fig.add_subplot(gs[0, 0])
        ax_h.axis("off")
        rate = ys[-1]
        ax_h.text(0.0, 0.72, f"1 {base}",
                  fontsize=14, color="#888",
                  transform=ax_h.transAxes, va="center")
        ax_h.text(0.0, 0.25,
                  f"{rate:,.4f} {target}".replace(",", " "),
                  fontsize=24, fontweight="bold", color="#222",
                  transform=ax_h.transAxes, va="center")
        if len(ys) >= 2 and ys[0] != 0:
            pct = (ys[-1] - ys[0]) / ys[0] * 100
            sign = "+" if pct >= 0 else ""
            ax_h.text(1.0, 0.25, f"{sign}{pct:.2f}%",
                      fontsize=14, fontweight="bold", color=line_color,
                      transform=ax_h.transAxes, va="center", ha="right")

        # Plot
        ax = fig.add_subplot(gs[1, 0])
        ax.set_facecolor("white")
        ax.fill_between(xs, ys, color=line_color, alpha=0.12)
        ax.plot(xs, ys, color=line_color, linewidth=2.0)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#ddd")
        ax.tick_params(axis="x", rotation=0, colors="#888", labelsize=9)
        ax.tick_params(axis="y", colors="#888", labelsize=9)
        ax.grid(True, alpha=0.15, linestyle="--", linewidth=0.6)

        # Adaptive x-axis formatter: short window → HH:MM, longer → DD.MM.
        span = (xs[-1] - xs[0]) if len(xs) >= 2 else timedelta(seconds=0)
        if span <= timedelta(hours=36):
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        elif span <= timedelta(days=14):
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        else:
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))

        buf = BytesIO()
        plt.savefig(buf, format="png", facecolor="white", dpi=140)
        plt.close(fig)
        buf.seek(0)

        file_path = self.reports_dir / f"fx_{base}_{target}_{lang}.png"
        _composite_blue_card(buf, file_path)
        return file_path

    async def generate_user_pie_chart(
        self, db: AsyncSession, user_id: int
    ) -> Path | None:
        """Pie chart of expense categories for the past 30 days across all accounts."""
        return await self._generate_pie(
            db, user_id, account_id=None,
            cache_filename=f"pie_user_{user_id}.png",
        )

    async def generate_history_pie_chart(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        tx_type: TransactionType | None = None,
        account_type: AccountType | None = None,
        account_id: int | None = None,
    ) -> Path | None:
        """
        Pie chart for the history view, adapting to the active filters.

        Modes:
        - tx_type is None → 2-slice income vs expense aggregate.
        - tx_type is set  → category breakdown for that direction.

        Source/account filters narrow the underlying tx set; size/amount filters
        are intentionally ignored (a pie of "small purchases only" is rarely useful).
        Cache key includes all filter dimensions; results live 24h on disk.
        """
        # Build a stable cache filename per filter combination.
        parts = [f"u{user_id}"]
        if tx_type is not None:
            parts.append(f"t-{tx_type.value}")
        if account_type is not None:
            parts.append(f"at-{account_type.value}")
        if account_id is not None:
            parts.append(f"acc-{account_id}")
        cache_filename = "pie_hist_" + "__".join(parts) + ".png"

        file_path = self.reports_dir / cache_filename
        if file_path.exists():
            mtime = datetime.utcfromtimestamp(file_path.stat().st_mtime)
            if datetime.utcnow() - mtime < timedelta(hours=PIE_CACHE_HOURS):
                return file_path

        since = datetime.utcnow() - timedelta(days=30)
        where_clauses = [
            Transaction.user_id == user_id,
            Transaction.created_at >= since,
        ]
        if tx_type is not None:
            where_clauses.append(Transaction.tx_type == tx_type)
        if account_id is not None:
            where_clauses.append(Transaction.account_id == account_id)

        stmt = select(Transaction)
        if account_type is not None:
            stmt = stmt.join(Account, Account.id == Transaction.account_id)
            where_clauses.append(Account.account_type == account_type)
        stmt = stmt.where(and_(*where_clauses))
        rows = (await db.execute(stmt)).scalars().all()

        if not rows:
            return None

        if tx_type is None:
            income_sum = sum(
                (Decimal(t.amount) for t in rows if t.tx_type == TransactionType.INCOME),
                Decimal("0"),
            )
            expense_sum = sum(
                (Decimal(t.amount) for t in rows if t.tx_type == TransactionType.EXPENSE),
                Decimal("0"),
            )
            if income_sum == 0 and expense_sum == 0:
                return None
            labels: list[str] = []
            values: list[float] = []
            colors: list[str] | None = []
            if income_sum > 0:
                labels.append("Доходы")
                values.append(float(income_sum))
                colors.append("#3da35d")
            if expense_sum > 0:
                labels.append("Расходы")
                values.append(float(expense_sum))
                colors.append("#d64545")
            title = "Доходы и расходы — 30 дней"
        else:
            by_cat: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for t in rows:
                by_cat[t.category or "other"] += Decimal(t.amount)

            total = sum(by_cat.values())
            threshold = total * Decimal("0.02")
            big = {k: v for k, v in by_cat.items() if v >= threshold}
            small_sum = sum(v for k, v in by_cat.items() if v < threshold)
            if small_sum > 0:
                big["Прочее"] = big.get("Прочее", Decimal("0")) + small_sum

            sorted_items = sorted(big.items(), key=lambda kv: kv[1], reverse=True)
            labels = [k for k, _ in sorted_items]
            values = [float(v) for _, v in sorted_items]
            colors = None
            direction = "Расходы" if tx_type == TransactionType.EXPENSE else "Доходы"
            title = f"{direction} по категориям — 30 дней"

        _render_pie_to_card(file_path, labels=labels, values=values, title=title, colors=colors)
        return file_path
