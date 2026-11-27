"""
analytics.py
------------
Instagram kontent tahlili.

1) Instagram'dan ma'lumot yig'adi (profil, postlar, statistika, komentlar,
   storylar, auditoriya).
2) Raqamlarni kod bilan hisoblaydi (ER, saqlash/ulashish ulushi, format
   bo'yicha o'rtacha, eng yaxshi/zaif postlar) - AI raqam to'qimasligi uchun.
3) Tayyor raqamlarni Gemini'ga beradi va aniq xulosa/tavsiya oladi.
"""

import json
import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone

import accounts
import config
import instagram_api as ig
from instagram_api import InstagramError

log = logging.getLogger("analytics")

TASHKENT = timezone(timedelta(hours=5))
WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
MONTHS = ["yan", "fev", "mar", "apr", "may", "iyun", "iyul", "avg", "sen", "okt", "noy", "dek"]
GENDERS = {"F": "Ayol", "M": "Erkak", "U": "Noma'lum"}

CTA_RE = re.compile(
    r"direkt|direct|izoh|koment|komment|yozing|yozib|bio|link|buyurtma|qo['’`]ng['’`]iroq|"
    r"telefon|manzil|saqlab|saqlang|ulashing|belgilang|do['’`]stingiz|пишите|директ|ссылк|"
    r"заказ|звон|комментар|сохрани",
    re.IGNORECASE,
)

ANALYST_RULES = """
Sen Jarvis — Asomiddinning shaxsiy SMM va marketing tahlilchisisan. Instagram ma'lumotlarini tahlil qilasan.

QOIDALAR
- Faqat berilgan raqamlarga tayan. Raqam, sabab yoki fakt to'qima. Ma'lumot yetmasa, buni ochiq ayt.
- Har bir xulosani raqam bilan asosla (masalan: "Reels o'rtacha 12 000 ko'rish, karusel 3 000").
- 1-2 postdan qat'iy qonuniyat chiqarma, bunday xulosani "taxmin" deb belgila.
- Umumiy maslahat berma ("sifatli kontent qiling", "muntazam joylang" kabi). Har tavsiya aniq, bajariladigan va aynan shu akkauntga xos bo'lsin.
- ER = (like + koment + ulashish + saqlash) / qamrov. Saqlash va ulashish kontent qiymatining eng kuchli signali. Ko'rish/obunachi nisbati algoritm kontentni obunachilardan tashqariga tarqatayotganini ko'rsatadi.
- Reels uchun o'rtacha tomosha vaqti hook va ushlab turish sifatini ko'rsatadi.
- O'zbek tilida, lotin yozuvida yoz. Markdown belgilarini (**, ##, __) ishlatma, oddiy matn yoz. Bo'lim sarlavhalarini katta harf bilan yoz, ro'yxat uchun raqam yoki tire ishlat.
""".strip()

ACCOUNT_STRUCTURE = """
JAVOB TUZILISHI
1. UMUMIY HOLAT: 2-3 gap.
2. NIMA ISHLAYAPTI: 3-4 band, raqam bilan.
3. NIMA ISHLAMAYAPTI: 2-4 band, raqam bilan.
4. ENG YAXSHI VA ENG ZAIF KONTENT: nega shunday natija bergani (hook, format, mavzu, joylash vaqti).
5. AUDITORIYA VA KOMENTLAR: kimlar kuzatyapti, nima so'rashyapti, qanday e'tiroz yoki sotuv signali bor.
6. KEYINGI 7 KUN UCHUN 5 TA ANIQ HARAKAT.
7. 3 TA KONTENT G'OYASI: har biriga format, hook (birinchi 2 soniyadagi matn yoki kadr) va nima uchun ishlashi.
""".strip()

POST_STRUCTURE = """
JAVOB TUZILISHI
1. NATIJA: akkaunt o'rtachasiga nisbatan necha barobar yuqori yoki past (ko'rish, ER, saqlash, ulashish).
2. NEGA SHUNDAY BO'LDI: hook, format, caption, joylash vaqti, mavzu bo'yicha.
3. KOMENTLAR: odamlar nima deyapti, nima so'rayapti, sotuvga signal bormi.
4. NIMANI TAKRORLASH, NIMANI O'ZGARTIRISH: aniq bandlar.
5. QAYTA ISHLASH G'OYASI: shu mavzuda yangi kontent, hook, tuzilish va CTA bilan.
""".strip()


# ---------- formatlash ----------

def n(value):
    """12345 -> '12 345'."""
    if value is None:
        return "-"
    try:
        return f"{int(round(float(value))):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def pct(value):
    return "-" if value is None else f"{value * 100:.1f}%"


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _local_time(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(TASHKENT)
    except (TypeError, ValueError):
        return None


def _date_label(dt):
    return f"{dt.day}-{MONTHS[dt.month - 1]}" if dt else "?"


def kind_name(media):
    if (media.get("media_product_type") or "").upper() == "REELS":
        return "Reels"
    if (media.get("media_product_type") or "").upper() == "STORY":
        return "Story"
    return {
        "CAROUSEL_ALBUM": "Karusel",
        "IMAGE": "Rasm",
        "VIDEO": "Video",
    }.get((media.get("media_type") or "").upper(), "Post")


def hook_of(caption):
    for line in (caption or "").splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return ""


def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _safe(fn, label, errors, default=None):
    """Ixtiyoriy qismni oladi. Instagram bermasa, tahlil to'xtamaydi."""
    try:
        return fn()
    except InstagramError as e:
        if e.fatal or e.rate_limited:
            raise
        log.info("%s olinmadi: %s", label, e)
        errors.append(label)
        return default


# ---------- obunachilar tarixi ----------

def _history_path():
    return accounts.path("ig_history.json")


def record_followers(count):
    if count is None:
        return
    path = _history_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    followers = data.setdefault("followers", {})
    followers[datetime.now(TASHKENT).strftime("%Y-%m-%d")] = int(count)
    data["followers"] = dict(sorted(followers.items())[-400:])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log.warning("Obunachilar tarixini saqlab bo'lmadi: %s", e)


def followers_change(current):
    """{7: +120, 30: +540} - shu kunlar oldingi qayd bilan farq."""
    try:
        with open(_history_path(), encoding="utf-8") as f:
            history = json.load(f).get("followers", {})
    except (OSError, ValueError):
        return {}
    if current is None or not history:
        return {}
    today = datetime.now(TASHKENT).date()
    out = {}
    for days in (7, 30):
        target = today - timedelta(days=days)
        older = [(d, v) for d, v in history.items()
                 if datetime.strptime(d, "%Y-%m-%d").date() <= target]
        if older:
            out[days] = int(current) - older[-1][1]
    return out


# ---------- hisob-kitob ----------

def post_metrics(post, followers=None):
    ins = post.get("insights") or {}
    likes = _to_float(ins.get("likes", post.get("like_count")))
    comments = _to_float(ins.get("comments", post.get("comments_count")))
    shares = _to_float(ins.get("shares"))
    saved = _to_float(ins.get("saved"))
    reach = _to_float(ins.get("reach"))
    views = _to_float(ins.get("views"))
    interactions = _to_float(ins.get("total_interactions"))
    if interactions is None:
        parts = [v for v in (likes, comments, shares, saved) if v is not None]
        interactions = sum(parts) if parts else None

    watch_ms = _to_float(ins.get("ig_reels_avg_watch_time"))
    caption = post.get("caption") or ""
    dt = _local_time(post.get("timestamp"))

    def ratio(a, b):
        return a / b if a is not None and b else None

    return {
        "id": post.get("id"),
        "kind": kind_name(post),
        "permalink": post.get("permalink") or "",
        "dt": dt,
        "views": views,
        "reach": reach,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "saved": saved,
        "interactions": interactions,
        "er": ratio(interactions, reach),
        "save_rate": ratio(saved, reach),
        "share_rate": ratio(shares, reach),
        "views_per_follower": ratio(views, followers),
        "avg_watch_s": watch_ms / 1000 if watch_ms is not None else None,
        "profile_visits": _to_float(ins.get("profile_visits")),
        "follows": _to_float(ins.get("follows")),
        "replies": _to_float(ins.get("replies")),
        "hook": hook_of(caption),
        "caption_len": len(caption),
        "hashtags": len(re.findall(r"#\w+", caption)),
        "has_cta": bool(CTA_RE.search(caption)),
        "comment_samples": post.get("comment_samples") or [],
        "has_insights": bool(ins),
    }


def by_format(metrics):
    groups = {}
    for m in metrics:
        groups.setdefault(m["kind"], []).append(m)
    out = {}
    for kind, items in groups.items():
        out[kind] = {
            "count": len(items),
            "views": _avg(m["views"] for m in items),
            "reach": _avg(m["reach"] for m in items),
            "er": _avg(m["er"] for m in items),
            "save_rate": _avg(m["save_rate"] for m in items),
            "share_rate": _avg(m["share_rate"] for m in items),
            "avg_watch_s": _avg(m["avg_watch_s"] for m in items),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))


def _perf(m):
    """Saralash uchun asosiy natija: ko'rish, bo'lmasa qamrov, bo'lmasa like."""
    for key in ("views", "reach", "likes"):
        if m.get(key) is not None:
            return m[key]
    return 0


def time_slots(metrics):
    slots = {"Ertalab (6-12)": [], "Kunduzi (12-17)": [], "Kechqurun (17-22)": [], "Tunda (22-6)": []}
    for m in metrics:
        if not m["dt"]:
            continue
        h = m["dt"].hour
        key = ("Ertalab (6-12)" if 6 <= h < 12 else "Kunduzi (12-17)" if 12 <= h < 17
               else "Kechqurun (17-22)" if 17 <= h < 22 else "Tunda (22-6)")
        slots[key].append(_perf(m))
    return {k: (len(v), _avg(v)) for k, v in slots.items() if v}


def posting_frequency(metrics):
    now = datetime.now(TASHKENT)
    dts = [m["dt"] for m in metrics if m["dt"]]
    return {
        "7": sum(1 for d in dts if now - d <= timedelta(days=7)),
        "30": sum(1 for d in dts if now - d <= timedelta(days=30)),
        "covers_30": bool(dts) and now - min(dts) >= timedelta(days=30),
    }


# ---------- ma'lumot yig'ish ----------

def collect(limit=12, comments_per_post=5):
    errors = []
    profile = ig.get_profile()
    followers = profile.get("followers_count")
    record_followers(followers)

    posts = ig.get_recent_posts(limit)
    for post in posts:
        post["insights"] = _safe(lambda p=post: ig.get_media_insights(p), "post statistikasi", errors, {})
        if comments_per_post and post.get("comments_count"):
            comments = _safe(
                lambda p=post: ig.get_all_comments(p["id"], fields="id,text,username,timestamp",
                                                   limit=comments_per_post),
                "komentlar", errors, [],
            )
            post["comment_samples"] = [
                (c.get("text") or "").strip()[:150] for c in comments if (c.get("text") or "").strip()
            ]

    stories = _safe(ig.get_stories, "storylar", errors, []) or []
    for story in stories:
        story["insights"] = _safe(lambda s=story: ig.get_media_insights(s), "story statistikasi", errors, {})

    metrics = [post_metrics(p, followers) for p in posts]
    return {
        "profile": profile,
        "followers_change": followers_change(followers),
        "posts": metrics,
        "stories": [post_metrics(s, followers) for s in stories],
        "account": _safe(lambda: ig.get_account_insights(7), "akkaunt statistikasi", errors, {}) or {},
        "audience": _safe(ig.get_audience, "auditoriya", errors, {}) or {},
        "formats": by_format(metrics),
        "slots": time_slots(metrics),
        "frequency": posting_frequency(metrics),
        "missing": sorted(set(errors)),
    }


# ---------- matn ko'rinishi ----------

def _post_line(m, index=None):
    head = f"{index}. " if index is not None else ""
    parts = [f"{head}{m['kind']}, {_date_label(m['dt'])}"]
    if m["views"] is not None:
        parts.append(f"{n(m['views'])} ko'rish")
    elif m["likes"] is not None:
        parts.append(f"{n(m['likes'])} like")
    if m["er"] is not None:
        parts.append(f"ER {pct(m['er'])}")
    line = ", ".join(parts)
    if m["hook"]:
        line += f'\n   "{m["hook"][:70]}"'
    if m["permalink"]:
        line += f"\n   {m['permalink']}"
    return line


def _audience_text(audience):
    lines = []
    names = {"city": "Shaharlar", "age": "Yosh", "gender": "Jins"}
    for key in ("city", "age", "gender"):
        rows = audience.get(key)
        if not rows:
            continue
        total = sum(v or 0 for _, v in rows) or 1
        items = []
        for label, value in rows[:4]:
            if key == "gender":
                label = GENDERS.get(label, label)
            items.append(f"{label} {round((value or 0) * 100 / total)}%")
        lines.append(f"{names[key]}: " + ", ".join(items))
    return lines


def summary_text(data, title=None):
    p = data["profile"]
    posts = data["posts"]
    out = [title or f"📊 @{p.get('username', '?')} — oxirgi {len(posts)} ta kontent"]

    change = data["followers_change"]
    line = f"Obunachilar: {n(p.get('followers_count'))}"
    if 7 in change:
        line += f" (7 kunda {change[7]:+d})"
    elif 30 in change:
        line += f" (30 kunda {change[30]:+d})"
    out.append(line)
    freq = data["frequency"]
    freq_line = f"Joylash: oxirgi 7 kunda {freq['7']} ta"
    if freq["covers_30"]:
        freq_line += f", 30 kunda {freq['30']} ta"
    out.append(freq_line)

    acc = data["account"]
    if acc:
        bits = []
        for key, label in (("reach", "qamrov"), ("views", "ko'rish"),
                           ("accounts_engaged", "faol auditoriya"),
                           ("profile_links_taps", "profildagi tugma/link bosishlar")):
            if acc.get(key) is not None:
                bits.append(f"{label} {n(acc[key])}")
        if bits:
            out.append("\n7 kunlik akkaunt: " + " | ".join(bits))

    if data["formats"]:
        out.append("\nFormat bo'yicha (o'rtacha):")
        for kind, f in data["formats"].items():
            bits = [f"{kind} ({f['count']})"]
            if f["views"] is not None:
                bits.append(f"{n(f['views'])} ko'rish")
            if f["er"] is not None:
                bits.append(f"ER {pct(f['er'])}")
            if f["save_rate"] is not None:
                bits.append(f"saqlash {pct(f['save_rate'])}")
            if f["share_rate"] is not None:
                bits.append(f"ulashish {pct(f['share_rate'])}")
            if f["avg_watch_s"] is not None:
                bits.append(f"tomosha {f['avg_watch_s']:.1f}s")
            out.append(" | ".join(bits))

    ranked = sorted(posts, key=_perf, reverse=True)
    if ranked:
        out.append("\n🔝 Eng kuchli:")
        out += [_post_line(m, i) for i, m in enumerate(ranked[:3], 1)]
    if len(ranked) > 5:
        out.append("\n🔻 Eng zaif:")
        out += [_post_line(m, i) for i, m in enumerate(ranked[-2:], 1)]

    if data["stories"]:
        views = [s["views"] for s in data["stories"] if s["views"] is not None]
        out.append(f"\nFaol storylar: {len(data['stories'])} ta"
                   + (f", o'rtacha {n(_avg(views))} ko'rish" if views else ""))

    aud = _audience_text(data["audience"])
    if aud:
        out.append("\nAuditoriya:")
        out += aud

    if data["missing"]:
        out.append("\nℹ️ Instagram bermagan ma'lumotlar: " + ", ".join(data["missing"]))
    return "\n".join(out)


def _metrics_block(m, idx):
    dt = m["dt"]
    when = f"{dt:%Y-%m-%d} {WEEKDAYS[dt.weekday()]} {dt:%H:%M}" if dt else "?"
    nums = []
    for key, label in (("views", "ko'rish"), ("reach", "qamrov"), ("likes", "like"),
                       ("comments", "koment"), ("shares", "ulashish"), ("saved", "saqlash"),
                       ("profile_visits", "profilga o'tish"), ("follows", "obuna bo'ldi"),
                       ("replies", "javob")):
        if m.get(key) is not None:
            nums.append(f"{label} {n(m[key])}")
    if m["er"] is not None:
        nums.append(f"ER {pct(m['er'])}")
    if m["save_rate"] is not None:
        nums.append(f"saqlash ulushi {pct(m['save_rate'])}")
    if m["share_rate"] is not None:
        nums.append(f"ulashish ulushi {pct(m['share_rate'])}")
    if m["views_per_follower"] is not None:
        nums.append(f"ko'rish/obunachi {m['views_per_follower']:.2f}")
    if m["avg_watch_s"] is not None:
        nums.append(f"o'rtacha tomosha {m['avg_watch_s']:.1f}s")
    lines = [f"#{idx} | {m['kind']} | {when} | " + " | ".join(nums)]
    if not m["has_insights"]:
        lines.append("   (statistika berilmagan, faqat like/koment bor)")
    lines.append(f"   Hook: \"{m['hook']}\"" if m["hook"] else "   Hook: (caption yo'q)")
    lines.append(f"   Caption: {m['caption_len']} belgi, {m['hashtags']} hashtag, CTA: "
                 + ("bor" if m["has_cta"] else "yo'q"))
    if m["comment_samples"]:
        lines.append("   Komentlar: " + " || ".join(f'"{c}"' for c in m["comment_samples"][:5]))
    return "\n".join(lines)


def ai_data_text(data):
    p = data["profile"]
    lines = [
        f"AKKAUNT: @{p.get('username', '?')} | obunachi {n(p.get('followers_count'))} | "
        f"jami post {n(p.get('media_count'))} | bio: {(p.get('biography') or '')[:200]}",
    ]
    if data["followers_change"]:
        lines.append("OBUNACHI O'ZGARISHI: " + ", ".join(
            f"{d} kunda {v:+d}" for d, v in data["followers_change"].items()))
    if data["account"]:
        lines.append("7 KUNLIK AKKAUNT: " + ", ".join(f"{k} {n(v)}" for k, v in data["account"].items()))
    aud = _audience_text(data["audience"])
    if aud:
        lines.append("AUDITORIYA: " + " ; ".join(aud))
    freq = data["frequency"]
    lines.append(f"JOYLASH CHASTOTASI: 7 kunda {freq['7']} ta"
                 + (f", 30 kunda {freq['30']} ta" if freq["covers_30"] else ""))
    if data["formats"]:
        lines.append("FORMAT BO'YICHA O'RTACHA:")
        for kind, f in data["formats"].items():
            lines.append(
                f"- {kind}: {f['count']} ta, ko'rish {n(f['views'])}, qamrov {n(f['reach'])}, "
                f"ER {pct(f['er'])}, saqlash {pct(f['save_rate'])}, ulashish {pct(f['share_rate'])}"
                + (f", tomosha {f['avg_watch_s']:.1f}s" if f["avg_watch_s"] is not None else "")
            )
    if data["slots"]:
        lines.append("JOYLASH VAQTI BO'YICHA O'RTACHA NATIJA (Toshkent vaqti): " + "; ".join(
            f"{k}: {c} ta post, o'rtacha {n(v)}" for k, (c, v) in data["slots"].items()))
    lines.append("\nKONTENTLAR (yangidan eskiga):")
    lines += [_metrics_block(m, i) for i, m in enumerate(data["posts"], 1)]
    if data["stories"]:
        lines.append("\nFAOL STORYLAR:")
        lines += [_metrics_block(m, i) for i, m in enumerate(data["stories"], 1)]
    if data["missing"]:
        lines.append("\nINSTAGRAM BERMAGAN MA'LUMOTLAR: " + ", ".join(data["missing"]))
    return "\n".join(lines)


def ai_account_analysis(ai, data):
    message = "Quyidagi Instagram ma'lumotlarini tahlil qil.\n\n" + ai_data_text(data)
    return ai.generate_text(ANALYST_RULES + "\n\n" + ACCOUNT_STRUCTURE, message)


# ---------- bitta post ----------

def find_post(ref, search_limit=50):
    """ref: 1 = eng oxirgi post, yoki post havolasi (instagram.com/p/... yoki /reel/...)."""
    ref = (ref or "").strip()
    if ref.isdigit():
        index = int(ref)
        if index < 1:
            return None
        posts = ig.get_recent_posts(index)
        return posts[index - 1] if len(posts) >= index else None
    match = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", ref)
    if not match:
        return None
    code = match.group(1)
    for post in ig.get_recent_posts(search_limit):
        if f"/{code}" in (post.get("permalink") or ""):
            return post
    return None


def collect_post(post, baseline_count=20):
    errors = []
    profile = ig.get_profile()
    followers = profile.get("followers_count")
    post["insights"] = _safe(lambda: ig.get_media_insights(post), "post statistikasi", errors, {})
    comments = _safe(lambda: ig.get_all_comments(post["id"], fields="id,text,username,timestamp", limit=40),
                     "komentlar", errors, []) or []
    post["comment_samples"] = [(c.get("text") or "").strip()[:200] for c in comments if c.get("text")]

    baseline_posts = [p for p in ig.get_recent_posts(baseline_count) if p.get("id") != post.get("id")]
    for p in baseline_posts:
        p["insights"] = _safe(lambda x=p: ig.get_media_insights(x), "post statistikasi", errors, {})
    baseline = [post_metrics(p, followers) for p in baseline_posts]
    return {
        "profile": profile,
        "post": post_metrics(post, followers),
        "baseline": by_format(baseline),
        "baseline_all": {
            "views": _avg(m["views"] for m in baseline),
            "er": _avg(m["er"] for m in baseline),
            "save_rate": _avg(m["save_rate"] for m in baseline),
            "share_rate": _avg(m["share_rate"] for m in baseline),
            "median_views": statistics.median([m["views"] for m in baseline if m["views"] is not None])
            if any(m["views"] is not None for m in baseline) else None,
            "count": len(baseline),
        },
        "missing": sorted(set(errors)),
    }


def post_summary_text(d):
    m = d["post"]
    base = d["baseline_all"]
    out = [f"🔎 {m['kind']}, {_date_label(m['dt'])}", m["permalink"]]
    rows = []
    for key, label in (("views", "Ko'rish"), ("reach", "Qamrov"), ("likes", "Like"),
                       ("comments", "Koment"), ("shares", "Ulashish"), ("saved", "Saqlash"),
                       ("profile_visits", "Profilga o'tish"), ("follows", "Obuna bo'ldi")):
        if m.get(key) is not None:
            rows.append(f"{label}: {n(m[key])}")
    out += rows
    if m["er"] is not None:
        line = f"ER: {pct(m['er'])}"
        if base["er"]:
            line += f" (o'rtacha {pct(base['er'])})"
        out.append(line)
    if m["views"] is not None and base["median_views"]:
        out.append(f"Odatdagi postdan {m['views'] / base['median_views']:.1f} barobar ko'p ko'rilgan"
                   if m["views"] >= base["median_views"]
                   else f"Odatdagi postdan {base['median_views'] / max(m['views'], 1):.1f} barobar kam ko'rilgan")
    if m["avg_watch_s"] is not None:
        out.append(f"O'rtacha tomosha: {m['avg_watch_s']:.1f} soniya")
    if d["missing"]:
        out.append("ℹ️ Instagram bermagan: " + ", ".join(d["missing"]))
    return "\n".join(out)


def ai_post_analysis(ai, d):
    base = d["baseline_all"]
    lines = [
        f"AKKAUNT: @{d['profile'].get('username', '?')} | obunachi {n(d['profile'].get('followers_count'))}",
        f"SOLISHTIRISH UCHUN oxirgi {base['count']} ta post o'rtachasi: ko'rish {n(base['views'])}, "
        f"mediana ko'rish {n(base['median_views'])}, ER {pct(base['er'])}, "
        f"saqlash {pct(base['save_rate'])}, ulashish {pct(base['share_rate'])}",
        "FORMAT BO'YICHA O'RTACHA: " + "; ".join(
            f"{k}: {f['count']} ta, ko'rish {n(f['views'])}, ER {pct(f['er'])}" for k, f in d["baseline"].items()),
        "\nTAHLIL QILINADIGAN POST:",
        _metrics_block(d["post"], 1),
    ]
    if d["post"]["comment_samples"]:
        lines.append("\nBARCHA KOMENTLAR (40 tagacha):")
        lines += [f"- {c}" for c in d["post"]["comment_samples"]]
    message = "Quyidagi bitta postni chuqur tahlil qil.\n\n" + "\n".join(lines)
    return ai.generate_text(ANALYST_RULES + "\n\n" + POST_STRUCTURE, message)


def stories_text(stories):
    if not stories:
        return "Hozir faol story yo'q (Instagram faqat oxirgi 24 soatdagi storylarni beradi)."
    out = [f"📱 Faol storylar: {len(stories)} ta"]
    for i, s in enumerate(stories, 1):
        bits = [f"{i}. {_date_label(s['dt'])} {s['dt']:%H:%M}" if s["dt"] else f"{i}."]
        for key, label in (("views", "ko'rish"), ("reach", "qamrov"), ("replies", "javob"),
                           ("shares", "ulashish"), ("profile_visits", "profilga o'tish"),
                           ("follows", "obuna")):
            if s.get(key) is not None:
                bits.append(f"{label} {n(s[key])}")
        out.append(" | ".join(bits))
    return "\n".join(out)
