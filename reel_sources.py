"""
reel_sources.py
---------------
Obunachilar qaysi reelsdan kelayotganini TAXMINIY hisoblash (HUD: "Qaysi reelsdan · Top 5").

Instagram API reels uchun "follows" (shu videodan obuna bo'lganlar) ko'rsatkichini bermaydi
(faqat oddiy post va story uchun bor). Shuning uchun Jarvis har ~15 daqiqada oxirgi reelslarning
ko'rishlar sonini yozib boradi. Ikki o'lchov orasida qo'shilgan obunachilar shu oraliqda ko'rishlari
ko'paygan reelslarga ko'rish ulushiga qarab bo'linadi. Natija aniq emas, taxminiy.
"""

import json
import logging
import os
import threading
import time

import config
import followers_live
import instagram_api as ig

log = logging.getLogger("reel_sources")
_lock = threading.Lock()
SNAP_EVERY = 900         # 15 daqiqa
MAX_REELS = 8            # oxirgi nechta reels kuzatiladi (API so'rovlarini tejash)
KEEP_DAYS = 3


def _path():
    return os.path.join(config.DATA_DIR, "reel_sources.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("snaps", [])
            d.setdefault("meta", {})
            return d
    except (OSError, ValueError):
        pass
    return {"snaps": [], "meta": {}}


def _save(d):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_path() + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(_path() + ".tmp", _path())


def record(views, meta=None, now=None):
    """views: {media_id: ko'rishlar}. meta: {media_id: {caption, thumb, link, ts}}."""
    now = int(now or time.time())
    views = {str(k): int(v) for k, v in (views or {}).items() if v is not None}
    if not views:
        return
    with _lock:
        d = _load()
        d["snaps"].append([now, views])
        d["snaps"] = [s for s in d["snaps"] if s[0] >= now - KEEP_DAYS * 86400][-400:]
        if meta:
            d["meta"].update({str(k): v for k, v in meta.items()})
        live = {k for s in d["snaps"] for k in s[1]}
        d["meta"] = {k: v for k, v in d["meta"].items() if k in live}
        try:
            _save(d)
        except OSError as e:
            log.warning("Saqlab bo'lmadi: %s", e)


def snapshot(now=None, min_interval=SNAP_EVERY):
    """Oxirgi reelslarning ko'rishlarini Instagram'dan o'qib yozadi (min_interval'dan tez emas)."""
    now = int(now or time.time())
    snaps = _load()["snaps"]
    if snaps and now - snaps[-1][0] < min_interval:
        return False
    reels = [m for m in ig.get_recent_posts(20) if ig.media_kind(m) == "REELS"][:MAX_REELS]
    views, meta = {}, {}
    for m in reels:
        try:
            v = ig.get_reel_views(m["id"])
        except ig.InstagramError as e:
            if e.fatal or e.rate_limited:
                raise
            continue
        if v is None:
            continue
        views[m["id"]] = v
        meta[m["id"]] = {
            "caption": (m.get("caption") or "").strip()[:120],
            "thumb": m.get("thumbnail_url") or m.get("media_url") or "",
            "link": m.get("permalink") or "",
            "ts": m.get("timestamp") or "",
        }
    record(views, meta, now)
    return bool(views)


def _gained(points, t0, t1):
    """(t0, t1] oralig'ida qo'shilgan obunachilar (faqat ijobiy o'zgarishlar)."""
    g = 0
    for (a, ca), (b, cb) in zip(points, points[1:]):
        if t0 < b <= t1 and cb > ca:
            g += cb - ca
    return g


def top(now=None, hours=24, n=5, points=None):
    now = int(now or time.time())
    d = _load()
    snaps = [s for s in d["snaps"] if s[0] >= now - hours * 3600]
    points = followers_live._load() if points is None else points
    score, vgain = {}, {}
    gained_total = attributed = 0
    for (t0, v0), (t1, v1) in zip(snaps, snaps[1:]):
        g = _gained(points, t0, t1)
        gained_total += g
        deltas = {k: v1[k] - v0[k] for k in v1 if k in v0 and v1[k] > v0[k]}
        for k, dv in deltas.items():
            vgain[k] = vgain.get(k, 0) + dv
        total = sum(deltas.values())
        if not g or not total:
            continue
        attributed += g
        for k, dv in deltas.items():
            score[k] = score.get(k, 0.0) + g * dv / total
    ranked = sorted(vgain, key=lambda k: (score.get(k, 0), vgain[k]), reverse=True)[:n]
    items = []
    for k in ranked:
        m = d["meta"].get(k, {})
        items.append({
            "id": k, "caption": m.get("caption", ""), "thumb": m.get("thumb", ""),
            "link": m.get("link", ""), "ts": m.get("ts", ""),
            "followers": int(round(score.get(k, 0))), "views": vgain[k],
            "share": round(100 * score.get(k, 0) / attributed) if attributed else 0,
        })
    return {
        "items": items, "hours": hours, "gained": gained_total, "attributed": attributed,
        "since": snaps[0][0] if snaps else None, "snaps": len(snaps),
    }
