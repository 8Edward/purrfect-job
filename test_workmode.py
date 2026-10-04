"""Offline tests for workmode.py (fake remote): py -3 telegram/test_workmode.py -> ok"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workmode as wm  # noqa: E402

# denials, with the freehire qualifiers
assert wm.remote_contradicted("This is not a remote position.") == "not a remote position"
assert wm.remote_contradicted("100% on-site for the first 90 days, then remote") is None
assert wm.remote_contradicted("We are <b>not a REMOTE position</b>") == "not a remote position"
assert wm.remote_contradicted("Формат работы: офис, Москва")
assert wm.remote_contradicted("Работа только в офисе на Тверской")
assert wm.remote_contradicted("Удалённая работа не рассматривается")
assert wm.remote_contradicted("Возможна работа в офисе, но можно и удалённо") is None
assert wm.remote_contradicted("Работа в офисе на время испытательного срока, потом удалёнка") is None
assert wm.remote_contradicted("Не только офис: можно удалённо") is None
assert wm.remote_contradicted("Полностью удалённая работа, офис не нужен") is None

# modes
assert wm.mode_from_text("Hybrid working: 3 days in the office") == "hybrid"
assert wm.mode_from_text("We are fully remote") == "remote"
assert wm.mode_from_text("Формат: гибрид, 2 дня в офисе") == "hybrid"
assert wm.mode_from_text("Формат: удалёнка или гибрид") == "remote"
assert wm.mode_from_text("Our remote team ships fast") == ""  # a bare «remote» is not a statement
assert wm.mode_from_text("Офис на Тверской, полный день, формат: офис") == "onsite"

# «remote, but only from …»
assert wm.only_from("Data Analyst [Remote-US]", "") == ["США"]
assert wm.only_from("Data Analyst (Remote, LATAM)", "") == ["Латинской Америки"]
assert wm.only_from("Data Analyst (Middle)", "") == []
assert wm.only_from("Analyst", "Applicants must be US citizens.") == ["США"]
assert wm.only_from("Analyst", "No US citizenship required.") == []
assert wm.only_from("Analyst", "Work without visa sponsorship; must be authorized to work in the United States") == ["США"]
assert wm.only_from("Аналитик", "Удалёнка только из РФ") == ["РФ"]
assert wm.only_from("Аналитик", "Гражданство РФ обязательно") == ["РФ"]
assert wm.only_from("Аналитик", "Работаем с кандидатами из любой страны, кроме РФ") == []

# card flags
assert wm.flags({"title": "BI Analyst", "text": "Формат: гибрид, 3 дня в офисе", "location": ""}) == ["⚠️ гибрид"]
assert wm.flags({"title": "BI Analyst (Remote, US only)", "text": "", "location": "Remote"}) == ["🌍 только США"]
assert wm.flags({"title": "BI", "text": "удалёнка", "location": ""},
                orig={"remote": None, "text": "Hybrid working: 3 days in the office", "only": []}) == ["⚠️ в оригинале гибрид"]
assert wm.flags({"title": "BI", "text": "удалёнка"}, orig={"remote": True, "text": "", "only": ["US"]}) == ["🌍 только США"]
assert wm.flags({"title": "BI", "text": "удалёнка"}, orig={"text": "This is not a remote role."}) == ["⚠️ в оригинале офис"]
assert wm.flags({"title": "BI", "text": "Полностью удалённая работа"}) == []
# vacancy-bot posts: the work format on a line of its own
post = "🟩 Business Engineer\n  ▫ ASML | Semiconductor\n\nOffice 🏢\nRequired languages: 🇬🇧\n\nSkills: sql"
assert wm.format_line(post) == "onsite" and wm.flags({"title": "x", "text": post}) == ["⚠️ не удалёнка: офис"]
assert wm.format_line("Hybrid 🏙\nSkills") == "hybrid" and wm.format_line("Work format: Office") == "onsite"
assert wm.format_line("We are an office of 5 people.\nRemote 🏠") == "" and wm.format_line("Our office is great") == ""

# the owner's feed archives office / hybrid vacancies on arrival
assert wm.not_remote({"title": "x", "text": post}) and wm.not_remote({"title": "BI", "text": "Формат: гибрид, 3 дня в офисе"})
assert not wm.not_remote({"title": "BI", "text": "Полностью удалённая работа"}) and not wm.not_remote({"title": "BI (Remote, US only)", "location": "Remote"})
assert not wm.not_remote({"title": "Аналитик", "company": "Сбер", "location": "г Москва"})  # only a city: stays in the feed

# links in posts and JSON-LD
assert wm.post_links("Подробнее: https://career.habr.com/vacancies/1000.\nПиши @hr https://t.me/hr") == ["https://career.habr.com/vacancies/1000"]
page = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Data Analyst",'
        '"jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"@type":"Country","name":"USA"},'
        '"hiringOrganization":{"name":"Acme"},"description":"&lt;p&gt;SQL&lt;/p&gt;"}</script></html>')
p = wm.ld_postings(page)
assert len(p) == 1 and wm.from_ld(p[0], "u")["remote"] is True and wm.from_ld(p[0], "u")["only"] == ["USA"]
assert wm.flags({"title": "x"}, orig=wm.from_ld(p[0], "u")) == ["🌍 только США"]
print("ok")
