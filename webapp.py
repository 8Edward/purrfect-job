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
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import yaml
from aiohttp import web
from telethon import utils

import cv_reader
import guests
import tg_collect as tc

log = logging.getLogger("job-radar.app")

WEB_DIR = tc.TG_DIR / "webapp"
GRAPH_PATH = tc.TG_DIR / "rolegraph.yml"
FEED_CACHE_PATH = tc.ROOT / "data" / "tg-feed-cache.json"
HOST, PORT = "127.0.0.1", 8787
SKINS = ("radar", "dossier", "tropic")
INIT_MAX_AGE = timedelta(days=1)  # how long one app launch stays valid
SEARCH_DAYS = 7                   # how far back search reads channels
POSTS_TTL = timedelta(minutes=15) # channel messages are re-read at most this often
POSTS_PER_CHANNEL = 300
QUERY_MAX = 80
RESULTS_MAX = 80
SEARCH_COOLDOWN = 2.0             # seconds between one person's searches
ACTIONS = ("take", "hide", "restore", "fav")
VIEWS = {
    "new": lambda i: i["status"] == "new",
    "fav": lambda i: i.get("fav"),
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
    }


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
        r.add_get("/api/cv", self.api_cv)
        r.add_post("/api/cv", self.api_cv_upload)
        r.add_post("/api/cv/apply", self.api_cv_apply)
        r.add_post("/api/cv/delete", self.api_cv_delete)
        r.add_post("/api/reset", self.api_reset)
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
        return web.json_response({
            "name": "Эдуард" if m.is_owner else (m.info.get("name") or m.name).split(" ")[0],
            "owner": m.is_owner,
            "skin": state.get("skin") if state.get("skin") in SKINS else None,
            "onboarded": bool(state.get("app_onboarded")),
            "stats": {
                "new": sum(1 for i in inbox.values() if i["status"] == "new"),
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
        keep = VIEWS.get(request.query.get("view", "new"), VIEWS["new"])
        inbox = m.load_inbox()
        items = sorted(((k, v) for k, v in inbox.items() if keep(v)), key=lambda kv: kv[1]["date"], reverse=True)
        return web.json_response({"items": [item_json(k, v) for k, v in items[:300]]})

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
        if not phrase_re(query):
            return web.json_response({"query": query, "mode": mode, "graph": None, "items": [], "sources": {}})

        graph, view = load_graph(), None
        terms = [("exact", "точно", phrase_re(query))]
        if mode == "similar":
            rid = find_role(graph, query)
            if rid:
                view = graph_view(graph, rid, query)
                for node in view["nodes"]:
                    if node["id"] in off:
                        continue
                    label = {"syn": "синоним", "adj": "смежная", "far": "дальняя"}[node["kind"]] + ": " + node["label"]
                    for name in node["names"]:
                        rx = phrase_re(name) if len(name.strip()) > 3 else None  # «BA», «PM»: too noisy
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
        for key in m.selected(guests.load_pool()):
            for post in await self.channel_posts(key, m):
                add(post, "channels")
        listed = guests.listed_sites()
        feed = guests.read_json(FEED_CACHE_PATH, {}).get("feed", {})
        for provider in m.sites(listed):
            for j in (feed.get(provider) or {}).get("jobs", []):
                if j.get("url"):
                    add(guests.web_job(j, listed.get(provider, provider), m), "sites")
        try:
            hh = guests.hh_settings(m)
            names = [query]  # hh.ru gets the phrase itself, plus synonyms and close roles in «similar»
            if mode == "similar":
                names += [label.split(": ", 1)[1] for kind, label, _ in terms[1:] if kind in ("syn", "adj")]
            q = " OR ".join(dict.fromkeys(f'"{n}"' for n in names))[:guests.HH_QUERY_MAX]
            for j in await guests.fetch_hh(q, hh["remote"]):
                add(guests.web_job(j, guests.HH_NAME, m), "hh")
        except Exception as e:
            log.warning("search hh.ru: %s", e)
        return jobs, counts

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
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        analysis = cv_reader.analyze(text, load_graph(), guests.load_pool())
        cv_reader.save(m, field.filename, text, analysis)
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

    async def api_cv_delete(self, request):
        m = self.member(request)
        cv_reader.forget(m)
        return web.json_response({"cv": None})

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
