"""
accounts.py
-----------
Jarvis bir nechta Instagram akkaunt bilan ishlaydi:

- "main" — asosiy do'kon sahifasi (bolalar_dunyosi.uzb)
- "toy"  — o'yinchoq profili (bolalar_dunyosi_oyinchoq)

Qaysi akkaunt bilan ishlanayotgani contextvars orqali saqlanadi: Instagram so'rovlari,
token, komment/direkt holati, do'kon ma'lumotlari va xotira shu akkauntga tegishli bo'ladi.
Shunda bir akkaunt narxi ikkinchisida aytilib ketmaydi.

Asosiy akkaunt fayllari avvalgidek DATA_DIR ichida qoladi (hech narsa ko'chmaydi),
o'yinchoq profiliniki esa DATA_DIR/toy/ ichida.
"""

import contextvars
import json
import os
from contextlib import contextmanager

import config

MAIN = "main"
TOY = "toy"
ALL = (MAIN, TOY)

# HUD va Telegram xabarlarida ko'rinadigan nom (Instagram username kelmaguncha)
DEFAULT_USERNAMES = {
    MAIN: "bolalar_dunyosi.uzb",
    TOY: (os.environ.get("INSTAGRAM_TOY_USERNAME") or "bolalar_dunyosi_oyinchoq").strip().lstrip("@").lower(),
}
TITLES = {MAIN: "Asosiy sahifa", TOY: "O'yinchoq profili"}
ICONS = {MAIN: "🛴", TOY: "🧸"}

_current = contextvars.ContextVar("jarvis_ig_account", default=MAIN)


def normalize(acc):
    acc = str(acc or "").strip().lower()
    return acc if acc in ALL else MAIN


def current():
    return _current.get()


def is_toy():
    return current() == TOY


@contextmanager
def use(acc):
    token = _current.set(normalize(acc))
    try:
        yield
    finally:
        _current.reset(token)


def set_current(acc):
    """Context manager'siz o'rnatish (masalan so'rov boshida). Qaytaradi: reset uchun token."""
    return _current.set(normalize(acc))


def data_dir(acc=None):
    acc = normalize(acc or current())
    if acc == MAIN:
        return config.DATA_DIR
    return os.path.join(config.DATA_DIR, acc)


def path(name, acc=None):
    return os.path.join(data_dir(acc), name)


def env_token(acc=None):
    acc = normalize(acc or current())
    if acc == MAIN:
        return (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    return (os.environ.get("INSTAGRAM_TOY_TOKEN") or os.environ.get("INSTAGRAM_ACCESS_TOKEN_2") or "").strip()


def label(acc=None):
    """Telegram xabarlari uchun qisqa belgi. Asosiy akkauntda bo'sh (eski xabarlar o'zgarmaydi)."""
    acc = normalize(acc or current())
    if acc == MAIN:
        return ""
    return f"{ICONS[acc]} {DEFAULT_USERNAMES[acc]}"


class Scoped:
    """Modul darajasidagi kesh (dict yoki list), har akkaunt uchun alohida.
    Eski kod o'zgarmaydi: cache["value"], cache.update(...), cache[0] ishlayveradi."""

    def __init__(self, factory):
        self._factory = factory
        self._items = {}

    def obj(self):
        acc = current()
        if acc not in self._items:
            self._items[acc] = self._factory()
        return self._items[acc]

    def __getitem__(self, k):
        return self.obj()[k]

    def __setitem__(self, k, v):
        self.obj()[k] = v

    def __contains__(self, k):
        return k in self.obj()

    def __len__(self):
        return len(self.obj())

    def __iter__(self):
        return iter(self.obj())

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.obj(), name)


def other_connected_usernames():
    """Hozirgidan boshqa, Jarvis'ga token bilan ulangan akkauntlarimiz username'lari.
    Token fayli yoki Variables'dagi tokenga qarab aniqlanadi (Instagram'ga so'rov yuborilmaydi)."""
    out = set()
    cur = current()
    for acc in ALL:
        if acc == cur:
            continue
        connected = bool(env_token(acc))
        if not connected:
            try:
                with open(path("instagram_token.json", acc), encoding="utf-8") as f:
                    connected = bool((json.load(f) or {}).get("token"))
            except (OSError, ValueError, AttributeError):
                connected = False
        if connected:
            out.add(DEFAULT_USERNAMES[acc])
    return out


def our_usernames():
    """Barcha akkauntlarimiz username'lari (o'zimizning komment yoki javobimizni tanish uchun)."""
    return {u for u in DEFAULT_USERNAMES.values() if u}


# ----- umumiy komment ro'yxati: collab videoda bir komentga faqat bitta akkaunt javob beradi -----

import threading as _threading  # noqa: E402

_claims_lock = _threading.Lock()
MAX_CLAIMS = 20000


def _claims_path():
    return os.path.join(config.DATA_DIR, "shared_comment_claims.json")


def claim_comment(cid, acc=None):
    """Komentni shu akkauntga biriktiradi. Boshqa akkauntimiz allaqachon olgan bo'lsa False.
    Ikkala akkaunt ulangan collab videoda bir xil komment ikkalasida ham ko'rinadi: ikki javob ketmasin."""
    cid = str(cid or "")
    if not cid:
        return True
    acc = normalize(acc or current())
    with _claims_lock:
        try:
            with open(_claims_path(), encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except (OSError, ValueError):
            d = {}
        owner = d.get(cid)
        if owner:
            return owner == acc
        d[cid] = acc
        if len(d) > MAX_CLAIMS:
            d = dict(list(d.items())[-MAX_CLAIMS:])
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            tmp = _claims_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, _claims_path())
        except OSError:
            pass
        return True
