"""
creator_rewards/rewards.py
--------------------------
Mukofot formulasi va vaqt yordamchilari. Hech qanday baza yoki tarmoq yo'q —
faqat sof hisob-kitob, shuning uchun oson test qilinadi.

Formula (tasdiqlangan):
    10 000 ko'rishgacha            -> 0 so'm
    10 000 ko'rish                 -> 100 000 so'm
    keyin har to'liq 1 000 ko'rish -> +4 000 so'm
    100 000 va undan ko'p          -> 460 000 so'm (eng yuqori)
"""

import secrets
from datetime import datetime, timedelta, timezone

MIN_VIEWS = 10_000
BASE_REWARD = 100_000
STEP_VIEWS = 1_000
STEP_REWARD = 4_000
CAP_VIEWS = 100_000
MAX_REWARD = BASE_REWARD + (CAP_VIEWS - MIN_VIEWS) // STEP_VIEWS * STEP_REWARD  # 460 000

TRACKING_DAYS = 30
VOUCHER_DAYS = 30
SCREENSHOT_DAYS = (1, 7, 14, 30)

TASHKENT = timezone(timedelta(hours=5))
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 0/O, 1/I kabi chalkash belgilarsiz


def reward_for_views(views: int) -> int:
    """30 kunlik ko'rishlar soni uchun vaucher summasi (so'm)."""
    views = int(views or 0)
    if views < MIN_VIEWS:
        return 0
    steps = (min(views, CAP_VIEWS) - MIN_VIEWS) // STEP_VIEWS
    return BASE_REWARD + steps * STEP_REWARD


def views_to_next_step(views: int):
    """Mukofot keyingi marta oshishi uchun yana nechta ko'rish kerak. Chegaraga yetgan bo'lsa None."""
    views = int(views or 0)
    if views < MIN_VIEWS:
        return MIN_VIEWS - views
    if views >= CAP_VIEWS:
        return None
    return STEP_VIEWS - views % STEP_VIEWS


def new_voucher_code() -> str:
    part = lambda: "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))  # noqa: E731
    return f"BD-{part()}-{part()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def tashkent_str(text_or_dt, with_time=True) -> str:
    dt = from_iso(text_or_dt) if isinstance(text_or_dt, str) else text_or_dt
    return dt.astimezone(TASHKENT).strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def som(amount: int) -> str:
    return f"{int(amount):,}".replace(",", " ") + " so'm"
