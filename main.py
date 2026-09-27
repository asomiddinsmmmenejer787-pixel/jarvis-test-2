"""
main.py
-------
Jarvis'ni ishga tushirish uchun kirish nuqtasi.

Ishlatish:
    python main.py

Terminalda savolingizni yozing, Jarvis javob beradi.
Chiqish uchun: exit yoki quit yozing.

Qo'llab-quvvatlanadigan buyruqlar:
    /script <mavzu>     - to'liq video ssenariysi
    /hook <mavzu>       - faqat hook variantlari
    /caption <mavzu>    - Instagram caption
    /telegram <mavzu>   - Telegram post
    /ad <mavzu>         - reklama ssenariysi
    /ideas <mavzu>      - kontent g'oyalari
    /ai_video <mavzu>   - AI video generatori uchun texnik prompt

Buyruqsiz yozsangiz ("Bolalar uchun post yoz"), Jarvis uni umumiy
kontent so'rovi sifatida qabul qiladi.
"""

import sys
import config
from core.jarvis import Jarvis
from ai.gemini import GeminiProvider
from ai.mock import MockProvider


def build_ai_provider():
    """config.py'dagi sozlamalarga qarab tegishli AI provider'ni yaratadi."""
    if config.AI_PROVIDER == "gemini":
        return GeminiProvider(api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL)
    else:
        return MockProvider()


def print_welcome():
    print("=" * 50)
    print("  JARVIS — SMM va Marketing Yordamchisi")
    print("=" * 50)
    print(f"AI provider: {config.AI_PROVIDER}")
    print("Chiqish uchun 'exit' yoki 'quit' yozing.\n")
    print("Buyruqlar: /script /hook /caption /telegram /ad /ideas /ai_video")
    print("Yoki oddiy tilda so'rov yozing.\n")


def main():
    try:
        config.validate_config()
    except RuntimeError as e:
        print(f"XATOLIK: {e}")
        sys.exit(1)

    ai_provider = build_ai_provider()
    jarvis = Jarvis(ai_provider=ai_provider, memory_dir=config.MEMORY_DIR)

    print_welcome()

    while True:
        try:
            user_input = input("Siz: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nJarvis to'xtatildi.")
            break

        if user_input.lower() in ("exit", "quit"):
            print("Xayr!")
            break

        if not user_input:
            continue

        reply = jarvis.handle(user_input)
        print(f"\nJarvis:\n{reply}\n")


if __name__ == "__main__":
    main()
