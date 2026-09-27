"""
core/models.py
--------------
Dastur bo'ylab ishlatiladigan umumiy data strukturalar.
Bular oddiy "data konteyner" klasslar — mantiq emas, faqat shakl.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """Foydalanuvchidan kelgan bitta xabar."""
    text: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Reply:
    """Jarvis tomonidan qaytariladigan javob."""
    text: str
    source: str = "ai"  # "ai", "mock", "error" kabi qiymatlar bo'lishi mumkin
