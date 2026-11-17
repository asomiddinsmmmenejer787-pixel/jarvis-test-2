"""
ai/groq.py
----------
Groq API (OpenAI bilan mos) — Gemini limiti tugaganda ishlaydigan bepul zaxira AI.

- Kalit: GROQ_API_KEY (Railway Variables) yoki Telegram'da /groqkey bilan saqlangan kalit.
- Bepul tarifda limit har bir model uchun alohida, shuning uchun bir nechta model navbat bilan
  sinaladi; limitga urilgan model bir muddat chetlab o'tiladi.
"""

import json
import logging
import os
import re
import threading
import time

import requests

import config
from ai.base import AIError, AIProvider

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.groq.com/openai/v1"
TIMEOUT = 60
# Sifat bo'yicha tartib. Kalitda yo'q modellar avtomatik tushib qoladi.
DEFAULT_MODELS = (
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "llama-3.3-70b-versatile",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
)
BAD_TTL = {"limit": 15 * 60, "busy": 3 * 60, "not_found": 24 * 3600, "timeout": 5 * 60}
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_lock = threading.Lock()


class GroqError(AIError):
    pass


def _key_file():
    return os.path.join(config.DATA_DIR, "groq_key.json")


def get_api_key():
    """Avval Telegram orqali saqlangan kalit, bo'lmasa Railway'dagi GROQ_API_KEY."""
    try:
        with open(_key_file(), encoding="utf-8") as f:
            key = (json.load(f) or {}).get("key", "")
        if key:
            return key
    except (OSError, ValueError, AttributeError):
        pass
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def save_api_key(key):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    fd = os.open(_key_file() + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"key": key}, f)
    os.replace(_key_file() + ".tmp", _key_file())


def check_key(key):
    """Kalitni tekshiradi. Qaytaradi: mavjud modellar soni. Yaroqsiz bo'lsa GroqError."""
    try:
        r = requests.get(f"{API_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except requests.exceptions.RequestException as e:
        raise GroqError(f"Groq bilan bog'lanib bo'lmadi ({type(e).__name__})")
    if r.status_code in (401, 403):
        raise GroqError("Kalit yaroqsiz", fatal=True)
    if r.status_code != 200:
        raise GroqError(f"Groq HTTP {r.status_code}")
    return len(r.json().get("data", []))


def _parse_json(text):
    text = _THINK_RE.sub("", text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


class GroqProvider(AIProvider):
    name = "Groq"

    def __init__(self, models=None):
        env = [m.strip() for m in (os.environ.get("GROQ_MODELS") or "").split(",") if m.strip()]
        self.models = tuple(models or env or DEFAULT_MODELS)
        self._bad = {}
        self._names = None
        self._names_at = 0
        self._resolved_model = None
        self._no_json_mode = set()

    @property
    def model(self):
        return self._resolved_model or self.models[0]

    def configured(self):
        return bool(get_api_key())

    def _headers(self):
        return {"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"}

    def _known(self):
        if self._names is None or time.time() - self._names_at > 3600:
            try:
                r = requests.get(f"{API_BASE_URL}/models", headers=self._headers(), timeout=30)
                if r.status_code == 200:
                    self._names = {m["id"] for m in r.json().get("data", [])}
                    self._names_at = time.time()
            except (requests.exceptions.RequestException, ValueError, KeyError):
                return None
        return self._names

    def _candidates(self):
        order = [m for m in dict.fromkeys((self._resolved_model, *self.models)) if m]
        names = self._known()
        if names:
            order = [m for m in order if m in names] or order
        now = time.time()
        with _lock:
            fresh = [m for m in order if self._bad.get(m, 0) < now]
        return fresh or order[:1]

    def _mark(self, model, reason):
        with _lock:
            self._bad[model] = time.time() + BAD_TTL[reason]
        if self._resolved_model == model:
            self._resolved_model = None
        logger.warning(f"Groq modeli '{model}' vaqtincha chetlab o'tiladi ({reason}).")

    def _complete(self, system, message, temperature, json_mode):
        """Qaytaradi: matn. Hamma model ishlamasa GroqError."""
        if not get_api_key():
            raise GroqError("GROQ_API_KEY o'rnatilmagan", fatal=True)
        last = "Mos Groq modeli topilmadi"
        for model in self._candidates()[:4]:
            body = {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}],
                "temperature": temperature,
                "max_tokens": 4096,
            }
            if json_mode and model not in self._no_json_mode:
                body["response_format"] = {"type": "json_object"}
            try:
                r = requests.post(f"{API_BASE_URL}/chat/completions", json=body, headers=self._headers(), timeout=TIMEOUT)
            except requests.exceptions.Timeout:
                self._mark(model, "timeout")
                last = "Groq javob bermadi"
                continue
            except requests.exceptions.RequestException as e:
                raise GroqError(f"Groq bilan bog'lanib bo'lmadi ({type(e).__name__})")
            if r.status_code == 200:
                try:
                    text = r.json()["choices"][0]["message"].get("content") or ""
                except (ValueError, KeyError, IndexError):
                    text = ""
                if self._resolved_model != model:
                    logger.info(f"Groq modeli: {model}")
                self._resolved_model = model
                return _THINK_RE.sub("", text).strip()
            if r.status_code in (401, 403):
                raise GroqError("Groq kaliti yaroqsiz", fatal=True)
            if r.status_code == 400 and "response_format" in body:
                # Model JSON rejimini qo'llamaydi: oddiy rejimda qayta urinamiz
                self._no_json_mode.add(model)
                return self._complete(system, message, temperature, json_mode)
            if r.status_code == 404:
                self._mark(model, "not_found")
            elif r.status_code == 429:
                self._mark(model, "limit")
            else:
                self._mark(model, "busy")
            last = f"Groq HTTP {r.status_code}"
        raise GroqError(last)

    def generate_text(self, system_instruction: str, user_message: str) -> str:
        try:
            text = self._complete(system_instruction, user_message, 0.8, False)
        except GroqError as e:
            logger.error(f"Groq xatosi: {e}")
            return f"[Groq xatosi] {e}"
        return text or "[Groq xatosi] Bo'sh javob"

    def generate_json(self, system_instruction: str, user_message: str, temperature: float = 0.9):
        text = self._complete(system_instruction + "\n\nJavobni faqat JSON ko'rinishida ber.",
                              user_message, min(temperature, 1.0), True)
        return _parse_json(text)

    def diagnose(self) -> dict:
        info = {"configured": self.configured(), "tested": []}
        if not info["configured"]:
            return info
        start = time.time()
        try:
            self._complete("Faqat OK deb javob ber.", "OK?", 0.0, False)
            info["status"] = 200
        except GroqError as e:
            info["status"] = str(e)
        info["working"] = self._resolved_model
        info["tested"].append(f"{self._resolved_model or '-'}: {info['status']}, {round(time.time() - start, 1)} s")
        return info
