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

ivan@mail.ru | +7 (926) 111-22-33 | Москва | ТГ @ivan

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
print("ok")
