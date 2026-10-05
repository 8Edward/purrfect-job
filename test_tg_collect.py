"""Offline checks for the job filter and pipeline formatting. Run: python telegram/test_tg_collect.py"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

import tg_collect as tc
import filtercheck  # noqa: E402
import reality  # noqa: E402
import sourcestats  # noqa: E402

_scratch = Path(tempfile.mkdtemp())  # the bot's counters must not get the test channels
sourcestats.PATH, filtercheck.PATH, reality.PATH = _scratch / "ss.json", _scratch / "fc.json", _scratch / "rl.json"
import usage  # noqa: E402
usage.PATH = _scratch / "usage.json"

cfg = yaml.safe_load((Path(__file__).parent / "search.default.yml").read_text(encoding="utf-8"))
f = tc.JobFilter(cfg["keywords"], cfg["min_length"])

JOB_RU = """🔥 #вакансия Аналитик данных (Middle)
Компания: Ромашка. Удалённо, парт-тайм 20 ч/нед.
Стек: SQL, ClickHouse, Airflow, Superset. Вилка 150-200к.
Задачи: витрины данных, дашборды для продукта, ad-hoc. Писать @hr_romashka"""

JOB_EN = """We're hiring a Senior Data Analyst (remote, contract)!
You will own our metrics layer in dbt and Looker, partner with product
and finance, and build trustworthy dashboards. Salary $4-5k/month."""

CV_POST = """#резюме Аналитик данных, 3 года опыта, SQL, Python, Airflow, Greenplum.
Ищу удалённую работу, рассмотрю вакансии в продуктовых компаниях. Пишите в личку."""

NOT_ROLE = """#вакансия Frontend-разработчик (React) в команду маркетплейса.
Удалённо, вилка 250-300к, TypeScript, Next.js, опыт от 3 лет. Пишите @hr."""

CHATTER = "Коллеги, кто знает хороший курс по SQL? Хочу подтянуть оконные функции, посоветуйте что-нибудь, спасибо!"

assert f.is_job(JOB_RU), "russian job post"
assert f.is_job(JOB_EN), "english job post"
assert not f.is_job(CV_POST), "candidate CV is not a job"
assert not f.is_job(NOT_ROLE), "frontend role filtered out"
assert not f.is_job(CHATTER), "chat question is not a job"
assert not f.is_job("#вакансия аналитик SQL"), "too short"

assert set(f.tags_for(JOB_RU)) == {"part-time", "remote"}, f.tags_for(JOB_RU)
assert set(f.tags_for(JOB_EN)) == {"contract", "remote"}, f.tags_for(JOB_EN)

assert tc.fingerprint(JOB_RU) == tc.fingerprint("  " + JOB_RU.replace("\n", " ") + "!")
assert tc.guess_title(JOB_RU) == "Аналитик данных (Middle)", tc.guess_title(JOB_RU)

job = {
    "chat": "Data | Jobs",
    "chat_id": -1001234,
    "msg_id": 42,
    "date": datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
    "link": "https://t.me/c/1234/42",
    "text": JOB_RU,
    "tags": ["part-time", "remote"],
}
line = tc.pipeline_line(job, "jds/tg-x.md")
assert line == (
    "- [ ] local:jds/tg-x.md | TG: Data / Jobs | Аналитик данных (Middle) | Remote"
    " | posted: 2026-10-02 | note: https://t.me/c/1234/42 [part-time, remote]"
), line


# channels.txt parsing
ck = tc.channel_key
assert ck("@Data_Jobs") == "data_jobs"
assert ck("https://t.me/data_jobs/123") == "data_jobs"
assert ck("t.me/data_jobs") == "data_jobs"
assert ck("https://t.me/c/1234567/89") == "id:1234567"
assert ck("Аналитики | Вакансии") == "аналитики | вакансии"
assert ck("C# / .NET jobs") == "c# / .net jobs"
with tempfile.TemporaryDirectory() as d:
    sample = Path(d) / "channels.txt"
    sample.write_text("\ufeff# comment\n\n@data_jobs\n  C# jobs  \n# @disabled\n", encoding="utf-8")
    assert tc.load_channels(sample) == ["@data_jobs", "C# jobs"]

# bot: inbox, screens, channel list editing
import boards
import bot


def data(button):
    return button.type.data.decode()


item = bot.to_inbox(job)
assert item["status"] == "new" and item["source"] == "tg" and item["title"] == "Аналитик данных (Middle)"
assert bot.from_inbox(item)["date"] == job["date"]
assert bot.job_key(job) == "1001234_42"
assert len(f"t:{bot.job_key(dict(job, chat_id=-1009999999999, msg_id=99999999))}:all:99".encode()) <= 64

caption, rows = bot.card_screen("1001234_42", item, "new", 2)
assert caption.startswith("<b>Аналитик данных (Middle)</b>") and "💬 Data | Jobs · 02.10 09:00" in caption
assert [data(b) for b in rows[0]] == ["t:1001234_42:new:2", "n:1001234_42:new:2"]
assert data(rows[-1][0]) == "l:new:2"  # back to the same list page
assert "✅ <b>В работе</b>" in bot.card_screen("k", dict(item, status="taken"))[0]
assert bot.card_screen("k", dict(item, fav=True))[0].startswith("⭐ ")

with tempfile.TemporaryDirectory() as d:
    real = tc.CHANNELS_PATH
    tc.CHANNELS_PATH = Path(d) / "channels.txt"
    tc.CHANNELS_PATH.write_text("# мои чаты\n@data_jobs\nАналитики | Чат\n", encoding="utf-8")
    assert bot.add_channel("https://t.me/new_jobs") is True
    assert bot.add_channel("@New_Jobs") is False  # same chat, other spelling
    assert bot.remove_channel("2") == "Аналитики | Чат"
    assert bot.remove_channel("t.me/data_jobs/5") == "t.me/data_jobs/5"
    assert bot.remove_channel("@nope") is None
    assert tc.CHANNELS_PATH.read_text(encoding="utf-8") == "# мои чаты\nhttps://t.me/new_jobs\n"

    # screens that read channels.txt
    caption, rows = bot.settings_screen(dict(cfg, boards_enabled=True))
    assert "1 чат," in caption and all(bot.visible_len(caption) <= 1024 for _ in [0])
    caption, rows = bot.chats_screen()
    assert data(rows[0][0]) == "c:del:0"
    tc.CHANNELS_PATH = real

# board jobs
line = "- [ ] https://weworkremotely.com/remote-jobs/x | Acme | Data Analyst | Anywhere | $60k | posted: 2026-09-30"
info = boards.parse_pipeline_line(line)
assert (info["company"], info["title"], info["location"], info["comp"], info["posted"]) == ("Acme", "Data Analyst", "Anywhere", "$60k", "2026-09-30")
assert boards.parse_pipeline_line("- [ ] https://x.io/1 | Co | Role | posted: 2026-10-01")["location"] == ""
web = boards.board_job("https://weworkremotely.com/remote-jobs/x", line)
assert web["site"] == "We Work Remotely" and web["key"].startswith("w_")
web_item = bot.to_inbox(web)
assert bot.job_key(web) == web["key"] and "key" not in web_item and web_item["pipeline_line"] == line
caption, rows = bot.card_screen(web["key"], web_item)
assert "🏢 Acme" in caption and "📍 Anywhere" in caption and "💰 $60k" in caption and "🌐 We Work Remotely" in caption

with tempfile.TemporaryDirectory() as d:
    real = tc.PIPELINE_PATH
    tc.PIPELINE_PATH = Path(d) / "pipeline.md"
    tc.PIPELINE_PATH.write_text("## Pending\n\n" + line + "\n- [ ] https://other.io/2 | B | C\n\n## Processed\n", encoding="utf-8")
    taken = boards.take_from_pipeline(["https://weworkremotely.com/remote-jobs/x"])
    assert taken == {"https://weworkremotely.com/remote-jobs/x": line}
    assert "weworkremotely" not in tc.PIPELINE_PATH.read_text() and "other.io/2" in tc.PIPELINE_PATH.read_text()
    tc.PIPELINE_PATH = real

# lists: newest first, paging, back to home; mixed sources
inbox = {}
for i in range(11):
    j = dict(job, msg_id=i, date=job["date"].replace(day=1 + i))
    inbox[bot.job_key(j)] = dict(bot.to_inbox(j), status="hidden" if i == 0 else "new", fav=i in (3, 5))
inbox[web["key"]] = web_item
caption, rows = bot.list_screen(inbox, "all")
assert "· 12" in caption and "стр. 1/2" in caption and data(rows[0][0]) == "o:1001234_10:all:0", caption
assert [data(b) for b in rows[2]] == ["l:all:1"] and data(rows[-1][0]) == "h"
caption, rows = bot.list_screen(inbox, "all", page=1)
assert "🙈" in caption and "🌐 We Work Remotely" in caption and [data(b) for b in rows[1]] == ["l:all:0"]
assert "· 2" in bot.list_screen(inbox, "fav")[0]
assert "Пока пусто" in bot.list_screen({}, "fav")[0]
for view in ("new", "all", "fav"):
    for page in (0, 1):
        assert bot.visible_len(bot.list_screen(inbox, view, page)[0]) <= 1024

caption, rows = bot.home_screen(inbox, next_check=7)
assert "<b>11</b> новых вакансий" in caption and "через 7 мин" in caption and data(rows[0][0]) == "rf:1"  # no app yet
caption, rows = bot.home_screen(inbox, owner=True, app_url="https://x.trycloudflare.com")
assert len(rows) == 2 and data(rows[1][0]) == "u"  # the app button + 👥 Близкие, nothing else
assert bot.plural(1, "a", "b", "c") == "a" and bot.plural(3, "a", "b", "c") == "b" and bot.plural(12, "a", "b", "c") == "c"
assert "2 новые вакансии" in bot.found_screen([item, web_item])[0]
assert "за 3 дн. нет" in bot.found_screen([], 3)[0]
assert bot.visible_len(bot.HELP) <= 1024
long_card = bot.card_screen("k", dict(item, text="слово " * 400, title="Очень длинный заголовок " * 4))[0]
assert bot.visible_len(long_card) <= 1024
assert bot.visible_len(bot.fit("<b>x</b>" + "я" * 2000)) <= 1024

# keywords from chat
assert [bot.show_pattern(p) for p in ["\\banalyst\\b", "power ?bi", "парт-?тайм", "C\\+\\+", "#cv\\b"]] == ["analyst", "power bi", "парт-тайм", "C++", "#cv"]
assert bot.short("Очень длинный заголовок вакансии", 20) == "Очень длинный…"
assert "1</b> новая вакансия ждёт тебя" in bot.home_screen({"a": item})[0]
assert bot.word_to_pattern("power bi") == "power bi" and bot.word_to_pattern("C++") == "C\\+\\+"
search = {"keywords": {"role": ["sql"]}}
assert bot.add_keywords(search, "role", "Tableau, SQL, , metabase") == ["Tableau", "metabase"]
assert search["keywords"]["role"] == ["sql", "Tableau", "metabase"]
assert tc.JobFilter({"job": ["вакансия"], "role": search["keywords"]["role"]}, 10).is_job("Вакансия: аналитик Metabase")

# family mode: invites, folders, limits, shared channel pool, shared sites
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import guests
guests.START_EMPTY = False  # these checks cover the Data Analyst preset machinery (kept for «example» setups)


def all_data(rows):
    return [data(b) for row in rows for b in row if hasattr(b, "data") or hasattr(b.type, "data")]


def check_buttons(rows):
    for row in rows:
        for b in row:
            if hasattr(b, "type") and hasattr(b.type, "data"):
                assert len(b.type.data) <= 64, b.type.data


assert guests.folder_name(42, "Mama_Ivanova") == "42_Mama_Ivanova"
assert guests.folder_name(42, None, "Мама Иванова") == "42_Мама_Иванова"
assert guests.folder_name(42, None, "🌸") == "42_user"
assert guests.folder_name(42, "../../etc") == "42_etc"  # no path tricks in folder names
assert guests.public_username("@Buh_Jobs") == "buh_jobs"
assert guests.public_username("https://t.me/buh_jobs/15") == "buh_jobs"
assert guests.public_username("https://t.me/c/123/4") is None  # private chat
assert guests.public_username("Бухгалтеры | Чат") is None  # a title isn't enough
assert guests.split_channels("@a_jobs, t.me/b_jobs\n@c_jobs") == ["@a_jobs", "t.me/b_jobs", "@c_jobs"]
assert len(guests.split_channels(" ".join(f"@ch{i}_xx" for i in range(50)))) == 10
assert bot.to_int("7") == 7 and bot.to_int("x") == 0 and bot.to_int("99999999") == 10_000 and bot.to_int(None, -1) == -1

# keywords: literal, limited
s = {"keywords": {"role": []}}
added, refused = guests.add_words(s, "role", "бухгалт, 1С, (a+)+$, " + "x" * 50)
assert added == ["бухгалт", "1С", "(a+)+$"] and len(refused) == 1
assert s["keywords"]["role"][2] == "\\(a\\+\\)\\+\\$"  # a regex trick stays plain text
s = {"keywords": {"role": [f"w{i}" for i in range(guests.MAX_WORDS)]}}
assert guests.add_words(s, "role", "ещё")[0] == []

caption, rows = bot.home_screen({}, owner=True)
assert data(rows[-1][0]) == "u"
assert "u" not in [data(b) for r in bot.home_screen({})[1] for b in r]  # guests have no 👥 button
assert "✅ <b>В работе</b>" == bot.card_screen("k", dict(item, status="taken"), status_lines=bot.GUEST_STATUS_LINE)[0].split("\n\n")[-1]

with tempfile.TemporaryDirectory() as d:
    saved = guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, guests.PORTALS_PATH
    guests.USERS_DIR = Path(d)
    guests.REGISTRY_PATH = Path(d) / "users.json"
    guests.POOL_PATH = Path(d) / "pool.json"
    guests.PORTALS_PATH = Path(d) / "portals.yml"
    guests.PORTALS_PATH.write_text(
        "# my portals\ntitle_filter:\n  positive: [Analyst]\n\n# boards\njob_boards:\n"
        "  - name: Remotive\n    provider: remotive\n    enabled: true\n"
        "  - name: Himalayas\n    provider: himalayas\n    enabled: false\n\n"
        "# WebSearch\nsearch_queries: []\n", encoding="utf-8")

    # invites
    reg = guests.load_registry()
    code = guests.new_invite(reg)
    assert guests.use_invite(reg, code) and not guests.use_invite(reg, code)  # one-time
    old = guests.new_invite(reg)
    reg["invites"][old]["created"] = (guests.now() - timedelta(days=8)).isoformat()
    assert not guests.use_invite(reg, old) and old not in reg["invites"]  # expired
    full = {"users": {str(i): {} for i in range(guests.MAX_MEMBERS - 1)}, "invites": {}}
    assert guests.new_invite(full) and guests.new_invite(full) is None  # open invites count too

    mama = guests.register(reg, SimpleNamespace(id=101, username="mama", first_name="Мама", last_name=None))
    papa = guests.register(reg, SimpleNamespace(id=202, username=None, first_name="Папа", last_name="Петров"))
    guests.save_registry(reg)
    assert (Path(d) / "101_mama" / "search.yml").exists() and papa.dir.name == "202_Папа_Петров"
    assert guests.get(guests.load_registry(), 101).name == "@mama" and papa.name == "Папа Петров"
    assert mama.load_state()["step"] is None and any("data analyst" in p for p in mama.cfg()["keywords"]["role"])
    assert "ru-company" in mama.cfg()["keywords"]["tags"] and "hh" in mama.load_search()["sites"]
    assert "#резюме" in mama.cfg()["keywords"]["negative"]
    for g, words in ((mama, "бухгалт, 1С, accountant"), (papa, "водител")):
        s = g.load_search()
        guests.add_words(s, "role", words)
        g.save_search(s)
        g.update_state(step=None)

    t0 = datetime.now(timezone.utc)
    BUH = ("#вакансия Бухгалтер на первичку, 1С 8.3, удалённо, зарплата 80к. "
           "Пишите в личку, ждём резюме с опытом от года, обучение за счёт компании.")
    DRIVER = ("Требуется водитель категории B на доставку по городу, график 5/2, зарплата 90к, "
              "оформление по ТК, служебный автомобиль и топливная карта.")
    posts = {
        "buh_jobs": [SimpleNamespace(id=3, date=t0, message=BUH), SimpleNamespace(id=2, date=t0, message="привет всем")],
        "shared_jobs": [SimpleNamespace(id=7, date=t0, message=DRIVER), SimpleNamespace(id=6, date=t0, message=BUH),
                        SimpleNamespace(id=1, date=t0 - timedelta(days=5), message=DRIVER)],
        "some_person": [],
    }

    class FakeClient:
        def __init__(self):
            self.reads = []

        async def get_entity(self, key):
            if key not in posts:
                raise ValueError("No user has that username")
            cls = type("User" if key == "some_person" else "Channel", (), {})
            e = cls()
            e.id, e.title, e.username = 500 + len(key), key.upper(), key
            return e

        async def iter_messages(self, entity, min_id=0):
            self.reads.append((entity.username, min_id))
            for msg in posts[entity.username]:  # newest first, like Telegram
                if msg.id > min_id:
                    yield msg

    guests.utils.get_peer_id = lambda e: -1000000000000 - e.id  # fake entities aren't real peers

    # pool: adding, validation, daily limit, hard cap
    pool = guests.load_pool()
    added, refused = asyncio.run(guests.add_to_pool(FakeClient(), pool, mama,
                                                    "@buh_jobs t.me/shared_jobs @nope_chan @some_person t.me/c/1/2"))
    assert added == ["buh_jobs", "shared_jobs"], added
    assert [w for w, _ in refused] == ["@nope_chan", "@some_person", "t.me/c/1/2"], refused
    assert set(guests.load_pool()["channels"]) == {"buh_jobs", "shared_jobs"}
    assert mama.selected(guests.load_pool()) == ["buh_jobs", "shared_jobs"]
    assert guests.pool_adds_left(guests.load_pool(), mama) == guests.POOL_ADDS_PER_DAY - 2
    pool = guests.load_pool()
    added, _ = asyncio.run(guests.add_to_pool(FakeClient(), pool, papa, "@shared_jobs"))
    assert added == ["shared_jobs"] and guests.pool_adds_left(guests.load_pool(), papa) == guests.POOL_ADDS_PER_DAY  # ticking isn't adding
    pool = guests.load_pool()
    assert guests.can_remove(pool, "buh_jobs", mama) and not guests.can_remove(pool, "buh_jobs", papa)
    assert guests.can_remove(pool, "buh_jobs", SimpleNamespace(is_owner=True, id=1))
    pool["adds"]["101"] = [guests.now().isoformat()] * 3
    posts["more_jobs"] = []
    assert asyncio.run(guests.add_to_pool(FakeClient(), pool, mama, "@more_jobs"))[1][0][1].startswith("не больше")
    for i in range(guests.MAX_POOL - 2):
        pool["channels"][f"filler_{i:02d}"] = {"title": "", "added_by": 1, "added": ""}
    assert asyncio.run(guests.add_to_pool(FakeClient(), pool, SimpleNamespace(is_owner=True, id=1, selected=lambda p: [], set_selected=lambda k: None), "@more_jobs"))[1][0][1].startswith("в пуле уже")
    for i in range(guests.MAX_POOL - 2):
        del pool["channels"][f"filler_{i:02d}"]
    guests.save_pool(pool)

    # reading: each channel once, filtered per person, incremental
    client = FakeClient()
    found, missing = asyncio.run(guests.scan_pool(client, [mama, papa], guests.load_pool(), pause=0))
    assert sorted(r[0] for r in client.reads) == ["buh_jobs", "shared_jobs"]  # each channel read once
    assert [j["msg_id"] for j in found[101]] == [3]  # the same BUH post in shared_jobs is deduped
    assert [j["msg_id"] for j in found[202]] == [7]  # only the driver post; the 5-day-old one is too old
    assert found[101][0]["link"] == "https://t.me/buh_jobs/3" and missing == []
    assert mama.load_state()["pool_ids"] == {"buh_jobs": 3, "shared_jobs": 7}
    posts["shared_jobs"].insert(0, SimpleNamespace(id=8, date=t0, message=DRIVER + " Срочно!"))
    client = FakeClient()
    found, _ = asyncio.run(guests.scan_pool(client, [mama, papa], guests.load_pool(), pause=0))
    assert ("shared_jobs", 7) in client.reads and found[202][0]["msg_id"] == 8 and found[101] == []  # incremental
    pool = guests.load_pool()
    pool["channels"]["gone_channel"] = {"title": "", "added_by": 202, "added": ""}
    papa.set_selected(["gone_channel"])
    found, missing = asyncio.run(guests.scan_pool(FakeClient(), [papa], pool, pause=0))
    assert missing == ["gone_channel"] and found[202] == []
    guests.remove_from_pool(pool, "gone_channel")
    assert papa.selected(guests.load_pool()) == []  # removed from the pool = gone from everyone's ticks

    # sites: the shared list is portals.yml job_boards; edits keep the rest of the file
    assert {k: v for k, v in guests.listed_sites().items() if not k.startswith("co-")} == {"hh": "hh.ru", "remotive": "Remotive", "himalayas": "Himalayas"}
    guests.add_board("jobicy")
    guests.set_board_enabled("himalayas", True)
    guests.set_board_enabled("remotive", False)
    text = guests.PORTALS_PATH.read_text(encoding="utf-8")
    assert "# my portals" in text and "# WebSearch" in text and "positive: [Analyst]" in text
    boards_now = {b["provider"]: b["enabled"] for b in guests.read_boards()}
    assert boards_now == {"remotive": False, "himalayas": True, "jobicy": False}, boards_now
    guests.remove_board("remotive")
    assert {k for k in guests.listed_sites() if not k.startswith("co-")} == {"hh", "himalayas", "jobicy"}
    try:
        guests.add_board("evil-site")
        raise AssertionError("only catalog sites can be added")
    except KeyError:
        pass
    owner_like = guests.Owner(1)
    owner_search = {}
    owner_like.load_search = lambda: dict(owner_search)  # don't touch the real search.yml
    owner_like.save_search = lambda s: (owner_search.clear(), owner_search.update(s))
    assert owner_like.sites(guests.listed_sites()) == ["himalayas"]  # yours = enabled in portals.yml
    owner_like.set_sites(["hh", "himalayas"])
    assert owner_like.sites(guests.listed_sites()) == ["hh", "himalayas"] and owner_search["sites"] == ["hh"]

    _s = mama.load_search()  # start from a person with no hh (the newcomer preset ticks it)
    _s["hh"] = {}
    mama.save_search(_s)
    mama.set_sites(["himalayas", "jobicy", "not_listed"])
    assert mama.sites(guests.listed_sites()) == ["himalayas", "jobicy"]
    feed = {"himalayas": {"jobs": [
        {"title": "Senior Accountant", "url": "https://h.io/1", "company": "Acme", "location": "Remote", "posted": "2026-09-30"},
        {"title": "Python Developer", "url": "https://h.io/2", "company": "B", "location": "", "posted": ""},
    ]}, "jobicy": {"error": "timeout"}}
    jobs = guests.match_site_jobs(feed, mama, guests.listed_sites())
    assert [j["title"] for j in jobs] == ["Senior Accountant"] and jobs[0]["site"] == "Himalayas"
    assert guests.match_site_jobs(feed, mama, guests.listed_sites()) == []  # seen once = not again
    web_item = bot.to_inbox(jobs[0])
    assert "🏢 Acme" in bot.card_screen(jobs[0]["key"], web_item, status_lines=bot.GUEST_STATUS_LINE)[0]

    # screens: fit in Telegram limits, buttons carry what the handlers expect
    pool = guests.load_pool()
    caption, rows = bot.pool_screen(pool, mama)
    assert "2 из 60" in caption and "У тебя отмечено: 2" in caption and bot.visible_len(caption) <= 1024
    assert data(rows[0][0]) == "p:t:buh_jobs:0" and data(rows[0][1]) == "p:d:buh_jobs:0"
    caption, rows = bot.pool_screen(pool, papa)
    assert len(rows[0]) == 1  # papa can't delete mama's channel
    caption, rows = bot.pool_screen(pool, mama, onboarding=True)
    assert caption.startswith("<b>Шаг 2 из 2") and data(rows[-1][0]) == "skip"
    for i in range(guests.MAX_POOL - 2):
        pool["channels"][f"channel_number_{i:02d}_with_long_name"] = {"title": "Очень длинное название канала " * 3, "added_by": 1, "added": f"z{i}"}
    for page in range(4):
        caption, rows = bot.pool_screen(pool, mama, page)
        check_buttons(rows)
        assert bot.visible_len(caption) <= 1024
    assert not any(data(b) == "p:a" for row in bot.pool_screen(pool, mama)[1] for b in row)  # full: no ➕
    caption, rows = bot.sites_screen(mama)
    assert "у тебя отмечено 2" in caption and data(rows[0][0]) == "w:t:hh" and data(rows[1][0]) == "w:t:himalayas"
    assert len(rows[1]) == 1 and "запрос не задан" in caption  # guests can't delete; hh not ticked: no hh buttons
    caption, rows = bot.sites_screen(owner_like)
    assert len(rows[0]) == 1 and [data(b) for b in rows[1]] == ["w:q", "w:r"]  # hh: no 🗑, ticked: query buttons
    assert data(rows[2][1]) == "w:d:himalayas" and data(rows[-2][0]) == "w:add"
    check_buttons(rows)

    # hh.ru RSS
    rss = ("<?xml version='1.0' encoding='utf-8'?><rss version=\"2.0\"><channel><title>HeadHunter Vacancy</title>"
           "<item><pubDate>2026-10-02T17:36:33.390+03:00</pubDate><title>Главный бухгалтер</title>"
           "<link>https://hh.ru/vacancy/111?from=rss</link><description>&lt;p&gt;Вакансия компании: Ромашка&lt;/p&gt; "
           "&lt;p&gt;Регион: Москва&lt;/p&gt; &lt;p&gt;Предполагаемый уровень месячного дохода: от 80 000 ₽&lt;/p&gt;"
           "</description></item>"
           "<item><pubDate>2026-10-02T17:00:00.000+03:00</pubDate><title>Менеджер по продажам</title>"
           "<link>https://hh.ru/vacancy/222</link><description>&lt;p&gt;Вакансия компании: B&lt;/p&gt;</description></item>"
           "<item><title>Бухгалтер на фишинге</title><link>https://evil.example/vacancy/1</link></item>"
           "</channel></rss>").encode("utf-8")
    raw = guests.parse_hh_rss(rss)
    assert [j["url"] for j in raw] == ["https://hh.ru/vacancy/111", "https://hh.ru/vacancy/222"]  # foreign links dropped
    assert (raw[0]["company"], raw[0]["location"], raw[0]["comp"]) == ("Ромашка", "Москва", "от 80 000 ₽")
    jobs = guests.match_hh_jobs(raw, mama)
    assert [j["title"] for j in jobs] == ["Главный бухгалтер"] and jobs[0]["site"] == "hh.ru"  # title must match words
    assert jobs[0]["pipeline_line"].startswith("- [ ] https://hh.ru/vacancy/111 | Ромашка | Главный бухгалтер | Москва")
    assert jobs[0]["date"].year == 2026 and guests.match_hh_jobs(raw, mama) == []  # seen once = not again
    assert guests.default_hh_query(mama).startswith('"data analyst" OR "аналитик данных" OR')  # preset words come first
    guests.set_hh(mama, query="  бухгалтер\n OR   главбух " + "x" * 300)
    assert guests.hh_settings(mama)["query"].startswith("бухгалтер OR главбух") and len(guests.hh_settings(mama)["query"]) == guests.HH_QUERY_MAX
    assert guests.hh_settings(mama)["remote"] is True
    assert "schedule=remote" in guests.hh_url("аналитик", True) and "schedule" not in guests.hh_url("аналитик", False)
    caption, rows = bot.catalog_screen(mama)
    assert "Предложить" in caption and all(data(r[0]).startswith("w:p:") for r in rows[:-1])
    check_buttons(rows)
    caption, rows = bot.family_screen(guests.load_registry(), "https://x.trycloudflare.com")
    assert "Пользователи" in caption and "любой" in caption and bot.visible_len(caption) <= 1024
    check_buttons(rows[1:])
    # open access (2026-10-05): anyone joins on the first visit; a switched-off person is never re-registered
    reg = guests.load_registry()

    class Stranger:
        id, username, first_name, last_name = 777, "stranger", "Незнакомец", None
    newcomer, why = guests.join(reg, Stranger())
    assert newcomer and why is None and guests.get(reg, 777) and newcomer.load_inbox() == {}
    assert guests.join(reg, Stranger())[0].id == 777  # a second visit is the same person
    assert guests.set_blocked(reg, 777, True) and guests.is_blocked(reg, 777)
    assert guests.get(reg, 777) is None and 777 not in [g.id for g in guests.all_guests(reg)]
    assert 777 in [g.id for g in guests.everyone(reg)] and guests.join(reg, Stranger()) == (None, "blocked")
    assert newcomer.dir.exists()  # switched off: the data stays
    guests.set_blocked(reg, 777, False)
    assert guests.get(reg, 777) and guests.ai_limit(guests.get(reg, 777), reg) == guests.AI_DAILY
    reg["settings"]["ai_daily"] = 3
    reg["users"]["777"]["ai_limit"] = 5
    assert guests.ai_limit(guests.get(reg, 777), reg) == 5
    reg["users"]["777"]["ai"] = False
    assert guests.ai_limit(guests.get(reg, 777), reg) == 0
    full = {"users": {str(i): {} for i in range(guests.MAX_MEMBERS)}, "invites": {}, "settings": {}}
    assert guests.join(full, Stranger()) == (None, "full")
    caption, rows = bot.guest_settings_screen(mama)
    assert "бухгалт, 1С" in caption and data(rows[0][0]) == "k:role"

    big = {f"k{i}": dict(bot.to_inbox(dict(job, msg_id=i)), date=f"2026-01-{i % 28 + 1:02d}") for i in range(guests.INBOX_CAP + 30)}
    big["k0"]["fav"] = True
    assert len(guests.trim_inbox(big)) == guests.INBOX_CAP and "k0" in big

    assert guests.remove(reg, 101) and guests.get(reg, 101) is None and mama.dir.exists()
    guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, guests.PORTALS_PATH = saved

# vacancy cards without «вакансия»/«hiring» (@revacancy_global): role must be in the title line
card = tc.JobFilter({"job": ["ваканси"], "role": ["data analyst", "sql"]}, 50)
assert card.is_job("🟪 Senior Data Analyst | 5 year(s) | Senior | IC\n▫️ Acme | Fintech\n▫️ 90k - 120k USD Annually\nSkills: sql, python\n👉 Contacts and Full Description")
assert not card.is_job("🟪 Senior Software Engineer | 5 year(s) | Senior | IC\n▫️ Acme | Fintech\n▫️ 90k - 120k USD Annually\nSkills: sql, golang\n👉 Contacts and Full Description")

# role phrases of two words match in any order and any spelling (the audit found «антифрод аналитик» missing
# «Ведущий системный аналитик в команду антифрод решений»); longer phrases stay literal
roles = tc.compile_role(["антифрод аналитик", "Anti fraud", "data quality engineer"])
assert roles[0].search("Ведущий системный аналитик в команду антифрод решений") and roles[0].search("Аналитик (Антифрод)")
assert roles[1].search("Anti-fraud Analyst") and roles[1].search("antifraud") and not roles[0].search("Data Scientist (Антифрод)")
assert roles[2].search("Data Quality Engineer") and not roles[2].search("engineer of data quality")
assert not roles[0].search("аналитик" + " " * 60 + "антифрод")

# -- languages without our job words: the role still decides; why_not names the reason for the control sample
ml = tc.JobFilter({"job": ["ваканси", "hiring"], "role": ["data analyst"], "negative": ["стажёр"]}, 40)
assert ml.is_job("Buscamos Data Analyst para nuestro equipo remoto, contrato indefinido")
assert ml.is_job("Шукаємо Data Analyst у команду, віддалено, повна зайнятість")
assert ml.is_job("Wir suchen einen Data Analyst (m/w/d) — remote möglich, Vollzeit")
assert not ml.is_job("Buscamos diseñador gráfico para nuestro equipo remoto, contrato indefinido")
assert ml.why_not("Как стать Data Analyst за 3 месяца: курс со скидкой, пишите") == "нет слова «вакансия»"
assert ml.why_not("Data Analyst") == "короткий"
assert ml.why_not("Вакансия: стажёр Data Analyst, пишите в личку, удалённо, гибкий график") == "стоп-слово"
assert ml.why_not("Вакансия: Senior Data Analyst в финтех, удалённо, пишите в личку @hr") is None   # a job
assert ml.why_not("Ищем дизайнера в команду, удалённо, гибкий график, пишите") is None             # not their role

# -- 🧪 control sample (filtercheck): pending -> one Claude call a day -> loss count
with tempfile.TemporaryDirectory() as tmp:
    filtercheck.PATH = Path(tmp) / "fc.json"
    now_ = datetime.now(timezone.utc).isoformat()
    posts = [{"text": f"Data Analyst post number {n} " * 5, "chat": "c", "link": f"https://t.me/c/{n}", "date": now_,
              "why": "нет слова «вакансия»"} for n in range(4)]
    filtercheck.add(posts + posts[:1])
    assert len(filtercheck.load()["pending"]) == 4 and filtercheck.due()

    class FakeAI:
        async def ask(self, system, prompt, timeout=0):
            assert "### 1" in prompt and "résumé" in system
            return 'Sure: {"1": true, "2": false, "3": false, "4": true}'

    import asyncio as _asyncio  # noqa: E402
    assert _asyncio.run(filtercheck.run(FakeAI())) == (4, 2)
    rep = filtercheck.report()
    assert rep["checked"] == 4 and rep["lost"] == 2 and rep["waiting"] == 0 and len(rep["items"]) == 2
    assert not filtercheck.due() and _asyncio.run(filtercheck.run(FakeAI())) == (0, 0)  # once a week
    filtercheck.add(posts)  # already checked: not queued again
    assert filtercheck.load()["pending"] == []
    assert filtercheck.parse("no json here", 3) == {} and filtercheck.parse('{"9": true, "1": "yes"}', 3) == {}

print("ok")
