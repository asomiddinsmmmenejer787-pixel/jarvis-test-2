"""
content/script_engine.py
--------------------------
Video/Reels ssenariylari va AI video promptlari uchun maxsus modul.
ContentEngine'dan alohida, chunki ssenariy generatsiyasi o'zining
maxsus tuzilishiga (HOOK, RETENTION, CTA va h.k.) ega.
"""

from ai.base import AIProvider
from content.prompt_templates import (
    JARVIS_PERSONALITY,
    build_script_prompt,
    build_hook_prompt,
    build_ai_video_prompt,
)


class ScriptEngine:
    """Video ssenariylari va AI video promptlarini generatsiya qiluvchi modul."""

    def __init__(self, ai_provider: AIProvider):
        self.ai_provider = ai_provider

    def generate_script(self, topic: str) -> str:
        """To'liq Reels ssenariysini yaratadi (HOOK -> CTA)."""
        prompt = build_script_prompt(topic)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)

    def generate_hook(self, topic: str) -> str:
        """Faqat bir nechta hook variantlarini yaratadi."""
        prompt = build_hook_prompt(topic)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)

    def generate_ai_video_prompt(self, topic: str) -> str:
        """AI video generatori (Veo va h.k.) uchun texnik prompt yaratadi."""
        prompt = build_ai_video_prompt(topic)
        return self.ai_provider.generate_text(JARVIS_PERSONALITY, prompt)
