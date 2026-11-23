"""
creator_rewards/igconnect.py
----------------------------
Blogerning Instagram akkauntini rasmiy "Instagram orqali kirish" (Business Login) bilan ulash.

Bloger havolani bosadi -> Instagram ruxsat so'raydi -> bloger rozi bo'ladi -> Jarvis token oladi.
Parol hech qachon Jarvis'ga kelmaydi; bloger ruxsatni istalgan vaqtda Instagram sozlamalaridan bekor qiladi.

Ulangan akkauntdan Jarvis o'zi oladi:
  - Collab Reels'ning aniq statistikasi (ko'rishlar, qamrov, saqlash, ulashish) — skrinshotsiz;
  - akkaunt ma'lumotlari (obunachilar, postlar, faollik) — 10 ballik baho uchun.

Muhit o'zgaruvchilari: IG_APP_SECRET (majburiy), IG_APP_ID (jarviss-IG ilovasi), CREATOR_IG_REDIRECT (ixtiyoriy).
Meta ilovasi "ishlab chiqish" rejimida bo'lsa, faqat tester qilib qo'shilgan akkauntlar ulana oladi.
"""

import hashlib
import hmac
import logging
import os
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger("creator_ig")

AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
API = GRAPH + "/v23.0"
SCOPES = "instagram_business_basic,instagram_business_manage_insights"
DEFAULT_APP_ID = "975102868186193"  # jarviss-IG (Meta panelidagi "ID приложения Instagram")
STATE_TTL = 30 * 60
REEL_METRICS = "views,reach,saved,shares,likes,comments,total_interactions,ig_reels_avg_watch_time"


class IGError(Exception):
    """token_bad=True — token yaroqsiz (bloger ruxsatni bekor qilgan yoki muddati o'tgan)."""

    def __init__(self, message, token_bad=False):
        super().__init__(message)
        self.token_bad = token_bad


def app_id():
    return (os.environ.get("IG_APP_ID") or DEFAULT_APP_ID).strip()


def app_secret():
    return (os.environ.get("IG_APP_SECRET") or "").strip()


def redirect_uri():
    url = (os.environ.get("CREATOR_IG_REDIRECT") or "").strip()
    if url:
        return url
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    return f"https://{domain}/creator/ig/callback" if domain else ""


def configured():
    return bool(app_secret() and redirect_uri())


# ---------------- havola va state ----------------

def _sign(text):
    return hmac.new((app_secret() + "|ig-state").encode(), text.encode(), hashlib.sha256).hexdigest()[:24]


def make_state(tg_id, now=None):
    payload = f"{int(tg_id)}.{int(now or time.time())}"
    return f"{payload}.{_sign(payload)}"


def read_state(state, now=None):
    """state ichidagi Telegram ID. Soxta yoki eskirgan bo'lsa None."""
    try:
        tg, ts, sig = str(state).split(".")
        payload = f"{int(tg)}.{int(ts)}"
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    if (now or time.time()) - int(ts) > STATE_TTL:
        return None
    return int(tg)


def auth_url(tg_id):
    return AUTH_URL + "?" + urlencode({
        "client_id": app_id(), "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPES, "state": make_state(tg_id), "enable_fb_login": "0", "force_reauth": "true"})


# ---------------- tokenlar ----------------

def _check(r):
    try:
        data = r.json()
    except ValueError:
        raise IGError(f"Instagram javobi o'qilmadi (HTTP {r.status_code})")
    err = data.get("error") if isinstance(data, dict) else None
    if r.status_code != 200 or err:
        err = err if isinstance(err, dict) else {"message": data.get("error_message") or str(err)}
        code = err.get("code")
        raise IGError(err.get("message") or f"HTTP {r.status_code}", token_bad=code in (190, 102, 10, 200))
    return data


def exchange_code(code):
    """Kodni 60 kunlik tokenga almashtiradi: (token, ig_user_id, amal qilish soniyalari)."""
    code = (code or "").split("#")[0]
    short = _check(requests.post(TOKEN_URL, data={
        "client_id": app_id(), "client_secret": app_secret(), "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(), "code": code}, timeout=25))
    long = _check(requests.get(GRAPH + "/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": app_secret(),
        "access_token": short["access_token"]}, timeout=25))
    return long["access_token"], str(short.get("user_id") or ""), int(long.get("expires_in") or 5_184_000)


def refresh(token):
    data = _check(requests.get(GRAPH + "/refresh_access_token", params={
        "grant_type": "ig_refresh_token", "access_token": token}, timeout=25))
    return data["access_token"], int(data.get("expires_in") or 5_184_000)


# ---------------- ma'lumot olish ----------------

def profile(token):
    return _check(requests.get(API + "/me", params={
        "fields": "user_id,username,account_type,followers_count,follows_count,media_count",
        "access_token": token}, timeout=25))


def recent_media(token, limit=50):
    data = _check(requests.get(API + "/me/media", params={
        "fields": "id,permalink,timestamp,media_type,media_product_type,like_count,comments_count",
        "limit": limit, "access_token": token}, timeout=25))
    return data.get("data", [])


def media_insights(token, media_id):
    """{'views': .., 'reach': ..} — eski (akkaunt biznesga o'tishidan oldingi) postlar uchun {}."""
    data = None
    for metrics in (REEL_METRICS, "views,reach"):  # biror ko'rsatkich mos kelmasa — faqat asosiylari
        try:
            data = _check(requests.get(f"{API}/{media_id}/insights", params={
                "metric": metrics, "access_token": token}, timeout=25))
            break
        except IGError as e:
            if e.token_bad:
                raise
    if data is None:
        return {}
    out = {}
    for row in data.get("data", []):
        vals = row.get("values") or [{}]
        if isinstance(vals[0].get("value"), (int, float)):
            out[row["name"]] = int(vals[0]["value"])
    return out


def find_reel(token, shortcode):
    """Blogerning o'z postlari ichidan shortcode bo'yicha videoni topadi (Collab ham)."""
    for m in recent_media(token):
        if shortcode and f"/{shortcode}" in (m.get("permalink") or ""):
            return m
    return None


def account_metrics(token, max_insights=12):
    """vet.score uchun ma'lumot: obunachilar va oxirgi postlar (Reels ko'rishlari bilan)."""
    me = profile(token)
    posts, asked = [], 0
    for m in recent_media(token, limit=25):
        p = {"at": m.get("timestamp"), "likes": m.get("like_count"), "comments": m.get("comments_count"),
             "reel": m.get("media_product_type") == "REELS", "url": m.get("permalink")}
        if p["reel"] and asked < max_insights:
            asked += 1
            p["views"] = media_insights(token, m["id"]).get("views")
        posts.append(p)
    return {"source": "instagram_login", "username": me.get("username"), "followers": me.get("followers_count"),
            "following": me.get("follows_count"), "media_count": me.get("media_count"),
            "account_type": me.get("account_type"), "posts": posts}
