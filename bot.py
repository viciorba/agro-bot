# ───────────────────────── ВЕБ-СЕРВЕР ДЛЯ RENDER ─────────────────────────
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


# ───────────────────────── ЗАПУСК ─────────────────────────
async def main():
    if not BOT_TOKEN or not ADMIN_IDS or not CHANNEL_ID:
        raise SystemExit("Задайте переменные BOT_TOKEN, ADMIN_IDS и CHANNEL_ID")
    init_db()
    
    # Запускаем веб-сервер для Render
    await web_server()
    
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
