"""
instagram_api.py
----------------
Instagram Graph API (Instagram Login) bilan ishlash:
profil, postlar, statistika (insights), komentlar, komentga javob,
komentni yashirish va tokenni avtomatik yangilash.

Token Railway Variables'dagi INSTAGRAM_ACCESS_TOKEN'dan olinadi.
Token 60 kunda eskiradi, shuning uchun Jarvis uni har ~20 kunda o'zi
yangilaydi va yangisini DATA_DIR/instagram_token.json fayliga saqlaydi.
Variables'ga yangi token qo'ysangiz, Jarvis avtomatik o'shanga o'tadi.

Xavfsizlik: token hech qachon logga yoki xato matniga chiqarilmaydi.
"""

import hashlib
import json
import logging
import os
import threading
import time

import requests

import config

log = logging.getLogger("instagram_api")

API_VERSION = os.environ.get("IG_API_VERSION", "v23.0").strip() or "v23.0"
BASE = f"https://graph.instagram.com/{API_VERSION}"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
TIMEOUT = 30

RATE_LIMIT_CODES = {4, 17, 32, 613}
TRANSIENT_CODES = {1, 2}

PROFILE_FIELDS = "user_id,username,name,biography,followers_count,follows_count,media_count"
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,permalink,timestamp,"
    "like_count,comments_count"
)
COMMENT_FIELDS = "id,text,username,timestamp,like_count,replies{id,username,text,timestamp}"

# Har bir kontent turi uchun so'raladigan ko'rsatkichlar
INSIGHT_METRICS = {
    "REELS": [
        "views", "reach", "likes", "comments", "shares", "saved",
        "total_interactions", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
    ],
    "FEED": [
        "views", "reach", "likes", "comments", "shares", "saved",
        "total_interactions", "profile_visits", "follows",
    ],
    "STORY": [
        "views", "reach", "replies", "shares", "total_interactions",
        "profile_visits", "follows",
    ],
}
ACCOUNT_METRICS = [
    "reach", "views", "accounts_engaged", "total_interactions",
    "likes", "comments", "shares", "saves", "profile_links_taps",
]

# API qo'llamaydigan ko'rsatkichlar shu yerda eslab qolinadi (qayta so'ralmaydi)
_unsupported = {}
_token_lock = threading.Lock()


class InstagramError(RuntimeError):
    """Instagram API xatosi.
    fatal=True: token yo'q yoki yaroqsiz, odam aralashuvisiz tuzalmaydi.
    rate_limited=True: limit to'lgan, keyinroq urinish kerak."""

    def __init__(self, message, fatal=False, code=None, rate_limited=False):
        super().__init__(message)
        self.fatal = fatal
        self.code = code
        self.rate_limited = rate_limited


# ---------- token ----------

def _token_file():
    return os.path.join(config.DATA_DIR, "instagram_token.json")


def _env_token():
    return (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()


def _fingerprint(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16] if token else ""


def _load_store():
    try:
        with open(_token_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_store(store):
    path = _token_file()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f)
        os.replace(tmp, path)
    except OSError as e:
        log.error("Token faylini saqlab bo'lmadi: %s", type(e).__name__)


def _current_store():
    """Variables'dagi token o'zgargan bo'lsa, eski saqlangan token bekor qilinadi."""
    env = _env_token()
    store = _load_store()
    if env and store.get("env_fp") != _fingerprint(env):
        store = {"env_fp": _fingerprint(env)}
    return store


def get_token():
    store = _current_store()
    return store.get("token") or _env_token()


def is_configured():
    return bool(get_token())


def refresh_token_if_needed(every_days=20):
    """Tokenni har every_days kunda yangilaydi. Yangilangan bo'lsa True."""
    with _token_lock:
        store = _current_store()
        token = store.get("token") or _env_token()
        if not token:
            return False
        now = time.time()
        if now - store.get("refreshed_at", 0) < every_days * 86400:
            return False
        if now - store.get("attempt_at", 0) < 86400:
            return False
        store["attempt_at"] = now
        try:
            r = requests.get(
                REFRESH_URL,
                params={"grant_type": "ig_refresh_token", "access_token": token},
                timeout=TIMEOUT,
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            _save_store(store)
            log.warning("Tokenni yangilab bo'lmadi: %s", type(e).__name__)
            return False

        new_token = data.get("access_token") if isinstance(data, dict) else None
        if not new_token:
            _save_store(store)
            msg = ((data or {}).get("error") or {}).get("message", "noma'lum") if isinstance(data, dict) else "noma'lum"
            log.warning("Token yangilanmadi (ertaga yana urinamiz): %s", msg)
            return False

        store.update(token=new_token, refreshed_at=now)
        _save_store(store)
        days = int(data.get("expires_in", 0) or 0) // 86400
        log.info("Instagram token yangilandi, %s kun amal qiladi.", days)
        return True


# ---------- so'rov yuborish ----------

def _request(method, path, params=None, data=None):
    token = get_token()
    if not token:
        raise InstagramError(
            "INSTAGRAM_ACCESS_TOKEN o'rnatilmagan", fatal=True, code="no_token"
        )

    if path.startswith("http"):
        url, params = path, None  # paging "next" havolasida hamma narsa bor
    else:
        url = f"{BASE}/{path.lstrip('/')}"
        params = dict(params or {})
        params["access_token"] = token

    last_error = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, params=params, data=data, timeout=TIMEOUT)
        except requests.RequestException as e:
            last_error = InstagramError(f"Instagram bilan bog'lanib bo'lmadi ({type(e).__name__})")
            time.sleep(2 * (attempt + 1))
            continue

        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None

        if r.status_code < 400 and not err:
            return body

        err = err or {}
        code = err.get("code")
        msg = err.get("message") or f"HTTP {r.status_code}"

        if code == 190:
            raise InstagramError(
                "Instagram token yaroqsiz yoki muddati o'tgan", fatal=True, code=code
            )
        if code in RATE_LIMIT_CODES or r.status_code == 429:
            raise InstagramError(
                "Instagram API limiti to'ldi, keyinroq urinamiz", code=code, rate_limited=True
            )
        if code in TRANSIENT_CODES or r.status_code >= 500:
            last_error = InstagramError(msg, code=code)
            time.sleep(2 * (attempt + 1))
            continue
        raise InstagramError(msg, code=code)

    raise last_error


def _paged(path, params, limit):
    items = []
    page = _request("GET", path, params)
    while True:
        items.extend(page.get("data", []))
        nxt = (page.get("paging") or {}).get("next")
        if len(items) >= limit or not nxt:
            break
        page = _request("GET", nxt)
    return items[:limit]


# ---------- profil va kontent ----------

def get_profile():
    return _request("GET", "me", {"fields": PROFILE_FIELDS})


def get_recent_posts(limit=10):
    return _paged("me/media", {"fields": MEDIA_FIELDS, "limit": min(limit, 50)}, limit)


def get_media(media_id):
    return _request("GET", str(media_id), {"fields": MEDIA_FIELDS})


def get_stories():
    """Hozir faol (24 soat ichidagi) storylar."""
    fields = "id,media_type,media_product_type,permalink,timestamp,caption"
    return _paged("me/stories", {"fields": fields, "limit": 50}, 50)


# ---------- statistika ----------

def _parse_insights(body):
    out = {}
    for item in body.get("data", []):
        name = item.get("name")
        if not name:
            continue
        if isinstance(item.get("total_value"), dict) and "value" in item["total_value"]:
            out[name] = item["total_value"]["value"]
        elif item.get("values"):
            out[name] = item["values"][0].get("value")
    return out


def _is_invalid_metric(error):
    text = str(error).lower()
    return "must be one of" in text or "invalid metric" in text or "not supported" in text and "metric" in text


def _fetch_metrics(path, kind, metrics, extra=None):
    """Ko'rsatkichlarni bitta so'rovda oladi; xato bo'lsa birma-bir urinadi."""
    bad = _unsupported.setdefault(kind, set())
    metrics = [m for m in metrics if m not in bad]
    if not metrics:
        return {}
    params = dict(extra or {})
    try:
        return _parse_insights(_request("GET", path, {**params, "metric": ",".join(metrics)}))
    except InstagramError as e:
        if e.fatal or e.rate_limited:
            raise

    result = {}
    for metric in metrics:
        try:
            result.update(_parse_insights(_request("GET", path, {**params, "metric": metric})))
        except InstagramError as e:
            if e.fatal or e.rate_limited:
                raise
            if _is_invalid_metric(e):
                bad.add(metric)
                continue
            break  # bu post uchun statistika umuman berilmayapti (masalan juda eski post)
    return result


def media_kind(media):
    product = (media.get("media_product_type") or "").upper()
    if product == "REELS":
        return "REELS"
    if product == "STORY":
        return "STORY"
    return "FEED"


def get_media_insights(media):
    kind = media_kind(media)
    return _fetch_metrics(f"{media['id']}/insights", kind, INSIGHT_METRICS[kind])


def get_account_insights(days=7):
    until = int(time.time())
    since = until - days * 86400
    return _fetch_metrics(
        "me/insights",
        "ACCOUNT",
        ACCOUNT_METRICS,
        {"period": "day", "metric_type": "total_value", "since": since, "until": until},
    )


def get_audience():
    """Obunachilar demografiyasi (shahar, yosh, jins). 100+ obunachi kerak."""
    out = {}
    for breakdown in ("city", "age", "gender"):
        try:
            body = _request("GET", "me/insights", {
                "metric": "follower_demographics",
                "period": "lifetime",
                "metric_type": "total_value",
                "breakdown": breakdown,
            })
        except InstagramError as e:
            if e.fatal or e.rate_limited:
                raise
            continue
        rows = []
        for item in body.get("data", []):
            for bd in (item.get("total_value") or {}).get("breakdowns", []):
                for res in bd.get("results", []):
                    dims = res.get("dimension_values") or []
                    if dims:
                        rows.append((str(dims[0]), res.get("value", 0)))
        rows.sort(key=lambda x: -(x[1] or 0))
        if rows:
            out[breakdown] = rows[:6]
    return out


# ---------- komentlar ----------

def get_all_comments(media_id, fields=COMMENT_FIELDS, limit=500):
    return _paged(f"{media_id}/comments", {"fields": fields, "limit": 50}, limit)


def reply_to_comment(comment_id, message):
    return _request("POST", f"{comment_id}/replies", data={"message": message})


def hide_comment(comment_id, hide=True):
    return _request("POST", str(comment_id), data={"hide": "true" if hide else "false"})
