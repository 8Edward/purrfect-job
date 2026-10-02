"""Personal Telegram bot: the interface to the Telegram job collector.

- Every `interval_minutes` (default 15) while the computer is on, reads only
  the messages that arrived since the last check in the chats from
  channels.txt, and alerts you with a card per new job post:
  👍 В работу  -> added to data/pipeline.md for /career-ops pipeline
  👎 Скрыть    -> logged to data/discard.log
  ⭐           -> favourites
- Menu: new / all / favourite job lists, re-check the last 1 or 3 days,
  settings (interval, depth, keywords, chats) editable from the chat.
- Answers only you (the account logged in via login.bat).

Run: pyw -3 telegram/bot.py   (no console; log in data/tg-bot.log)
"""

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from telethon import Button, TelegramClient, events

import tg_collect as tc

INBOX_PATH = tc.ROOT / "data" / "tg-inbox.json"
DISCARD_LOG = tc.ROOT / "data" / "discard.log"
LOG_PATH = tc.ROOT / "data" / "tg-bot.log"
BOT_SESSION = tc.TG_DIR / "bot"

PAGE_SIZE = 8

BTN_NEW, BTN_ALL, BTN_FAV = "🆕 Новое", "📋 Все вакансии", "⭐ Избранное"
BTN_DAY, BTN_3DAYS, BTN_SETTINGS = "🔄 За сутки", "🔄 За 3 дня", "⚙️ Настройки"
MENU = [
    [Button.text(BTN_NEW, resize=True), Button.text(BTN_ALL), Button.text(BTN_FAV)],
    [Button.text(BTN_DAY), Button.text(BTN_3DAYS)],
    [Button.text(BTN_SETTINGS)],
]

VIEWS = {
    "new": ("🆕 Новые", lambda item: item["status"] == "new"),
    "all": ("📋 Все вакансии", lambda item: True),
    "fav": ("⭐ Избранное", lambda item: item.get("fav")),
}
STATUS_ICON = {"new": "🆕", "taken": "✅", "hidden": "🙈"}
STATUS_LABEL = {"taken": "✅ В работе — оценка: /career-ops pipeline", "hidden": "🙈 Скрыто"}

KEYWORD_GROUPS = {
    "job": ("🔑 Признаки вакансии", "В сообщении должно быть хотя бы одно из этих слов."),
    "role": ("🎯 Роль и стек", "И хотя бы одно из этих."),
    "negative": ("🚫 Стоп-слова", "Если есть хоть одно — сообщение пропускается."),
}
INTERVALS, DEPTHS, MIN_LENGTHS = (5, 15, 30, 60), (1, 3, 7), (50, 120, 200)

HELP = """<b>Бот вакансий из Telegram</b>

Каждые 15 минут я проверяю новые сообщения в чатах из списка и присылаю алерт 🔔, если нашлась вакансия.
👍 — в работу (попадёт в career-ops на оценку), 👎 — скрыть, ⭐ — в избранное.

Кнопки внизу:
🆕 Новое · 📋 Все вакансии · ⭐ Избранное — списки
🔄 За сутки / За 3 дня — перепроверить чаты за период (найдёт то, что пропустили старые настройки)
⚙️ Настройки — интервал, глубина, слова для поиска, чаты

Команды: /scan — проверить сейчас, /help — эта справка"""

log = logging.getLogger("tg-bot")


def esc(text):
    return html.escape(str(text), quote=False)


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
    item = dict(job, status="new", fav=False)
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


# ---------------------------------------------------------------- keywords

def word_to_pattern(word):
    """A word typed in the chat becomes a literal, case-insensitive pattern."""
    return re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", word.strip())


def add_keywords(search, group, text):
    patterns = search.setdefault("keywords", {}).setdefault(group, [])
    added = []
    for word in (w.strip() for w in text.split(",")):
        pattern = word_to_pattern(word)
        if pattern and pattern.lower() not in {p.lower() for p in patterns}:
            patterns.append(pattern)
            added.append(word)
    return added


# ---------------------------------------------------------------- views

def card_text(job, status="new", fav=False):
    tags = " ".join(f"#{t.replace('-', '_')}" for t in job["tags"])
    body = job["text"] if len(job["text"]) <= 1500 else job["text"][:1500] + "…"
    star = "⭐ " if fav else ""
    head = f"{star}<b>{esc(tc.guess_title(job['text']))}</b>\n💬 {esc(job['chat'])} · {job['date']:%d.%m %H:%M}"
    text = f"{head}\n{tags}\n\n{esc(body)}".replace("\n\n\n", "\n\n")
    if status in STATUS_LABEL:
        text += f"\n\n<b>{STATUS_LABEL[status]}</b>"
    return text


def card_buttons(job, key, status="new", fav=False):
    rows = []
    if status == "new":
        rows.append([Button.inline("👍 В работу", f"t:{key}".encode()), Button.inline("👎 Скрыть", f"h:{key}".encode())])
    elif status == "hidden":
        rows.append([Button.inline("↩️ Вернуть в новые", f"r:{key}".encode())])
    rows.append([Button.inline("★ Убрать из избранного" if fav else "⭐ В избранное", f"f:{key}".encode())])
    if job.get("link"):
        rows.append([Button.url("🔗 Открыть пост", job["link"])])
    return rows


def item_card(key, item):
    job = from_inbox(item)
    status, fav = item["status"], item.get("fav", False)
    return card_text(job, status, fav), card_buttons(job, key, status, fav)


def list_view(inbox, view, page=0):
    title, keep = VIEWS[view]
    items = sorted(((k, v) for k, v in inbox.items() if keep(v)), key=lambda kv: kv[1]["date"], reverse=True)
    if not items:
        empty = {"new": "Новых вакансий нет.", "fav": "В избранном пусто. Жми ⭐ на карточке.", "all": "Пока ни одной вакансии."}
        return f"<b>{title}</b>\n\n{empty[view]}", None
    pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [f"<b>{title}</b> · {len(items)}" + (f" · стр. {page + 1}/{pages}" if pages > 1 else "")]
    for n, (_, item) in enumerate(chunk, 1):
        job = from_inbox(item)
        icons = STATUS_ICON[item["status"]] + ("⭐" if item.get("fav") else "")
        lines.append(f"{n}. {icons} <b>{esc(tc.guess_title(job['text'])[:60])}</b>\n     {esc(job['chat'])} · {job['date']:%d.%m}")
    lines.append("\nНажми номер, чтобы открыть карточку.")

    numbers = [Button.inline(str(n), f"o:{key}".encode()) for n, (key, _) in enumerate(chunk, 1)]
    rows = [numbers[:4], numbers[4:]] if len(numbers) > 4 else [numbers]
    if pages > 1:
        rows.append([
            Button.inline("◀️", f"l:{view}:{page - 1}".encode()) if page else Button.inline(" ", b"x"),
            Button.inline(f"{page + 1}/{pages}", b"x"),
            Button.inline("▶️", f"l:{view}:{page + 1}".encode()) if page < pages - 1 else Button.inline(" ", b"x"),
        ])
    return "\n".join(lines), rows


def settings_view(cfg):
    kw = cfg.get("keywords", {})
    interval, depth, min_len = cfg.get("interval_minutes", 15), cfg.get("lookback_days", 1), cfg.get("min_length", 120)
    text = (
        "<b>⚙️ Настройки поиска</b>\n\n"
        f"⏱ Проверка каждые <b>{interval} мин</b>\n"
        f"📅 Сообщения не старше <b>{depth} дн.</b>\n"
        f"📏 Короче <b>{min_len}</b> символов — пропускаю\n"
        f"💬 Чатов в списке: <b>{len(tc.load_channels(tc.CHANNELS_PATH))}</b>\n"
        f"🔑 Слов: признаки {len(kw.get('job', []))} · роль {len(kw.get('role', []))} · стоп {len(kw.get('negative', []))}"
    )

    def choice(prefix, values, current, unit):
        return [Button.inline(f"{'✓ ' if v == current else ''}{v}{unit}", f"s:{prefix}:{v}".encode()) for v in values]

    rows = [
        choice("int", INTERVALS, interval, " мин"),
        choice("lb", DEPTHS, depth, " дн"),
        choice("ml", MIN_LENGTHS, min_len, " симв"),
        [Button.inline("💬 Чаты", b"c:list")],
        [Button.inline(KEYWORD_GROUPS[g][0], f"k:{g}".encode()) for g in ("job", "role")],
        [Button.inline(KEYWORD_GROUPS["negative"][0], b"k:negative")],
    ]
    return text, rows


def keywords_view(cfg, group):
    title, hint = KEYWORD_GROUPS[group]
    patterns = cfg.get("keywords", {}).get(group, [])
    text = f"<b>{title}</b>\n{hint}\n\nНажми на слово, чтобы удалить."
    buttons = [Button.inline(f"✖ {p}"[:40], f"k:del:{group}:{i}".encode()) for i, p in enumerate(patterns)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([Button.inline("➕ Добавить", f"k:add:{group}".encode()), Button.inline("⬅️ Назад", b"s:main")])
    return text, rows


def chats_view():
    chats = tc.load_channels(tc.CHANNELS_PATH)
    text = "<b>💬 Чаты</b>\nЧитаю только их. Нажми на чат, чтобы убрать." if chats else "<b>💬 Чаты</b>\nСписок пуст — добавь чат."
    rows = [[Button.inline(f"✖ {c}"[:60], f"c:del:{i}".encode())] for i, c in enumerate(chats)]
    rows.append([Button.inline("➕ Добавить", b"c:add"), Button.inline("⬅️ Назад", b"s:main")])
    return text, rows


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
        self.awaiting = None  # ("kw", group) or ("chat",): the next text you send is that input

    async def start(self):
        await self.user.connect()
        if not await self.user.is_user_authorized():
            raise SystemExit("Нет входа в Telegram: сначала запусти telegram\\login.bat")
        self.owner = (await self.user.get_me()).id
        await self.bot.start(bot_token=self.cfg["bot_token"])
        self.bot.add_event_handler(self.on_message, events.NewMessage(from_users=self.owner))
        self.bot.add_event_handler(self.on_button, events.CallbackQuery())
        log.info("bot started, owner %s", self.owner)

    async def say(self, text, **kw):
        try:
            await self.bot.send_message(self.owner, text, parse_mode="html", link_preview=False, **kw)
        except Exception:
            log.exception("cannot message owner — did you press Start in the bot chat?")

    def reload(self):
        self.cfg = tc.load_config()

    # ------------------------------------------------------------ scanning

    async def run_scan(self, manual=False, rescan_days=None):
        if self.lock.locked():
            if manual:
                await self.say("Уже проверяю, подожди минуту.")
            return
        async with self.lock:
            self.reload()  # pick up settings edits without a restart
            state = tc.load_state()
            jobs, missing = await tc.scan_chats(
                self.user, self.cfg, tc.job_filter_from(self.cfg), state, rescan_days=rescan_days)
            if not rescan_days:
                state["last_run"] = datetime.now(timezone.utc).isoformat()
            tc.save_state(state)

            inbox = load_inbox()
            for job in jobs:
                inbox[job_key(job)] = to_inbox(job)
            save_inbox(inbox)
            log.info("scan (rescan=%s): %d new job(s), %d missing chat(s)", rescan_days, len(jobs), len(missing))

            if jobs:
                await self.say(f"🔔 <b>Новых вакансий: {len(jobs)}</b>")
            for job in jobs:
                text, buttons = item_card(job_key(job), inbox[job_key(job)])
                await self.say(text, buttons=buttons, silent=True)
            if missing and (manual or set(missing) != self.reported_missing):
                self.reported_missing = set(missing)
                names = "\n".join(f"• {esc(n)}" for n in missing)
                await self.say(f"⚠️ Не нашёл эти чаты из списка (опечатка или ты в них не состоишь):\n{names}")
            if manual and not jobs:
                period = f" за {rescan_days} дн." if rescan_days else ""
                await self.say(f"Новых вакансий{period} нет. Все найденные раньше — в «{BTN_ALL}».")

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

    async def safe_scan(self, manual=False, rescan_days=None):
        if not rescan_days:
            self.last_attempt = datetime.now(timezone.utc)
        try:
            await self.run_scan(manual, rescan_days)
        except Exception as e:
            log.exception("scan failed")
            if manual or not self.failing:  # one alert per streak of failures
                await self.say(f"❌ Проверка упала, попробую снова через {self.cfg.get('interval_minutes', 15)} мин: {esc(e)}")
            self.failing = True
        else:
            if self.failing:
                await self.say("✅ Проверка снова работает.")
            self.failing = False

    # ------------------------------------------------------------ messages

    async def on_message(self, event):
        text = event.raw_text.strip()
        menu = {
            BTN_NEW: lambda: self.show_list("new"),
            BTN_ALL: lambda: self.show_list("all"),
            BTN_FAV: lambda: self.show_list("fav"),
            BTN_DAY: lambda: self.rescan(1),
            BTN_3DAYS: lambda: self.rescan(3),
            BTN_SETTINGS: self.show_settings,
        }
        if text in menu:
            self.awaiting = None
            return await menu[text]()

        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        if command in ("/start", "/help"):
            self.awaiting = None
            return await event.respond(HELP, parse_mode="html", buttons=MENU)
        if command == "/scan":
            await event.respond("Проверяю…")
            return await self.safe_scan(manual=True)
        if command == "/add":  # kept from the first version
            return await self.handle_input(("chat",), text.partition(" ")[2])
        if command == "/remove":
            removed = remove_channel(text.partition(" ")[2].strip())
            return await event.respond(f"Убрал: {removed}" if removed else "Не нашёл такого в списке.")

        if self.awaiting and not command:
            awaiting, self.awaiting = self.awaiting, None
            return await self.handle_input(awaiting, text)
        await event.respond("Выбери действие в меню внизу 👇", buttons=MENU)

    async def handle_input(self, awaiting, text):
        text = text.strip()
        if not text:
            return await self.say("Пусто — ничего не добавил.")
        if awaiting[0] == "chat":
            added = add_channel(text)
            await self.say(f"Добавил чат: {esc(text)}. Начну читать со следующей проверки." if added else "Этот чат уже в списке.")
            view_text, buttons = chats_view()
        else:
            group = awaiting[1]
            search = tc.load_search()
            added = add_keywords(search, group, text)
            tc.save_search(search)
            self.reload()
            await self.say(f"Добавил: {esc(', '.join(added))}" if added else "Эти слова уже есть.")
            view_text, buttons = keywords_view(self.cfg, group)
        await self.say(view_text, buttons=buttons)

    async def show_list(self, view, page=0, event=None):
        text, buttons = list_view(load_inbox(), view, page)
        if event:
            return await event.edit(text, parse_mode="html", buttons=buttons)
        await self.say(text, buttons=buttons)

    async def show_settings(self, event=None):
        self.reload()
        text, buttons = settings_view(self.cfg)
        if event:
            return await event.edit(text, parse_mode="html", buttons=buttons)
        await self.say(text, buttons=buttons)

    async def rescan(self, days):
        await self.say(f"Перепроверяю чаты за {days} дн.…")
        await self.safe_scan(manual=True, rescan_days=days)

    # ------------------------------------------------------------ buttons

    async def on_button(self, event):
        if event.sender_id != self.owner:
            return await event.answer("Это не твой бот.")
        action, _, arg = event.data.decode().partition(":")
        if action == "x":
            return await event.answer()
        if action == "l":
            view, _, page = arg.partition(":")
            await event.answer()
            return await self.show_list(view, int(page), event)
        if action == "s":
            return await self.on_setting(event, arg)
        if action == "k":
            return await self.on_keywords(event, arg)
        if action == "c":
            return await self.on_chats(event, arg)
        return await self.on_job(event, action, arg)

    async def on_job(self, event, action, key):
        inbox = load_inbox()
        item = inbox.get(key)
        if not item:
            return await event.answer("Не нашёл эту вакансию.")
        if action == "o":
            text, buttons = item_card(key, item)
            await event.answer()
            return await self.say(text, buttons=buttons)

        job = from_inbox(item)
        if action in ("t", "h") and item["status"] != "new":
            return await event.answer("Уже разобрано.")
        if action == "t":
            tc.add_to_pipeline([tc.pipeline_line(job, tc.save_jd(job))])
            item["status"] = "taken"
        elif action == "h":
            with DISCARD_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\t{job.get('link') or key}\thidden in telegram bot\n")
            item["status"] = "hidden"
        elif action == "r" and item["status"] == "hidden":
            item["status"] = "new"
        elif action == "f":
            item["fav"] = not item.get("fav", False)
            await event.answer("Добавил в избранное ⭐" if item["fav"] else "Убрал из избранного")
        if action != "f":
            await event.answer()
        save_inbox(inbox)
        text, buttons = item_card(key, item)
        await event.edit(text, parse_mode="html", buttons=buttons, link_preview=False)

    async def on_setting(self, event, arg):
        if arg != "main":
            name, _, value = arg.partition(":")
            field = {"int": "interval_minutes", "lb": "lookback_days", "ml": "min_length"}[name]
            search = tc.load_search()
            search[field] = int(value)
            tc.save_search(search)
            await event.answer("Сохранил ✓")
        else:
            await event.answer()
        await self.show_settings(event)

    async def on_keywords(self, event, arg):
        self.reload()
        parts = arg.split(":")
        if parts[0] == "add":
            self.awaiting = ("kw", parts[1])
            await event.answer()
            return await self.say(
                f"Напиши слово для «{KEYWORD_GROUPS[parts[1]][0]}» — можно несколько через запятую.\n"
                "Регистр не важен, ищу как часть слова: «аналит» найдёт и «аналитик», и «аналитика».")
        if parts[0] == "del":
            group, index = parts[1], int(parts[2])
            search = tc.load_search()
            patterns = search.get("keywords", {}).get(group, [])
            if index < len(patterns):
                removed = patterns.pop(index)
                tc.save_search(search)
                self.reload()
                await event.answer(f"Удалил: {removed}")
            else:
                await event.answer()
            group_name = group
        else:
            await event.answer()
            group_name = parts[0]
        text, buttons = keywords_view(self.cfg, group_name)
        await event.edit(text, parse_mode="html", buttons=buttons)

    async def on_chats(self, event, arg):
        if arg == "add":
            self.awaiting = ("chat",)
            await event.answer()
            return await self.say("Пришли чат: @username, ссылку t.me/… (для приватного — ссылку на любое сообщение в нём) или точное название.")
        if arg.startswith("del:"):
            chats = tc.load_channels(tc.CHANNELS_PATH)
            index = int(arg.split(":")[1])
            if index < len(chats):
                remove_channel(chats[index])
                await event.answer(f"Убрал: {chats[index]}")
            else:
                await event.answer()
        else:
            await event.answer()
        text, buttons = chats_view()
        await event.edit(text, parse_mode="html", buttons=buttons)


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
    await app.say("🤖 Бот запущен.", buttons=MENU)
    await app.bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
