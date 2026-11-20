"""
ai/gemini.py
------------
Google Gemini API bilan ishlaydigan haqiqiy AI provider.

Xavfsizlik:
- API key URL ichiga emas, so'rov sarlavhasiga (x-goog-api-key) qo'yiladi.
- Xato matnlariga URL yoki kalit hech qachon chiqarilmaydi.

Barqarorlik:
- Gemini vaqtincha band bo'lsa (503, 429 va h.k.), so'rov avtomatik qayta yuboriladi.
- Model ishlamasa (404, band, javob bermadi), keyingi Gemini modeli avtomatik sinaladi
  va birinchi ishlagani eslab qolinadi.
"""

import json
import logging
import os
import threading
import time
import requests
from ai.base import AIError, AIProvider

logger = logging.getLogger(__name__)

API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Bitta so'rov uchun kutish vaqti (soniya). Javob bermagan model darhol keyingisiga almashtiriladi.
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("GEMINI_TIMEOUT", "90") or 90)
# Navbat bilan sinaladigan modellar. Google ba'zi modellarni yangi kalitlar uchun yopadi (404)
# yoki ular vaqtincha sekinlashadi, shuning uchun bittasiga bog'lanib qolmaymiz.
MODEL_CANDIDATES = (
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
)
# Ishlamagan modelni qancha vaqt chetlab o'tish (soniya)
BAD_MODEL_TTL = {"not_found": 24 * 3600, "busy": 10 * 60, "timeout": 20 * 60}
MAX_MODELS_PER_REQUEST = 3
MIN_INTERVAL_SECONDS = float(os.environ.get("GEMINI_MIN_INTERVAL", "4") or 4)
_pace = {"next": 0.0}
_pace_lock = threading.Lock()
# Google'ning o'zgaruvchan taxalluslari: ba'zan juda sekin modelga ulanadi, shuning uchun oxirida sinaladi
SLOW_ALIASES = {"gemini-flash-latest", "gemini-pro-latest"}
# Vaqtinchalik xatolar
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
# Bir model ichida qayta urinishdan oldin kutish (soniya)
RETRY_DELAYS_SECONDS = (2, 5)


class GeminiError(AIError):
    """generate_json() xatosi. fatal=True bo'lsa (kalit noto'g'ri va h.k.), qayta urinish foydasiz."""


class GeminiProvider(AIProvider):
    name = "Gemini"
    """Google Gemini API orqali javob generatsiya qiluvchi provider."""

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GeminiProvider uchun api_key kerak.")
        self.api_key = api_key
        self.model = model
        self._resolved_model = None  # oxirgi muvaffaqiyatli ishlagan model
        self._bad = {}               # model -> qachongacha chetlab o'tiladi
        self._names = None           # kalitga ochiq modellar ro'yxati (kesh)
        self._names_at = 0

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _throttle(self):
        """So'rovlar orasida kamida MIN_INTERVAL soniya: bepul limitga (429) kamroq urilish uchun."""
        with _pace_lock:
            wait = _pace["next"] - time.time()
            if wait > 0:
                time.sleep(wait)
            _pace["next"] = time.time() + MIN_INTERVAL_SECONDS

    def _post_generate(self, model: str, payload: dict) -> requests.Response:
        """So'rov yuboradi. Vaqtinchalik xato bo'lsa, bir necha marta qayta uriniladi."""
        url = f"{API_BASE_URL}/models/{model}:generateContent"

        def send() -> requests.Response:
            self._throttle()
            # Ulanish uzilsa yoki javob kechiksa, yana 2 marta urinib ko'ramiz
            for attempt in range(3):
                try:
                    return requests.post(
                        url, json=payload, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS
                    )
                except requests.exceptions.ReadTimeout:
                    # Model javob bermayapti: shu modelga qayta urinish foydasiz
                    logger.warning(f"'{model}' {REQUEST_TIMEOUT_SECONDS} soniyada javob bermadi.")
                    raise
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                    logger.warning(f"Gemini ulanish xatosi ({type(e).__name__}), urinish {attempt + 1}/3")
                    if attempt == 2:
                        raise
                    time.sleep(3 * (attempt + 1))

        response = send()
        for delay in RETRY_DELAYS_SECONDS:
            if response.status_code not in RETRY_STATUS_CODES or response.status_code == 429:
                break  # 429 = limit tugagan: kutish foydasiz, darhol boshqa model yoki zaxira AI'ga o'tamiz
            logger.warning(
                f"Gemini vaqtincha band (kod {response.status_code}), "
                f"{delay} soniyadan keyin qayta uriniladi."
            )
            time.sleep(delay)
            response = send()
        return response

    def _available_models(self) -> list:
        response = requests.get(
            f"{API_BASE_URL}/models",
            headers=self._headers(),
            params={"pageSize": 200},
            timeout=30,
        )
        response.raise_for_status()
        return [
            m["name"].split("/", 1)[1]
            for m in response.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

    def _known_names(self):
        """Kalitga ochiq modellar (1 soat kesh). Olib bo'lmasa None."""
        if self._names is None or time.time() - self._names_at > 3600:
            try:
                self._names = set(self._available_models())
                self._names_at = time.time()
            except (requests.exceptions.RequestException, ValueError):
                return None
        return self._names

    def _candidates(self) -> list:
        order = []
        configured = [] if self.model in SLOW_ALIASES else [self.model]
        for m in (self._resolved_model, *configured, *MODEL_CANDIDATES):
            if m and m not in order:
                order.append(m)
        names = self._known_names()
        if names:
            order = [m for m in order if m in names] or order
        now = time.time()
        fresh = [m for m in order if self._bad.get(m, 0) < now]
        return fresh or order[:1]

    def _mark_bad(self, model: str, reason: str):
        self._bad[model] = time.time() + BAD_MODEL_TTL[reason]
        if self._resolved_model == model:
            self._resolved_model = None
        logger.warning(f"Gemini modeli '{model}' vaqtincha chetlab o'tiladi ({reason}).")

    def _generate_with_fallback(self, payload: dict) -> requests.Response:
        """Modellarni navbat bilan sinaydi, birinchi ishlaganini eslab qoladi."""
        last_response, last_error = None, None
        tried = 0
        for model in self._candidates():
            if tried >= MAX_MODELS_PER_REQUEST:
                break
            tried += 1
            try:
                response = self._post_generate(model, payload)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                self._mark_bad(model, "timeout")
                last_error = e
                continue
            if response.status_code == 200:
                if self._resolved_model != model:
                    logger.info(f"Gemini modeli: {model}")
                self._resolved_model = model
                return response
            if response.status_code == 404:
                # Bu kalitga yopilgan model: darhol javob qaytaradi, shuning uchun urinishlar soniga
                # kirmaydi (aks holda 2 ta band model + 1 ta 404 bo'lsa, Gemini butunlay ishlamay qolardi)
                self._mark_bad(model, "not_found")
                last_response = response
                tried -= 1
                continue
            if response.status_code in RETRY_STATUS_CODES:
                self._mark_bad(model, "busy")
                last_response = response
                continue
            return response  # 400/401/403: kalit yoki so'rov xatosi, boshqa model yordam bermaydi
        if last_response is not None:
            return last_response
        if last_error is not None:
            raise last_error
        raise RuntimeError("Mos Gemini modeli topilmadi.")

    def diagnose(self) -> dict:
        """Har bir nomzod modelni kichik so'rov bilan tekshiradi (/diag uchun). Kalit chiqarilmaydi."""
        payload = {"contents": [{"role": "user", "parts": [{"text": "Faqat OK deb javob ber."}]}]}
        names = self._known_names()
        info = {"model": self._resolved_model or self.model, "tested": []}
        order = []
        configured = [] if self.model in SLOW_ALIASES else [self.model]
        for m in (*configured, *MODEL_CANDIDATES):
            if m not in order and (names is None or m in names):
                order.append(m)
        working = None
        for model in order[:6]:
            start = time.time()
            try:
                r = requests.post(
                    f"{API_BASE_URL}/models/{model}:generateContent",
                    json=payload, headers=self._headers(), timeout=30,
                )
                status = r.status_code
                if status != 200:
                    try:
                        msg = str((r.json().get("error") or {}).get("message", ""))[:120]
                    except ValueError:
                        msg = ""
                    if status in (400, 401, 403) and "error" not in info:
                        info["error"] = msg
            except requests.exceptions.RequestException as e:
                status = type(e).__name__
            info["tested"].append(f"{model}: {status}, {round(time.time() - start, 1)} s")
            if status == 200 and working is None:
                working = model
            if working and len(info["tested"]) >= 2:
                break
        if working:
            self._resolved_model = working
            self._bad.pop(working, None)
        info["working"] = working
        info["status"] = 200 if working else "xato"
        if names is not None:
            info["flash_models"] = sorted(n for n in names if "flash" in n)[:12]
        else:
            info["models_error"] = "ro'yxat olinmadi"
        return info

    @staticmethod
    def _extract_text(data: dict) -> str:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise KeyError("text")
        return text

    def generate_text(self, system_instruction: str, user_message: str) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        }

        try:
            response = self._generate_with_fallback(payload)
            response.raise_for_status()
            return self._extract_text(response.json())

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "noma'lum"
            logger.error(f"Gemini HTTP xatosi, kod: {status}")
            if status == 429:
                return ("[Gemini xatosi] Gemini so'rovlar limiti tugadi (kod 429). "
                        "Bir necha daqiqadan keyin qayta yozing. Takrorlansa, Google AI Studio'da "
                        "kalit limitini tekshirish kerak.")
            if status in RETRY_STATUS_CODES:
                return f"[Gemini xatosi] Gemini serveri hozir band (kod {status}). Bir daqiqadan so'ng qayta yozing."
            return f"[Gemini xatosi] So'rov bajarilmadi (kod: {status})."
        except requests.exceptions.RequestException as e:
            logger.error(f"Gemini ulanish xatosi: {type(e).__name__}")
            return (f"[Gemini xatosi] Gemini bilan bog'lanib bo'lmadi ({type(e).__name__}). "
                    "Birozdan so'ng qayta urinib ko'ring.")
        except RuntimeError as e:
            logger.error(f"Gemini model tanlash xatosi: {e}")
            return "[Gemini xatosi] Mos model topilmadi."
        except (KeyError, IndexError):
            return "[Gemini xatosi] Javobni o'qib bo'lmadi (masalan, xavfsizlik filtri bloklagan bo'lishi mumkin)."

    def generate_json(self, system_instruction: str, user_message: str, temperature: float = 0.9):
        """
        Javobni JSON ko'rinishida oladi (komentlarga javob tizimi uchun).
        Qaytaradi: dict/list yoki None (javob bloklangan yoki o'qib bo'lmagan).
        Xato bo'lsa GeminiError ko'taradi.
        """
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        try:
            response = self._generate_with_fallback(payload)
        except requests.exceptions.RequestException as e:
            raise GeminiError(f"Gemini bilan bog'lanib bo'lmadi ({type(e).__name__})")
        except RuntimeError as e:
            raise GeminiError(str(e))

        if response.status_code != 200:
            raise GeminiError(
                f"Gemini HTTP {response.status_code}",
                fatal=response.status_code in (400, 401, 403),
            )
        try:
            candidates = response.json().get("candidates") or []
            if not candidates:
                return None
            return json.loads(self._extract_text({"candidates": candidates}))
        except (ValueError, KeyError, IndexError, TypeError):
            return None
