"""Personal Telegram bot: the interface to the Telegram job collector.

- Every `interval_minutes` (default 15) while the computer is on, reads only
  the messages that arrived since the last check in the chats from
  channels.txt, and alerts you with a card per new job post:
  👍 В работу  -> added to data/pipeline.md for /career-ops pipeline
  👎 Скрыть    -> logged to data/discard.log, never shown again
- Commands: /scan /new /list /add /remove /help
- Answers only you (the account logged in via login.bat).

Run: pyw -3 telegram/bot.py   (no console; log in data/tg-bot.log)
"""

import asyncio
import html
import json
import logging
from datetime import datetime, timedelta, timezone

from telethon import Button, TelegramClient, events

import tg_collect as tc

INBOX_PATH = tc.ROOT / "data" / "tg-inbox.json"
DISCARD_LOG = tc.ROOT / "data" / "discard.log"
LOG_PATH = tc.ROOT / "data" / "tg-bot.log"
BOT_SESSION = tc.TG_DIR / "bot"

HELP = """<b>Бот вакансий из Telegram</b>

Каждые 15 минут я проверяю новые сообщения в чатах из списка
и присылаю алерт, если нашлась вакансия.
👍 — в работу (попадёт в career-ops на оценку), 👎 — скрыть.

/scan — собрать прямо сейчас
/new — показать ещё не разобранные
/list — список чатов
/add <i>@канал, ссылка или название</i> — добавить чат
/remove <i>то же или номер из /list</i> — убрать чат
/help — эта справка"""

log = logging.getLogger("tg-bot")


# ---------------------------------------------------------------- storage

def load_inbox():
    if INBOX_PATH.exists():
        return json.loads(INBOX_PATH.read_text(encoding="utf-8"))
    return {}


def save_inbox(inbox):
    INBOX_PATH.parent.mkdir(exist_ok=True)
    INBOX_PATH.write_text(json.dumps(inbox, ensure_ascii=False, indent=1), encoding="utf-8")


def job_key(job):
    return f"{abs(job['chat_id'])}_{job['msg_id']}"


def from_inbox(item):
    job = dict(item)
    job["date"] = datetime.fromisoformat(item["date"])
    return job


def to_inbox(job):
    item = dict(job, status="new")
    item["date"] = job["date"].isoformat()
    return item


# ---------------------------------------------------------------- channels.txt

def read_channel_lines():
    if not tc.CHANNELS_PATH.exists():
        return []
    return tc.CHANNELS_PATH.read_text(encoding="utf-8-sig").splitlines()


def add_channel(name):
    lines = read_channel_lines()
    if tc.channel_key(name) in {tc.channel_key(c) for c in tc.load_channels(tc.CHANNELS_PATH)}:
        return False
    lines.append(name)
    tc.CHANNELS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def remove_channel(arg):
    active = tc.load_channels(tc.CHANNELS_PATH)
    if arg.isdigit() and 1 <= int(arg) <= len(active):
        arg = active[int(arg) - 1]
    key = tc.channel_key(arg)
    lines = read_channel_lines()
    kept = [l for l in lines if l.strip().startswith("#") or not l.strip() or tc.channel_key(l) != key]
    if len(kept) == len(lines):
        return None
    tc.CHANNELS_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return arg


# ---------------------------------------------------------------- cards

def card_text(job):
    tags = " ".join(f"#{t.replace('-', '_')}" for t in job["tags"])
    body = job["text"] if len(job["text"]) <= 1500 else job["text"][:1500] + "…"
    head = f"<b>{html.escape(tc.guess_title(job['text']), quote=False)}</b>\n💬 {html.escape(job['chat'], quote=False)} · {job['date']:%d.%m %H:%M}"
    return f"{head}\n{tags}\n\n{html.escape(body, quote=False)}".replace("\n\n\n", "\n\n")


def card_buttons(job, key):
    row = [Button.inline("👍 В работу", f"t:{key}".encode()), Button.inline("👎 Скрыть", f"h:{key}".encode())]
    rows = [row]
    if job.get("link"):
        rows.append([Button.url("🔗 Открыть пост", job["link"])])
    return rows


# ---------------------------------------------------------------- bot

class JobBot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.user = tc.user_client(cfg)
        self.bot = TelegramClient(str(BOT_SESSION), int(cfg["api_id"]), cfg["api_hash"])
        self.bot.parse_mode = None  # plain text unless a call asks for html: chat names contain _ and *
        self.owner = None
        self.lock = asyncio.Lock()
        self.last_attempt = None
        self.failing = False
        self.reported_missing = set()  # warn about a missing chat once, not every 15 minutes

    async def start(self):
        await self.user.connect()
        if not await self.user.is_user_authorized():
            raise SystemExit("Нет входа в Telegram: сначала запусти telegram\\login.bat")
        self.owner = (await self.user.get_me()).id
        await self.bot.start(bot_token=self.cfg["bot_token"])

        mine = {"from_users": self.owner}
        self.bot.add_event_handler(self.on_help, events.NewMessage(pattern=r"^/(start|help)\b", **mine))
        self.bot.add_event_handler(self.on_scan, events.NewMessage(pattern=r"^/scan\b", **mine))
        self.bot.add_event_handler(self.on_new, events.NewMessage(pattern=r"^/new\b", **mine))
        self.bot.add_event_handler(self.on_list, events.NewMessage(pattern=r"^/list\b", **mine))
        self.bot.add_event_handler(self.on_add, events.NewMessage(pattern=r"^/add\b", **mine))
        self.bot.add_event_handler(self.on_remove, events.NewMessage(pattern=r"^/remove\b", **mine))
        self.bot.add_event_handler(self.on_button, events.CallbackQuery())
        log.info("bot started, owner %s", self.owner)

    async def say(self, text, **kw):
        try:
            await self.bot.send_message(self.owner, text, parse_mode="html", link_preview=False, **kw)
        except Exception:
            log.exception("cannot message owner — did you press Start in the bot chat?")

    # ------------------------------------------------------------ scanning

    async def run_scan(self, manual=False):
        if self.lock.locked():
            if manual:
                await self.say("Уже собираю, подожди минуту.")
            return
        async with self.lock:
            self.cfg = tc.load_config()  # pick up keyword edits without a restart
            state = tc.load_state()
            jobs, missing = await tc.scan_chats(self.user, self.cfg, tc.job_filter_from(self.cfg), state)
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            tc.save_state(state)

            inbox = load_inbox()
            for job in jobs:
                inbox[job_key(job)] = to_inbox(job)
            save_inbox(inbox)
            log.info("scan: %d new job(s), %d missing chat(s)", len(jobs), len(missing))

            if jobs:
                await self.say(f"🔔 <b>Новых вакансий: {len(jobs)}</b>")
            for job in jobs:
                await self.say(card_text(job), buttons=card_buttons(job, job_key(job)), silent=True)
            if missing and (manual or set(missing) != self.reported_missing):
                self.reported_missing = set(missing)
                names = "\n".join(f"• {html.escape(n, quote=False)}" for n in missing)
                await self.say(f"⚠️ Не нашёл эти чаты из списка (опечатка или ты в них не состоишь):\n{names}")
            if manual and not jobs:
                await self.say("Новых вакансий нет.")

    async def scheduler(self):
        """Scan every interval_minutes. Checks the wall clock once a minute instead of one long
        sleep, so a laptop waking up from sleep scans right away rather than an interval later."""
        while True:
            interval = timedelta(minutes=self.cfg.get("interval_minutes", 15))
            last = tc.load_state().get("last_run")
            due = [datetime.fromisoformat(last)] if last else []
            if self.last_attempt:
                due.append(self.last_attempt)  # after a failure, retry an interval later, not every minute
            if not due or datetime.now(timezone.utc) - max(due) >= interval:
                await self.safe_scan()
            await asyncio.sleep(60)

    async def safe_scan(self, manual=False):
        self.last_attempt = datetime.now(timezone.utc)
        try:
            await self.run_scan(manual)
        except Exception as e:
            log.exception("scan failed")
            if manual or not self.failing:  # one alert per streak of failures
                await self.say(f"❌ Сбор упал, попробую снова через {self.cfg.get('interval_minutes', 15)} мин: {html.escape(str(e), quote=False)}")
            self.failing = True
        else:
            if self.failing:
                await self.say("✅ Сбор снова работает.")
            self.failing = False

    # ------------------------------------------------------------ handlers

    async def on_help(self, event):
        await event.respond(HELP, parse_mode="html")

    async def on_scan(self, event):
        await event.respond("Собираю…")
        await self.safe_scan(manual=True)

    async def on_new(self, event):
        pending = [(k, from_inbox(v)) for k, v in load_inbox().items() if v["status"] == "new"]
        if not pending:
            await event.respond("Неразобранных вакансий нет.")
        for key, job in pending[-20:]:
            await self.say(card_text(job), buttons=card_buttons(job, key))

    async def on_list(self, event):
        chats = tc.load_channels(tc.CHANNELS_PATH)
        if not chats:
            await event.respond("Список пуст. Добавь: /add @канал")
            return
        lines = "\n".join(f"{i}. {html.escape(c, quote=False)}" for i, c in enumerate(chats, 1))
        await event.respond(f"<b>Читаю чаты:</b>\n{lines}\n\nУбрать: /remove номер", parse_mode="html")

    async def on_add(self, event):
        name = event.raw_text.partition(" ")[2].strip()
        if not name:
            await event.respond("Напиши так: /add @канал (или ссылку, или точное название)")
        elif add_channel(name):
            await event.respond(f"Добавил: {name}. Начну читать со следующего сбора (или /scan).")
        else:
            await event.respond("Этот чат уже в списке.")

    async def on_remove(self, event):
        arg = event.raw_text.partition(" ")[2].strip()
        removed = remove_channel(arg) if arg else None
        await event.respond(f"Убрал: {removed}" if removed else "Не нашёл такого в списке. Посмотри /list")

    async def on_button(self, event):
        if event.sender_id != self.owner:
            return await event.answer("Это не твой бот.")
        action, _, key = event.data.decode().partition(":")
        inbox = load_inbox()
        item = inbox.get(key)
        if not item:
            return await event.answer("Не нашёл эту вакансию.")
        if item["status"] != "new":
            return await event.answer("Уже разобрано.")

        job = from_inbox(item)
        if action == "t":
            tc.add_to_pipeline([tc.pipeline_line(job, tc.save_jd(job))])
            item["status"], label = "taken", "✅ В работе — оценка: /career-ops pipeline"
        else:
            with DISCARD_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\t{job.get('link') or key}\thidden in telegram bot\n")
            item["status"], label = "hidden", "🙈 Скрыто"
        save_inbox(inbox)
        buttons = [[Button.url("🔗 Открыть пост", job["link"])]] if job.get("link") else None
        await event.edit(card_text(job) + f"\n\n<b>{label}</b>", parse_mode="html", buttons=buttons, link_preview=False)


async def main():
    logging.basicConfig(
        filename=LOG_PATH, encoding="utf-8", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    cfg = tc.load_config()
    if not cfg.get("bot_token"):
        raise SystemExit("В telegram/config.yml нет bot_token — создай бота у @BotFather и впиши токен.")
    app = JobBot(cfg)
    await app.start()
    asyncio.create_task(app.scheduler())
    await app.bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
