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
import audit
import brain
import followers_live
import catalog
from ai.base import is_error_text
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


def init(jarvis, responder, get_owner_id, bot_token, chat=None):
    _ctx.update(jarvis=jarvis, responder=responder, get_owner_id=get_owner_id, bot_token=bot_token, chat=chat)


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
    if is_error_text(text):
        return None, text.split("]", 1)[1].strip()
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


@router.get("/audit/{key}")
def audit_page(key: str, since: int = 0, limit: int = 300, kinds: str = ""):
    """Ishlab chiquvchi uchun maxfiy kuzatuv sahifasi (kalit /kuzatuv buyrug'ida)."""
    from fastapi.responses import PlainTextResponse
    if not audit.key_ok(key):
        raise HTTPException(status_code=404, detail="Not Found")
    status = None
    responder = _ctx.get("responder")
    if responder is not None:
        status = responder.status()
        status["lessons"] = brain.get_lessons()
        chat = _ctx.get("chat")
        status["chat_mode"] = chat.mode if chat is not None else None
    ks = tuple(k for k in kinds.split(",") if k) or None
    text = audit.text_dump(since, max(1, min(1000, limit)), ks, status)
    if responder is not None and since == 0:
        def row(it, keys):
            return json.dumps({k: (it.get(k)[:300] if isinstance(it.get(k), str) else it.get(k)) for k in keys},
                              ensure_ascii=False, default=str)
        parts = ["\n\nRUXSAT KUTAYOTGAN KOMENTLAR:"]
        parts += [row(i, ("cid", "created", "username", "text", "category", "reason", "reply", "permalink"))
                  for i in responder.get_pending()]
        parts.append("\nRUXSAT KUTAYOTGAN DIREKT (komentdan keyin):")
        parts += [row(i, ("cid", "created", "username", "comment", "topic", "reason", "text"))
                  for i in responder.get_dm_pending()]
        chat = _ctx.get("chat")
        if chat is not None:
            parts.append("\nRUXSAT KUTAYOTGAN DIREKT SUHBATLAR:")
            parts += [row(i, ("key", "created", "username", "history", "text", "kind", "reason"))
                      for i in chat.get_pending()]
        text += "\n".join(parts)
    return PlainTextResponse(text)


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
        responder, chat = _ctx.get("responder"), _ctx.get("chat")
        if responder is not None and not text.startswith("/") and ig.is_configured():
            try:
                status, last_post, hist = brain.build_context(responder, chat, _ctx["jarvis"].memory)
                res = brain.think(_ctx["jarvis"].ai_provider, text, status, last_post, hist)
            except Exception as e:  # noqa: BLE001
                log.warning("Xabarni tahlil qilib bo'lmadi: %s", e)
                res = {"kind": "chat"}
            if res.get("kind") == "work":
                lines, recheck = brain.apply(res, responder, chat)
                reply = "\n".join(lines)
                memory = _ctx["jarvis"].memory
                memory.add("user", text)
                memory.add("jarvis", reply)
                brain.log_entry(f"Egasi (mini ilova): «{text[:100]}»")
                if recheck:
                    def bg():
                        try:
                            for ev in responder.recheck(recheck)[0]:
                                brain.log_event(ev)
                        except Exception:  # noqa: BLE001
                            log.exception("Qayta tekshiruv xatosi")
                    threading.Thread(target=bg, daemon=True).start()
                return {"reply": reply}
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
    chat = _ctx.get("chat")
    return {"enabled": True, "status": responder.status(), "pending": items,
            "dm_pending": responder.get_dm_pending(),
            "chat": {"status": chat.status(), "pending": chat.get_pending()} if chat else None}


@router.post("/api/comments/mode")
async def api_comments_mode(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    mode = str((await request.json()).get("mode", ""))
    if responder is None or mode not in ("on", "off", "test"):
        raise HTTPException(status_code=400, detail="Noto'g'ri rejim")
    responder.set_mode(mode)
    return {"status": responder.status()}


@router.post("/api/comments/limit")
async def api_comments_limit(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    if responder is None:
        raise HTTPException(status_code=400, detail="Komentga javob tizimi o'chiq")
    try:
        n = int((await request.json()).get("limit"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Limit raqam bo'lsin")
    n = responder.set_day_limit(n)
    brain.log_entry(f"Kunlik komment limiti {n} ga o'zgartirildi (mini ilova)")
    return {"status": responder.status()}


@router.get("/api/followers")
def api_followers(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    out = {"connected": ig.is_configured()}
    if not out["connected"]:
        return out
    try:
        followers_live.sample(40)
        out["instagram_today"] = followers_live.instagram_today()
    except InstagramError as e:
        out["error"] = _ig_error(e)
    out.update(followers_live.view())
    out["now"] = int(time.time())
    return JSONResponse(_jsonable(out))


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


# ---------- direkt ----------

@router.post("/api/dm/mode")
async def api_dm_mode(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    mode = str((await request.json()).get("mode", ""))
    if responder is None or mode not in ("approve", "auto", "off"):
        raise HTTPException(status_code=400, detail="Noto'g'ri rejim")
    responder.set_dm_mode(mode)
    return {"status": responder.status()}


@router.post("/api/dm/{cid}")
def api_dm_action(cid: str, body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    responder = _ctx["responder"]
    if responder is None:
        raise HTTPException(status_code=400, detail="Tizim o'chiq")
    try:
        if body.get("action") == "send":
            msg = responder.approve_dm(cid, (body.get("text") or "").strip() or None)
        elif body.get("action") == "skip":
            msg = responder.reject_dm(cid)
        else:
            raise HTTPException(status_code=400, detail="Noma'lum amal")
    except InstagramError as e:
        raise HTTPException(status_code=502, detail=_ig_error(e))
    return {"message": msg}


# ---------- postlar bo'yicha ma'lumot ----------

@router.get("/api/posts")
def api_posts(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    data = catalog.load()
    out = {"shop": data["shop"], "memory": data["memory"], "posts": []}
    if not ig.is_configured():
        out["posts_error"] = "Instagram ulanmagan"
        return out
    try:
        recent = _cached("hud_posts", 120, lambda: ig.get_recent_posts(30))
    except InstagramError as e:
        out["posts_error"] = _ig_error(e)
        return out
    for p in recent:
        facts = catalog.post_facts(p)
        out["posts"].append({
            "id": p.get("id"), "kind": analytics.kind_name(p),
            "time": catalog._post_time(p),
            "thumb": p.get("thumbnail_url") or p.get("media_url") or "",
            "caption": p.get("caption") or "", "permalink": p.get("permalink"),
            "comments": p.get("comments_count"),
            "info": catalog.get_post_info(p.get("id")),
            "has_price": facts["has_price"],
            "reply_off": catalog.is_reply_off(p.get("id")),
        })
    return out


@router.post("/api/posts/{media_id}/reply")
def api_post_reply(media_id: str, body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    off = bool(body.get("off"))
    catalog.set_reply_off(media_id, off)
    brain.log_entry(f"Post {media_id} komment javobi: {'ochirildi' if off else 'yoqildi'} (mini ilova)")
    return {"reply_off": off}


@router.post("/api/posts/{media_id}/info")
def api_post_info(media_id: str, body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    return {"info": catalog.set_post_info(media_id, body)}


@router.get("/api/tasks")
def api_tasks(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    data = catalog.load()
    out = {"global": data.get("task", ""), "posts": []}
    if ig.is_configured():
        try:
            for p in _cached("hud_posts", 120, lambda: ig.get_recent_posts(30))[:15]:
                out["posts"].append({
                    "id": p.get("id"), "kind": analytics.kind_name(p), "time": catalog._post_time(p),
                    "caption": (p.get("caption") or "")[:90], "comments": p.get("comments_count"),
                    "task": catalog.get_post_info(p.get("id"))["task"],
                })
        except InstagramError as e:
            out["posts_error"] = _ig_error(e)
    return out


@router.post("/api/tasks")
def api_tasks_set(body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    media_id = body.get("media_id") or None
    catalog.set_task(str(media_id) if media_id else None, body.get("task", ""))
    return {"ok": True}


@router.post("/api/memory")
def api_memory_add(body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    try:
        return {"memory": catalog.add_memory(body.get("text", ""))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/memory/{index}")
def api_memory_delete(index: int, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    try:
        catalog.delete_memory(index)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"memory": catalog.get_memory()}


@router.post("/api/shop")
def api_shop(body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    return {"shop": catalog.set_shop(body)}


# ---------- direkt yozishmalar ----------

@router.post("/api/inbox/mode")
async def api_inbox_mode(request: Request, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    chat = _ctx.get("chat")
    mode = str((await request.json()).get("mode", ""))
    if chat is None or mode not in ("approve", "auto", "off"):
        raise HTTPException(status_code=400, detail="Noto'g'ri rejim")
    chat.set_mode(mode)
    return {"status": chat.status()}


@router.post("/api/inbox/{key}")
def api_inbox_action(key: str, body: dict, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    chat = _ctx.get("chat")
    if chat is None:
        raise HTTPException(status_code=400, detail="Tizim o'chiq")
    try:
        if body.get("action") == "send":
            msg = chat.approve(key, (body.get("text") or "").strip() or None)
        elif body.get("action") == "skip":
            msg = chat.reject(key)
        else:
            raise HTTPException(status_code=400, detail="Noma'lum amal")
    except InstagramError as e:
        raise HTTPException(status_code=502, detail=_ig_error(e))
    return {"message": msg}
