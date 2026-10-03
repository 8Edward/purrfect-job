"""Family mode: the same bot also serves people you invite.

Sources are SHARED, choices are personal:
- 📡 Channel pool (users/pool.json): public channels, at most 60 for everyone
  together, you included. They are read through YOUR account, so only public
  channels (@username) qualify. Everyone ticks the channels they want from the
  pool; each channel is read once per check and filtered per person. Your own
  private chats stay in channels.txt, seen by you only.
- 🌐 Sites: the job_boards list in portals.yml. You add sites (guests can only
  propose); everyone ticks the ones they want. For you, ticking toggles
  `enabled:` there (scan.mjs, as before); for guests the bot fetches the raw
  board feed (board-feed.mjs) and matches job titles against their words.

Each guest has a folder users/<telegram id>_<login>/ with search.yml (words,
ticked channels and sites), inbox.json and state.json. The folder is keyed by
the numeric id (a login can change), with the login added so you can tell whose
it is. Guests join with a one-time invite link from 👥 Близкие.
"""

import asyncio
import html
import json
import logging
import os
import re
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml
from telethon import utils

import boards
import tg_collect as tc

USERS_DIR = tc.TG_DIR / "users"
REGISTRY_PATH = USERS_DIR / "users.json"
POOL_PATH = USERS_DIR / "pool.json"
PRESET_PATH = tc.TG_DIR / "preset.data-analyst.yml"
RU_TAG = "ru-company"
PORTALS_PATH = tc.ROOT / "portals.yml"
OWNER_INBOX_PATH = tc.ROOT / "data" / "tg-inbox.json"
FEED_SCRIPT = tc.TG_DIR / "board-feed.mjs"

# Hard limits: keep your account's reading modest and the bot hard to break.
MAX_POOL = 60             # channels in the shared pool, everyone together
MAX_MEMBERS = 10          # invited people
POOL_ADDS_PER_DAY = 3     # new pool channels per guest per day (you: unlimited)
PROPOSALS_PER_DAY = 3     # site proposals per guest per day
MAX_WORDS = 40            # keywords per group
MAX_WORD_LEN = 40
MAX_INPUT = 1000          # characters of a typed message that get looked at
INBOX_CAP = 500           # stored vacancies per person; oldest unstarred go first
SITE_JOBS_PER_RUN = 20    # new site vacancies per person per check
REFRESH_COOLDOWN = timedelta(minutes=5)
POOL_EVERY = timedelta(minutes=15)
SITES_EVERY = timedelta(hours=2)
INVITE_TTL = timedelta(days=7)
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
DEFAULTS = {"lookback_days": 1, "min_length": 120}

# hh.ru: its official RSS feed of a vacancy search (no login, no API key; the
# API for job seekers was closed in Dec 2025). Up to 20 newest per query.
HH_ID = "hh"
HH_NAME = "hh.ru"
HH_RSS = "https://hh.ru/search/vacancy/rss"
HH_EVERY = timedelta(minutes=30)
HH_PAUSE = 3  # seconds between people's queries
HH_QUERY_MAX = 200
HH_PARTS_MAX = 6  # requests per hh.ru query (one per OR part)
HH_VACANCY_RE = re.compile(r"^https://([a-z0-9-]+\.)?hh\.ru/vacancy/\d+")

# Job boards that work as a whole board with no extra settings (each one checked
# with board-feed.mjs). Only these can be added from the bot.
SITE_CATALOG = {
    "himalayas": ("Himalayas", "удалёнка, все профессии"),
    "remoteok": ("Remote OK", "удалёнка, IT и не только"),
    "remotive": ("Remotive", "удалёнка: IT, маркетинг, поддержка"),
    "weworkremotely": ("We Work Remotely", "удалёнка, одна из крупнейших досок"),
    "workingnomads": ("Working Nomads", "удалёнка, разные профессии"),
    "jobicy": ("Jobicy", "удалёнка, разные профессии"),
    "nodesk": ("NoDesk", "удалёнка"),
    "jobspresso": ("Jobspresso", "удалёнка: IT и маркетинг"),
    "hackernews": ("Hacker News: Who is hiring", "IT, стартапы"),
    "4dayweek": ("4 Day Week", "4-дневная рабочая неделя"),
    "arbeitnow": ("Arbeitnow", "Германия и удалёнка"),
    "cryptocurrencyjobs": ("Cryptocurrency Jobs", "крипто и web3"),
    "getonbrd": ("Get on Board", "Латинская Америка, IT"),
    "landingjobs": ("Landing.jobs", "Европа, IT"),
    "thehub": ("The Hub", "стартапы Скандинавии"),
    "larajobs": ("LaraJobs", "PHP / Laravel"),
    "remotli": ("Remotli", "удалёнка, много вакансий"),
    "pythonorg": ("Python.org Jobs", "Python"),
    "generalist-world": ("Generalist World", "универсалы, операционка"),
    "agentic-jobs": ("Agentic Jobs", "AI / ML"),
    "flowxtra": ("Flowxtra", "Европа, разные профессии"),
}

log = logging.getLogger("job-radar.guests")


def now():
    return datetime.now(timezone.utc)


def read_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def recent(stamps, period=timedelta(days=1)):
    return [t for t in stamps if now() - datetime.fromisoformat(t) < period]


# ---------------------------------------------------------------- registry and invites

def load_registry():
    reg = read_json(REGISTRY_PATH, {})
    reg.setdefault("users", {})
    reg.setdefault("invites", {})
    return reg


def save_registry(reg):
    write_json(REGISTRY_PATH, reg)


def new_invite(reg):
    """A one-time code, or None when the bot is full (MAX_MEMBERS incl. open invites)."""
    drop_expired(reg)
    if len(reg["users"]) + len(reg["invites"]) >= MAX_MEMBERS:
        return None
    code = secrets.token_urlsafe(8)
    reg["invites"][code] = {"created": now().isoformat()}
    return code


def drop_expired(reg):
    for c, inv in list(reg["invites"].items()):
        if now() - datetime.fromisoformat(inv["created"]) > INVITE_TTL:
            del reg["invites"][c]


def use_invite(reg, code):
    """True if the code exists and is fresh. A code works once."""
    drop_expired(reg)
    return reg["invites"].pop(code, None) is not None


def folder_name(user_id, username=None, name=None):
    label = re.sub(r"[^\w-]+", "_", username or name or "").strip("_")[:32] or "user"
    return f"{user_id}_{label}"


# ---------------------------------------------------------------- people

class Member:
    """What the shared sources need from a person: words, ticks, inbox, state."""

    is_owner = False

    def cfg(self):
        return {**DEFAULTS, **self.load_search()}

    def update_state(self, **fields):
        state = self.load_state()
        state.update(fields)
        self.save_state(state)
        return state

    def selected(self, pool):
        return [k for k in self.load_search().get("pool", []) if k in pool["channels"]]

    def set_selected(self, keys):
        search = self.load_search()
        search["pool"] = list(dict.fromkeys(keys))
        self.save_search(search)

    def load_inbox(self):
        return read_json(self.inbox_path, {})

    def save_inbox(self, inbox):
        write_json(self.inbox_path, trim_inbox(inbox))


class Guest(Member):
    def __init__(self, uid, info):
        self.id = int(uid)
        self.info = info
        self.dir = USERS_DIR / info["dir"]
        self.search_path = self.dir / "search.yml"
        self.inbox_path = self.dir / "inbox.json"
        self.state_path = self.dir / "state.json"
        self.screens_dir = self.dir / "screens"

    @property
    def name(self):
        return "@" + self.info["username"] if self.info.get("username") else self.info.get("name") or str(self.id)

    def load_search(self):
        if self.search_path.exists():
            return yaml.safe_load(self.search_path.read_text(encoding="utf-8")) or {}
        return {}

    def save_search(self, search):
        self.dir.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(search, allow_unicode=True, sort_keys=False, width=1000)
        self.search_path.write_text("# Written by the bot (⚙️ Настройки). Safe to edit by hand.\n" + body, encoding="utf-8")

    def load_state(self):
        return read_json(self.state_path, {})

    def save_state(self, state):
        write_json(self.state_path, state)

    def sites(self, listed):
        return [s for s in self.load_search().get("sites", []) if s in listed]

    def set_sites(self, ids):
        search = self.load_search()
        search["sites"] = list(dict.fromkeys(ids))
        self.save_search(search)


class Owner(Member):
    """You: your files stay where they always were (search.yml, data/tg-*.json)."""

    is_owner = True
    name = "ты"

    def __init__(self, uid):
        self.id = uid
        self.inbox_path = OWNER_INBOX_PATH

    def load_search(self):
        return tc.load_search()

    def save_search(self, search):
        tc.save_search(search)

    def load_state(self):
        return tc.load_state()

    def save_state(self, state):
        tc.save_state(state)

    def sites(self, listed):
        mine = [b["provider"] for b in read_boards() if b.get("enabled", True) and b["provider"] in listed]
        return ([HH_ID] if HH_ID in self.load_search().get("sites", []) else []) + mine

    def set_sites(self, ids):  # only hh.ru lives here; job boards are `enabled:` in portals.yml
        search = self.load_search()
        search["sites"] = [HH_ID] if HH_ID in ids else []
        self.save_search(search)


def trim_inbox(inbox):
    """Keep at most INBOX_CAP vacancies: drop the oldest that aren't starred or taken."""
    extra = len(inbox) - INBOX_CAP
    if extra <= 0:
        return inbox
    droppable = sorted((k for k, v in inbox.items() if not v.get("fav") and v.get("status") != "taken" and not v.get("track")),
                       key=lambda k: inbox[k].get("date", ""))
    for k in droppable[:extra]:
        del inbox[k]
    return inbox


def get(reg, uid):
    info = reg["users"].get(str(uid))
    return Guest(uid, info) if info else None


def all_guests(reg):
    return [Guest(uid, info) for uid, info in reg["users"].items()]


def register(reg, user):
    """Add a Telegram user as a guest and create their folder with starter settings."""
    name = " ".join(filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)]))
    info = {
        "dir": folder_name(user.id, getattr(user, "username", None), name),
        "username": getattr(user, "username", None),
        "name": name[:64],
        "joined": now().isoformat(),
    }
    reg["users"][str(user.id)] = info
    guest = Guest(user.id, info)
    if not guest.search_path.exists():  # a returning guest keeps old settings
        guest.save_search(preset_search())
    guest.update_state(step="role")
    return guest


START_EMPTY = True  # since 2026-10-03: newcomers start with an empty feed setup (no words, channels, sites, hh)


def preset_search():
    """The settings a newcomer starts with: empty (START_EMPTY), or search.default.yml + the preset file.
    The hidden parts stay either way: the «is this a job post» words and the #ru_company tag."""
    defaults = yaml.safe_load(tc.SEARCH_DEFAULT_PATH.read_text(encoding="utf-8")) or {}
    kw = defaults.get("keywords", {})
    preset = load_preset()
    tags = dict(kw.get("tags", {}))
    if preset.get("ru_companies"):
        tags[RU_TAG] = preset["ru_companies"]
    search = {
        "lookback_days": DEFAULTS["lookback_days"],
        "min_length": DEFAULTS["min_length"],
        "keywords": {"job": kw.get("job", []), "role": preset.get("role", []),
                     "negative": preset.get("negative") or kw.get("negative", []), "tags": tags},
        "pool": [k for k in preset.get("pool", []) if k in load_pool()["channels"]],
        "sites": [s for s in preset.get("sites", []) if s in SITE_CATALOG],
    }
    if HH_ID in preset.get("sites", []) and preset.get("hh"):
        search["sites"].append(HH_ID)
        search["hh"] = preset["hh"]
    if START_EMPTY:
        search["keywords"].update(role=[], negative=[])
        search.update(pool=[], sites=[])
        search.pop("hh", None)
    return search


def reset_member(member, wipe_jobs=False):
    """«Начать заново»: settings back to the newcomer preset, résumé and app choices forgotten.
    Found vacancies, favourites and «в работе» stay unless wipe_jobs. Reading marks stay, so
    old posts don't flood back. For you, bot-wide settings (intervals, sites in portals.yml) stay."""
    fresh = preset_search()
    if member.is_owner:
        search = member.load_search()
        search["keywords"] = fresh["keywords"]
        search["pool"] = fresh["pool"]
        search["sites"] = [HH_ID] if HH_ID in fresh["sites"] else []  # job boards = portals.yml, untouched
        search["hh"] = fresh.get("hh", {})
        member.save_search(search)
    else:
        member.save_search(fresh)
    state = member.load_state()
    for k in ("skin", "app_onboarded", "cv", "manual_at", "resumes", "resume_draft"):
        state.pop(k, None)
    member.save_state(state)
    cv = (tc.ROOT / "data" / "tg-cv.txt") if member.is_owner else member.dir / "cv.txt"
    if cv.exists():
        cv.unlink()
    rdir = resume_dir(member)
    if rdir.exists():
        for f in rdir.glob("*.md"):
            f.unlink()
    if wipe_jobs:
        member.save_inbox({})


def resume_dir(member):
    """Where a person's résumé versions live (Markdown files)."""
    return (tc.ROOT / "data" / "tg-resumes") if member.is_owner else member.dir / "resumes"


def load_preset():
    """The starter settings newcomers get (telegram/preset.data-analyst.yml); {} if missing."""
    if PRESET_PATH.exists():
        return yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8")) or {}
    return {}


def web_tags(member, text):
    """Tags for a site vacancy (e.g. ru-company), from the person's own tag patterns."""
    tags = member.cfg().get("keywords", {}).get("tags") or {}
    return tc.JobFilter({"tags": tags}, 0).tags_for(text)


def parse_posted(text):
    """'2026-10-02T17:36:33+03:00' / '2026-10-02' / junk -> an aware datetime (now if unknown)."""
    for value in (str(text or "").strip(), str(text or "")[:10]):
        try:
            date = datetime.fromisoformat(value)
        except ValueError:
            continue
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    return now()


def web_job(j, site, member=None):
    """A raw site / hh.ru vacancy {title, url, company, location, comp, posted} -> a stored job."""
    date = parse_posted(j.get("posted"))
    company, title = j.get("company") or "", j.get("title") or "Вакансия"
    cells = [j["url"], company or site, title, j.get("location", ""), j.get("comp", "")]
    line = "- [ ] " + " | ".join(tc.clean_cell(c) for c in cells) + f" | posted: {date:%Y-%m-%d}"
    return {"source": "web", "key": boards.board_key(j["url"]), "title": title, "company": company,
            "location": j.get("location", ""), "comp": j.get("comp", ""), "site": site, "link": j["url"],
            "date": date, "tags": web_tags(member, f"{title} {company}") if member else [],
            "pipeline_line": line}


# ---------------------------------------------------------------- one vacancy: store and act

DISCARD_LOG = tc.ROOT / "data" / "discard.log"


def to_item(job):
    """A found job -> the stored inbox item (status new, not starred)."""
    item = dict(job, status="new", fav=False)
    item.setdefault("source", "tg")
    if item["source"] == "tg":
        item.setdefault("title", tc.guess_title(job["text"]))
    item["date"] = job["date"].isoformat()
    item.pop("key", None)
    return item


def job_action(member, key, item, action):
    """take / hide / restore / fav on one stored vacancy (changes `item`; the caller saves).
    Returns (changed, note). For you, «take» also puts it into career-ops pipeline.md."""
    if action in ("take", "hide") and item["status"] != "new":
        return False, "Уже разобрано"
    if action == "take":
        if member.is_owner:
            if item.get("source") == "web":
                tc.add_to_pipeline([item["pipeline_line"]])
            else:
                job = dict(item, date=datetime.fromisoformat(item["date"]))
                tc.add_to_pipeline([tc.pipeline_line(job, tc.save_jd(job))])
        item["status"] = "taken"
        return True, "👍 В работе! Оценка: /career-ops pipeline" if member.is_owner else "👍 В работе"
    if action == "hide":
        if member.is_owner:
            with DISCARD_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{now().isoformat(timespec='seconds')}\t{item.get('link') or key}\thidden in job radar\n")
        item["status"] = "hidden"
        return True, "🙈 Скрыто"
    if action == "restore":
        item["status"] = "new"
        return True, "↩️ Вернул в новые"
    if action == "fav":
        item["fav"] = not item.get("fav", False)
        return True, "⭐ В избранном" if item["fav"] else "Убрал из избранного"
    return False, None


def remove(reg, uid):
    """Stop serving a guest. Their folder stays (it's their data); an invite brings them back."""
    return reg["users"].pop(str(uid), None) is not None


# ---------------------------------------------------------------- keywords

def add_words(search, group, text):
    """Words typed in the chat -> literal, case-insensitive patterns. Returns (added, refused)."""
    patterns = search.setdefault("keywords", {}).setdefault(group, [])
    added, refused = [], []
    for word in (w.strip() for w in text[:MAX_INPUT].split(",")):
        if not word:
            continue
        if len(word) > MAX_WORD_LEN:
            refused.append((word[:MAX_WORD_LEN] + "…", f"длиннее {MAX_WORD_LEN} символов"))
            continue
        pattern = re.escape(word).replace("\\ ", " ")
        if pattern.lower() in {p.lower() for p in patterns}:
            continue
        if len(patterns) >= MAX_WORDS:
            refused.append((word, f"не больше {MAX_WORDS} слов в списке"))
            continue
        patterns.append(pattern)
        added.append(word)
    return added, refused


# ---------------------------------------------------------------- 📡 channel pool

def load_pool():
    pool = read_json(POOL_PATH, {})
    pool.setdefault("channels", {})
    pool.setdefault("adds", {})
    pool.setdefault("proposals", {})
    return pool


def save_pool(pool):
    write_json(POOL_PATH, pool)


def public_username(text):
    """'@Jobs_Data', 't.me/jobs_data/12' -> 'jobs_data'; private links and titles -> None."""
    key = tc.channel_key(text)
    return key if USERNAME_RE.match(key) else None


def split_channels(text):
    return [t for t in re.split(r"[\s,]+", text[:MAX_INPUT].strip()) if t][:10]


def pool_adds_left(pool, member):
    if member.is_owner:
        return MAX_POOL
    return max(0, POOL_ADDS_PER_DAY - len(recent(pool["adds"].get(str(member.id), []))))


def can_remove(pool, key, member):
    ch = pool["channels"].get(key)
    return bool(ch) and (member.is_owner or ch.get("added_by") == member.id)


async def resolve_public(client, username):
    """The channel or public group behind @username; ValueError if it isn't one."""
    entity = await client.get_entity(username)
    if entity.__class__.__name__ != "Channel" or not getattr(entity, "username", None):
        raise ValueError("это не открытый канал или группа")
    return entity


async def add_to_pool(client, pool, member, text):
    """Add the channels named in `text` to the pool and tick them for `member`.
    Returns (added keys, [(what was sent, why refused)]). Saves the pool."""
    added, refused = [], []
    ticked = member.selected(pool)
    for word in split_channels(text):
        key = public_username(word)
        if not key:
            refused.append((word, "нужен @username или ссылка t.me/… открытого канала"))
            continue
        if key in pool["channels"]:
            if key not in ticked:
                ticked.append(key)
                added.append(key)
            continue
        if len(pool["channels"]) >= MAX_POOL:
            refused.append((word, f"в пуле уже {MAX_POOL} каналов — это предел"))
            continue
        if pool_adds_left(pool, member) <= 0:
            refused.append((word, f"не больше {POOL_ADDS_PER_DAY} новых каналов в сутки"))
            continue
        try:
            entity = await resolve_public(client, key)
        except Exception:
            refused.append((word, "не нашёл такой открытый канал"))
            continue
        pool["channels"][key] = {"title": (getattr(entity, "title", "") or key)[:80],
                                 "added_by": member.id, "added": now().isoformat()}
        if not member.is_owner:
            pool["adds"].setdefault(str(member.id), []).append(now().isoformat())
            pool["adds"][str(member.id)] = recent(pool["adds"][str(member.id)])
        ticked.append(key)
        added.append(key)
    save_pool(pool)
    member.set_selected(ticked)
    return added, refused


def remove_from_pool(pool, key):
    pool["channels"].pop(key, None)
    save_pool(pool)


async def migrate_owner_channels(client, owner):
    """Move public channels from channels.txt into the pool (ticked for you); private ones stay."""
    if not tc.CHANNELS_PATH.exists():
        return []
    lines = tc.CHANNELS_PATH.read_text(encoding="utf-8-sig").splitlines()
    public = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#") and public_username(l)]
    if not public:
        return []
    pool = load_pool()
    added, _ = await add_to_pool(client, pool, owner, " ".join(public))
    moved = {public_username(l) for l in public} & (set(pool["channels"]))
    kept = [l for l in lines if not (public_username(l) in moved and not l.strip().startswith("#"))]
    tc.CHANNELS_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return sorted(moved)


async def scan_pool(client, members, pool, rescan_days=None, pause=1.0):
    """Read the ticked pool channels once each and filter per person.

    Incremental like the private-chat scan: per person and channel a watermark
    (state["pool_ids"]). rescan_days=N re-reads the last N days ignoring it.
    Returns ({member id: [job]}, [channel keys that didn't open]).
    """
    wanted = {}
    for m in members:
        for key in m.selected(pool):
            wanted.setdefault(key, []).append(m)
    filters = {m.id: tc.job_filter_from(m.cfg()) for m in members}
    states = {m.id: m.load_state() for m in members}
    seen = {m.id: set(states[m.id].get("fingerprints", [])) for m in members}
    marks = {m.id: dict(states[m.id].get("pool_ids", {})) for m in members}
    found = {m.id: [] for m in members}
    missing = []

    for key, followers in wanted.items():
        days = rescan_days or max(m.cfg().get("lookback_days", 1) for m in followers)
        cutoff = now() - timedelta(days=days)
        start = {m.id: 0 if rescan_days else marks[m.id].get(key, 0) for m in followers}
        try:
            entity = await client.get_entity(key)
            msgs = []
            async for msg in client.iter_messages(entity, min_id=min(start.values())):
                if msg.date < cutoff:
                    break
                msgs.append(msg)
        except Exception as e:
            log.warning("pool channel @%s: %s", key, e)
            missing.append(key)
            continue
        title = getattr(entity, "title", None) or "@" + key
        chat_id = utils.get_peer_id(entity)
        msgs.reverse()  # oldest first
        for m in followers:
            newest = marks[m.id].get(key, 0)
            for msg in msgs:
                newest = max(newest, msg.id)
                text = msg.message or ""
                if msg.id <= start[m.id] or not filters[m.id].is_job(text):
                    continue
                fp = tc.fingerprint(text)
                if fp in seen[m.id]:
                    continue
                seen[m.id].add(fp)
                found[m.id].append({
                    "chat": title,
                    "chat_id": chat_id,
                    "msg_id": msg.id,
                    "date": msg.date,
                    "link": f"https://t.me/{key}/{msg.id}",
                    "text": text,
                    "tags": filters[m.id].tags_for(text),
                })
            marks[m.id][key] = newest
        await asyncio.sleep(pause)  # gentle on Telegram

    for m in members:
        state = m.load_state()  # re-read: the UI may have written to it meanwhile
        state["pool_ids"] = marks[m.id]
        state["fingerprints"] = sorted(seen[m.id])[-5000:]
        if not rescan_days:
            state["pool_run"] = now().isoformat()
        m.save_state(state)
        found[m.id].sort(key=lambda j: j["date"])
    return found, missing


# ---------------------------------------------------------------- 🌐 sites (portals.yml job_boards)

def read_boards():
    data = yaml.safe_load(PORTALS_PATH.read_text(encoding="utf-8")) if PORTALS_PATH.exists() else {}
    return [b for b in (data or {}).get("job_boards") or [] if isinstance(b, dict) and b.get("provider")]


def listed_sites():
    """Sites in the shared list that the bot can serve: {id: name}. hh.ru is always there."""
    boards_ = {b["provider"]: SITE_CATALOG[b["provider"]][0] for b in read_boards() if b["provider"] in SITE_CATALOG}
    return {HH_ID: HH_NAME, **boards_}


def _board_block(lines):
    """(start, end) line range of the job_boards: list and its entries [(start, end, provider)]."""
    try:
        head = next(i for i, l in enumerate(lines) if re.match(r"^job_boards:\s*(#.*)?$", l))
    except StopIteration:
        return None, []
    end = next((i for i in range(head + 1, len(lines)) if re.match(r"^[^\s#]", lines[i])), len(lines))
    starts = [i for i in range(head + 1, end) if re.match(r"^\s+- ", lines[i])]
    entries = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else end
        while e > s + 1 and (not lines[e - 1].strip() or lines[e - 1].lstrip().startswith("#")):
            e -= 1
        provider = next((m.group(1) for l in lines[s:e] for m in [re.search(r"provider:\s*['\"]?([\w-]+)", l)] if m), None)
        entries.append((s, e, provider))
    return (head, end), entries


def _write_portals(lines):
    """Write portals.yml only if the result still parses; the old file stays otherwise."""
    text = "\n".join(lines) + "\n"
    yaml.safe_load(text)
    tmp = PORTALS_PATH.with_suffix(".yml.partial")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, PORTALS_PATH)


def set_board_enabled(provider, on):
    lines = PORTALS_PATH.read_text(encoding="utf-8").splitlines()
    _, entries = _board_block(lines)
    for s, e, p in entries:
        if p == provider:
            for i in range(s, e):
                if re.match(r"^\s+enabled:", lines[i]):
                    lines[i] = re.sub(r"enabled:.*$", f"enabled: {'true' if on else 'false'}", lines[i])
                    break
            else:
                indent = re.match(r"^(\s*)", lines[s + 1] if e > s + 1 else "    ").group(1) or "    "
                lines.insert(e, f"{indent}enabled: {'true' if on else 'false'}")
            return _write_portals(lines)
    raise KeyError(provider)


def add_board(provider):
    if provider not in SITE_CATALOG:
        raise KeyError(provider)
    if provider in {b["provider"] for b in read_boards()}:
        return
    lines = PORTALS_PATH.read_text(encoding="utf-8").splitlines()
    block, entries = _board_block(lines)
    entry = [f"  - name: {SITE_CATALOG[provider][0]}", f"    provider: {provider}", "    enabled: false"]
    if block is None:
        lines += ["", "job_boards:"] + entry
    else:
        at = entries[-1][1] if entries else block[0] + 1
        lines[at:at] = entry
    _write_portals(lines)


def remove_board(provider):
    lines = PORTALS_PATH.read_text(encoding="utf-8").splitlines()
    _, entries = _board_block(lines)
    for s, e, p in entries:
        if p == provider:
            del lines[s:e]
            return _write_portals(lines)


def proposals_left(pool, member):
    return max(0, PROPOSALS_PER_DAY - len(recent(pool["proposals"].get(str(member.id), []))))


def record_proposal(pool, member):
    pool["proposals"][str(member.id)] = recent(pool["proposals"].get(str(member.id), [])) + [now().isoformat()]
    save_pool(pool)


async def fetch_feed(providers, timeout=900):
    """Run board-feed.mjs for these providers: {provider: {"jobs": [...]} | {"error": "..."}}."""
    ids = [p for p in providers if p in SITE_CATALOG]
    if not ids:
        return {}
    proc = await asyncio.create_subprocess_exec(
        "node", str(FEED_SCRIPT), ",".join(ids), cwd=str(tc.ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("сайты не ответили за 15 минут")
    # split("\n"), not splitlines(): job texts carry U+2028 and friends, which splitlines() breaks on
    lines = [l.strip() for l in out.decode("utf-8", "replace").split("\n") if l.startswith("{")]
    if not lines:
        raise RuntimeError("board-feed.mjs: нет ответа")
    return json.loads(lines[-1])


def match_site_jobs(feed, member, listed):
    """New site vacancies for one person: title/company/location must contain one of
    their «Что ищу» words and none of the stop words."""
    kw = member.cfg().get("keywords", {})
    role, negative = tc.compile_patterns(kw.get("role")), tc.compile_patterns(kw.get("negative"))
    if not role:
        return []
    state = member.load_state()
    seen = set(state.get("site_seen", []))
    inbox = member.load_inbox()
    jobs = []
    for provider in member.sites(listed):
        for j in (feed.get(provider) or {}).get("jobs", []):
            key = boards.board_key(j["url"])
            if key in seen or key in inbox:
                continue
            seen.add(key)
            text = " ".join([j["title"], j.get("company", ""), j.get("location", "")])
            if not any(p.search(text) for p in role) or any(p.search(text) for p in negative):
                continue
            jobs.append(web_job(j, listed.get(provider, provider), member))
    member.update_state(site_seen=sorted(seen)[-20000:], sites_run=now().isoformat())
    return jobs[:SITE_JOBS_PER_RUN]


# ---------------------------------------------------------------- hh.ru (RSS)

def plain(pattern):
    """'\\bsql\\b' -> 'sql', 'power ?bi' -> 'power bi': a keyword as a person reads it."""
    text = pattern.replace("\\b", "").replace(" ?", " ").replace("-?", "-")
    return re.sub(r"\\(.)", r"\1", text)


def hh_settings(member):
    s = member.load_search().get("hh") or {}
    return {"query": str(s.get("query") or ""), "remote": bool(s.get("remote", True))}


def set_hh(member, **fields):
    search = member.load_search()
    hh = dict(search.get("hh") or {})
    if "query" in fields:
        fields["query"] = clean_hh_query(fields["query"])
    hh.update(fields)
    search["hh"] = hh
    member.save_search(search)


def clean_hh_query(text):
    return re.sub(r"\s+", " ", str(text)).strip()[:HH_QUERY_MAX]


def default_hh_query(member):
    """Their «Что ищу» words as an hh.ru OR-query (stems like «бухгалт» get a * wildcard)."""
    words = [plain(p) for p in member.cfg().get("keywords", {}).get("role", [])][:8]
    out = []
    for w in words:
        w = w.strip()
        if not w or not re.match(r"^[\w .+#/-]+$", w):
            continue
        out.append(f'"{w}"' if " " in w else w)
    return " OR ".join(out)


def parse_hh_rss(data):
    """hh.ru RSS bytes -> [{title, url, company, location, comp, posted}]."""
    root = ET.fromstring(data)
    jobs = []
    for item in root.findall("./channel/item")[:100]:
        link = (item.findtext("link") or "").strip()
        if not HH_VACANCY_RE.match(link):
            continue
        desc = html.unescape(re.sub(r"<[^>]+>", "\n", html.unescape(item.findtext("description") or "")))

        def field(label):
            m = re.search(label + r":\s*([^\n]+)", desc)
            return m.group(1).replace("\xa0", " ").strip()[:120] if m else ""

        jobs.append({
            "title": re.sub(r"\s+", " ", item.findtext("title") or "").strip()[:200] or "Вакансия",
            "url": link.split("?")[0],
            "company": field("Вакансия компании"),
            "location": field("Регион"),
            "comp": field("Предполагаемый уровень месячного дохода"),
            "posted": (item.findtext("pubDate") or "").strip()[:40],
        })
    return jobs


def hh_url(query, remote):
    # search_field=name: hh.ru otherwise matches the description too, and its RSS holds only
    # the 20 newest hits, so a few lucky words in descriptions crowded out every real title match
    params = {"text": query, "search_field": "name", "order_by": "publication_time"}
    if remote:
        params["schedule"] = "remote"
    return HH_RSS + "?" + urlencode(params)


def hh_parts(query):
    """«a OR "b c" OR d» -> separate queries: hh.ru RSS has no pages (20 items per query),
    so one request per part lets every role word get its own 20."""
    parts = [x.strip() for x in re.split(r"\s+OR\s+", query) if x.strip()]
    return list(dict.fromkeys(parts))[:HH_PARTS_MAX] or [query]


def _hh_get(query, remote):
    jobs, seen = [], set()
    for i, part in enumerate(hh_parts(query)):
        if i:
            time.sleep(0.7)
        req = Request(hh_url(part, remote), headers={"User-Agent": "Mozilla/5.0 (JobRadar personal job alerts)"})
        with urlopen(req, timeout=30) as resp:
            for j in parse_hh_rss(resp.read(3_000_000)):
                if j["url"] not in seen:
                    seen.add(j["url"])
                    jobs.append(j)
    return jobs


async def fetch_hh(query, remote):
    return await asyncio.get_running_loop().run_in_executor(None, _hh_get, query, remote)


HIMALAYAS_SEARCH = "https://himalayas.app/jobs/api/search"


def _himalayas_get(queries, pages=2):
    """Live Himalayas search (its feed is ~100k jobs, the cached feed holds only the newest):
    [{title, url, company, location, posted}]."""
    jobs, seen = [], set()
    for q in queries[:3]:
        for page in range(1, pages + 1):
            url = HIMALAYAS_SEARCH + "?" + urlencode({"q": q, "page": page})
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (JobRadar personal job alerts)"}), timeout=20) as resp:
                data = json.loads(resp.read(5_000_000))
            items = data.get("jobs") or []
            for it in items:
                link = str(it.get("applicationLink") or it.get("guid") or "")
                if not re.match(r"^https://([a-z0-9-]+\.)?himalayas\.app/", link) or link in seen:
                    continue
                seen.add(link)
                pub = it.get("pubDate")
                jobs.append({"title": str(it.get("title") or "")[:200], "url": link, "company": str(it.get("companyName") or "")[:120],
                             "location": ", ".join(str(x) for x in it.get("locationRestrictions") or [])[:120],
                             "posted": datetime.fromtimestamp(int(pub), timezone.utc).isoformat() if str(pub).isdigit() else ""})
            if len(items) < 20:
                break
    return jobs


async def fetch_himalayas(queries):
    return await asyncio.get_running_loop().run_in_executor(None, _himalayas_get, queries)


def match_hh_jobs(raw, member):
    """New hh.ru vacancies for one person: title must hold a «Что ищу» word (hh also
    matches the description, which drags in unrelated jobs), no stop words."""
    kw = member.cfg().get("keywords", {})
    role, negative = tc.compile_patterns(kw.get("role")), tc.compile_patterns(kw.get("negative"))
    state = member.load_state()
    seen = set(state.get("hh_seen", []))
    inbox = member.load_inbox()
    jobs = []
    for j in raw:
        key = boards.board_key(j["url"])
        if key in seen or key in inbox:
            continue
        seen.add(key)
        if role and not any(p.search(j["title"]) for p in role):
            continue
        if any(p.search(f"{j['title']} {j['company']}") for p in negative):
            continue
        jobs.append(web_job(j, HH_NAME, member))
    member.update_state(hh_seen=sorted(seen)[-20000:], hh_run=now().isoformat())
    return jobs[:SITE_JOBS_PER_RUN]
