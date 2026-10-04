"""Letters from templates (no AI): python telegram/test_letters.py -> ok"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import letters  # noqa: E402

MD = """# Иван Петров

Data Analyst

## О себе

Аналитик данных с опытом в SQL и построении дашбордов. Автоматизировал отчётность для отдела продаж.

## Навыки

SQL, Python, Tableau, Excel, ClickHouse

## Опыт

- Строил отчёты в Tableau
"""
info = letters.parse_resume(MD)
assert info["name"] == "Иван Петров" and info["summary"].startswith("Аналитик данных") and info["skills"] == ["SQL", "Python", "Tableau", "Excel", "ClickHouse"]
item = {"title": "BI Analyst", "company": "Acme"}
assert letters.matched_skills(info["skills"], "We need SQL and Tableau; pythonic skills") == ["SQL", "Tableau"]  # whole words only

ru = letters.compose(MD, item, "Нужны SQL и Tableau", "ru")
assert ru.startswith("Здравствуйте (Acme)!") and "«BI Analyst» в Acme" in ru and "SQL, Tableau" in ru and ru.rstrip().endswith("Иван Петров")
assert "Аналитик данных с опытом в SQL" in ru and "ClickHouse" not in ru  # only what the vacancy names
en = letters.compose(MD, item, "SQL, Tableau", "en", tone="bold")
assert en.startswith("Hi, Acme team,") and "15-minute call" in en and "Talk soon," in en
none = letters.compose(MD, item, "ничего общего", "ru", tone="short")  # no match: the main tools instead
assert "Инструменты: SQL, Python, Tableau, Excel, ClickHouse." in none
mine = letters.compose(MD, item, "", "ru", about="Мой собственный абзац о себе.")
assert "Мой собственный абзац о себе." in mine and "Аналитик данных с опытом" not in mine
empty = letters.compose("# Имя\n", {"title": "X"}, "", "ru")  # a bare résumé: no colon left hanging
assert "Коротко о себе." in empty and not any(line.endswith(":") for line in empty.splitlines())
assert [p["id"] for p in letters.resume_presets(MD)] == ["resume"] and letters.resume_presets("# Имя\n") == []
assert {t["id"] for t in letters.tone_list("en")} == set(letters.TONES) and letters.compose(MD, item, "", "de")  # unknown language falls back
long = letters._fit("Слово. " * 200)
assert len(long) <= letters.ABOUT_MAX and long.endswith(".")
# a résumé like a hh.ru export: contacts line, then the summary paragraph, no «О себе» section; languages are not skills
HH = """# Иван Петров

ivan@mail.ru | +7 (926) 111-22-33 | Москва | ТГ @ivan_p

Аналитик данных Big Data, экспертный уровень SQL и Python для ETL и сложной аналитики. Имею опыт проектирования витрин данных.

## ОПЫТ РАБОТЫ

### Ведущий аналитик

- Делал отчёты

## НАВЫКИ

- Языки: Русский (родной), Английский (B2)
- Базы данных: PostgreSQL, Greenplum, ClickHouse
"""
info = letters.parse_resume(HH)
assert info["summary"].startswith("Аналитик данных Big Data") and "ivan@mail.ru" not in info["summary"]
assert info["skills"] == ["PostgreSQL", "Greenplum", "ClickHouse"], info["skills"]
# profile: only known fields, ticked ones in the letter, résumé contacts found by rules
pr = letters.clean_profile({"values": {"fio": "Eduard Dunaev", "salary": "от 3000 USD", "bogus": "x", "email": ""}, "custom": [{"label": "Visa", "value": "нет"}, {"label": "", "value": "z"}],
                            "use": ["fio", "salary", "nope"]})
assert pr["values"] == {"fio": "Eduard Dunaev", "salary": "от 3000 USD"} and pr["use"] == ["fio", "salary"] and pr["custom"] == [{"label": "Visa", "value": "нет", "use": True}]
text = letters.compose(MD, item, "", "ru", profile=pr)
assert text.rstrip().endswith("Visa: нет") and "Ожидаемая зарплата: от 3000 USD" in text and "Eduard Dunaev" in text and "Иван Петров" not in text
assert "Expected salary: от 3000 USD" in letters.compose(MD, item, "", "en", profile=pr)
mine = letters.clean_profile({"values": {"about": "Свой абзац про меня.", "phone": "+1 555"}, "use": ["about"]})
t2 = letters.compose(MD, item, "", "ru", profile=mine)
assert "Свой абзац про меня." in t2 and "+1 555" not in t2 and "Аналитик данных с опытом" not in t2
found = letters.from_resume(HH)
assert found["fio"] == "Иван Петров" and found["email"] == "ivan@mail.ru" and found["telegram"] == "@ivan_p" and found["phone"] == "+7 (926) 111-22-33" and found["location"] == "Москва", found
assert letters.manual_reasons("Выполните тестовое задание и ответьте на вопросы") == ["тестовое задание", "вопросы или анкета"]
assert letters.manual_reasons("Ищем аналитика, SQL") == [] and letters.manual_reasons("Please answer the following questions") == ["questions / test"]
print("ok")
