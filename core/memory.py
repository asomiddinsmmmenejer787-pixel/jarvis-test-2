"""
core/memory.py
---------------
Oddiy session memory — foydalanuvchi bilan bo'lgan suhbat tarixini
JSON fayl orqali saqlaydi. Murakkab vector database yoki RAG hozircha
kerak emas, lekin kelajakda shunga almashtirish oson bo'ladigan
sodda interfeys bilan yozilgan.
"""

import os
import json
from datetime import datetime
from typing import List, Dict


class Memory:
    """Bitta foydalanuvchi/session uchun suhbat tarixini boshqaradi."""

    def __init__(self, memory_dir: str, session_id: str = "default"):
        os.makedirs(memory_dir, exist_ok=True)
        self.file_path = os.path.join(memory_dir, f"{session_id}.json")
        self.history: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        """Fayldan mavjud tarixni o'qiydi, bo'lmasa bo'sh ro'yxat qaytaradi."""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def add(self, role: str, text: str):
        """Yangi xabarni tarixga qo'shadi va faylga saqlaydi."""
        self.history.append(
            {
                "role": role,  # "user" yoki "jarvis"
                "text": text,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self._save()

    def get_recent(self, limit: int = 10) -> List[Dict]:
        """Oxirgi N ta xabarni qaytaradi (kontekst uchun)."""
        return self.history[-limit:]

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
