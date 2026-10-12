python
import os
import threading
import logging

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn

logger = logging.getLogger(__name__)

web = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@web.get("/")
async def root(request: Request):
    code = request.query_params.get("code")

    if code:
        # OAuth kodini logga yozmaymiz.
        logger.info("OAuth callback qabul qilindi.")
        return PlainTextResponse(
            "Ruxsat javobi qabul qilindi. Bu oynani yopishingiz mumkin."
        )

    error = request.query_params.get("error")
    if error:
        logger.warning("OAuth ruxsati rad etildi yoki xatolik yuz berdi.")
        return PlainTextResponse(
            "Instagram ruxsati olinmadi. Jarayonni qayta tekshiring.",
            status_code=400,
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
