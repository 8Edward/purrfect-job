"""Offline tests for the mini app server (webapp.py). Run: python telegram/test_webapp.py -> ok

Uses a temp folder and a fake bot: no Telegram, no network, real data untouched."""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent))

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import guests  # noqa: E402
guests.START_EMPTY = False  # the preset machinery is what is tested here
import webapp  # noqa: E402

TOKEN = "123:TEST"


def sign(user, token=TOKEN, age=0):
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "q1",
              "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# -- initData checks
good = sign({"id": 5, "first_name": "Мама"})
assert webapp.check_init_data(good, TOKEN)["id"] == 5
assert webapp.check_init_data(good, "999:OTHER") is None                       # wrong bot
assert webapp.check_init_data(good.replace("q1", "q2"), TOKEN) is None          # tampered
assert webapp.check_init_data(sign({"id": 5}, age=200000), TOKEN) is None       # stale
assert webapp.check_init_data("", TOKEN) is None

# -- phrase matching and the role graph
rx = webapp.phrase_re("бизнес аналитик")
assert rx.search("ведущий бизнес-аналитик") and rx.search("ищем бизнес аналитика в команду")
assert not rx.search("бизнесаналитик") and not rx.search("системный аналитик")
assert webapp.phrase_re("системный аналитик").search("ищем системного аналитика")
assert webapp.phrase_re("аналитик данных").search("аналитиков данных") and not webapp.phrase_re("sql").search("mysql")
assert webapp.phrase_re("data analyst").search("Senior Data Analysts")
assert webapp.phrase_re("  ") is None
words = webapp.load_words()
rx = webapp.title_re("антифрод аналитик", words)
assert all(rx.search(webapp.norm(t)) for t in ("Fraud Analyst", "Аналитик (антифрод)", "аналитик по антифроду", "Senior Anti-Fraud Analyst"))
assert not rx.search("аналитик данных") and not rx.search("antifraud manager")
assert webapp.hh_variants("антифрод аналитик", words) == ["антифрод аналитик", "antifraud analyst"]
graph = webapp.load_graph()
assert webapp.find_role(graph, "Бизнес-аналитик") == "business-analyst"
assert webapp.find_role(graph, "senior data analyst remote") == "data-analyst"
assert webapp.find_role(graph, "водолаз") is None
view = webapp.graph_view(graph, "business-analyst", "бизнес аналитик")
kinds = {n["id"]: n["kind"] for n in view["nodes"]}
assert kinds["system-analyst"] == "adj" and kinds["product-manager"] == "far" and "syn:1" in kinds
assert "business-analyst" in graph["system-analyst"]["links"]  # links work both ways
# roles by meaning: `also` titles (O*NET + Russian), any word order, word equivalents
assert webapp.find_role(graph, "аналитик dwh") == "dwh-analyst" and webapp.find_role(graph, "DWH-аналитик") == "dwh-analyst"
assert webapp.find_role(graph, "chargeback analyst") == "fraud-analyst"
assert webapp.find_role(graph, "антифрод") == "fraud-analyst"
assert webapp.find_role(graph, "системный аналитик dwh") == "dwh-analyst"
for t, rid in [("Chargeback Specialist", "fraud-analyst"), ("Head of Fraud Operations", "fraud-analyst"),
               ("Trust & Safety Analyst", "fraud-analyst"), ("Аналитик по мониторингу транзакций", "fraud-analyst"),
               ("Data Warehouse Analyst", "dwh-analyst"), ("Аналитик хранилища данных (DWH)", "dwh-analyst"),
               ("Senior Insights Analyst", "data-analyst"), ("Аналитик SQL", "data-analyst"),
               ("Power BI Developer", "bi-analyst"), ("Credit Risk Specialist", "risk-analyst")]:
    assert rid in webapp.roles_of_title(t), (t, webapp.roles_of_title(t))
assert webapp.roles_of_title("Welder") == [] and webapp.roles_of_title("Водитель такси") == []
# words side by side vs scattered; the first role in a title is what the job is
tr = webapp.tight_re("антифрод аналитик")
assert all(tr.search(webapp.norm(t)) for t in ("Fraud Analyst", "Аналитик (антифрод)", "аналитик по антифроду"))
assert not webapp.tight_re("data analyst").search(webapp.norm("Data Scientist, Marketing Analytics"))
assert webapp.title_roles("Senior Data Engineer – Data Analytics Platform")[0][2] == "data-engineer"
assert webapp.title_roles("Аналитик DWH")[0][2] == "dwh-analyst"

# Claude's labels: kept apart, only sure ones used, ✓/✗ review, reset keeps checked ones
import re  # noqa: E402
import roles  # noqa: E402
roles.CACHE_PATH = Path(tempfile.mkdtemp()) / "roles.json"
import filtercheck  # noqa: E402
import reality  # noqa: E402
import sourcestats  # noqa: E402
_scratch = Path(tempfile.mkdtemp())  # the bot's counters must not get the test sources
sourcestats.PATH, filtercheck.PATH, reality.PATH = _scratch / "ss.json", _scratch / "fc.json", _scratch / "rl.json"
jobs_ = [{"source": "web", "key": "a", "title": "Payments Integrity Specialist", "date": datetime.now(timezone.utc)},
         {"source": "web", "key": "b", "title": "Welder", "date": datetime.now(timezone.utc)},
         {"source": "web", "key": "c", "title": "Data Guru", "date": datetime.now(timezone.utc)}]
todo = roles.unknown(jobs_, lambda t: bool(webapp.roles_of_title(t)))
assert [j["key"] for j in todo] == ["a", "b", "c"]
got = roles.parse('```json\n{"1": {"roles": ["fraud-analyst", "nonsense"], "sure": true}, "2": {"roles": [], "sure": true},'
                  ' "3": {"roles": ["data-analyst"], "sure": false}}\n```', todo, graph)
assert got["t:payments integrity specialist"]["roles"] == ["fraud-analyst"]
roles.save({"labels": got, "meta": {}})
assert roles.label_of(jobs_[0]) == (["fraud-analyst"], False)   # sure: used, not yet checked
assert roles.label_of(jobs_[2]) == ([], False)                  # a guess: never used
assert roles.unknown(jobs_, lambda t: False) == []               # each title is asked once
items, n = roles.review_list()
assert n == 2 and items[0]["title"] == "Payments Integrity Specialist"  # the guess waits for ✓ too
roles.review(items[0]["key"], False)
assert roles.label_of(jobs_[0]) == ([], False)                  # ✗: never used again
roles.review(items[0]["key"], True)
assert roles.label_of(jobs_[0]) == (["fraud-analyst"], True)
assert roles.reset() == 1 and roles.label_of(jobs_[0]) == (["fraud-analyst"], True)  # ✓ survives a reset
assert roles.own(items[0]["key"], ["data-analyst"], "аналитик данных")                 # ✎ the owner's own role wins
assert roles.label_of(jobs_[0]) == (["data-analyst"], True) and roles.load()["labels"][items[0]["key"]]["own"] == "аналитик данных"
assert roles.own("no-such-key", [], "x") is False

# second look: the REAL description via a redirect chain, then Claude judges by it
import http.server  # noqa: E402
import threading  # noqa: E402
import jdfetch  # noqa: E402
LONG = "We are hiring a payments fraud analyst to investigate chargebacks and build detection rules. " * 8
class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/go":
            self.send_response(302); self.send_header("Location", "/agg"); self.end_headers(); return
        body = {"/agg": '<html><body><p>short</p><a href="/real">Apply on company site</a></body></html>',
                "/real": f"<html><body><main>{LONG}</main></body></html>"}.get(self.path, "<html>x</html>")
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
jdfetch.SKIP = re.compile(r"^$")  # the test server is on localhost
assert "payments fraud analyst" in jdfetch.describe(base + "/go"), "redirect + aggregator + apply link"
assert jdfetch.describe(base + "/nothing") == ""
class _Ai:
    async def ask(self, system, prompt, timeout=0):
        assert "payments fraud analyst" in prompt and "REAL description" in system
        return '{"1": {"roles": ["fraud-analyst"], "sure": true}}'
roles.DAILY = 40; roles.DEEP_MIN = 1
jd = {"source": "web", "key": "d", "title": "Risk Guru", "link": base + "/go", "date": datetime.now(timezone.utc)}
roles.save({"labels": {"t:risk guru": {"title": "Risk Guru", "roles": [], "sure": False, "at": "x"}}, "meta": {}})
cand = roles.deep_candidates([jd])
assert [j["key"] for j in cand] == ["d"]
assert asyncio.run(roles.deep(_Ai(), cand, graph)) == 1
assert roles.label_of(jd) == (["fraud-analyst"], False) and roles.load()["labels"]["t:risk guru"]["jd"]
assert roles.deep_candidates([jd]) == []  # looked at once
srv.shutdown()
roles.DAILY = 0; roles.DEEP_DAILY = 0  # the app tests below must never call the real Claude


CV_TEXT = """Анна Петрова — Middle бизнес-аналитик
Опыт: 4 года. Бизнес-аналитик в банке: сбор и анализ требований, BPMN-схемы, user stories,
техническое задание для разработки, работа с Jira и Confluence. Писала SQL-запросы к PostgreSQL
для проверки данных, строила отчёты в Excel. Вместе с системным аналитиком описывала интеграции.
Языки: английский — B2 (upper-intermediate)."""


def make_docx(text):
    import io
    import zipfile
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def make_pdf():
    """A tiny real one-page PDF with an English data-analyst résumé (built by hand, no libraries)."""
    lines = ["John Smith - Data Analyst", "Data Analyst at Acme: SQL, Python, Tableau, ClickHouse dashboards.",
             "Data Analyst at Beta: A/B tests, retention and funnel analysis for the product team.",
             "Built ETL in Airflow. English: C1. Looking for a remote data analyst role."] * 2
    stream = "BT /F1 11 Tf 40 760 Td 14 TL " + " ".join(f"({l}) '" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode("latin-1")


class FakeEntity:
    id = 777
    title = "Data Jobs"
    username = "datajob"


class FakeMsg:
    def __init__(self, i, text, days=0):
        self.id, self.message = i, text
        self.date = datetime.now(timezone.utc) - timedelta(days=days)


class FakeUser:
    async def get_entity(self, key):
        return FakeEntity()

    async def iter_messages(self, entity, limit=None):
        for m in [FakeMsg(3, "Вакансия: Бизнес-аналитик в финтех, удалённо. Требуется опыт с требованиями и BPMN, " * 2),
                  FakeMsg(2, "Ищем системного аналитика, вакансия, удалёнка, Kafka, REST и SQL — откликайтесь. " * 2),
                  FakeMsg(1, "Вакансия старая: бизнес аналитик " * 10, days=30)]:
            yield m


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_file(self, to, path, **kw):
        self.sent.append((to, path))

    async def send_message(self, to, text, **kw):
        self.sent.append((to, text))


class FakeClaude:
    def __init__(self, answers, fail=None):
        self.answers, self.fail = answers, fail
        self.lock = asyncio.Lock()
        self.checked = (time.monotonic(), None)

    async def ask(self, system, prompt, timeout=0):
        if self.fail:
            raise RuntimeError(self.fail)
        return self.answers.pop(0)


class FakeRadar:
    def __init__(self, reg):
        self.bot = FakeBot()
        self.cfg = {"bot_token": TOKEN}
        self.user = FakeUser()
        self.lock = asyncio.Lock()
        self.reg = reg

    def member_for(self, uid):
        return guests.get(guests.load_registry(), uid)

    def minutes_to_next_check(self):
        return 5

    def reload(self):
        pass

    async def check_pool(self, members, rescan_days=None):
        return {m.id: [] for m in members}


async def main():
    with tempfile.TemporaryDirectory() as d:
        saved = guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, webapp.FEED_CACHE_PATH, guests.fetch_hh
        guests.USERS_DIR = Path(d)
        guests.REGISTRY_PATH = Path(d) / "users.json"
        guests.POOL_PATH = Path(d) / "pool.json"
        webapp.FEED_CACHE_PATH = Path(d) / "feed.json"
        try:
            guests.save_pool({"channels": {"datajob": {"title": "Data Jobs", "added_by": 5, "added": "a"}}, "adds": {}, "proposals": {}})
            reg = guests.load_registry()

            class U:
                id, username, first_name, last_name = 5, "mama", "Мама", None
            g = guests.register(reg, U())
            guests.save_registry(reg)
            g.update_state(step=None)
            g.set_selected(["datajob"])
            g.set_sites(["hh", "himalayas"])
            inbox_job = {"source": "web", "key": "hh.ru/vacancy/1", "title": "Бизнес-аналитик", "company": "Uzum",
                         "location": "Remote", "comp": "", "site": "hh.ru", "link": "https://hh.ru/vacancy/1",
                         "date": datetime.now(timezone.utc), "tags": ["ru-company"], "pipeline_line": "- [ ] x"}
            g.save_inbox({"hh.ru/vacancy/1": guests.to_item(inbox_job)})
            guests.write_json(webapp.FEED_CACHE_PATH, {"feed": {"himalayas": {"jobs": [
                {"title": "Business Analyst", "url": "https://himalayas.app/jobs/ba", "company": "Plata", "location": "Remote", "posted": "2026-10-01"},
                {"title": "Product Owner", "url": "https://himalayas.app/jobs/po", "company": "X", "posted": "2026-10-01"},
                {"title": "Welder", "url": "https://himalayas.app/jobs/w", "company": "Y", "posted": "2026-10-01"}]}}})

            async def fake_hh(query, remote):
                assert query.startswith("бизнес аналитик OR business analyst"), query
                return [{"title": "Системный аналитик", "url": "https://hh.ru/vacancy/9", "company": "Avito",
                         "location": "Москва", "comp": "", "posted": "2026-10-02T10:00:00+03:00"}]
            guests.fetch_hh = fake_hh
            real_hima = guests.fetch_himalayas

            async def fake_hima(queries, country=""):  # the live site changes by the hour: a test must not depend on it
                return []
            guests.fetch_himalayas = fake_hima

            webapp.utils.get_peer_id = lambda entity: -1000000000777  # a real Channel would give this
            app = webapp.WebApp(FakeRadar(reg))
            app_ = app.app()
            webapp.SEARCH_COOLDOWN = 0
            async with TestClient(TestServer(app_)) as c:
                auth = {"Authorization": "tma " + sign({"id": 5, "first_name": "Мама"})}
                assert (await c.get("/api/me")).status == 401
                assert (await c.get("/api/me", headers={"Authorization": "tma " + sign({"id": 6})})).status == 403
                me = await (await c.get("/api/me", headers=auth)).json()
                assert me["name"] == "Мама" and me["stats"]["new"] == 1 and me["stats"]["channels"] == 1 and not me["onboarded"]

                jobs = await (await c.get("/api/jobs?view=new", headers=auth)).json()
                assert [j["key"] for j in jobs["items"]] == ["hh.ru/vacancy/1"] and jobs["items"][0]["tags"] == ["ru-company"]
                r = await (await c.post("/api/jobs/hh.ru%2Fvacancy%2F1/fav", headers=auth)).json()
                assert r["ok"] and r["item"]["fav"]
                # 🗄 archive: out of «Избранное» and the feed, into «Архив», and back
                r = await (await c.post("/api/jobs/hh.ru%2Fvacancy%2F1/archive", headers=auth)).json()
                assert r["ok"] and r["item"]["archived"]
                keys = lambda v: [j["key"] for j in v["items"]]
                assert keys(await (await c.get("/api/jobs?view=fav", headers=auth)).json()) == []
                assert keys(await (await c.get("/api/jobs?view=new", headers=auth)).json()) == []
                assert keys(await (await c.get("/api/jobs?view=archive", headers=auth)).json()) == ["hh.ru/vacancy/1"]
                assert (await (await c.get("/api/me", headers=auth)).json())["stats"]["archived"] == 1
                r = await (await c.post("/api/jobs/hh.ru%2Fvacancy%2F1/unarchive", headers=auth)).json()
                assert r["ok"] and not r["item"]["archived"]
                assert keys(await (await c.get("/api/jobs?view=fav", headers=auth)).json()) == ["hh.ru/vacancy/1"]
                assert (await c.post("/api/jobs/nope/take", headers=auth)).status == 404
                assert (await c.post("/api/jobs/hh.ru%2Fvacancy%2F1/explode", headers=auth)).status == 400

                # exact: only the phrase itself (any case / hyphen / ending), from inbox + channel
                s = await (await c.get("/api/search?q=бизнес аналитик&mode=exact", headers=auth)).json()
                titles = {i["title"] for i in s["items"]}
                assert "Бизнес-аналитик" in titles and not any("Системн" in t for t in titles), titles
                assert {i["match"]["kind"] for i in s["items"]} <= {"exact", "syn", "role", "loose"} and s["graph"] is None, [(i["title"], i["match"]) for i in s["items"]]
                assert s["sources"]["channels"] == 2  # the 30-day-old post is out
                # in portions: fast + chan + hh + hima = the same set as one answer, nothing twice
                got = []
                for part in ("fast", "chan", "hh", "hima"):
                    d = await (await c.get("/api/search?q=бизнес аналитик&mode=exact&part=" + part, headers=auth)).json()
                    assert d["part"] == part
                    got += [i["key"] for i in d["items"]]
                assert len(got) == len(set(got)) and set(got) == {i["key"] for i in s["items"]}, (got, [i["key"] for i in s["items"]])
                # a post that only mentions the phrase deep in its text comes last, labelled
                app.posts["datajob"][1].append({"source": "tg", "key": "1000000000777_8", "chat": "Data Jobs", "chat_id": -1, "msg_id": 8,
                                                "date": datetime.now(timezone.utc), "link": "https://t.me/datajob/8",
                                                "title": "Вакансия: Full Stack разработчик",
                                                "text": "Вакансия: Full Stack разработчик. Будете работать с бизнес-аналитиком и дизайнером, удалённо. "
                                                        "React, Node.js, PostgreSQL, зарплата по итогам собеседования."})
                s = await (await c.get("/api/search?q=бизнес аналитик&mode=exact", headers=auth)).json()
                assert s["items"][-1]["match"] == {"kind": "text", "label": "в тексте поста"}
                assert [i["match"]["kind"] for i in s["items"]].count("text") == 1
                # similar: synonyms (Business Analyst), adjacent (системный), far (Product Owner) — ranked
                s = await (await c.get("/api/search?q=бизнес аналитик&mode=similar", headers=auth)).json()
                kinds = [i["match"]["kind"] for i in s["items"]]
                assert kinds == sorted(kinds, key={"exact": 0, "syn": 1, "role": 1, "ai": 1.5, "loose": 1.8, "adj": 2, "far": 3, "text": 4}.get), kinds
                # «Business Analyst» is the same words in English, so it counts as exact now
                assert {"adj", "far"} <= set(kinds) and s["graph"]["root"]["label"] == "бизнес аналитик"
                assert any(i["title"] == "Business Analyst" and i["match"]["kind"] == "exact" for i in s["items"])
                assert not any(i["title"] == "Welder" for i in s["items"])
                # switching a role off removes it
                s2 = await (await c.get("/api/search?q=бизнес аналитик&mode=similar&off=product-manager", headers=auth)).json()
                assert "far" not in [i["match"]["kind"] for i in s2["items"]] or len(s2["items"]) < len(s["items"])
                # acting on a search result stores it
                hh_hit = next(i for i in s["items"] if i["title"] == "Системный аналитик")
                assert hh_hit["match"]["kind"] == "adj" and hh_hit["origin"] == "hh.ru"
                r = await (await c.post(f"/api/search/{hh_hit['key'].replace('/', '%2F')}/take", headers=auth)).json()
                assert r["ok"] and g.load_inbox()[hh_hit["key"]]["status"] == "taken" and g.load_inbox()[hh_hit["key"]]["via"] == "search"
                assert (await c.post("/api/search/unknown/take", headers=auth)).status == 404
                assert (await c.get("/api/roles", headers=auth)).status == 403  # reviewing Claude's roles: owner only

                # feedback: short ones refused, saved, a guest's note reaches the owner, no flooding
                webapp.FEEDBACK_PATH = Path(tempfile.mkdtemp()) / "fb.json"
                assert (await c.post("/api/feedback", headers=auth, json={"text": "a"})).status == 400
                assert (await c.post("/api/feedback", headers=auth, json={"text": "Кнопка не жмётся"})).status == 200
                assert (await c.post("/api/feedback", headers=auth, json={"text": "ещё раз сразу"})).status == 429
                assert guests.read_json(webapp.FEEDBACK_PATH, [])[0]["text"] == "Кнопка не жмётся"

                # sources
                src = await (await c.get("/api/sources", headers=auth)).json()
                assert src["channels"][0] == {"key": "datajob", "title": "Data Jobs", "on": True, "removable": True}
                assert any(x["id"] == "hh" and x["on"] for x in src["sites"])
                assert src["country"] == {"on": False, "key": "Russian Federation", "name": "Россия"}  # guests: off until they turn it on
                src = await (await c.post("/api/sources", headers=auth, json={"type": "country", "on": True, "key": "Thailand"})).json()
                assert src["country"] == {"on": True, "key": "Thailand", "name": "Таиланд"} and g.load_search()["country"]["key"] == "Thailand"
                assert (await c.post("/api/sources", headers=auth, json={"type": "country", "key": "Atlantis"})).status == 400
                await c.post("/api/sources", headers=auth, json={"type": "country", "on": False})
                src = await (await c.post("/api/sources", headers=auth, json={"type": "channels_all", "on": True})).json()
                assert all(x["on"] for x in src["channels"])
                src = await (await c.post("/api/sources", headers=auth, json={"type": "channels_all", "on": False})).json()
                assert not any(x["on"] for x in src["channels"])
                src = await (await c.post("/api/sources", headers=auth, json={"type": "sites_all", "on": True})).json()
                assert src["sites"] and all(x["on"] for x in src["sites"])
                src = await (await c.post("/api/sources", headers=auth, json={"type": "sites_all", "on": False})).json()
                assert not any(x["on"] for x in src["sites"])
                await c.post("/api/sources", headers=auth, json={"type": "channel", "key": "datajob", "on": True})
                src = await (await c.post("/api/sources", headers=auth, json={"type": "channel", "key": "datajob", "on": False})).json()
                assert not src["channels"][0]["on"]
                src = await (await c.post("/api/sources", headers=auth, json={"type": "words", "group": "role", "add": "аналитик требований"})).json()
                assert "аналитик требований" in src["words"]["role"]
                n = src["words"]["role"].index("аналитик требований")
                src = await (await c.post("/api/sources", headers=auth, json={"type": "words", "group": "role", "remove": n})).json()
                assert "аналитик требований" not in src["words"]["role"]
                src = await (await c.post("/api/sources", headers=auth, json={"type": "words", "group": "negative", "clear": True})).json()
                assert src["words"]["negative"] == [] and src["words"]["role"]  # «Сбросить» empties only that group
                src = await (await c.post("/api/sources", headers=auth, json={"type": "hh", "query": "  BA  ", "remote": False})).json()
                assert src["hh"] == {"query": "BA", "remote": False}
                assert (await c.post("/api/sources", headers=auth, json={"type": "boom"})).status == 400

                # settings
                await c.post("/api/settings", headers=auth, json={"skin": "remote", "onboarded": True})
                await c.post("/api/settings", headers=auth, json={"skin": "<script>"})
                me = await (await c.get("/api/me", headers=auth)).json()
                assert me["skin"] == "remote" and me["onboarded"]
                r1 = await (await c.post("/api/refresh", headers=auth)).json()
                assert r1["busy"] is False and r1["new"] == 0 and r1["more"] is False and r1["full"] is True  # first time: everything is looked at
                r2 = await (await c.post("/api/refresh", headers=auth)).json()
                assert r2["full"] is False  # nothing changed since: light
                # light ↻ starts a source only when its last pass is old
                calls = []

                async def fake_task(full=None):
                    calls.append(full)
                radar = app.radar
                radar.hh_task, radar.sites_task = fake_task, fake_task
                radar.hh_run = radar.sites_run = None
                r3 = await (await c.post("/api/refresh", headers=auth)).json()
                assert r3["started"] == ["hh", "sites"] and r3["more"] is True and calls == [None, None], (r3, calls)
                r4 = await (await c.post("/api/refresh", headers=auth)).json()
                assert r4["started"] == [] and r4["ages"]["hh"] == 0 and len(calls) == 2, r4  # just started: fresh

                # résumé -> preset
                assert (await (await c.get("/api/cv", headers=auth)).json()) == {"cv": None}
                form = aiohttp.FormData()
                form.add_field("file", make_docx(CV_TEXT), filename="Резюме.docx")
                cv = (await (await c.post("/api/cv", headers=auth, data=form)).json())["cv"]
                primary = [r["id"] for r in cv["roles"] if r["primary"]]
                assert primary[0] == "business-analyst" and "system-analyst" in [r["id"] for r in cv["roles"]], cv["roles"]
                assert cv["english"] == "B2" and cv["level"] == "Middle" and "Требования / BPMN" in [s["label"] for s in cv["skills"]]
                assert "sql" in cv["skill_words"] and cv["roles"][0]["words"][0] == "бизнес аналитик"
                assert '"бизнес аналитик"' in cv["roles"][0]["hh"] and (g.dir / "cv.txt").exists()
                r = await (await c.post("/api/cv/apply", headers=auth, json={"roles": ["business-analyst", "system-analyst"],
                                                                             "channels": ["datajob"], "mode": "replace"})).json()
                assert r["ok"] and r["hh_query"].startswith('"бизнес аналитик"')
                words = g.load_search()["keywords"]["role"]
                assert "бизнес аналитик" in words and "системный аналитик" in words and "data analyst" not in words, words
                assert "datajob" in g.selected(guests.load_pool()) and "hh" in g.load_search()["sites"]
                r = await c.post("/api/cv/apply", headers=auth, json={"roles": [], "mode": "merge"})
                assert r.status == 400
                for name, blob, ok in (("cv.txt", CV_TEXT.encode("utf-8"), True), ("cv.pdf", make_pdf(), True),
                                       ("photo.jpg", b"\xff\xd8\xff" + b"0" * 500, False), ("short.txt", b"hi", False)):
                    form = aiohttp.FormData()
                    form.add_field("file", blob, filename=name)
                    resp = await c.post("/api/cv", headers=auth, data=form)
                    assert (resp.status == 200) == ok, (name, resp.status, await resp.text())
                    if not ok:
                        assert (await resp.json())["error"]
                pdf_cv = (await (await c.get("/api/cv", headers=auth)).json())["cv"]
                assert pdf_cv["file"] == "cv.pdf" and pdf_cv["roles"][0]["id"] == "data-analyst", pdf_cv["roles"]
                assert (await (await c.post("/api/cv/delete", headers=auth)).json()) == {"cv": None}
                assert not (g.dir / "cv.txt").exists()

                # ▤ résumé workshop
                rs = await (await c.get("/api/resume", headers=auth)).json()
                # the three uploads above (docx, txt, pdf) each became a version
                assert [v["name"] for v in rs["versions"]] == ["Основное", "Загруженное", "Загруженное 2"], rs["versions"]
                assert rs["active"]["name"] == "Загруженное 2"
                assert rs["ai"]["allowed"] is False  # a guest without the owner's switch
                vid = rs["active"]["id"]
                blocks = rs["blocks"]
                blocks[0]["x"] = "Анна П. Петрова"
                rs = await (await c.post("/api/resume/save", headers=auth, json={"vid": vid, "blocks": blocks})).json()
                assert rs["blocks"][0] == {"t": "h1", "x": "Анна П. Петрова"} and "doctor" in rs
                assert (await c.post("/api/resume/save", headers=auth, json={"vid": vid, "markdown": "  "})).status == 400
                rs = await (await c.post("/api/resume/version", headers=auth, json={"action": "duplicate", "vid": vid})).json()
                assert rs["active"]["name"] == "Загруженное 2 (копия)" and len(rs["versions"]) == 4
                rs = await (await c.post("/api/resume/version", headers=auth, json={"action": "delete", "vid": rs["active"]["id"]})).json()
                assert len(rs["versions"]) == 3
                rs = await (await c.post("/api/resume/version", headers=auth, json={"action": "select", "vid": vid})).json()
                assert rs["active"]["id"] == vid
                assert (await c.post("/api/resume/version", headers=auth, json={"action": "select", "vid": "../../etc"})).status == 400
                assert (await c.post("/api/resume/new", headers=auth, json={"mode": "text", "text": "коротко"})).status == 400
                # builder: draft is kept between steps, then built into a new version
                d = await (await c.post("/api/resume/draft", headers=auth, json={"draft": {"lang": "ru", "name": "Анна", "title": "Бизнес-аналитик"}})).json()
                assert d["ok"] and "BPMN" in d["skills"] and d["summary"].startswith("Бизнес-аналитик")
                assert (await (await c.get("/api/resume/draft", headers=auth)).json())["draft"]["name"] == "Анна"
                rs = await (await c.post("/api/resume/build", headers=auth, json={"name": "С нуля", "draft": {
                    "lang": "ru", "name": "Анна", "title": "Бизнес-аналитик", "email": "a@x.io",
                    "jobs": [{"role": "Бизнес-аналитик", "company": "Банк", "from": "2022", "to": "наст. время",
                              "bullets": [{"action": "Описала 40 процессов", "how": "в BPMN", "result": ""}]}]}})).json()
                assert rs["active"]["name"] == "С нуля" and rs["blocks"][0]["x"] == "Анна"
                assert (await (await c.get("/api/resume/draft", headers=auth)).json())["draft"] == {}
                built_vid = rs["active"]["id"]
                # vacancy match: pasted text, and a Telegram vacancy by key
                mt = await (await c.post("/api/resume/match", headers=auth, json={"vid": built_vid, "vacancy": "Ищем бизнес-аналитика: BPMN, SQL, Jira, Confluence. " * 3})).json()
                assert "bpmn" in {x["term"].lower() for x in mt["present"]} and "sql" in {x["term"].lower() for x in mt["missing"]}
                assert (await c.post("/api/resume/match", headers=auth, json={"vid": built_vid, "vacancy": "мало"})).status == 400
                # AI: refused until the owner switches it on for this guest
                assert (await c.post("/api/resume/ai", headers=auth, json={"op": "improve", "vid": built_vid})).status == 403
                reg = guests.load_registry()
                reg["users"]["5"]["ai"] = True
                guests.save_registry(reg)
                app.ai = FakeClaude([json.dumps({"changes": [{"before": "Описала 40 процессов в BPMN", "after": "Описала в BPMN 40 бизнес-процессов банка",
                                                              "why": "конкретнее"}], "questions": ["Какой результат?"]}, ensure_ascii=False)])
                job = await (await c.post("/api/resume/ai", headers=auth, json={"op": "improve", "vid": built_vid})).json()
                for _ in range(50):
                    j = await (await c.get(f"/api/resume/ai/{job['job']}", headers=auth)).json()
                    if j["status"] != "running":
                        break
                    await asyncio.sleep(0.05)
                assert j["status"] == "done", j
                ch = j["result"]["changes"]
                assert ch[0]["after"].startswith("Описала в BPMN 40") and j["result"]["questions"] == ["Какой результат?"]
                assert (await c.get(f"/api/resume/ai/{job['job']}", headers={"Authorization": "tma " + sign({"id": 1})})).status in (403, 404)
                rs = await (await c.post("/api/resume/apply", headers=auth, json={"vid": built_vid, "changes": ch, "summary": "Бизнес-аналитик, BPMN."})).json()
                assert rs["applied"] == 2 and any(b["x"].startswith("Описала в BPMN 40") for b in rs["blocks"])
                assert any(b["x"] == "Бизнес-аналитик, BPMN." for b in rs["blocks"])
                app.ai = FakeClaude(["# Anna\n\n**Business Analyst**\n\na@x.io\n\n## Summary\n\nBusiness analyst, BPMN.\n\n## Work Experience\n\n"
                                     "### Business Analyst — Bank\n\n2022 — Present\n\n- Described 40 bank business processes in BPMN\n"])
                job = await (await c.post("/api/resume/ai", headers=auth, json={"op": "translate", "vid": built_vid, "lang": "en"})).json()
                for _ in range(50):
                    j = await (await c.get(f"/api/resume/ai/{job['job']}", headers=auth)).json()
                    if j["status"] != "running":
                        break
                    await asyncio.sleep(0.05)
                assert j["status"] == "done" and j["result"]["name"] == "English", j
                rs = await (await c.get("/api/resume", headers=auth)).json()
                assert any(v["name"] == "English" and v["lang"] == "en" for v in rs["versions"]) and rs["active"]["id"] == built_vid
                app.ai = FakeClaude([], fail="login")
                job = await (await c.post("/api/resume/ai", headers=auth, json={"op": "summary", "vid": built_vid})).json()
                await asyncio.sleep(0.1)
                j = await (await c.get(f"/api/resume/ai/{job['job']}", headers=auth)).json()
                assert j["status"] == "error" and "войти в Claude Code" in j["error"]
                # design: template, colour, photo (cropped in the app, re-checked and resized here)
                rs = await (await c.get("/api/resume", headers=auth)).json()
                assert rs["design"]["tpl"] == "classic" and 'data-i="0"' in rs["paper"]["html"] and len(rs["templates"]) == 4
                rs = await (await c.post("/api/resume/design", headers=auth, json={"vid": built_vid, "tpl": "modern", "color": "teal", "tpl_x": 1})).json()
                assert rs["design"]["tpl"] == "modern" and rs["design"]["color"] == "teal" and "#0f6a6e" in rs["paper"]["css"]
                assert (await (await c.post("/api/resume/design", headers=auth, json={"vid": built_vid, "tpl": "nope"})).json())["design"]["tpl"] == "modern"
                assert (await c.post("/api/resume/photo", headers=auth, json={"vid": built_vid, "data": "data:image/png;base64,AAAA"})).status == 400
                from io import BytesIO
                from PIL import Image
                buf = BytesIO()
                Image.new("RGB", (300, 200), (200, 80, 40)).save(buf, "PNG")
                png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
                rs = await (await c.post("/api/resume/photo", headers=auth, json={"vid": built_vid, "data": png})).json()
                assert rs["design"]["photo"] and rs["design"]["has_photo"] and 'data-photo="1"' in rs["paper"]["html"]
                assert Image.open(guests.resume_dir(app.radar.member_for(5)) / "photo.jpg").size == (400, 400)
                # export: files go to the person's chat with the bot (in the chosen template, with the photo)
                for fmt in ("md", "html", "pdf"):
                    r = await (await c.post("/api/resume/export", headers=auth, json={"vid": built_vid, "format": fmt})).json()
                    assert r == {"ok": True, "format": fmt}, r
                sent = app.radar.bot.sent
                assert [Path(p).suffix for _, p in sent] == [".md", ".html", ".pdf"] and all(to == 5 for to, _ in sent)
                assert Path(sent[2][1]).stat().st_size > 5000
                # search preset from a version without a file
                cvp = await (await c.post("/api/cv/from-resume", headers=auth, json={"vid": built_vid})).json()
                assert cvp["cv"]["roles"][0]["id"] == "business-analyst"

                # start over
                form = aiohttp.FormData()
                form.add_field("file", CV_TEXT.encode("utf-8"), filename="cv.txt")
                await c.post("/api/cv", headers=auth, data=form)
                await c.post("/api/cv/apply", headers=auth, json={"roles": ["business-analyst"], "mode": "replace"})
                assert "data analyst" not in g.load_search()["keywords"]["role"]
                assert (await c.post("/api/reset", headers=auth, json={})).status == 400  # no confirmation, nothing happens
                assert "data analyst" not in g.load_search()["keywords"]["role"]
                jobs_before = len(g.load_inbox())
                assert (await (await c.post("/api/reset", headers=auth, json={"confirm": "reset"})).json()) == {"ok": True}
                assert g.load_search()["keywords"]["role"] == guests.load_preset()["role"]
                assert not (g.dir / "cv.txt").exists() and "cv" not in g.load_state() and "skin" not in g.load_state()
                assert len(g.load_inbox()) == jobs_before > 0  # vacancies kept unless asked
                me = await (await c.get("/api/me", headers=auth)).json()
                assert not me["onboarded"] and me["skin"] is None
                await c.post("/api/reset", headers=auth, json={"confirm": "reset", "wipe_jobs": True})
                assert g.load_inbox() == {}

                page = await c.get("/")
                assert page.status == 200 and "{{v}}" not in await page.text()
                assert (await c.get("/static/app.js")).status == 200
                assert (await c.get("/static/../config.yml")).status in (403, 404)
        finally:
            guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, webapp.FEED_CACHE_PATH, guests.fetch_hh = saved
            guests.fetch_himalayas = real_hima


asyncio.run(main())
print("ok")
