"""
O'z-o'zini tuzatadigan miya testlari: qorovul, soatlik tekshiruv, kunlik o'rganish, HUD.
Ishga tushirish: python -m tests.test_selfheal
"""
import os
import sys
import tempfile
import time

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jarvis-heal-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts  # noqa: E402
import audit  # noqa: E402
import brain  # noqa: E402
import selfheal  # noqa: E402


class AI:
    def __init__(self, res):
        self.res, self.calls = res, []

    def generate_json(self, system, message, temperature=0.9):
        self.calls.append(message)
        return self.res


class Resp:
    mode = "on"

    def __init__(self):
        self._counts = {"p1": 3, "p2": 1, "old": 5}
        self._recent_ids = ["p1", "p2"]


def test_watch_and_limits():
    assert not selfheal.paused()
    assert selfheal.note_rate_limit() == 5 and selfheal.paused()
    assert selfheal.note_rate_limit() == 10
    selfheal.note_ok()
    assert not selfheal.paused()
    r = Resp()
    d = selfheal.load()
    d["last_ok"] = time.time() - 3600
    selfheal.save(d)
    for _ in range(3):
        audit.record("comment", action="send_error", cid="x")
    notes = selfheal.watch(r, force=True)
    assert any("tekshirilmadi" in n for n in notes) and any("yuborilmadi" in n for n in notes), notes
    assert "p1" not in r._counts and "old" in r._counts      # oxirgi postlar qayta o'qitiladi
    assert selfheal.watch(r, force=True) == []                 # bir xil ogohlantirish takrorlanmaydi
    print("  ✅ qorovul: to'xtashni sezadi, limitda sekinlashadi, oxirgi postlarni qayta o'qitadi")


def test_review():
    audit.record("comment", action="sent", category="question", user="ona", post="p1",
                 text="Yetkazib berish bormi?", reply="Direktga yozing, aniqlab aytamiz 😊")
    with accounts.use(accounts.TOY):
        audit.record("comment", action="sent", category="positive", user="x", post="t1",
                     text="Zo'r", reply="Rahmat")
    ai = AI({"mistakes": [{"problem": "Yetkazib berish savoliga javob berilmadi",
                           "example": "Direktga yozing", "rule": "Yetkazib berish so'ralsa Toshkent bo'ylab 1 kunda deb ayt"},
                          {"problem": "takror", "rule": "Yetkazib berish so'ralganda Toshkent bo'ylab 1 kunda deb ayting"}]})
    res = selfheal.review(ai, force=True)
    assert res["checked"] == 1, res                 # o'yinchoq profili javobi asosiy tekshiruvga kirmaydi
    assert "Yetkazib berish savoliga" not in ai.calls[0].split("OXIRGI JAVOBLAR")[0]
    assert len(res["rules"]) == 1, res["rules"]     # o'xshash qoida ikki marta qo'shilmaydi
    assert brain.get_lessons()[-1].startswith(selfheal.AUTO_PREFIX)
    assert "🤖" in selfheal.review_text(res)
    assert selfheal.review(ai) is None             # soat o'tmaguncha qayta tekshirilmaydi
    with accounts.use(accounts.TOY):
        assert not any(x.startswith(selfheal.AUTO_PREFIX) for x in brain.get_lessons())
    print("  ✅ soatlik tekshiruv: xato topadi, qoida darhol qo'shiladi, akkauntlar aralashmaydi")


def test_rule_cap_and_delete():
    words = ["olma", "behi", "anor", "uzum", "nok", "gilos", "shaftoli", "o'rik", "qovun", "tarvuz", "limon",
             "apelsin", "banan", "anjir", "xurmo", "yong'oq", "bodom", "pista", "mayiz", "turshak"]
    for i in range(selfheal.MAX_AUTO_RULES + 3):
        a = words[i].replace("'", "")
        selfheal.add_rule(f"{a}xon {a}bek {a}jon {a}oy {a}gul")
    assert len(selfheal.auto_rules()) == selfheal.MAX_AUTO_RULES
    n = len(selfheal.auto_rules())
    selfheal.delete_auto(0)
    assert len(selfheal.auto_rules()) == n - 1
    print("  ✅ avto-qoidalar soni cheklangan, o'chirish ishlaydi")


def test_daily():
    now = time.time()
    audit.record("owner_edit", where="comment", customer="Narxi?", draft="Direktga yozdik 😊",
                 owner="Narxi 389 000 so'm, Chilonzor 9 da bor")
    audit.record("comment", action="sent", category="price", user="dilya", post="p9", text="Narxi?", reply="Direktga yozdik")
    audit.record("comment", action="sent", category="price", user="dilya", post="p9", text="Narxi qancha deyapman",
                 reply="Direktga qarang")

    class Mem:
        d = {"1": {"username": "aziz", "msgs": [{"t": now - 9 * 3600, "who": "mijoz", "text": "Lego bormi?"},
                                                 {"t": now - 8 * 3600, "who": "jarvis", "text": "Ha, bor"}]},
             "2": {"username": "lola", "msgs": [{"t": now - 600, "who": "mijoz", "text": "Tushunmadim, bot misiz?"}]},
             "3": {"username": "ali", "msgs": [{"t": now - 900, "who": "jarvis", "text": "Narxi 100"},
                                                {"t": now - 800, "who": "egasi", "text": "Assalomu alaykum, men yordam beraman"}]}}

    class Chat:
        memory = Mem()

    o = selfheal._outcomes(Chat(), now - 86400)
    assert len(o["edits"]) == 1 and len(o["repeats"]) == 1 and len(o["silent"]) == 1
    assert len(o["unhappy"]) == 1 and len(o["owner_took"]) == 1
    ai = AI({"worked": ["Aniq narx aytilganda mijoz davom etdi"], "failed": ["Narx o'rniga direktga yo'llash"],
             "mistakes": [{"problem": "Narx so'ralganda aniq narx aytilmadi",
                           "rule": "Narx so'ralsa FAKTLARdagi aniq narxni komentda ham qisqa ayt"}]})
    text = selfheal.daily(ai, Chat(), force=True)
    assert "Kunlik" in text and "tuzatdingiz" in text and "🤖" in text, text
    assert "Egasi yubordi" in ai.calls[0]
    assert selfheal.daily(ai, Chat()) is None       # bugun allaqachon qilingan
    print("  ✅ kunlik o'rganish: egasining tuzatishi, qayta savol, jimlik, norozilikdan saboq")


def test_owner_edit_logged():
    import auto_reply
    import instagram_api as ig
    r = auto_reply.CommentResponder(AI({}), state_path=os.path.join(os.environ["DATA_DIR"], "ar.json"))
    r.state.pending["c1"] = {"cid": "c1", "text": "Narxi?", "reply": "Direktga yozdik", "category": "price"}
    orig = ig.reply_to_comment
    ig.reply_to_comment = lambda cid, text: None
    try:
        r.approve("c1", "389 000 so'm")
    finally:
        ig.reply_to_comment = orig
    last = [x for x in audit.recent(50, kinds=("owner_edit",))][-1]
    assert last["owner"] == "389 000 so'm" and last["draft"] == "Direktga yozdik"
    print("  ✅ egasi javobni o'zgartirsa, saboq sifatida yoziladi")


def test_view():
    v = selfheal.view()
    assert v["counts"]["reviews"] >= 1 and v["rules"] and v["found"] and v["report"]
    print("  ✅ HUD ma'lumoti tayyor")


if __name__ == "__main__":
    test_watch_and_limits()
    test_review()
    test_rule_cap_and_delete()
    test_daily()
    test_owner_edit_logged()
    test_view()
    print("✅ O'z-o'zini tuzatish testlari o'tdi")
