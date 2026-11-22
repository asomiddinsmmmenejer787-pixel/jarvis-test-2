"""
creator_rewards/store.py
------------------------
Blogerlar, Reels, ko'rishlar, mukofotlar va vaucherlar bazasi (SQLite, DATA_DIR ichida).
Barcha pul bilan bog'liq amallar bitta tranzaksiyada va qulf ostida bajariladi.

Vaucher qoidalari:
- har bir kod bir marta ishlatiladi;
- ishlatilmagan qoldiq "carry" sifatida saqlanadi va keyingi vaucherga qo'shiladi;
- qoldiq birinchi vaucherning muddati tugaguncha amal qiladi; yangi vaucherga qo'shilganda
  u bilan birga 30 kun amal qiladi (30 kundan oshmaydi).
"""

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import timedelta

from . import rewards as R

REEL_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:reel|reels|p)/([A-Za-z0-9_-]{5,})")
IG_USER_RE = re.compile(r"^@?([A-Za-z0-9_.]{1,30})$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators(
  tg_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT, photo_url TEXT,
  phone TEXT, instagram TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'draft',
  promo_code TEXT UNIQUE, created_at TEXT NOT NULL, approved_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS reels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL REFERENCES creators(tg_id),
  url TEXT NOT NULL, shortcode TEXT NOT NULL UNIQUE, posted_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'monitoring', last_views INTEGER NOT NULL DEFAULT 0,
  reminders TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, reel_id INTEGER NOT NULL REFERENCES reels(id),
  views INTEGER NOT NULL, source TEXT NOT NULL, file_id TEXT, verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rewards(
  id INTEGER PRIMARY KEY AUTOINCREMENT, reel_id INTEGER NOT NULL UNIQUE REFERENCES reels(id),
  views INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL, decided_at TEXT, admin_id INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS vouchers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL REFERENCES creators(tg_id),
  code TEXT NOT NULL UNIQUE, reward_id INTEGER UNIQUE REFERENCES rewards(id),
  nominal INTEGER NOT NULL CHECK(nominal > 0), carried_in INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active', issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  used_amount INTEGER, used_at TEXT, receipt TEXT);
CREATE TABLE IF NOT EXISTS carry(
  tg_id INTEGER PRIMARY KEY REFERENCES creators(tg_id), amount INTEGER NOT NULL CHECK(amount >= 0),
  expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor INTEGER, action TEXT NOT NULL, details TEXT,
  at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relay(admin_msg_id INTEGER PRIMARY KEY, tg_id INTEGER NOT NULL);
"""


class StoreError(Exception):
    """Foydalanuvchiga ko'rsatsa bo'ladigan xato."""


class Store:
    def __init__(self, path=None):
        if path is None:
            import config
            path = os.path.join(config.DATA_DIR, "creator_rewards.db")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    # ---------- umumiy ----------

    @contextmanager
    def tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def _one(self, sql, args=()):
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    @staticmethod
    def _audit(db, actor, action, **details):
        db.execute("INSERT INTO audit(actor,action,details,at) VALUES(?,?,?,?)",
                   (actor, action, json.dumps(details, ensure_ascii=False), R.to_iso(R.utcnow())))

    # ---------- blogerlar ----------

    def creator(self, tg_id):
        return self._one("SELECT * FROM creators WHERE tg_id=?", (tg_id,))

    def upsert_telegram(self, tg_id, first_name="", last_name="", username="", photo_url=None):
        with self.tx() as db:
            db.execute(
                "INSERT INTO creators(tg_id,first_name,last_name,username,photo_url,created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(tg_id) DO UPDATE SET first_name=excluded.first_name, last_name=excluded.last_name, "
                "username=excluded.username, photo_url=COALESCE(excluded.photo_url, creators.photo_url)",
                (tg_id, first_name or "", last_name or "", username or "", photo_url, R.to_iso(R.utcnow())))
        return self.creator(tg_id)

    def set_phone(self, tg_id, phone):
        with self.tx() as db:
            db.execute("UPDATE creators SET phone=? WHERE tg_id=?", (phone, tg_id))

    def set_instagram(self, tg_id, text):
        m = IG_USER_RE.match((text or "").strip().rstrip("/").split("instagram.com/")[-1].split("?")[0])
        if not m:
            raise StoreError("Instagram username noto'g'ri. Masalan: @madina_blog")
        name = m.group(1).lower()
        other = self._one("SELECT tg_id FROM creators WHERE instagram=? AND tg_id<>?", (name, tg_id))
        if other:
            raise StoreError("Bu Instagram akkaunt boshqa Telegram hisobiga ulangan.")
        with self.tx() as db:
            db.execute("UPDATE creators SET instagram=? WHERE tg_id=?", (name, tg_id))
        return name

    def submit_application(self, tg_id):
        c = self.creator(tg_id)
        if not c or not c["phone"] or not c["instagram"]:
            raise StoreError("Avval telefon raqam va Instagram username kiriting.")
        with self.tx() as db:
            db.execute("UPDATE creators SET status='pending' WHERE tg_id=? AND status IN ('draft','rejected')", (tg_id,))
            self._audit(db, tg_id, "application", instagram=c["instagram"])
        return self.creator(tg_id)

    def decide_creator(self, tg_id, approve, admin_id, note=""):
        c = self.creator(tg_id)
        if not c:
            raise StoreError("Bloger topilmadi.")
        with self.tx() as db:
            if approve:
                promo = c["promo_code"] or self._unique_promo(db, c)
                db.execute("UPDATE creators SET status='approved', approved_at=?, promo_code=?, note=? WHERE tg_id=?",
                           (R.to_iso(R.utcnow()), promo, note, tg_id))
            else:
                db.execute("UPDATE creators SET status='rejected', note=? WHERE tg_id=?", (note, tg_id))
            self._audit(db, admin_id, "creator_" + ("approved" if approve else "rejected"), tg_id=tg_id, note=note)
        return self.creator(tg_id)

    @staticmethod
    def _unique_promo(db, c):
        base = re.sub(r"[^A-Z0-9]", "", (c["instagram"] or c["first_name"] or "BLOGER").upper())[:8] or "BLOGER"
        code, n = base, 1
        while db.execute("SELECT 1 FROM creators WHERE promo_code=?", (code,)).fetchone():
            n += 1
            code = f"{base}{n}"
        return code

    def pending_creators(self):
        return self._all("SELECT * FROM creators WHERE status='pending' ORDER BY created_at")

    # ---------- Reels ----------

    def add_reel(self, tg_id, url, posted_at=None):
        c = self.creator(tg_id)
        if not c or c["status"] != "approved":
            raise StoreError("Reels qo'shish uchun arizangiz tasdiqlangan bo'lishi kerak.")
        m = REEL_RE.search(url or "")
        if not m:
            raise StoreError("Bu Instagram Reels havolasi emas. Masalan: https://www.instagram.com/reel/ABC123/")
        code = m.group(1)
        if self._one("SELECT 1 FROM reels WHERE shortcode=?", (code,)):
            raise StoreError("Bu video allaqachon ro'yxatga olingan.")
        start = posted_at or R.utcnow()
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO reels(tg_id,url,shortcode,posted_at,ends_at,created_at) VALUES(?,?,?,?,?,?)",
                (tg_id, f"https://www.instagram.com/reel/{code}/", code, R.to_iso(start),
                 R.to_iso(start + timedelta(days=R.TRACKING_DAYS)), R.to_iso(R.utcnow())))
            self._audit(db, tg_id, "reel_added", shortcode=code)
            return cur.lastrowid

    def reel(self, reel_id):
        return self._one("SELECT * FROM reels WHERE id=?", (reel_id,))

    def reels_of(self, tg_id):
        return self._all("SELECT * FROM reels WHERE tg_id=? ORDER BY posted_at DESC", (tg_id,))

    def add_snapshot(self, reel_id, views, source, file_id=None, verified=False, actor=None):
        views = int(views)
        if views < 0 or views > 1_000_000_000:
            raise StoreError("Ko'rishlar soni noto'g'ri.")
        reel = self.reel(reel_id)
        if not reel:
            raise StoreError("Video topilmadi.")
        if reel["status"] in ("rewarded", "rejected", "blocked"):
            raise StoreError("Bu video bo'yicha hisob-kitob yopilgan.")
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO snapshots(reel_id,views,source,file_id,verified,created_at) VALUES(?,?,?,?,?,?)",
                (reel_id, views, source, file_id, 1 if verified else 0, R.to_iso(R.utcnow())))
            if verified:
                db.execute("UPDATE reels SET last_views=? WHERE id=?", (views, reel_id))
            self._audit(db, actor, "snapshot", reel_id=reel_id, views=views, source=source, verified=verified)
            return cur.lastrowid

    def snapshot(self, snap_id):
        return self._one("SELECT * FROM snapshots WHERE id=?", (snap_id,))

    def verify_snapshot(self, snap_id, admin_id, views=None):
        """Administrator skrinshotdagi raqamni tasdiqlaydi. Muddat tugagan video uchun hisob-kitob yaratadi."""
        s = self.snapshot(snap_id)
        if not s:
            raise StoreError("Skrinshot topilmadi.")
        views = int(views if views is not None else s["views"])
        with self.tx() as db:
            db.execute("UPDATE snapshots SET verified=1, views=? WHERE id=?", (views, snap_id))
            db.execute("UPDATE reels SET last_views=MAX(last_views, ?) WHERE id=?", (views, s["reel_id"]))
            self._audit(db, admin_id, "snapshot_verified", snap_id=snap_id, views=views)
        reel = self.reel(s["reel_id"])
        if reel["status"] == "awaiting_final" or R.utcnow() >= R.from_iso(reel["ends_at"]):
            return self.create_reward(reel["id"], admin_id)
        return None

    def close_due_reels(self):
        """30 kuni tugagan videolarni yakuniy tekshiruvga o'tkazadi. Yangi o'tganlar ro'yxatini qaytaradi."""
        now = R.to_iso(R.utcnow())
        due = self._all("SELECT * FROM reels WHERE status='monitoring' AND ends_at<=?", (now,))
        if due:
            with self.tx() as db:
                for r in due:
                    db.execute("UPDATE reels SET status='awaiting_final' WHERE id=?", (r["id"],))
                    self._audit(db, None, "tracking_closed", reel_id=r["id"])
        return due

    def due_reminders(self):
        """(reel, day) juftliklari: skrinshot yuborish kuni kelgan, eslatma hali ketmagan."""
        out = []
        now = R.utcnow()
        for r in self._all("SELECT * FROM reels WHERE status IN ('monitoring','awaiting_final')"):
            sent = json.loads(r["reminders"] or "[]")
            age = (now - R.from_iso(r["posted_at"])).total_seconds() / 86400
            for day in R.SCREENSHOT_DAYS:
                if age >= day and day not in sent:
                    out.append((r, day))
        return out

    def mark_reminded(self, reel_id, day):
        r = self.reel(reel_id)
        sent = sorted(set(json.loads(r["reminders"] or "[]")) | {day})
        with self.tx() as db:
            db.execute("UPDATE reels SET reminders=? WHERE id=?", (json.dumps(sent), reel_id))

    # ---------- mukofot ----------

    def create_reward(self, reel_id, actor=None):
        reel = self.reel(reel_id)
        existing = self._one("SELECT * FROM rewards WHERE reel_id=?", (reel_id,))
        if existing:
            return existing
        views = reel["last_views"]
        amount = R.reward_for_views(views)
        with self.tx() as db:
            if amount <= 0:
                db.execute("UPDATE reels SET status='no_reward' WHERE id=?", (reel_id,))
                self._audit(db, actor, "no_reward", reel_id=reel_id, views=views)
                return None
            db.execute("INSERT INTO rewards(reel_id,views,amount,created_at) VALUES(?,?,?,?)",
                       (reel_id, views, amount, R.to_iso(R.utcnow())))
            db.execute("UPDATE reels SET status='awaiting_approval' WHERE id=?", (reel_id,))
            self._audit(db, actor, "reward_calculated", reel_id=reel_id, views=views, amount=amount)
        return self._one("SELECT * FROM rewards WHERE reel_id=?", (reel_id,))

    def reward(self, reward_id):
        return self._one("SELECT * FROM rewards WHERE id=?", (reward_id,))

    def decide_reward(self, reward_id, approve, admin_id, note=""):
        """Tasdiqlansa vaucher chiqaradi (oldingi qoldiq va faol vaucher unga qo'shiladi)."""
        with self.tx() as db:
            rw = db.execute("SELECT * FROM rewards WHERE id=?", (reward_id,)).fetchone()
            if not rw:
                raise StoreError("Hisob-kitob topilmadi.")
            if rw["status"] != "pending":
                raise StoreError("Bu hisob-kitob bo'yicha qaror allaqachon qabul qilingan.")
            reel = db.execute("SELECT * FROM reels WHERE id=?", (rw["reel_id"],)).fetchone()
            now = R.utcnow()
            if not approve:
                db.execute("UPDATE rewards SET status='rejected', decided_at=?, admin_id=?, note=? WHERE id=?",
                           (R.to_iso(now), admin_id, note, reward_id))
                db.execute("UPDATE reels SET status='rejected' WHERE id=?", (reel["id"],))
                self._audit(db, admin_id, "reward_rejected", reward_id=reward_id, note=note)
                return None
            tg_id = reel["tg_id"]
            carried = self._take_balance(db, tg_id, now)
            nominal = rw["amount"] + carried
            code = R.new_voucher_code()
            while db.execute("SELECT 1 FROM vouchers WHERE code=?", (code,)).fetchone():
                code = R.new_voucher_code()
            db.execute(
                "INSERT INTO vouchers(tg_id,code,reward_id,nominal,carried_in,issued_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (tg_id, code, reward_id, nominal, carried, R.to_iso(now),
                 R.to_iso(now + timedelta(days=R.VOUCHER_DAYS))))
            db.execute("UPDATE rewards SET status='approved', decided_at=?, admin_id=? WHERE id=?",
                       (R.to_iso(now), admin_id, reward_id))
            db.execute("UPDATE reels SET status='rewarded' WHERE id=?", (reel["id"],))
            self._audit(db, admin_id, "voucher_issued", reward_id=reward_id, code=code, nominal=nominal, carried=carried)
        return self._one("SELECT * FROM vouchers WHERE code=?", (code,))

    @staticmethod
    def _take_balance(db, tg_id, now):
        """Amaldagi qoldiq va ishlatilmagan faol vaucherlarni yig'ib, ularni yopadi."""
        total = 0
        row = db.execute("SELECT * FROM carry WHERE tg_id=?", (tg_id,)).fetchone()
        if row and R.from_iso(row["expires_at"]) > now:
            total += row["amount"]
        db.execute("DELETE FROM carry WHERE tg_id=?", (tg_id,))
        for v in db.execute("SELECT * FROM vouchers WHERE tg_id=? AND status='active'", (tg_id,)).fetchall():
            if R.from_iso(v["expires_at"]) > now:
                total += v["nominal"]
                db.execute("UPDATE vouchers SET status='merged' WHERE id=?", (v["id"],))
            else:
                db.execute("UPDATE vouchers SET status='expired' WHERE id=?", (v["id"],))
        return total

    # ---------- vaucher ishlatish ----------

    def voucher_by_code(self, code):
        return self._one("SELECT * FROM vouchers WHERE code=?", ((code or "").strip().upper(),))

    def redeem(self, code, amount, admin_id, receipt=""):
        """Kodni bir marta ishlatadi. Qoldiq keyingi vaucherga o'tadi."""
        amount = int(amount)
        if amount <= 0:
            raise StoreError("Summa noto'g'ri.")
        with self.tx() as db:
            v = db.execute("SELECT * FROM vouchers WHERE code=?", ((code or "").strip().upper(),)).fetchone()
            if not v:
                raise StoreError("Bunday vaucher kodi yo'q.")
            now = R.utcnow()
            if v["status"] != "active":
                raise StoreError("Bu vaucher faol emas (ishlatilgan, birlashtirilgan yoki muddati o'tgan).")
            if R.from_iso(v["expires_at"]) <= now:
                db.execute("UPDATE vouchers SET status='expired' WHERE id=?", (v["id"],))
                raise StoreError("Vaucher muddati tugagan.")
            if amount > v["nominal"]:
                raise StoreError(f"Summa vaucher qiymatidan katta ({R.som(v['nominal'])}).")
            left = v["nominal"] - amount
            db.execute("UPDATE vouchers SET status='used', used_amount=?, used_at=?, receipt=? WHERE id=?",
                       (amount, R.to_iso(now), receipt, v["id"]))
            if left > 0:
                db.execute("INSERT INTO carry(tg_id,amount,expires_at) VALUES(?,?,?) "
                           "ON CONFLICT(tg_id) DO UPDATE SET amount=carry.amount+excluded.amount, "
                           "expires_at=MAX(carry.expires_at, excluded.expires_at)",
                           (v["tg_id"], left, v["expires_at"]))
            self._audit(db, admin_id, "voucher_redeemed", code=v["code"], amount=amount, left=left, receipt=receipt)
            return {"tg_id": v["tg_id"], "code": v["code"], "nominal": v["nominal"], "used": amount,
                    "left": left, "left_expires_at": v["expires_at"]}

    def expire_old(self):
        now = R.to_iso(R.utcnow())
        expired = self._all("SELECT * FROM vouchers WHERE status='active' AND expires_at<=?", (now,))
        with self.tx() as db:
            db.execute("UPDATE vouchers SET status='expired' WHERE status='active' AND expires_at<=?", (now,))
            gone = db.execute("SELECT * FROM carry WHERE expires_at<=?", (now,)).fetchall()
            db.execute("DELETE FROM carry WHERE expires_at<=?", (now,))
            for row in gone:
                self._audit(db, None, "carry_expired", tg_id=row["tg_id"], amount=row["amount"])
        return expired, [dict(r) for r in gone]

    # ---------- HUD uchun umumiy ko'rinish ----------

    def overview(self, tg_id):
        c = self.creator(tg_id)
        if not c:
            return None
        now = R.utcnow()
        reels = []
        pending = 0
        for r in self.reels_of(tg_id):
            rw = self._one("SELECT * FROM rewards WHERE reel_id=?", (r["id"],))
            est = rw["amount"] if rw else R.reward_for_views(r["last_views"])
            ends = R.from_iso(r["ends_at"])
            if r["status"] in ("monitoring", "awaiting_final", "awaiting_approval"):
                pending += est
            snaps = self._all("SELECT views, verified, created_at FROM snapshots WHERE reel_id=? ORDER BY id", (r["id"],))
            sent = set(json.loads(r["reminders"] or "[]"))
            next_day = next((d for d in R.SCREENSHOT_DAYS if d not in sent), None)
            reels.append({
                "id": r["id"], "url": r["url"], "shortcode": r["shortcode"], "status": r["status"],
                "views": r["last_views"], "reward": est, "next_step": R.views_to_next_step(r["last_views"]),
                "posted_at": r["posted_at"], "ends_at": r["ends_at"],
                "days_left": max(0, (ends - now).days), "day": min(R.TRACKING_DAYS, max(0, (now - R.from_iso(r["posted_at"])).days)),
                "next_screenshot_day": next_day,
                "history": [{"views": s["views"], "verified": bool(s["verified"]), "at": s["created_at"]} for s in snaps],
            })
        active = self._all("SELECT * FROM vouchers WHERE tg_id=? AND status='active' AND expires_at>? ORDER BY id DESC",
                           (tg_id, R.to_iso(now)))
        carry = self._one("SELECT * FROM carry WHERE tg_id=? AND expires_at>?", (tg_id, R.to_iso(now)))
        history = self._all("SELECT code, nominal, carried_in, status, issued_at, expires_at, used_amount, used_at "
                            "FROM vouchers WHERE tg_id=? ORDER BY id DESC LIMIT 30", (tg_id,))
        available = sum(v["nominal"] for v in active) + (carry["amount"] if carry else 0)
        earned = sum(v["nominal"] - v["carried_in"] for v in history)
        successful = sum(1 for r in reels if r["status"] == "rewarded")
        level = "Hamkor" if successful >= 5 else ("Faol" if successful >= 2 else "Yangi")
        return {
            "profile": {k: c[k] for k in ("tg_id", "first_name", "last_name", "username", "photo_url", "phone",
                                           "instagram", "status", "promo_code", "created_at", "approved_at")},
            "level": level, "successful_reels": successful,
            "balance": {"available": available, "pending": pending, "earned_total": earned},
            "voucher": dict(active[0]) if active else None,
            "carry": carry, "vouchers": history, "reels": reels,
            "rules": {"min_views": R.MIN_VIEWS, "base": R.BASE_REWARD, "step_views": R.STEP_VIEWS,
                      "step_reward": R.STEP_REWARD, "cap_views": R.CAP_VIEWS, "max_reward": R.MAX_REWARD,
                      "tracking_days": R.TRACKING_DAYS, "voucher_days": R.VOUCHER_DAYS},
        }

    # ---------- xabar almashinuvi ----------

    def remember_relay(self, admin_msg_id, tg_id):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO relay(admin_msg_id,tg_id) VALUES(?,?)", (admin_msg_id, tg_id))

    def relay_target(self, admin_msg_id):
        row = self._one("SELECT tg_id FROM relay WHERE admin_msg_id=?", (admin_msg_id,))
        return row["tg_id"] if row else None
