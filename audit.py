"""
audit.py
--------
Jarvis'ning har bir qarori va xatosini yozib boradi (DATA_DIR/audit.jsonl) va vaqti-vaqti bilan
GitHub'dagi YOPIQ (private) omborga yuboradi. Shu yozuvlar orqali ishlab chiquvchi (Claude)
Jarvis qanday ishlayotganini ko'rib, xatolarini tuzatadi.

Sozlamalar (Railway Variables):
  LOGS_REPO     - masalan "foydalanuvchi/jarvis-logs" (yopiq ombor bo'lishi SHART)
  GITHUB_TOKEN  - faqat shu omborga "Contents: Read and write" ruxsatli token
Ombor ochiq (public) bo'lsa, mijozlar ma'lumoti chiqib ketmasligi uchun hech narsa yuborilmaydi.
"""

import base64
import json
import logging
import os
import threading
import time
from datetime import datetime

import requests

import config

log = logging.getLogger("audit")
_lock = threading.Lock()
MAX_BYTES = 4_000_000
SHIP_EVERY = 600
_state = {"last_ship": 0.0, "repo_ok": None, "repo_checked": 0.0, "last_error": ""}
API = "https://api.github.com"


def _path():
    return os.path.join(config.DATA_DIR, "audit.jsonl")


def _offset_path():
    return os.path.join(config.DATA_DIR, "audit.shipped")


def record(kind, **fields):
    """Bitta yozuv qo'shadi. Hech qachon xato tashlamaydi."""
    try:
        item = {"t": int(time.time()), "k": kind}
        for k, v in fields.items():
            if isinstance(v, str):
                v = v[:600]
            item[k] = v
        line = json.dumps(item, ensure_ascii=False, default=str) + "\n"
        with _lock:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            p = _path()
            if os.path.exists(p) and os.path.getsize(p) > MAX_BYTES:
                _rotate(p)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:  # noqa: BLE001
        pass


def _rotate(p):
    """Eng eski yarmini tashlaydi (yuborilmagan qismi saqlanadi)."""
    with open(p, "rb") as f:
        data = f.read()
    shipped = _read_offset()
    cut = max(0, len(data) // 2)
    cut = data.find(b"\n", cut) + 1
    with open(p + ".tmp", "wb") as f:
        f.write(data[cut:])
    os.replace(p + ".tmp", p)
    _write_offset(max(0, shipped - cut))


def _read_offset():
    try:
        with open(_offset_path()) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_offset(n):
    with open(_offset_path(), "w") as f:
        f.write(str(n))


def recent(n=200, kinds=None):
    try:
        with open(_path(), encoding="utf-8") as f:
            lines = f.readlines()[-max(n * 3, n):]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            it = json.loads(ln)
        except ValueError:
            continue
        if not kinds or it.get("k") in kinds:
            out.append(it)
    return out[-n:]


class AuditLogHandler(logging.Handler):
    """WARNING va undan yuqori loglarni audit'ga ham yozadi (xatolarni ko'rish uchun)."""

    def emit(self, rec):
        if rec.name in ("audit", "httpx", "telegram.ext.Updater") or rec.levelno < logging.WARNING:
            return
        msg = rec.getMessage()
        if rec.exc_info:
            import traceback
            msg += "\n" + "".join(traceback.format_exception(*rec.exc_info))[-1500:]
        record("log", level=rec.levelname, src=rec.name, msg=msg[:2000])


def install():
    logging.getLogger().addHandler(AuditLogHandler())


# ---------- GitHub'ga yuborish ----------

def _cfg():
    return os.environ.get("LOGS_REPO", "").strip(), os.environ.get("GITHUB_TOKEN", "").strip()


def _gh(method, url, token, **kw):
    return requests.request(method, API + url, timeout=25, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"}, **kw)


def _repo_private(repo, token):
    if time.time() - _state["repo_checked"] < 3600 and _state["repo_ok"] is not None:
        return _state["repo_ok"]
    _state["repo_checked"] = time.time()
    r = _gh("GET", f"/repos/{repo}", token)
    if r.status_code != 200:
        _state["repo_ok"] = None
        _state["last_error"] = f"Omborni ochib bo'lmadi ({r.status_code})"
        return None
    _state["repo_ok"] = bool(r.json().get("private"))
    if not _state["repo_ok"]:
        _state["last_error"] = "Ombor ochiq (public): mijozlar ma'lumoti uchun yopiq ombor kerak"
    return _state["repo_ok"]


def _put(repo, token, path, content, msg):
    body = {"message": msg, "content": base64.b64encode(content.encode()).decode()}
    r = _gh("GET", f"/repos/{repo}/contents/{path}", token)
    if r.status_code == 200:
        body["sha"] = r.json().get("sha")
    r = _gh("PUT", f"/repos/{repo}/contents/{path}", token, json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub {r.status_code}: {r.text[:200]}")


def ship(status=None, force=False):
    """Yangi yozuvlarni GitHub'ga yuboradi. (yuborilgan qatorlar soni, xato matni yoki '')."""
    repo, token = _cfg()
    if not repo or not token:
        return 0, "LOGS_REPO yoki GITHUB_TOKEN o'rnatilmagan"
    if not force and time.time() - _state["last_ship"] < SHIP_EVERY:
        return 0, ""
    _state["last_ship"] = time.time()
    try:
        if not _repo_private(repo, token):
            return 0, _state["last_error"]
        with _lock:
            try:
                with open(_path(), "rb") as f:
                    f.seek(_read_offset())
                    chunk = f.read(900_000)
            except OSError:
                chunk = b""
        cut = chunk.rfind(b"\n") + 1
        chunk = chunk[:cut]
        now = datetime.now(config_tz())
        if chunk:
            name = f"logs/{now:%Y-%m-%d}/{now:%H%M%S}.jsonl"
            _put(repo, token, name, chunk.decode("utf-8", "replace"), f"log {now:%Y-%m-%d %H:%M}")
            with _lock:
                _write_offset(_read_offset() + cut)
        if status is not None:
            _put(repo, token, "status.json",
                 json.dumps({"time": now.isoformat(), **status}, ensure_ascii=False, indent=1, default=str),
                 "status")
        _state["last_error"] = ""
        return chunk.count(b"\n"), ""
    except Exception as e:  # noqa: BLE001
        _state["last_error"] = str(e)[:300]
        log.warning("Loglarni GitHub'ga yuborib bo'lmadi: %s", e)
        return 0, _state["last_error"]


def config_tz():
    from catalog import TASHKENT
    return TASHKENT


def info():
    repo, token = _cfg()
    return {"repo": repo, "configured": bool(repo and token), "private": _state["repo_ok"],
            "last_ship": int(_state["last_ship"]) or None, "error": _state["last_error"]}
