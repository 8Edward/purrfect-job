"""Job Radar — personal Telegram bot that works like an app.

One screen (a dashboard picture with a caption and buttons) that changes in
place: home -> lists -> job card -> settings, instead of a stream of messages.

Sources:
- Telegram: chats from channels.txt, checked every `interval_minutes`,
  incrementally (only messages newer than the last one seen).
- Job boards: career-ops scan.mjs, every `boards_interval_hours`.

A job card: 👍 В работу -> data/pipeline.md for /career-ops pipeline,
👎 Скрыть, ⭐ favourite. Answers only you (the account logged in via login.bat).

Run: pyw -3 telegram/bot.py   (no console; log in data/tg-bot.log)
"""

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from telethon import Button, TelegramClient, events, functions, types

import boards
import screens
import tg_collect as tc

INBOX_PATH = tc.ROOT / "data" / "tg-inbox.json"
DISCARD_LOG = tc.ROOT / "data" / "discard.log"
LOG_PATH = tc.ROOT / "data" / "tg-bot.log"
SCREENS_DIR = tc.ROOT / "data" / "tg-screens"
BOT_SESSION = tc.TG_DIR / "bot"

PAGE_SIZE = 8
CAPTION_LIMIT = 1024
EXCERPT = 520

VIEWS = {
    "new": ("🆕 Новые", lambda item: item["status"] == "new"),
    "all": ("📋 Все вакансии", lambda item: True),
    "fav": ("⭐ Избранное", lambda item: item.get("fav")),
}
EMPTY = {
    "new": "Всё разобрано 🎉\nНовые вакансии появятся здесь после следующей проверки.",
    "fav": "Пока пусто.\nОткрой вакансию и нажми ⭐ — она окажется здесь.",
    "all": "Пока ни одной вакансии.\nНажми «🔄 Обновить», чтобы проверить прямо сейчас.",
}
STATUS_ICON = {"new": "🆕", "taken": "✅", "hidden": "🙈"}
STATUS_LINE = {"taken": "✅ <b>В работе</b> — оценит /career-ops pipeline", "hidden": "🙈 <b>Скрыта</b>"}

KEYWORD_GROUPS = {
    "job": ("🔑 Признаки вакансии", "В сообщении должно быть хотя бы одно из этих слов."),
    "role": ("🎯 Роль и стек", "И хотя бы одно из этих."),
    "negative": ("🚫 Стоп-слова", "Если есть хоть одно — сообщение пропускается."),
}
INTERVALS, DEPTHS, MIN_LENGTHS, BOARD_HOURS = (5, 15, 30, 60), (1, 3, 7), (50, 120, 200), (1, 2, 4, 8)

HELP = """<b>❓ Как это работает</b>

<b>Откуда вакансии</b>
💬 Твои Telegram-чаты — проверяю каждые 15 минут, только новые сообщения.
🌐 Сайты удалённой работы — раз в пару часов.

<b>Что делать с вакансией</b>
👍 <b>В работу</b> — уйдёт в career-ops: /career-ops pipeline оценит её и подготовит резюме.
👎 <b>Скрыть</b> — больше не мешает (можно вернуть).
⭐ — в избранное.

<b>Обновить за сутки / 3 дня</b> — перечитать чаты за период: найдёт то, что пропустили старые настройки (например, после добавления слова).

<b>⚙️ Настройки</b> — частота, глубина, слова для поиска, чаты, сайты. Всё кнопками, без файлов."""

log = logging.getLogger("job-radar")


def esc(text):
    return html.escape(str(text), quote=False)


def visible_len(caption):
    return len(html.unescape(re.sub(r"<[^>]+>", "", caption)))


def fit(caption, limit=CAPTION_LIMIT):
    """Telegram rejects captions over 1024 visible characters: fall back to trimmed plain text."""
    if visible_len(caption) <= limit:
        return caption
    log.warning("caption too long (%d), trimming", visible_len(caption))
    plain = html.unescape(re.sub(r"<[^>]+>", "", caption))
    return esc(plain[:limit - 1] + "…")


def short(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",.:;—-") + "…"


def show_pattern(pattern):
    """'\\banalyst\\b' -> 'analyst', 'power ?bi' -> 'power bi': keywords as a person reads them."""
    text = pattern.replace("\\b", "").replace(" ?", " ").replace("-?", "-")
    return re.sub(r"\\(.)", r"\1", text)


def btn(text, data):
    return Button.inline(text, data.encode())


# ---------------------------------------------------------------- inbox

def load_inbox():
    if INBOX_PATH.exists():
        return json.loads(INBOX_PATH.read_text(encoding="utf-8"))
    return {}


def save_inbox(inbox):
    INBOX_PATH.parent.mkdir(exist_ok=True)
    INBOX_PATH.write_text(json.dumps(inbox, ensure_ascii=False, indent=1), encoding="utf-8")


def job_key(job):
    return job.get("key") or f"{abs(job['chat_id'])}_{job['msg_id']}"


def to_inbox(job):
    item = dict(job, status="new", fav=False)
    item.setdefault("source", "tg")
    if item["source"] == "tg":
        item.setdefault("title", tc.guess_title(job["text"]))
    item["date"] = job["date"].isoformat()
    item.pop("key", None)
    return item


def from_inbox(item):
    job = dict(item)
    job["date"] = datetime.fromisoformat(item["date"])
    return job


def title_of(item):
    return item.get("title") or tc.guess_title(item.get("text", ""))


def origin_of(item):
    return f"🌐 {item['site']}" if item.get("source") == "web" else f"💬 {item['chat']}"


def stats_of(inbox):
    return {
        "new": sum(1 for i in inbox.values() if i["status"] == "new"),
        "taken": sum(1 for i in inbox.values() if i["status"] == "taken"),
        "fav": sum(1 for i in inbox.values() if i.get("fav")),
    }


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


# ---------------------------------------------------------------- screens (caption, buttons)

HOME_BTN = btn("🏠 Домой", "h")


def home_screen(inbox, next_check=None):
    s = stats_of(inbox)
    if s["new"]:
        n = s["new"]
        hello = f"🔥 <b>{n}</b> {plural(n, 'новая вакансия ждёт', 'новые вакансии ждут', 'новых вакансий ждут')} тебя"
    else:
        hello = "✨ Всё разобрано — новых вакансий пока нет"
    lines = [hello]
    if next_check:
        lines.append(f"⏱ Следующая проверка через {next_check} мин")
    rows = [
        [btn(f"🆕 Новые · {s['new']}", "l:new:0"), btn(f"⭐ Избранное · {s['fav']}", "l:fav:0")],
        [btn(f"📋 Все вакансии · {len(inbox)}", "l:all:0")],
        [btn("🔄 Обновить за сутки", "rf:1"), btn("🔄 За 3 дня", "rf:3")],
        [btn("⚙️ Настройки", "s"), btn("❓ Как это работает", "help")],
    ]
    return "\n".join(lines), rows


def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def list_screen(inbox, view, page=0):
    title, keep = VIEWS[view]
    items = sorted(((k, v) for k, v in inbox.items() if keep(v)), key=lambda kv: kv[1]["date"], reverse=True)
    if not items:
        return f"<b>{title}</b>\n\n{EMPTY[view]}", [[HOME_BTN]]
    pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [f"<b>{title}</b> · {len(items)}" + (f"   стр. {page + 1}/{pages}" if pages > 1 else ""), ""]
    for n, (_, item) in enumerate(chunk, 1):
        icons = STATUS_ICON[item["status"]] + ("⭐" if item.get("fav") else "")
        date = datetime.fromisoformat(item["date"])
        lines.append(f"<b>{n}.</b> {icons} {esc(short(title_of(item), 52))}\n      <i>{esc(origin_of(item)[:40])} · {date:%d.%m}</i>")
    caption = "\n".join(lines)

    numbers = [btn(f"{n}", f"o:{key}:{view}:{page}") for n, (key, _) in enumerate(chunk, 1)]
    rows = [numbers[:4], numbers[4:]] if len(numbers) > 4 else [numbers]
    nav = []
    if page:
        nav.append(btn("◀️", f"l:{view}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("▶️", f"l:{view}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([HOME_BTN])
    return caption, rows


def card_screen(key, item, view="all", page=0):
    job = from_inbox(item)
    star = "⭐ " if item.get("fav") else ""
    head = [f"{star}<b>{esc(short(title_of(item), 80))}</b>", ""]
    if item.get("source") == "web":
        if item.get("company"):
            head.append(f"🏢 {esc(item['company'])}")
        if item.get("location"):
            head.append(f"📍 {esc(item['location'])}")
        if item.get("comp"):
            head.append(f"💰 {esc(item['comp'])}")
        head.append(f"{esc(origin_of(item))} · {job['date']:%d.%m}")
        body = "Полное описание — на сайте, кнопка «🔗 Вакансия»."
    else:
        head.append(f"{esc(origin_of(item))} · {job['date']:%d.%m %H:%M}")
        if item.get("tags"):
            head.append(" ".join(f"#{t.replace('-', '_')}" for t in item["tags"]))
        text = item["text"].strip()
        body = esc(text if len(text) <= EXCERPT else text[:EXCERPT].rsplit(" ", 1)[0] + "…")
    caption = "\n".join(head) + f"\n\n{body}"
    if item["status"] in STATUS_LINE:
        caption += f"\n\n{STATUS_LINE[item['status']]}"

    ctx = f"{key}:{view}:{page}"
    rows = []
    if item["status"] == "new":
        rows.append([btn("👍 В работу", f"t:{ctx}"), btn("👎 Скрыть", f"n:{ctx}")])
    elif item["status"] == "hidden":
        rows.append([btn("↩️ Вернуть в новые", f"r:{ctx}")])
    rows.append([btn("★ Убрать из избранного" if item.get("fav") else "⭐ В избранное", f"f:{ctx}")])
    links = []
    if item.get("source") != "web" and len(item.get("text", "")) > EXCERPT:
        links.append(btn("📄 Полный текст", f"full:{key}"))
    if item.get("link"):
        links.append(Button.url("🔗 Вакансия" if item.get("source") == "web" else "🔗 Пост", item["link"]))
    if links:
        rows.append(links)
    rows.append([btn("⬅️ К списку", f"l:{view}:{page}"), HOME_BTN])
    return caption, rows


def found_screen(items, period=None):
    if not items:
        where = f" за {period} дн." if period else ""
        return f"🔍 <b>Новых вакансий{where} нет</b>\n\nВсё найденное раньше — в «📋 Все вакансии».", [[HOME_BTN]]
    n = len(items)
    lines = [f"🔔 <b>{n} {plural(n, 'новая вакансия', 'новые вакансии', 'новых вакансий')}</b>", ""]
    for item in items[:6]:
        lines.append(f"• {esc(short(title_of(item), 48))}\n   <i>{esc(origin_of(item)[:40])}</i>")
    if n > 6:
        lines.append(f"…и ещё {n - 6}")
    return "\n".join(lines), [[btn("👀 Смотреть новые", "l:new:0")], [HOME_BTN]]


def settings_screen(cfg):
    kw = cfg.get("keywords", {})
    interval, depth, min_len = cfg.get("interval_minutes", 15), cfg.get("lookback_days", 1), cfg.get("min_length", 120)
    web_on, web_hours = cfg.get("boards_enabled", True), cfg.get("boards_interval_hours", 2)
    chats = len(tc.load_channels(tc.CHANNELS_PATH))
    caption = (
        "<b>⚙️ Настройки</b>\n\n"
        f"💬 <b>Telegram</b>: {chats} {plural(chats, 'чат', 'чата', 'чатов')}, проверка каждые {interval} мин,\n"
        f"      сообщения не старше {depth} дн., короче {min_len} символов — мимо\n"
        f"🌐 <b>Сайты</b>: " + (f"каждые {web_hours} ч" if web_on else "выключены") + "\n"
        f"🔑 <b>Слова</b>: признаки {len(kw.get('job', []))} · роль {len(kw.get('role', []))} · стоп {len(kw.get('negative', []))}\n\n"
        "<i>Нажми на значение, чтобы выбрать. ✓ — сейчас.</i>"
    )

    def choice(prefix, values, current, unit):
        return [btn(f"{'✓' if v == current else ''}{v}{unit}", f"s:{prefix}:{v}") for v in values]

    rows = [
        [btn("⏱ Как часто проверять чаты", "noop")],
        choice("int", INTERVALS, interval, " мин"),
        [btn("📅 Глубина", "noop")] + choice("lb", DEPTHS, depth, " дн"),
        [btn("📏 Мин. длина", "noop")] + choice("ml", MIN_LENGTHS, min_len, ""),
        [btn("🌐 Сайты: " + ("✓ вкл" if web_on else "выкл"), "s:web:" + ("off" if web_on else "on"))]
        + (choice("wi", BOARD_HOURS, web_hours, " ч") if web_on else []),
        [btn(f"💬 Чаты · {chats}", "c")],
        [btn(KEYWORD_GROUPS["job"][0], "k:job"), btn(KEYWORD_GROUPS["role"][0], "k:role")],
        [btn(KEYWORD_GROUPS["negative"][0], "k:negative"), HOME_BTN],
    ]
    return caption, rows


def keywords_screen(cfg, group):
    title, hint = KEYWORD_GROUPS[group]
    patterns = cfg.get("keywords", {}).get(group, [])
    caption = f"<b>{title}</b>\n\n{hint}\n\n<i>Нажми на слово, чтобы удалить.</i>"
    buttons = [btn(f"✖ {show_pattern(p)}"[:40], f"k:del:{group}:{i}") for i, p in enumerate(patterns)]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([btn("➕ Добавить", f"k:add:{group}"), btn("⬅️ Назад", "s")])
    return caption, rows


def chats_screen():
    chats = tc.load_channels(tc.CHANNELS_PATH)
    caption = ("<b>💬 Чаты</b>\n\nЧитаю только их.\n\n<i>Нажми на чат, чтобы убрать.</i>" if chats
               else "<b>💬 Чаты</b>\n\nСписок пуст — добавь первый чат.")
    rows = [[btn(f"✖ {c}"[:60], f"c:del:{i}")] for i, c in enumerate(chats)]
    rows.append([btn("➕ Добавить", "c:add"), btn("⬅️ Назад", "s")])
    return caption, rows


def input_screen(what):
    prompts = {
        "chat": "<b>➕ Новый чат</b>\n\nПришли сообщением:\n• @username\n• ссылку t.me/…\n"
                "• для приватного — ссылку на любое сообщение в нём\n• или точное название чата",
    }
    if what in prompts:
        caption = prompts[what]
    else:
        caption = (f"<b>➕ {KEYWORD_GROUPS[what][0]}</b>\n\nПришли слово сообщением — можно несколько через запятую.\n\n"
                   "<i>Регистр не важен, ищу как часть слова: «аналит» найдёт и «аналитик», и «аналитика».</i>")
    back = "c" if what == "chat" else f"k:{what}"
    return caption, [[btn("✖ Отмена", back)]]


# ---------------------------------------------------------------- bot

class JobRadar:
    def __init__(self, cfg):
        self.cfg = cfg
        self.user = tc.user_client(cfg)
        self.bot = TelegramClient(str(BOT_SESSION), int(cfg["api_id"]), cfg["api_hash"])
        self.bot.parse_mode = "html"
        self.owner = None
        self.lock = asyncio.Lock()
        self.failing = {"tg": False, "web": False}
        self.last_attempt = {"tg": None, "web": None}
        self.reported_missing = set()
        self.awaiting = None  # (what, screen message id): the next text you send is that input

    async def start(self):
        await self.user.connect()
        if not await self.user.is_user_authorized():
            raise SystemExit("Нет входа в Telegram: сначала запусти telegram\\login.bat")
        self.owner = (await self.user.get_me()).id
        await self.bot.start(bot_token=self.cfg["bot_token"])
        self.bot.add_event_handler(self.on_message, events.NewMessage(from_users=self.owner))
        self.bot.add_event_handler(self.on_button, events.CallbackQuery())
        await self.set_profile()
        log.info("started, owner %s", self.owner)

    async def set_profile(self):
        try:
            await self.bot(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(), lang_code="",
                commands=[types.BotCommand("start", "🏠 Главный экран"), types.BotCommand("scan", "🔄 Проверить сейчас")]))
            await self.bot(functions.bots.SetBotInfoRequest(
                lang_code="", about="Радар удалённых вакансий: чаты Telegram + сайты",
                description="Job Radar находит вакансии в твоих Telegram-чатах и на сайтах удалённой работы "
                            "и показывает их как приложение: новые, избранное, в работе."))
        except Exception:
            log.warning("could not set bot commands/description", exc_info=True)

    def reload(self):
        self.cfg = tc.load_config()

    # ------------------------------------------------------------ screen plumbing

    def dashboard(self):
        inbox = load_inbox()
        state = tc.load_state()
        last = state.get("last_run")
        stats = dict(stats_of(inbox),
                     chats=len(tc.load_channels(tc.CHANNELS_PATH)),
                     boards=state.get("boards_scanned", 0) if self.cfg.get("boards_enabled", True) else 0,
                     checked_at=datetime.fromisoformat(last).astimezone().replace(second=0, microsecond=0) if last else None)
        return screens.render_dashboard(stats, SCREENS_DIR)

    def minutes_to_next_check(self):
        last = tc.load_state().get("last_run")
        if not last:
            return None
        due = datetime.fromisoformat(last) + timedelta(minutes=self.cfg.get("interval_minutes", 15))
        return max(1, int((due - datetime.now(timezone.utc)).total_seconds() // 60) + 1)

    async def send_screen(self, caption, buttons, silent=False):
        try:
            return await self.bot.send_file(self.owner, self.dashboard(), caption=fit(caption), buttons=buttons, silent=silent)
        except Exception:
            log.exception("cannot message owner — did you press Start in the bot chat?")

    async def edit_screen(self, target, caption, buttons, refresh_picture=False):
        """target: a CallbackQuery event or a message id."""
        caption = fit(caption)
        kw = {"buttons": buttons, "link_preview": False}
        if refresh_picture:
            kw["file"] = self.dashboard()
        if isinstance(target, int):
            return await self.bot.edit_message(self.owner, target, caption, **kw)
        return await target.edit(caption, **kw)

    async def show_home(self, target=None):
        caption, buttons = home_screen(load_inbox(), self.minutes_to_next_check())
        if target is not None:
            return await self.edit_screen(target, caption, buttons, refresh_picture=True)
        state = tc.load_state()
        msg = await self.send_screen(caption, buttons)
        if msg and state.get("home_msg"):
            try:  # keep the chat tidy: one home screen at a time
                await self.bot.delete_messages(self.owner, state["home_msg"])
            except Exception:
                pass
        if msg:
            state = tc.load_state()
            state["home_msg"] = msg.id
            tc.save_state(state)

    # ------------------------------------------------------------ checking

    async def check(self, tg=True, web=False, rescan_days=None, manual=False):
        """Run the sources under one lock. Returns new inbox items, or None if busy."""
        if self.lock.locked():
            return None
        found = []
        async with self.lock:
            self.reload()
            if tg:
                found += await self.guarded("tg", self.check_telegram(rescan_days, manual), manual)
            if web and self.cfg.get("boards_enabled", True):
                found += await self.guarded("web", self.check_boards(), manual)
        return found

    async def guarded(self, source, coro, manual):
        if source == "tg" or not manual:
            self.last_attempt[source] = datetime.now(timezone.utc)
        name = "Telegram" if source == "tg" else "сайтов"
        try:
            result = await coro
        except Exception as e:
            log.exception("%s check failed", source)
            if manual or not self.failing[source]:  # one alert per streak of failures
                await self.send_screen(f"❌ <b>Проверка {name} не удалась</b>\n\n<code>{esc(str(e)[:300])}</code>\n\n"
                                       "Попробую снова автоматически.", [[HOME_BTN]])
            self.failing[source] = True
            return []
        if self.failing[source]:
            await self.send_screen(f"✅ Проверка {name} снова работает.", [[HOME_BTN]], silent=True)
        self.failing[source] = False
        return result

    async def check_telegram(self, rescan_days, manual):
        state = tc.load_state()
        jobs, missing = await tc.scan_chats(
            self.user, self.cfg, tc.job_filter_from(self.cfg), state, rescan_days=rescan_days)
        if not rescan_days:
            state["last_run"] = datetime.now(timezone.utc).isoformat()
        tc.save_state(state)
        if missing and (manual or set(missing) != self.reported_missing):
            self.reported_missing = set(missing)
            names = "\n".join(f"• {esc(n)}" for n in missing)
            await self.send_screen(f"⚠️ <b>Не нашёл чаты из списка</b>\n\n{names}\n\n"
                                   "Опечатка в названии или ты в них не состоишь.", [[btn("💬 Чаты", "c")], [HOME_BTN]])
        log.info("telegram: %d new (rescan=%s)", len(jobs), rescan_days)
        return self.store(jobs)

    async def check_boards(self):
        jobs, receipt = await boards.scan_boards()
        state = tc.load_state()
        state["boards_run"] = datetime.now(timezone.utc).isoformat()
        state["boards_scanned"] = receipt.get("scanned", 0)
        tc.save_state(state)
        log.info("boards: %d new of %s found", len(jobs), receipt.get("found"))
        return self.store(jobs)

    def store(self, jobs):
        inbox = load_inbox()
        items = []
        for job in jobs:
            key = job_key(job)
            if key not in inbox:
                inbox[key] = to_inbox(job)
                items.append(inbox[key])
        save_inbox(inbox)
        return items

    async def alert(self, items):
        if items:
            caption, buttons = found_screen(items)
            await self.send_screen(caption, buttons)

    async def scheduler(self):
        """Checks the wall clock once a minute instead of one long sleep, so a laptop
        waking up from sleep checks right away rather than an interval later."""
        while True:
            now, state = datetime.now(timezone.utc), tc.load_state()

            def due(source, state_key, interval):
                times = [datetime.fromisoformat(state[state_key])] if state.get(state_key) else []
                if self.last_attempt[source]:
                    times.append(self.last_attempt[source])  # after a failure, retry an interval later
                return not times or now - max(times) >= interval

            tg = due("tg", "last_run", timedelta(minutes=self.cfg.get("interval_minutes", 15)))
            web = self.cfg.get("boards_enabled", True) and due(
                "web", "boards_run", timedelta(hours=self.cfg.get("boards_interval_hours", 2)))
            if tg or web:
                if web:
                    self.last_attempt["web"] = now
                found = await self.check(tg=tg, web=web)
                await self.alert(found or [])
            await asyncio.sleep(60)

    # ------------------------------------------------------------ messages

    async def on_message(self, event):
        text = event.raw_text.strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        try:
            await event.delete()  # the chat stays a clean "app screen"
        except Exception:
            pass

        if self.awaiting and not command:
            what, screen_id = self.awaiting
            self.awaiting = None
            return await self.handle_input(what, text, screen_id)
        self.awaiting = None
        if command == "/scan":
            msg = await self.send_screen("⏳ <b>Проверяю чаты и сайты…</b>", None)
            return await self.refresh(msg.id if msg else None, None)
        await self.show_home()

    async def handle_input(self, what, text, screen_id):
        if what == "chat":
            if text:
                add_channel(text)
            caption, buttons = chats_screen()
        else:
            search = tc.load_search()
            add_keywords(search, what, text)
            tc.save_search(search)
            self.reload()
            caption, buttons = keywords_screen(self.cfg, what)
        await self.edit_screen(screen_id, caption, buttons)

    async def refresh(self, target, days):
        if target is None:
            return
        found = await self.check(tg=True, web=True, rescan_days=days, manual=True)
        if found is None:
            caption, buttons = "⏳ <b>Уже проверяю</b>\n\nЗагляни через минуту.", [[HOME_BTN]]
        else:
            caption, buttons = found_screen(found, days)
        await self.edit_screen(target, caption, buttons, refresh_picture=True)

    # ------------------------------------------------------------ buttons

    async def on_button(self, event):
        if event.sender_id != self.owner:
            return await event.answer("Это личный бот.", alert=True)
        parts = event.data.decode().split(":")
        action, args = parts[0], parts[1:]
        handler = {
            "h": self.b_home, "l": self.b_list, "o": self.b_open, "full": self.b_full, "close": self.b_close,
            "rf": self.b_refresh, "help": self.b_help, "s": self.b_settings, "k": self.b_keywords,
            "c": self.b_chats, "noop": self.b_noop,
        }.get(action)
        if handler:
            return await handler(event, *args)
        if action in ("t", "n", "r", "f"):
            return await self.b_job(event, action, *args)
        await event.answer()

    async def b_noop(self, event, *_):
        await event.answer()

    async def b_home(self, event):
        self.awaiting = None
        await event.answer()
        await self.show_home(event)

    async def b_list(self, event, view, page):
        await event.answer()
        await self.edit_screen(event, *list_screen(load_inbox(), view, int(page)))

    async def b_open(self, event, key, view="all", page="0"):
        item = load_inbox().get(key)
        if not item:
            return await event.answer("Вакансия не найдена", alert=True)
        await event.answer()
        await self.edit_screen(event, *card_screen(key, item, view, int(page)))

    async def b_job(self, event, action, key, view="all", page="0"):
        inbox = load_inbox()
        item = inbox.get(key)
        if not item:
            return await event.answer("Вакансия не найдена", alert=True)
        if action in ("t", "n") and item["status"] != "new":
            return await event.answer("Уже разобрано")
        if action == "t":
            if item.get("source") == "web":
                tc.add_to_pipeline([item["pipeline_line"]])
            else:
                job = from_inbox(item)
                tc.add_to_pipeline([tc.pipeline_line(job, tc.save_jd(job))])
            item["status"] = "taken"
            note = "👍 В работе! Оценка: /career-ops pipeline"
        elif action == "n":
            with DISCARD_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\t{item.get('link') or key}\thidden in job radar bot\n")
            item["status"] = "hidden"
            note = "🙈 Скрыто"
        elif action == "r":
            item["status"] = "new"
            note = "↩️ Вернул в новые"
        else:
            item["fav"] = not item.get("fav", False)
            note = "⭐ В избранном" if item["fav"] else "Убрал из избранного"
        save_inbox(inbox)
        await event.answer(note)
        await self.edit_screen(event, *card_screen(key, item, view, int(page)))

    async def b_full(self, event, key):
        item = load_inbox().get(key)
        await event.answer()
        if item:
            text = f"<b>{esc(title_of(item))}</b>\n{esc(origin_of(item))}\n\n{esc(item.get('text', ''))}"
            await self.bot.send_message(self.owner, text[:4000], buttons=[[btn("✖ Закрыть", "close")]], link_preview=False)

    async def b_close(self, event):
        await event.answer()
        await event.delete()

    async def b_refresh(self, event, days):
        await event.answer("Проверяю…")
        await self.edit_screen(event, f"⏳ <b>Проверяю чаты за {days} дн. и сайты…</b>\n\nЭто займёт до минуты.", None)
        await self.refresh(event, int(days))

    async def b_help(self, event):
        await event.answer()
        await self.edit_screen(event, HELP, [[HOME_BTN]])

    async def b_settings(self, event, name=None, value=None):
        if name:
            search = tc.load_search()
            if name == "web":
                search["boards_enabled"] = value == "on"
            else:
                field = {"int": "interval_minutes", "lb": "lookback_days", "ml": "min_length", "wi": "boards_interval_hours"}[name]
                search[field] = int(value)
            tc.save_search(search)
            await event.answer("Сохранил ✓")
        else:
            self.awaiting = None
            await event.answer()
        self.reload()
        await self.edit_screen(event, *settings_screen(self.cfg))

    async def b_keywords(self, event, sub, group=None, index=None):
        self.reload()
        if sub == "add":
            self.awaiting = (group, event.message_id)
            await event.answer()
            return await self.edit_screen(event, *input_screen(group))
        if sub == "del":
            search = tc.load_search()
            patterns = search.get("keywords", {}).get(group, [])
            if int(index) < len(patterns):
                removed = patterns.pop(int(index))
                tc.save_search(search)
                self.reload()
                await event.answer(f"Удалил: {show_pattern(removed)}")
            else:
                await event.answer()
        else:
            group = sub
            self.awaiting = None
            await event.answer()
        await self.edit_screen(event, *keywords_screen(self.cfg, group))

    async def b_chats(self, event, sub=None, index=None):
        if sub == "add":
            self.awaiting = ("chat", event.message_id)
            await event.answer()
            return await self.edit_screen(event, *input_screen("chat"))
        if sub == "del":
            chats = tc.load_channels(tc.CHANNELS_PATH)
            if int(index) < len(chats):
                remove_channel(chats[int(index)])
                await event.answer(f"Убрал: {chats[int(index)]}")
            else:
                await event.answer()
        else:
            self.awaiting = None
            await event.answer()
        await self.edit_screen(event, *chats_screen())


async def main():
    logging.basicConfig(
        filename=LOG_PATH, encoding="utf-8", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    cfg = tc.load_config()
    if not cfg.get("bot_token"):
        raise SystemExit("В telegram/config.yml нет bot_token — создай бота у @BotFather и впиши токен.")
    app = JobRadar(cfg)
    await app.start()
    await app.show_home()
    asyncio.create_task(app.scheduler())
    await app.bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
