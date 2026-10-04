"""Quick vacancy score from word overlap (no AI): python telegram/test_scoring.py -> ok"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import scoring  # noqa: E402
import tg_collect as tc  # noqa: E402

now = datetime.now(timezone.utc)
ctx = {"skills": ["SQL", "Python", "Tableau"], "roles": tc.compile_role(["data analyst", "аналитик данных"]),
       "country": {"on": True, "key": "Thailand", "name": "Таиланд"}}
good = {"title": "Data Analyst", "company": "X", "text": "SQL, Python and Tableau dashboards. " * 10, "date": now.isoformat()}
r = scoring.score(ctx, good)
assert r["score"] == 5.0 and any("название" in w for w in r["why"]) and any("навыки" in w for w in r["why"]), r
office = dict(good, text=good["text"] + "\nOffice 🏢\n")
assert scoring.score(ctx, office)["score"] == 3.0 and "не удалёнка" in scoring.score(ctx, office)["why"]
us = dict(good, location="Remote - US")
assert scoring.score(ctx, us)["score"] == 3.5 and "не берут из вашей страны" in scoring.score(ctx, us)["why"]
old = dict(good, date=(now - timedelta(days=20)).isoformat())
assert scoring.score(ctx, old)["score"] == 4.7
other = {"title": "Sales Manager", "company": "Y", "text": "Cold calls and negotiations with clients. " * 10, "date": now.isoformat()}
assert scoring.score(ctx, other)["score"] == 1.5, scoring.score(ctx, other)
thin = {"title": "Sales Manager", "text": "short", "date": now.isoformat()}
assert scoring.score(ctx, thin)["score"] == 2.0  # a short card: no penalty for missing skills
assert scoring.score({"skills": [], "roles": [], "country": ctx["country"]}, good)["score"] is None
print("ok")
