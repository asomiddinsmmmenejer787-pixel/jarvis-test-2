"""
instagram_api.py
----------------
Instagram Graph API klienti. Jarvis shu modul orqali akkauntdan
ma'lumot oladi (profil, postlar, kommentlar, statistika) va javob yozadi.

Sozlamalar (faqat muhit o'zgaruvchilari, kodga yozilmaydi):
- INSTAGRAM_ACCESS_TOKEN  : Meta'dan olingan token (majburiy)
- INSTAGRAM_USER_ID       : Instagram Business/Creator akkaunt ID (majburiy)
- INSTAGRAM_API_VERSION   : Graph API versiyasi (ixtiyoriy, standart v21.0)

Xavfsizlik:
- Token faqat "Authorization" sarlavhasida yuboriladi, URL'ga qo'yilmaydi.
- Xato matnlariga token hech qachon chiqarilmaydi.
- Bu modul hech narsani o'zi yubormaydi. Javob yozish faqat
  auto_reply.py ichidan chaqiriladi, u esa DRY_RUN bilan boshqariladi.
"""

import logging
import os

import requests

log = logging.getLogger("instagram_api")

GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"
REQUEST_TIMEOUT_SECONDS = 20
MAX_PAGES = 10

# Meta xato kodlari, ularda qayta urinish foydasiz (token yoki ruxsat muammosi)
FATAL_ERROR_CODES = {10, 190, 200}


class InstagramError(RuntimeError):
    """Instagram bilan ishlashdagi xato. fatal=True bo'lsa, qayta urinish foydasiz."""

    def __init__(self, message, fatal=False, code=None):
        super().__init__(message)
        self.fatal = fatal
        self.code = code


def _settings():
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        raise InstagramError(
            "INSTAGRAM_ACCESS_TOKEN topilmadi. Railway Variables'ni tekshiring.",
            fatal=True,
        )
    user_id = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    if not user_id:
        raise InstagramError(
            "INSTAGRAM_USER_ID topilmadi. Railway Variables'ni tekshiring.",
            fatal=True,
        )
    version = os.environ.get("INSTAGRAM_API_VERSION", "").strip() or DEFAULT_API_VERSION
    return token, user_id, version


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _check_response(response):
    """Javobni tekshiradi, xato bo'lsa InstagramError ko'taradi."""
    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code == 200 and "error" not in body:
        return body

    error = body.get("error", {}) if isinstance(body, dict) else {}
    code = error.get("code")
    message = error.get("message", "noma'lum xato")
    fatal = response.status_code in (401, 403) or code in FATAL_ERROR_CODES
    raise InstagramError(
        f"Instagram API xatosi (HTTP {response.status_code}, kod {code}): {message}",
        fatal=fatal,
        code=code,
    )


def _send(method, path, params=None, data=None):
    token, _, version = _settings()
    url = f"{GRAPH_BASE}/{version}/{path.lstrip('/')}"
    try:
        response = requests.request(
            method,
            url,
            headers=_headers(token),
            params=params,
            data=data,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise InstagramError(f"Instagram bilan ulanish xatosi: {type(e).__name__}")
    return _check_response(response)


def _get_all(path, params):
    """Sahifalangan (paging) ro'yxatning hammasini yig'adi, MAX_PAGES chegarasi bilan."""
    token, _, _ = _settings()
    body = _send("GET", path, params=params)
    items = list(body.get("data", []))
    pages = 1

    while pages < MAX_PAGES:
        next_url = (body.get("paging") or {}).get("next")
        if not next_url:
            break
        try:
            response = requests.get(
                next_url,
                headers=_headers(token),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise InstagramError(f"Instagram bilan ulanish xatosi: {type(e).__name__}")
        body = _check_response(response)
        items.extend(body.get("data", []))
        pages += 1

    return items


def get_profile():
    """Akkaunt asosiy ma'lumoti: username, obunachilar soni, postlar soni."""
    _, user_id, _ = _settings()
    return _send(
        "GET",
        user_id,
        params={"fields": "id,username,followers_count,media_count"},
    )


def get_recent_posts(limit=5):
    """Oxirgi postlar (reel va rasmlar)."""
    _, user_id, _ = _settings()
    return _get_all(
        f"{user_id}/media",
        params={
            "fields": "id,caption,media_type,timestamp,permalink,like_count,comments_count",
            "limit": limit,
        },
    )[:limit]


def get_all_comments(media_id, fields="id,text,username,timestamp"):
    """Berilgan postning barcha kommentlari (sahifalab)."""
    return _get_all(f"{media_id}/comments", params={"fields": fields, "limit": 50})


def get_media_insights(media_id, metrics):
    """Post statistikasi. metrics: masalan 'reach,saved,plays' (media turiga qarab farq qiladi)."""
    body = _send("GET", f"{media_id}/insights", params={"metric": metrics})
    return body.get("data", [])


def reply_to_comment(comment_id, text):
    """Kommentga javob yozadi. Bu yozish amali, faqat auto_reply ruxsat berganda chaqiriladi."""
    body = _send("POST", f"{comment_id}/replies", data={"message": text})
    return body.get("id")


def check_connection():
    """Ulanishni tekshiradi va xulosani qaytaradi (token ko'rsatilmaydi)."""
    profile = get_profile()
    posts = get_recent_posts(limit=3)
    return {
        "username": profile.get("username"),
        "followers": profile.get("followers_count"),
        "media_count": profile.get("media_count"),
        "recent_posts": len(posts),
    }
