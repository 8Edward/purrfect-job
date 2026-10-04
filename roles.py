"""🧭 Which role a vacancy is, whatever words its title uses.

Two layers (2026-10-04):
1. The dictionary: rolegraph.yml, each role's `names` and `also` (titles picked from the O*NET
   job titles database plus Russian names). Checked by a person, instant, free. Search matches it
   in webapp.py.
2. Claude: titles the dictionary doesn't know go to `claude -p` on the owner's subscription in
   small batches in the background. Answers live here, in data/tg-roles-ai.json, apart from the
   dictionary, so a mistake never spreads:
   - Claude only ever sees the dictionary as its reference, never its own earlier answers;
   - only answers it is sure about («high») are used, and they rank after the dictionary,
     marked «🤖» on the card;
   - the owner reviews them in the app: ✓ = checked (ranks like the dictionary), ✗ = never used;
   - «Сбросить» forgets everything Claude said (checked ✓ answers stay).
"""

import json
import re
import time
from datetime import date

import guests
import tg_collect as tc

CACHE_PATH = tc.ROOT / "data" / "tg-roles-ai.json"
BATCH = 50          # titles per Claude call
PER_RUN = 2         # calls per background run (each ~20 s, the résumé AI waits meanwhile)
DAILY = 40          # calls per day (owner's subscription, shared with everything else)
MAX_ENTRIES = 30000
TIMEOUT = 180

_cache = {"mtime": None, "data": None}


def norm(text):
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


def key_of(job):
    """Site vacancies are labelled by title (the same title means the same role); a Telegram post
    by the post itself, its «title» is just the first line."""
    if job.get("source") == "tg":
        return "p:" + str(job.get("key"))
    return "t:" + norm(job.get("title"))[:120]


def load():
    try:
        mtime = CACHE_PATH.stat().st_mtime
    except OSError:
        return {"labels": {}, "meta": {}}
    if _cache["mtime"] != mtime:
        data = guests.read_json(CACHE_PATH, {})
        _cache.update(mtime=mtime, data={"labels": data.get("labels") or {}, "meta": data.get("meta") or {}})
    return _cache["data"]


def save(data):
    labels = data["labels"]
    if len(labels) > MAX_ENTRIES:  # forget the oldest unchecked ones
        old = sorted((k for k, v in labels.items() if v.get("ok") is None), key=lambda k: labels[k].get("at", ""))
        for k in old[:len(labels) - MAX_ENTRIES]:
            del labels[k]
    guests.write_json(CACHE_PATH, data)
    _cache["mtime"] = None


def label_of(job, data=None):
    """-> (role ids, checked) usable for search: sure answers not rejected by the owner, else ([], False)."""
    v = (data or load())["labels"].get(key_of(job))
    if not v or v.get("ok") is False:
        return [], False
    if v.get("ok") is True:
        return v.get("roles") or [], True
    return (v.get("roles") or [], False) if v.get("sure") else ([], False)


def calls_left(data=None):
    meta = (data or load())["meta"]
    return DAILY - (meta.get("n", 0) if meta.get("day") == date.today().isoformat() else 0)


def unknown(jobs, known):
    """Vacancies the dictionary can't place (`known(title)` False) and Claude hasn't seen, newest first."""
    labels = load()["labels"]
    out, seen = [], set()
    for job in sorted(jobs, key=lambda j: j["date"].timestamp() if hasattr(j["date"], "timestamp") else 0, reverse=True):
        k = key_of(job)
        if k in seen or k in labels or not norm(job.get("title")):
            continue
        seen.add(k)
        if not known(job.get("title") or ""):
            out.append(job)
    return out


SYSTEM = """You label job vacancies with roles from a fixed list. Judge by the work the person will do,
not by exact words: "Payments Risk Specialist" and "Trust & Safety Analyst" are fraud work,
"Аналитик DWH" is a data warehouse analyst. A vacancy may have 0, 1 or 2 roles. Use [] when no role
fits or the text is not a job vacancy (a digest, an article, a résumé). Answer with JSON only."""


def prompt_for(jobs, graph):
    roles = "\n".join(f"- {rid}: {', '.join(r['names'][:5])}" for rid, r in graph.items())
    lines = []
    for i, job in enumerate(jobs, 1):
        line = f"{i}. {re.sub(r'\s+', ' ', job.get('title') or '')[:120]}"
        if job.get("source") == "tg":  # a post's first line may not be the title: give a bit more
            line += " | " + re.sub(r"\s+", " ", job.get("text") or "")[:220]
        lines.append(line)
    return (f"Roles:\n{roles}\n\nVacancies:\n" + "\n".join(lines) +
            '\n\nReturn {"1": {"roles": ["role-id"], "sure": true}, "2": {"roles": [], "sure": true}, ...} '
            'for every number. "sure": false when you are guessing.')


def parse(text, jobs, graph):
    """Claude's answer -> {key: entry}; unknown role ids and missing numbers are dropped."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("bad-answer")
    raw = json.loads(text[start:end + 1])
    out, now = {}, time.strftime("%Y-%m-%d %H:%M")
    for i, job in enumerate(jobs, 1):
        v = raw.get(str(i))
        if isinstance(v, list):
            v = {"roles": v, "sure": False}
        if not isinstance(v, dict) or not isinstance(v.get("roles"), list):
            continue
        roles = [r for r in v["roles"] if isinstance(r, str) and r in graph][:2]
        out[key_of(job)] = {"title": (job.get("title") or "")[:120], "roles": roles, "sure": bool(v.get("sure")),
                            "at": now}
    return out


async def classify(ai, jobs, graph):
    """One Claude call for up to BATCH vacancies. -> how many got a role."""
    data = load()
    if calls_left(data) <= 0 or not jobs:
        return 0
    text = await ai.ask(SYSTEM, prompt_for(jobs[:BATCH], graph), timeout=TIMEOUT)
    got = parse(text, jobs[:BATCH], graph)
    data = load()  # re-read: the owner may have reviewed something meanwhile
    today = date.today().isoformat()
    meta = data["meta"]
    meta.update(day=today, n=(meta.get("n", 0) if meta.get("day") == today else 0) + 1)
    for k, v in got.items():
        if k not in data["labels"]:
            data["labels"][k] = v
    save(data)
    return sum(1 for v in got.values() if v["roles"])


def review_list(limit=60):
    """Unchecked answers with a role, newest first: sure ones (already used) and guesses
    (used only after ✓)."""
    labels = load()["labels"]
    items = [dict(v, key=k) for k, v in labels.items() if v.get("roles") and v.get("ok") is None]
    items.sort(key=lambda v: (v.get("at", ""), bool(v.get("sure"))), reverse=True)
    return items[:limit], len(items)


def review(key, ok):
    data = load()
    if key not in data["labels"]:
        return False
    data["labels"][key]["ok"] = bool(ok)
    save(data)
    return True


def reset():
    """Forget Claude's answers except those the owner checked (✓)."""
    data = load()
    data["labels"] = {k: v for k, v in data["labels"].items() if v.get("ok") is True}
    save(data)
    return len(data["labels"])


def stats():
    labels = load()["labels"].values()
    return {"total": len(labels),
            "used": sum(1 for v in labels if v.get("roles") and (v.get("ok") is True or (v.get("sure") and v.get("ok") is None))),
            "checked": sum(1 for v in labels if v.get("ok") is True),
            "rejected": sum(1 for v in labels if v.get("ok") is False),
            "calls_left": calls_left()}
