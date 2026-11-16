"""
ai/gemini.py
------------
Google Gemini API bilan ishlaydigan haqiqiy AI provider.

Xavfsizlik:
- API key URL ichiga emas, so'rov sarlavhasiga (x-goog-api-key) qo'yiladi.
- Xato matnlariga URL yoki kalit hech qachon chiqarilmaydi.

Barqarorlik:
- Gemini vaqtincha band bo'lsa (503, 429 va h.k.), so'rov avtomatik qayta yuboriladi.
- Agar GEMINI_MODEL topilmasa (404), provider mavjud "flash" modelni o'zi tanlaydi.
"""

import json
import logging
import time
import requests
from ai.base import AIProvider

logger = logging.getLogger(__name__)

API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
import os

# Yangi Gemini modellari uzoq "o'ylashi" mumkin, katta tahlil uchun 30 soniya kam
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("GEMINI_TIMEOUT", "150") or 150)
PREFERRED_ALIAS = "gemini-2.5-flash"
# Bu nomlar Google'ning o'zgaruvchan taxalluslari; ba'zan juda sekin modelga ulanadi.
# Shuning uchun ularning o'rniga barqaror modelni ishlatamiz.
UNSTABLE_ALIASES = {"gemini-flash-latest", "gemini-pro-latest"}
# Vaqtinchalik xatolar: shu kodlar chiqsa, qayta urinib ko'riladi
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
# Har bir qayta urinishdan oldin kutish (soniya). 3 ta qayta urinish.
RETRY_DELAYS_SECONDS = (2, 5, 10)
# Matn generatsiyasi uchun mos kelmaydigan model turlari
EXCLUDED_MODEL_KEYWORDS = (
    "image", "tts", "live", "audio", "embedding", "vision",
    "robotics", "computer", "native",
)


class GeminiError(RuntimeError):
    """generate_json() xatosi. fatal=True bo'lsa (kalit noto'g'ri va h.k.), qayta urinish foydasiz."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class GeminiProvider(AIProvider):
    """Google Gemini API orqali javob generatsiya qiluvchi provider."""

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GeminiProvider uchun api_key kerak.")
        self.api_key = api_key
        if model in UNSTABLE_ALIASES:
            logger.warning(f"'{model}' o'rniga barqaror '{PREFERRED_ALIAS}' ishlatiladi.")
            model = PREFERRED_ALIAS
        self.model = model
        self._resolved_model = None  # 404 bo'lsa, avtomatik tanlangan model shu yerda saqlanadi

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _post_generate(self, model: str, payload: dict) -> requests.Response:
        """So'rov yuboradi. Vaqtinchalik xato bo'lsa, bir necha marta qayta uriniladi."""
        url = f"{API_BASE_URL}/models/{model}:generateContent"

        def send() -> requests.Response:
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
            if response.status_code not in RETRY_STATUS_CODES:
                break
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

    def _alternate_model(self, exclude: str):
        """Hozirgi model ishlamasa, o'rniga boshqa barqaror flash modelni tanlaydi."""
        try:
            names = self._available_models()
        except (requests.exceptions.RequestException, ValueError):
            return None
        if PREFERRED_ALIAS in names and PREFERRED_ALIAS != exclude:
            return PREFERRED_ALIAS
        flash = [n for n in names if "flash" in n and n != exclude
                 and not any(k in n for k in EXCLUDED_MODEL_KEYWORDS)
                 and "preview" not in n and "exp" not in n and "latest" not in n]
        full = [n for n in flash if "lite" not in n]
        pool = full or flash
        return sorted(pool)[-1] if pool else None

    def _generate_with_fallback(self, payload: dict) -> requests.Response:
        """Asosiy model bilan urinadi; band/uzilish bo'lsa, boshqa modelga o'tadi."""
        model = self._resolved_model or self.model
        try:
            response = self._post_generate(model, payload)
            if response.status_code == 404 and not self._resolved_model:
                model = self._pick_available_model()
                self._resolved_model = model
                logger.warning(f"'{self.model}' topilmadi. '{model}' ishlatilmoqda.")
                response = self._post_generate(model, payload)
            if response.status_code not in RETRY_STATUS_CODES:
                return response
            failure = None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            failure = e
            response = None

        alt = self._alternate_model(exclude=model)
        if not alt:
            if failure:
                raise failure
            return response
        logger.warning(f"'{model}' ishlamadi, '{alt}' modeliga o'tilmoqda.")
        alt_response = self._post_generate(alt, payload)
        if alt_response.status_code == 200:
            self._resolved_model = alt
        return alt_response

    def diagnose(self) -> dict:
        """Gemini'ni kichik so'rov bilan tekshiradi (/diag uchun). Kalitni hech qayerga chiqarmaydi."""
        info = {"model": self._resolved_model or self.model, "timeout": REQUEST_TIMEOUT_SECONDS}
        payload = {"contents": [{"role": "user", "parts": [{"text": "Faqat OK deb javob ber."}]}]}
        start = time.time()
        try:
            r = requests.post(
                f"{API_BASE_URL}/models/{info['model']}:generateContent",
                json=payload, headers=self._headers(), timeout=60,
            )
            info["status"] = r.status_code
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code != 200:
                info["error"] = str((body.get("error") or {}).get("message", ""))[:300]
        except requests.exceptions.RequestException as e:
            info["status"] = type(e).__name__
        info["seconds"] = round(time.time() - start, 1)
        if info.get("status") != 200:
            alt = self._alternate_model(exclude=info["model"])
            if alt:
                start = time.time()
                try:
                    r = requests.post(
                        f"{API_BASE_URL}/models/{alt}:generateContent",
                        json=payload, headers=self._headers(), timeout=60,
                    )
                    info["alt"] = f"{alt}: {r.status_code}, {round(time.time() - start, 1)} s"
                    if r.status_code == 200:
                        self._resolved_model = alt
                except requests.exceptions.RequestException as e:
                    info["alt"] = f"{alt}: {type(e).__name__}"
        try:
            names = self._available_models()
            info["flash_models"] = [n for n in names if "flash" in n][:10]
            info["model_exists"] = info["model"] in names
        except requests.exceptions.HTTPError as e:
            info["models_error"] = e.response.status_code if e.response is not None else "?"
        except requests.exceptions.RequestException as e:
            info["models_error"] = type(e).__name__
        return info

    def _pick_available_model(self) -> str:
        """Kalit uchun mavjud modellardan mos bittasini tanlaydi."""
        names = self._available_models()

        if PREFERRED_ALIAS in names:
            return PREFERRED_ALIAS

        flash = [
            n for n in names
            if "flash" in n and n not in UNSTABLE_ALIASES
            and not any(k in n for k in EXCLUDED_MODEL_KEYWORDS)
        ]
        stable = [n for n in flash if "preview" not in n and "exp" not in n]
        full_size = [n for n in (stable or flash) if "lite" not in n]
        pool = full_size or stable or flash
        if not pool:
            raise RuntimeError("Mos Gemini modeli topilmadi.")
        return sorted(pool)[-1]

    def generate_text(self, system_instruction: str, user_message: str) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        }

        try:
            response = self._generate_with_fallback(payload)
            response.raise_for_status()
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

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
            text = candidates[0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (ValueError, KeyError, IndexError, TypeError):
            return None
