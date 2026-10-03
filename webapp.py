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
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import yaml
from aiohttp import web
from telethon import utils

import cv_reader
import extras
import guests
import resume
import resume_ai
import tg_collect as tc

log = logging.getLogger("job-radar.app")

WEB_DIR = tc.TG_DIR / "webapp"
GRAPH_PATH = tc.TG_DIR / "rolegraph.yml"
FEED_CACHE_PATH = tc.ROOT / "data" / "tg-feed-cache.json"
HOST, PORT = "127.0.0.1", 8787
SKINS = ("hybrid", "remote", "office")  # cat themes; old names map onto them
OLD_SKINS = {"radar": "hybrid", "tropic": "remote", "dossier": "office"}
INIT_MAX_AGE = timedelta(days=1)  # how long one app launch stays valid
SEARCH_DAYS = 21                  # how far back search reads channels (job channels post a few a week)
POSTS_TTL = timedelta(minutes=15) # channel messages are re-read at most this often
POSTS_PER_CHANNEL = 300
QUERY_MAX = 80
RESULTS_MAX = 80
SEARCH_COOLDOWN = 0.5             # seconds between one person's searches (graph taps come fast)
ACTIONS = ("take", "hide", "restore", "fav")
VIEWS = {
    "new": lambda i: i["status"] == "new",
    "fav": lambda i: i.get("fav"),  # «Избранное»: swiping right stars a vacancy (there is no «в работе» any more)
    "taken": lambda i: i["status"] == "taken",
    "hidden": lambda i: i["status"] == "hidden",
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
    groups = (yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8")) or {}).get("words") or []
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


def hh_variants(phrase, words=None):
    """The phrase for hh.ru plus its English twin («антифрод аналитик» -> «antifraud analyst»)."""
    words = load_words() if words is None else words
    tokens = [w for w in re.findall(r"[\w+#&.]+", norm(phrase)) if w not in FILLER]
    latin = [next((t for t in words.get(ru_stem(w), []) if not re.search(r"[а-я]", norm(t))), w) for w in tokens]
    return list(dict.fromkeys([" ".join(tokens), " ".join(latin)]))


def load_graph():
    if not GRAPH_PATH.exists():
        return {}
    roles = (yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8")) or {}).get("roles") or {}
    graph = {rid: {"names": [str(n) for n in r.get("names") or []], "links": dict(r.get("links") or {})}
             for rid, r in roles.items() if isinstance(r, dict)}
    for rid, role in graph.items():  # links work both ways
        for other, kind in list(role["links"].items()):
            if other in graph:
                graph[other]["links"].setdefault(rid, kind)
    return graph


def find_role(graph, query):
    """The role the query names: an exact name first, else the longest name inside the query."""
    q = " ".join(re.findall(r"[\w+#&.]+", norm(query)))
    best, size = None, 0
    for rid, role in graph.items():
        for name in role["names"]:
            n = " ".join(re.findall(r"[\w+#&.]+", norm(name)))
            if n == q:
                return rid
            if n and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", q) and len(n) > size:
                best, size = rid, len(n)
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
        "tags": item.get("tags", []),
        "text": excerpt(item.get("text", ""), 4000 if full else 600),
        "salary": extras.salary_of(item),
        "tz": extras.tz_label(item),
        "ai": item.get("ai"),
        "track": item.get("track"),
        "silent": extras.silent_days(item),
        "summary": extras.summary(item),
    }


SORTS = {  # feed order: newest first, or by pay / AI score / starred, newest first among equals
    "date": lambda g: 0,
    "salary": lambda g: -max(extras.salary_value(v) for _, v in g),
    "ai": lambda g: -max(((v.get("ai") or {}).get("score") or 0) for _, v in g),
    "fav": lambda g: -int(any(v.get("fav") for _, v in g)),
}
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
    gs.sort(key=SORTS.get(sort, SORTS["date"]))
    return grouped_json(gs[offset:offset + limit], grouped=True), len(gs)


def grouped_json(pairs, grouped=False):
    """Vacancies with duplicates merged: one card, the other sources listed under «also»."""
    out = []
    for g in (pairs if grouped else extras.groups(pairs)):
        (key, item), rest = g[0], g[1:]
        data = item_json(key, item)
        if rest:
            data["also"] = [{"key": k, "origin": extras.origin(v), "source": "web" if v.get("source") == "web" else "tg",
                             "link": v.get("link", "")} for k, v in rest]
            if not data["ai"]:
                data["ai"] = next((v["ai"] for _, v in rest if v.get("ai")), None)
        out.append(data)
    return out


def feed_pairs(m, inbox):
    """The «new» feed: newest first, the person's time zone filter applied. -> (pairs, filtered out)"""
    tz = extras.tz_settings(m)
    new = sorted(((k, v) for k, v in inbox.items() if v["status"] == "new"), key=lambda kv: kv[1]["date"], reverse=True)
    new = [(k, v) for k, v in new if v.get("source") == "web" or not tc.looks_like_article(v.get("text", ""))]
    keep = [(k, v) for k, v in new if extras.tz_fits(v, tz)]
    return keep, len(new) - len(keep)


def searchable(job):
    return " ".join([job.get("title") or "", job.get("company") or "", job.get("text") or ""])


# ---------------------------------------------------------------- the server

class WebApp:
    def __init__(self, radar):
        self.radar = radar           # the running bot (JobRadar): clients, lock, owner
        self.posts = {}              # channel key -> (read at, [job-shaped posts])
        self.results = {}            # person id -> {key: job} from their last search
        self.last_search = {}
        self.warming = False
        self.runner = None
        self.ai = resume_ai.Claude(radar.cfg.get("claude_exe"))
        log.info("ai: claude at %s", self.ai.exe or "— not found")
        self.ai_jobs = {}            # job id -> {"member", "op", "status", "result"|"error"}
        self.ai_checking = False

    # -- plumbing

    def member(self, request):
        auth = request.headers.get("Authorization", "")
        user = check_init_data(auth[4:] if auth.startswith("tma ") else "", self.radar.cfg["bot_token"])
        if not user:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Откройте приложение через бота в Telegram."}),
                                       content_type="application/json")
        member = self.radar.member_for(user["id"])
        if member is None:
            raise web.HTTPForbidden(text=json.dumps({"error": "Это личное приложение. Попросите у владельца приглашение."}),
                                    content_type="application/json")
        return member

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
        r.add_get("/api/sources", self.api_sources)
        r.add_post("/api/sources", self.api_sources_change)
        r.add_post("/api/settings", self.api_settings)
        r.add_post("/api/refresh", self.api_refresh)
        r.add_post("/api/score", self.api_score)
        r.add_post("/api/score/new", self.api_score_new)
        r.add_get("/api/resume/original", self.api_original)
        r.add_post("/api/resume/original/send", self.api_original_send)
        r.add_get("/api/track", self.api_track)
        r.add_post("/api/track", self.api_track_set)
        r.add_post("/api/track/add", self.api_track_add)
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
            "funnel": extras.funnel(inbox),
            "stats": {
                "new": len(extras.groups(feed_pairs(m, inbox)[0])),
                "taken": sum(1 for i in inbox.values() if i["status"] == "taken"),
                "fav": sum(1 for i in inbox.values() if i.get("fav")),
                "hidden": sum(1 for i in inbox.values() if i["status"] == "hidden"),
                "channels": len(m.selected(pool)) + (len(tc.load_channels(tc.CHANNELS_PATH)) if m.is_owner else 0),
                "sites": len(m.sites(listed)),
            },
            "checked_at": last,
            "next_check": self.radar.minutes_to_next_check() if m.is_owner else None,
        })

    async def api_jobs(self, request):
        m = self.member(request)
        view = request.query.get("view", "new")
        keep = VIEWS.get(view, VIEWS["new"])
        inbox = m.load_inbox()
        if view == "new" or view not in VIEWS:
            items, tz_hidden = feed_pairs(m, inbox)
        else:
            items, tz_hidden = sorted(((k, v) for k, v in inbox.items() if keep(v)), key=lambda kv: kv[1]["date"], reverse=True), 0
        try:
            offset = max(0, int(request.query.get("offset", 0)))
            limit = min(PAGE_MAX, max(1, int(request.query.get("limit", 40))))
        except ValueError:
            offset, limit = 0, 40
        f = request.query.get("f", "all")
        if f in FILTERS:
            items = [(k, v) for k, v in items if FILTERS[f](v)]
        page, total = page_json(items, request.query.get("sort", "date"), offset, limit)
        unscored = sum(1 for g in extras.groups(items) if not any(v.get("ai") for _, v in g)) if view == "new" else 0
        return web.json_response({"items": page, "total": total, "offset": offset, "tz_hidden": tz_hidden, "unscored": unscored})

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
        now_ = time.monotonic()
        if now_ - self.last_search.get(m.id, 0) < SEARCH_COOLDOWN:
            await asyncio.sleep(SEARCH_COOLDOWN)
        self.last_search[m.id] = time.monotonic()
        words = load_words()
        if not title_re(query, words):
            return web.json_response({"query": query, "mode": mode, "graph": None, "items": [], "sources": {}})

        graph, view = load_graph(), None
        terms = [("exact", "точно", title_re(query, words))]
        rid = find_role(graph, query)
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

        jobs, counts = await self.gather(m, query, terms, mode)
        hits, rank = [], {"exact": 0, "syn": 1, "adj": 2, "far": 3, "text": 4}
        inbox = m.load_inbox()
        for job in jobs:
            title = norm(job.get("title"))
            hit = next(((kind, label) for kind, label, rx in terms if rx.search(title)), None)  # closest kind wins
            if hit is None and job.get("source") == "tg" and terms[0][2].search(norm(searchable(job))):
                # the phrase is only somewhere in a post (often a digest of several jobs): last, labelled
                hit = ("text", "в тексте поста")
            if hit:
                hits.append((rank[hit[0]], job, *hit))
        hits.sort(key=lambda h: (h[0], -h[1]["date"].timestamp()))
        keep, items, same = {}, [], set()
        for _, job, kind, label in hits:
            twin = (norm(job.get("title")).strip(), norm(job.get("company")).strip())
            if twin in same and twin[1]:  # one vacancy posted on several sites: show it once
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
        self.results[m.id] = keep
        log.info("search for %s: %r %s -> %d", m.name, query, mode, len(items))
        return web.json_response({"query": query, "mode": mode, "graph": view, "items": items, "sources": counts,
                                  "no_role": mode == "similar" and view is None})

    async def gather(self, m, query, terms, mode):
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
        for key, item in m.load_inbox().items():
            add(dict(item, key=key, date=datetime.fromisoformat(item["date"])), "inbox")
        gate = asyncio.Semaphore(4)  # channels are read side by side, a few at a time

        async def read(key):
            async with gate:
                return await self.channel_posts(key, m)
        hh_task = asyncio.create_task(self.hh_search(m, query, terms))  # hh.ru and Himalayas go meanwhile
        hima_task = asyncio.create_task(self.himalayas_search(m, query)) if "himalayas" in m.sites(guests.listed_sites()) else None
        for posts in await asyncio.gather(*(read(k) for k in m.selected(guests.load_pool()))):
            for post in posts:
                add(post, "channels")
        listed = guests.listed_sites()
        feed = guests.read_json(FEED_CACHE_PATH, {}).get("feed", {})
        for provider in m.sites(listed):
            for j in (feed.get(provider) or {}).get("jobs", []):
                if j.get("url"):
                    add(guests.web_job(j, listed.get(provider, provider), m), "sites")
        if hima_task:  # its cached feed is only the newest few hundred of ~100k
            for j in await hima_task:
                add(guests.web_job(j, listed.get("himalayas", "Himalayas"), m), "sites")
        for j in await hh_task:
            add(guests.web_job(j, guests.HH_NAME, m), "hh")
        return jobs, counts

    async def himalayas_search(self, m, query):
        try:
            return await guests.fetch_himalayas(hh_variants(query))
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

    async def channel_posts(self, key, member):
        """Job posts of one public channel for the last SEARCH_DAYS days (cached a while)."""
        hit = self.posts.get(key)
        if hit and datetime.now(timezone.utc) - hit[0] < POSTS_TTL:
            posts = hit[1]
        else:
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
            self.posts[key] = (datetime.now(timezone.utc), posts)
        flt = tc.job_filter_from(member.cfg())
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
            "words": {g: [guests.plain(p) for p in kw.get(g, [])] for g in ("role", "negative")},
            "tz": extras.tz_settings(m),
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
        elif kind == "site" and data.get("id") in listed:
            pid, on = data["id"], bool(data.get("on"))
            mine = m.sites(listed)
            if m.is_owner and pid != guests.HH_ID:
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
        elif kind == "words" and data.get("group") in ("role", "negative"):
            search = m.load_search()
            if data.get("add"):
                added, refused = guests.add_words(search, data["group"], str(data["add"]))
                note = "; ".join(f"{w} — {why}" for w, why in refused) or None
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
        info = m.load_state().get("cv")
        if not info:
            return {"cv": None}
        graph, pool = load_graph(), guests.load_pool()
        analysis = dict(info.get("analysis") or {})
        analysis["roles"] = [dict(r, words=cv_reader.role_words(graph, [r["id"]]), hh=cv_reader.hh_query(graph, [r["id"]]))
                             for r in analysis.get("roles", []) if r["id"] in graph]
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
            result = cv_reader.apply(m, load_graph(), roles, channels, "replace" if data.get("mode") == "replace" else "merge",
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

    async def vacancy_text(self, m, data):
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
                out, _ = await asyncio.wait_for(proc.communicate(), 40)
                if proc.returncode == 0 and len(out.strip()) >= 80:
                    return out.decode("utf-8", "replace")[:12000]
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
            return self.bad("ИИ для вас пока не включён. Попросите владельца бота включить его в «👥 Близкие».", 403)
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
            return self.bad("ИИ для вас пока не включён. Попросите владельца бота включить его в «👥 Близкие».", 403)
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
            return self.bad("ИИ для вас пока не включён. Попросите владельца бота включить его в «👥 Близкие».", 403)
        if self.running(m, "score_new"):
            return self.bad("ИИ уже оценивает ленту — подождите.", 409)
        left = extras.scores_left(m)
        if left <= 0:
            return self.bad("Оценки ИИ на сегодня закончились. Завтра снова можно.", 429)
        if not extras.candidate_text(m):
            return self.bad("Чтобы ИИ сравнил вакансии с вашим опытом, добавьте резюме во вкладке «▤ Резюме».")
        pairs, _ = feed_pairs(m, m.load_inbox())
        todo = [g[0] for g in extras.groups(pairs) if not any(v.get("ai") for _, v in g)]
        if not todo:
            return self.bad("Все вакансии в ленте уже оценены.")
        todo = todo[:min(left, SCORE_BATCH)]
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
            return self.bad("ИИ для вас пока не включён. Попросите владельца бота включить его в «👥 Близкие».", 403)
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

    async def api_refresh(self, request):
        """Re-read the person's channels for the last day right now (like 🔄 in the chat)."""
        m = self.member(request)
        if self.radar.lock.locked():
            return web.json_response({"busy": True, "new": 0})
        if m.is_owner:
            found = await self.radar.check(tg=True, web=False, rescan_days=1, manual=True) or []
        else:
            async with self.radar.lock:
                found = (await self.radar.check_pool([m], rescan_days=1))[m.id]
        return web.json_response({"busy": False, "new": len(found)})
