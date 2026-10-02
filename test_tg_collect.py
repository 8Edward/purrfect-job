"""Offline checks for the job filter and pipeline formatting. Run: python telegram/test_tg_collect.py"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

import tg_collect as tc

cfg = yaml.safe_load((Path(__file__).parent / "config.example.yml").read_text(encoding="utf-8"))
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

# bot: inbox round trip, cards, channel list editing
import bot

item = bot.to_inbox(job)
assert item["status"] == "new" and bot.from_inbox(item)["date"] == job["date"]
assert bot.job_key(job) == "1001234_42"
card = bot.card_text(job)
assert card.startswith("<b>Аналитик данных (Middle)</b>\n💬 Data | Jobs · 02.10 09:00\n#part_time #remote"), card
assert len(bot.card_buttons(job, "k")) == 2 and len(bot.card_buttons(dict(job, link=None), "k")) == 1
assert len(f"t:{bot.job_key(dict(job, chat_id=-1009999999999, msg_id=99999999))}".encode()) <= 64  # Telegram callback data limit

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
    tc.CHANNELS_PATH = real

print("ok")
