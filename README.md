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

## 9. Telegram bot + Instagram (Railway)

Bot `python telegram_runner.py` bilan ishga tushadi (Procfile'da yozilgan).

### Railway Variables

| O'zgaruvchi | Majburiy | Nima uchun |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ha | Telegram bot tokeni |
| `AI_PROVIDER` | ha | `gemini` |
| `GEMINI_API_KEY` | ha | Google AI Studio kaliti |
| `GEMINI_MODEL` | yo'q | masalan `gemini-2.5-flash` |
| `INSTAGRAM_ACCESS_TOKEN` | Instagram uchun | Meta'dan olingan token (Instagram Login) |
| `OWNER_CHAT_ID` | tavsiya | Botdan faqat shu odam foydalanadi. ID'ni `/myid` ko'rsatadi |
| `DATA_DIR` | tavsiya | Railway Volume ulangan papka, masalan `/data` |
| `AUTO_REPLY_MODE` | yo'q | Boshlang'ich rejim: `test` (standart), `on`, `off` |
| `MAX_PER_DAY` / `MAX_PER_RUN` | yo'q | Kuniga / bir tekshiruvda avtomatik javoblar (40 / 8) |
| `MAX_AGE_HOURS` | yo'q | Necha soatlik komentlarga javob beriladi (24) |
| `RECENT_POSTS` | yo'q | Komentlari tekshiriladigan oxirgi postlar soni (10) |
| `CHECK_EVERY_SEC` | yo'q | Komentlarni tekshirish oralig'i, soniya (600) |
| `WEEKLY_REPORT` / `WEEKLY_REPORT_HOUR` | yo'q | Dushanba kungi hisobot: `1`/`0`, soat (10) |
| `BRAND_NAME` | yo'q | Komentlarga javobda brend nomi |

**Volume:** Railway → servis → Settings → Volumes → `/data` papkasini ulang va `DATA_DIR=/data` qo'ying.
Shunda suhbat tarixi, komentlar holati va yangilangan Instagram tokeni deploy'dan keyin o'chmaydi.

### Buyruqlar

- `/insta [son]` — oxirgi kontentlar statistikasi + Jarvis tahlili
- `/post 1` yoki `/post <havola>` — bitta postni chuqur tahlil
- `/stories` — faol storylar statistikasi
- `/autoreply on|test|off` — komentlarga javob rejimi
- `/pending` — ruxsat kutayotgan komentlar
- `/myid` — Telegram ID

### Mini ilova (Jarvis HUD)

Telegram'da bot oynasidagi **Jarvis HUD** tugmasi yoki `/app` buyrug'i ochadi.
Bo'limlar: HUD (akkaunt ko'rsatkichlari), TAHLIL (akkaunt, bitta post, storylar),
KONTENT (Jarvis bilan ishlash: ssenariy, hook, caption...), KOMENT (avtojavob rejimi, ruxsat kutayotganlar).
Har bir vazifaning bosqichlari jonli ko'rsatiladi (faqat umumiy bosqich nomlari).

- Manzil avtomatik: `https://<RAILWAY_PUBLIC_DOMAIN>/app` (yoki `WEBAPP_URL` bilan o'zgartiriladi).
- Kirish faqat Telegram imzosi (initData) orqali va faqat bot egasi uchun.
- Dizaynni brauzerda ko'rish (soxta ma'lumot bilan): `/app?demo=1`.

### Postlar bo'yicha ma'lumot va direkt xabar

- HUD → POSTLAR: oxirgi 30 post sana-vaqt, rasm va opisaniya bilan. Har postga mahsulot nomi,
  narx va qo'shimcha ma'lumot kiritiladi; opisaniyadan foydalanishni o'chirish mumkin.
  Pastda do'kon ma'lumotlari (manzil, telefon, yetkazib berish...) — hamma postga qo'shiladi.
- Komentga javob va direkt xabar shu komment yozilgan postning FAKTLARiga tayanadi:
  egasi kiritgan ma'lumot (eng ishonchli) + opisaniya + do'kon ma'lumotlari.
- Narx so'ralsa: ochiq javob "narxni direktga yubordik", direktga esa aniq javob.
  Oddiy savolga postda javob bo'lsa, ochiq javobda ham qisqa javob beriladi.
- Direkt matnidagi har bir raqam faktlar bilan tekshiriladi. Mos kelmasa yoki postda javob
  bo'lmasa, xabar avtomatik yuborilmaydi — egasiga tasdiqqa tushadi.
- Rejim: `/direkt tasdiq` (standart), `/direkt avto`, `/direkt off`.
  Token `instagram_business_manage_messages` ruxsatiga ega bo'lishi kerak.

### Komentlarga javob qanday ishlaydi

- Har 10 daqiqada oxirgi postlardagi yangi komentlar tekshiriladi.
- Narx, savol, maqtov, shikoyat — `on` rejimida Jarvis o'zi javob yozadi.
- Raqobatchi, so'kingan, haqoratli yoki Jarvis ishonchi past komentlar egasiga
  Telegram'da tugmalar bilan keladi: Yuborish / Yashirish / Javobsiz qoldirish.
  O'z javobingizni yozish uchun shu xabarga Reply qiling.
- Javobda narx, link, @, hashtag bo'lsa — yuborilmaydi.
- Instagram tokeni har ~20 kunda avtomatik yangilanadi.

### Testlar

```
python -m tests.test_instagram
python -m tests.test_webapp
python -m tests.test_direkt
```

## 10. Future integrations (kelajakdagi rejalar)

- **Stage 2:** Mijoz xabarlarini avtomatik kategoriyalash (narx, yetkazib berish va h.k.), mahsulotlar bazasi
- **Stage 3:** Real Telegram integratsiyasi
- **Stage 4:** Real Instagram integratsiyasi (Meta Graph API)
- **Stage 5:** Analitika, lead ajratish, to'liq database

## 11. Security

- `.env` fayli hech qachon GitHub'ga yuklanmaydi (`.gitignore`da bloklangan)
- API key, token yoki parol hech qachon kod ichiga yozilmaydi
- Loyihani boshqalar bilan bo'lishishdan oldin `.env` faylingiz yo'qligiga ishonch hosil qiling
