"""
core/router.py
---------------
Foydalanuvchi kiritgan buyruqni (/script, /caption va h.k.) aniqlaydi
va tegishli parametr (mavzu) bilan birga qaytaradi.

Bu modul HECH QANDAY AI chaqirmaydi — faqat matnni tahlil qiladi.
"""

from dataclasses import dataclass

# Qo'llab-quvvatlanadigan buyruqlar ro'yxati
SUPPORTED_COMMANDS = [
    "/script",
    "/hook",
    "/caption",
    "/telegram",
    "/ad",
    "/ideas",
    "/ai_video",
]

DEFAULT_COMMAND = "/content"  # buyruq berilmasa, umumiy kontent so'rovi deb hisoblanadi


@dataclass
class ParsedCommand:
    command: str  # masalan "/script"
    topic: str    # buyruqdan keyingi matn


def parse_input(user_text: str) -> ParsedCommand:
    """
    Foydalanuvchi matnini buyruq va mavzuga ajratadi.

    Masalan:
        "/script elektr samokat" -> command="/script", topic="elektr samokat"
        "Bolalar uchun caption yoz" -> command="/content", topic="Bolalar uchun caption yoz"
    """
    text = user_text.strip()

    for command in SUPPORTED_COMMANDS:
        if text.startswith(command):
            topic = text[len(command):].strip()
            return ParsedCommand(command=command, topic=topic)

    # Agar aniq buyruq berilmagan bo'lsa, butun matnni mavzu sifatida olamiz
    return ParsedCommand(command=DEFAULT_COMMAND, topic=text)
