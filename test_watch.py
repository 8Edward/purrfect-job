"""Offline tests for the ⭐ watched companies. `py -3 telegram/test_watch.py` -> ok"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import watch  # noqa: E402

assert watch.company_key("Nitka Technologies, Inc.") == watch.company_key("NITKA TECHNOLOGIES") == "nitka technologies"
assert watch.company_key("Альфа-Банк. Центральный офис") == watch.company_key("Альфа-Банк")
assert watch.company_key("ООО Ромашка") == "ромашка" and watch.company_key("") == ""


class M:
    is_owner = True
    id = 1

    def __init__(self, inbox):
        self.inbox, self.state = inbox, {}

    def load_state(self):
        return dict(self.state)

    def update_state(self, **f):
        self.state.update(f)

    def load_inbox(self):
        return self.inbox

    def cfg(self):
        return {}


def job(company, title, **kw):
    return {"company": company, "title": title, "status": "new", "date": datetime.now(timezone.utc).isoformat(), **kw}


m = M({"a": job("Acme Inc.", "Data Analyst", fav=True), "b": job("Acme", "BI Developer"), "c": job("Other", "Data Analyst")})
companies, items = watch.overview(m)
assert [c["key"] for c in companies] == ["acme"] and items == []          # the baseline: what is already there is not «new»
m.inbox["d"] = job("ACME LLC", "Product Analyst")                          # a new vacancy of a watched company
m.inbox["e"] = job("Acme", "Data Analyst")                                 # another city of the favourite itself: not an alarm
m.inbox["f"] = job("Other", "SQL Dev")
assert [k for k, _ in watch.overview(m)[1]] == ["d"]
assert [k for k, _ in watch.to_announce(m)] == ["d"]
watch.mark_announced(m, ["d"])
assert watch.to_announce(m) == [] and [k for k, _ in watch.overview(m)[1]] == ["d"]   # told once, but still shown in the app
watch.add(m, "Other Ltd")                                                  # by hand: baseline again, then only newer ones
assert [k for k, _ in watch.overview(m)[1]] == ["d"]
m.inbox["g"] = job("Other", "Data Engineer")
assert [k for k, _ in watch.overview(m)[1]] == ["g", "d"] or [k for k, _ in watch.overview(m)[1]] == ["d", "g"]
watch.remove(m, "acme")                                                    # the favourite stays, the company is not watched
assert [c["key"] for c in watch.overview(m)[0]] == ["other"] and m.inbox["a"]["fav"]
m.inbox["a"]["archived"] = True
watch.add(m, "Acme")                                                       # re-added by hand: a clean baseline
assert watch.overview(m)[1] and all(k != "d" for k, _ in watch.overview(m)[1])
try:
    watch.add(m, "  ")
    raise SystemExit("empty name accepted")
except ValueError:
    pass
print("ok")
