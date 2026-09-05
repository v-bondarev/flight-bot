"""Рендер-сервис: отдаёт HTML страницы после исполнения её JS в настоящем браузере.

Нужен для табло, которые отдают контент только браузеру (VKO: JS-челлендж →
куки → reload). Ничего не эмулируем — страница исполняется как у посетителя.
Слушает только localhost; наружу — через SSH-туннель (-L) с сервера бота.

    GET /render?url=<https://…>&wait=<мс после загрузки>&selector=<css, ждать>
    → {"url": <финальный url>, "html": <content>}
"""
import asyncio
import os

from aiohttp import web
from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_one_at_a_time = asyncio.Semaphore(1)   # Chromium тяжёлый; табло опрашиваются редко


async def render(request: web.Request) -> web.Response:
    url = request.query.get("url", "")
    if not url.startswith(("http://", "https://")):
        return web.json_response({"error": "url"}, status=400)
    wait = min(int(request.query.get("wait", "8000")), 30000)
    selector = request.query.get("selector")
    async with _one_at_a_time:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            try:
                ctx = await browser.new_context(user_agent=UA, locale="ru-RU")
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(wait)          # челлендж → reload
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:  # noqa: BLE001 — сеть может не затихнуть, HTML всё равно берём
                    pass
                if selector:
                    try:
                        await page.wait_for_selector(selector, timeout=15000)
                    except Exception:  # noqa: BLE001
                        pass
                return web.json_response({"url": page.url, "html": await page.content()})
            finally:
                await browser.close()


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


app = web.Application(client_max_size=0)
app.add_routes([web.get("/render", render), web.get("/health", health)])

if __name__ == "__main__":
    web.run_app(app, host=os.getenv("BIND", "0.0.0.0"), port=int(os.getenv("PORT", "8765")))
