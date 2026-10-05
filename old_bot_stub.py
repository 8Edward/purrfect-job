"""The old bot after the move to @PurrfectJob_bot: answers anything with «we moved» and a link.

Runs on `old_bot_token` from config.yml (the token the bot had before), in its own process and session
(telegram/old_bot.session), so it never shares a token with the main bot. On start it also takes the
mini-app button off the old bot's menu (for everyone and for each known person), so nobody lands in an
app that no longer answers there.

Autostart:  powershell -ExecutionPolicy Bypass -File telegram\\old_bot_autostart.ps1
"""

import asyncio
import logging

from telethon import Button, TelegramClient, events, functions, types

import guests
import tg_collect as tc

NEW_BOT = "PurrfectJob_bot"
TEXT = (f"🐾 <b>Бот переехал</b>\nТеперь Purrfect Job живёт здесь: @{NEW_BOT}\n"
        f"Откройте его и нажмите «Старт» — ваши настройки и вакансии на месте.\n\n"
        f"🐾 <b>The bot has moved</b>\nPurrfect Job now lives at @{NEW_BOT}. Open it and press Start: "
        f"your settings and vacancies are all there.")
SESSION = tc.TG_DIR / "old_bot"
LOG_PATH = tc.ROOT / "data" / "old-bot.log"
log = logging.getLogger("old-bot")


async def drop_app_buttons(bot):
    """The old menu button opened the mini app: back to the plain menu, for everyone and every guest."""
    await bot(functions.bots.SetBotMenuButtonRequest(user_id=types.InputUserEmpty(), button=types.BotMenuButtonCommands()))
    for g in guests.all_guests(guests.load_registry()):
        await reset_button(bot, g.id)


async def reset_button(bot, uid):
    try:
        await bot(functions.bots.SetBotMenuButtonRequest(user_id=await bot.get_input_entity(uid), button=types.BotMenuButtonCommands()))
    except Exception as e:  # someone the old bot never met: nothing to reset
        log.info("menu button for %s: %s", uid, e)


async def main():
    cfg = tc.read_yaml(tc.CONFIG_PATH)
    token = str(cfg.get("old_bot_token") or "").strip()
    if not token:
        raise SystemExit("config.yml has no old_bot_token: nothing to run")
    if token == str(cfg.get("bot_token") or "").strip():
        raise SystemExit("old_bot_token is the same as bot_token: the main bot already uses it")
    bot = TelegramClient(str(SESSION), int(cfg["api_id"]), cfg["api_hash"])
    await bot.start(bot_token=token)
    try:
        await drop_app_buttons(bot)
    except Exception as e:
        log.warning("menu buttons not reset: %s", e)

    reset = set()

    @bot.on(events.NewMessage(incoming=True))
    async def moved(event):
        if event.is_private:
            if event.sender_id not in reset:  # the owner too: their own app button goes on first contact
                reset.add(event.sender_id)
                await reset_button(bot, event.sender_id)
            await event.respond(TEXT, parse_mode="html", buttons=[Button.url(f"Открыть @{NEW_BOT}", f"https://t.me/{NEW_BOT}")])

    @bot.on(events.CallbackQuery())
    async def moved_button(event):  # an old inline button in the chat
        await event.answer(f"Бот переехал: @{NEW_BOT}", alert=True)

    log.info("old bot stub running")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
