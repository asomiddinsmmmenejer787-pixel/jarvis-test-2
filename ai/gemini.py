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

import logging
import time
import requests
from ai.base import AIProvider

logger = logging.getLogger(__name__)

API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
REQUEST_TIMEOUT_SECONDS = 30
PREFERRED_ALIAS = "gemini-flash-latest"
# Vaqtinchalik xatolar: shu kodlar chiqsa, qayta urinib ko'riladi
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
# Har bir qayta urinishdan oldin kutish (soniya). 3 ta qayta urinish.
RETRY_DELAYS_SECONDS = (2, 5, 10)
# Matn generatsiyasi uchun mos kelmaydigan model turlari
EXCLUDED_MODEL_KEYWORDS = (
    "image", "tts", "live", "audio", "embedding", "vision",
    "robotics", "computer", "native",
)


class GeminiProvider(AIProvider):
    """Google Gemini API orqali javob generatsiya qiluvchi provider."""

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GeminiProvider uchun api_key kerak.")
        self.api_key = api_key
        self.model = model
        self._resolved_model = None  # 404 bo'lsa, avtomatik tanlangan model shu yerda saqlanadi

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _post_generate(self, model: str, payload: dict) -> requests.Response:
        """So'rov yuboradi. Vaqtinchalik xato bo'lsa, bir necha marta qayta uriniladi."""
        url = f"{API_BASE_URL}/models/{model}:generateContent"

        def send() -> requests.Response:
            return requests.post(
                url, json=payload, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS
            )

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

    def _pick_available_model(self) -> str:
        """Kalit uchun mavjud modellardan mos bittasini tanlaydi."""
        response = requests.get(
            f"{API_BASE_URL}/models",
            headers=self._headers(),
            params={"pageSize": 200},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        names = [
            m["name"].split("/", 1)[1]
            for m in response.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

        if PREFERRED_ALIAS in names:
            return PREFERRED_ALIAS

        flash = [
            n for n in names
            if "flash" in n and not any(k in n for k in EXCLUDED_MODEL_KEYWORDS)
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
            model = self._resolved_model or self.model
            response = self._post_generate(model, payload)

            if response.status_code == 404 and not self._resolved_model:
                model = self._pick_available_model()
                self._resolved_model = model
                logger.warning(
                    f"'{self.model}' topilmadi. '{model}' ishlatilmoqda. "
                    f"Doimiy qilish uchun Railway'da GEMINI_MODEL={model} qiling."
                )
                response = self._post_generate(model, payload)

            response.raise_for_status()
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "noma'lum"
            logger.error(f"Gemini HTTP xatosi, kod: {status}")
            if status in RETRY_STATUS_CODES:
                return "[Gemini xatosi] Gemini serveri hozir band. Bir daqiqadan so'ng qayta yozing."
            return f"[Gemini xatosi] So'rov bajarilmadi (kod: {status})."
        except requests.exceptions.RequestException as e:
            logger.error(f"Gemini ulanish xatosi: {type(e).__name__}")
            return "[Gemini xatosi] Gemini bilan bog'lanib bo'lmadi. Birozdan so'ng qayta urinib ko'ring."
        except RuntimeError as e:
            logger.error(f"Gemini model tanlash xatosi: {e}")
            return "[Gemini xatosi] Mos model topilmadi."
        except (KeyError, IndexError):
            return "[Gemini xatosi] Javobni o'qib bo'lmadi (masalan, xavfsizlik filtri bloklagan bo'lishi mumkin)."
