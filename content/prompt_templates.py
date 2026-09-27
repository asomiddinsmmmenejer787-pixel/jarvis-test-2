"""
content/prompt_templates.py
----------------------------
Gemini'ga yuboriladigan tayyor prompt shablonlari.
Har bir funksiya bitta turdagi kontent uchun to'liq prompt matnini quradi.

Bu fayl faqat MATN tayyorlaydi — AI'ga so'rov yubormaydi.
"""

JARVIS_PERSONALITY = """
Sen Jarvis — SMM va marketing bo'yicha shaxsiy yordamchisan.
Asosiy tilingiz o'zbek tili. Kerak bo'lsa rus yoki ingliz tilida ham yoza olasan.

Xarakteringiz:
- professional va aniq
- qisqa, keraksiz gap qo'shmaysan
- kreativ, lekin haqiqatga yaqin
- foydalanuvchi bergan brend/mahsulot ma'lumotlariga qat'iy amal qilasan
- faktlarni o'ylab topmaysan

Agar so'rov uchun yetarli ma'lumot berilmagan bo'lsa, aniqlashtiruvchi savol ber
yoki "Bu ma'lumot menda yo'q" deb ayt — hech qachon narx yoki faktlarni to'qima.
"""


def build_script_prompt(topic: str) -> str:
    """Reels/video ssenariysi uchun to'liq prompt yaratadi."""
    return f"""
Quyidagi mavzu uchun qisqa video (Reels) ssenariysini yoz: "{topic}"

Ssenariy quyidagi tuzilishda bo'lsin:

1. HOOK — birinchi 2-3 soniyada e'tiborni tortadigan jumla
2. PROBLEM — mijoz duch keladigan muammo
3. RETENTION — tomoshabinni ushlab turadigan qism
4. PRODUCT / SOLUTION — mahsulot yechim sifatida taqdim etiladi
5. DESIRE — mahsulotni xohlash hissini kuchaytirish
6. CTA — aniq harakatga chaqiruv

Har bir bo'limni sarlavha bilan aniq ajrat. Ortiqcha cho'zma.
"""


def build_hook_prompt(topic: str) -> str:
    """Faqat hook (e'tibor tortuvchi jumla) yaratish uchun prompt."""
    return f"""
"{topic}" mavzusidagi video uchun 5 ta turli HOOK varianti yoz.
Har biri 1-2 jumladan iborat, birinchi soniyalarda diqqatni tortishi kerak.
Faqat hooklarni ro'yxat qilib ber, izoh yozma.
"""


def build_caption_prompt(topic: str, platform: str = "Instagram") -> str:
    """Ijtimoiy tarmoq posti uchun caption yaratish prompti."""
    return f"""
"{topic}" mavzusida {platform} uchun caption (post matni) yoz.
Qisqa, jonli, emoji bilan (ortiqcha emas), oxirida CTA bo'lsin.
"""


def build_telegram_post_prompt(topic: str) -> str:
    """Telegram kanali uchun post yaratish prompti."""
    return f"""
"{topic}" mavzusida Telegram kanal posti yoz.
Instagram'dan farqli o'laroq, biroz batafsilroq va aniq ma'lumot bilan yoz.
Oxirida aniq CTA (masalan: "Batafsil: link" yoki "Buyurtma uchun yozing") qo'sh.
"""


def build_ad_prompt(topic: str) -> str:
    """Reklama ssenariysi uchun prompt."""
    return f"""
"{topic}" mahsuloti uchun qisqa reklama ssenariysi yoz.
HOOK, PRODUCT DESIRE va CTA qismlarini aniq ajrat.
"""


def build_ideas_prompt(topic: str) -> str:
    """Kontent g'oyalari ro'yxati uchun prompt."""
    return f"""
"{topic}" mavzusi/mahsuloti bo'yicha 7 ta kontent g'oyasini ro'yxat qilib ber.
Har biri qisqa sarlavha va 1 jumlali tushuntirish bilan.
"""


def build_ai_video_prompt(topic: str) -> str:
    """AI video generatori (Veo va h.k.) uchun texnik prompt."""
    return f"""
"{topic}" mavzusida AI video generatori uchun texnik prompt yoz.
Quyidagi bo'limlar bilan, aniq va tushunarli qilib:

SCENE — joy va vaziyat tavsifi
CAMERA — kamera harakati (masalan: slow pan, tracking shot)
LIGHTING — yorug'lik sharoiti
CHARACTER — asosiy qahramon tavsifi (agar bo'lsa)
ACTION — nima sodir bo'ladi
DIALOGUE — agar kerak bo'lsa gap-so'zlar
SOUND — fon musiqasi yoki tovush effektlari
CONTINUITY — davomiylikni saqlash uchun eslatmalar
NEGATIVE PROMPT — nima bo'lmasligi kerak (masalan: distorted hands, extra limbs)

Cinematic klishelardan saqlan, mahsulot shaklini o'zgartirma degan eslatmani unutma.
"""
