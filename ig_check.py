"""
ig_check.py
-----------
Instagram ulanishini tekshirish uchun qisqa skript. Hech narsa yubormaydi,
faqat o'qiydi.

Ishlatish (Railway shell yoki lokal, env o'zgaruvchilar o'rnatilgan holda):
    python ig_check.py
"""

import sys

from instagram_api import InstagramError, check_connection


def main():
    try:
        info = check_connection()
    except InstagramError as e:
        print(f"XATOLIK: {e}")
        if e.fatal:
            print("Bu token yoki ruxsat muammosi. Qayta urinish foydasiz.")
        sys.exit(1)

    print("Instagram ulanishi ishlayapti.")
    print(f"Akkaunt: @{info['username']}")
    print(f"Obunachilar: {info['followers']}")
    print(f"Postlar soni: {info['media_count']}")
    print(f"So'nggi postlar (tekshirildi): {info['recent_posts']}")


if __name__ == "__main__":
    main()
