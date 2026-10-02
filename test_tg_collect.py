"""Offline checks for the job filter and pipeline formatting. Run: python telegram/test_tg_collect.py"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

import tg_collect as tc

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
assert "<b>11</b> новых вакансий" in caption and "через 7 мин" in caption and data(rows[0][0]) == "l:new:0"
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

print("ok")
