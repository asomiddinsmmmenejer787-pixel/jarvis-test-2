"""
core/jarvis.py
---------------
Jarvis'ning "miyasi". Foydalanuvchi so'rovini qabul qiladi:
- /script, /hook, /caption kabi buyruq bo'lsa, tegishli engine'ga yo'naltiradi;
- buyruqsiz oddiy xabar bo'lsa, suhbat (chat) rejimida javob beradi.
Javobni memory'ga saqlab, qaytaradi.
"""

from core.router import parse_input, DEFAULT_COMMAND
from core.memory import Memory
from content.content_engine import ContentEngine
from content.script_engine import ScriptEngine
from content.prompt_templates import JARVIS_PERSONALITY
from ai.base import AIProvider

# Suhbat kontekstiga nechta oxirgi xabar qo'shiladi
CHAT_HISTORY_LIMIT = 8
# Kontekstdagi har bir eski xabar shu uzunlikdan oshsa, qisqartiriladi
MAX_HISTORY_MESSAGE_CHARS = 600
# AI xato qaytarsa, javob shu belgi bilan boshlanadi (bunday javob xotiraga yozilmaydi)
ERROR_PREFIX = "[Gemini xatosi]"

CHAT_STYLE_RULES = """
Suhbat qoidalari:
- Foydalanuvchi bilan oddiy, tabiiy suhbat qur. Salomga salom qaytar.
- Javoblar qisqa va aniq bo'lsin.
- Markdown belgilarini (**, ##, __) ishlatma, oddiy matn yoz. Ro'yxat kerak bo'lsa, raqam yoki tire ishlat.
- Foydalanuvchi ssenariy, caption, post yoki g'oya so'rasa, to'liq yozib ber.
"""


class Jarvis:
    """Barcha modullarni bog'lovchi asosiy klass."""

    def __init__(self, ai_provider: AIProvider, memory_dir: str):
        self.ai_provider = ai_provider
        self.content_engine = ContentEngine(ai_provider)
        self.script_engine = ScriptEngine(ai_provider)
        self.memory = Memory(memory_dir)

    def handle(self, user_text: str) -> str:
        """
        Foydalanuvchi xabarini qabul qilib, mos javobni qaytaradi.
        Bu Jarvis bilan ishlashning yagona kirish nuqtasi.
        """
        parsed = parse_input(user_text)
        history = self.memory.get_recent(CHAT_HISTORY_LIMIT)

        reply = self._route_to_engine(parsed.command, parsed.topic, history)

        # Xato javoblar xotiraga yozilmaydi, keyingi suhbatni buzmasligi uchun
        if not reply.startswith(ERROR_PREFIX):
            self.memory.add("user", user_text)
            self.memory.add("jarvis", reply)
        return reply

    def _route_to_engine(self, command: str, topic: str, history: list) -> str:
        """Buyruqqa qarab tegishli engine metodini chaqiradi."""
        if command == DEFAULT_COMMAND:
            return self._chat(topic, history)

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
        else:
            return self.content_engine.generate_ideas(topic)

    def _chat(self, user_text: str, history: list) -> str:
        """Buyruqsiz xabarga oldingi suhbatni hisobga olib, oddiy suhbat javobini beradi."""
        lines = []
        for item in history:
            speaker = "Foydalanuvchi" if item["role"] == "user" else "Jarvis"
            lines.append(f"{speaker}: {item['text'][:MAX_HISTORY_MESSAGE_CHARS]}")

        if lines:
            message = (
                "Oldingi suhbat:\n" + "\n".join(lines)
                + f"\n\nFoydalanuvchining yangi xabari:\n{user_text}"
            )
        else:
            message = user_text

        return self.ai_provider.generate_text(JARVIS_PERSONALITY + CHAT_STYLE_RULES, message)
