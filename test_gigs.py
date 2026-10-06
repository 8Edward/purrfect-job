"""Offline checks for gigs.py: py -3 telegram/test_gigs.py -> ok"""
from datetime import datetime, timedelta, timezone

import gigs

now = datetime.now(timezone.utc)
a = gigs.assess("Нужно собрать таблицу в Excel", "Разовая задача: свести данные из 3 файлов в Google Sheets, без опыта можно. Удалённо.", date=now)
assert a and a["level"] == "easy" and "Excel" in a["skills"], a
assert gigs.assess("Аналитик данных", "Высшее образование, SQL, полный день") is None  # «образование» is not «разовая»
assert gigs.assess("Подработка", "Пассивный доход без вложений, пиши + в лс") is None
assert gigs.assess("Фриланс", "Доход от 3000 руб в день, пошаговая инструкция") is None
assert gigs.assess("Senior Data Engineer (part-time)", "5+ years of experience, Spark, Airflow") is None
b = gigs.assess("Data entry", "Copy numbers from PDFs into Excel", market=True, date=now - timedelta(days=5))
assert b and b["why"][0] == "задача с биржи", b
assert gigs.assess("Аналитик данных", "", by_source="частичная / проектная")
assert gigs.assess("Разработка платформы под ключ", "Бюджет: 600 000 ₽, фриланс") is None
assert gigs.budget_value("Бюджет: 600 000 ₽") > 3000

post = {"key": "1_1", "title": "Нужен исполнитель", "text": "Ищу исполнителя: парсинг сайта в Excel, разовая задача, удалённо",
        "date": now, "source": "tg", "chat": "frilans"}
twin = dict(post, key="1_2")
old = dict(post, key="1_3", title="Другое", date=now - timedelta(days=30))
inbox = {"2_1": {"title": "Part-time analyst", "text": "part-time, 20 hours/week, SQL", "date": now.isoformat(), "status": "hidden"}}
rows = gigs.collect([post, twin, old], inbox, [], [])
assert [j["key"] for j, _ in rows] == ["1_1"], rows
print("ok")
