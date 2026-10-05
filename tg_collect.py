"""Collect job posts from your Telegram chats into career-ops data/pipeline.md.

Reads every group/channel your account is in (including private ones), keeps
messages from the last `lookback_days` that look like job posts, saves each
one to jds/tg-*.md and adds a `- [ ] local:jds/...` line to the Pending section
of data/pipeline.md, so `/career-ops pipeline` evaluates it like any other JD.

Read-only: it never sends, reacts or marks anything as read.

Usage:
    python telegram/tg_collect.py            # collect
    python telegram/tg_collect.py --dry-run  # show matches, write nothing
"""

import argparse
import asyncio
import copy
import functools
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TG_DIR = ROOT / "telegram"
CONFIG_PATH = TG_DIR / "config.yml"
CHANNELS_PATH = TG_DIR / "channels.txt"
SEARCH_DEFAULT_PATH = TG_DIR / "search.default.yml"
SEARCH_PATH = TG_DIR / "search.yml"  # written by the bot's settings menu
SESSION_PATH = TG_DIR / "tg"  # Telethon adds .session
STATE_PATH = ROOT / "data" / "tg-state.json"
PIPELINE_PATH = ROOT / "data" / "pipeline.md"
JDS_DIR = ROOT / "jds"

PENDING_HEADERS = ("## pending", "## pendientes")


# ---------------------------------------------------------------- filtering

def flexible(pattern):
    """A typed phrase matches however it is written: «Anti fraud» = anti-fraud = antifraud,
    «антифрод» = анти-фрод. Spaces and hyphens between words are optional."""
    pattern = re.sub(r"анти-?\s?фрод", lambda _: r"анти[\s\-]?фрод", pattern, flags=re.IGNORECASE)
    pattern = re.sub(r"anti-?\s?fraud", lambda _: r"anti[\s\-]?fraud", pattern, flags=re.IGNORECASE)
    return pattern.replace(" ", r"[\s\-–—_]*")


@functools.lru_cache(maxsize=4096)
def _compiled(pattern, role=False):
    return re.compile(role_regex(pattern) if role else flexible(pattern), re.IGNORECASE)


def compile_patterns(patterns):
    # cached: a search builds a filter for thousands of vacancies, and compiling each time took a minute
    return [_compiled(p) for p in patterns or []]


def role_regex(pattern):
    """A «Что ищу» phrase of exactly two plain words matches in either order, up to 40 characters
    apart (the in-app search always did: «антифрод аналитик» finds «аналитик в команду антифрод»).
    Longer phrases, regexes and everything else keep their literal reading."""
    plain = re.sub(r"\\(.)", r"\1", pattern).strip()
    words = plain.split()
    if len(words) == 2 and all(re.fullmatch(r"[\w+#.\-]+", w) for w in words):
        a, b = (flexible(re.escape(w)) for w in words)
        return f"(?:{a}[^\\n]{{0,40}}{b}|{b}[^\\n]{{0,40}}{a})"
    return flexible(pattern)


def compile_role(patterns):
    return [_compiled(p, True) for p in patterns or []]


# Card-style channels (e.g. @revacancy_global) post «Title | Senior | IC / Company / 150k - 180k USD /
# Skills: … / Contacts and Full Description» and never say «вакансия» or «hiring».
JOB_CARD = re.compile(
    r"full description|required languages|\bskills\s*:|\bзарплата\s*:|\bз/п\s*:|"
    r"\d[\d\s.,]*k?\s*[-–]\s*\d[\d\s.,]*k?\s*(usd|eur|gbp|\$|€|₽|руб)", re.IGNORECASE)


# The person's job words are Russian / English; a post in another language has none of them and was
# silently dropped. These are the same «it's a vacancy» markers in other languages (the role words
# still have to match, so they widen the net only for the roles the person wants).
JOB_WORDS_INTL = re.compile(
    r"ваканс[іi]|шукаємо|запрошуємо|потрібен|потрібна|"                       # Ukrainian
    r"\bvacante\b|\bbuscamos\b|estamos contratando|oferta de (trabajo|empleo)|"  # Spanish
    r"\bvaga\b|\bvagas\b|contratando|oportunidade de emprego|"               # Portuguese
    r"wir suchen|stellenangebot|stellenanzeige|\(m/w/d\)|\(w/m/d\)|"          # German
    r"nous recrutons|offre d.emploi|poste à pourvoir|on recrute|"             # French
    r"oferta pracy|\bszukamy\b|zatrudnimy|"                                  # Polish
    r"iş ilanı|arıyoruz|"                                                    # Turkish
    r"cerchiamo|offerta di lavoro|"                                          # Italian
    r"\bvacature\b|wij zoeken|"                                              # Dutch
    r"бос жұмыс орны|"                                                       # Kazakh
    r"求人|招聘|채용",                                                         # Japanese / Chinese / Korean
    re.IGNORECASE)


class JobFilter:
    def __init__(self, keywords, min_length):
        self.job = compile_patterns(keywords.get("job"))
        self.role = compile_role(keywords.get("role"))
        self.negative = compile_patterns(keywords.get("negative"))
        self.tags = {name: compile_patterns(p) for name, p in (keywords.get("tags") or {}).items()}
        self.min_length = min_length

    def is_job(self, text):
        if not text or len(text) < self.min_length:
            return False
        if any(p.search(text) for p in self.negative) or looks_like_article(text):
            return False
        if any(p.search(text) for p in self.job) or JOB_WORDS_INTL.search(text):
            return any(p.search(text) for p in self.role)
        # a vacancy card: its first line is the job title, so the role must be there
        # (otherwise «Skills: sql, python» would let every engineer through)
        head = text.strip().splitlines()[0]
        return bool(JOB_CARD.search(text)) and any(p.search(head) for p in self.role)

    def has_job_word(self, text):
        return any(p.search(text) for p in self.job) or bool(JOB_CARD.search(text) or JOB_WORDS_INTL.search(text))

    def is_job_like(self, text):
        """A job post by its own words (вакансия, hiring…), whatever the role: for search."""
        if not text or len(text) < self.min_length or any(p.search(text) for p in self.negative):
            return False
        return self.has_job_word(text) and not looks_like_article(text)

    def why_not(self, text):
        """Why is_job said no, for a post that does mention one of the person's roles (the control
        sample): None if it is a job or no role is mentioned at all (then it is simply not theirs)."""
        if not text or not any(p.search(text) for p in self.role) or self.is_job(text):
            return None
        if len(text) < self.min_length:
            return "короткий"
        if any(p.search(text) for p in self.negative):
            return "стоп-слово"
        if looks_like_article(text):
            return "похож на статью"
        if JOB_CARD.search(text):
            return "роль не в названии"
        return "нет слова «вакансия»"

    def tags_for(self, text):
        return [name for name, pats in self.tags.items() if any(p.search(text) for p in pats)]


# An article, a market review, a digest of vacancies or a career-advice post mentions «вакансии»,
# «зарплата», «ищем» too. It is told apart by how it is written, and kept only when it also
# reads like an actual offer (duties, requirements, how to apply).
ARTICLE_SIGNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\(ссылка\)|\bссылка в (комментари|описани)|читать (полностью|далее|статью)|read more|full article",
    r"медианн\w*|по данным\b|исследовани\w*|опрос\w*|рейтинг\w*|статистик\w*|аналитик\w* (рынка|hh)|\bWEF\b|survey|report shows",
    r"подборк\w*|дайджест\w*|вакансии недели|топ[- ]\d+|\btop \d+\b|лучших вакансий|обзор рынка",
    r"\bстать\w*\b|лонгрид|колонк\w*|подкаст|вебинар|эфир\w*|мнение|совет\w* (соискател|кандидат)|как (найти|пройти|составить|написать)",
    r"професси\w* (будущего|до 20\d\d)|рынок труда|на рынке труда|job market|layoffs|увольнени\w* в",
)]
DIGEST_SIGN = re.compile(r"подборк\w* вакансий|дайджест\w* вакансий|вакансии (недели|дня)|лучших вакансий|топ[- ]\d+ вакансий", re.IGNORECASE)
OFFER_SIGNS = [re.compile(p, re.IGNORECASE) for p in (
    r"обязанност\w*|чем (предстоит|нужно) заниматься|задачи\s*:|что (делать|нужно делать)|responsibilit\w*|what you.ll do",
    r"требовани\w*|ожидаем|мы ждём|мы ждем|requirements|you have|must have|qualifications",
    r"(мы )?предлагаем|условия\s*:|we offer|benefits|компенсаци\w*|оформлени\w*",
    r"отклик\w*|присыла\w*|пиши(те)? (в|@)|резюме (на|в|присыла)|send (your )?(cv|resume)|\bapply\b|контакт\w*\s*:|@\w{4,}",
)]


def looks_like_article(text):
    head = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    article = sum(1 for p in ARTICLE_SIGNS if p.search(text)) + (1 if head.rstrip().endswith("?") else 0)
    article += 2 if DIGEST_SIGN.search(text) else 0  # a list of other people's vacancies
    offer = sum(1 for p in OFFER_SIGNS if p.search(text))
    return article >= 2 and offer < 2


def fingerprint(text):
    """Same post reposted into several chats gets the same fingerprint."""
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.sha1(norm[:600].encode("utf-8")).hexdigest()[:16]


def guess_title(text):
    for line in text.splitlines():
        line = re.sub(r"#\w+", "", line)  # hashtags
        line = re.sub(r"[^\w\s.,:()/+\-–—&'\"«»]", "", line)  # emoji and decorations
        line = re.sub(r"\s+", " ", line).strip(" -–—:*")
        if len(line) >= 4:
            return line[:90]
    return "Telegram job post"


def clean_cell(value):
    return re.sub(r"\s+", " ", str(value)).replace("|", "/").strip()


# ---------------------------------------------------------------- telegram

def message_link(entity, msg_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if entity.__class__.__name__ == "Channel":  # private channel or supergroup
        return f"https://t.me/c/{entity.id}/{msg_id}"
    return None  # basic groups have no message links


def load_channels(path):
    """channels.txt: one chat per line as @username, t.me link or exact title. Lines starting with # are comments."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def channel_key(name):
    """Normalize '@name', 't.me/name/45', 't.me/c/123/45' and plain titles to one comparable form."""
    n = name.strip().lower()
    link = re.match(r"^(https?://)?(www\.)?(t|telegram)\.me/(.+)$", n)
    if link:
        path = link.group(4)
        private = re.match(r"^c/(\d+)", path)
        return "id:" + private.group(1) if private else path.split("/")[0]
    return n[1:] if n.startswith("@") else n


def dialog_keys(entity, title):
    keys = {title.strip().lower(), "id:" + str(entity.id)}
    if getattr(entity, "username", None):
        keys.add(entity.username.lower())
    return keys


async def scan_chats(client, cfg, job_filter, state, dry_run=False, rescan_days=None):
    """Read the chats from channels.txt with a connected user client.

    Normal run: incremental, only messages newer than each chat's watermark and
    not older than lookback_days. rescan_days=N re-reads the last N days ignoring
    the watermark (catches posts missed by an older keyword set); posts already
    collected are still skipped by fingerprint.

    Returns (jobs, missing): new job posts oldest first, and channels.txt
    entries that matched no chat. Updates `state` in place.
    """
    days = rescan_days or cfg.get("lookback_days", 1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen_fps = set(state.get("fingerprints", []))
    last_ids = state.setdefault("last_ids", {})
    found = []
    channels = {n: channel_key(n) for n in load_channels(CHANNELS_PATH)}
    matched = set()

    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue
        entity, title = dialog.entity, dialog.name or "?"
        keys = dialog_keys(entity, title)
        wanted = [n for n, k in channels.items() if k in keys]
        if not wanted:
            continue
        matched.update(wanted)
        if dialog.date and dialog.date < cutoff:
            continue  # nothing new in this chat

        # Incremental load: the watermark is the last message id read in this chat. Telegram
        # message ids only grow within a chat, so unlike timestamps nothing is skipped or
        # re-read across clock changes or a run that crashed halfway.
        key = str(dialog.id)
        watermark = last_ids.get(key, 0)
        min_id = 0 if rescan_days else watermark
        if dialog.message and dialog.message.id <= min_id:
            continue  # no new messages since the last check
        newest = watermark
        async for msg in client.iter_messages(entity, min_id=min_id):
            if msg.date < cutoff:
                break
            newest = max(newest, msg.id)
            text = msg.message or ""
            if not job_filter.is_job(text):
                continue
            fp = fingerprint(text)
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            found.append({
                "chat": title,
                "chat_id": dialog.id,
                "msg_id": msg.id,
                "date": msg.date,
                "link": message_link(entity, msg.id),
                "text": text,
                "tags": job_filter.tags_for(text),
            })
        if not dry_run:
            last_ids[key] = newest

    if not dry_run:
        state["fingerprints"] = sorted(seen_fps)[-5000:]
    found.sort(key=lambda j: j["date"])
    return found, [n for n in channels if n not in matched]


def user_client(cfg):
    from telethon import TelegramClient

    return TelegramClient(str(SESSION_PATH), int(cfg["api_id"]), cfg["api_hash"])


async def collect(cfg, job_filter, state, dry_run):
    client = user_client(cfg)
    await client.start()  # first run asks for phone number and login code
    try:
        jobs, missing = await scan_chats(client, cfg, job_filter, state, dry_run)
    finally:
        await client.disconnect()
    for name in missing:
        print(f"! не нашёл чат из channels.txt: {name} (проверь название или что ты в нём состоишь)")
    return jobs


# ---------------------------------------------------------------- output

def save_jd(job):
    JDS_DIR.mkdir(exist_ok=True)
    name = f"tg-{job['date']:%Y-%m-%d}-{abs(job['chat_id'])}-{job['msg_id']}.md"
    body = [
        f"# {guess_title(job['text'])}",
        "",
        f"- Source: Telegram — {job['chat']}",
        f"- Posted: {job['date']:%Y-%m-%d %H:%M} UTC",
        f"- Link: {job['link'] or 'n/a (basic group, no message links)'}",
        "",
        "---",
        "",
        job["text"],
        "",
    ]
    (JDS_DIR / name).write_text("\n".join(body), encoding="utf-8")
    return f"jds/{name}"


def pipeline_line(job, jd_path):
    note = job["link"] or f"tg chat {job['chat']}"
    if job["tags"]:
        note += " [" + ", ".join(job["tags"]) + "]"
    location = "Remote" if "remote" in job["tags"] else ""
    cells = [
        f"local:{jd_path}",
        f"TG: {clean_cell(job['chat'])}",
        clean_cell(guess_title(job["text"])),
    ]
    if location:
        cells.append(location)
    cells += [f"posted: {job['date']:%Y-%m-%d}", f"note: {clean_cell(note)}"]
    return "- [ ] " + " | ".join(cells)


def add_to_pipeline(lines):
    text = PIPELINE_PATH.read_text(encoding="utf-8") if PIPELINE_PATH.exists() else "# Pipeline\n\n## Pending\n\n## Processed\n"
    out, inserted = [], False
    for line in text.splitlines():
        out.append(line)
        if not inserted and line.strip().lower() in PENDING_HEADERS:
            out.append("")
            out.extend(lines)
            inserted = True
    if not inserted:
        out += ["", "## Pending", ""] + lines
    tmp = PIPELINE_PATH.with_suffix(".md.partial")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, PIPELINE_PATH)


# ---------------------------------------------------------------- main

_yaml_cache = {}


def read_yaml(path):
    """A yaml file as a fresh dict. Parsing is slow (portals.yml, companies.yml: a third of a second), and a feed
    request read five of them, so the parsed text is kept until the file changes (modified time and size)."""
    path = Path(path)
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _yaml_cache.get(str(path))
    if not hit or hit[0] != key:
        hit = (key, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        _yaml_cache[str(path)] = hit
    return copy.deepcopy(hit[1])


def load_search():
    path = SEARCH_PATH if SEARCH_PATH.exists() else SEARCH_DEFAULT_PATH
    return read_yaml(path)


def save_search(search):
    header = "# Search settings, written by the bot (⚙️ Настройки). Safe to edit by hand.\n"
    body = yaml.safe_dump(search, allow_unicode=True, sort_keys=False, width=1000)
    SEARCH_PATH.write_text(header + body, encoding="utf-8")


def load_config():
    """config.yml (credentials) + search settings (search.yml or its defaults)."""
    cfg = read_yaml(CONFIG_PATH)
    return {**cfg, **load_search()}


def job_filter_from(cfg):
    return JobFilter(cfg.get("keywords", {}), cfg.get("min_length", 120))


def save_state(state):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows console: don't crash on emoji
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print matches, write nothing")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        sys.exit(f"Нет {CONFIG_PATH}. Скопируй config.example.yml в config.yml и впиши api_id / api_hash.")
    if not CHANNELS_PATH.exists():
        sys.exit(f"Нет {CHANNELS_PATH}. Впиши туда чаты, по одному на строку.")
    cfg = load_config()
    job_filter = job_filter_from(cfg)
    state = load_state()

    jobs = asyncio.run(collect(cfg, job_filter, state, args.dry_run))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if args.dry_run:
        for job in jobs:
            print(f"- {job['chat']} | {guess_title(job['text'])} | {job['tags']} | {job['link']}")
        print(f"[{stamp}] dry run: {len(jobs)} job post(s), nothing written")
        return

    lines = [pipeline_line(job, save_jd(job)) for job in jobs]
    if lines:
        add_to_pipeline(lines)
    save_state(state)
    print(f"[{stamp}] added {len(lines)} job post(s) to data/pipeline.md")
    for line in lines:
        print("  " + line)


if __name__ == "__main__":
    main()
