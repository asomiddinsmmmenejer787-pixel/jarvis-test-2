"""
ads_api.py
----------
Meta reklama (target) natijalari: qamrov, ko'rishlar, kliklar, sarflangan pul,
direktga yozganlar. Faqat O'QIYDI, reklamani o'zgartirmaydi.

Kerak:
- META_ADS_TOKEN: reklama kabinetini o'qishga ruxsatli token (ads_read).
  Railway Variables'ga qo'yiladi yoki Telegram'da /reklamatoken orqali yuboriladi
  (u holda DATA_DIR/ads_token.json fayliga saqlanadi).
- META_AD_ACCOUNT_ID: reklama kabineti raqami (standart: egasining asosiy kabineti).

Xavfsizlik: token hech qachon logga yoki xato matniga chiqarilmaydi.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

import requests

import config

log = logging.getLogger("ads_api")

API_VERSION = os.environ.get("META_API_VERSION", "v23.0").strip() or "v23.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"
TIMEOUT = 30
DEFAULT_ACCOUNT = "3201139930118828"

PRESETS = {
    "today": ("today", "Bugun"),
    "yesterday": ("yesterday", "Kecha"),
    "7": ("last_7d", "Oxirgi 7 kun"),
    "30": ("last_30d", "Oxirgi 30 kun"),
}

INSIGHT_FIELDS = "reach,impressions,clicks,inline_link_clicks,spend,cpc,cpm,ctr,frequency,actions"
CAMPAIGN_FIELDS = "campaign_id,campaign_name," + INSIGHT_FIELDS

# Direktga yozishni boshlaganlar (Meta har xil nom bilan qaytaradi)
MESSAGE_ACTIONS = (
    "onsite_conversion.messaging_conversation_started_7d",
    "onsite_conversion.total_messaging_connection",
)

ACCOUNT_STATUS = {
    1: "faol", 2: "o'chirilgan", 3: "to'lov o'tmagan", 7: "tekshiruvda",
    8: "to'lov kutilmoqda", 9: "kutish davri", 100: "yopilmoqda", 101: "yopilgan",
}

_lock = threading.Lock()


class AdsError(Exception):
    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


def _token_file():
    return os.path.join(config.DATA_DIR, "ads_token.json")


def get_token():
    try:
        with open(_token_file(), encoding="utf-8") as f:
            saved = (json.load(f) or {}).get("token")
        if saved:
            return saved
    except (OSError, ValueError):
        pass
    return os.environ.get("META_ADS_TOKEN", "").strip()


def account_id():
    raw = os.environ.get("META_AD_ACCOUNT_ID", "").strip() or DEFAULT_ACCOUNT
    return raw if raw.startswith("act_") else "act_" + raw


def is_configured():
    return bool(get_token())


def _request(path, params=None, token=None):
    token = token or get_token()
    if not token:
        raise AdsError("Reklama kabineti hali ulanmagan (token yo'q)", fatal=True)
    params = dict(params or {})
    params["access_token"] = token
    try:
        r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise AdsError(f"Meta bilan bog'lanib bo'lmadi ({type(e).__name__})")
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    if r.status_code < 400 and not err:
        return body
    err = err or {}
    code = err.get("code")
    if code == 190:
        raise AdsError("Reklama tokeni yaroqsiz yoki muddati o'tgan", fatal=True)
    if code in (10, 200, 294) or (isinstance(code, int) and 200 <= code < 300):
        raise AdsError("Tokenda reklama kabinetini o'qishga ruxsat yo'q (ads_read kerak)", fatal=True)
    if code in (4, 17, 32, 613, 80004) or r.status_code == 429:
        raise AdsError("Meta so'rov limiti tugadi, birozdan keyin qayta urinib ko'ring")
    msg = re.sub(r"access_token=[^&\s]+", "access_token=***", str(err.get("message") or f"HTTP {r.status_code}"))
    raise AdsError(f"Meta xatosi: {msg}")


def set_token(token):
    """Tokenni tekshiradi (kabinetni o'qib ko'radi) va saqlaydi. Kabinet nomini qaytaradi."""
    token = (token or "").strip()
    if len(token) < 20:
        raise AdsError("Token juda qisqa, to'liq nusxa oling")
    info = _request(account_id(), {"fields": "name,currency"}, token=token)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with _lock:
        with open(_token_file(), "w", encoding="utf-8") as f:
            json.dump({"token": token}, f)
    return info.get("name") or account_id()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _messages(actions):
    found = {a.get("action_type"): _num(a.get("value")) for a in (actions or []) if isinstance(a, dict)}
    for name in MESSAGE_ACTIONS:
        if name in found:
            return int(found[name])
    return 0


def _row(raw):
    spend = _num(raw.get("spend"))
    link_clicks = int(_num(raw.get("inline_link_clicks")))
    clicks = int(_num(raw.get("clicks")))
    msgs = _messages(raw.get("actions"))
    return {
        "reach": int(_num(raw.get("reach"))),
        "impressions": int(_num(raw.get("impressions"))),
        "clicks": clicks,
        "link_clicks": link_clicks,
        "spend": round(spend, 2),
        "ctr": round(_num(raw.get("ctr")), 2),
        "cpm": round(_num(raw.get("cpm")), 2),
        "cpc": round(spend / clicks, 2) if clicks else None,
        "messages": msgs,
        "cost_per_message": round(spend / msgs, 2) if msgs else None,
        "frequency": round(_num(raw.get("frequency")), 2),
    }


# ---------- target sozlamalari ----------

GENDERS = {1: "Erkaklar", 2: "Ayollar"}


def targeting_summary(t):
    """Meta target sozlamalarini oddiy so'zlar bilan: yosh, jins, joy, qiziqishlar."""
    t = t or {}
    a1, a2 = t.get("age_min") or 18, t.get("age_max") or 65
    out = {"age": f"{a1}–{a2}{'+' if a2 >= 65 else ''} yosh",
           "gender": ", ".join(GENDERS.get(g, str(g)) for g in t.get("genders") or []) or "Hammasi"}
    geo = t.get("geo_locations") or {}
    places = [c.get("name", "") + (f" (+{c['radius']} km)" if c.get("radius") else "")
              for c in geo.get("cities") or []]
    places += [r.get("name", "") for r in geo.get("regions") or []]
    places += [f"{c.get('name') or 'nuqta'} atrofi {c.get('radius', '')} km".replace("  ", " ")
               for c in geo.get("custom_locations") or []]
    out["places"] = [x for x in places if x.strip()] or list(geo.get("countries") or [])
    interests = []
    for spec in t.get("flexible_spec") or []:
        for key in ("interests", "behaviors", "life_events", "family_statuses"):
            interests += [i.get("name") for i in spec.get(key) or [] if isinstance(i, dict) and i.get("name")]
    out["interests"] = interests[:12]
    out["advantage"] = (t.get("targeting_automation") or {}).get("advantage_audience") == 1
    return out


def get_targets(acc=None):
    """Faol reklama guruhlari va ularning target sozlamalari (muddati tugaganlari tashlanadi)."""
    acc = acc or account_id()
    body = _request(f"{acc}/adsets", {
        "fields": "name,campaign{name},daily_budget,end_time,targeting",
        "filtering": json.dumps([{"field": "effective_status", "operator": "IN", "value": ["ACTIVE"]}]),
        "limit": 50,
    })
    now = datetime.now(timezone.utc)
    out = []
    for a in body.get("data") or []:
        end = a.get("end_time") or ""
        try:
            if end and datetime.fromisoformat(end.replace("+0000", "+00:00")) < now:
                continue
        except ValueError:
            pass
        out.append({
            "name": a.get("name") or "Guruh",
            "campaign": (a.get("campaign") or {}).get("name") or "",
            "daily_budget": round(_num(a.get("daily_budget")) / 100, 2) or None,
            "end": end[:10],
            "target": targeting_summary(a.get("targeting")),
        })
    return out


def advice(campaigns, targets, period):
    """Raqamlardan kelib chiqadigan qisqa maslahatlar. Hech narsa to'qilmaydi."""
    tips = []
    spent = [c for c in campaigns if c["spend"] > 0]
    if period in ("7", "30"):
        names = {c["name"] for c in spent}
        idle = sorted({t["campaign"] for t in targets if t["campaign"] and t["campaign"] not in names})
        if idle:
            tips.append(", ".join(f"«{n}»" for n in idle[:3]) + " faol turibdi, lekin bu davrda pul "
                        "sarflanmagan. Kabinetda tekshiring yoki keraksizini o'chiring.")
    if len(spent) >= 4:
        tips.append(f"Pul {len(spent)} ta kampaniyaga bo'linib ketgan. Eng arzon natija berayotgan "
                    "1-2 tasiga byudjetni jamlasangiz, Meta tezroq o'rganadi.")
    for c in spent:
        if c["impressions"] >= 1000 and 0 < c["ctr"] < 0.7:
            tips.append(f"«{c['name']}»: bosish foizi past ({c['ctr']}%). Videoning birinchi 2 soniyasini "
                        "kuchliroq qiling yoki boshqa video qo'ying.")
        if c.get("frequency", 0) >= 3.5:
            tips.append(f"«{c['name']}»: bir odamga o'rtacha {c['frequency']} marta ko'rsatilgan, auditoriya "
                        "charchagan. Yangi video qo'ying yoki auditoriyani kengaytiring.")
    paid = sorted([c for c in spent if c["cost_per_message"] and c["messages"] >= 3],
                  key=lambda c: c["cost_per_message"])
    if len(paid) >= 2 and paid[-1]["cost_per_message"] >= paid[0]["cost_per_message"] * 1.8:
        b, w = paid[0], paid[-1]
        tips.append(f"Direktga bitta yozgan «{b['name']}»da ${b['cost_per_message']}, «{w['name']}»da "
                    f"${w['cost_per_message']}. Byudjetni «{b['name']}» tomonga o'tkazish foydali.")
    return tips[:6]


def get_summary(period="7"):
    """Kabinet holati, umumiy natija va kampaniyalar bo'yicha natija."""
    preset, label = PRESETS.get(str(period), PRESETS["7"])
    acc = account_id()
    info = _request(acc, {"fields": "name,currency,account_status,amount_spent"})
    total = _request(f"{acc}/insights", {"fields": INSIGHT_FIELDS, "date_preset": preset})
    camps = _request(f"{acc}/insights", {"fields": CAMPAIGN_FIELDS, "date_preset": preset,
                                         "level": "campaign", "limit": 50})
    rows = (total.get("data") or [{}])
    campaigns = []
    for c in camps.get("data") or []:
        item = _row(c)
        item["name"] = c.get("campaign_name") or c.get("campaign_id") or "Kampaniya"
        campaigns.append(item)
    campaigns.sort(key=lambda x: x["spend"], reverse=True)
    try:
        targets = get_targets(acc)
    except AdsError as e:
        log.warning("Target sozlamalarini o'qib bo'lmadi: %s", e)
        targets = []
    status = info.get("account_status")
    return {
        "account": {"id": acc, "name": info.get("name") or acc, "currency": info.get("currency") or "USD",
                    "status": ACCOUNT_STATUS.get(status, str(status) if status is not None else ""),
                    "problem": status not in (None, 1)},
        "period": str(period) if str(period) in PRESETS else "7",
        "label": label,
        "total": _row(rows[0] if rows else {}),
        "campaigns": campaigns,
        "targets": targets,
        "advice": advice(campaigns, targets, str(period) if str(period) in PRESETS else "7"),
    }


def _money(v, cur):
    if v is None:
        return "—"
    return f"{v:,.2f} {'$' if cur == 'USD' else cur}".replace(",", " ")


def _int(v):
    return f"{int(v):,}".replace(",", " ")


def summary_text(s):
    """Telegram uchun qisqa hisobot."""
    cur = s["account"]["currency"]
    t = s["total"]
    lines = [f"📣 Reklama natijasi · {s['label']}"]
    if s["account"]["problem"]:
        lines.append(f"⚠️ Kabinet holati: {s['account']['status']}")
    if not t["impressions"] and not t["spend"]:
        lines.append("Bu davrda reklama yurmagan.")
        return "\n".join(lines)
    lines += [
        f"💸 Sarflandi: {_money(t['spend'], cur)}",
        f"👥 Qamrov: {_int(t['reach'])}",
        f"👁 Ko'rishlar: {_int(t['impressions'])}",
        f"👆 Kliklar: {_int(t['clicks'])} (1 klik: {_money(t['cpc'], cur)})",
    ]
    if t["messages"]:
        lines.append(f"💬 Direktga yozganlar: {_int(t['messages'])} (1 tasi: {_money(t['cost_per_message'], cur)})")
    if s["campaigns"]:
        lines.append("")
        for c in s["campaigns"][:5]:
            extra = f", {c['messages']} ta yozgan" if c["messages"] else ""
            lines.append(f"• {c['name'][:40]}: {_money(c['spend'], cur)}, {_int(c['reach'])} qamrov, "
                         f"{_int(c['clicks'])} klik{extra}")
    if s.get("targets"):
        lines += ["", "🎯 Target sozlamalari:"]
        for t in s["targets"][:5]:
            g = t["target"]
            where = ", ".join(g["places"][:3]) or "joy ko'rsatilmagan"
            lines.append(f"• {t['name'][:40]}: {g['age']}, {g['gender']}, {where}")
    if s.get("advice"):
        lines += ["", "💡 Maslahat:"] + [f"- {x}" for x in s["advice"]]
    return "\n".join(lines)
