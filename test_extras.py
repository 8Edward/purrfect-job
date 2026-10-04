"""Offline tests for extras.py and its app endpoints. Run: python telegram/test_extras.py -> ok

Temp folder, fake bot and fake Claude: no Telegram, no network, real data untouched."""

import asyncio
import json
import os
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
import filtercheck  # noqa: E402
import reality  # noqa: E402
import sourcestats  # noqa: E402

_scratch = Path(tempfile.mkdtemp())  # the bot's counters must not get the test sources
sourcestats.PATH, filtercheck.PATH, reality.PATH = _scratch / "ss.json", _scratch / "fc.json", _scratch / "rl.json"
import tierb  # noqa: E402
tierb.SEEN_PATH = _scratch / "tb.json"
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

# -- newcomers start with an empty feed setup
empty = guests.preset_search()
assert empty["keywords"]["role"] == [] and empty["keywords"]["negative"] == [] and empty["pool"] == [] and empty["sites"] == []
assert "hh" not in empty and empty["keywords"]["job"] and "ru-company" in empty["keywords"]["tags"]

# -- hh.ru PDF export read right: wrapped lines, «Опыт работы — 4 года», «Обо мне …» on one line, phone kept
HH = """Иванов Иван
Мужчина, 24 года, родился 2 октября 2001
+7 (999) 1234567 — предпочитаемый способ связи
ivan@mail.ru
Опыт работы — 4 года 2 месяца
Март 2023 —
настоящее время
3 года 1 месяц
Robosculptor
Robotics Software Engineer
- Разработка модулей планирования пути для роботов Han's и
AUBO
- Внедрение ПО на базе Docker Compose, сократил время
развёртывания с 2 часов до 10 минут
Образование
2025 МГТУ им. Баумана
Навыки
Знание языков Русский — Родной
Навыки Python SQL Git Linux ROS
Дополнительная информация
Обо мне Увлекаюсь робототехникой с 7 класса, быстро учусь новому, веду командные проекты.
Иванов Иван • Резюме обновлено 7 июня 2025 в 13:33"""
md = resume.from_text(resume.clean_export(HH)[0])
assert "+7 (999) 1234567" in md and "Резюме обновлено" not in md
assert "- Разработка модулей планирования пути для роботов Han's и AUBO" in md
assert "с 2 часов до 10 минут" in md.split("сократил время")[1][:40]
assert "Март 2023 — настоящее время" in md and "### Robosculptor" in md
keys = [k for k, _, _ in resume.sections(md)]
assert keys == ["head", "experience", "education", "languages", "skills", "summary"], keys
doc = resume.doctor(md)
ids = {r["id"] for r in doc["remarks"]}
assert not ids & {"summary", "experience", "skills"} and "personal" in ids, ids
intro = "# Анна\n\nanna@mail.ru | Москва\n\n" + "Аналитик данных: SQL, Python, витрины и дашборды для бизнеса, качество данных. " * 3 + "\n\n## Опыт работы\n\n- Строила отчёты\n"
assert "summary" not in {r["id"] for r in resume.doctor(intro)["remarks"]}  # «о себе» without a heading counts

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

# -- 🌏 which countries a vacancy hires from
hf = lambda loc: extras.hires_from({"location": loc}, "Thailand")
assert hf("Remote - US") is False and hf("Remote (EMEA)") is False and hf("Europe") is False
assert hf("Remote, Thailand") is True and hf("APAC") is True and hf("Worldwide") is True and hf("Remote - Asia") is True
assert hf("San Francisco, CA") is None and hf("") is None and hf("Москва") is None
assert hf("Удалённо · Россия") is None  # Russian sites name the company's country, not who they hire
assert extras.hires_from({"countries": [], "location": "Worldwide"}, "Thailand") is True
assert extras.hires_from({"countries": ["Germany", "France"]}, "Thailand") is False
assert extras.hires_from({"countries": ["Germany", "Thailand"]}, "Thailand") is True
cs = {"on": True, "key": "Thailand"}
assert extras.country_fits({"location": "US only"}, cs) is False and extras.country_fits({"location": "US only"}, dict(cs, on=False))
assert extras.country_fits({"text": "no location"}, cs)

# -- tier B: remoteintech companies remembered when they reach a feed
tierb._names = {"gitlab": "GitLab", "doist": "Doist"}
assert tierb.is_tierb("GitLab") and tierb.is_tierb("git lab") and not tierb.is_tierb("Yandex") and not tierb.is_tierb("")
assert tierb.remember([{"company": "GitLab"}, {"company": "Yandex"}, {"company": "GITLAB"}]) == 2
assert tierb.load_seen()["GitLab"]["n"] == 2 and "Yandex" not in tierb.load_seen()
tierb._names = None

# -- 🗓 age
_n = datetime.now(timezone.utc)
assert extras.is_old({"date": (_n - timedelta(days=15)).isoformat()}) and not extras.is_old({"date": (_n - timedelta(days=13)).isoformat()})
assert not extras.is_old({}) and not extras.is_old({"date": "junk"})
_me = SimpleNamespace(is_owner=True, load_search=lambda: {})
assert _n - timedelta(days=1) <= extras.age_cutoff(_me, 1) <= _n and abs((_n - extras.age_cutoff(_me, 7)).days - 7) <= 0

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
# same company + title on ONE site: separate openings unless the texts match ≥95% (freehire jobhash port)
p1 = {"source": "web", "title": "Data Analyst", "company": "Plata", "link": "https://p/1", "site": "Plata", "text": "credit risk models " * 5}
p2 = {"source": "web", "title": "Data Analyst", "company": "Plata", "link": "https://p/2", "site": "Plata", "text": "marketing attribution funnels " * 5}
p3 = {"source": "web", "title": "Data Analyst, Mexico City", "company": "Plata", "link": "https://p/3", "site": "Plata", "text": "credit risk models " * 5}
p4 = {"source": "web", "title": "Data Analyst", "company": "Plata", "link": "https://p/4", "site": "Plata"}
p5 = {"source": "web", "title": "Data Analyst (Remote)", "company": "Plata", "link": "https://hh/5", "site": "hh.ru"}
assert extras.role_title("Data Analyst, Mexico City") == "data analyst" and extras.role_title("Analyst, Berlin") == "analyst berlin"
assert extras.role_title("Data Analyst (Remote)") == "data analyst remote" and extras.role_title("Front-end Developer") == "front end developer"
gs = [sorted(k for k, _ in g) for g in extras.groups([("1", p1), ("2", p2), ("3", p3), ("4", p4)])]
assert sorted(gs) == [["1", "3"], ["2"], ["4"]], gs                # empty text never merges within a site
assert extras.desc_similarity(set(), set()) == 0
gs = [sorted(k for k, _ in g) for g in extras.groups([("1", p1), ("6", dict(p5, title="Data Analyst"))])]
assert gs == [["1", "6"]]                                           # another site: one role cross-posted

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
            g.set_sites(list(guests.listed_sites()))  # the feed shows only ticked sites
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
                # the feed follows today's settings: a new stop word hides stored vacancies, removing it brings them back
                s = g.load_search()
                s.setdefault("keywords", {})["negative"] = ["uzum"]
                g.save_search(s)
                hidden = await (await c.get("/api/jobs?view=new", headers=auth)).json()
                assert [i["key"] for i in hidden["items"]] == ["us"]
                s["keywords"]["negative"] = []
                g.save_search(s)
                assert len((await (await c.get("/api/jobs?view=new", headers=auth)).json())["items"]) == 2
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
                assert not extras.digest_due(g, {"digest_date": None})  # switched off for now
                extras.DIGEST_ENABLED = True
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

                # 📊 funnel, pages, sorting, chips, «Оценить новые»
                me = await (await c.get("/api/me", headers=auth)).json()
                assert me["funnel"]["applied"] >= 1 and me["funnel"]["interview"] >= 1 and me["funnel"]["jobs"] >= 3, me["funnel"]
                inbox = g.load_inbox()
                for n in range(7):
                    inbox[f"p{n}"] = {"source": "web", "title": f"Analyst {n}", "company": f"Co{n}", "link": f"https://p/{n}", "site": "X",
                                      "text": "", "comp": f"${(n + 1) * 1000}", "status": "new", "fav": False, "tags": [],
                                      "date": (now - timedelta(minutes=n)).isoformat()}
                g.save_inbox(inbox)
                await c.post("/api/sources", headers=auth, json={"type": "tz", "on": False})
                p1 = await (await c.get("/api/jobs?view=new&limit=3&offset=0", headers=auth)).json()
                p2 = await (await c.get("/api/jobs?view=new&limit=3&offset=3", headers=auth)).json()
                assert len(p1["items"]) == 3 and p1["total"] == 7 and not {i["key"] for i in p1["items"]} & {i["key"] for i in p2["items"]}
                top = await (await c.get("/api/jobs?view=new&sort=salary&limit=2", headers=auth)).json()
                assert [i["key"] for i in top["items"]] == ["p6", "p5"]
                assert (await (await c.get("/api/jobs?view=new&f=hh", headers=auth)).json())["total"] == 0
                assert p1["unscored"] == 7
                reg = guests.load_registry()
                reg["users"]["5"]["ai"] = True
                guests.save_registry(reg)
                app.ai = FakeClaude([json.dumps({"score": 4.0, "verdict": "ok", "pros": [], "cons": []})] * 7)
                job = await (await c.post("/api/score/new", headers=auth, json={})).json()
                assert job["total"] == 7
                j = await wait(c, auth, job["job"])
                assert j["status"] == "done" and j["result"]["scored"] == 7 and j["progress"]["done"] == 7, j
                assert (await (await c.get("/api/jobs?view=new", headers=auth)).json())["unscored"] == 0
                assert (await c.post("/api/score/new", headers=auth, json={})).status == 400  # nothing left: no tokens spent
                assert extras.summary({"title": "Data Analyst", "text": "Удалёнка. Задачи: строить отчёты в Power BI; SQL"})[0] == "🛠 SQL, Power BI"

                # 📎 original file, 📡 radar settings
                assert (await (await c.get("/api/resume/original", headers=auth)).json())["original"] is None
                extras.save_original(g, "cv.pdf", b"%PDF-1.4 fake", "Иван Иванов\nАналитик")
                o = await (await c.get("/api/resume/original", headers=auth)).json()
                assert o["original"]["file"] == "cv.pdf" and o["text"].startswith("Иван")
                await c.post("/api/settings", headers=auth, json={"alerts": False, "interval": 60})
                me = await (await c.get("/api/me", headers=auth)).json()
                assert me["alerts"] is False and me["interval"] == 15  # a guest can't change how often the radar looks

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

                # 🔁 résumé role chips sync the feed: their words swap, typed words stay
                s = g.load_search()
                s.setdefault("keywords", {})["role"] = ["моё слово"]
                g.save_search(s)
                r1 = await (await c.post("/api/cv/apply", headers=auth, json={"roles": ["data-analyst"], "channels": [], "mode": "sync", "skill_words": False})).json()
                words1 = g.load_search()["keywords"]["role"]
                assert "моё слово" in words1 and any("analyst" in w for w in words1) and r1["hh_query"]
                await c.post("/api/cv/apply", headers=auth, json={"roles": ["data-engineer"], "channels": [], "mode": "sync", "skill_words": False})
                words2 = g.load_search()["keywords"]["role"]
                assert "моё слово" in words2 and not any("analyst" in w for w in words2) and any("engineer" in w for w in words2), words2

                # ✉️ «Откликнуться» in dry mode: draft, «send» (nothing leaves), tracker marked
                import apply
                os.environ["JR_APPLY_DRY"] = "1"
                apply.resume_pdf = lambda m, vid: asyncio.sleep(0, None)
                app.owner_only = app.member
                inbox = g.load_inbox()
                inbox["mail1"] = {"source": "tg", "title": "Data Analyst", "chat": "datajob", "status": "new", "fav": False, "tags": [],
                                  "date": now.isoformat(), "text": "Ищем аналитика данных, SQL. Резюме на hr@uzum.com или @hr_uzum."}
                g.save_inbox(inbox)
                app.ai = FakeClaude([json.dumps({"text": "Здравствуйте! Я аналитик данных, пишу SQL-запросы к PostgreSQL. " * 3})])
                job = await (await c.post("/api/apply/prepare", headers=auth, json={"key": "mail1"})).json()
                j = await wait(c, auth, job["job"])
                assert j["status"] == "done", j
                r = j["result"]
                assert r["channels"]["email"] == ["hr@uzum.com"] and r["channels"]["tg"] == ["hr_uzum"] and r["dry"] and r["lang"] == "ru"
                assert (await c.post("/api/apply/send", headers=auth, json={"key": "mail1", "to": "nope", "subject": "x", "text": r["letter"]})).status == 400
                s = await (await c.post("/api/apply/send", headers=auth, json={"key": "mail1", "to": "hr@uzum.com", "subject": r["subject"],
                                                                                "text": r["letter"], "vid": r["resume"]["vid"]})).json()
                assert s["result"] == "dry" and s["item"]["track"]["stage"] == "applied" and s["item"]["fav"]
                # ✉️ batch: drafts for starred vacancies without a response, one confirmation, never twice
                apply.BATCH_PAUSE = 0
                inbox = g.load_inbox()
                for n in range(2):
                    inbox[f"fav{n}"] = {"source": "web", "title": f"BI Analyst {n}", "company": "Co", "site": "X", "link": f"https://x/{n}",
                                        "status": "taken", "fav": True, "tags": [], "date": now.isoformat(),
                                        "text": f"BI analyst, SQL. Send CV to jobs{n}@co.com" if n == 0 else "BI analyst, SQL. Apply on the site."}
                g.save_inbox(inbox)
                app.ai = FakeClaude([json.dumps({"text": "Hello! I am a data analyst writing SQL queries to PostgreSQL. " * 3})] * 2)
                job = await (await c.post("/api/apply/batch", headers=auth, json={"keys": ["fav0", "fav1", "mail1"]})).json()
                assert job["total"] == 2  # mail1 already has a response
                j = await wait(c, auth, job["job"])
                assert j["status"] == "done" and len(j["result"]["drafts"]) == 2, j
                d0, d1 = j["result"]["drafts"]
                items = [{"key": d["key"], "to": (d["channels"]["email"] or [""])[0], "subject": d["subject"], "text": d["letter"],
                          "vid": d["resume"]["vid"]} for d in (d0, d1)]
                rep = (await (await c.post("/api/apply/batch/send", headers=auth, json={"items": items})).json())["report"]
                assert [r["result"] for r in rep] == ["dry", "manual"], rep
                assert g.load_inbox()["fav0"]["track"]["stage"] == "applied" and "track" not in g.load_inbox()["fav1"]
                rep = (await (await c.post("/api/apply/batch/send", headers=auth, json={"items": items[:1]})).json())["report"]
                assert rep[0]["result"] == "already"
                del os.environ["JR_APPLY_DRY"]

                # 🗑 deleting the last résumé version sticks (it used to come back from cv.txt)
                import cv_reader
                cv_reader.cv_path(g).parent.mkdir(parents=True, exist_ok=True)
                cv_reader.cv_path(g).write_text("Иван Иванов\nАналитик данных\n" + "Писал SQL-запросы для отчётов. " * 10, encoding="utf-8")
                for v in resume.load_index(g)["list"]:
                    await c.post("/api/resume/version", headers=auth, json={"action": "delete", "vid": v["id"]})
                left = await (await c.get("/api/resume", headers=auth)).json()
                assert left["versions"] == [] and left["active"] is None, left.get("versions")

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

# -- 📊 source counters: failed ≠ empty ≠ gone quiet
with tempfile.TemporaryDirectory() as tmp:
    sourcestats.PATH = Path(tmp) / "stats.json"
    sourcestats.record("site:a", "A", True, 50)
    sourcestats.record("site:b", "B", False, 0, "HTTP 403")
    sourcestats.record("site:c", "C", True, 0)
    rep = {r["id"]: r for r in sourcestats.report()}
    assert rep["site:a"]["state"] == "ok" and rep["site:b"]["state"] == "fail" and "403" in rep["site:b"]["label"]
    assert rep["site:c"]["state"] == "empty" and [r["id"] for r in sourcestats.report()][0] == "site:b"
    data = sourcestats.load()
    day = lambda n: (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()  # noqa: E731
    data["site:c"]["days"] = {day(5): {"ok": 3, "fail": 0, "max": 40, "sum": 40},
                              **{day(n): {"ok": 3, "fail": 0, "max": 0, "sum": 0} for n in (2, 1, 0)}}
    sourcestats.save(data)
    assert sourcestats.status(sourcestats.load()["site:c"])["state"] == "quiet"
    assert [r["id"] for r in sourcestats.report({"site:a"})] == ["site:a"]
print("ok")
