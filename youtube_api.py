"""
youtube_api.py
--------------
YouTube Data API v3 bilan ishlash.

Ikki xil ulanish:
1) API kalit (/ytkey) - faqat O'QIYDI: videoning ochiq ma'lumotlari,
   nishadagi top videolar, ularning teglari. Shu bilan Jarvis SEO taklif beradi.
2) OAuth (/ytulash) - ixtiyoriy. Ulansa, Jarvis tasdiqlangan sarlavha,
   tavsif va teglarni videoga O'ZI yozadi (videos.update).
   Buning uchun Railway Variables'ga YT_CLIENT_ID va YT_CLIENT_SECRET qo'yiladi.

Kalit va tokenlar DATA_DIR/youtube.json fayliga saqlanadi va hech qachon
logga yoki xato matniga chiqarilmaydi.

Kunlik kvota: 10 000 birlik. search = 100, videos.list = 1, videos.update = 50.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import config

log = logging.getLogger("youtube_api")

BASE = "https://www.googleapis.com/youtube/v3"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
TIMEOUT = 30
DAILY_QUOTA = int(os.environ.get("YT_DAILY_QUOTA", "10000") or 10000)
COST = {"search": 100, "videos.list": 1, "channels.list": 1, "videos.update": 50}

_lock = threading.Lock()


class YouTubeError(RuntimeError):
    """fatal=True: kalit/token yaroqsiz, odam aralashuvisiz tuzalmaydi."""

    def __init__(self, message, fatal=False, quota=False):
        super().__init__(message)
        self.fatal = fatal
        self.quota = quota


# ---------- saqlash ----------

def _store_path():
    return os.path.join(config.DATA_DIR, "youtube.json")


def _load():
    try:
        with open(_store_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(store):
    path = _store_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        log.error("youtube.json saqlab bo'lmadi: %s", type(e).__name__)


def _update(**fields):
    with _lock:
        store = _load()
        store.update(fields)
        _save(store)
        return store


# ---------- kvota hisobi ----------

def _pacific_day():
    # YouTube kvotasi Tinch okeani vaqti bilan yarim tunda yangilanadi (~UTC-8)
    return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")


def _spend(op):
    cost = COST.get(op, 1)
    with _lock:
        store = _load()
        q = store.get("quota") or {}
        if q.get("day") != _pacific_day():
            q = {"day": _pacific_day(), "used": 0}
        q["used"] = int(q.get("used", 0)) + cost
        store["quota"] = q
        _save(store)


def quota_used():
    q = _load().get("quota") or {}
    return int(q.get("used", 0)) if q.get("day") == _pacific_day() else 0


# ---------- API kalit ----------

def get_api_key():
    return _load().get("api_key") or (os.environ.get("YOUTUBE_API_KEY") or "").strip()


def has_key():
    return bool(get_api_key())


def set_api_key(key):
    """Kalitni tekshiradi va saqlaydi. Yaroqsiz bo'lsa YouTubeError."""
    key = (key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{30,60}", key):
        raise YouTubeError("Bu YouTube API kalitiga o'xshamaydi (odatda AIza... bilan boshlanadi)")
    try:
        r = requests.get(
            f"{BASE}/videos",
            params={"part": "id", "chart": "mostPopular", "regionCode": "UZ", "maxResults": 1, "key": key},
            timeout=TIMEOUT,
        )
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        raise YouTubeError(f"YouTube bilan bog'lanib bo'lmadi ({type(e).__name__})")
    if r.status_code >= 400:
        raise YouTubeError(_error_text(body, r.status_code), fatal=True)
    _update(api_key=key, key_saved_at=time.time())
    _spend("videos.list")
    log.info("YouTube API kaliti saqlandi.")
    return True


def _error_text(body, status):
    err = (body or {}).get("error") if isinstance(body, dict) else None
    err = err or {}
    reason = ""
    for e in err.get("errors") or []:
        reason = e.get("reason") or reason
    details = err.get("details") or []
    for d in details:
        reason = d.get("reason") or reason
    if reason in ("API_KEY_INVALID", "keyInvalid", "badRequest") and status == 400:
        return "Kalit noto'g'ri"
    if reason in ("accessNotConfigured", "SERVICE_DISABLED"):
        return "Google Cloud'da YouTube Data API v3 yoqilmagan (Library → Enable)"
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        return "Bugungi YouTube kvotasi tugadi, ertaga tiklanadi"
    if reason in ("API_KEY_SERVICE_BLOCKED", "forbidden", "ipRefererBlocked", "API_KEY_HTTP_REFERRER_BLOCKED"):
        return "Kalit cheklovi bu so'rovga ruxsat bermayapti (API restrictions'da YouTube Data API v3 tanlanganini tekshiring)"
    return err.get("message") or f"HTTP {status}"


# ---------- OAuth (ixtiyoriy) ----------

def _client():
    cid = (os.environ.get("YT_CLIENT_ID") or "").strip()
    secret = (os.environ.get("YT_CLIENT_SECRET") or "").strip()
    return cid, secret


def oauth_available():
    cid, secret = _client()
    return bool(cid and secret)


def is_connected():
    return bool(_load().get("refresh_token"))


def redirect_uri():
    uri = (os.environ.get("YT_REDIRECT_URI") or "").strip()
    if uri:
        return uri
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    return f"https://{domain}/youtube/callback" if domain else ""


def auth_link():
    """Egasiga yuboriladigan ruxsat havolasi. state 15 daqiqa amal qiladi."""
    cid, _ = _client()
    if not cid:
        raise YouTubeError("YT_CLIENT_ID va YT_CLIENT_SECRET Railway Variables'ga qo'yilmagan")
    uri = redirect_uri()
    if not uri:
        raise YouTubeError("Server manzili topilmadi (RAILWAY_PUBLIC_DOMAIN yoki YT_REDIRECT_URI)")
    state = secrets.token_urlsafe(24)
    _update(oauth_state=state, oauth_state_at=time.time())
    return AUTH_URL + "?" + urlencode({
        "client_id": cid,
        "redirect_uri": uri,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


def finish_oauth(code, state):
    """Google qaytargan kodni refresh tokenga almashtiradi. Qaytaradi: kanal nomi."""
    store = _load()
    if not state or state != store.get("oauth_state") or time.time() - store.get("oauth_state_at", 0) > 900:
        raise YouTubeError("Havola eskirgan yoki noto'g'ri. Telegram'da /ytulash ni qayta bosing.")
    cid, secret = _client()
    try:
        r = requests.post(TOKEN_URL, data={
            "code": code,
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        }, timeout=TIMEOUT)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        raise YouTubeError(f"Google bilan bog'lanib bo'lmadi ({type(e).__name__})")
    if r.status_code >= 400 or not body.get("refresh_token"):
        raise YouTubeError("Google ruxsat bermadi: " + str(body.get("error_description") or body.get("error") or r.status_code))
    _update(
        refresh_token=body["refresh_token"],
        access_token=body.get("access_token"),
        access_exp=time.time() + int(body.get("expires_in", 3600)) - 60,
        oauth_state=None,
    )
    try:
        ch = my_channel()
        title = (ch.get("snippet") or {}).get("title", "")
        _update(channel_id=ch.get("id"), channel_title=title)
        return title
    except YouTubeError:
        return ""


def _access_token():
    store = _load()
    if store.get("access_token") and store.get("access_exp", 0) > time.time():
        return store["access_token"]
    rt = store.get("refresh_token")
    if not rt:
        raise YouTubeError("YouTube kanal ulanmagan (/ytulash)", fatal=True)
    cid, secret = _client()
    try:
        r = requests.post(TOKEN_URL, data={
            "client_id": cid,
            "client_secret": secret,
            "refresh_token": rt,
            "grant_type": "refresh_token",
        }, timeout=TIMEOUT)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        raise YouTubeError(f"Google bilan bog'lanib bo'lmadi ({type(e).__name__})")
    if r.status_code >= 400 or not body.get("access_token"):
        if body.get("error") == "invalid_grant":
            _update(refresh_token=None, access_token=None)
            raise YouTubeError(
                "YouTube ruxsati bekor bo'lgan. /ytulash bilan qayta ulang "
                "(Consent screen 'In production' holatida bo'lishi kerak, aks holda 7 kunda o'chadi)",
                fatal=True,
            )
        raise YouTubeError("Tokenni yangilab bo'lmadi: " + str(body.get("error") or r.status_code))
    _update(access_token=body["access_token"], access_exp=time.time() + int(body.get("expires_in", 3600)) - 60)
    return body["access_token"]


def disconnect():
    _update(refresh_token=None, access_token=None, access_exp=0, channel_id=None, channel_title=None)


# ---------- so'rov ----------

def _request(method, path, op, params=None, body=None, oauth=False):
    params = dict(params or {})
    headers = {}
    if oauth:
        headers["Authorization"] = "Bearer " + _access_token()
    else:
        key = get_api_key()
        if not key:
            raise YouTubeError("YouTube API kaliti yo'q. /ytkey bilan yuboring.", fatal=True)
        params["key"] = key

    last = None
    for attempt in range(3):
        try:
            r = requests.request(method, f"{BASE}/{path}", params=params, json=body, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            last = YouTubeError(f"YouTube bilan bog'lanib bo'lmadi ({type(e).__name__})")
            time.sleep(2 * (attempt + 1))
            continue
        _spend(op)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code < 400:
            return data
        text = _error_text(data, r.status_code)
        if r.status_code >= 500:
            last = YouTubeError(text)
            time.sleep(2 * (attempt + 1))
            continue
        if "kvota" in text:
            raise YouTubeError(text, quota=True)
        if r.status_code == 401 and oauth:
            _update(access_token=None, access_exp=0)
            if attempt == 0:
                headers["Authorization"] = "Bearer " + _access_token()
                continue
        raise YouTubeError(text, fatal=r.status_code in (400, 401, 403) and "Kalit" in text)
    raise last


# ---------- videolar ----------

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{11}$")
LINK_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/\S+", re.I)


def parse_video_id(text):
    """Havola yoki ID'dan 11 belgili video ID'ni ajratadi. Topilmasa None."""
    text = (text or "").strip()
    if _ID_RE.match(text):
        return text
    m = LINK_RE.search(text)
    if not m:
        return None
    url = m.group(0)
    if not url.lower().startswith("http"):
        url = "https://" + url
    u = urlparse(url)
    host = u.netloc.lower()
    parts = [p for p in u.path.split("/") if p]
    vid = None
    if "youtu.be" in host and parts:
        vid = parts[0]
    elif parts and parts[0] in ("shorts", "live", "embed", "v") and len(parts) > 1:
        vid = parts[1]
    else:
        vid = (parse_qs(u.query).get("v") or [None])[0]
    return vid if vid and _ID_RE.match(vid) else None


def iso_duration_sec(value):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def get_video(video_id):
    """Video ma'lumoti. Kanal ulangan bo'lsa, Private videolarni ham ko'radi."""
    use_oauth = is_connected() and oauth_available()
    data = _request(
        "GET", "videos", "videos.list",
        {"part": "snippet,contentDetails,statistics,status", "id": video_id},
        oauth=use_oauth,
    )
    items = data.get("items") or []
    if not items:
        hint = "" if use_oauth else " Private video bo'lsa, uni ko'rish uchun kanalni /ytulash bilan ulang yoki Unlisted qiling."
        raise YouTubeError("Video topilmadi." + hint)
    return items[0]


def my_channel():
    data = _request("GET", "channels", "channels.list", {"part": "snippet,statistics", "mine": "true"}, oauth=True)
    items = data.get("items") or []
    if not items:
        raise YouTubeError("Bu Google akkauntda YouTube kanal topilmadi")
    return items[0]


def search_niche(query, days=180, limit=10, region="UZ"):
    """Nishadagi eng ko'p ko'rilgan videolar (sarlavha, teglar, ko'rishlar). 101+ kvota birligi."""
    query = (query or "").strip()[:100]
    if not query:
        return []
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = _request("GET", "search", "search", {
        "part": "id",
        "q": query,
        "type": "video",
        "order": "viewCount",
        "publishedAfter": after,
        "regionCode": region,
        "maxResults": max(1, min(limit, 25)),
    })
    ids = [it["id"]["videoId"] for it in found.get("items") or [] if (it.get("id") or {}).get("videoId")]
    if not ids:
        return []
    data = _request("GET", "videos", "videos.list", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)})
    out = []
    for v in data.get("items") or []:
        sn = v.get("snippet") or {}
        st = v.get("statistics") or {}
        out.append({
            "id": v.get("id"),
            "title": sn.get("title", ""),
            "channel": sn.get("channelTitle", ""),
            "tags": (sn.get("tags") or [])[:15],
            "description_head": (sn.get("description") or "")[:200],
            "views": int(st.get("viewCount", 0) or 0),
            "likes": int(st.get("likeCount", 0) or 0),
            "duration_sec": iso_duration_sec((v.get("contentDetails") or {}).get("duration")),
        })
    out.sort(key=lambda x: x["views"], reverse=True)
    return out


def update_video_metadata(video_id, title=None, description=None, tags=None):
    """Sarlavha/tavsif/teglarni videoga yozadi. Boshqa maydonlar (kategoriya, til) saqlanadi."""
    data = _request("GET", "videos", "videos.list", {"part": "snippet", "id": video_id}, oauth=True)
    items = data.get("items") or []
    if not items:
        raise YouTubeError("Video topilmadi yoki bu kanalga tegishli emas")
    sn = dict(items[0].get("snippet") or {})
    snippet = {
        "categoryId": sn.get("categoryId") or "22",
        "title": title if title is not None else sn.get("title", ""),
        "description": description if description is not None else sn.get("description", ""),
        "tags": tags if tags is not None else sn.get("tags", []),
    }
    for keep in ("defaultLanguage", "defaultAudioLanguage"):
        if sn.get(keep):
            snippet[keep] = sn[keep]
    _request("PUT", "videos", "videos.update", {"part": "snippet"}, body={"id": video_id, "snippet": snippet}, oauth=True)
    return True


def status():
    store = _load()
    return {
        "key": has_key(),
        "oauth_available": oauth_available(),
        "connected": bool(store.get("refresh_token")),
        "channel": store.get("channel_title") or "",
        "quota_used": quota_used(),
        "quota_limit": DAILY_QUOTA,
    }
