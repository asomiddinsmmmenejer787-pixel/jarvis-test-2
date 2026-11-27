"""
followers_live.py
-----------------
Obunachilar sonini jonli kuzatish.

Instagram API kim obuna bo'lganini aytmaydi, faqat umumiy sonni beradi. Shuning uchun Jarvis sonni
muntazam o'qib, har o'zgarishni (+3, -1) vaqti bilan yozib boradi. Qo'shimcha ravishda Instagram'ning
o'z kunlik hisobi (yangi obunalar / obunadan chiqqanlar) ham olinadi.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import accounts
import config
import instagram_api as ig
from catalog import TASHKENT

log = logging.getLogger("followers_live")
_lock = threading.Lock()
KEEP_DAYS = 8
HEARTBEAT = 600          # o'zgarish bo'lmasa ham grafik uchun 10 daqiqada bir nuqta
_last_fetch = accounts.Scoped(lambda: [0.0])
_insights = accounts.Scoped(lambda: {"t": 0.0, "v": None, "err": None})


def _path():
    return accounts.path("followers_live.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return [p for p in d if isinstance(p, list) and len(p) == 2] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(points):
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    with open(_path() + ".tmp", "w", encoding="utf-8") as f:
        json.dump(points, f)
    os.replace(_path() + ".tmp", _path())


def record(count, now=None):
    """Yangi o'lchovni yozadi. O'zgarish bo'lsa True."""
    if count is None:
        return False
    now = int(now or time.time())
    count = int(count)
    with _lock:
        pts = _load()
        changed = not pts or pts[-1][1] != count
        if changed or now - pts[-1][0] >= HEARTBEAT:
            pts.append([now, count])
            pts = [p for p in pts if p[0] >= now - KEEP_DAYS * 86400][-6000:]
            try:
                _save(pts)
            except OSError as e:
                log.warning("Saqlab bo'lmadi: %s", e)
        return changed


def sample(min_interval=45):
    """Instagram'dan sonni o'qiydi (min_interval soniyadan tez-tez emas)."""
    if time.time() - _last_fetch[0] < min_interval:
        return None
    _last_fetch[0] = time.time()
    count = ig.get_profile().get("followers_count")
    record(count)
    return count


def _today_start(now):
    d = datetime.fromtimestamp(now, TASHKENT)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def instagram_today(now=None):
    """Instagram'ning o'z hisobi (10 daqiqa kesh). Xato bo'lsa None."""
    now = now or time.time()
    if now - _insights["t"] < 600:
        return _insights["v"]
    _insights["t"] = now
    try:
        start = _today_start(now)
        _insights["v"] = ig.get_follows_unfollows(start, int(now))
        _insights["err"] = None
    except ig.InstagramError as e:
        if e.fatal:
            raise
        _insights["v"], _insights["err"] = None, str(e)
    return _insights["v"]


def view(now=None, hours=24, feed=40):
    now = int(now or time.time())
    pts = _load()
    start = _today_start(now)
    changes, gained, lost = [], 0, 0
    for (t0, c0), (t1, c1) in zip(pts, pts[1:]):
        d = c1 - c0
        if not d:
            continue
        changes.append({"t": t1, "delta": d, "count": c1})
        if t1 >= start:
            if d > 0:
                gained += d
            else:
                lost += -d
    series = [p for p in pts if p[0] >= now - hours * 3600]
    hour_ago = [p for p in pts if p[0] <= now - 3600]
    count = pts[-1][1] if pts else None
    return {
        "count": count,
        "updated": pts[-1][0] if pts else None,
        "series": series,
        "changes": changes[::-1][:feed],
        "today": {"gained": gained, "lost": lost, "net": gained - lost,
                  "since": start, "tracked_from": max(start, pts[0][0]) if pts else None},
        "hour": (count - hour_ago[-1][1]) if (count is not None and hour_ago) else None,
    }
