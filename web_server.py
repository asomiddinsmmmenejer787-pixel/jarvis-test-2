```python
import os
import threading
import logging

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn

logger = logging.getLogger(__name__)

web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@web.get("/")
async def root(request: Request):
    code = request.query_params.get("code")

    if code:
        # OAuth kodini logga chiqarmang.
        logger.info("Instagram OAuth callback qabul qilindi.")

        # Hozircha faqat callback kelganini tasdiqlaydi.
        # Kodni xavfsiz saqlash/almashtirish alohida bajariladi.
        return PlainTextResponse(
            "Instagram ruxsat javobi qabul qilindi. "
            "Bu oynani yopishingiz mumkin."
        )

    return PlainTextResponse("Jarvis ishlayapti")


def _run():
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(web, host="0.0.0.0", port=port)


def start_web_server():
    thread = threading.Thread(
        target=_run,
        daemon=True,
        name="jarvis-web-server",
    )
    thread.start()
