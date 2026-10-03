"""Job Radar — personal Telegram bot that works like an app.

One screen (a dashboard picture with a caption and buttons) that changes in
place: home -> lists -> job card -> settings, instead of a stream of messages.

Sources:
- Telegram: chats from channels.txt, checked every `interval_minutes`,
  incrementally (only messages newer than the last one seen).
- Job boards: career-ops scan.mjs, every `boards_interval_hours`.

A job card: 👍 В работу -> data/pipeline.md for /career-ops pipeline,
👎 Скрыть, ⭐ favourite. Answers you (the account logged in via login.bat) and
the people you invite in 👥 Близкие (see guests.py).

Run: pyw -3 telegram/bot.py   (no console; log in data/tg-bot.log)
"""

import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from telethon import Button, TelegramClient, events, functions, types

import boards
import extras
import guests
import screens
import tg_collect as tc
import tunnel
import webapp

INBOX_PATH = tc.ROOT / "data" / "tg-inbox.json"
DISCARD_LOG = tc.ROOT / "data" / "discard.log"
LOG_PATH = tc.ROOT / "data" / "tg-bot.log"
SCREENS_DIR = tc.ROOT / "data" / "tg-screens"
BOT_SESSION = tc.TG_DIR / "bot"

PAGE_SIZE = 8
POOL_PAGE = 8
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


def to_int(value, default=0):
    """Button data can be forged by a modified client: never trust it to be a number."""
    try:
        return max(-1, min(int(value), 10_000))
    except (TypeError, ValueError):
        return default


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


to_inbox = guests.to_item
ACTIONS = {"t": "take", "n": "hide", "r": "restore", "f": "fav"}


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


def app_button(url, text="📱 Открыть приложение"):
    """A button that opens the Job Radar mini app inside Telegram."""
    return types.KeyboardInlineButton(text, types.InlineButtonTypeWebView(url))


def home_screen(inbox, next_check=None, owner=False, app_url=None):
    """The chat's only screen: the radar animation, a line about new vacancies and the app button.
    Everything else (feed, search, settings, résumé) lives in the app; the owner keeps 👥 Близкие here."""
    s = stats_of(inbox)
    if s["new"]:
        n = s["new"]
        hello = f"🔥 <b>{n}</b> {plural(n, 'новая вакансия ждёт', 'новые вакансии ждут', 'новых вакансий ждут')} тебя"
    else:
        hello = "✨ Всё разобрано — новых вакансий пока нет"
    lines = ["🐾 <b>Purrfect Job</b>", hello]
    if next_check:
        lines.append(f"⏱ Следующая проверка через {next_check} мин")
    rows = [[app_button(app_url, "📱 Открыть Purrfect Job")]] if app_url else \
        [[btn("🔄 Проверить сейчас", "rf:1")]]
    if not app_url:
        lines.append("<i>Приложение сейчас подключается — кнопка появится через минуту.</i>")
    if owner:
        rows.append([btn("👥 Близкие", "u")])
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


def card_screen(key, item, view="all", page=0, status_lines=STATUS_LINE):
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
    if item["status"] in status_lines:
        caption += f"\n\n{status_lines[item['status']]}"

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
        [btn(f"🔒 Мои закрытые чаты · {chats}", "c")],
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


def chats_screen(chats=None):
    if chats is None:
        chats = tc.load_channels(tc.CHANNELS_PATH)
    caption = ("<b>🔒 Мои чаты</b>\n\nЗакрытые чаты, их вижу только я и читаю только для тебя. "
               "Открытые каналы — в 📡 Каналы (общий пул).\n\n<i>Нажми на чат, чтобы убрать.</i>" if chats
               else "<b>🔒 Мои чаты</b>\n\nСписок пуст. Сюда — закрытые чаты, открытые каналы добавляй в 📡 Каналы.")
    rows = [[btn(f"✖ {c}"[:60], f"c:del:{i}")] for i, c in enumerate(chats)]
    rows.append([btn("➕ Добавить", "c:add"), btn("⬅️ Назад", "s")])
    return caption, rows


def input_screen(what):
    prompts = {
        "chat": "<b>➕ Новый чат</b>\n\nПришли сообщением:\n• @username\n• ссылку t.me/…\n"
                "• для приватного — ссылку на любое сообщение в нём\n• или точное название чата",
        "pool": "<b>➕ Канал в общий пул</b>\n\nПришли сообщением @username или ссылку t.me/… открытого канала "
                "или чата с вакансиями. Можно несколько — через пробел или с новой строки.\n\n"
                f"<i>Только открытые — те, что открываются по ссылке у любого. В пуле не больше "
                f"{guests.MAX_POOL} каналов на всех.</i>",
        "hhq": "<b>🔎 Запрос для hh.ru</b>\n\nПришли его так же, как написал бы в поиске на hh.ru. Например:\n"
               "• <code>аналитик данных</code>\n• <code>бухгалтер OR главбух</code>\n"
               "• <code>\"системный аналитик\" NOT стажёр</code>\n\n"
               f"<i>До {guests.HH_QUERY_MAX} символов. Вакансия придёт, если в её названии есть слово из «🎯 Что ищу».</i>",
    }
    if what in prompts:
        caption = prompts[what]
    else:
        caption = (f"<b>➕ {KEYWORD_GROUPS[what][0]}</b>\n\nПришли слово сообщением — можно несколько через запятую.\n\n"
                   "<i>Регистр не важен, ищу как часть слова: «аналит» найдёт и «аналитик», и «аналитика».</i>")
    back = {"chat": "c", "pool": "p:v:0", "hhq": "w"}.get(what, f"k:{what}")
    return caption, [[btn("✖ Отмена", back)]]


# ---------------------------------------------------------------- family mode (guests.py)

GUEST_STATUS_LINE = {"taken": "✅ <b>В работе</b>", "hidden": "🙈 <b>Скрыта</b>"}
GUEST_SUBTITLE = "Вакансии из Telegram-каналов"

GUEST_HELP = """<b>❓ Как это работает</b>

📡 <b>Каналы</b> — общий список открытых каналов (до 30 на всех). Отметь ✓ те, что нужны тебе: читаю их каждые 15 минут. Можно добавить свой канал.
🌐 <b>Сайты</b> — сайты вакансий, тоже отмечаешь нужные. Проверяю раз в 2 часа, ищу твои слова в названии вакансии.

<b>Вакансия из канала</b> — сообщение, где есть слово из «🔑 Признаки вакансии» (вакансия, ищем, hiring…), слово из «🎯 Что ищу» и нет слов из «🚫 Стоп-слова».

👍 <b>В работу</b> — отложить к себе (отметка ✅). 👎 <b>Скрыть</b> — убрать с глаз. ⭐ — в избранное.

<b>Обновить за сутки / 3 дня</b> — перечитать каналы за период (не чаще раза в 5 минут).

Никому ничего не пишу и ничего не отмечаю прочитанным."""

ONBOARD_ROLE = """👋 <b>Привет! Я Purrfect Job</b> — ищу вакансии в Telegram-каналах и присылаю только подходящие.

<b>Шаг 1 из 2. Какую работу ищешь?</b>
Пришли слова через запятую: профессию и главные навыки. Например: <i>бухгалтер, 1С, главбух</i> или <i>дизайнер, figma, ui</i>.

Проще всего — загрузить резюме в приложении (кнопка «Purrfect Job» → «▤ Резюме»): я сам предложу слова, каналы и запрос для hh.ru. Тогда жми «Настрою потом».

<i>Регистр не важен, ищу как часть слова: «бухгалт» найдёт и «бухгалтер», и «бухгалтерия».</i>"""

ROLE_KEEP_BUTTONS = [[Button.inline("Настрою потом →", b"rk")]]

ONBOARD_POOL = "<b>Шаг 2 из 2. Где искать?</b>\nОтметь каналы, которые читать для тебя, или добавь свой.\n\n"

SITES_FAQ = """<b>🔒 А закрытые и платные сайты?</b>

Бот читает только <b>открытые</b> сайты — где вакансии видны без входа.

Сайты с логином (hh.ru с откликами, LinkedIn, платные доски) почти все запрещают роботов и передачу доступа. Если бот будет заходить под чьим-то аккаунтом и читать за всех, аккаунт быстро заблокируют — иногда вместе с подпиской. И пароль пришлось бы хранить на компьютере.

Поэтому платный доступ каждый использует сам, в браузере или приложении сайта. А сюда — только открытые источники."""


def pool_screen(pool, member, page=0, onboarding=False):
    keys = sorted(pool["channels"], key=lambda k: pool["channels"][k].get("added", ""))
    ticked = set(member.selected(pool))
    free = guests.MAX_POOL - len(keys)
    lines = [ONBOARD_POOL if onboarding else "",
             f"<b>📡 Общие каналы</b>: {len(keys)} из {guests.MAX_POOL} · свободно {free}",
             f"У тебя отмечено: {len(ticked)}", ""]
    lines.append("✓ — читаю для тебя. Нажми на канал, чтобы включить или выключить." if keys
                 else "Пока пусто — добавь первый канал.")
    if not member.is_owner:
        left = guests.pool_adds_left(pool, member)
        lines.append(f"<i>Новых каналов сегодня можно добавить: {left}. 🗑 — убрать канал, который добавил ты.</i>")
    pages = max(1, (len(keys) + POOL_PAGE - 1) // POOL_PAGE)
    page = max(0, min(page, pages - 1))
    rows = []
    for k in keys[page * POOL_PAGE:(page + 1) * POOL_PAGE]:
        title = pool["channels"][k].get("title") or ""
        label = ("✓ " if k in ticked else "◻️ ") + f"@{k}" + (f" · {title}" if title and title.lower() != k else "")
        row = [btn(label[:48], f"p:t:{k}:{page}")]
        if guests.can_remove(pool, k, member):
            row.append(btn("🗑", f"p:d:{k}:{page}"))
        rows.append(row)
    nav = ([btn("◀️", f"p:v:{page - 1}")] if page else []) + ([btn("▶️", f"p:v:{page + 1}")] if page < pages - 1 else [])
    if nav:
        rows.append(nav)
    tools = [btn("➕ Добавить", "p:a")] if free > 0 else []
    if keys:
        tools += [btn("✓ Все", f"p:all:{page}"), btn("◻️ Никакие", f"p:none:{page}")]
    if tools:
        rows.append(tools)
    rows.append([btn("Готово →", "skip")] if onboarding else [HOME_BTN])
    return "\n".join(lines).strip(), rows


def pool_delete_screen(pool, key, page):
    title = pool["channels"].get(key, {}).get("title", "")
    return (f"<b>Убрать @{esc(key)} из общего пула?</b>\n{esc(title)}\n\n"
            "Канал пропадёт у всех, кто его отметил. Найденные вакансии останутся."), \
        [[btn("🗑 Да, убрать", f"p:dok:{key}:{page}")], [btn("⬅️ Назад", f"p:v:{page}")]]


def sites_screen(member):
    listed = guests.listed_sites()
    mine = set(member.sites(listed))
    lines = [f"<b>🌐 Сайты вакансий</b>: {len(listed)} · у тебя отмечено {len(mine)}", ""]
    if member.is_owner:
        lines.append("✓ — сайт включён в твой поиск career-ops (фильтры из portals.yml). "
                     "Близкие отмечают сайты из этого же списка для себя.")
    else:
        lines += ["✓ — присылаю тебе вакансии оттуда, если в названии вакансии есть слово из «🎯 Что ищу». "
                  "Проверяю раз в 2 часа.",
                  "<i>Сайты в основном англоязычные: добавь слова и по-английски (accountant, designer…).</i>"]
    hh = guests.hh_settings(member)
    hh_line = (f"🔎 <b>hh.ru</b>: «{esc(hh['query'])}»" + (", только удалёнка" if hh["remote"] else "")
               if hh["query"] else "🔎 <b>hh.ru</b>: запрос не задан")
    lines += ["", hh_line + ". Проверяю раз в 30 минут, до 20 свежих вакансий за раз."]
    rows = []
    order = sorted(listed.items(), key=lambda kv: (kv[0] != guests.HH_ID, kv[1].lower()))
    for pid, name in order:
        row = [btn(("✓ " if pid in mine else "◻️ ") + name, f"w:t:{pid}")]
        if member.is_owner and not guests.is_own_site(pid):
            row.append(btn("🗑", f"w:d:{pid}"))
        rows.append(row)
        if pid == guests.HH_ID and pid in mine:
            rows.append([btn("🔎 Запрос для hh.ru", "w:q"),
                         btn(("✓" if hh["remote"] else "◻️") + " Только удалёнка", "w:r")])
    rows.append([btn("➕ Добавить сайт" if member.is_owner else "💡 Предложить сайт", "w:add"),
                 btn("🔒 А платные сайты?", "w:faq")])
    rows.append([HOME_BTN])
    return "\n".join(lines), rows


def catalog_screen(member):
    listed = guests.listed_sites()
    rest = [(pid, v) for pid, v in guests.SITE_CATALOG.items() if pid not in listed]
    if not rest:
        return "<b>Все сайты, которые я умею читать, уже в списке.</b>", [[btn("⬅️ Назад", "w")]]
    head = ("<b>➕ Добавить сайт</b>\n\nСайт появится в общем списке выключенным — каждый отметит его себе сам."
            if member.is_owner else
            "<b>💡 Предложить сайт</b>\n\nВладелец бота получит предложение и решит, добавлять ли.\n"
            f"<i>Можно {guests.PROPOSALS_PER_DAY} предложения в сутки.</i>")
    head += "\n\n<i>Здесь только сайты, которые я умею читать без входа и настроек.</i>"
    action = "a" if member.is_owner else "p"
    rows = [[btn(f"{name} — {desc}"[:60], f"w:{action}:{pid}")] for pid, (name, desc) in rest]
    rows.append([btn("⬅️ Назад", "w")])
    return head, rows


def guest_settings_screen(guest):
    kw = guest.cfg().get("keywords", {})
    role = ", ".join(show_pattern(p) for p in kw.get("role", [])) or "—"
    caption = (
        "<b>⚙️ Настройки</b>\n\n"
        f"🎯 <b>Что ищу</b>: {esc(short(role, 300))}\n"
        f"🚫 Стоп-слов: {len(kw.get('negative', []))} · 🔑 признаков вакансии: {len(kw.get('job', []))}\n\n"
        "<i>Каналы и сайты — кнопками 📡 и 🌐 на главном экране.</i>"
    )
    rows = [
        [btn("🎯 Что ищу", "k:role"), btn("🚫 Стоп-слова", "k:negative")],
        [btn("🔑 Признаки вакансии", "k:job"), HOME_BTN],
    ]
    return caption, rows


def family_screen(reg, pool, bot_username, invite=None, full=False):
    people = guests.all_guests(reg)
    lines = [f"<b>👥 Близкие</b>: {len(people)} из {guests.MAX_MEMBERS}", ""]
    if people:
        for g in people:
            n = len(g.selected(pool))
            joined = datetime.fromisoformat(g.info["joined"])
            lines.append(f"• <b>{esc(g.name)}</b> — {n} {plural(n, 'канал', 'канала', 'каналов')}, с {joined:%d.%m}")
    else:
        lines.append("Пока никого. Нажми «➕ Пригласить» и отправь ссылку человеку.")
    lines += ["", f"📡 Каналов в общем пуле: {len(pool['channels'])} из {guests.MAX_POOL}. "
                  "Читаю их через твой аккаунт каждые 15 минут.",
              "🤖 ИИ для резюме работает через твою подписку Claude, и текст резюме уходит в Claude. "
              "Включай его тем, кому доверяешь (до 12 запросов в день на человека)."]
    if invite:
        lines += ["", "🔗 <b>Приглашение</b> — на одного человека, действует 7 дней:",
                  f"<code>https://t.me/{bot_username}?start={invite}</code>",
                  "<i>Нажми на ссылку, чтобы скопировать, и перешли её человеку.</i>"]
    if full:
        lines += ["", f"⚠️ Мест нет: не больше {guests.MAX_MEMBERS} человек (считая неиспользованные приглашения)."]
    rows = [[btn(f"🤖 ИИ {'✓' if g.info.get('ai') else '—'}", f"u:ai:{g.id}"), btn(f"✖ {g.name}"[:40], f"u:del:{g.id}")]
            for g in people]
    rows.append([btn("➕ Пригласить", "u:inv"), HOME_BTN])
    return "\n".join(lines), rows


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
        self.guest_awaiting = {}  # the same, per guest id
        self.me = None  # you as a guests.Member (pool ticks, inbox)
        self.pool_run = None
        self.sites_run = None
        self.sites_busy = False
        self.hh_run = None
        self.hh_busy = False
        self.daily_run = None
        self.last_seen = {}  # guest id -> time of the last action (anti-flood)
        self.bot_username = None
        self.app_url = None  # the mini app's public address (tunnel.py), None while offline
        self.webapp = None

    async def start(self):
        await self.user.connect()
        if not await self.user.is_user_authorized():
            raise SystemExit("Нет входа в Telegram: сначала запусти telegram\\login.bat")
        self.owner = (await self.user.get_me()).id
        self.me = guests.Owner(self.owner)
        for g in guests.all_guests(guests.load_registry()):  # the chat setup questions are gone: the app sets people up
            if g.load_state().get("step"):
                g.update_state(step=None, prompt_msg=None)
        try:
            moved = await guests.migrate_owner_channels(self.user, self.me)
            if moved:
                log.info("moved public channels to the shared pool: %s", moved)
        except Exception:
            log.exception("could not move public channels to the pool")
        await self.bot.start(bot_token=self.cfg["bot_token"])
        self.bot_username = (await self.bot.get_me()).username
        self.bot.add_event_handler(self.on_any_message, events.NewMessage(incoming=True, func=lambda e: e.is_private))
        self.bot.add_event_handler(self.on_button, events.CallbackQuery())
        await self.set_profile()
        log.info("started, owner %s", self.owner)
        try:
            self.webapp = webapp.WebApp(self)
            await self.webapp.start()
            asyncio.create_task(tunnel.run(webapp.PORT, self.set_app_url, self.cfg.get("cloudflared")))
        except Exception:
            log.exception("mini app did not start; the bot keeps working in the chat")

    async def set_app_url(self, url):
        """The tunnel got a (new) address, or lost it: re-point every «Открыть» button —
        the default menu button, each person's own one (clients cache the default), and
        the app buttons in messages already sent."""
        self.app_url = url
        button = types.BotMenuButton("Purrfect Job", url) if url else types.BotMenuButtonCommands()
        people = [self.owner] + [g.id for g in guests.all_guests(guests.load_registry())]
        for user in [None] + people:
            try:
                peer = types.InputUserEmpty() if user is None else await self.bot.get_input_entity(user)
                await self.bot(functions.bots.SetBotMenuButtonRequest(user_id=peer, button=button))
            except Exception:
                log.warning("could not set the menu button for %s", user or "everyone", exc_info=True)
        state = tc.load_state()
        if url and url != state.get("app_url"):
            state["app_url"] = url
            tc.save_state(state)
            await self.repoint_app_buttons(url)
        home = tc.load_state().get("home_msg")
        if home:
            try:
                caption, buttons = home_screen(load_inbox(), self.minutes_to_next_check(), owner=True, app_url=url)
                await self.edit_screen(home, caption, buttons)
            except Exception:
                pass  # the home message may be gone or unchanged

    async def set_profile(self):
        try:
            await self.bot(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(), lang_code="",
                commands=[types.BotCommand("start", "🐾 Purrfect Job")]))
            await self.bot(functions.bots.SetBotInfoRequest(
                lang_code="", about="Purrfect Job: удалённые вакансии из чатов Telegram и с сайтов",
                description="Purrfect Job находит вакансии в твоих Telegram-чатах и на сайтах удалённой работы "
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
                     chats=len(tc.load_channels(tc.CHANNELS_PATH)) + len(self.me.selected(guests.load_pool())),
                     boards=state.get("boards_scanned", 0) if self.cfg.get("boards_enabled", True) else 0,
                     checked_at=datetime.fromisoformat(last).astimezone().replace(second=0, microsecond=0) if last else None)
        return screens.render_dashboard(stats, SCREENS_DIR)

    def minutes_to_next_check(self):
        last = tc.load_state().get("last_run")
        if not last:
            return None
        due = datetime.fromisoformat(last) + timedelta(minutes=self.cfg.get("interval_minutes", 15))
        return max(1, int((due - datetime.now(timezone.utc)).total_seconds() // 60) + 1)

    def guest_dashboard(self, guest):
        inbox, state = guest.load_inbox(), guest.load_state()
        last = state.get("pool_run")
        stats = dict(stats_of(inbox), chats=len(guest.selected(guests.load_pool())),
                     boards=len(guest.sites(guests.listed_sites())),
                     checked_at=datetime.fromisoformat(last).astimezone().replace(second=0, microsecond=0) if last else None)
        return screens.render_dashboard(stats, guest.screens_dir, subtitle=GUEST_SUBTITLE)

    async def send_screen(self, caption, buttons, silent=False, guest=None, home=False):
        """A message to you or a guest: text with buttons; the home screen comes with the radar animation."""
        to = guest.id if guest else self.owner
        try:
            if home:
                anim = screens.radar_animation(SCREENS_DIR, self.cfg.get("brand") or screens.BRAND)
                msg = await self.bot.send_file(to, str(anim), caption=fit(caption), buttons=buttons, silent=silent)
            else:
                msg = await self.bot.send_message(to, fit(caption, 4000), buttons=buttons, silent=silent, link_preview=False)
        except Exception:
            log.exception("cannot message %s — did they press Start in the bot chat?", to)
            return None
        self.remember_app_button(to, msg.id, buttons)
        return msg

    async def edit_screen(self, target, caption, buttons, refresh_picture=False, guest=None):
        """target: a CallbackQuery event or a message id."""
        caption = fit(caption)
        kw = {"buttons": buttons, "link_preview": False}  # pictures are gone; the home animation stays as it is
        chat = guest.id if guest else self.owner
        self.remember_app_button(chat, target if isinstance(target, int) else target.message_id, buttons)
        if isinstance(target, int):
            return await self.bot.edit_message(chat, target, caption, **kw)
        return await target.edit(caption, **kw)

    def remember_app_button(self, chat, msg_id, buttons):
        """Note messages that carry an app button, so a new tunnel address can be patched in."""
        if not any(isinstance(b, types.KeyboardInlineButton) and isinstance(b.type, types.InlineButtonTypeWebView)
                   for row in buttons or [] for b in (row if isinstance(row, list) else [row])):
            return
        state = tc.load_state()
        seen = [m for m in state.get("app_msgs", []) if m != [chat, msg_id]]
        state["app_msgs"] = (seen + [[chat, msg_id]])[-200:]
        tc.save_state(state)

    async def repoint_app_buttons(self, url):
        """Put the new address into the app buttons of messages already sent."""
        by_chat = {}
        for chat, msg_id in tc.load_state().get("app_msgs", []):
            by_chat.setdefault(chat, []).append(msg_id)
        fixed, alive = 0, []
        for chat, ids in by_chat.items():
            try:
                msgs = await self.bot.get_messages(chat, ids=ids)
            except Exception:
                log.warning("app buttons: cannot read messages in %s", chat, exc_info=True)
                continue
            for msg in msgs:
                if not msg or not getattr(msg, "reply_markup", None):
                    continue
                rows = [[types.KeyboardInlineButton(b.text, types.InlineButtonTypeWebView(url))
                         if isinstance(b, types.KeyboardInlineButton) and isinstance(b.type, types.InlineButtonTypeWebView) else b
                         for b in row.buttons] for row in msg.reply_markup.rows]
                try:
                    await self.bot.edit_message(chat, msg.id, buttons=rows)
                    fixed += 1
                    alive.append([chat, msg.id])
                except Exception as e:
                    if "not modified" in str(e).lower():
                        alive.append([chat, msg.id])
                await asyncio.sleep(0.3)
        state = tc.load_state()
        state["app_msgs"] = alive[-200:]
        tc.save_state(state)
        log.info("app buttons re-pointed in %d messages", fixed)

    async def show_home(self, target=None):
        caption, buttons = home_screen(load_inbox(), self.minutes_to_next_check(), owner=True, app_url=self.app_url)
        if target is not None:
            return await self.edit_screen(target, caption, buttons, refresh_picture=True)
        state = tc.load_state()
        msg = await self.send_screen(caption, buttons, home=True)
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
            if tg and manual:  # your ticked pool channels; on schedule they go with everyone's (pool_pass)
                found += await self.guarded("tg", self.owner_pool(rescan_days), manual)
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

    def found(self, items, period=None):
        """The «new vacancies» screen, with the app button on top once the app is online."""
        caption, rows = found_screen(items, period)
        if self.app_url and items:
            rows.insert(0, [app_button(self.app_url, "📱 Разобрать в приложении")])
        return caption, rows

    async def alert(self, items):
        await self.notify(self.me, items)

    async def notify(self, member, items):
        """«N новых вакансий» in the chat, unless the person switched these off in the app («Ещё»)."""
        if items and not member.load_state().get("alerts_off"):
            await self.send_screen(*self.found(items), guest=self.g(member))

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
            if not self.pool_run or now - self.pool_run >= timedelta(minutes=self.cfg.get("interval_minutes", 15)):
                self.pool_run = now
                try:
                    await self.pool_pass()
                except Exception:
                    log.exception("pool check failed")
            if not self.sites_run or now - self.sites_run >= guests.SITES_EVERY:
                self.sites_run = now
                asyncio.create_task(self.sites_task())  # slow (minutes): don't hold up the clock
            if not self.hh_run or now - self.hh_run >= guests.HH_EVERY:
                self.hh_run = now
                asyncio.create_task(self.hh_task())
            if not self.daily_run or now - self.daily_run >= timedelta(minutes=10):
                self.daily_run = now
                await self.daily_pass()
            await asyncio.sleep(60)

    # ------------------------------------------------------------ messages

    async def on_any_message(self, event):
        if event.sender_id == self.owner:
            return await self.on_message(event)
        await self.on_guest_message(event)

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
        if what == "pool" or (what == "chat" and guests.public_username(text)):
            return await self.pool_input(self.me, text, screen_id)  # open channels go to the shared pool
        if what == "hhq":
            return await self.hh_query_input(self.me, text, screen_id)
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
            caption, buttons = self.found(found, days)
        await self.edit_screen(target, caption, buttons, refresh_picture=True)

    # ------------------------------------------------------------ buttons

    async def on_button(self, event):
        if event.sender_id != self.owner:
            return await self.on_guest_button(event)
        parts = event.data.decode().split(":")
        action, args = parts[0], parts[1:]
        handler = {
            "h": self.b_home, "l": self.b_list, "o": self.b_open, "full": self.b_full, "close": self.b_close,
            "rf": self.b_refresh, "help": self.b_help, "s": self.b_settings, "k": self.b_keywords,
            "c": self.b_chats, "noop": self.b_noop, "u": self.b_family,
            "p": lambda e, *a: self.pool_action(e, self.me, *(a or ["v"])),
            "w": lambda e, *a: self.sites_action(e, self.me, *a),
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
        changed, note = guests.job_action(self.me, key, item, ACTIONS[action])
        if not changed:
            return await event.answer(note)
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

    # ------------------------------------------------------------ shared plumbing for you and guests

    def g(self, member):
        """send_screen/edit_screen take guest=None for you."""
        return None if member.is_owner else member

    def member_for(self, uid):
        if uid == self.owner:
            return self.me
        return guests.get(guests.load_registry(), uid)

    def set_awaiting(self, member, value):
        if member.is_owner:
            self.awaiting = value
        elif value is None:
            self.guest_awaiting.pop(member.id, None)
        else:
            self.guest_awaiting[member.id] = value

    def too_fast(self, uid):
        """Anti-flood for guests: about one action per second, extra ones are dropped."""
        t = time.monotonic()
        last = self.last_seen.get(uid, 0.0)
        self.last_seen[uid] = t
        return t - last < 0.7

    def store_for(self, member, jobs):
        return self.store(jobs) if member.is_owner else self.guest_store(member, jobs)

    def guest_store(self, guest, jobs):
        inbox = guest.load_inbox()
        items = []
        for job in jobs:
            key = job_key(job)
            if key not in inbox:
                inbox[key] = to_inbox(job)
                items.append(inbox[key])
        guest.save_inbox(inbox)
        return items

    # ------------------------------------------------------------ 📡 pool and 🌐 sites: checking

    async def check_pool(self, members, rescan_days=None):
        """Read the ticked pool channels for these people. The caller holds self.lock."""
        pool = guests.load_pool()
        found, missing = await guests.scan_pool(self.user, members, pool, rescan_days=rescan_days)
        gone = sorted(missing)
        if gone != self.me.load_state().get("pool_missing", []):
            self.me.update_state(pool_missing=gone)
            if gone:
                await self.send_screen("⚠️ <b>Не открываются каналы из общего пула</b>\n\n"
                                       + "\n".join(f"• @{esc(k)}" for k in gone)
                                       + "\n\nВозможно, канал закрыли или переименовали. Убрать — в 📡 Каналы.",
                                       [[btn("📡 Каналы", "p:v:0")], [HOME_BTN]])
        log.info("pool: %s", {m.name: len(found[m.id]) for m in members})
        return {m.id: self.store_for(m, found[m.id]) for m in members}

    async def owner_pool(self, rescan_days):
        return (await self.check_pool([self.me], rescan_days))[self.me.id]

    async def pool_pass(self):
        if self.lock.locked():
            return
        people = [g for g in guests.all_guests(guests.load_registry()) if not g.load_state().get("step")]
        async with self.lock:
            result = await self.check_pool([self.me] + people)
        for m in [self.me] + people:
            if result[m.id]:
                await self.notify(m, result[m.id])

    async def sites_pass(self):
        listed = guests.listed_sites()
        people = [g for g in guests.all_guests(guests.load_registry())
                  if not g.load_state().get("step") and g.sites(listed)]
        wanted = {s for m in people + [self.me] for s in m.sites(listed)}  # yours too: the app's search reads the cache
        if not wanted - {guests.HH_ID}:
            return
        feed = await guests.fetch_feed(sorted(wanted))
        guests.write_json(webapp.FEED_CACHE_PATH, {"at": datetime.now(timezone.utc).isoformat(), "feed": feed})
        errors = {p: r["error"] for p, r in feed.items() if "error" in r}
        if errors:
            log.warning("site feed errors: %s", errors)
        for g in people:
            items = self.guest_store(g, guests.match_site_jobs(feed, g, listed))
            log.info("sites for %s: %d new", g.name, len(items))
            if items:
                await self.notify(g, items)

    async def hh_pass(self):
        listed = guests.listed_sites()
        people = [self.me] + [g for g in guests.all_guests(guests.load_registry()) if not g.load_state().get("step")]
        for m in people:
            settings = guests.hh_settings(m)
            if guests.HH_ID not in m.sites(listed) or not settings["query"]:
                continue
            try:
                raw = await guests.fetch_hh(settings["query"], settings["remote"])
            except Exception as e:
                log.warning("hh.ru for %s: %s", m.name, e)
                continue
            items = self.store_for(m, guests.match_hh_jobs(raw, m))
            log.info("hh.ru for %s: %d of %d new", m.name, len(items), len(raw))
            if items:
                await self.notify(m, items)
            await asyncio.sleep(guests.HH_PAUSE)

    async def hh_task(self):
        if self.hh_busy:
            return
        self.hh_busy = True
        try:
            await self.hh_pass()
        except Exception:
            log.exception("hh.ru pass failed")
        finally:
            self.hh_busy = False

    async def sites_task(self):
        if self.sites_busy:
            return
        self.sites_busy = True
        try:
            await self.sites_pass()
        except Exception:
            log.exception("sites pass failed")
        finally:
            self.sites_busy = False

    # ------------------------------------------------------------ ☀️ morning digest, 📋 tracker reminders

    async def daily_pass(self):
        """Once a day after 9:00 (the person's time zone): the 5 best new vacancies of the last day.
        In the daytime: a reminder about responses that have been silent for a week."""
        people = [self.me] + [g for g in guests.all_guests(guests.load_registry()) if not g.load_state().get("step")]
        for m in people:
            try:
                if extras.digest_due(m, m.load_state()):
                    m.update_state(digest_date=extras.local_now(m).date().isoformat())
                    items, total = extras.digest_items(m, m.load_inbox())
                    log.info("digest for %s: %d of %d", m.name, len(items), total)
                    if items:
                        await self.send_screen(*self.digest_screen(items, total), guest=self.g(m))
                if not 9 <= extras.local_now(m).hour < 22:
                    continue
                inbox = m.load_inbox()
                due = extras.due_reminders(inbox)
                if due:
                    for _, item in due:
                        item["track"]["reminded"] = item["track"]["at"]
                    m.save_inbox(inbox)
                    await self.send_screen(*self.reminder_screen(due), guest=self.g(m))
            except Exception:
                log.exception("daily pass for %s failed", m.name)

    def digest_screen(self, items, total):
        lines = [f"☀️ <b>Доброе утро! {len(items)} {plural(len(items), 'лучшая вакансия', 'лучшие вакансии', 'лучших вакансий')} за сутки</b>", ""]
        for n, (_, item) in enumerate(items, 1):
            title = esc(short(title_of(item), 60))
            if item.get("link"):
                title = f'<a href="{esc(item["link"])}">{title}</a>'
            extra = [item.get("company") or origin_of(item)]
            if extras.salary_of(item):
                extra.append("💰 " + extras.salary_of(item))
            if item.get("ai"):
                extra.append(f"✨ {item['ai']['score']}/5")
            lines.append(f"{n}. {title}\n   <i>{esc(short(' · '.join(x for x in extra if x), 70))}</i>")
        if total > len(items):
            lines.append(f"\nВсего новых за сутки: {total}")
        rows = [[app_button(self.app_url, "📱 Разобрать в приложении")]] if self.app_url else []
        return "\n".join(lines), rows + [[HOME_BTN]]

    def reminder_screen(self, due):
        lines = ["⏰ <b>Тишина уже неделю</b>", ""]
        for _, item in due[:8]:
            track = item["track"]
            lines.append(f"• {esc(short(title_of(item), 50))}" + (f" — {esc(short(item.get('company', ''), 30))}" if item.get("company") else "")
                         + f"\n   <i>{extras.STAGES[track['stage']].lower()}, {extras.silent_days(item)} дн. без новостей</i>")
        lines.append("\nМожно написать короткое письмо-напоминание или отметить этап: «Ещё → 📋 Мои отклики».")
        rows = [[app_button(self.app_url, "📋 Открыть отклики")]] if self.app_url else []
        return "\n".join(lines), rows + [[HOME_BTN]]

    # ------------------------------------------------------------ 📡 pool and 🌐 sites: screens

    async def pool_action(self, event, member, sub="v", *args):
        pool = guests.load_pool()
        onboarding = not member.is_owner and member.load_state().get("step") == "channels"
        key = args[0] if sub in ("t", "d", "dok") and args else None
        page = to_int(args[1] if key is not None and len(args) > 1 else (args[0] if args else 0))
        note = None
        if key is not None and key not in pool["channels"]:
            note = "Этого канала уже нет в пуле"
        elif sub == "t":
            ticked = member.selected(pool)
            if key in ticked:
                ticked.remove(key)
                note = f"Выключил @{key}"
            else:
                ticked.append(key)
                note = f"✓ Читаю @{key}"
            member.set_selected(ticked)
        elif sub == "all":
            member.set_selected(list(pool["channels"]))
            note = "✓ Отметил все"
        elif sub == "none":
            member.set_selected([])
            note = "Снял все отметки"
        elif sub == "a":
            if len(pool["channels"]) >= guests.MAX_POOL:
                return await event.answer(f"В пуле уже {guests.MAX_POOL} каналов — это предел.", alert=True)
            if guests.pool_adds_left(pool, member) <= 0:
                return await event.answer(f"Сегодня уже добавлено {guests.POOL_ADDS_PER_DAY} канала. Завтра можно ещё.", alert=True)
            self.set_awaiting(member, ("pool", event.message_id))
            await event.answer()
            return await self.edit_screen(event, *input_screen("pool"), guest=self.g(member))
        elif sub == "d":
            if not guests.can_remove(pool, key, member):
                return await event.answer("Убрать канал может тот, кто его добавил, или владелец бота.", alert=True)
            await event.answer()
            return await self.edit_screen(event, *pool_delete_screen(pool, key, page), guest=self.g(member))
        elif sub == "dok":
            if not guests.can_remove(pool, key, member):
                return await event.answer("Убрать канал может тот, кто его добавил, или владелец бота.", alert=True)
            guests.remove_from_pool(pool, key)
            log.info("pool: %s removed @%s", member.name, key)
            note = f"Убрал @{key} из пула"
        self.set_awaiting(member, None)
        await event.answer(note)
        await self.edit_screen(event, *pool_screen(guests.load_pool(), member, page, onboarding), guest=self.g(member))

    async def hh_query_input(self, member, text, screen_id):
        query = guests.clean_hh_query(text)
        if query:
            guests.set_hh(member, query=query)
            if guests.HH_ID not in member.sites(guests.listed_sites()):
                member.set_sites(member.sites(guests.listed_sites()) + [guests.HH_ID])
        caption, rows = sites_screen(member)
        await self.edit_screen(screen_id, ("✅ Запрос для hh.ru сохранён.\n\n" if query else "") + caption, rows,
                               guest=self.g(member))

    async def pool_input(self, member, text, screen_id):
        pool = guests.load_pool()
        added, refused = await guests.add_to_pool(self.user, pool, member, text)
        if added:
            log.info("pool: %s added %s", member.name, added)
        onboarding = not member.is_owner and member.load_state().get("step") == "channels"
        caption, rows = pool_screen(guests.load_pool(), member, 0, onboarding)
        notes = ([f"✅ Отметил: {esc(', '.join('@' + k for k in added))}"] if added else [])
        notes += [f"❌ {esc(w)} — {why}" for w, why in refused]
        if notes:
            caption = "\n".join(notes) + "\n\n" + caption
        await self.edit_screen(screen_id, caption, rows, guest=self.g(member))

    async def sites_action(self, event, member, sub=None, pid=None):
        listed = guests.listed_sites()
        note, alert = None, False
        try:
            if sub == "t" and pid in listed:
                mine = member.sites(listed)
                on = pid not in mine
                if member.is_owner and not guests.is_own_site(pid):
                    guests.set_board_enabled(pid, on)
                else:
                    member.set_sites(mine + [pid] if on else [s for s in mine if s != pid])
                note = f"✓ {listed[pid]}" if on else f"Выключил {listed[pid]}"
                if pid == guests.HH_ID and on and not guests.hh_settings(member)["query"]:
                    query = guests.default_hh_query(member)
                    if not query:  # no words yet: ask for the query right away
                        self.set_awaiting(member, ("hhq", event.message_id))
                        await event.answer()
                        return await self.edit_screen(event, *input_screen("hhq"), guest=self.g(member))
                    guests.set_hh(member, query=query)
                    note = "✓ hh.ru — запрос собрал из твоих слов, поправь при желании"
            elif sub == "q":
                self.set_awaiting(member, ("hhq", event.message_id))
                await event.answer()
                return await self.edit_screen(event, *input_screen("hhq"), guest=self.g(member))
            elif sub == "r":
                remote = not guests.hh_settings(member)["remote"]
                guests.set_hh(member, remote=remote)
                note = "✓ Только удалёнка" if remote else "Любой формат работы"
            elif sub == "add":
                await event.answer()
                return await self.edit_screen(event, *catalog_screen(member), guest=self.g(member))
            elif sub == "faq":
                await event.answer()
                return await self.edit_screen(event, SITES_FAQ, [[btn("⬅️ Назад", "w")]], guest=self.g(member))
            elif sub == "a" and member.is_owner and pid in guests.SITE_CATALOG:
                guests.add_board(pid)
                note = f"Добавил {guests.SITE_CATALOG[pid][0]} — отметь его ✓, если нужен тебе"
            elif sub == "p" and not member.is_owner and pid in guests.SITE_CATALOG and pid not in listed:
                pool = guests.load_pool()
                if guests.proposals_left(pool, member) <= 0:
                    note, alert = f"Не больше {guests.PROPOSALS_PER_DAY} предложений в сутки.", True
                else:
                    guests.record_proposal(pool, member)
                    name, desc = guests.SITE_CATALOG[pid]
                    await self.bot.send_message(
                        self.owner, f"💡 <b>{esc(member.name)}</b> предлагает добавить сайт <b>{esc(name)}</b> — {esc(desc)}.",
                        buttons=[[btn("✅ Добавить", f"w:a:{pid}"), btn("✖ Не надо", "close")]])
                    note, alert = "Отправил владельцу бота. Если он добавит сайт, он появится в списке.", True
            elif sub == "d" and member.is_owner and pid in listed:
                await event.answer()
                return await self.edit_screen(event, (
                    f"<b>Убрать {esc(listed[pid])} из списка сайтов?</b>\n\nОн пропадёт у всех, кто его отметил, "
                    "и выключится в твоём portals.yml."), [[btn("🗑 Да, убрать", f"w:dok:{pid}")], [btn("⬅️ Назад", "w")]])
            elif sub == "dok" and member.is_owner and pid in listed:
                guests.remove_board(pid)
                note = f"Убрал {listed[pid]}"
        except Exception:
            log.exception("sites action %s %s failed", sub, pid)
            note, alert = "Не получилось изменить список сайтов — подробности в логе.", True
        await event.answer(note, alert=alert)
        await self.edit_screen(event, *sites_screen(member), guest=self.g(member))

    # ------------------------------------------------------------ 👥 family: the owner's side

    async def b_family(self, event, sub=None, uid=None):
        reg = guests.load_registry()
        invite, note, full = None, None, False
        g = guests.get(reg, uid) if uid else None
        if sub == "inv":
            invite = guests.new_invite(reg)
            guests.save_registry(reg)
            full = invite is None
        elif sub == "del" and g:
            await event.answer()
            return await self.edit_screen(event, (
                f"<b>Отключить {esc(g.name)}?</b>\n\n"
                "Бот перестанет отвечать этому человеку и читать для него каналы. "
                "Его настройки останутся в папке — новое приглашение вернёт всё как было. "
                "Каналы, которые он добавил в пул, останутся."),
                [[btn("✖ Да, отключить", f"u:delok:{uid}")], [btn("⬅️ Назад", "u")]])
        elif sub == "ai" and g:
            reg["users"][str(g.id)]["ai"] = not g.info.get("ai")
            guests.save_registry(reg)
            note = f"ИИ для {g.name}: {'включён' if reg['users'][str(g.id)]['ai'] else 'выключен'}"
            log.info("guest %s ai=%s", g.id, reg["users"][str(g.id)]["ai"])
        elif sub == "delok" and g and guests.remove(reg, uid):
            guests.save_registry(reg)
            log.info("guest removed: %s (%s)", g.name, uid)
            note = f"Отключил {g.name}"
        await event.answer(note)
        await self.edit_screen(event, *family_screen(reg, guests.load_pool(), self.bot_username, invite, full))

    # ------------------------------------------------------------ 👥 family: the guest's side

    async def guest_show_home(self, guest, target=None):
        caption, buttons = home_screen(guest.load_inbox(), app_url=self.app_url)
        if target is not None:
            return await self.edit_screen(target, caption, buttons, refresh_picture=True, guest=guest)
        msg = await self.send_screen(caption, buttons, guest=guest, home=True)
        old = guest.load_state().get("home_msg")
        if msg and old:
            try:
                await self.bot.delete_messages(guest.id, old)
            except Exception:
                pass
        if msg:
            guest.update_state(home_msg=msg.id)

    async def guest_prompt(self, guest, text, buttons=None):
        """Onboarding questions: one message at a time, the previous one is removed."""
        old = guest.load_state().get("prompt_msg")
        msg = await self.bot.send_message(guest.id, text, buttons=buttons, link_preview=False)
        if old:
            try:
                await self.bot.delete_messages(guest.id, old)
            except Exception:
                pass
        guest.update_state(prompt_msg=msg.id)

    async def guest_finish_onboarding(self, guest):
        old = guest.load_state().get("prompt_msg")
        guest.update_state(step=None, prompt_msg=None)
        if old:
            try:
                await self.bot.delete_messages(guest.id, old)
            except Exception:
                pass
        if guest.selected(guests.load_pool()):
            msg = await self.send_screen("⏳ <b>Проверяю твои каналы за сутки…</b>\n\nЭто займёт до минуты.", None, guest=guest)
            if msg:
                guest.update_state(home_msg=msg.id)
                return await self.guest_refresh(guest, msg.id, 1, cooldown=False)
        await self.guest_show_home(guest)

    async def guest_refresh(self, guest, target, days, cooldown=True):
        last = guest.load_state().get("manual_at")
        wait = (datetime.fromisoformat(last) + guests.REFRESH_COOLDOWN - datetime.now(timezone.utc)) if last else None
        if cooldown and wait and wait.total_seconds() > 0:
            mins = int(wait.total_seconds() // 60) + 1
            caption, buttons = f"⏳ <b>Обновлять можно раз в 5 минут</b>\n\nПопробуй через {mins} мин.", [[HOME_BTN]]
        elif self.lock.locked():
            caption, buttons = "⏳ <b>Сейчас идёт проверка</b>\n\nЗагляни через минуту.", [[HOME_BTN]]
        else:
            guest.update_state(manual_at=datetime.now(timezone.utc).isoformat())
            async with self.lock:
                items = (await self.check_pool([guest], rescan_days=days))[guest.id]
            caption, buttons = self.found(items, days)
        await self.edit_screen(target, caption, buttons, refresh_picture=True, guest=guest)

    async def on_guest_message(self, event):
        try:
            await self.guest_message(event)
        except Exception:
            log.exception("guest message from %s failed", event.sender_id)
            try:
                await event.respond("Что-то пошло не так 😕 Нажми /start, чтобы вернуться на главный экран.")
            except Exception:
                pass

    async def guest_message(self, event):
        text = (event.raw_text or "").strip()[:guests.MAX_INPUT]
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        reg = guests.load_registry()
        guest = guests.get(reg, event.sender_id)
        if guest is None:
            if self.too_fast(event.sender_id):
                return
            code = text.split()[1] if command == "/start" and len(text.split()) > 1 else None
            if code and guests.use_invite(reg, code):
                guest = guests.register(reg, await event.get_sender())
                guests.save_registry(reg)
                log.info("guest joined: %s (%s) -> %s", guest.name, guest.id, guest.dir.name)
                await self.send_screen(f"👥 <b>{esc(guest.name)}</b> подключился по приглашению.",
                                       [[btn("👥 Близкие", "u")], [HOME_BTN]], silent=True)
                return await self.guest_show_home(guest)  # setup happens in the app (its own onboarding)
            guests.save_registry(reg)  # expired invites were dropped
            if code:
                return await event.respond("Ссылка-приглашение недействительна: она одноразовая и работает 7 дней. Попроси новую.")
            return await event.respond("Это личный бот. Чтобы им пользоваться, попроси у владельца ссылку-приглашение.")

        try:
            await event.delete()
        except Exception:
            pass
        if self.too_fast(guest.id):
            return
        step = guest.load_state().get("step")
        if step == "role":
            search = guest.load_search()
            added, refused = guests.add_words(search, "role", text) if text and not command else ([], [])
            if not added:
                notes = "\n".join(f"❌ {esc(w)} — {why}" for w, why in refused)
                return await self.guest_prompt(guest, (notes + "\n\n" if notes else "") + ONBOARD_ROLE, ROLE_KEEP_BUTTONS)
            guest.save_search(search)
            guest.update_state(step="channels")
            return await self.guest_prompt(guest, *pool_screen(guests.load_pool(), guest, 0, onboarding=True))

        awaiting = self.guest_awaiting.pop(guest.id, None)
        if awaiting and not command:
            what, screen_id = awaiting
            if what == "pool":
                return await self.pool_input(guest, text, screen_id)
            if what == "hhq":
                return await self.hh_query_input(guest, text, screen_id)
            search = guest.load_search()
            added, refused = guests.add_words(search, what, text)
            guest.save_search(search)
            caption, buttons = keywords_screen(guest.cfg(), what)
            if refused:
                caption = "\n".join(f"❌ {esc(w)} — {why}" for w, why in refused) + "\n\n" + caption
            return await self.edit_screen(screen_id, caption, buttons, guest=guest)
        if step == "channels":
            return await self.guest_prompt(guest, *pool_screen(guests.load_pool(), guest, 0, onboarding=True))
        if command == "/scan":
            msg = await self.send_screen("⏳ <b>Проверяю каналы…</b>", None, guest=guest)
            return await self.guest_refresh(guest, msg.id, None) if msg else None
        await self.guest_show_home(guest)

    async def on_guest_button(self, event):
        try:
            await self.guest_button(event)
        except Exception:
            log.exception("guest button %r from %s failed", event.data, event.sender_id)
            try:
                await event.answer("Что-то пошло не так 😕 Попробуй ещё раз.", alert=True)
            except Exception:
                pass

    async def guest_button(self, event):
        guest = guests.get(guests.load_registry(), event.sender_id)
        if guest is None:
            return await event.answer("Нет доступа. Попроси у владельца ссылку-приглашение.", alert=True)
        if self.too_fast(guest.id):
            return await event.answer("Не так быстро 🙂")
        parts = event.data.decode("utf-8", "replace")[:64].split(":")
        action, args = parts[0], parts[1:]
        step = guest.load_state().get("step")
        if action == "skip":
            await event.answer()
            return await self.guest_finish_onboarding(guest)
        if action == "rk" and step == "role":
            await event.answer()
            guest.update_state(step="channels")
            return await self.guest_prompt(guest, *pool_screen(guests.load_pool(), guest, 0, onboarding=True))
        if step == "role" or (step == "channels" and action != "p"):
            return await event.answer("Сначала ответь на вопрос выше 🙂")
        if action not in ("k", "p"):
            self.guest_awaiting.pop(guest.id, None)

        if action == "h":
            await event.answer()
            return await self.guest_show_home(guest, event)
        if action == "l" and len(args) == 2 and args[0] in VIEWS:
            await event.answer()
            return await self.edit_screen(event, *list_screen(guest.load_inbox(), args[0], to_int(args[1])), guest=guest)
        if action in ("o", "t", "n", "r", "f") and args:
            return await self.guest_job(event, guest, action, *args[:3])
        if action == "full" and args:
            item = guest.load_inbox().get(args[0])
            await event.answer()
            if item:
                text = f"<b>{esc(title_of(item))}</b>\n{esc(origin_of(item))}\n\n{esc(item.get('text', ''))}"
                await self.bot.send_message(guest.id, text[:4000], buttons=[[btn("✖ Закрыть", "close")]], link_preview=False)
            return
        if action == "close":
            await event.answer()
            return await event.delete()
        if action == "rf" and args and args[0] in ("1", "3"):
            await event.answer("Проверяю…")
            await self.edit_screen(event, f"⏳ <b>Проверяю каналы за {args[0]} дн.…</b>\n\nЭто займёт до минуты.", None, guest=guest)
            return await self.guest_refresh(guest, event, int(args[0]))
        if action == "help":
            await event.answer()
            return await self.edit_screen(event, GUEST_HELP, [[HOME_BTN]], guest=guest)
        if action == "s":
            await event.answer()
            return await self.edit_screen(event, *guest_settings_screen(guest), guest=guest)
        if action == "k" and args:
            return await self.guest_keywords(event, guest, *args[:3])
        if action == "p":
            return await self.pool_action(event, guest, *(args[:3] or ["v"]))
        if action == "w":
            return await self.sites_action(event, guest, *args[:2])
        await event.answer()

    async def guest_job(self, event, guest, action, key, view="all", page="0"):
        inbox = guest.load_inbox()
        item = inbox.get(key)
        view = view if view in VIEWS else "all"
        if not item:
            return await event.answer("Вакансия не найдена", alert=True)
        changed, note = guests.job_action(guest, key, item, ACTIONS.get(action, ""))
        if action in ("t", "n") and not changed:
            return await event.answer(note)
        if changed:
            guest.save_inbox(inbox)
        await event.answer(note)
        await self.edit_screen(event, *card_screen(key, item, view, to_int(page), GUEST_STATUS_LINE), guest=guest)

    async def guest_keywords(self, event, guest, sub, group=None, index=None):
        group = sub if sub in KEYWORD_GROUPS else group
        if group not in KEYWORD_GROUPS:
            return await event.answer()
        if sub == "add":
            self.guest_awaiting[guest.id] = (group, event.message_id)
            await event.answer()
            return await self.edit_screen(event, *input_screen(group), guest=guest)
        if sub == "del":
            search = guest.load_search()
            patterns = search.get("keywords", {}).get(group, [])
            i = to_int(index, -1)
            if 0 <= i < len(patterns):
                removed = patterns.pop(i)
                guest.save_search(search)
                await event.answer(f"Удалил: {show_pattern(removed)}")
            else:
                await event.answer()
        else:
            self.guest_awaiting.pop(guest.id, None)
            await event.answer()
        await self.edit_screen(event, *keywords_screen(guest.cfg(), group), guest=guest)

async def main():
    # basicConfig(encoding=) needs Python 3.9+, so pass a handler instead.
    logging.basicConfig(
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")], level=logging.INFO,
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
