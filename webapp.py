"""📱 The Job Radar mini app: a web page that opens inside Telegram.

The bot serves the page (telegram/webapp/) and a small JSON API on
127.0.0.1:8787. tunnel.py gives it a free public https address (Cloudflare
quick tunnel), which the bot puts behind its «Открыть» button.

Who is who: Telegram signs every app launch (initData) with the bot token.
Each API call sends that signature and we check it, so a person only ever sees
their own vacancies and settings: you (the owner), or a guest you invited.
Anyone else gets 403. Nothing here writes to Telegram chats.
"""

import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl

from aiohttp import web
from telethon import utils

import apply
import cv_reader
import extras
import filtercheck
import guests
import resume
import letters
import scoring
import resume_ai
import roles
import sourcestats
import tg_collect as tc
import usage
import watch

log = logging.getLogger("job-radar.app")

WEB_DIR = tc.TG_DIR / "webapp"
GRAPH_PATH = tc.TG_DIR / "rolegraph.yml"
FEED_CACHE_PATH = tc.ROOT / "data" / "tg-feed-cache.json"
FEEDBACK_PATH = tc.ROOT / "data" / "tg-feedback.json"   # «Ещё → Обратная связь»: [{who, id, text, at}], newest last
FEEDBACK_MAX, FEEDBACK_KEEP, FEEDBACK_GAP = 1500, 300, 30  # characters, stored messages, seconds between two from one person
HOST, PORT = "127.0.0.1", 8787
SKINS = ("hybrid", "remote", "office")  # cat themes; old names map onto them
OLD_SKINS = {"radar": "hybrid", "tropic": "remote", "dossier": "office"}
INIT_MAX_AGE = timedelta(days=1)  # how long one app launch stays valid
SEARCH_DAYS = 21                  # how far back search reads channels (job channels post a few a week)
POSTS_TTL = timedelta(minutes=15) # channel messages are re-read at most this often
POSTS_PER_CHANNEL = 300
SEARCH_WAIT = 20        # seconds a search waits for channels not read yet
TEXT_HEAD = 450          # a post counts for «в тексте поста» only when the phrase is in its first characters (digests / ads name it deep inside)
SEARCH_LIVE_WAIT = 6    # seconds a search waits for hh.ru / Himalayas
POSTS_CACHE_PATH = tc.ROOT / "data" / "tg-posts-cache.json"   # channel posts kept on disk (~4 MB), so the first search after a restart is fast
POSTS_STALE_MAX = timedelta(days=2)  # older than this the disk copy is not used
QUERY_MAX = 80
RESULTS_MAX = 80
NEAR_SLOTS = 30                   # «similar»: places kept for neighbour roles when the role itself has plenty
SEARCH_COOLDOWN = 0.5             # seconds between one person's searches (graph taps come fast)
ACTIONS = ("take", "hide", "restore", "fav", "archive", "unarchive", "unsee")
VIEWS = {
    "new": lambda i: i["status"] == "new",
    "fav": lambda i: i.get("fav") and not i.get("archived"),  # «Избранное»: swiping right stars a vacancy
    "taken": lambda i: i["status"] == "taken",
    "hidden": lambda i: i["status"] == "hidden" and not i.get("archived"),
    "archive": lambda i: i.get("archived"),  # «Ещё → Архив»: put away by hand, out of the other lists until returned
    "seen": lambda i: i.get("seen") and i["status"] == "new" and not i.get("archived"),  # «Мои → Просмотрено»: shown, not decided
    "all": lambda i: True,
}


# ---------------------------------------------------------------- who is calling

def check_init_data(init_data, bot_token, max_age=INIT_MAX_AGE):
    """Telegram's signed launch data -> the user dict, or None if forged or stale.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    got = pairs.pop("hash", "")
    if not got:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > max_age.total_seconds():
            return None
        user = json.loads(pairs.get("user", ""))
    except ValueError:
        return None
    return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None


# ---------------------------------------------------------------- role graph and matching

def norm(text):
    return str(text or "").lower().replace("ё", "е")


RU_ENDING = re.compile(r"(ого|ему|ыми|ими|ый|ий|ой|ая|яя|ое|ее|ые|ие|ов|ев|ом|ем|ам|ям|ах|ях|а|я|о|е|ы|и|й|ь|у|ю)$")


def ru_stem(word):
    """«системный» -> «системн», so any case ending matches; short words stay whole."""
    m = RU_ENDING.search(word)
    return word[:m.start()] if m and m.start() >= 4 else word


def phrase_re(phrase):
    """The phrase as typed, word for word, but forgiving: any case, «-» or space between
    words, Russian endings («аналитик данных» finds «аналитика данных»), English plurals."""
    words = re.findall(r"[\w+#&.]+", norm(phrase))
    if not words:
        return None
    parts = [re.escape(ru_stem(w)) + r"[а-я]{0,4}" if re.search(r"[а-я]", w) else re.escape(w) + r"(?:s|es)?"
             for w in words]
    return re.compile(r"(?<![\w])" + r"[\s\-–—/]+".join(parts) + r"(?![\w])", re.IGNORECASE)


FILLER = {"по", "в", "на", "и", "для", "с", "of", "and", "the", "for", "in", "&", "/"}


def word_pat(term):
    """One word (or a short hyphenated term) with the same forgiving rules as phrase_re."""
    words = re.findall(r"[\w+#&.]+", norm(term))
    parts = [re.escape(ru_stem(w)) + r"[а-я]{0,4}" if re.search(r"[а-я]", w) else re.escape(w) + r"(?:s|es)?"
             for w in words]
    return r"[\s\-–—/]*".join(parts)


def load_words():
    """Word equivalents from rolegraph.yml `words:` (антифрод = antifraud = fraud…): stem -> [terms]."""
    if not GRAPH_PATH.exists():
        return {}
    groups = tc.read_yaml(GRAPH_PATH).get("words") or []
    out = {}
    for group in groups:
        terms = [str(t) for t in group or [] if str(t).strip()]
        for t in terms:
            out.setdefault(ru_stem(" ".join(re.findall(r"[\w+#&.]+", norm(t)))), terms)
    return out


def title_re(phrase, words=None):
    """Search match: every word of the phrase somewhere on one line, in any order, each word
    also by its equivalents — «антифрод аналитик» finds «Аналитик (антифрод)», «Fraud Analyst»,
    «аналитик по антифроду». Prepositions are skipped. None for an empty phrase."""
    words = load_words() if words is None else words
    tokens = [w for w in re.findall(r"[\w+#&.]+", norm(phrase)) if w not in FILLER]
    if not tokens:
        return None
    looks = []
    for w in tokens:
        alts = dict.fromkeys([word_pat(w)] + [word_pat(t) for t in words.get(ru_stem(w), [])])
        looks.append(r"(?=[^\n]*?(?<![\w])(?:" + "|".join(a for a in alts if a) + r")(?![\w]))")
    return re.compile(r"^" + "".join(looks), re.IGNORECASE | re.MULTILINE)


PART_END = re.compile(r"\s[-–—|]\s|[,(|:]|\s(?:at|в команду|в компанию)\s")  # where a title's role part ends
# between words of one phrase: a space, a hyphen, «/», «&», a bracket, maybe a preposition; never « - » or «,»
# («Electrical Engineer - Data Centres» is no «инженер данных»)
TIGHT_SEP = r"(?:\s*[/&+]\s*|\s*[()\[\]]+\s*|-|\s+)(?:(?:по|в|на|и|для|с|of|in|for|and|the)\s+)?"
REV_SEP = r"(?:\s*[()\[\]]+\s*|\s+)(?:(?:по|в|на|для|с|of|in|for)\s+)?"


def tight_re(phrase, words=None):
    """The phrase's words side by side (either order for 2-3 words, a preposition or bracket may sit
    between), each also by its equivalents: «Аналитик (антифрод)», «аналитик по антифроду», «DWH Analyst».
    Unlike title_re, «Data Scientist, Marketing Analytics» does not count as «data analyst»."""
    words = load_words() if words is None else words
    tokens = [w for w in re.findall(r"[\w+#&.]+", norm(phrase)) if w not in FILLER]
    if not tokens:
        return None
    alts = ["(?:" + "|".join(dict.fromkeys(a for a in [word_pat(w)] + [word_pat(t) for t in words.get(ru_stem(w), [])] if a)) + ")"
            for w in tokens]
    # other orders only with a space, a bracket or a preposition between («аналитик DWH», «аналитик (антифрод)»),
    # not across «-» or «,»: «Electrical Engineer - Data Centres» is no data engineer
    others = [o for o in itertools.permutations(alts) if list(o) != alts] if len(alts) <= 3 else []
    return re.compile(r"(?<![\w])(?:" + "|".join([TIGHT_SEP.join(alts)] + [REV_SEP.join(o) for o in others]) + r")(?![\w])",
                      re.IGNORECASE)


def hh_variants(phrase, words=None):
    """The phrase for hh.ru plus its English twin («антифрод аналитик» -> «antifraud analyst»)."""
    words = load_words() if words is None else words
    tokens = [w for w in re.findall(r"[\w+#&.]+", norm(phrase)) if w not in FILLER]
    latin = [next((t for t in words.get(ru_stem(w), []) if not re.search(r"[а-я]", norm(t))), w) for w in tokens]
    return list(dict.fromkeys([" ".join(tokens), " ".join(latin)]))


def load_graph():
    if not GRAPH_PATH.exists():
        return {}
    roles = tc.read_yaml(GRAPH_PATH).get("roles") or {}
    graph = {rid: {"names": [str(n) for n in r.get("names") or []], "also": [str(n) for n in r.get("also") or []],
                   "not": [str(n) for n in r.get("not") or []],
                   "links": dict(r.get("links") or {})}
             for rid, r in roles.items() if isinstance(r, dict)}
    for rid, role in graph.items():  # links work both ways
        for other, kind in list(role["links"].items()):
            if other in graph:
                graph[other]["links"].setdefault(rid, kind)
    return graph


_matchers = {}


def role_matchers():
    """[(role id, name, pattern)] for every name and `also` title of every role, built once
    per version of rolegraph.yml."""
    if "list" in _matchers and time.monotonic() - _matchers.get("checked", 0) < 2:
        return _matchers["list"]  # a search asks thousands of times: the file is looked at once every 2 s
    mtime = GRAPH_PATH.stat().st_mtime if GRAPH_PATH.exists() else None
    _matchers["checked"] = time.monotonic()
    if _matchers.get("mtime") != mtime or "list" not in _matchers:
        words, out = load_words(), []
        for rid, role in load_graph().items():
            for name in role["names"] + role["also"]:
                rx = title_re(name, words) if len(name.strip()) > 3 else None  # «BA», «PM»: too noisy
                if rx:
                    out.append((rid, name, rx, tight_re(name, words)))
        _matchers.clear()
        _matchers.update(mtime=mtime, list=out, known={}, heads={})
    return _matchers["list"]


def roles_of_title(title):
    """Role ids the dictionary sees in a title (cached per title)."""
    matchers = role_matchers()
    t = norm(title)
    known = _matchers["known"]
    if t not in known:
        if len(known) > 50000:
            known.clear()
        known[t] = list(dict.fromkeys(rid for rid, _, rx, _ in matchers if rx.search(t)))
    return known[t]


def title_roles(title):
    """[(start, end, role id, name)] of role names standing side by side in a title, first first.
    The first one is what the job is: «Senior Data Engineer – Data Analytics Platform» is a data
    engineer, not a data analyst (cached per title)."""
    matchers = role_matchers()
    t = norm(title)
    heads = _matchers["heads"]
    if t not in heads:
        if len(heads) > 50000:
            heads.clear()
        found = []
        for rid, name, _, tight in matchers:
            m_ = tight.search(t) if tight else None
            if m_:
                found.append((m_.start(), m_.end(), rid, name))
        heads[t] = sorted(found)
    return heads[t]


def find_role(graph, query):
    """The role the query names: an exact name first, else the longest name whose words are all
    in the query (any order, word equivalents count: «аналитик dwh» -> dwh-analyst)."""
    q = " ".join(re.findall(r"[\w+#&.]+", norm(query)))
    for rid, role in graph.items():
        for name in role["names"] + role.get("also", []):
            if " ".join(re.findall(r"[\w+#&.]+", norm(name))) == q:
                return rid
    best, size = None, 0
    for rid, name, rx, _ in role_matchers():
        if rid in graph and len(name) > size and rx.search(norm(query)):
            best, size = rid, len(name)
    return best


def graph_view(graph, rid, query):
    """Nodes around the role: its other names (synonyms), adjacent and far roles."""
    role = graph[rid]
    qn = norm(query).strip()
    nodes = [{"id": f"syn:{i}", "label": n, "kind": "syn", "names": [n]}
             for i, n in enumerate(role["names"]) if norm(n) != qn]
    order = {"adj": 0, "far": 1}
    for other, kind in sorted(role["links"].items(), key=lambda kv: order.get(kv[1], 2)):
        if other in graph and kind in order:
            names = graph[other]["names"]
            nodes.append({"id": other, "label": names[0], "kind": kind, "names": names})
    return {"root": {"id": rid, "label": query}, "nodes": nodes}


# ---------------------------------------------------------------- shaping vacancies for the page

def excerpt(text, size):
    text = (text or "").strip()
    return text if len(text) <= size else text[:size].rsplit(" ", 1)[0] + "…"


def item_json(key, item, full=False):
    web_ = item.get("source") == "web"
    return {
        "key": key,
        "title": item.get("title") or tc.guess_title(item.get("text", "")),
        "company": item.get("company", ""),
        "location": item.get("location", ""),
        "comp": item.get("comp", ""),
        "source": "web" if web_ else "tg",
        "origin": item.get("site") if web_ else item.get("chat", ""),
        "link": item.get("link", ""),
        "date": item["date"] if isinstance(item["date"], str) else item["date"].isoformat(),
        "status": item.get("status", "new"),
        "fav": bool(item.get("fav")),
        "archived": bool(item.get("archived")),
        "seen": item.get("seen"),
        "tags": item.get("tags", []),
        "text": excerpt(item.get("text", ""), 4000 if full else 600),
        "salary": extras.salary_of(item),
        "salary_usd": int(extras.salary_value(item)),
        "tz": extras.tz_label(item),
        "ai": item.get("ai"),
        "track": item.get("track"),
        "silent": extras.silent_days(item),
        "summary": extras.summary(item),
        "old": extras.is_old(item),
        "grade": extras.grade_of(item),
    }


SORTS = {  # feed order: newest first, or by pay / AI score / starred, newest first among equals
    "date": lambda g: 0,
    "salary": lambda g: -max(extras.salary_value(v) for _, v in g),
    "ai": lambda g: -max(((v.get("ai") or {}).get("score") or 0) for _, v in g),
    "fav": lambda g: -int(any(v.get("fav") for _, v in g)),
    "az": lambda g: (min((v.get("title") or "").casefold().lstrip(" \"'«([#") for _, v in g), min((v.get("company") or "").casefold() for _, v in g)),
}
SOURCE_FILTERS = ("tg", "web", "hh")


def apply_filters(items, spec):
    """spec = «tg,web,ru»: the source chips (channels / sites / hh.ru) add up (any of them), the tag chips (ru, pt)
    all have to match; «nohh» = everything except hh.ru."""
    chips = [c for c in str(spec).split(",") if c in FILTERS or c == "nohh"]
    sources = [FILTERS[c] for c in chips if c in SOURCE_FILTERS]
    if "nohh" in chips:
        sources.append(lambda v: not FILTERS["hh"](v))
    tags = [FILTERS[c] for c in chips if c not in SOURCE_FILTERS and c != "nohh"]
    return [(k, v) for k, v in items if (not sources or any(s(v) for s in sources)) and all(t(v) for t in tags)]


PAGE_MAX = 100
INTERVALS = [15, 30, 60, 120, 240]  # «Как часто искать», minutes
FILTERS = {  # the feed's chips
    "ru": lambda v: "ru-company" in (v.get("tags") or []),
    "pt": lambda v: "part-time" in (v.get("tags") or []),
    "tg": lambda v: v.get("source", "tg") != "web",
    "hh": lambda v: v.get("source") == "web" and v.get("site") == "hh.ru",
    "web": lambda v: v.get("source") == "web" and v.get("site") != "hh.ru",
}
SCORE_BATCH = 10  # «Оценить новые»: at most this many per tap


def page_json(pairs, sort="date", offset=0, limit=40):
    """One page of a list with duplicates merged: only the page gets turned into JSON, so a feed of
    thousands stays light. -> (items, total)"""
    gs = extras.groups(pairs)  # pairs come newest first, and the sort is stable
    by = SORTS.get(sort, SORTS["date"])
    gs.sort(key=lambda g: (int(any((v.get("reality") or {}).get("kind") in ("evergreen", "closed", "stale") for _, v in g)), by(g)))  # ♾ / 🪦 go below
    return grouped_json(gs[offset:offset + limit], grouped=True), len(gs)


def grouped_json(pairs, grouped=False):
    """Vacancies with duplicates merged: one card, the other sources listed under «also»."""
    out = []
    for g in (pairs if grouped else extras.groups(pairs)):
        (key, item), rest = g[0], g[1:]
        data = item_json(key, item)
        if rest:
            data["also"] = [{"key": k, "origin": extras.origin(v), "source": "web" if v.get("source") == "web" else "tg",
                             "link": v.get("link", ""), "location": (v.get("location") or "")[:40]} for k, v in rest]
            if not data["ai"]:
                data["ai"] = next((v["ai"] for _, v in rest if v.get("ai")), None)
        out.append(data)
    return out


def fits_now(m):
    """Does a stored vacancy still fit the person's CURRENT feed settings (words, stop words,
    ticked channels and sites)? Vacancies were filtered when found; after the settings change
    the feed follows at once. Nothing is deleted: change the settings back and they return."""
    filt = tc.job_filter_from(m.cfg())
    listed = guests.listed_sites()
    names = {name: pid for pid, name in listed.items()}
    sites = set(m.sites(listed))
    pool = guests.load_pool()
    channels = set(m.selected(pool))
    stopped = guests.stop_companies(m)

    def fits(v):
        if v.get("fav"):
            return True
        if stopped and stopped(v):
            return False
        if v.get("source") == "web":
            if v.get("site") in names and names[v["site"]] not in sites:
                return False
            text = " ".join([v.get("title") or "", v.get("company") or "", v.get("location") or ""])
            return (not filt.role or any(p.search(text) for p in filt.role)) and not any(p.search(text) for p in filt.negative)
        chan = re.match(r"https://t\.me/([A-Za-z0-9_]{4,})/\d+", v.get("link") or "")
        if chan and chan.group(1).lower() in {k.lower() for k in pool["channels"]} and chan.group(1).lower() not in {k.lower() for k in channels}:
            return False
        return not filt.role or filt.is_job(v.get("text", ""))
    return fits


def origin_name(v):
    """Where a stored vacancy came from, as the card shows it: the site, or the Telegram chat."""
    return (v.get("site") if v.get("source") == "web" else v.get("chat")) or ""


def excluded_origins(m):
    return set(m.load_state().get("feed_exclude") or [])


def unseen(item, since=None):
    """Not looked at yet. `since` (the start of the person's app visit): what they saw during this visit stays
    in the feed until they come back, so cards don't vanish under their finger."""
    seen = item.get("seen")
    return not seen or (since is not None and seen >= since)


def parse_since(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat() if value else None
    except ValueError:
        return None


def feed_pairs(m, inbox, since=None):
    """The «new» feed: newest first, nothing seen before this visit, the person's time zone and country filters
    applied. -> (pairs, filtered out)"""
    tz, cs = extras.tz_settings(m), extras.country_settings(m)
    fits = fits_now(m)
    new = sorted(((k, v) for k, v in inbox.items() if v["status"] == "new" and not v.get("archived") and unseen(v, since) and fits(v)),
                 key=lambda kv: kv[1]["date"], reverse=True)
    new = [(k, v) for k, v in new if v.get("source") == "web" or not tc.looks_like_article(v.get("text", ""))]
    keep = [(k, v) for k, v in new if extras.tz_fits(v, tz) and extras.country_fits(v, cs)]
    gone = excluded_origins(m)  # «Исключить источник»: whole channels / sites the person does not want in the feed
    if gone:
        keep = [(k, v) for k, v in keep if origin_name(v) not in gone]
    return keep, len(new) - len(keep)


def feed_new_count(m):
    """How many new vacancies the feed shows (what fits today's settings, duplicates merged): the same number everywhere."""
    return len(extras.groups(feed_pairs(m, m.load_inbox())[0]))


def searchable(job):
    return " ".join([job.get("title") or "", job.get("company") or "", job.get("text") or ""])


# ---------------------------------------------------------------- the server

class WebApp:
    def __init__(self, radar):
        self.radar = radar           # the running bot (JobRadar): clients, lock, owner
        self.posts = self.load_posts_cache()  # channel key -> (read at, [job-shaped posts])
        self.posts_refreshing = {}
        self.shown = {}              # person id -> {"keys", "twins"}: what the current search has shown so far
        self.posts_dirty = None
        self.results = {}            # person id -> {key: job} from their last search
        self.seen_at = {}            # person id -> monotonic time «seen_at» was last written
        self.last_search = {}
        self.site_cache = {}         # person id -> (feed file + settings stamp, their site vacancies as stored jobs)
        self.last_feedback = {}
        self.jd_cache = {}
        self.warming = False
        self.runner = None
        self.ai = resume_ai.Claude(radar.cfg.get("claude_exe"))
        log.info("ai: claude at %s", self.ai.exe or "— not found")
        self.ai_jobs = {}            # job id -> {"member", "op", "status", "result"|"error"}
        self.ai_checking = False
        self.roles_task = None       # Claude labelling unknown job titles (roles.py)

    # -- plumbing

    def member(self, request):
        auth = request.headers.get("Authorization", "")
        user = check_init_data(auth[4:] if auth.startswith("tma ") else "", self.radar.cfg["bot_token"])
        if not user:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Откройте приложение через бота в Telegram."}),
                                       content_type="application/json")
        member = self.radar.member_for(user["id"])
        if member is None:  # open access: a newcomer joins on their first visit; a switched-off person is told so
            reg = guests.load_registry()
            member, why = guests.join(reg, SimpleNamespace(id=user["id"], username=user.get("username"),
                                                           first_name=user.get("first_name"), last_name=user.get("last_name")))
            if member is None:
                text = guests.BLOCKED_TEXT if why == "blocked" else "Сейчас нет свободных мест. Загляните позже."
                raise web.HTTPForbidden(text=json.dumps({"error": text, "blocked": why == "blocked"}),
                                        content_type="application/json")
            guests.save_registry(reg)
            log.info("app: new person %s (%s) -> %s", member.name, member.id, member.dir.name)
            joined = getattr(self.radar, "on_joined", None)
            if joined:
                asyncio.create_task(joined(member))
        usage.WHO.set(member.id)  # Claude calls started by this request (and its background jobs) count for this person
        self.touch(member)
        return member

    def touch(self, m):
        """«Был в приложении» for the owner's list of people: written at most every 10 minutes."""
        if m.is_owner:
            return
        t = time.monotonic()
        if t - self.seen_at.get(m.id, -1e9) < 600:
            return
        self.seen_at[m.id] = t
        m.update_state(seen_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def admin(self, request):
        """The owner only — checked here, on the server, for every admin call (the page just hides the block)."""
        m = self.member(request)
        if not m.is_owner:
            raise web.HTTPForbidden(text='{"error": "Только для администратора"}', content_type="application/json")
        return m

    async def api_admin_users(self, request):
        """«Ещё → 👥 Пользователи»: everyone who joined, their AI use by day; switch off / AI / daily limit.
        POST {uid, blocked} | {uid, ai} | {uid, limit: int|null} | {default_limit: int}."""
        self.admin(request)
        reg = guests.load_registry()
        if request.method == "POST":
            data = await self.body(request)
            info = reg["users"].get(str(data.get("uid")))
            if "default_limit" in data:
                try:
                    reg["settings"]["ai_daily"] = max(0, min(guests.AI_DAILY_MAX, int(data["default_limit"])))
                except (TypeError, ValueError):
                    return self.bad("Нужно число")
            elif info is None:
                return self.bad("Нет такого пользователя", 404)
            elif "blocked" in data:
                guests.set_blocked(reg, data["uid"], bool(data["blocked"]))
                self.results.pop(int(data["uid"]), None)
                log.info("admin: %s %s", "switched off" if data["blocked"] else "switched on", data["uid"])
            elif "ai" in data:
                if data["ai"]:
                    info.pop("ai", None)  # on = the default
                else:
                    info["ai"] = False
            elif "limit" in data:
                if data["limit"] is None:
                    info.pop("ai_limit", None)
                else:
                    try:
                        info["ai_limit"] = max(0, min(guests.AI_DAILY_MAX, int(data["limit"])))
                    except (TypeError, ValueError):
                        return self.bad("Нужно число")
            else:
                return self.bad("Не понял, что поменять")
            guests.save_registry(reg)
        days = request.query.get("days", "")
        spent = usage.report(min(90, int(days)) if days.isdigit() else 14)
        empty = {"days": [], "total": {"n": 0, "in": 0, "out": 0}, "today": {"n": 0, "in": 0, "out": 0}, "ops": {}}
        people = []
        for g in guests.everyone(reg):
            people.append({
                "id": g.id, "name": g.info.get("name") or "", "username": g.info.get("username") or "",
                "joined": g.info.get("joined"), "seen": g.load_state().get("seen_at"), "blocked": bool(g.info.get("blocked")),
                "ai": g.info.get("ai") is not False, "limit": g.info.get("ai_limit"),
                "limit_now": guests.ai_limit(g, reg), "usage": spent.get(str(g.id), empty),
                "jobs": len(g.load_inbox()),
            })
        people.sort(key=lambda p: (p["blocked"], -p["usage"]["total"]["n"], p["name"].lower()))
        owner = getattr(self.radar, "owner", None)
        return web.json_response({
            "people": people, "default_limit": guests.ai_default(reg), "max_limit": guests.AI_DAILY_MAX,
            "cap": guests.MAX_MEMBERS,
            "owner": spent.get(str(owner), empty) if owner else empty,
            "system": spent.get(usage.SYSTEM, empty),
        })

    @staticmethod
    async def body(request):
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def app(self):
        app = web.Application(client_max_size=6 * 1024 * 1024, middlewares=[self.errors])  # room for a résumé
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/api/me", self.api_me)
        r.add_get("/api/jobs", self.api_jobs)
        r.add_get("/api/jobs/{key}", self.api_job)
        r.add_post("/api/jobs/{key}/{action}", self.api_job_action)
        r.add_get("/api/search", self.api_search)
        r.add_post("/api/search/{key}/{action}", self.api_search_action)
        r.add_get("/api/roles", self.api_roles)
        r.add_get("/api/source-health", self.api_source_health)
        r.add_post("/api/roles", self.api_roles)
        r.add_get("/api/sources", self.api_sources)
        r.add_post("/api/sources", self.api_sources_change)
        r.add_post("/api/settings", self.api_settings)
        r.add_post("/api/refresh", self.api_refresh)
        r.add_post("/api/score", self.api_score)
        r.add_post("/api/score/new", self.api_score_new)
        r.add_post("/api/apply/prepare", self.api_apply_prepare)
        r.add_post("/api/apply/send", self.api_apply_send)
        r.add_post("/api/apply/done", self.api_apply_done)
        r.add_get("/api/resume/original", self.api_original)
        r.add_post("/api/resume/original/send", self.api_original_send)
        r.add_get("/api/watch", self.api_watch)
        r.add_post("/api/watch", self.api_watch_change)
        r.add_get("/api/track", self.api_track)
        r.add_post("/api/track", self.api_track_set)
        r.add_post("/api/track/add", self.api_track_add)
        r.add_post("/api/feedback", self.api_feedback)
        r.add_post("/api/feed/exclude", self.api_feed_exclude)
        r.add_post("/api/seen", self.api_seen)
        r.add_post("/api/apply/compose", self.api_apply_compose)
        r.add_post("/api/apply/presets", self.api_apply_presets)
        r.add_get("/api/apply/profile", self.api_apply_profile)
        r.add_post("/api/apply/profile", self.api_apply_profile)
        r.add_get("/api/plan", self.api_plan)
        r.add_post("/api/plan", self.api_plan_make)
        r.add_get("/api/cv", self.api_cv)
        r.add_post("/api/cv", self.api_cv_upload)
        r.add_post("/api/cv/apply", self.api_cv_apply)
        r.add_post("/api/cv/delete", self.api_cv_delete)
        r.add_post("/api/cv/from-resume", self.api_cv_from_resume)
        r.add_post("/api/reset", self.api_reset)
        r.add_get("/api/resume", self.api_resume)
        r.add_post("/api/resume/version", self.api_resume_version)
        r.add_post("/api/resume/save", self.api_resume_save)
        r.add_post("/api/resume/new", self.api_resume_new)
        r.add_get("/api/resume/draft", self.api_resume_draft)
        r.add_post("/api/resume/draft", self.api_resume_draft_save)
        r.add_post("/api/resume/build", self.api_resume_build)
        r.add_post("/api/resume/match", self.api_resume_match)
        r.add_post("/api/resume/ai", self.api_resume_ai)
        r.add_get("/api/resume/ai/{job}", self.api_resume_ai_job)
        r.add_post("/api/resume/apply", self.api_resume_apply)
        r.add_post("/api/resume/export", self.api_resume_export)
        r.add_post("/api/resume/design", self.api_resume_design)
        r.add_post("/api/resume/photo", self.api_resume_photo)
        r.add_post("/api/resume/import", self.api_resume_import)
        r.add_get("/api/admin/users", self.api_admin_users)
        r.add_post("/api/admin/users", self.api_admin_users)
        r.add_static("/static/", WEB_DIR, show_index=False)
        return app

    @web.middleware
    async def errors(self, request, handler):
        try:
            response = await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            log.exception("app: %s %s failed", request.method, request.path)
            return web.json_response({"error": "Что-то пошло не так. Подробности в логе бота."}, status=500)
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    async def start(self):
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, HOST, PORT).start()
        log.info("app server on http://%s:%d", HOST, PORT)

    async def index(self, request):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        stamp = str(int(max(p.stat().st_mtime for p in WEB_DIR.iterdir())))  # fresh JS/CSS after an update
        return web.Response(text=html.replace("{{v}}", stamp), content_type="text/html")

    # -- me, list, one vacancy, actions

    def warm(self, m):
        """Read the person's channels in the background as soon as the app opens,
        so their first search doesn't wait for Telegram."""
        if self.warming:
            return
        keys = [k for k in m.selected(guests.load_pool())
                if k not in self.posts or datetime.now(timezone.utc) - self.posts[k][0] >= POSTS_TTL]
        if not keys:
            return
        self.warming = True

        async def run():
            try:
                for key in keys:
                    await self.channel_posts(key, m)
            except Exception:
                log.exception("app: warming channels failed")
            finally:
                self.warming = False
        asyncio.create_task(run())

    def queue_roles(self, jobs):
        """Titles from this search the dictionary can't place go to Claude in the background
        (owner's subscription, roles.DAILY calls a day); the next search knows them."""
        if (self.roles_task and not self.roles_task.done()) or roles.calls_left() <= 0:
            return

        async def run():
            graph, found = load_graph(), 0
            try:  # the first pass over a few thousand titles takes seconds: not in the request
                todo = await asyncio.to_thread(roles.unknown, jobs, lambda title: bool(roles_of_title(title)))
                for i in range(0, min(len(todo), roles.BATCH * roles.PER_RUN), roles.BATCH):
                    found += await roles.classify(self.ai, todo[i:i + roles.BATCH], graph)
                if len(todo) >= roles.MIN_BATCH:
                    log.info("roles: Claude labelled %d of %d unknown titles, %d got a role", min(len(todo), roles.BATCH * roles.PER_RUN), len(todo), found)
                look = await asyncio.to_thread(roles.deep_candidates, jobs)  # unsure ones: read the real description
                if look:
                    n = await roles.deep(self.ai, look, graph)
                    log.info("roles: second look at %d vacancies by their descriptions, %d got a role", len(look), n)
            except Exception as e:
                log.warning("roles: Claude labelling failed: %s", e)
        self.roles_task = asyncio.create_task(run())

    async def api_source_health(self, request):
        """«Ещё → 📡 Радар»: how each source did lately (sourcestats). Owner: every source;
        a guest: only the sites, hh.ru and pool channels they ticked."""
        m = self.member(request)
        ids = None
        if not m.is_owner:
            listed = guests.listed_sites()
            ids = {("hh" if s == guests.HH_ID else "site:" + s) for s in m.sites(listed)}
            ids |= {"tg:" + k for k in m.selected(guests.load_pool())}
        return web.json_response({"items": sourcestats.report(ids),
                                  "filter": filtercheck.report() if m.is_owner else None})

    @staticmethod
    def roles_summary():
        """«Ещё → Радар»: what the role helper spent today and how much it already knows (the pile grows, the spending shrinks)."""
        s = roles.stats()
        graph = load_graph()
        return {"calls": s["calls"], "calls_cap": s["calls_cap"], "deep": s["deep_calls"], "deep_cap": s["deep_cap"],
                "dict": sum(len(r["names"]) + len(r["also"]) for r in graph.values()),
                "cache": s["total"], "checked": s["checked"], "used": s["used"]}

    async def api_roles(self, request):
        """Owner: Claude's labels to check (✓/✗), counts; POST {key, ok} or {reset: true}."""
        m = self.member(request)
        if not m.is_owner:
            raise web.HTTPForbidden(text='{"error": "Только для владельца"}', content_type="application/json")
        if request.method == "POST":
            data = await self.body(request)
            if data.get("reset"):
                roles.reset()
            elif isinstance(data.get("key"), str) and isinstance(data.get("role"), str) and data["role"].strip():
                typed = norm(data["role"]).strip()
                graph = load_graph()
                rid = next((r for r, g in graph.items() if typed in {norm(n).strip() for n in g["names"] + g["also"]}), None)
                roles.own(data["key"], [rid] if rid else [], data["role"].strip())
            elif isinstance(data.get("key"), str):
                roles.review(data["key"], bool(data.get("ok")))
        graph = load_graph()
        items, total = roles.review_list()
        return web.json_response({
            "items": [{"key": v["key"], "title": v["title"], "sure": bool(v.get("sure")), "jd": bool(v.get("jd")),
                       "roles": [graph[r]["names"][0] if r in graph else r for r in v["roles"]]} for v in items],
            "pending": total, "stats": roles.stats(),
            "names": sorted({g["names"][0] for g in graph.values() if g["names"]})})

    async def api_me(self, request):
        m = self.member(request)
        self.warm(m)
        inbox, state, listed = m.load_inbox(), m.load_state(), guests.listed_sites()
        pool = guests.load_pool()
        last = state.get("last_run" if m.is_owner else "pool_run")
        if not state.get("fav_migrated"):  # «в работе» became «Избранное»: what was taken is starred now
            for item in inbox.values():
                if item.get("status") == "taken":
                    item["fav"] = True
            m.save_inbox(inbox)
            m.update_state(fav_migrated=True)
        new_count = len(extras.groups(feed_pairs(m, inbox, parse_since(request.query.get("since")))[0]))  # once: it is the slow part
        return web.json_response({
            "name": "Эдуард" if m.is_owner else (m.info.get("name") or m.name).split(" ")[0],
            "owner": m.is_owner,
            "skin": OLD_SKINS.get(state.get("skin"), state.get("skin")) if OLD_SKINS.get(state.get("skin"), state.get("skin")) in SKINS else None,
            "onboarded": bool(state.get("app_onboarded")),
            "digest": not state.get("digest_off"),
            "alerts": not state.get("alerts_off"),
            "interval": int(self.radar.cfg.get("interval_minutes", 15)),
            "intervals": INTERVALS,
            "ai": resume_ai.allowed(m),
            "roles_ai": self.roles_summary() if m.is_owner else None,
            "watch_new": len(watch.overview(m)[1]),
            "funnel": {**extras.funnel(inbox, jobs=False), "jobs": new_count},  # «вакансий» = the feed's own pool
            "stats": {
                "new": new_count,
                "taken": sum(1 for i in inbox.values() if i["status"] == "taken"),
                "fav": sum(1 for i in inbox.values() if VIEWS["fav"](i)),
                "archived": sum(1 for i in inbox.values() if i.get("archived")),
                "seen": sum(1 for i in inbox.values() if VIEWS["seen"](i)),
                "hidden": sum(1 for i in inbox.values() if i["status"] == "hidden"),
                "channels": len(m.selected(pool)) + (len(tc.load_channels(tc.CHANNELS_PATH)) if m.is_owner else 0),
                "sites": len(m.sites(listed)),
            },
            "checked_at": last,
            "refreshing": bool(getattr(self.radar, "hh_busy", False) or getattr(self.radar, "sites_busy", False)),  # a manual refresh is still reading hh.ru / sites
            "next_check": self.radar.minutes_to_next_check() if m.is_owner else None,
        })

    async def api_jobs(self, request):
        m = self.member(request)
        view = request.query.get("view", "new")
        keep = VIEWS.get(view, VIEWS["new"])
        inbox = m.load_inbox()
        if view == "new" or view not in VIEWS:
            items, tz_hidden = feed_pairs(m, inbox, parse_since(request.query.get("since")))
        elif view == "seen":  # the latest looked at first
            items, tz_hidden = sorted(((k, v) for k, v in inbox.items() if keep(v)), key=lambda kv: kv[1]["seen"], reverse=True), 0
        else:
            items, tz_hidden = sorted(((k, v) for k, v in inbox.items() if keep(v)), key=lambda kv: kv[1]["date"], reverse=True), 0
        try:
            offset = max(0, int(request.query.get("offset", 0)))
            limit = min(PAGE_MAX, max(1, int(request.query.get("limit", 40))))
        except ValueError:
            offset, limit = 0, 40
        pool = items  # the whole feed, before this request's chips: its sources fill «Исключить источник»
        items = apply_filters(items, request.query.get("f", "all"))
        grade = request.query.get("grade", "")
        if grade in dict(extras.GRADES):
            items = [(k, v) for k, v in items if extras.grade_fits(v, grade)]
        age = request.query.get("age", "")
        if age in ("1", "3", "7", "14"):
            since = extras.age_cutoff(m, int(age))
            items = [(k, v) for k, v in items if datetime.fromisoformat(v["date"]) >= since]
        sort = request.query.get("sort", "date")
        ctx = scoring.context(m)
        if sort == "algo":  # «По совпадению слов»: the quick score, best first (newest first among equals)
            items = sorted(items, key=lambda kv: -(scoring.score(ctx, kv[1])["score"] or 0))
            sort = "date"
        page, total = page_json(items, sort, offset, limit)
        for d in page:
            d["algo"] = scoring.score(ctx, inbox.get(d["key"]) or {})
        unscored = sum(1 for g in extras.groups(items) if not any(v.get("ai") for _, v in g)) if view == "new" else 0
        origins = {}
        if view == "new":  # the sources in today's pool, biggest first (for «Исключить источник»)
            for _, v in pool:
                origins[origin_name(v)] = origins.get(origin_name(v), 0) + 1
        return web.json_response({"items": page, "total": total, "offset": offset, "tz_hidden": tz_hidden, "unscored": unscored,
                                  "origins": [{"name": n, "n": c} for n, c in sorted(origins.items(), key=lambda kv: -kv[1]) if n][:40],
                                  "excluded": sorted(excluded_origins(m))})

    async def api_feed_exclude(self, request):
        """{origin, on}: leave a channel / site out of the person's feed (and its counters), or bring it back."""
        m = self.member(request)
        data = await self.body(request)
        name = str(data.get("origin") or "").strip()[:200]
        if not name:
            return self.bad("Не понял, какой источник")
        gone = excluded_origins(m)
        if data.get("on", True):
            if len(gone) >= 30:
                return self.bad("Исключено уже 30 источников")
            gone.add(name)
        else:
            gone.discard(name)
        m.update_state(feed_exclude=sorted(gone))
        return web.json_response({"ok": True, "excluded": sorted(gone)})

    async def api_seen(self, request):
        """{keys}: vacancies the person looked at (a card on top for a while, or opened). Marked once, with the time;
        the same vacancy from other sources goes along. Next visit the feed shows only what is new."""
        m = self.member(request)
        data = await self.body(request)
        keys = [str(k) for k in (data.get("keys") or [])][:200]
        inbox, stamp, n = m.load_inbox(), guests.now().isoformat(), 0
        for key in keys:
            for k in [key] + (extras.twins_of(inbox, key) if key in inbox else []):
                item = inbox.get(k)
                if item and item["status"] == "new" and not item.get("seen"):
                    item["seen"] = stamp
                    n += 1
        if n:
            m.save_inbox(inbox)
        return web.json_response({"ok": True, "marked": n})

    async def api_job(self, request):
        m = self.member(request)
        item = m.load_inbox().get(request.match_info["key"])
        if not item:
            raise web.HTTPNotFound(text='{"error": "Вакансия не найдена"}', content_type="application/json")
        return web.json_response(item_json(request.match_info["key"], item, full=True))

    async def api_job_action(self, request):
        m = self.member(request)
        key, action = request.match_info["key"], request.match_info["action"]
        if action not in ACTIONS:
            raise web.HTTPBadRequest(text='{"error": "Неизвестное действие"}', content_type="application/json")
        inbox = m.load_inbox()
        item = inbox.get(key)
        if not item:
            raise web.HTTPNotFound(text='{"error": "Вакансия не найдена"}', content_type="application/json")
        if action == "unsee":  # «Вернуть в ленту» from «Просмотрено»
            changed, note = bool(item.pop("seen", None)), "Вернул в ленту"
            for twin in extras.twins_of(inbox, key):
                inbox[twin].pop("seen", None)
        else:
            changed, note = guests.job_action(m, key, item, action)
        if changed and action in ("take", "hide", "restore"):  # the same vacancy from other sources goes along
            for twin in extras.twins_of(inbox, key):
                if inbox[twin]["status"] != item["status"] and (action == "restore" or inbox[twin]["status"] == "new"):
                    inbox[twin]["status"] = item["status"]
        if changed:
            m.save_inbox(inbox)
        return web.json_response({"ok": changed, "note": note, "item": item_json(key, item)})

    # -- search

    async def api_search(self, request):
        m = self.member(request)
        query = re.sub(r"\s+", " ", request.query.get("q", "")).strip()[:QUERY_MAX]
        mode = "similar" if request.query.get("mode") == "similar" else "exact"
        off = set(request.query.get("off", "").split(",")) - {""}
        # part: "fast" = what is at hand (feed, cached channels, site feeds) at once; "chan", "hh", "hima" = the slower
        # sources one by one, each sends only what the earlier parts did not; "all" = everything in one answer
        part = request.query.get("part") if request.query.get("part") in ("fast", "chan", "hh", "hima") else "all"
        now_ = time.monotonic()
        if part in ("all", "fast"):
            if now_ - self.last_search.get(m.id, 0) < SEARCH_COOLDOWN:
                await asyncio.sleep(SEARCH_COOLDOWN)
            self.last_search[m.id] = time.monotonic()
        words = load_words()
        if not title_re(query, words):
            return web.json_response({"query": query, "mode": mode, "graph": None, "items": [], "sources": {}})

        graph, view = load_graph(), None
        terms = [("exact", "точно", title_re(query, words))]
        rid = find_role(graph, query)
        by_role = {}  # role id -> (kind, label) for Claude's labels: the role itself and, in «similar», its neighbours
        near = {}     # neighbour role id -> (kind, label), «similar» only
        if rid:  # the role's own synonyms count in both modes: «аналитик данных» = «data analyst»
            nodes = graph_view(graph, rid, query)
            if mode == "similar":
                view = nodes
            for node in nodes["nodes"]:
                if node["id"] in off or (mode == "exact" and node["kind"] != "syn"):
                    continue
                label = {"syn": "синоним", "adj": "смежная", "far": "дальняя"}[node["kind"]] + ": " + node["label"]
                for name in node["names"]:
                    rx = title_re(name, words) if len(name.strip()) > 3 else None  # «BA», «PM»: too noisy
                    if rx:
                        terms.append((node["kind"], label, rx))
                if node["kind"] != "syn":
                    by_role[node["id"]] = (node["kind"], "🤖 " + label)
                    near[node["id"]] = (node["kind"], label)
            # the role's other job titles (`also`: Chargeback Analyst, Trust & Safety…) rank like synonyms
            terms[1:1] = [("role", "роль: " + name, rx) for r, name, rx, _ in role_matchers()
                          if r == rid and name in graph[rid]["also"]]
            by_role[rid] = ("ai", "🤖 по смыслу: " + graph[rid]["names"][0])

        qt, me = tight_re(query, words), rid or "_q"
        other_job = (re.compile(r"(?<![\w])(?:" + "|".join(word_pat(w) for w in graph[rid]["not"]) + r")(?![\w])", re.IGNORECASE)
                     if rid and graph[rid]["not"] else None)  # «Trust & Safety Product Manager» isn't a fraud analyst

        def refine(hit, title):
            """Words scattered over a title aren't enough: what stands side by side counts, and the
            role named first in the title's head is what the job is (when the query is a role):
            «Data Scientist, Product Analytics» is not a data analyst, «Data Analyst / DWH Analyst» is both."""
            if hit[0] in ("text", "ai"):
                return hit
            found = title_roles(title)
            q = qt.search(title) if qt else None
            if q:
                found = sorted(found + [(q.start(), q.end(), me, query)])
            cut = PART_END.search(title)
            end = cut.start() if cut else len(title)
            head = [f for f in found if f[0] < end]  # «Program Coordinator - Data Analytics»: the head has no data analyst
            if head:  # at the first spot the longest name wins: «Data Analytics Engineer» is an analytics engineer
                first = [f for f in head if f[0] == head[0][0]]
                longest = max(f[1] for f in first)
                lead = {f[2] for f in first if f[1] == longest}
            else:
                lead = set()
            neighbour = next((near[r] for r in lead if r in near), None)
            if hit[0] in ("adj", "far"):  # a neighbour role only when that's what the title is
                return neighbour
            if rid and head:
                mine = next((f for f in head if f[2] == me), None)
                if mine is None or (me not in lead and not re.search(r"/|\bor\b|\bили\b", title[head[0][1]:mine[0]])):
                    return neighbour
            if q and q.start() < end:
                return ("exact", "точно")
            if other_job and other_job.search(title):
                return neighbour
            own = next((f[3] for f in head if f[2] == rid), None) if rid else None
            if own:
                return ("syn", "синоним: " + own) if own in graph[rid]["names"] else ("role", "роль: " + own)
            return ("loose", "слова вразброс")

        jobs, counts = await self.gather(m, query, terms, mode, part)
        hits, rank = [], {"exact": 0, "syn": 1, "role": 1, "ai": 1.5, "loose": 1.8, "adj": 2, "far": 3, "text": 4}
        inbox, labels = m.load_inbox(), roles.load()
        anyterm = tc.AnyOf(rx for _, _, rx in terms)  # one pass rules out most titles; the ordered loop runs on the rest
        for job in jobs:
            title = norm(job.get("title"))
            hit = (next(((kind, label) for kind, label, rx in terms if rx.search(title)), None)  # closest kind wins
                   if anyterm.search(title) else None)
            if hit:
                hit = refine(hit, title)
            if hit is None and by_role:  # Claude's label for a title the dictionary doesn't know
                ids, checked = roles.label_of(job, labels)
                hit = next((by_role[r] for r in ids if r in by_role), None)
                if hit and checked:
                    hit = ("role" if hit[0] == "ai" else hit[0], hit[1].replace("🤖 ", "✓ "))
            if hit is None and job.get("source") == "tg" and terms[0][2].search(norm(searchable(job))[:TEXT_HEAD]):
                # the phrase is only somewhere in a post (often a digest of several jobs): last, labelled
                hit = ("text", "в тексте поста")
            if hit:
                hits.append((rank[hit[0]], job, *hit))
        self.queue_roles(jobs)
        hits.sort(key=lambda h: (h[0], -h[1]["date"].timestamp()))
        if mode == "similar":  # a popular role fills the list by itself: keep room for its neighbours
            core = [h for h in hits if h[0] < 2]
            if len(core) > RESULTS_MAX - NEAR_SLOTS:
                rest = [h for h in hits if h[0] >= 2]
                hits = core[:RESULTS_MAX - NEAR_SLOTS] + rest[:NEAR_SLOTS] + core[RESULTS_MAX - NEAR_SLOTS:]
        later = part in ("chan", "hh", "hima")
        shown = self.shown.get(m.id, {"keys": set(), "twins": set()}) if later else {"keys": set(), "twins": set()}
        keep, items, same = {}, [], set(shown["twins"])
        cs, stopped = extras.country_settings(m), guests.stop_companies(m)
        for _, job, kind, label in hits:
            if job["key"] in shown["keys"] or not extras.country_fits(job, cs) or (stopped and stopped(job)):
                continue
            # one vacancy posted on several sites: show it once (no company: the same title on the same site)
            twin = (norm(job.get("title")).strip(), norm(job.get("company")).strip() or "@" + norm(job.get("site") or job.get("chat")))
            if twin in same and twin[1] != "@":
                continue
            same.add(twin)
            if len(items) >= RESULTS_MAX:
                break
            key = job["key"]
            keep[key] = job
            data = item_json(key, dict(job, status=inbox.get(key, {}).get("status", "found"),
                                       fav=inbox.get(key, {}).get("fav", False)))
            data["match"] = {"kind": kind, "label": label}
            items.append(data)
        if later:
            self.results.setdefault(m.id, {}).update(keep)
        else:
            self.results[m.id] = keep
        self.shown[m.id] = {"keys": shown["keys"] | set(keep), "twins": same}
        log.info("search for %s: %r %s %s -> %d", m.name, query, mode, part, len(items))
        return web.json_response({"query": query, "mode": mode, "part": part, "graph": view, "items": items, "sources": counts,
                                  "no_role": mode == "similar" and view is None})

    async def gather(self, m, query, terms, mode, part="all"):
        """Candidate vacancies from everything this person reads: their inbox, their channels
        (last SEARCH_DAYS days), cached site feeds and a live hh.ru query."""
        jobs, seen = [], set()

        def add(job, where):
            if job["key"] in seen:
                return
            seen.add(job["key"])
            jobs.append(job)
            counts[where] = counts.get(where, 0) + 1

        counts = {}
        if part in ("all", "fast"):
            for key, item in m.load_inbox().items():
                add(dict(item, key=key, date=datetime.fromisoformat(item["date"])), "inbox")
        gate = asyncio.Semaphore(4)  # channels are read side by side, a few at a time
        flt = tc.job_filter_from(m.cfg())

        async def read(key):
            async with gate:
                return await self.channel_posts(key, m, wait=part != "fast", flt=flt)
        t0 = time.monotonic()
        hh_task = asyncio.create_task(self.hh_search(m, query, terms)) if part in ("all", "hh") else None  # hh.ru and Himalayas go meanwhile
        hima_task = (asyncio.create_task(self.himalayas_search(m, query))
                     if part in ("all", "hima") and "himalayas" in m.sites(guests.listed_sites()) else None)
        chan_tasks = [asyncio.ensure_future(read(k)) for k in m.selected(guests.load_pool())] if part in ("all", "fast", "chan") else []
        if chan_tasks:  # whatever is read within SEARCH_WAIT counts; slow channels finish in the background for next time
            await asyncio.wait(chan_tasks, timeout=SEARCH_WAIT)
        for task in chan_tasks:
            if task.done() and not task.cancelled() and not task.exception():
                for post in task.result():
                    add(post, "channels")
        t1 = time.monotonic()
        tagger = guests.tagger_for(m)
        listed = guests.listed_sites()
        if part in ("all", "fast"):
            for job in self.site_jobs(m, listed, tagger):
                add(job, "sites")
        t2 = time.monotonic()
        if hima_task:  # its cached feed is only the newest few hundred of ~100k
            for j in await self.within(hima_task, 30 if part == "hima" else None):
                add(guests.web_job(j, listed.get("himalayas", "Himalayas"), m, tagger), "sites")
        t3 = time.monotonic()
        for j in await self.within(hh_task, 30 if part == "hh" else None) if hh_task else []:
            add(guests.web_job(j, guests.HH_NAME, m, tagger), "hh")
        log.info("search timing: channels %.1fs, site feeds %.1fs, himalayas wait %.1fs, hh wait %.1fs, %d candidates",
                 t1 - t0, t2 - t1, t3 - t2, time.monotonic() - t3, len(jobs))
        return jobs, counts

    def site_jobs(self, m, listed, tagger):
        """The person's ticked site feeds as stored jobs. Built once per version of the feed file and of their
        settings (a search used to rebuild ~19k of them every time); nothing downstream changes these dicts."""
        try:
            mtime = FEED_CACHE_PATH.stat().st_mtime
        except OSError:
            mtime = None
        sites = m.sites(listed)
        stamp = (mtime, json.dumps([sites, listed, m.cfg().get("keywords", {}).get("tags")], sort_keys=True, default=str))
        hit = self.site_cache.get(m.id)
        if hit and hit[0] == stamp:
            return hit[1]
        feed = guests.read_json(FEED_CACHE_PATH, {}).get("feed", {})
        jobs = [guests.web_job(j, listed.get(provider, provider), m, tagger)
                for provider in sites for j in (feed.get(provider) or {}).get("jobs", []) if j.get("url")]
        self.site_cache[m.id] = (stamp, jobs)
        return jobs

    async def within(self, task, seconds=None):
        """A live source (hh.ru, Himalayas) gets a few seconds; if it is slower the search goes on without it
        (a search that waits over 100 s dies with Cloudflare's error 524)."""
        done, _ = await asyncio.wait([task], timeout=seconds or SEARCH_LIVE_WAIT)
        return task.result() if done and not task.exception() else []

    async def himalayas_search(self, m, query):
        try:
            cs = extras.country_settings(m)
            return await guests.fetch_himalayas(hh_variants(query), cs["key"] if cs["on"] else "")
        except Exception as e:
            log.warning("search himalayas: %s", e)
            return []

    async def hh_search(self, m, query, terms):
        try:
            hh = guests.hh_settings(m)
            # hh.ru gets the phrase (words in any order, in the title) and its English twin, plus
            # synonyms (and close roles in «similar»); one request per variant, 20 hits each
            names = hh_variants(query) + [label.split(": ", 1)[1] for kind, label, _ in terms[1:] if kind in ("syn", "adj")]
            q = " OR ".join(dict.fromkeys(n for n in names if n.strip()))[:guests.HH_QUERY_MAX]
            return await guests.fetch_hh(q, hh["remote"])
        except Exception as e:
            log.warning("search hh.ru: %s", e)
            return []

    def load_posts_cache(self):
        out = {}
        try:
            raw = json.loads(POSTS_CACHE_PATH.read_text(encoding="utf-8")) if POSTS_CACHE_PATH.exists() else {}
            for key, entry in raw.items():
                at = datetime.fromisoformat(entry["at"])
                if datetime.now(timezone.utc) - at < POSTS_STALE_MAX:
                    out[key] = (at, [dict(p, date=datetime.fromisoformat(p["date"])) for p in entry["posts"]])
        except Exception as e:
            log.warning("posts cache not loaded: %s", e)
        return out

    def save_posts_cache(self):
        """Written a few seconds after the last channel read (not after every channel)."""
        self.posts_dirty = None
        try:
            data = {k: {"at": at.isoformat(), "posts": [dict(p, date=p["date"].isoformat()) for p in posts]}
                    for k, (at, posts) in self.posts.items()}
            guests.write_json(POSTS_CACHE_PATH, data)
        except Exception as e:
            log.warning("posts cache not saved: %s", e)

    async def read_channel(self, key):
        """Read one channel from Telegram into the cache; the old copy stays if it fails."""
        posts = []
        try:
            entity = await self.radar.user.get_entity(key)
            cutoff = datetime.now(timezone.utc) - timedelta(days=SEARCH_DAYS)
            title = getattr(entity, "title", None) or "@" + key
            peer = utils.get_peer_id(entity)  # same key as the feed uses, so no duplicates
            async for msg in self.radar.user.iter_messages(entity, limit=POSTS_PER_CHANNEL):
                if msg.date < cutoff:
                    break
                if msg.message and len(msg.message) >= 80:
                    posts.append({"source": "tg", "key": f"{abs(peer)}_{msg.id}", "chat": title,
                                  "chat_id": peer, "msg_id": msg.id, "date": msg.date,
                                  "link": f"https://t.me/{key}/{msg.id}", "text": msg.message,
                                  "title": tc.guess_title(msg.message)})
            await asyncio.sleep(0.3)
        except Exception as e:
            log.warning("search: channel @%s: %s", key, e)
            return
        self.posts[key] = (datetime.now(timezone.utc), posts)
        if self.posts_dirty is None:
            self.posts_dirty = asyncio.get_running_loop().call_later(20, self.save_posts_cache)

    async def refresh_channel(self, key):
        """One read per channel at a time; a second caller waits for the first one's result."""
        task = self.posts_refreshing.get(key)
        if task is None:
            task = self.posts_refreshing[key] = asyncio.ensure_future(self.read_channel(key))
            task.add_done_callback(lambda _: self.posts_refreshing.pop(key, None))
        await asyncio.shield(task)

    async def channel_posts(self, key, member, wait=True, flt=None):
        """Job posts of one public channel for the last SEARCH_DAYS days. A copy kept on disk is
        shown at once; if it is older than POSTS_TTL it is refreshed in the background."""
        hit = self.posts.get(key)
        if hit and datetime.now(timezone.utc) - hit[0] >= POSTS_TTL:
            asyncio.create_task(self.refresh_channel(key))
        elif not hit:
            if wait:
                await self.refresh_channel(key)
                hit = self.posts.get(key)
            else:  # the quick part of a search does not wait for Telegram: the channel is read for the next part
                asyncio.create_task(self.refresh_channel(key))
        posts = hit[1] if hit else []
        flt = flt or tc.job_filter_from(member.cfg())  # a search passes it in: reading search.yml per channel cost seconds
        out = []
        for p in posts:  # a job post by its own words (вакансия, hiring…), no stop words
            if flt.is_job_like(p["text"]):
                out.append(dict(p, tags=flt.tags_for(p["text"])))
        return out

    async def api_search_action(self, request):
        m = self.member(request)
        key, action = request.match_info["key"], request.match_info["action"]
        job = self.results.get(m.id, {}).get(key)
        if action not in ACTIONS or job is None:
            raise web.HTTPNotFound(text='{"error": "Результат устарел, повторите поиск"}', content_type="application/json")
        inbox = m.load_inbox()
        if key not in inbox:
            inbox[key] = guests.to_item(job)
            inbox[key]["via"] = "search"
        changed, note = guests.job_action(m, key, inbox[key], action)
        m.save_inbox(inbox)
        return web.json_response({"ok": changed, "note": note, "item": item_json(key, inbox[key])})

    # -- sources and settings

    async def api_sources(self, request):
        m = self.member(request)
        pool, listed = guests.load_pool(), guests.listed_sites()
        ticked, mine = set(m.selected(pool)), set(m.sites(listed))
        kw = m.cfg().get("keywords", {})
        return web.json_response({
            "channels": [{"key": k, "title": c.get("title") or k, "on": k in ticked,
                          "removable": guests.can_remove(pool, k, m)}
                         for k, c in sorted(pool["channels"].items(), key=lambda kv: kv[1].get("added", ""))],
            "max_pool": guests.MAX_POOL,
            "adds_left": guests.pool_adds_left(pool, m),
            "sites": [{"id": pid, "name": name, "on": pid in mine} for pid, name in listed.items()],
            "hh": guests.hh_settings(m),
            "words": {g: [guests.plain(p) for p in kw.get(g, [])] for g in ("role", "negative", "companies")},
            "tz": extras.tz_settings(m),
            "country": extras.country_settings(m),
            "countries": [{"key": k, "name": v[0]} for k, v in extras.COUNTRIES.items()],
        })

    async def api_sources_change(self, request):
        m = self.member(request)
        data = await self.body(request)
        kind, note = data.get("type"), None
        pool, listed = guests.load_pool(), guests.listed_sites()
        if kind == "channel" and data.get("key") in pool["channels"]:
            ticked = [k for k in m.selected(pool) if k != data["key"]]
            m.set_selected(ticked + [data["key"]] if data.get("on") else ticked)
        elif kind == "channel_add":
            added, refused = await guests.add_to_pool(self.radar.user, pool, m, str(data.get("text", ""))[:guests.MAX_INPUT])
            note = "; ".join([f"Добавил: {', '.join('@' + a for a in added)}"] if added else []
                             + [f"{w} — {why}" for w, why in refused])
        elif kind == "channel_remove" and guests.can_remove(pool, data.get("key"), m):
            guests.remove_from_pool(pool, data["key"])
        elif kind == "channels_all":  # «Включить все» / «Выключить все»
            m.set_selected(list(pool["channels"]) if data.get("on") else [])
        elif kind == "sites_all":
            on = bool(data.get("on"))
            ids = list(listed)
            if m.is_owner:
                for pid in ids:
                    if not guests.is_own_site(pid):
                        guests.set_board_enabled(pid, on)
            m.set_sites(ids if on else [])
            if on and not guests.hh_settings(m)["query"]:
                guests.set_hh(m, query=guests.default_hh_query(m))
        elif kind == "site" and data.get("id") in listed:
            pid, on = data["id"], bool(data.get("on"))
            mine = m.sites(listed)
            if m.is_owner and not guests.is_own_site(pid):
                guests.set_board_enabled(pid, on)
            else:
                m.set_sites(mine + [pid] if on and pid not in mine else [s for s in mine if s != pid or on])
            if pid == guests.HH_ID and on and not guests.hh_settings(m)["query"]:
                guests.set_hh(m, query=guests.default_hh_query(m))
        elif kind == "hh":
            fields = {}
            if "query" in data:
                fields["query"] = str(data["query"])
            if "remote" in data:
                fields["remote"] = bool(data["remote"])
            guests.set_hh(m, **fields)
        elif kind == "tz":
            try:
                extras.set_tz(m, **{k: data[k] for k in ("on", "mine", "spread") if k in data})
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text='{"error": "Не понял часовой пояс"}', content_type="application/json")
        elif kind == "country":
            try:
                extras.set_country(m, **{k: data[k] for k in ("on", "key") if k in data})
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text='{"error": "Не знаю такую страну"}', content_type="application/json")
        elif kind == "words" and data.get("group") in ("role", "negative", "companies"):
            search = m.load_search()
            if data.get("add"):
                added, refused = guests.add_words(search, data["group"], str(data["add"]))
                note = "; ".join(f"{w} — {why}" for w, why in refused) or None
            elif data.get("clear"):  # «Сбросить»: the whole group is emptied (the app asks twice)
                search.setdefault("keywords", {})[data["group"]] = []
            elif isinstance(data.get("remove"), int):
                words = search.get("keywords", {}).get(data["group"], [])
                if 0 <= data["remove"] < len(words):
                    words.pop(data["remove"])
            m.save_search(search)
            if m.is_owner:
                self.radar.reload()
        else:
            raise web.HTTPBadRequest(text='{"error": "Не понял, что поменять"}', content_type="application/json")
        response = await self.api_sources(request)
        if note:
            payload = json.loads(response.text)
            payload["note"] = note
            return web.json_response(payload)
        return response

    async def api_settings(self, request):
        m = self.member(request)
        data = await self.body(request)
        fields = {}
        if data.get("skin") in SKINS:
            fields["skin"] = data["skin"]
        if data.get("onboarded"):
            fields["app_onboarded"] = True
        if "digest" in data:
            fields["digest_off"] = not data["digest"]
        if "alerts" in data:
            fields["alerts_off"] = not data["alerts"]
        if m.is_owner and data.get("interval") in INTERVALS:  # how often the radar looks: the owner's call, for everyone
            search = m.load_search()
            search["interval_minutes"] = data["interval"]
            m.save_search(search)
            self.radar.reload()
        if fields:
            m.update_state(**fields)
        return web.json_response({"ok": True, **fields})

    # -- résumé -> preset (cv_reader.py)

    def cv_payload(self, m):
        graph, pool = load_graph(), guests.load_pool()
        info = cv_reader.refresh(m, graph, pool)
        if not info:
            return {"cv": None}
        analysis = dict(info.get("analysis") or {})
        analysis["roles"] = [dict(r, words=cv_reader.role_words(graph, [r["id"]]), hh=cv_reader.hh_query(graph, [r["id"]]))
                             for r in analysis.get("roles", []) if r["id"] in graph or r["id"].startswith(cv_reader.OWN)]
        ticked, suggested = set(m.selected(pool)), set(analysis.get("channels", []))
        analysis["channels"] = [{"key": k, "title": c.get("title") or k, "on": k in ticked, "suggested": k in suggested}
                                for k, c in pool["channels"].items() if k in suggested or k in ticked]
        return {"cv": {"file": info.get("file"), "at": info.get("at"), **analysis}}

    async def api_cv(self, request):
        return web.json_response(self.cv_payload(self.member(request)))

    async def api_cv_upload(self, request):
        m = self.member(request)
        try:
            form = await request.post()
        except Exception:
            raise web.HTTPBadRequest(text='{"error": "Файл не дошёл, попробуйте ещё раз"}', content_type="application/json")
        field = form.get("file")
        if not hasattr(field, "file"):
            raise web.HTTPBadRequest(text='{"error": "Выберите файл с резюме"}', content_type="application/json")
        data = field.file.read(cv_reader.MAX_BYTES + 1)
        try:
            text = await asyncio.get_running_loop().run_in_executor(None, cv_reader.extract_text, field.filename, data)
            extras.save_original(m, field.filename, data, text)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        analysis = cv_reader.analyze(text, load_graph(), guests.load_pool())
        cv_reader.save(m, field.filename, text, analysis)
        try:  # the uploaded résumé also becomes an editable version in the «Резюме» tab
            first = not resume.load_index(m)["list"]
            resume.save_version(m, resume.from_text(resume.clean_export(text)[0]),
                                name=resume.unique_name(m, "Основное" if first else "Загруженное"))
        except ValueError as e:
            log.info("cv for %s: not kept as a version: %s", m.name, e)
        log.info("cv for %s: %d chars, roles %s", m.name, len(text), [r["id"] for r in analysis["roles"] if r["primary"]])
        return web.json_response(self.cv_payload(m))

    async def api_cv_apply(self, request):
        m = self.member(request)
        data = await self.body(request)
        roles = [str(r) for r in data.get("roles") or []][:10]
        channels = [str(c) for c in data.get("channels") or []][:guests.MAX_POOL] if "channels" in data else None
        try:
            result = cv_reader.apply(m, load_graph(), roles, channels, data.get("mode") if data.get("mode") in ("replace", "sync") else "merge",
                                     skill_words=bool(data.get("skill_words", True)))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        if m.is_owner:
            self.radar.reload()
        log.info("cv preset applied for %s: %s", m.name, result)
        return web.json_response({"ok": True, **result})

    async def api_cv_from_resume(self, request):
        """Search-preset analysis of a résumé version (built or pasted in the app, no file)."""
        m = self.member(request)
        data = await self.body(request)
        try:
            meta, md = resume.get_version(m, data.get("vid"))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        text = re.sub(r"^#+\s*|\*\*", "", md, flags=re.MULTILINE)
        cv_reader.save(m, meta["name"], text, cv_reader.analyze(text, load_graph(), guests.load_pool()))
        return web.json_response(self.cv_payload(m))

    async def api_cv_delete(self, request):
        m = self.member(request)
        cv_reader.forget(m)
        return web.json_response({"cv": None})

    # -- résumé workshop (resume.py rules, resume_ai.py proposals)

    def ai_state(self):
        """The cached AI status right away; a stale one is re-checked in the background."""
        at, value = self.ai.checked
        if (not at or time.monotonic() - at > resume_ai.STATUS_TTL) and not self.ai_checking:
            self.ai_checking = True

            async def check():
                try:
                    self.ai.checked = (0.0, value)
                    await self.ai.status()
                except Exception:
                    log.exception("ai status check failed")
                finally:
                    self.ai_checking = False
            asyncio.create_task(check())
        return value

    def resume_payload(self, m):
        data = resume.overview(m)
        status = self.ai_state()
        data["ai"] = {"allowed": resume_ai.allowed(m), "ready": status is None, "status": status or "ready",
                      "left": resume_ai.uses_left(m), "owner": m.is_owner,
                      "why": None if status is None else resume_ai.ERRORS.get(status, "Проверяю, подключён ли ИИ…")}
        data["cv"] = self.cv_payload(m)["cv"]
        return data

    async def api_resume(self, request):
        return web.json_response(self.resume_payload(self.member(request)))

    @staticmethod
    def bad(text, status=400):
        return web.json_response({"error": text}, status=status)

    async def api_resume_version(self, request):
        m = self.member(request)
        data = await self.body(request)
        action, vid = data.get("action"), str(data.get("vid") or "")
        try:
            if action == "select":
                resume.set_active(m, vid)
            elif action == "rename":
                resume.rename_version(m, vid, str(data.get("name") or ""))
            elif action == "delete":
                resume.delete_version(m, vid)
            elif action == "duplicate":
                meta, md = resume.get_version(m, vid)
                new = resume.save_version(m, md, name=resume.unique_name(m, meta["name"] + " (копия)"))
                resume.set_design(m, new["id"], **resume.design_of(meta))
            else:
                return self.bad("Не понял, что сделать с версией")
        except ValueError as e:
            return self.bad(str(e))
        return web.json_response(self.resume_payload(m))

    async def api_resume_save(self, request):
        m = self.member(request)
        data = await self.body(request)
        md = data.get("markdown")
        if md is None and isinstance(data.get("blocks"), list):
            md = resume.markdown_of(data["blocks"][:600])
        if not isinstance(md, str) or not md.strip():
            return self.bad("Пустое резюме не сохраняю")
        try:
            resume.get_version(m, data.get("vid"))
            resume.save_version(m, md, vid=data.get("vid"))
        except ValueError as e:
            return self.bad(str(e))
        return web.json_response(self.resume_payload(m))

    async def api_resume_new(self, request):
        """A new version from pasted text (or an empty template)."""
        m = self.member(request)
        data = await self.body(request)
        text = str(data.get("text") or "")[:resume.MAX_MD]
        if data.get("mode") == "text" and len(text.strip()) < 100:
            return self.bad("Вставьте текст резюме целиком: сейчас слишком мало текста.")
        md = resume.from_text(text) if text.strip() else resume.build({"lang": data.get("lang", "ru")})
        try:
            resume.save_version(m, md, name=resume.unique_name(m, str(data.get("name") or "Основное")[:40]))
        except ValueError as e:
            return self.bad(str(e))
        return web.json_response(self.resume_payload(m))

    async def api_resume_draft(self, request):
        m = self.member(request)
        draft = m.load_state().get("resume_draft") or {}
        title = request.query.get("title") or draft.get("title") or ""
        return web.json_response({"draft": draft, "skills": resume.skill_suggestions(title),
                                  "summary": resume.suggest_summary(draft) if draft else ""})

    async def api_resume_draft_save(self, request):
        m = self.member(request)
        draft = (await self.body(request)).get("draft")
        if not isinstance(draft, dict) or len(json.dumps(draft, ensure_ascii=False)) > 40000:
            return self.bad("Черновик не сохранился")
        m.update_state(resume_draft=draft)
        return web.json_response({"ok": True, "summary": resume.suggest_summary(draft),
                                  "skills": resume.skill_suggestions(draft.get("title") or "")})

    async def api_resume_build(self, request):
        m = self.member(request)
        data = await self.body(request)
        draft = data.get("draft") if isinstance(data.get("draft"), dict) else m.load_state().get("resume_draft") or {}
        md = resume.build(draft)
        try:
            meta = resume.save_version(m, md, name=resume.unique_name(m, str(data.get("name") or "Основное")[:40]),
                                       lang="en" if draft.get("lang") == "en" else "ru")
        except ValueError as e:
            return self.bad(str(e))
        m.update_state(resume_draft=None)
        log.info("resume built for %s: %s", m.name, meta["id"])
        return web.json_response(self.resume_payload(m))

    async def vacancy_text(self, m, data, fetch_seconds=40):
        """The vacancy to match or tailor to: pasted text, or one of the person's vacancies by key
        (a Telegram post's text; for a site, the full text from the ATS when career-ops can fetch it)."""
        text = str(data.get("vacancy") or "").strip()
        if len(text) >= 80:
            return text[:12000]
        key = str(data.get("job_key") or "")
        item = m.load_inbox().get(key) or self.results.get(m.id, {}).get(key)
        if not item:
            raise ValueError("Вставьте текст вакансии (хотя бы пару абзацев) или выберите вакансию из своих.")
        if item.get("source") == "tg" and len(item.get("text") or "") >= 80:
            return item["text"][:12000]
        if item.get("link"):
            try:
                proc = await asyncio.create_subprocess_exec("node", "fetch-jd.mjs", item["link"], cwd=str(tc.ROOT),
                                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), fetch_seconds)
                if proc.returncode == 0 and len(out.strip()) >= 80:
                    return out.decode("utf-8", "replace")[:12000]
            except Exception:
                try:
                    proc.kill()  # a page that did not answer in time must not keep a process running
                except Exception:
                    pass
        raise ValueError("Текст этой вакансии не подтянулся. Откройте её по ссылке, скопируйте описание и вставьте сюда.")

    async def api_resume_match(self, request):
        m = self.member(request)
        data = await self.body(request)
        try:
            _, md = resume.get_version(m, data.get("vid"))
            vacancy = await self.vacancy_text(m, data)
        except ValueError as e:
            return self.bad(str(e))
        return web.json_response({**resume.match(md, vacancy), "vacancy": vacancy[:3000]})

    async def api_resume_ai(self, request):
        m = self.member(request)
        data = await self.body(request)
        op = data.get("op")
        if op not in resume_ai.OPS:
            return self.bad("Не знаю такой помощи")
        if not resume_ai.allowed(m):
            return self.bad("ИИ для вашего аккаунта отключён администратором.", 403)
        if resume_ai.uses_left(m) <= 0:
            return self.bad("ИИ на сегодня всё: дневной лимит исчерпан. Завтра снова можно.", 429)
        if any(j["member"] == m.id and j["status"] == "running" for j in self.ai_jobs.values()):
            return self.bad("ИИ ещё работает над прошлым запросом — подождите его.", 409)
        try:
            meta, md = resume.get_version(m, data.get("vid"))
            vacancy = await self.vacancy_text(m, data) if op in ("tailor", "cover") else ""
        except ValueError as e:
            return self.bad(str(e))
        for jid in [j for j, v in self.ai_jobs.items() if time.time() - v["started"] > 3600]:
            del self.ai_jobs[jid]
        jid = secrets.token_hex(6)
        self.ai_jobs[jid] = {"member": m.id, "op": op, "vid": meta["id"], "status": "running", "started": time.time()}
        asyncio.create_task(self.ai_job(jid, m, op, md, vacancy, data))
        log.info("ai %s for %s started (%s)", op, m.name, jid)
        return web.json_response({"job": jid, "queued": self.ai.lock.locked()})

    async def ai_job(self, jid, m, op, md, vacancy, data):
        job = self.ai_jobs[jid]
        try:
            result = await resume_ai.run(self.ai, op, md, vacancy, data.get("lang") if data.get("lang") in ("ru", "en") else None,
                                         str(data.get("focus") or "")[:300])
            if op == "translate":
                name = resume.unique_name(m, "English" if result["lang"] == "en" else "Русский")
                new = resume.save_version(m, result["markdown"], name=name, lang=result["lang"], make_active=False)
                resume.set_design(m, new["id"], **resume.design_of(resume.get_version(m, job["vid"])[0]))
                result = {"vid": new["id"], "name": new["name"], "problems": result["problems"]}
            resume_ai.count_use(m)
            job.update(status="done", result=result)
            self.ai.checked = (time.monotonic(), None)
        except RuntimeError as e:
            if str(e) in ("login", "missing"):
                self.ai.checked = (time.monotonic(), str(e))
            job.update(status="error", error=resume_ai.human_error(e))
            log.warning("ai %s for %s failed: %s", op, m.name, e)
        except ValueError as e:
            job.update(status="error", error=str(e))
        except Exception:
            log.exception("ai %s for %s crashed", op, m.name)
            job.update(status="error", error="Что-то пошло не так. Подробности в логе бота.")

    async def api_resume_ai_job(self, request):
        m = self.member(request)
        job = self.ai_jobs.get(request.match_info["job"])
        if not job or job["member"] != m.id:
            return self.bad("Задача не найдена", 404)
        return web.json_response({k: v for k, v in job.items() if k != "member"})

    async def api_resume_apply(self, request):
        """Accepted proposals: line rewrites by index (checked against the text they replace)
        and/or a new «О себе»."""
        m = self.member(request)
        data = await self.body(request)
        try:
            meta, md = resume.get_version(m, data.get("vid"))
        except ValueError as e:
            return self.bad(str(e))
        bl = resume.blocks(md)
        applied = 0
        for c in (data.get("changes") or [])[:40]:
            i, before, after = c.get("index"), str(c.get("before", "")), str(c.get("after", "")).strip()
            if not after:
                continue
            if not (isinstance(i, int) and 0 <= i < len(bl) and resume_ai.norm_line(bl[i]["x"]) == resume_ai.norm_line(before)):
                i = next((k for k, b in enumerate(bl) if resume_ai.norm_line(b["x"]) == resume_ai.norm_line(before)), None)
            if i is not None:
                bl[i]["x"] = after
                applied += 1
        summary = str(data.get("summary") or "").strip()
        if summary:
            bl = resume.with_summary(bl, summary)
            applied += 1
        resume.save_version(m, resume.markdown_of(bl), vid=meta["id"])
        return web.json_response({"applied": applied, **self.resume_payload(m)})

    async def api_resume_import(self, request):
        """A file exported from hh.ru / LinkedIn (or any résumé file) -> a new version."""
        m = self.member(request)
        try:
            form = await request.post()
        except Exception:
            return self.bad("Файл не дошёл, попробуйте ещё раз")
        field = form.get("file")
        if not hasattr(field, "file"):
            return self.bad("Выберите файл")
        data = field.file.read(cv_reader.MAX_BYTES + 1)
        if len(data) > cv_reader.MAX_BYTES:
            return self.bad("Файл больше 5 МБ. Для LinkedIn выберите в архиве только профиль (быстрый вариант).")
        try:
            md, source = await asyncio.get_running_loop().run_in_executor(None, resume.import_file, field.filename, data)
            try:
                text = await asyncio.get_running_loop().run_in_executor(None, cv_reader.extract_text, field.filename, data)
            except ValueError:
                text = md  # a LinkedIn archive: its text is what the version was built from
            extras.save_original(m, field.filename, data, text)
            first = not resume.load_index(m)["list"]
            base = {"hh.ru": "С hh.ru", "LinkedIn": "С LinkedIn", "архив LinkedIn": "С LinkedIn"}.get(source, "Загруженное")
            resume.save_version(m, md, name=resume.unique_name(m, "Основное" if first else base))
        except ValueError as e:
            return self.bad(str(e))
        log.info("resume import for %s: %s, %d chars", m.name, source, len(md))
        return web.json_response({"source": source, **self.resume_payload(m)})

    async def api_resume_design(self, request):
        """Template, colour, photo on/off and photo shape of a version."""
        m = self.member(request)
        data = await self.body(request)
        changes = {k: data[k] for k in ("tpl", "color", "photo", "shape") if k in data}
        try:
            resume.set_design(m, str(data.get("vid") or ""), **changes)
        except ValueError as e:
            return self.bad(str(e))
        return web.json_response(self.resume_payload(m))

    async def api_resume_photo(self, request):
        """{vid, data: "data:image/...;base64,..."} -> photo.jpg, shown on that version;
        {remove: true} -> photo deleted everywhere."""
        m = self.member(request)
        data = await self.body(request)
        if data.get("remove"):
            resume.delete_photo(m)
            return web.json_response(self.resume_payload(m))
        raw = str(data.get("data") or "")
        match = re.match(r"^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)$", raw)
        if not match or len(raw) > 4 * 1024 * 1024:
            return self.bad("Нужна картинка JPG или PNG до 3 МБ")
        try:
            resume.get_version(m, data.get("vid"))
            await asyncio.get_running_loop().run_in_executor(None, resume.save_photo, m, base64.b64decode(match.group(2)))
            resume.set_design(m, str(data.get("vid")), photo=True)
        except ValueError as e:
            return self.bad(str(e))
        log.info("resume photo for %s saved", m.name)
        return web.json_response(self.resume_payload(m))

    async def api_resume_export(self, request):
        """PDF / HTML / Markdown of a version, sent as a file into the person's chat with the bot."""
        m = self.member(request)
        data = await self.body(request)
        fmt = data.get("format") if data.get("format") in ("pdf", "html", "md") else "pdf"
        try:
            meta, _ = resume.get_version(m, data.get("vid"))
            work = guests.resume_dir(m) / "export"
            path = resume.export_file(m, meta["id"], fmt, work)
            if fmt == "pdf":
                path = await resume.to_pdf(path)
        except (ValueError, RuntimeError) as e:
            return self.bad(str(e))
        caption = {"pdf": "📄 Резюме в PDF — можно сразу отправлять работодателю.",
                   "html": "🌐 Резюме в HTML: откройте в браузере, можно править и печатать в PDF.",
                   "md": "📝 Резюме текстом (Markdown)."}[fmt]
        try:
            await self.radar.bot.send_file(m.id, str(path), caption=f"{caption}\n«{meta['name']}»", force_document=True)
        except Exception:
            log.exception("export to %s failed", m.id)
            return self.bad("Не получилось отправить файл в чат. Откройте чат с ботом и нажмите Start.", 500)
        return web.json_response({"ok": True, "format": fmt})

    # -- ✨ vacancy score, 📋 application tracker, 🧭 career plan (extras.py)

    def new_ai_job(self, m, op, **extra):
        for jid in [j for j, v in self.ai_jobs.items() if time.time() - v["started"] > 3600]:
            del self.ai_jobs[jid]
        jid = secrets.token_hex(6)
        self.ai_jobs[jid] = {"member": m.id, "op": op, "status": "running", "started": time.time(), **extra}
        return jid

    async def run_ai_job(self, jid, m, work):
        """Run `work()` (a coroutine giving the result) as a job the page polls."""
        job = self.ai_jobs[jid]
        try:
            job.update(status="done", result=await work())
            self.ai.checked = (time.monotonic(), None)
        except RuntimeError as e:
            if str(e) in ("login", "missing"):
                self.ai.checked = (time.monotonic(), str(e))
            job.update(status="error", error=resume_ai.human_error(e))
            log.warning("ai %s for %s failed: %s", job["op"], m.name, e)
        except ValueError as e:
            job.update(status="error", error=str(e))
        except Exception:
            log.exception("ai %s for %s crashed", job["op"], m.name)
            job.update(status="error", error="Что-то пошло не так. Подробности в логе бота.")

    def running(self, m, op):
        return [j for j in self.ai_jobs.values() if j["member"] == m.id and j["op"] == op and j["status"] == "running"]

    async def api_score(self, request):
        """✨ How well one vacancy fits the person (1–5 and why), by Claude on the owner's subscription."""
        m = self.member(request)
        key = str((await self.body(request)).get("key") or "")
        if not resume_ai.allowed(m):
            return self.bad("ИИ для вашего аккаунта отключён администратором.", 403)
        item = m.load_inbox().get(key) or self.results.get(m.id, {}).get(key)
        if not item:
            return self.bad("Вакансия не найдена", 404)
        same = next((j for j, v in self.ai_jobs.items() if v["member"] == m.id and v.get("key") == key and v["status"] == "running"), None)
        if same:
            return web.json_response({"job": same, "queued": True})
        if extras.scores_left(m) <= 0:
            return self.bad("Оценки ИИ на сегодня закончились. Завтра снова можно.", 429)
        if not extras.candidate_text(m):
            return self.bad("Чтобы ИИ сравнил вакансию с вашим опытом, добавьте резюме во вкладке «▤ Резюме».")
        if len(self.running(m, "score")) >= 3:
            return self.bad("ИИ уже оценивает три вакансии — подождите их.", 409)
        jid = self.new_ai_job(m, "score", key=key)
        asyncio.create_task(self.run_ai_job(jid, m, lambda: self.score_one(m, key, item)))
        return web.json_response({"job": jid, "queued": self.ai.lock.locked(), "left": extras.scores_left(m)})

    async def score_one(self, m, key, item):
        thin = False
        try:
            text = await self.vacancy_text(m, {"job_key": key})
        except ValueError:
            text, thin = item.get("text") or "", True
        head = "\n".join(f"{k}: {v}" for k, v in (("Title", item.get("title")), ("Company", item.get("company")),
                                                   ("Location", item.get("location")), ("Pay", extras.salary_of(item))) if v)
        with usage.scope("score"):
            answer = await self.ai.ask(extras.SCORE_RULES, extras.score_prompt(m, item, head + "\n\n" + text, thin))
        result = dict(extras.parse_score(answer), thin=thin)
        inbox = m.load_inbox()
        if key in inbox:
            inbox[key]["ai"] = result
            m.save_inbox(inbox)
        extras.count_score(m)
        log.info("ai score for %s: %s %s", m.name, key, result["score"])
        return result

    async def api_score_new(self, request):
        """✨ «Оценить новые»: the feed's vacancies that have no score yet, one by one (each counts
        against the daily cap). Already scored ones are never sent again."""
        m = self.member(request)
        if not resume_ai.allowed(m):
            return self.bad("ИИ для вашего аккаунта отключён администратором.", 403)
        if self.running(m, "score_new"):
            return self.bad("ИИ уже оценивает ленту — подождите.", 409)
        left = extras.scores_left(m)
        if left <= 0:
            return self.bad("Оценки ИИ на сегодня закончились. Завтра снова можно.", 429)
        if not extras.candidate_text(m):
            return self.bad("Чтобы ИИ сравнил вакансии с вашим опытом, добавьте резюме во вкладке «▤ Резюме».")
        body = await self.body(request)
        inbox = m.load_inbox()
        if body.get("view") == "fav":  # «Избранное → Оценить все»: starred vacancies without a score, up to what is left today
            pairs = sorted(((k, v) for k, v in inbox.items() if VIEWS["fav"](v)), key=lambda kv: kv[1]["date"], reverse=True)
            todo = [kv for kv in pairs if not kv[1].get("ai")]
            cap = left
        else:
            pairs, _ = feed_pairs(m, inbox)
            todo = [g[0] for g in extras.groups(pairs) if not any(v.get("ai") for _, v in g)]
            cap = min(left, SCORE_BATCH)
        if not todo:
            return self.bad("Все вакансии уже оценены.")
        if body.get("dry"):  # only count: the app shows «будет оценено N из M» and asks before it starts
            return web.json_response({"unscored": len(todo), "will": min(len(todo), cap), "left": left})
        todo = todo[:cap]
        jid = self.new_ai_job(m, "score_new", progress={"done": 0, "total": len(todo)})

        async def work():
            done, failed = 0, 0
            for key, item in todo:
                try:
                    await self.score_one(m, key, item)
                    done += 1
                except RuntimeError as e:
                    if str(e) in ("login", "missing"):
                        raise
                    failed += 1
                self.ai_jobs[jid]["progress"] = {"done": done + failed, "total": len(todo)}
            return {"scored": done, "failed": failed, "left": extras.scores_left(m)}
        asyncio.create_task(self.run_ai_job(jid, m, work))
        return web.json_response({"job": jid, "total": len(todo), "queued": self.ai.lock.locked()})

    # -- ✉️ «Откликнуться» (owner only; apply.py). Nothing is sent without the owner's own tap.

    def owner_only(self, request):
        m = self.member(request)
        if not m.is_owner:
            raise web.HTTPForbidden(text='{"error": "Отклики пока только у владельца"}', content_type="application/json")
        return m

    async def api_apply_prepare(self, request):
        """Draft a response: the letter in the vacancy's language, the résumé version to send, where it can go."""
        m = self.owner_only(request)
        body = await self.body(request)
        key = str(body.get("key") or "")
        item = m.load_inbox().get(key) or self.results.get(m.id, {}).get(key)
        if not item:
            return self.bad("Вакансия не найдена", 404)
        if body.get("mode") == "ai" and self.running(m, "apply"):
            return self.bad("Отклик уже готовится — подождите.", 409)
        if body.get("mode") != "ai":  # the default: a letter from templates, no AI, no tokens, at once
            try:
                return web.json_response({"result": await self.draft_tpl(m, key, item, str(body.get("tone") or ""), str(body.get("preset") or ""),
                                                                         str(body.get("lang") or ""))})
            except ValueError as e:
                return self.bad(str(e))
        if resume_ai.uses_left(m) <= 0:
            return self.bad("ИИ на сегодня всё: дневной лимит исчерпан.", 429)
        jid = self.new_ai_job(m, "apply", key=key)
        asyncio.create_task(self.run_ai_job(jid, m, lambda: self.draft_one(m, key, item)))
        return web.json_response({"job": jid, "queued": self.ai.lock.locked()})

    PRESET_MAX, PRESET_TEXT = 10, 700

    def about_presets(self, m, md, lang):
        """«О себе» texts to choose from: the résumé's own + the ones the person saved (in the letter's language or any)."""
        saved = [p for p in (m.load_state().get("apply_presets") or []) if p.get("lang") in (lang, "any", None)]
        profile = self.apply_profile(m)
        mine = [{"id": "profile", "name": "Из профиля", "text": letters.pv(profile, "about", lang)}] if letters.pv(profile, "about", lang) and "about" in profile["use"] else []
        return mine + letters.resume_presets(md) + [{"id": p["id"], "name": p["name"], "text": p["text"]} for p in saved]

    async def cached_vacancy_text(self, m, key, item):
        """The vacancy text for a letter. A site's full text comes from a page fetch (up to 40 s), so it is kept for
        20 minutes: changing the tone, the «о себе» or the language in the sheet must not fetch it again."""
        hit = self.jd_cache.get((m.id, key))
        if hit and time.monotonic() - hit[0] < 1200:
            return hit[1]
        try:
            text = await self.vacancy_text(m, {"job_key": key}, fetch_seconds=8)  # a letter needs no more than that
        except ValueError:
            text = item.get("text") or ""
        self.jd_cache[(m.id, key)] = (time.monotonic(), text)
        if len(self.jd_cache) > 200:
            self.jd_cache.pop(next(iter(self.jd_cache)))
        return text

    async def draft_tpl(self, m, key, item, tone="", preset="", lang_over=""):
        """A response draft from templates (letters.py): the same fields as draft_one plus the choices shown in the sheet."""
        text = await self.cached_vacancy_text(m, key, item)
        head = f"{item.get('title') or ''}\n{item.get('company') or ''}\n{item.get('location') or ''}\n\n"
        lang = lang_over if lang_over in ("ru", "en") else resume.detect_lang(text or head)
        meta = apply.pick_version(m, lang)
        if not meta:
            raise ValueError("Сначала добавьте резюме во вкладке «▤ Резюме».")
        _, md = resume.get_version(m, meta["id"])
        tone = tone if tone in letters.TONES else letters.DEFAULT_TONE
        presets = self.about_presets(m, md, lang)
        if not preset and any(p["id"] == "profile" for p in presets):
            preset = "profile"
        about = next((p["text"] for p in presets if p["id"] == preset), None)
        profile = self.apply_profile(m)
        name = (letters.pv(profile, "fio", lang) if "fio" in profile["use"] else "") or letters.parse_resume(md)["name"]
        manual = letters.manual_reasons(text or item.get("text"))
        return {"key": key, "title": item.get("title") or "", "company": item.get("company") or "",
                "letter": letters.compose(md, item, head + text, lang, tone, about, profile),
                "warning": ("Требует ручного ответа: " + ", ".join(manual) + ". Письмо этого не заменит.") if manual else "", "lang": lang,
                "subject": apply.subject_for(item, name, lang), "channels": apply.contacts(item, text),
                "resume": {"vid": meta["id"], "name": meta["name"], "lang": meta.get("lang", "ru")},
                "smtp": bool(apply.smtp_settings(self.radar.cfg)), "dry": apply.dry_run(self.radar.cfg),
                "blocks": letters.package_blocks(profile, lang),
                "tone": tone, "tones": letters.tone_list("ru"), "presets": presets, "preset": preset if about is not None else "",
                "ai_left": resume_ai.uses_left(m)}

    async def api_apply_compose(self, request):
        """Rebuild the letter with another tone / «о себе» text (typed or a preset). Free: no AI."""
        m = self.owner_only(request)
        body = await self.body(request)
        key = str(body.get("key") or "")
        item = m.load_inbox().get(key) or self.results.get(m.id, {}).get(key)
        if not item:
            return self.bad("Вакансия не найдена", 404)
        try:
            draft = await self.draft_tpl(m, key, item, str(body.get("tone") or ""), str(body.get("preset") or ""), str(body.get("lang") or ""))
            if isinstance(body.get("about"), str):  # the person's own paragraph, not saved
                _, md = resume.get_version(m, draft["resume"]["vid"])
                tone = draft["tone"]
                head = f"{item.get('title') or ''}\n{item.get('company') or ''}\n"
                draft["letter"] = letters.compose(md, item, head + (item.get("text") or ""), draft["lang"], tone, body["about"][:self.PRESET_TEXT],
                                                  self.apply_profile(m))
        except ValueError as e:
            return self.bad(str(e))
        return web.json_response({"letter": draft["letter"], "tone": draft["tone"], "lang": draft["lang"], "subject": draft["subject"],
                                  "blocks": draft["blocks"], "presets": draft["presets"]})

    @staticmethod
    def apply_profile(m):
        return letters.clean_profile(m.load_state().get("apply_profile") or {})

    async def api_apply_profile(self, request):
        """«Ещё → Профиль для откликов»: GET the form; POST {values, custom, use} saves it, {fill: true} fills the empty
        fields from the active résumé by rules (no AI) and returns what it found."""
        m = self.owner_only(request)
        found = None
        if request.method == "POST":
            body = await self.body(request)
            if body.get("fill"):
                index = resume.load_index(m)
                meta = next((v for v in index["list"] if v["id"] == index["active"]), None)
                if not meta:
                    return self.bad("Сначала добавьте резюме во вкладке «▤ Резюме».")
                _, md = resume.get_version(m, meta["id"])
                profile = self.apply_profile(m)
                found = {k: v for k, v in letters.profile_lang_values(letters.from_resume(md)).items()}
                changed = {}
                for k, texts in found.items():  # only into empty places: what the person typed stays
                    cell = profile["values"].setdefault(k, {"ru": "", "en": ""})
                    for lg, text in texts.items():
                        if not cell.get(lg):
                            cell[lg], changed[k] = text, True
                found = changed
                profile = letters.clean_profile(profile)
            else:
                profile = letters.clean_profile(body)
            m.update_state(apply_profile=profile)
        profile = self.apply_profile(m)
        return web.json_response({"fields": [{"key": k, "ru": ru, "en": en} for k, ru, en, _ in letters.PROFILE_FIELDS],
                                  "profile": profile, "found": sorted(found) if found is not None else None,
                                  "custom_max": letters.CUSTOM_MAX})

    async def api_apply_presets(self, request):
        """Save / remove a «о себе» preset of the person: {name, text} or {remove: id}."""
        m = self.owner_only(request)
        body = await self.body(request)
        saved = list(m.load_state().get("apply_presets") or [])
        if body.get("remove"):
            saved = [p for p in saved if p.get("id") != body["remove"]]
        else:
            name, text = str(body.get("name") or "").strip()[:40], str(body.get("text") or "").strip()[:self.PRESET_TEXT]
            if not name or len(text) < 10:
                return self.bad("Нужны название и текст (от 10 символов)")
            if len(saved) >= self.PRESET_MAX:
                return self.bad(f"Не больше {self.PRESET_MAX} своих текстов: удалите ненужный")
            saved.append({"id": "p" + secrets.token_hex(4), "name": name, "text": text, "lang": resume.detect_lang(text)})
        m.update_state(apply_presets=saved)
        return web.json_response({"ok": True, "n": len(saved)})

    async def draft_one(self, m, key, item):
        """One response draft: the letter in the vacancy's language from the résumé's facts, where it can go."""
        try:
            text = await self.vacancy_text(m, {"job_key": key})
        except ValueError:
            text = item.get("text") or ""
        head = f"{item.get('title') or ''}\n{item.get('company') or ''}\n{item.get('location') or ''}\n\n"
        lang = resume.detect_lang(text or head)
        meta = apply.pick_version(m, lang)
        if not meta:
            raise ValueError("Сначала добавьте резюме во вкладке «▤ Резюме».")
        _, md = resume.get_version(m, meta["id"])
        letter = await resume_ai.run(self.ai, "cover", md, head + text, lang)
        resume_ai.count_use(m)
        name = (resume.blocks(md)[0]["x"] if resume.blocks(md) else "").strip("# ")
        return {"key": key, "title": item.get("title") or "", "company": item.get("company") or "",
                "letter": letter["text"], "warning": letter["warning"], "lang": lang,
                "subject": apply.subject_for(item, name, lang), "channels": apply.contacts(item, text),
                "resume": {"vid": meta["id"], "name": meta["name"], "lang": meta.get("lang", "ru")},
                "smtp": bool(apply.smtp_settings(self.radar.cfg)), "dry": apply.dry_run(self.radar.cfg)}

    async def api_apply_send(self, request):
        """«Отправить» by email: only after the owner's tap; the PDF of the chosen version goes along."""
        m = self.owner_only(request)
        data = await self.body(request)
        key, to = str(data.get("key") or ""), str(data.get("to") or "").strip()
        subject, text = str(data.get("subject") or "").strip()[:200], str(data.get("text") or "").strip()[:8000]
        if not apply.EMAIL_RE.fullmatch(to):
            return self.bad("Не похоже на адрес почты")
        if len(text) < 40 or not subject:
            return self.bad("Письмо пустое")
        smtp = apply.smtp_settings(self.radar.cfg)
        if not smtp and not apply.dry_run(self.radar.cfg):
            return self.bad("Почта не подключена: нужен пароль приложения Gmail в config.yml (smtp).")
        try:
            pdf = await apply.resume_pdf(m, data.get("vid"))
            result = await apply.dry_or_send(self.radar.cfg, smtp, to, subject, text, pdf, log)
        except (ValueError, RuntimeError) as e:
            return self.bad(str(e))
        except Exception as e:
            log.exception("apply email to %s failed", to)
            return self.bad(f"Письмо не ушло: {str(e)[:160]}", 500)
        item = apply.mark_applied(m, key, f"Отклик по почте: {to}")
        log.info("apply: %s to %s for %s", result, to, key)
        return web.json_response({"ok": True, "result": result, "item": item_json(key, item) if item else None})

    async def api_apply_done(self, request):
        """The owner applied another way (opened the vacancy / a mail draft / a Telegram chat): the letter and the
        PDF go into their bot chat to forward or attach, and the vacancy is marked «Откликнулся»."""
        m = self.owner_only(request)
        data = await self.body(request)
        key, how = str(data.get("key") or ""), str(data.get("how") or "сайт")[:60]
        text = str(data.get("text") or "").strip()[:3500]
        try:
            pdf = await apply.resume_pdf(m, data.get("vid")) if data.get("vid") else None
        except (ValueError, RuntimeError):
            pdf = None
        try:
            if text:
                await self.radar.bot.send_message(m.id, "✉️ Письмо для отклика — скопируйте:\n\n" + text, link_preview=False)
            if pdf:
                await self.radar.bot.send_file(m.id, str(pdf), caption="📄 Резюме для этого отклика", force_document=True)
        except Exception:
            log.warning("apply: could not send the draft to the chat", exc_info=True)
        item = apply.mark_applied(m, key, f"Отклик: {how}")
        return web.json_response({"ok": True, "item": item_json(key, item) if item else None})

    async def api_original(self, request):
        """📎 The résumé file as it was uploaded: its name, date and text."""
        info, _, text = extras.original_info(self.member(request))
        return web.json_response({"original": info, "text": text[:30000] if info else ""})

    async def api_original_send(self, request):
        m = self.member(request)
        info, path, _ = extras.original_info(m)
        if not info:
            return self.bad("Загруженного файла нет")
        try:
            await self.radar.bot.send_file(m.id, str(path), caption=f"📎 Резюме, как вы его загрузили: {info['file']}",
                                           force_document=True, file_name=info["file"])
        except TypeError:
            await self.radar.bot.send_file(m.id, str(path), caption=f"📎 Резюме, как вы его загрузили: {info['file']}", force_document=True)
        except Exception:
            log.exception("original to %s failed", m.id)
            return self.bad("Не получилось отправить файл в чат. Откройте чат с ботом и нажмите Start.", 500)
        return web.json_response({"ok": True})

    async def api_watch(self, request):
        """⭐ Избранные компании: the companies and their new vacancies."""
        m = self.member(request)
        companies, fresh = watch.overview(m)
        return web.json_response({"companies": companies, "items": grouped_json(fresh[:60]), "new": len(fresh)})

    async def api_watch_change(self, request):
        """{op: "add", name} / {op: "remove", key}."""
        m = self.member(request)
        data = await self.body(request)
        try:
            if data.get("op") == "add":
                watch.add(m, data.get("name"))
            elif data.get("op") == "remove":
                watch.remove(m, data.get("key"))
            else:
                return self.bad("Не понял действие")
        except ValueError as e:
            return self.bad(str(e))
        return await self.api_watch(request)

    async def api_track(self, request):
        m = self.member(request)
        inbox = m.load_inbox()
        tracked = sorted(((k, v) for k, v in inbox.items() if v.get("track")), key=lambda kv: kv[1]["track"].get("at", ""), reverse=True)
        taken = sorted(((k, v) for k, v in inbox.items() if v["status"] == "taken" and not v.get("track")),
                       key=lambda kv: kv[1]["date"], reverse=True)
        return web.json_response({"items": [item_json(k, v) for k, v in tracked],
                                  "taken": grouped_json(taken[:80])[:60], "stages": extras.STAGES})

    async def api_track_set(self, request):
        """{key, stage, note}: move a vacancy along «Мои отклики»; stage "" takes it off."""
        m = self.member(request)
        data = await self.body(request)
        key = str(data.get("key") or "")
        inbox = m.load_inbox()
        if key not in inbox and key in self.results.get(m.id, {}):
            inbox[key] = guests.to_item(self.results[m.id][key])
            inbox[key]["via"] = "search"
        item = inbox.get(key)
        if not item:
            return self.bad("Вакансия не найдена", 404)
        try:
            if data.get("stage") and item["status"] != "taken":
                item["status"] = "new"
                guests.job_action(m, key, item, "take")
            extras.set_stage(item, data.get("stage") or None, data.get("note"))
        except ValueError as e:
            return self.bad(str(e))
        m.save_inbox(inbox)
        return web.json_response({"ok": True, "item": item_json(key, item)})

    async def api_feedback(self, request):
        """A person's note about the app (a wish, a bug): saved, and the owner gets it in the bot chat."""
        m = self.member(request)
        text = str((await self.body(request)).get("text") or "").strip()[:FEEDBACK_MAX]
        if len(text) < 3:
            raise web.HTTPBadRequest(text='{"error": "Напишите хотя бы пару слов"}', content_type="application/json")
        now_ = time.monotonic()
        if now_ - self.last_feedback.get(m.id, -999) < FEEDBACK_GAP:
            raise web.HTTPTooManyRequests(text='{"error": "Подождите полминуты перед следующим сообщением"}', content_type="application/json")
        self.last_feedback[m.id] = now_
        name = "Эдуард" if m.is_owner else (m.info.get("name") or m.name)
        notes = guests.read_json(FEEDBACK_PATH, [])
        notes.append({"who": name, "id": m.id, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        guests.write_json(FEEDBACK_PATH, notes[-FEEDBACK_KEEP:])
        owner = getattr(self.radar, "owner", None)
        if owner and not m.is_owner:
            try:
                await self.radar.bot.send_message(owner, f"💬 Отзыв о приложении от {name}:\n\n{text}")
            except Exception as e:
                log.warning("feedback to the owner: %s", e)
        log.info("feedback from %s (%d characters)", name, len(text))
        return web.json_response({"ok": True})

    async def api_track_add(self, request):
        """A response sent somewhere else (not from the feed), typed by hand."""
        m = self.member(request)
        data = await self.body(request)
        try:
            key, item = extras.manual_item(data.get("title"), data.get("company"), data.get("link"), data.get("note"))
            extras.set_stage(item, data.get("stage") or "applied")
        except ValueError as e:
            return self.bad(str(e))
        inbox = m.load_inbox()
        inbox[key] = item
        m.save_inbox(inbox)
        return web.json_response({"ok": True, "item": item_json(key, item)})

    async def api_plan(self, request):
        m = self.member(request)
        return web.json_response({"plan": m.load_state().get("career_plan"), "goals": extras.GOALS})

    async def api_plan_make(self, request):
        """🧭 Career plan from a résumé version and a goal (grade up / new role / new field)."""
        m = self.member(request)
        data = await self.body(request)
        goal, target = data.get("goal"), re.sub(r"\s+", " ", str(data.get("target") or "")).strip()[:120]
        if goal not in extras.GOALS:
            return self.bad("Выберите цель")
        if goal != "grade" and len(target) < 3:
            return self.bad("Напишите, куда хотите перейти: роль или направление.")
        if not resume_ai.allowed(m):
            return self.bad("ИИ для вашего аккаунта отключён администратором.", 403)
        if resume_ai.uses_left(m) <= 0:
            return self.bad("ИИ на сегодня всё: дневной лимит исчерпан. Завтра снова можно.", 429)
        if self.running(m, "plan"):
            return self.bad("План уже составляется — подождите.", 409)
        try:
            meta, md = resume.get_version(m, data.get("vid"))
        except ValueError:
            return self.bad("Сначала добавьте резюме: план строится по нему.")
        if len(md.strip()) < 200:
            return self.bad("В резюме слишком мало текста для плана.")
        jid = self.new_ai_job(m, "plan")

        async def work():
            with usage.scope("plan"):
                plan = extras.parse_plan(await self.ai.ask(extras.PLAN_RULES, extras.plan_prompt(md, goal, target)), md)
            plan.update(goal=goal, goal_text=target, version=meta["name"], at=extras.now().isoformat(timespec="seconds"))
            m.update_state(career_plan=plan)
            resume_ai.count_use(m)
            log.info("career plan for %s: %s %r", m.name, goal, target)
            return plan
        asyncio.create_task(self.run_ai_job(jid, m, work))
        return web.json_response({"job": jid, "queued": self.ai.lock.locked()})

    async def api_reset(self, request):
        """«Начать заново». The page asks first; the word "reset" here is a second guard
        against a stray call."""
        m = self.member(request)
        data = await self.body(request)
        if data.get("confirm") != "reset":
            raise web.HTTPBadRequest(text='{"error": "Нужно подтверждение"}', content_type="application/json")
        guests.reset_member(m, wipe_jobs=bool(data.get("wipe_jobs")))
        self.results.pop(m.id, None)
        if m.is_owner:
            self.radar.reload()
        log.info("app: %s started over (wipe_jobs=%s)", m.name, bool(data.get("wipe_jobs")))
        return web.json_response({"ok": True})

    def mark_checked(self, m):
        """«Проверено N мин назад» and «следующая через» start over after a manual check
        (a rescan used to leave the old time, so the header looked stuck)."""
        stamp = datetime.now(timezone.utc).isoformat()
        if m.is_owner:
            state = tc.load_state()
            state["last_run"] = stamp
            tc.save_state(state)
        else:
            m.update_state(pool_run=stamp)

    REFRESH_FRESH = {"hh": 10, "sites": 30, "boards": 60}  # minutes: younger than this, ↻ does not start that source again

    @staticmethod
    def settings_hash(m):
        """The feed settings that decide what a vacancy is (words, stop words, stop companies, sites, hh.ru query)."""
        s = m.load_search()
        return hashlib.md5(json.dumps([s.get("keywords"), s.get("sites"), s.get("hh")], sort_keys=True, default=str).encode()).hexdigest()[:12]

    async def api_refresh(self, request):
        """↻: the database is filled in the background (channels every 15 min, hh.ru every 30, sites hourly), so this is
        light: channels read right now (new posts only), and a source is started only when its last pass is older than
        REFRESH_FRESH. After the person changed their words / sites, ONE full pass checks everything again."""
        m = self.member(request)
        if self.radar.lock.locked():  # a scheduled check is running: wait for it, then do the full pass anyway
            try:
                await asyncio.wait_for(self.radar.lock.acquire(), 150)
                self.radar.lock.release()
            except asyncio.TimeoutError:
                return web.json_response({"busy": True, "new": 0})
        full = self.settings_hash(m) != m.load_state().get("refresh_hash")  # the words changed: look at everything again
        days = 3 if full else None
        t0, now_ = time.monotonic(), datetime.now(timezone.utc)
        last_tg = tc.load_state().get("last_run") if m.is_owner else m.load_state().get("pool_run")
        if not full and last_tg and now_ - datetime.fromisoformat(last_tg) < timedelta(minutes=3):
            found = []  # the channels were read minutes ago: nothing to fetch yet
        elif m.is_owner:
            found = await self.radar.check(tg=True, web=False, rescan_days=days, manual=True) or []
        else:
            async with self.radar.lock:
                found = (await self.radar.check_pool([m], rescan_days=days))[m.id]
        spent = round(time.monotonic() - t0, 1)
        now_ = datetime.now(timezone.utc)

        def stale(run, minutes):
            return full or not run or now_ - run >= timedelta(minutes=minutes)
        boards_run = tc.load_state().get("boards_run")
        started, ages = [], {}  # hh.ru and the sites go on in the background (they take longer); the page reloads later
        for name, attr, minutes in (("hh", "hh_run", self.REFRESH_FRESH["hh"]), ("sites", "sites_run", self.REFRESH_FRESH["sites"])):
            run = getattr(self.radar, attr, None)
            ages[name] = int((now_ - run).total_seconds() // 60) if run else None
            if hasattr(self.radar, name + "_task") and stale(run, minutes):
                setattr(self.radar, attr, now_)
                asyncio.create_task(getattr(self.radar, name + "_task")(full=m.id if full else None))
                started.append(name)
        if m.is_owner and hasattr(self.radar, "boards_task"):
            run = datetime.fromisoformat(boards_run) if boards_run else None
            ages["boards"] = int((now_ - run).total_seconds() // 60) if run else None
            if stale(run, self.REFRESH_FRESH["boards"]):
                asyncio.create_task(self.radar.boards_task())
                started.append("boards")
        m.update_state(refresh_hash=self.settings_hash(m))
        self.mark_checked(m)
        log.info("manual refresh by %s: %d new from channels in %.1f s, %s, started: %s", m.name, len(found), spent, "full" if full else "light", started or "nothing")
        return web.json_response({"busy": False, "new": len(found), "more": bool(started), "full": full, "started": started, "ages": ages})
