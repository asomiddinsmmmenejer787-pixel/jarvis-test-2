"""
core/jarvis.py
---------------
Jarvis'ning "miyasi". Foydalanuvchi so'rovini qabul qiladi, router orqali
qaysi turdagi kontent kerakligini aniqlaydi, tegishli engine'ga yo'naltiradi
va javobni memory'ga saqlab, qaytaradi.
"""

from core.router import parse_input
from core.memory import Memory
from content.content_engine import ContentEngine
from content.script_engine import ScriptEngine
from ai.base import AIProvider


class Jarvis:
    """Barcha modullarni bog'lovchi asosiy klass."""

    def __init__(self, ai_provider: AIProvider, memory_dir: str):
        self.content_engine = ContentEngine(ai_provider)
        self.script_engine = ScriptEngine(ai_provider)
        self.memory = Memory(memory_dir)

    def handle(self, user_text: str) -> str:
        """
        Foydalanuvchi xabarini qabul qilib, mos javobni qaytaradi.
        Bu Jarvis bilan ishlashning yagona kirish nuqtasi.
        """
        parsed = parse_input(user_text)
        self.memory.add("user", user_text)

        reply = self._route_to_engine(parsed.command, parsed.topic)

        self.memory.add("jarvis", reply)
        return reply

    def _route_to_engine(self, command: str, topic: str) -> str:
        """Buyruqqa qarab tegishli engine metodini chaqiradi."""
        if not topic:
            return (
                "Iltimos, mavzuni ham kiriting. Masalan:\n"
                "/script elektr samokat uchun reklama"
            )

        if command == "/script":
            return self.script_engine.generate_script(topic)
        elif command == "/hook":
            return self.script_engine.generate_hook(topic)
        elif command == "/ai_video":
            return self.script_engine.generate_ai_video_prompt(topic)
        elif command == "/caption":
            return self.content_engine.generate_caption(topic)
        elif command == "/telegram":
            return self.content_engine.generate_telegram_post(topic)
        elif command == "/ad":
            return self.content_engine.generate_ad(topic)
        elif command == "/ideas":
            return self.content_engine.generate_ideas(topic)
        else:
            # Aniq buyruq berilmagan bo'lsa, umumiy g'oyalar sifatida javob beramiz
            return self.content_engine.generate_ideas(topic)
