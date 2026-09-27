"""
content/content_engine.py
--------------------------
Umumiy kontent so'rovlarini boshqaradi: caption, telegram post, g'oyalar, reklama.
Har bir metod tegishli prompt shablonini quradi va AI provider orqali javob oladi.
"""

from ai.base import AIProvider
from content.prompt_templates import (
    JARVIS_PERSONALITY,
    build_caption_prompt,
    build_telegram_post_prompt,
    build_ad_prompt,
    build_ideas_prompt,
)


class ContentEngine:
    """Ssenariydan tashqari barcha kontent turlarini generatsiya qiluvchi modul."""

    def __init__(self, ai_provider: AIProvider):
        self.ai_provider = ai_provider

    def generate_caption(self, topic: str, platform: str = "Instagram") -> str:
        prompt = build_caption_prompt(topic, platform)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)

    def generate_telegram_post(self, topic: str) -> str:
        prompt = build_telegram_post_prompt(topic)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)

    def generate_ad(self, topic: str) -> str:
        prompt = build_ad_prompt(topic)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)

    def generate_ideas(self, topic: str) -> str:
        prompt = build_ideas_prompt(topic)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)
