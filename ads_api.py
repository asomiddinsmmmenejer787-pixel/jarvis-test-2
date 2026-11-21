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

INSIGHT_FIELDS = "reach,impressions,clicks,inline_link_clicks,spend,cpc,cpm,ctr,actions"
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
    }


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
    status = info.get("account_status")
    return {
        "account": {"id": acc, "name": info.get("name") or acc, "currency": info.get("currency") or "USD",
                    "status": ACCOUNT_STATUS.get(status, str(status) if status is not None else ""),
                    "problem": status not in (None, 1)},
        "period": str(period) if str(period) in PRESETS else "7",
        "label": label,
        "total": _row(rows[0] if rows else {}),
        "campaigns": campaigns,
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
    return "\n".join(lines)
