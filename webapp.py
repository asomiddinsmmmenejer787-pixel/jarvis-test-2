"""
webapp.py
---------
Telegram Mini App (Jarvis HUD) uchun backend.

- Har bir so'rov Telegram initData imzosi bilan tekshiriladi (HMAC, bot tokeni bilan).
  Faqat bot egasi kira oladi.
- Uzoq ishlar (Instagram tahlili, Jarvis javobi) "job" sifatida fon threadida bajariladi,
  interfeys esa ularning bosqichlarini jonli ko'rsatadi.
- Bosqichlar faqat umumiy nomlar (So'rov qabul qilindi, Tahlil qilinmoqda...),
  AI'ning ichki fikrlash jarayoni ko'rsatilmaydi.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import analytics
import config
import instagram_api as ig
from instagram_api import InstagramError

log = logging.getLogger("webapp")

router = APIRouter()
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
INIT_DATA_MAX_AGE = 24 * 3600

_ctx = {"jarvis": None, "responder": None, "get_owner_id": None, "bot_token": "", "started": time.time()}
_jobs = {}
_jobs_lock = threading.Lock()
_cache = {}


def init(jarvis, responder, get_owner_id, bot_token):
    _ctx.update(jarvis=jarvis, responder=responder, get_owner_id=get_owner_id, bot_token=bot_token)


def webapp_url():
    url = (os.environ.get("WEBAPP_URL") or "").strip()
    if url:
        return url
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    return f"https://{domain}/app" if domain else ""


# ---------- Telegram initData tekshiruvi ----------

def verify_init_data(init_data, bot_token, max_age=INIT_DATA_MAX_AGE):
    """To'g'ri bo'lsa Telegram user dict'ini, aks holda None qaytaradi."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def require_owner(init_data):
    user = verify_init_data(init_data, _ctx["bot_token"])
    if user is None:
        raise HTTPException(status_code=401, detail="Telegram orqali oching")
    owner = _ctx["get_owner_id"]() if _ctx["get_owner_id"] else None
    if owner is None or int(user["id"]) != int(owner):
        raise HTTPException(status_code=403, detail="Bu shaxsiy panel")
    return user


# ---------- yordamchilar ----------

def _cached(key, ttl, fn):
    item = _cache.get(key)
    if item and time.time() - item[0] < ttl:
        return item[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _report_path():
    return os.path.join(config.DATA_DIR, "last_report.json")


def _save_report(report):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_report_path() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
        os.replace(_report_path() + ".tmp", _report_path())
    except OSError:
        log.exception("Hisobotni saqlab bo'lmadi")


def _load_report():
    try:
        with open(_report_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_report(data, analysis=None):
    """analytics.collect() natijasini interfeys uchun ixcham JSON'ga aylantiradi."""
    posts = []
    for m in data["posts"]:
        posts.append({
            "kind": m["kind"], "date": m["dt"].strftime("%d.%m %H:%M") if m["dt"] else "",
            "views": m["views"], "reach": m["reach"], "likes": m["likes"], "comments": m["comments"],
            "shares": m["shares"], "saved": m["saved"], "er": m["er"], "save_rate": m["save_rate"],
            "share_rate": m["share_rate"], "avg_watch_s": m["avg_watch_s"], "hook": m["hook"],
            "permalink": m["permalink"],
        })
    audience = {}
    for key, rows in (data.get("audience") or {}).items():
        total = sum(v or 0 for _, v in rows) or 1
        audience[key] = [[analytics.GENDERS.get(lbl, lbl) if key == "gender" else lbl,
                          round((v or 0) * 100 / total)] for lbl, v in rows[:5]]
    p = data["profile"]
    return _jsonable({
        "created": datetime.now(analytics.TASHKENT).strftime("%d.%m.%Y %H:%M"),
        "profile": {"username": p.get("username"), "followers": p.get("followers_count"),
                    "media_count": p.get("media_count")},
        "followers_change": data.get("followers_change") or {},
        "account": data.get("account") or {},
        "formats": data.get("formats") or {},
        "slots": {k: {"count": c, "avg": v} for k, (c, v) in (data.get("slots") or {}).items()},
        "frequency": data.get("frequency") or {},
        "posts": posts,
        "stories": len(data.get("stories") or []),
        "audience": audience,
        "missing": data.get("missing") or [],
        "analysis": analysis,
    })


# ---------- jarayonlar (job) ----------

class JobError(Exception):
    """Foydalanuvchiga ko'rsatiladigan tushunarli xato."""


class Job:
    def __init__(self, kind, steps):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.steps = [{"key": k, "name": n, "state": "wait", "t": None} for k, n in steps]
        self.status = "running"
        self.result = None
        self.error = None
        self.started = time.time()
        self.finished = None

    def step(self, key):
        now = time.time()
        found = False
        for s in self.steps:
            if s["key"] == key:
                s["state"], s["t"], found = "active", now, True
                break
            if s["state"] in ("wait", "active"):
                if s["state"] == "active":
                    s["dur"] = round(now - (s["t"] or now), 1)
                s["state"] = "done"
        if not found:
            log.debug("Noma'lum bosqich: %s", key)

    def finish(self, result=None, error=None):
        now = time.time()
        for s in self.steps:
            if s["state"] == "active":
                s["dur"] = round(now - (s["t"] or now), 1)
                s["state"] = "error" if error else "done"
            elif s["state"] == "wait" and not error:
                s["state"] = "done"
        self.result, self.error = result, error
        self.status = "error" if error else "done"
        self.finished = now

    def view(self):
        return {"id": self.id, "kind": self.kind, "status": self.status, "steps": self.steps,
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "result": self.result, "error": self.error}


def start_job(kind, steps, fn):
    job = Job(kind, steps)
    with _jobs_lock:
        old = [k for k, j in _jobs.items() if j.finished and time.time() - j.finished > 3600]
        for k in old:
            _jobs.pop(k, None)
        _jobs[job.id] = job

    def run():
        try:
            job.step(steps[0][0])
            job.finish(result=fn(job))
        except InstagramError as e:
            job.finish(error=_ig_error(e))
        except JobError as e:
            job.finish(error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("Mini ilova jarayonida xato (%s)", kind)
            job.finish(error=f"Kutilmagan xato: {type(e).__name__}")

    threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
    return job


def _ig_error(e):
    if getattr(e, "code", None) == "no_token":
        return "Instagram ulanmagan. Botga /igtoken bilan token yuboring."
    if getattr(e, "fatal", False):
        return "Instagram token yaroqsiz. Botga yangi token yuboring: /igtoken IGAA..."
    if getattr(e, "rate_limited", False):
        return "Instagram limiti to'ldi, 30-60 daqiqadan keyin urinib ko'ring."
    return f"Instagram xatosi: {e}"


def _ai_text(text):
    """AI xatosini oddiy matndan ajratadi."""
    if isinstance(text, str) and text.startswith("[Gemini xatosi]"):
        return None, text.replace("[Gemini xatosi]", "").strip()
    return text, None


# ---------- sahifa ----------

@router.get("/app")
async def app_page():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


PRIVACY_HTML = """<!DOCTYPE html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Maxfiylik siyosati — Jarvis</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 18px;line-height:1.6;color:#1b2430}
h1{font-size:24px}h2{font-size:18px;margin-top:28px}</style></head><body>
<h1>Maxfiylik siyosati / Privacy Policy</h1>
<p>Jarvis — @bolalar_dunyosi.uzb Instagram akkauntining egasi uchun ichki SMM yordamchisi.
Ilovadan faqat akkaunt egasi foydalanadi.</p>
<h2>Qanday ma'lumotlar ishlatiladi</h2>
<p>Akkauntning o'z postlari statistikasi, postlarga yozilgan komentlar va ularga javob berish uchun zarur
ma'lumotlar (komment matni va foydalanuvchi nomi). Ma'lumotlar faqat statistika tahlili va komentlarga javob
berish uchun ishlatiladi, uchinchi shaxslarga sotilmaydi va berilmaydi.</p>
<h2>Saqlash</h2>
<p>Javob berilgan komentlarning identifikatorlari takror javob bermaslik uchun saqlanadi. Komment matnlari
ruxsat kutayotgan holatda vaqtincha saqlanadi va ko'rib chiqilgandan keyin o'chiriladi.</p>
<h2>Ma'lumotni o'chirish / Data deletion</h2>
<p>Ma'lumotlaringizni o'chirishni so'rash uchun @bolalar_dunyosi.uzb Instagram sahifasiga direkt orqali yozing.
So'rov 30 kun ichida bajariladi.</p>
<h2>English summary</h2>
<p>Jarvis is an internal assistant used only by the owner of the @bolalar_dunyosi.uzb Instagram account to view
the account's own insights and reply to comments on its posts. Data is not sold or shared with third parties.
To request deletion of your data, send a direct message to @bolalar_dunyosi.uzb on Instagram.</p>
</body></html>"""


@router.get("/privacy")
async def privacy_page():
    return HTMLResponse(PRIVACY_HTML)


# ---------- API ----------

@router.get("/api/status")
def api_status(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    ai = _ctx["jarvis"].ai_provider
    responder = _ctx["responder"]
    out = {
        "uptime": int(time.time() - _ctx["started"]),
        "ai": {"provider": type(ai).__name__.replace("Provider", ""),
               "model": getattr(ai, "_resolved_model", None) or getattr(ai, "model", "")},
        "autoreply": responder.status() if responder else None,
        "instagram": {"connected": ig.is_configured()},
        "report": _load_report(),
    }
    if ig.is_configured():
        try:
            profile = _cached("profile", 300, ig.get_profile)
            out["instagram"].update(
                username=profile.get("username"), followers=profile.get("followers_count"),
                media_count=profile.get("media_count"),
                change=analytics.followers_change(profile.get("followers_count")),
            )
            out["instagram"]["account"] = _cached("account7", 1800, lambda: ig.get_account_insights(7))
        except InstagramError as e:
            out["instagram"]["error"] = _ig_error(e)
    return JSONResponse(_jsonable(out))


INSTA_STEPS = [
    ("received", "So'rov qabul qilindi"),
    ("connect", "Instagram'ga ulanish"),
    ("collect", "Postlar, statistika va komentlar yig'ilmoqda"),
    ("metrics", "Ko'rsatkichlar hisoblanmoqda"),
    ("analyze", "Jarvis ma'lumotlarni tahlil qilmoqda"),
    ("prepare", "Hisobot tayyorlanmoqda"),
]


@router.post("/api/insta")
async def api_insta(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    body = await request.json() if await request.body() else {}
    limit = max(3, min(30, int(body.get("limit") or 12)))

    def work(job):
        job.step("connect")
        ig.get_profile()
        job.step("collect")
        data = analytics.collect(limit)
        job.step("metrics")
        if not data["posts"]:
            raise JobError("Akkauntda post topilmadi")
        job.step("analyze")
        text, err = _ai_text(analytics.ai_account_analysis(_ctx["jarvis"].ai_provider, data))
        job.step("prepare")
        report = build_report(data, text)
        report["analysis_error"] = err
        _save_report(report)
        return report

    return start_job("insta", INSTA_STEPS, work).view()


POST_STEPS = [
    ("received", "So'rov qabul qilindi"),
    ("find", "Post qidirilmoqda"),
    ("collect", "Statistika va komentlar yig'ilmoqda"),
    ("compare", "Akkaunt o'rtachasi bilan solishtirilmoqda"),
    ("analyze", "Jarvis postni tahlil qilmoqda"),
    ("prepare", "Xulosa tayyorlanmoqda"),
]


@router.post("/api/post")
async def api_post(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    ref = str((await request.json()).get("ref", "1")).strip() or "1"

    def work(job):
        job.step("find")
        post = analytics.find_post(ref)
        if post is None:
            raise JobError("Post topilmadi: raqam (1 = eng oxirgisi) yoki oxirgi 50 postdan birining havolasini yuboring")
        job.step("collect")
        d = analytics.collect_post(post)
        job.step("compare")
        summary = analytics.post_summary_text(d)
        job.step("analyze")
        text, err = _ai_text(analytics.ai_post_analysis(_ctx["jarvis"].ai_provider, d))
        job.step("prepare")
        m = d["post"]
        return _jsonable({
            "summary": summary, "analysis": text, "analysis_error": err,
            "post": {"kind": m["kind"], "permalink": m["permalink"], "views": m["views"], "er": m["er"],
                     "likes": m["likes"], "comments": m["comments"], "shares": m["shares"],
                     "saved": m["saved"], "hook": m["hook"]},
            "baseline": d["baseline_all"],
        })

    return start_job("post", POST_STEPS, work).view()


CHAT_STEPS = [
    ("received", "So'rov qabul qilindi"),
    ("understand", "So'rov tushunilmoqda"),
    ("context", "Oldingi suhbat konteksti olinmoqda"),
    ("generate", "Javob yaratilmoqda"),
    ("prepare", "Javob tayyorlanmoqda"),
]


@router.post("/api/chat")
async def api_chat(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    text = str((await request.json()).get("text", "")).strip()[:4000]
    if not text:
        raise HTTPException(status_code=400, detail="Bo'sh xabar")
    if ig_token_like(text):
        raise HTTPException(status_code=400, detail="Tokenni bu yerga emas, botga /igtoken bilan yuboring")

    def work(job):
        reply = _ctx["jarvis"].handle(text, progress=job.step)
        answer, err = _ai_text(reply)
        if err:
            raise JobError(f"Gemini javob bermadi: {err} (/diag bilan tekshiring)")
        return {"reply": answer}

    job = start_job("chat", CHAT_STEPS, work)
    return job.view()


def ig_token_like(text):
    return bool(re.search(r"IG[A-Za-z0-9_\-]{40,}", text))


@router.get("/api/job/{job_id}")
def api_job(job_id: str, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Jarayon topilmadi")
    return JSONResponse(_jsonable(job.view()))


@router.get("/api/stories")
def api_stories(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    try:
        followers = _cached("profile", 300, ig.get_profile).get("followers_count")
        out = []
        for s in ig.get_stories():
            try:
                s["insights"] = ig.get_media_insights(s)
            except InstagramError as e:
                if e.fatal or e.rate_limited:
                    raise
                s["insights"] = {}
            m = analytics.post_metrics(s, followers)
            out.append({"time": m["dt"].strftime("%H:%M") if m["dt"] else "", "views": m["views"],
                        "reach": m["reach"], "replies": m["replies"], "shares": m["shares"],
                        "profile_visits": m["profile_visits"], "follows": m["follows"],
                        "permalink": m["permalink"]})
        return {"stories": out}
    except InstagramError as e:
        raise HTTPException(status_code=502, detail=_ig_error(e))


# ---------- komentlar ----------

@router.get("/api/comments")
def api_comments(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    if responder is None:
        return {"enabled": False}
    from auto_reply import CATEGORY_NAMES
    items = [{**p, "category_name": CATEGORY_NAMES.get(p.get("category"), p.get("category"))}
             for p in responder.get_pending()]
    return {"enabled": True, "status": responder.status(), "pending": items}


@router.post("/api/comments/mode")
async def api_comments_mode(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    mode = str((await request.json()).get("mode", ""))
    if responder is None or mode not in ("on", "off", "test"):
        raise HTTPException(status_code=400, detail="Noto'g'ri rejim")
    responder.set_mode(mode)
    return {"status": responder.status()}


@router.post("/api/comments/{cid}")
def api_comments_action(cid: str, body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    if responder is None:
        raise HTTPException(status_code=400, detail="Komentlarga javob tizimi o'chiq")
    action = body.get("action")
    try:
        if action == "send":
            text = (body.get("text") or "").strip() or None
            msg = responder.approve(cid, text)
        elif action == "hide":
            msg = responder.hide(cid)
        elif action == "skip":
            msg = responder.reject(cid)
        else:
            raise HTTPException(status_code=400, detail="Noma'lum amal")
    except InstagramError as e:
        raise HTTPException(status_code=502, detail=_ig_error(e))
    return {"message": msg}
