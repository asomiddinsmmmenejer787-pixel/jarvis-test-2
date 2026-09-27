# Jarvis — SMM va Marketing AI Yordamchisi

## 1. Jarvis nima?

Jarvis — SMM va marketing ishlari uchun mo'ljallangan shaxsiy AI yordamchi.
U kontent g'oyalari, video ssenariylari, caption va reklama matnlari
yaratishga yordam beradi. Kelajakda Instagram va Telegram orqali mijozlar
bilan muloqotni ham avtomatlashtiradi.

## 2. Features (Stage 1)

- ✅ Video/Reels ssenariysi yaratish (`/script`)
- ✅ Hook variantlari yaratish (`/hook`)
- ✅ Instagram caption yaratish (`/caption`)
- ✅ Telegram post yaratish (`/telegram`)
- ✅ Reklama ssenariysi yaratish (`/ad`)
- ✅ Kontent g'oyalari ro'yxati (`/ideas`)
- ✅ AI video (Veo va h.k.) uchun texnik prompt (`/ai_video`)
- ✅ Suhbat tarixini saqlash (memory)
- ✅ Mock Instagram/Telegram — kelajakdagi integratsiya uchun tayyor arxitektura
- ✅ Gemini yoki Mock AI provider tanlash imkoniyati

**Keyingi bosqichlar:** mijoz xabarlarini avtomatik kategoriyalash,
real Instagram/Telegram integratsiyasi, mahsulotlar bazasi, analitika.

## 3. Requirements

- Python 3.9 yoki undan yuqori
- Internet aloqasi (faqat Gemini rejimida)
- Google Gemini API key (bepul: https://aistudio.google.com)

## 4. Installation (Windows)

Terminal (Command Prompt yoki PowerShell) orqali:

```
cd jarvis
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 5. .env sozlash

1. `.env.example` faylidan nusxa oling va nomini `.env` deb o'zgartiring
2. `.env` faylini oching va quyidagilarni to'ldiring:

```
GEMINI_API_KEY=sizning_api_kalitingiz
GEMINI_MODEL=gemini-2.0-flash
AI_PROVIDER=gemini
```

Agar API key hali bo'lmasa, sinab ko'rish uchun `AI_PROVIDER=mock` qoldiring —
bu holda internet yoki key kerak emas, lekin javoblar soxta bo'ladi.

## 6. Run (ishga tushirish)

```
python main.py
```

Terminalda savolingizni yozing:

```
Siz: /script elektr samokat uchun reklama
```

Chiqish uchun `exit` yoki `quit` yozing.

## 7. Testlarni ishga tushirish

```
python -m tests.test_stage1
```

Bu Mock AI bilan ishlaydi — internet yoki API key shart emas.

## 8. Project structure

```
jarvis/
├── main.py                  # Kirish nuqtasi (CLI)
├── config.py                # .env sozlamalarini o'qiydi
├── core/
│   ├── jarvis.py             # Asosiy "miya"
│   ├── router.py             # Buyruqlarni aniqlaydi
│   ├── memory.py             # Suhbat tarixi
│   └── models.py             # Umumiy data strukturalar
├── ai/
│   ├── base.py                # AIProvider interfeysi
│   ├── gemini.py               # Gemini implementatsiyasi
│   └── mock.py                 # Test uchun soxta AI
├── content/
│   ├── content_engine.py       # Caption, post, g'oyalar
│   ├── script_engine.py        # Video ssenariylari
│   └── prompt_templates.py     # Tayyor prompt shablonlari
├── integrations/
│   ├── base.py                  # MessagingProvider interfeysi
│   ├── mock_instagram.py        # Soxta Instagram
│   └── mock_telegram.py         # Soxta Telegram
├── data/
│   ├── products.json            # Mahsulotlar (hozircha bo'sh)
│   └── memory/                   # Suhbat tarixi shu yerda saqlanadi
└── tests/
    └── test_stage1.py            # Asosiy testlar
```

## 9. Future integrations (kelajakdagi rejalar)

- **Stage 2:** Mijoz xabarlarini avtomatik kategoriyalash (narx, yetkazib berish va h.k.), mahsulotlar bazasi
- **Stage 3:** Real Telegram integratsiyasi
- **Stage 4:** Real Instagram integratsiyasi (Meta Graph API)
- **Stage 5:** Analitika, lead ajratish, to'liq database

## 10. Security

- `.env` fayli hech qachon GitHub'ga yuklanmaydi (`.gitignore`da bloklangan)
- API key, token yoki parol hech qachon kod ichiga yozilmaydi
- Loyihani boshqalar bilan bo'lishishdan oldin `.env` faylingiz yo'qligiga ishonch hosil qiling
