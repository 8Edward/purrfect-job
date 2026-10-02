"""Offline tests for the mini app server (webapp.py). Run: python telegram/test_webapp.py -> ok

Uses a temp folder and a fake bot: no Telegram, no network, real data untouched."""

import asyncio
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
graph = webapp.load_graph()
assert webapp.find_role(graph, "Бизнес-аналитик") == "business-analyst"
assert webapp.find_role(graph, "senior data analyst remote") == "data-analyst"
assert webapp.find_role(graph, "водолаз") is None
view = webapp.graph_view(graph, "business-analyst", "бизнес аналитик")
kinds = {n["id"]: n["kind"] for n in view["nodes"]}
assert kinds["system-analyst"] == "adj" and kinds["product-manager"] == "far" and "syn:1" in kinds
assert "business-analyst" in graph["system-analyst"]["links"]  # links work both ways


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


class FakeRadar:
    def __init__(self, reg):
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
                assert '"бизнес аналитик"' in query
                return [{"title": "Системный аналитик", "url": "https://hh.ru/vacancy/9", "company": "Avito",
                         "location": "Москва", "comp": "", "posted": "2026-10-02T10:00:00+03:00"}]
            guests.fetch_hh = fake_hh

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
                assert (await c.post("/api/jobs/nope/take", headers=auth)).status == 404
                assert (await c.post("/api/jobs/hh.ru%2Fvacancy%2F1/explode", headers=auth)).status == 400

                # exact: only the phrase itself (any case / hyphen / ending), from inbox + channel
                s = await (await c.get("/api/search?q=бизнес аналитик&mode=exact", headers=auth)).json()
                titles = {i["title"] for i in s["items"]}
                assert "Бизнес-аналитик" in titles and not any("Системн" in t for t in titles), titles
                assert all(i["match"]["kind"] == "exact" for i in s["items"]) and s["graph"] is None
                assert s["sources"]["channels"] == 2  # the 30-day-old post is out
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
                assert kinds == sorted(kinds, key=["exact", "syn", "adj", "far", "text"].index), kinds
                assert {"syn", "adj", "far"} <= set(kinds) and s["graph"]["root"]["label"] == "бизнес аналитик"
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

                # sources
                src = await (await c.get("/api/sources", headers=auth)).json()
                assert src["channels"][0] == {"key": "datajob", "title": "Data Jobs", "on": True, "removable": True}
                assert any(x["id"] == "hh" and x["on"] for x in src["sites"])
                src = await (await c.post("/api/sources", headers=auth, json={"type": "channel", "key": "datajob", "on": False})).json()
                assert not src["channels"][0]["on"]
                src = await (await c.post("/api/sources", headers=auth, json={"type": "words", "group": "role", "add": "аналитик требований"})).json()
                assert "аналитик требований" in src["words"]["role"]
                n = src["words"]["role"].index("аналитик требований")
                src = await (await c.post("/api/sources", headers=auth, json={"type": "words", "group": "role", "remove": n})).json()
                assert "аналитик требований" not in src["words"]["role"]
                src = await (await c.post("/api/sources", headers=auth, json={"type": "hh", "query": "  BA  ", "remote": False})).json()
                assert src["hh"] == {"query": "BA", "remote": False}
                assert (await c.post("/api/sources", headers=auth, json={"type": "boom"})).status == 400

                # settings
                await c.post("/api/settings", headers=auth, json={"skin": "tropic", "onboarded": True})
                await c.post("/api/settings", headers=auth, json={"skin": "<script>"})
                me = await (await c.get("/api/me", headers=auth)).json()
                assert me["skin"] == "tropic" and me["onboarded"]
                assert (await (await c.post("/api/refresh", headers=auth)).json()) == {"busy": False, "new": 0}

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

                page = await c.get("/")
                assert page.status == 200 and "{{v}}" not in await page.text()
                assert (await c.get("/static/app.js")).status == 200
                assert (await c.get("/static/../config.yml")).status in (403, 404)
        finally:
            guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, webapp.FEED_CACHE_PATH, guests.fetch_hh = saved


asyncio.run(main())
print("ok")
