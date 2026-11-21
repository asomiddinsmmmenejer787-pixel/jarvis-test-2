"""
humanizer_uz.py — Jarvis javoblarini "odamday" qiladi (komment va direkt uchun).

Ikki qismdan iborat:
  1) HUMAN_STYLE_RULES — Gemini'ga beriladigan uslub qoidalari (prompt).
  2) humanize(text, kind) — Gemini yozgan javobni yuborishdan oldin tozalaydi.

Mavjud kodga tegmaydi. Ulash uchun faqat 2 joy kerak (pastdagi "ULASH" bo'limiga qarang).
"""

import re

# ---------------------------------------------------------------------------
# 1) PROMPT: Gemini'ga beriladigan uslub qoidalari
# ---------------------------------------------------------------------------

HUMAN_STYLE_RULES = """
Sen Instagram do'konining tirik sotuvchisisan, robot emassan. Mijozga xuddi
Toshkentdagi tajribali sotuvchi telefonida yozganday javob ber.

QANDAY YOZISH KERAK:
- Avval savolga javob ber (narx, razmer, bor-yo'qligi), keyin kerak bo'lsa bitta savol ber.
- Qisqa yoz. Komment: 1-2 gap. Direkt: 1-3 gap. Ro'yxat, punkt, qalin harf ishlatma.
- Mijoz qaysi tilda va yozuvda yozsa (lotin/kirill, o'zbek/rus), shunda javob ber.
- Oddiy so'zlar ishlat: "bor", "yo'q", "yozing", "olib kelamiz", "qaysi razmer kerak?".
- Har safar boshqacha boshla. Har javobni "Assalomu alaykum" bilan boshlama:
  suhbat davom etayotgan bo'lsa salomni takrorlama.
- Emoji juda kam: ko'pi bilan 1 ta, ko'p javobda umuman yo'q.
- Undov belgisini (!) kam ishlat, ketma-ket "!!" hech qachon.
- Narx, razmer, manzil, yetkazish shartini faqat berilgan ma'lumotdan ol.
  Bilmasang, o'ylab topma: "aniqlab, hozir yozaman" de.
- Kommentda sotuvni bosib turma: qisqa javob ber va direktga chaqir.

HECH QACHON YOZMA (bular robotni fosh qiladi):
- "Albatta!", "Ajoyib savol!", "Savolingiz uchun rahmat!", "Hurmatli mijoz",
  "Sizga yordam berishdan mamnunmiz", "Agar boshqa savollaringiz bo'lsa, bemalol murojaat qiling",
  "Biz bilan bog'langaningiz uchun rahmat", "Umid qilamanki, bu yordam beradi".
- "nafaqat ..., balki ..." va "bu shunchaki ... emas, bu ..." qoliplari.
- Uchtalik maqtovlar: "sifatli, qulay va hamyonbop".
- Mijozning savolini qaytarib takrorlash ("Siz narx haqida so'radingiz...").
- Uzun tire (—), ro'yxat belgisi, **qalin**, sarlavha.
"""

# Mavjud promptlarga qo'shiladigan qisqa blok (ohangni o'zgartirmaydi, faqat robot izlarini taqiqlaydi)
TELLS_BLOCK = """
ROBOT EMASLIGING BILINMASIN
- Bu iboralarni hech qachon yozma: "Albatta!", "Ajoyib savol", "Savolingiz uchun rahmat", "Hurmatli mijoz",
  "Sizga yordam berishdan mamnunmiz", "bemalol murojaat qiling", "Umid qilamanki", "mamnuniyat bilan",
  "Qanday yordam bera olaman?". Mijoz noaniq yozsa, sotuvchidek so'ra: "Qaysi mahsulot qiziqtirdi?".
- Mijozning savolini qaytarib takrorlama. Ro'yxat, **qalin**, sarlavha ishlatma.
- Har javobni bir xil so'z bilan boshlama. Oddiy gapir: "bor", "yo'q", "yozing", "qaysi biri kerak?".
- Misol. Yomon: "Albatta! Ushbu mahsulot mavjud, mamnuniyat bilan yetkazib beramiz!" Yaxshi: "Bor hozir. Qaysi biri kerak?"
""".strip()

# Mijozga bitta misol ko'rsatish foydali (few-shot). Promptga qo'shsa bo'ladi.
HUMAN_EXAMPLES = """
Mijoz: Narxi qancha?
Yomon: Assalomu alaykum! Ajoyib savol! Ushbu mahsulotning narxi 300 000 so'mni tashkil etadi. Agar boshqa savollaringiz bo'lsa, bemalol murojaat qiling! 😊🙏
Yaxshi: 300 ming. Qaysi razmer kerak?

Mijoz: 32 bormi
Yomon: Albatta! Sizga 32-razmer mavjudligini mamnuniyat bilan ma'lum qilamiz!
Yaxshi: 32 bor hozircha. Olib qo'yaymi?

Mijoz (komment): zo'r ekan
Yomon: Fikringiz uchun katta rahmat! Biz har doim mijozlarimizni xursand qilishga intilamiz! ❤️🔥
Yaxshi: Rahmat) Savol bo'lsa direktga yozing.
"""

# ---------------------------------------------------------------------------
# 2) TOZALOVCHI: yuborishdan oldin oxirgi tekshiruv
# ---------------------------------------------------------------------------

# Javob boshida yoki oxirida tursa, olib tashlanadigan robot iboralar
_BANNED = [
    r"albatta[!,.]?",
    r"ajoyib savol[!,.]?",
    r"yaxshi savol[!,.]?",
    r"savolingiz uchun (katta )?rahmat[!,.]?",
    r"hurmatli mijoz[!,.]?",
    r"sizga yordam berishdan mamnun(miz|man)[!,.]?",
    r"agar (boshqa |qo'shimcha )?savollaringiz bo'lsa,? bemalol murojaat qiling[!,.]?",
    r"biz bilan bog'langaningiz uchun rahmat[!,.]?",
    r"umid qilamanki,? bu (sizga )?yordam beradi[!,.]?",
    r"mamnuniyat bilan[!,.]?",
    r"конечно[!,.]?",
    r"отличный вопрос[!,.]?",
    r"если у вас (остались|есть) вопросы,? (пишите|обращайтесь)[!,.]?",
]

# Chatbot savoli o'rniga do'kon sotuvchisi savoli
_HELP_Q = re.compile(r"(?:sizga\s+)?(?:qanday|nima(?:da|ga)?)\s+yordam\s+(?:bera|ko'rsata)\s+ola(?:man|miz)\s*\?", re.I)
_HELP_Q_RU = re.compile(r"чем\s+(?:я\s+|мы\s+)?(?:могу|можем)\s+(?:вам\s+)?помочь\s*\?", re.I)

_GREETING = re.compile(r"^\s*(assalomu alaykum|assalomu aleykum|salom|здравствуйте|привет)[!,.]?\s*", re.I)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF❤️]"
)

_LIMITS = {"comment": 200, "dm": 900}


def _strip_banned(text: str) -> str:
    for pat in _BANNED:
        text = re.sub(r"(^|(?<=[.!?]\s))\s*" + pat + r"\s*", r"\1", text, flags=re.I)
        text = re.sub(r"\s*" + pat + r"\s*$", "", text, flags=re.I)
    return text


def _limit_emoji(text: str, max_emoji: int) -> str:
    count = 0

    def keep(m):
        nonlocal count
        count += 1
        return m.group(0) if count <= max_emoji else ""

    return _EMOJI.sub(keep, text)


def _cut_to_sentence(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[: last + 1] if last > 40 else cut.rstrip() + "…"


_STOP = {"qaysi", "sizga", "sizda", "kerak", "bizda", "bormi", "qanday", "nechta", "aynan", "edi"}


def _stems(sentence):
    words = re.findall(r"[^\W\d_]+", re.sub(r"[ʻʼ'‘’`]", "", sentence.lower()))
    return {w[:5] for w in words if len(w) >= 5 and w not in _STOP}


def _drop_repeated_questions(t):
    """Mazmunan bir xil savollardan faqat oxirgisi qoladi."""
    chunks = re.split(r"((?<=[.!?])\s+)", t)
    parts, seps = chunks[0::2], chunks[1::2] + [""]
    qs = [i for i, p in enumerate(parts) if p.rstrip().endswith("?")]
    drop = set()
    for a_i, a in enumerate(qs):
        for b in qs[a_i + 1:]:
            if _stems(parts[a]) & _stems(parts[b]):
                drop.add(a)
                break
    if not drop:
        return t
    return "".join(p + seps[i] for i, p in enumerate(parts) if i not in drop).strip()


def humanize(text: str, kind: str = "dm", already_greeted: bool = False) -> str:
    """
    kind: "comment" yoki "dm"
    already_greeted: shu suhbatda salom allaqachon berilgan bo'lsa True
    """
    if not text:
        return text
    t = text.strip()

    # markdown, ro'yxat belgilari, sarlavhalar
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?m)^\s*([-•*]|\d+[.)])\s+", "", t)
    t = re.sub(r"(?m)^#+\s*", "", t)

    # uzun tire -> vergul
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = t.replace(" -- ", ", ")

    # robot iboralar
    t = _HELP_Q.sub("Qaysi mahsulot qiziqtirdi?", t)
    t = _HELP_Q_RU.sub("Какой товар вас интересует?", t)
    t = _strip_banned(t)

    # salomni takrorlamaslik
    if already_greeted:
        t = _GREETING.sub("", t)

    # "!!" -> "!", ko'p undovni kamaytirish
    t = re.sub(r"!{2,}", "!", t)
    if t.count("!") > 1:
        first = t.find("!")
        t = t[: first + 1] + t[first + 1 :].replace("!", ".")

    # bir xabarda bir xil savolni ikki marta bermaslik ("Qaysi o'lcham kerak? ... Qaysi o'lchamni tekshiraylik?")
    t = _drop_repeated_questions(t)

    # emoji: komment 1 ta, direkt 1 ta; ba'zan umuman yo'q
    t = _limit_emoji(t, 1)

    # bo'sh joylar va qatorlar
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"^[,.\s]+", "", t).strip()

    # uzunlik
    t = _cut_to_sentence(t, _LIMITS.get(kind, 600))

    # birinchi harf katta
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def style_prompt(kind: str = "dm") -> str:
    """Gemini'ga yuboriladigan promptga qo'shiladigan qism."""
    where = "Instagram KOMMENTIGA" if kind == "comment" else "Instagram DIREKTIGA"
    return f"Hozir {where} javob yozyapsan.\n{HUMAN_STYLE_RULES}\nMisollar:\n{HUMAN_EXAMPLES}"


# ---------------------------------------------------------------------------
# ULASH (mavjud kodda faqat 2 ta o'zgarish):
#
#   from humanizer_uz import style_prompt, humanize
#
#   1) Gemini'ga komment/direkt uchun prompt yuborayotgan joyda,
#      promptning boshiga qo'shing:
#         prompt = style_prompt("comment") + "\n\n" + prompt     # yoki "dm"
#
#   2) Javobni Instagram'ga yuborishdan oldin:
#         reply = humanize(reply, kind="comment")               # yoki "dm"
#         reply = humanize(reply, kind="dm", already_greeted=True)  # suhbat davom etsa
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    samples = [
        ("Assalomu alaykum! Ajoyib savol! Ushbu mahsulotning narxi — 300 000 so'm!! "
         "Agar boshqa savollaringiz bo'lsa, bemalol murojaat qiling! 😊🙏🔥", "comment", False),
        ("Albatta! **32-razmer** mavjud — sizga mamnuniyat bilan yetkazib beramiz! 😊😊", "dm", True),
        ("Assalomu alaykum! Ha, bor. Qaysi tumandasiz?", "dm", True),
    ]
    for s, k, g in samples:
        print("OLDIN :", s)
        print("KEYIN :", humanize(s, k, g))
        print()
