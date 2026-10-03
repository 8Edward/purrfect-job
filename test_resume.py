"""Offline tests for the résumé workshop (resume.py, resume_ai.py). Run: python telegram/test_resume.py -> ok"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import resume  # noqa: E402
import resume_ai  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PLAIN = """Анна Петрова
anna@mail.ru · Telegram @anna_petrova · Москва

О себе
Я ответственный и коммуникабельный аналитик.

Опыт работы
Бизнес-аналитик — Банк Ромашка
Март 2022 — наст. время
• Занимался сбором требований к CRM
• Отвечала за BPMN-схемы процессов
• Подготовила 40 спецификаций для разработки

Образование
РЭУ им. Плеханова, 2021

Навыки
SQL, BPMN, Jira
"""

# -- blocks and Markdown
md = resume.from_text(PLAIN)
bl = resume.blocks(md)
assert bl[0] == {"t": "h1", "x": "Анна Петрова"}, bl[:2]
assert [b["x"] for b in bl if b["t"] == "h2"] == ["О себе", "Опыт работы", "Образование", "Навыки"], bl
assert {"t": "h3", "x": "Бизнес-аналитик — Банк Ромашка"} in bl
assert sum(1 for b in bl if b["t"] == "li") == 3
assert resume.blocks(resume.markdown_of(bl)) == bl  # round trip is stable
assert resume.from_text("# Already\n\n- md\n") == "# Already\n\n- md\n"
assert resume.inline_html("**Жирный** и *курсив* [сайт](https://x.io) <b>") == \
    '<b>Жирный</b> и <i>курсив</i> <a href="https://x.io">сайт</a> &lt;b&gt;'
summ = resume.with_summary(bl, "Бизнес-аналитик с 3 годами опыта.")
assert [b["x"] for b in summ if b["t"] == "p"][1] == "Бизнес-аналитик с 3 годами опыта." and len(summ) == len(bl)
added = resume.with_summary([b for b in bl if not (b["t"] == "h2" and b["x"] == "О себе") and "ответственный" not in b["x"]], "Новый текст")
assert any(b["t"] == "h2" and b["x"] == "О себе" for b in added) and any(b["x"] == "Новый текст" for b in added)

# -- doctor
d = resume.doctor(md)
ids = {r["id"]: r for r in d["remarks"]}
assert "weak" in ids and len(ids["weak"]["examples"]) == 2 and ids["weak"]["ai"] == "improve", ids.keys()
assert "numbers" in ids and "cliches" in ids and "summary-i" in ids and "where" in ids  # a city alone says nothing about the time zone
d_good = resume.doctor(open(ROOT / "cv.md", encoding="utf-8").read())
assert d_good["score"] >= 85, d_good
assert resume.doctor("Иван")["score"] < 60 and "contacts" in {r["id"] for r in resume.doctor("Иван")["remarks"]}
assert [r["level"] for r in d["remarks"]] == sorted((r["level"] for r in d["remarks"]), key=["must", "should", "nice"].index)

# -- vacancy match
vac = "We need a Data Analyst: SQL, Python, dbt and Tableau. Experience with Airflow is a plus. SQL daily, Tableau dashboards."
m = resume.match(md, vac)
present = {x["term"].lower() for x in m["present"]}
missing = {x["term"].lower() for x in m["missing"]}
assert "sql" in present and {"python", "dbt", "tableau", "airflow"} <= missing, (present, missing)
assert m["role"] == "аналитик данных" and 0 < m["score"] < 50

# -- builder
draft = {"lang": "ru", "name": "Пётр Иванов", "title": "Аналитик данных", "location": "Таиланд, UTC+7 · удалённо",
         "email": "p@x.io", "telegram": "@petr",
         "jobs": [{"role": "Аналитик данных", "company": "Почта", "from": "2021", "to": "наст. время",
                   "bullets": [{"action": "автоматизировал выгрузку отчётов", "how": "на Python",
                                "result": "время сократилось с 1 часа до 15 минут"},
                               {"action": "Построил 15 витрин", "how": "", "result": "сократив инциденты на 30%"}]}],
         "education": [{"school": "МГТУ им. Баумана", "degree": "Бакалавр", "year": "2023"}],
         "skills": ["SQL", "Python", "SQL"], "languages": [{"name": "Английский", "level": "B2"}],
         "wants": ["полная занятость"], "summary": ""}
built = resume.build(draft)
assert built.startswith("# Пётр Иванов\n") and "## Опыт работы" in built and "### Аналитик данных — Почта" in built
assert "- Автоматизировал выгрузку отчётов на Python: время сократилось с 1 часа до 15 минут" in built
assert "- Построил 15 витрин, сократив инциденты на 30%" in built
assert "SQL, Python\n" in built and "Английский (B2)" in built
assert "Аналитик данных с опытом" in built and "Ищу удалённую работу: полная занятость." in built  # auto summary
assert resume.doctor(built)["score"] >= 70
en = resume.build({**draft, "lang": "en", "summary": "Data analyst."})
assert "## Work Experience" in en and "## Summary" in en
assert "Power BI" in resume.skill_suggestions("аналитик данных")

# -- translation guard
src = "# Анна\n\n## Опыт работы\n\n### Аналитик — Ромашка\n\n- Сократила время на 40% в Airflow\n- 15 витрин\n"
good = "# Anna\n\n## Work Experience\n\n### Analyst — Romashka\n\n- Cut processing time by 40% in Airflow\n- 15 data marts\n"
assert resume.translation_guard(src, good, "en") == []
bad = "# Anna\n\n## Work Experience\n\n### Analyst — Romashka\n\n- Cut processing time a lot\n- витрины данных 15\n"
probs = resume.translation_guard(src, bad, "en")
assert any("40" in p for p in probs) and any("Airflow" in p for p in probs) and any("русский" in p for p in probs), probs

# -- AI: fact guard on proposals
changes, dropped = resume_ai.checked_changes([
    {"before": "Занимался сбором требований к CRM", "after": "Собрал требования к CRM для 3 отделов", "why": "x"},  # new number
    {"before": "Отвечала за BPMN-схемы процессов", "after": "Описала процессы в BPMN", "why": "сильный глагол"},
    {"before": "Строки нет в резюме", "after": "Что-то", "why": ""},
    {"before": "- Подготовила 40 спецификаций для разработки", "after": "Подготовила 40 спецификаций для разработки", "why": ""},
    {"before": "Отвечала за BPMN-схемы процессов", "after": "Описала процессы в BPMN и Visio", "why": "new tool"},
], md)
assert [c["after"] for c in changes] == ["Описала процессы в BPMN"] and dropped == 4, (changes, dropped)
assert changes[0]["index"] == next(i for i, b in enumerate(bl) if b["x"] == "Отвечала за BPMN-схемы процессов")
assert resume_ai.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
# English résumé: ordinary new words and line-initial verbs are fine; a new tool name is not
en_md = "# John\n\n## Work Experience\n\n- Executed complex ad-hoc SQL queries (CTEs) for top management in March 2024\n"
assert resume_ai.invented("Wrote streamlined ad-hoc SQL queries (CTEs) for executives", en_md) == []
assert resume_ai.invented("Wrote ad-hoc SQL and Snowflake queries for executives", en_md) == ["snowflake"]
assert resume_ai.invented("Delivered queries in September 2024 using PostgreSQL", en_md) == ["postgresql"]
assert resume.name_tokens("- Built marts in Greenplum. Optimized C++ code, S3 and dbt") == {"greenplum", "c++", "s3"}


class FakeAI:
    def __init__(self, answers):
        self.answers, self.prompts = answers, []
        self.lock = asyncio.Lock()

    async def ask(self, system, prompt, timeout=0):
        self.prompts.append((system, prompt))
        assert "Never invent facts" in system
        return self.answers.pop(0)


async def ai_ops():
    ai = FakeAI([json.dumps({"changes": [{"before": "Отвечала за BPMN-схемы процессов", "after": "Описала процессы в BPMN", "why": "глагол"}],
                             "questions": [{"ask": "Сколько процессов?"}]}, ensure_ascii=False)])
    r = await resume_ai.run(ai, "improve", md)
    assert r["changes"][0]["after"] == "Описала процессы в BPMN" and r["questions"] == ["Сколько процессов?"]
    ai = FakeAI(['{"variants": ["Бизнес-аналитик: BPMN, SQL.", "Аналитик с 10 годами опыта.", "Аналитик требований."]}'])
    r = await resume_ai.run(ai, "summary", md)
    assert r["variants"] == ["Бизнес-аналитик: BPMN, SQL.", "Аналитик требований."] and r["dropped"] == 1  # «10 лет» invented
    ai = FakeAI(["```markdown\n" + good + "```"])
    r = await resume_ai.run(ai, "translate", src, lang="en")
    assert r["lang"] == "en" and r["problems"] == [] and r["markdown"].startswith("# Anna")
    assert "Glossary" in ai.prompts[0][1] and "витрина данных = data mart" in ai.prompts[0][1]
    ai = FakeAI(['{"text": "Здравствуйте! Подготовила 40 спецификаций и 99 отчётов."}'])
    r = await resume_ai.run(ai, "cover", md, vacancy="Ищем бизнес-аналитика в банк")
    assert "99" in r["warning"] and r["text"].startswith("Здравствуйте")
    ai = FakeAI(["not json at all"])
    try:
        await resume_ai.run(ai, "improve", md)
        raise AssertionError("bad answer must fail")
    except RuntimeError as e:
        assert resume_ai.human_error(e).startswith("ИИ ответил не по формату")


asyncio.run(ai_ops())
assert resume_ai.human_error(RuntimeError("login")).startswith("ИИ не подключён")

# import: LinkedIn data archive (CSV tables; some start with «Notes:» lines)
import io  # noqa: E402
import zipfile  # noqa: E402
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("Profile.csv", "First Name,Last Name,Maiden Name,Headline,Summary,Geo Location\n"
               'Anna,Petrova,,Business Analyst,"Business analyst, 3 years in banking.",Bangkok\n')
    z.writestr("Positions.csv", "Company Name,Title,Description,Location,Started On,Finished On\n"
               'Bank,Business Analyst,"- Described 40 processes in BPMN\n- Wrote 25 specifications",Moscow,Mar 2022,\n'
               "Shop,Analyst,,Remote,Jan 2020,Feb 2022\n")
    z.writestr("Email Addresses.csv", "Email Address,Confirmed,Primary,Updated On\nold@x.io,Yes,No,\nanna@x.io,Yes,Yes,\n")
    z.writestr("Education.csv", "School Name,Start Date,End Date,Notes,Degree Name,Activities\nHSE,2015,2019,,Bachelor,\n")
    z.writestr("Skills.csv", "Name\nBPMN\nSQL\n")
    z.writestr("Languages.csv", "Name,Proficiency\nEnglish,Professional working proficiency\n")
    z.writestr("Certifications.csv", "Notes:\n\"Exported\"\n\nName,Url,Authority,Started On,Finished On,License Number\nCBAP,,IIBA,Jan 2023,,\n")
md, src = resume.import_file("Basic_LinkedInDataExport.zip", buf.getvalue())
bl = resume.blocks(md)
assert src == "архив LinkedIn" and bl[0] == {"t": "h1", "x": "Anna Petrova"}, bl[:3]
assert "anna@x.io" in md and "old@x.io" not in md and "Bangkok" in md
assert {"t": "h3", "x": "Business Analyst — Bank"} in bl and {"t": "li", "x": "Wrote 25 specifications"} in bl
assert "Mar 2022 — Present" in md and "Jan 2020 — Feb 2022" in md
assert "## Summary" in md and "## Courses & Certifications" in md and "CBAP · IIBA · Jan 2023" in md
assert "BPMN, SQL" in md and "English (Professional working proficiency)" in md
buf2 = io.BytesIO()
with zipfile.ZipFile(buf2, "w") as z:
    z.writestr("Positions.csv", "Company Name,Title,Description,Location,Started On,Finished On\nBank,Analyst,,,2022,\n")
md2, _ = resume.import_file("export.zip", buf2.getvalue())
assert "## Summary" not in md2  # no summary in the archive -> none made up
try:
    resume.import_file("x.zip", b"PK\x03\x04junk")
    raise AssertionError("broken zip must fail")
except ValueError:
    pass
# import: hh.ru export text (footer, «Опыт работы —N лет» heading) and RTF
hh = ("Петрова Анна\nЖенщина, 30 лет\nanna@x.io\nЖелаемая должность и зарплата\nБизнес-аналитик\n"
      "Опыт работы —3 года 2 месяца\nБизнес-аналитик\n- Описала 40 процессов в BPMN\n"
      "Петрова Анна • Резюме обновлено 1 октября 2026 в 12:00\nНавыки\nBPMN, SQL\n") * 2
text, source = resume.clean_export(hh)
assert source == "hh" and "Резюме обновлено" not in text and "\nОпыт работы\n" in text
rtf = (rb"{\rtf1\ansi\ansicpg1251{\fonttbl{\f0 Arial;}}\f0 \'cf\'e5\'f2\'f0\'ee\'e2\'e0 \'c0\'ed\'ed\'e0\par "
       + (r"\'ce\'ef\'fb\'f2 \'f0\'e0\'e1\'ee\'f2\'fb\par - " + "".join(f"\\u{ord(c)}?" for c in "Описала") + " 40 BPMN\\par ").encode() * 12 + b"}")
md3, src3 = resume.import_file("resume.doc", rtf)
assert resume.blocks(md3)[0] == {"t": "h1", "x": "Петрова Анна"} and "Описала 40 BPMN" in md3, md3[:200]
print("ok")
