"""Offline tests for reality.py (♾ evergreen / 🪦 closed): py -3 telegram/test_reality.py -> ok"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reality  # noqa: E402

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def day(n):
    return T0 + timedelta(days=n)


def job(url, title="Data Analyst", company="Acme"):
    return {"url": url, "title": title, "company": company}


data = {"urls": {}, "boards": {}}
reality.observe_feed({"co-a": {"jobs": [job("https://a/1"), job("https://a/9", "Welder")]}}, data=data, at=day(0))
item = {"source": "web", "link": "https://a/1", "title": "Data Analyst", "company": "Acme", "date": day(0).isoformat()}
assert reality.verdict(item, data, reality.index(data), at=day(1)) is None

# vanished from a site that answered twice since -> closed; a failed / stale / capped run does not count
reality.observe_feed({"co-a": {"error": "HTTP 500"}}, data=data, at=day(2))
reality.observe_feed({"co-a": {"jobs": [job("https://a/9", "Welder")], "stale": "timeout"}}, data=data, at=day(3))
assert reality.verdict(item, data, reality.index(data), at=day(4)) is None
reality.observe_feed({"co-a": {"jobs": [job("https://a/9", "Welder")]}}, data=data, at=day(5))
assert reality.verdict(item, data, reality.index(data), at=day(5)) is None  # one run is not enough
reality.observe_feed({"co-a": {"jobs": [job("https://a/9", "Welder")]}}, data=data, at=day(6))
assert reality.verdict(item, data, reality.index(data), at=day(6))["kind"] == "closed"

# evergreen: two signals needed, one never enough
d2 = {"urls": {}, "boards": {}}
for n in range(4):  # the same role re-posted under a new link every month, old links gone
    reality.observe_feed({"co-b": {"jobs": [job(f"https://b/{n}", "QA Engineer, Berlin", "Beta")]}}, data=d2, at=day(30 * n))
cur = {"source": "web", "link": "https://b/3", "title": "QA Engineer", "company": "Beta", "date": day(90).isoformat()}
v = reality.verdict(cur, d2, reality.index(d2), at=day(91))
assert v and v["kind"] == "evergreen" and "перевыкладок" in v["why"] and "висит" in v["why"], v
young = {"urls": {}, "boards": {}}
reality.observe_feed({"co-c": {"jobs": [job(f"https://c/{n}", "Support", "Gamma") for n in range(6)]}}, data=young, at=day(0))
one = {"source": "web", "link": "https://c/0", "title": "Support", "company": "Gamma", "date": day(0).isoformat()}
assert reality.verdict(one, young, reality.index(young), at=day(1)) is None       # mass-posting alone
one["text"] = "We are always hiring great people for our talent pool"
assert reality.verdict(one, young, reality.index(young), at=day(1))["kind"] == "evergreen"  # + evergreen words
tg = {"source": "tg", "title": "Аналитик", "text": "Вакансия закрыта, спасибо всем", "date": day(0).isoformat()}
assert reality.verdict(tg, young, {}, at=day(1))["kind"] == "closed"

# a site vacancy open for a month: stale (alone, no other signal); a role that came back: reposted (a label only)
mon = {"urls": {}, "boards": {}}
reality.observe_feed({"co-d": {"jobs": [job("https://d/1", "BI Analyst", "Delta")]}}, data=mon, at=day(0))
old = {"source": "web", "link": "https://d/1", "title": "BI Analyst", "company": "Delta", "date": day(0).isoformat()}
assert reality.verdict(old, mon, reality.index(mon), at=day(10)) is None
sv = reality.verdict(old, mon, reality.index(mon), at=day(35))
assert sv and sv["kind"] == "stale" and "35" in sv["why"], sv
rp = {"urls": {}, "boards": {}}
reality.observe_feed({"co-e": {"jobs": [job("https://e/1", "Analyst", "Echo")]}}, data=rp, at=day(0))
reality.observe_feed({"co-e": {"jobs": [job("https://e/2", "Analyst", "Echo")]}}, data=rp, at=day(5))
reality.observe_feed({"co-e": {"jobs": [job("https://e/2", "Analyst", "Echo")]}}, data=rp, at=day(6))
back = {"source": "web", "link": "https://e/2", "title": "Analyst", "company": "Echo", "date": day(5).isoformat()}
rv = reality.verdict(back, rp, reality.index(rp), at=day(7))
assert rv and rv["kind"] == "reposted", rv


class Owner:
    is_owner = True

    def __init__(self, inbox):
        self.inbox = inbox

    def load_inbox(self):
        return self.inbox

    def save_inbox(self, inbox):
        self.inbox = inbox


# apply: the owner's stale ones are archived; a starred or responded one never; a guest's only marked
own = Owner({"a": dict(old), "b": dict(old, fav=True), "c": dict(old, track={"stage": "applied"})})
real_now = reality.now
reality.now = lambda: day(35)
try:
    reality.apply(own, mon, reality.index(mon))
    guest = Owner({"a": dict(old)})
    guest.is_owner = False
    reality.apply(guest, mon, reality.index(mon))
finally:
    reality.now = real_now
assert own.inbox["a"].get("archived") and own.inbox["a"]["reality"]["kind"] == "stale"
assert not own.inbox["b"].get("archived") and not own.inbox["c"].get("archived")
assert not guest.inbox["a"].get("archived") and guest.inbox["a"]["reality"]["kind"] == "stale"
print("ok")
