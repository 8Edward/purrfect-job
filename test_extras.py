"""Offline tests for extras.py and its app endpoints. Run: python telegram/test_extras.py -> ok

Temp folder, fake bot and fake Claude: no Telegram, no network, real data untouched."""

import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import bot  # noqa: E402
import extras  # noqa: E402
import guests  # noqa: E402
import resume  # noqa: E402
import webapp  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
from urllib.parse import urlencode  # noqa: E402

TOKEN = "123:TEST"


def sign(user):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


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
    def __init__(self):
        self.cfg = {"bot_token": TOKEN}
        self.lock = asyncio.Lock()
        self.user = None

    def member_for(self, uid):
        return guests.get(guests.load_registry(), uid)

    def minutes_to_next_check(self):
        return 5

    def reload(self):
        pass

# -- 💰 salary
assert extras.salary_in("Зарплата: 200 000 – 250 000 руб.").startswith("200 000 – 250 000")
assert extras.salary_in("ЗП от 150к") == "от 150к"
assert extras.salary_in("Pay: $3000-4000/month") == "$3000-4000/month"
assert extras.salary_in("Salary 120k-150k USD") == "120k-150k USD"
assert extras.salary_in("до 300 000 ₽ на руки") == "до 300 000 ₽"
assert extras.salary_in("5+ years of experience, since 2024, team of 12") == ""
assert extras.salary_in("Опыт от 3 лет") == ""
assert extras.salary_of({"comp": "не указана", "text": "Оклад 180 000"}) == "180 000"
assert extras.salary_of({"comp": "от 100 000 ₽", "text": ""}) == "от 100 000 ₽"

# -- 📰 articles, digests and advice posts are not vacancies
import tg_collect as tc  # noqa: E402
ARTICLE = ("В какие профессии податься до 2030-го года?\n\nСамая высокооплачиваемая профессия — сварщик. Медианный оффер — "
           "270 тысяч рублей (ссылка). 1. Инженер-технолог. Медианная зарплата 250к, 1-2 резюме на вакансию.")
DIGEST = "Подборка вакансий недели для аналитиков 🔥\n1. Data Analyst в Ozon\n2. BI-аналитик в Avito\n3. Аналитик в Яндекс\nТоп-10 лучших вакансий читать по ссылке в описании"
OFFER = ("#вакансия Data Analyst в Uzum, удалёнка. Обязанности: дашборды, SQL. Требования: SQL, Python, 2+ года. "
         "Мы предлагаем: зарплата 3000$, гибкий график. Резюме присылайте @uzum_hr. По данным hh мы в топ-10 работодателей.")
assert tc.looks_like_article(ARTICLE) and tc.looks_like_article(DIGEST) and not tc.looks_like_article(OFFER)
flt = tc.JobFilter({"job": ["ваканси", "зарплат"], "role": ["аналитик", "analyst"]}, 50)
assert not flt.is_job(ARTICLE + " аналитик") and not flt.is_job_like(DIGEST) and flt.is_job(OFFER)

# -- 🕒 time zones
assert extras.tz_offsets("Remote, US only") == [-8, -7, -6, -5, -4, -3]
assert extras.tz_offsets("overlap with EST hours") == [-5]
assert extras.tz_offsets("Работа 10:00-19:00 мск") == [3]
assert extras.tz_offsets("UTC+7 ±2") == [7] and extras.tz_offsets("GMT-5 to GMT-3") == [-5, -3]
assert extras.tz_offsets("Join us based in a friendly team") == [] and extras.tz_offsets("any UTC") == []
assert extras.tz_label({"text": "CET hours"}) == "UTC+1"
tz = {"on": True, "mine": 7, "spread": 4}
assert extras.tz_fits({"text": "US only"}, tz) is False
assert extras.tz_fits({"text": "Europe time zones only"}, tz) is True          # UTC+3 is 4 h away
assert extras.tz_fits({"text": "no time zone said"}, tz) is True
assert extras.tz_fits({"text": "US only"}, dict(tz, on=False)) is True
assert extras.tz_gap(-11, 12) == 1

# -- 🔗 duplicates
now = datetime.now(timezone.utc)
a = {"source": "web", "title": "Data Analyst", "company": "Uzum", "link": "https://a/1", "site": "Himalayas", "date": now.isoformat()}
b = {"source": "web", "title": "Data analyst", "company": "UZUM", "link": "https://b/2", "site": "hh.ru", "comp": "$3000", "date": now.isoformat()}
post = "Ищем аналитика данных в команду, удалёнка, SQL, Python, полный день, хорошие условия, пишите в личку " * 2
c = {"source": "tg", "title": "Ищем аналитика", "chat": "datajob", "text": post, "date": now.isoformat()}
d = {"source": "tg", "title": "Ищем аналитика", "chat": "remoteit", "text": post, "date": now.isoformat()}
e = {"source": "web", "title": "Welder", "company": "Uzum", "link": "https://c/3", "date": now.isoformat()}
gs = extras.groups([("a", a), ("b", b), ("c", c), ("d", d), ("e", e)])
assert [[k for k, _ in g] for g in gs] == [["b", "a"], ["c", "d"], ["e"]]   # richest first (has pay)
assert sorted(extras.twins_of({"a": a, "b": b, "e": e}, "a")) == ["b"]

# -- 📋 tracker
item = {"title": "BA", "status": "taken"}
extras.set_stage(item, "applied")
assert item["track"]["stage"] == "applied" and len(item["track"]["log"]) == 1
extras.set_stage(item, "applied", note="написал HR")
assert len(item["track"]["log"]) == 1 and item["track"]["note"] == "написал HR"
item["track"]["at"] = (now - timedelta(days=8)).isoformat()
assert extras.silent_days(item) == 8 and [k for k, _ in extras.due_reminders({"x": item})] == ["x"]
item["track"]["reminded"] = item["track"]["at"]
assert extras.due_reminders({"x": item}) == []
extras.set_stage(item, "offer")
assert extras.silent_days(item) == 0 and "reminded" not in item["track"]
extras.set_stage(item, None)
assert "track" not in item
try:
    extras.set_stage(item, "hired")
    raise AssertionError("unknown stage accepted")
except ValueError:
    pass
key, manual = extras.manual_item("Аналитик", "Kaspi", "javascript:alert(1)")
assert key.startswith("manual_") and manual["link"] == "" and manual["status"] == "taken"

# -- ✨ AI answers
s = extras.parse_score('```json\n{"score": 7, "verdict": "Хорошо", "pros": ["SQL"], "cons": []}\n```')
assert s["score"] == 5.0 and s["pros"] == ["SQL"]
md = "# Анна\n\n## Опыт работы\n\n### Аналитик — Банк\n\n- SQL-отчёты для 3 отделов\n" + "- Строила отчёты в Excel\n" * 20
p = extras.parse_plan(json.dumps({"now": "Аналитик с 10 годами опыта в Tableau", "strengths": ["SQL-отчёты для 3 отделов", "Руководила 50 людьми"],
                                  "gaps": ["Python"], "steps": [{"title": "Выучить Python", "what": "курс", "weeks": "4 нед."}]}, ensure_ascii=False), md)
assert p["now"] == "" and p["strengths"] == ["SQL-отчёты для 3 отделов"] and p["dropped"] == 2 and p["steps"][0]["title"] == "Выучить Python"

# -- ☀️ digest text in the chat
items = [("k", {"title": "Data Analyst", "company": "Uzum", "link": "https://x/1", "comp": "$3000", "ai": {"score": 4.4},
                "source": "web", "site": "Himalayas", "date": now.isoformat(), "status": "new"})]
caption, rows = bot.JobRadar.digest_screen(SimpleNamespace(app_url=None), items, 3)
assert "Доброе утро" in caption and "✨ 4.4/5" in caption and "💰 $3000" in caption and "Всего новых за сутки: 3" in caption
caption, _ = bot.JobRadar.reminder_screen(SimpleNamespace(app_url="https://x"), [("k", dict(item, track={"stage": "test", "at": (now - timedelta(days=9)).isoformat()}))])
assert "тестовое, 9 дн." in caption


async def wait(c, auth, job):
    for _ in range(80):
        j = await (await c.get(f"/api/resume/ai/{job}", headers=auth)).json()
        if j["status"] != "running":
            return j
        await asyncio.sleep(0.05)
    raise AssertionError("job hangs")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        saved = guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, webapp.FEED_CACHE_PATH
        guests.USERS_DIR = Path(tmp)
        guests.REGISTRY_PATH = Path(tmp) / "users.json"
        guests.POOL_PATH = Path(tmp) / "pool.json"
        webapp.FEED_CACHE_PATH = Path(tmp) / "feed.json"
        try:
            guests.save_pool({"channels": {}, "adds": {}, "proposals": {}})
            reg = guests.load_registry()

            class U:
                id, username, first_name, last_name = 5, "mama", "Мама", None
            g = guests.register(reg, U())
            reg["users"]["5"]["ai"] = True
            guests.save_registry(reg)
            g = guests.get(reg, 5)
            g.update_state(step=None)
            fresh = (now - timedelta(hours=2)).isoformat()
            g.save_inbox({
                "a": dict(a, status="new", fav=False, tags=[], date=fresh),
                "b": dict(b, status="new", fav=False, tags=[], date=fresh, text="Remote, Europe time zones only"),
                "us": {"source": "web", "title": "BI Analyst", "company": "Acme", "link": "https://us/1", "site": "X",
                       "text": "Remote, US only", "status": "new", "fav": False, "tags": [], "date": fresh},
            })
            app = webapp.WebApp(FakeRadar())
            app.warm = lambda m: None
            async with TestClient(TestServer(app.app())) as c:
                auth = {"Authorization": "tma " + sign({"id": 5, "first_name": "Мама"})}
                feed = await (await c.get("/api/jobs?view=new", headers=auth)).json()
                assert len(feed["items"]) == 2 and feed["tz_hidden"] == 0
                card = next(i for i in feed["items"] if i["key"] == "b")
                assert card["also"][0]["key"] == "a" and card["salary"] == "$3000" and card["tz"].startswith("UTC+0")
                me = await (await c.get("/api/me", headers=auth)).json()
                assert me["stats"]["new"] == 2 and me["ai"] is True and me["digest"] is True

                # 🕒 time zone filter: on, UTC+7 ±4 -> the US-only job leaves the feed
                src = await (await c.post("/api/sources", headers=auth, json={"type": "tz", "on": True, "mine": 7, "spread": 4})).json()
                assert src["tz"] == {"on": True, "mine": 7.0, "spread": 4}
                feed = await (await c.get("/api/jobs?view=new", headers=auth)).json()
                assert [i["key"] for i in feed["items"]] == ["b"] and feed["tz_hidden"] == 1
                assert (await c.post("/api/sources", headers=auth, json={"type": "tz", "mine": "x"})).status == 400

                # ☀️ digest: top of the last day, filtered and merged
                top, total = extras.digest_items(g, g.load_inbox())
                assert [k for k, _ in top] == ["b"] and total == 1
                assert extras.digest_due(g, {"digest_date": None}) == (9 <= extras.local_now(g).hour < 23)
                assert not extras.digest_due(g, {"digest_off": True})
                await c.post("/api/settings", headers=auth, json={"digest": False})
                assert g.load_state()["digest_off"] is True

                # 🔗 taking a merged card takes its twin too
                r = await (await c.post("/api/jobs/b/take", headers=auth, json={})).json()
                assert r["ok"] and g.load_inbox()["a"]["status"] == "taken"

                # ✨ score: needs a résumé first
                r = await c.post("/api/score", headers=auth, json={"key": "us"})
                assert r.status == 400 and "резюме" in (await r.json())["error"]
                resume.save_version(g, "# Мама\n\n## Опыт работы\n\n### Аналитик — Банк\n\n" + "- Писала SQL-запросы к PostgreSQL\n" * 12)
                app.ai = FakeClaude([json.dumps({"score": 3.6, "verdict": "Неплохо, но только США", "pros": ["SQL"], "cons": ["US only"]},
                                                ensure_ascii=False)])
                job = await (await c.post("/api/score", headers=auth, json={"key": "us"})).json()
                j = await wait(c, auth, job["job"])
                assert j["status"] == "done" and j["result"]["score"] == 3.6 and j["result"]["thin"] is True  # no text to fetch
                assert g.load_inbox()["us"]["ai"]["verdict"] == "Неплохо, но только США"
                assert g.load_state()["ai_scores"]["n"] == 1
                assert (await c.post("/api/score", headers=auth, json={"key": "nope"})).status == 404
                app.ai = FakeClaude([], fail="login")
                j = await wait(c, auth, (await (await c.post("/api/score", headers=auth, json={"key": "a"})).json())["job"])
                assert j["status"] == "error" and "войти" in j["error"]

                # 📋 tracker
                r = await (await c.post("/api/track", headers=auth, json={"key": "us", "stage": "applied"})).json()
                assert r["item"]["track"]["stage"] == "applied" and r["item"]["status"] == "taken"
                assert (await c.post("/api/track", headers=auth, json={"key": "us", "stage": "hired"})).status == 400
                r = await (await c.post("/api/track/add", headers=auth, json={"title": "Аналитик", "company": "Kaspi", "stage": "interview"})).json()
                assert r["item"]["track"]["stage"] == "interview" and r["item"]["origin"] == "вручную"
                assert (await c.post("/api/track/add", headers=auth, json={"title": ""})).status == 400
                t = await (await c.get("/api/track", headers=auth)).json()
                assert {i["track"]["stage"] for i in t["items"]} == {"applied", "interview"}
                assert [i["key"] for i in t["taken"]] == ["b"] and t["taken"][0]["also"][0]["key"] == "a"  # twins once
                await c.post("/api/track", headers=auth, json={"key": "us", "stage": ""})
                assert "track" not in g.load_inbox()["us"]

                # 🧭 career plan
                assert (await c.post("/api/plan", headers=auth, json={"goal": "role"})).status == 400   # no target
                app.ai = FakeClaude([json.dumps({"now": "Аналитик в банке, пишет SQL-запросы к PostgreSQL", "target": "Product Analyst",
                                                 "strengths": ["SQL"], "gaps": ["A/B-тесты"],
                                                 "steps": [{"title": "Изучить A/B-тесты", "what": "курс + пет-проект", "weeks": "3–4 нед."}],
                                                 "resume": [], "questions": ["Сколько лет опыта?"]}, ensure_ascii=False)])
                job = await (await c.post("/api/plan", headers=auth, json={"goal": "role", "target": "Product Analyst"})).json()
                j = await wait(c, auth, job["job"])
                assert j["status"] == "done" and j["result"]["now"].startswith("Аналитик в банке"), j
                saved_plan = (await (await c.get("/api/plan", headers=auth)).json())["plan"]
                assert saved_plan["goal"] == "role" and saved_plan["steps"][0]["weeks"] == "3–4 нед."

                # someone without AI
                reg = guests.load_registry()
                reg["users"]["5"]["ai"] = False
                guests.save_registry(reg)
                assert (await c.post("/api/score", headers=auth, json={"key": "us"})).status == 403
                assert (await c.post("/api/plan", headers=auth, json={"goal": "grade"})).status == 403
        finally:
            guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, webapp.FEED_CACHE_PATH = saved


asyncio.run(main())
time.sleep(0)
print("ok")
