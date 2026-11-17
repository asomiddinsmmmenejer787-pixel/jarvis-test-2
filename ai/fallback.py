"""
ai/fallback.py
--------------
Bir nechta AI provayderni ketma-ket ishlatadi: birinchisi (Gemini) xato bersa yoki limiti tugasa,
avtomatik keyingisiga (Groq) o'tadi. Ishlamagan provayder bir necha daqiqa chetlab o'tiladi,
shunda har so'rovda limitga qayta-qayta urilib vaqt yo'qotilmaydi.
"""

import logging
import time

from ai.base import AIError, AIProvider, is_error_text

logger = logging.getLogger(__name__)
COOLDOWN = 5 * 60


class FallbackProvider(AIProvider):
    def __init__(self, providers):
        self.providers = [p for p in providers if p is not None]
        self._cool = {}
        self.last_used = None

    # webapp/HUD uchun: hozir qaysi AI ishlayapti
    @property
    def model(self):
        p = self.last_used or self.providers[0]
        return f"{getattr(p, 'name', type(p).__name__)}: {getattr(p, '_resolved_model', None) or getattr(p, 'model', '')}"

    def _order(self):
        now = time.time()
        ready = [p for p in self.providers if self._cool.get(id(p), 0) < now]
        cooling = [p for p in self.providers if p not in ready]
        return ready + cooling

    def _fail(self, p, why):
        self._cool[id(p)] = time.time() + COOLDOWN
        logger.warning(f"{getattr(p, 'name', type(p).__name__)} ishlamadi ({why}), keyingi AI'ga o'tilmoqda.")

    def _usable(self, p):
        return not hasattr(p, "configured") or p.configured()

    def generate_text(self, system_instruction: str, user_message: str) -> str:
        last = "[AI xatosi] Hech bir AI javob bermadi."
        for p in self._order():
            if not self._usable(p):
                continue
            text = p.generate_text(system_instruction, user_message)
            if not is_error_text(text):
                self.last_used = p
                return text
            self._fail(p, text[:80])
            last = text
        return last

    def generate_json(self, system_instruction: str, user_message: str, temperature: float = 0.9):
        errors = []
        got_none = False
        for p in self._order():
            if not self._usable(p) or not hasattr(p, "generate_json"):
                continue
            try:
                res = p.generate_json(system_instruction, user_message, temperature)
            except AIError as e:
                self._fail(p, str(e))
                errors.append(e)
                continue
            if res is None:
                got_none = True  # bloklangan yoki o'qib bo'lmadi: keyingisini sinaymiz
                continue
            self.last_used = p
            return res
        if got_none or not errors:
            return None
        # Hamma provayder ishlamadi. Hammasining kaliti noto'g'ri bo'lsagina fatal.
        raise AIError("; ".join(str(e) for e in errors), fatal=all(getattr(e, "fatal", False) for e in errors))

    def diagnose(self) -> dict:
        return {getattr(p, "name", type(p).__name__): p.diagnose() for p in self.providers if hasattr(p, "diagnose")}
