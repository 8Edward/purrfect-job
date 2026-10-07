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
assert gigs.pay_usd("₹12500 - ₹37500 INR", market=True)["label"] == "$150–450 за проект"
assert gigs.pay_usd("$15 - $25 USD / hour", market=True)["per"] == "/час"
assert gigs.pay_usd("60 000 руб/мес", cyrillic=True)["label"] == "$670/мес"
assert gigs.pay_usd("25К", cyrillic=True)["hi"] == 278
assert gigs.pay_usd("без оплаты") is None

import freelancer
proj = {"id": 1, "title": "PDF Contact Lists to Excel", "seo_url": "ocr/PDF-Contact-Lists-Excel", "type": "fixed",
        "currency": {"code": "INR"}, "budget": {"minimum": 12500, "maximum": 37500},
        "bid_stats": {"bid_count": 27, "bid_avg": 20000}, "time_submitted": int(now.timestamp()) - 3600,
        "description": "Extract contacts from 20 PDF files into one Excel sheet. Sample attached.", "jobs": [{"name": "Excel"}]}
fj = freelancer.to_job(proj)
assert fj["url"].endswith("ocr/PDF-Contact-Lists-Excel") and fj["comp"] == "₹12500 - ₹37500 INR" and not freelancer.junk(fj)
br = freelancer.brief(fj, "## Summary\nData Analyst with 3 years of experience. Excel, Python.")
assert br["bid"] == 14000 and br["bids"] == 27 and br["days"] == 2 and 0.9 < br["age_h"] < 1.1, br
assert freelancer.junk(dict(fj, title="IT Project Manager - Networks"))      # another job
assert freelancer.junk(dict(fj, text="Must be done purely manual, no OCR"))     # automation forbidden
assert freelancer.junk(dict(fj, fl=dict(fj["fl"], cur="USD", min=10, max=30)))  # under $50
t = freelancer.template(fj, "## Summary\nData Analyst with 3 years of experience in Big Data analytics.")
assert "I'm a Data Analyst with 3 years" in t and "@" not in t and "http" not in t
print("ok")
