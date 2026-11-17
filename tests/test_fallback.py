"""
tests/test_fallback.py
----------------------
Gemini -> Groq zaxira tizimi testlari (soxta serverlar bilan).

Ishga tushirish:
    python -m tests.test_fallback
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-fb-")
os.environ.pop("GROQ_API_KEY", None)

import requests  # noqa: E402

import ai.gemini as gem  # noqa: E402
import ai.groq as groq  # noqa: E402
from ai.base import AIError  # noqa: E402
from ai.fallback import FallbackProvider  # noqa: E402
from tests.test_instagram import check  # noqa: E402


class R:
    def __init__(self, code, body):
        self.status_code = code
        self.b = body

    def json(self):
        return self.b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


class World:
    def __init__(self):
        self.gemini = "ok"       # ok | 429 | 403
        self.groq = "ok"         # ok | 429 | 401
        self.groq_json_mode = True
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        if "googleapis" in url:
            self.calls.append("gemini")
            if self.gemini == "ok":
                text = '{"category":"positive","confidence":0.9,"reply":"G"}' if "generationConfig" in json else "Gemini javobi"
                return R(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})
            code = int(self.gemini)
            return R(code, {"error": {"message": "quota"}})
        self.calls.append("groq:" + json["model"])
        if self.groq != "ok":
            return R(int(self.groq), {"error": {"message": "x"}})
        if "response_format" in json and not self.groq_json_mode:
            return R(400, {"error": {"message": "response_format not supported"}})
        text = ('<think>hmm</think>Mana: {"category":"price","confidence":0.8,"reply":"Q"}'
                if "response_format" not in json and "JSON" in json["messages"][0]["content"]
                else ('{"category":"price","confidence":0.8,"reply":"Q"}' if "response_format" in json else "Groq javobi"))
        return R(200, {"choices": [{"message": {"content": text}}]})

    def get(self, url, headers=None, params=None, timeout=None):
        if "googleapis" in url:
            return R(200, {"models": [{"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]}]})
        if (headers or {}).get("Authorization", "").endswith("BAD"):
            return R(401, {})
        return R(200, {"data": [{"id": m} for m in ("openai/gpt-oss-120b", "llama-3.3-70b-versatile")]})


def run():
    w = World()
    for mod in (gem.requests, groq.requests):
        mod.post, mod.get = w.post, w.get
    gem.time.sleep = lambda s: None
    gem.MIN_INTERVAL_SECONDS = 0

    ai = FallbackProvider([gem.GeminiProvider("k", "gemini-3.5-flash"), groq.GroqProvider()])

    print("\n1) Groq ulanmagan")
    check(ai.generate_text("s", "u") == "Gemini javobi", "Gemini ishlasa Gemini javob beradi")
    w.gemini = "429"
    out = ai.generate_text("s", "u")
    check(out.startswith("[Gemini xatosi]") and not any(c.startswith("groq") for c in w.calls),
          "Groq kaliti yo'q bo'lsa, unga murojaat qilinmaydi")

    print("\n2) Groq ulandi")
    try:
        groq.check_key("gsk_BAD")
        check(False, "Yaroqsiz kalit qabul qilinmasligi kerak")
    except groq.GroqError as e:
        check(e.fatal, "Yaroqsiz Groq kaliti rad etildi")
    check(groq.check_key("gsk_GOOD") == 2, "Kalit tekshirildi")
    groq.save_api_key("gsk_GOOD")
    check(groq.get_api_key() == "gsk_GOOD", "Kalit saqlandi")
    ai = FallbackProvider([gem.GeminiProvider("k", "gemini-3.5-flash"), groq.GroqProvider()])
    w.calls.clear()
    check(ai.generate_text("s", "u") == "Groq javobi" and "groq:openai/gpt-oss-120b" in w.calls,
          "Gemini limiti tugaganda Groq javob berdi")
    check(ai.model.startswith("Groq"), "HUD'da hozirgi AI ko'rinadi")
    w.calls.clear()
    ai.generate_text("s", "u")
    check(w.calls and w.calls[0].startswith("groq"), "Ishlamagan Gemini bir muddat chetlab o'tildi (vaqt tejaladi)")
    res = ai.generate_json("s", "<komment>x</komment>")
    check(res == {"category": "price", "confidence": 0.8, "reply": "Q"}, "JSON javob Groq'dan olindi")

    print("\n3) Groq xatolari")
    w.groq = "429"
    w.calls.clear()
    ai2 = FallbackProvider([groq.GroqProvider()])
    ai2.generate_text("s", "u")
    check(w.calls == ["groq:openai/gpt-oss-120b", "groq:llama-3.3-70b-versatile"],
          "Groq modeli limitga ursa, keyingi Groq modeli sinaldi")
    w.groq = "ok"
    w.groq_json_mode = False
    g = groq.GroqProvider()
    check(g.generate_json("s", "u") == {"category": "price", "confidence": 0.8, "reply": "Q"},
          "JSON rejimini qo'llamaydigan modeldan ham JSON ajratib olindi (<think> tozalandi)")
    w.gemini, w.groq = "429", "429"
    ai3 = FallbackProvider([gem.GeminiProvider("k", "gemini-3.5-flash"), groq.GroqProvider()])
    try:
        ai3.generate_json("s", "u")
        check(False, "Xato ko'tarilishi kerak edi")
    except AIError as e:
        check(not e.fatal, "Ikkalasi ham limitda: vaqtinchalik xato (fatal emas)")
    w.gemini, w.groq = "403", "401"
    ai4 = FallbackProvider([gem.GeminiProvider("k", "gemini-3.5-flash"), groq.GroqProvider()])
    try:
        ai4.generate_json("s", "u")
    except AIError as e:
        check(e.fatal, "Ikkala kalit ham yaroqsiz: egasiga ogohlantirish (fatal)")

    print("\n4) Telegram /groqkey")
    w.gemini, w.groq = "ok", "ok"
    os.environ["OWNER_CHAT_ID"] = "777"
    import config
    config.OWNER_CHAT_ID = 777
    import telegram_runner as tr
    out = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            out.append(text)

    class Msg:
        deleted = False
        text = "gsk_" + "A" * 40
        reply_to_message = None

        async def delete(self):
            Msg.deleted = True

        async def reply_text(self, t, **k):
            out.append(t)

    class U:
        id = 777

    class Upd:
        effective_user = U()
        effective_chat = U()
        message = Msg()
        effective_message = message
        callback_query = None

    class Ctx:
        args = []
        bot = Bot()

    asyncio.run(tr.handle_message(Upd(), Ctx()))
    check(Msg.deleted and out[-1].startswith("✅ Groq ulandi") and groq.get_api_key() == "gsk_" + "A" * 40,
          "Kalitni oddiy xabar qilib yuborsa ham ulandi va xabar o'chirildi")

    print("\n✅ Zaxira AI testlari o'tdi")


if __name__ == "__main__":
    run()
