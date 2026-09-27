"""
ai/gemini.py
------------
Google Gemini API bilan ishlaydigan haqiqiy AI provider.
"""

import requests
from ai.base import AIProvider


class GeminiProvider(AIProvider):
    """Google Gemini API orqali javob generatsiya qiluvchi provider."""

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GeminiProvider uchun api_key kerak.")
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def generate_text(self, system_instruction: str, user_message: str) -> str:
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        }

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.RequestException as e:
            return f"[Gemini xatosi] API bilan bog'lanib bo'lmadi: {e}"
        except (KeyError, IndexError):
            return "[Gemini xatosi] Javobni o'qib bo'lmadi. API formati o'zgargan bo'lishi mumkin."
