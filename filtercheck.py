"""🧪 Control sample of the Telegram filter: how many real vacancies does it throw away?

While reading the pool channels, posts that DO mention one of a person's roles but were turned
down by the vacancy detector (no «вакансия» word, looks like an article, a stop word, too short)
are kept aside. Once a day up to SAMPLE of them go to `claude -p` in one request: «is this a job
posting, not a résumé / an ad / an article?». The share of «yes» is the filter's loss rate,
shown in «Ещё → 📡 Радар»; the lost posts are listed so the words can be tuned.
Idea from JobMonitor (github.com/spichkinevgeniy/JobMonitor, MIT); the code is ours.
"""

import json
import os
import random
import re
from datetime import datetime, timedelta, timezone

import tg_collect as tc

PATH = tc.ROOT / "data" / "tg-filter-check.json"
PENDING_MAX = 300
SAMPLE = 15
KEEP_DAYS = 30
LOST_KEEP = 30
TIMEOUT = 240

SYSTEM = ("You check posts from Telegram job channels. For each numbered post answer whether it is a job "
          "posting: an employer or recruiter offering a specific job, gig or project. NOT a job posting: a "
          "candidate's résumé or «ищу работу», an advertisement of courses or services, an article, news, a "
          "digest or list of other channels, a career-advice post. Answer ONLY with JSON like "
          '{"1": true, "2": false}, one key per post, nothing else.')


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def load():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except ValueError:
        data = {}
    for k, v in (("pending", []), ("days", {}), ("lost", [])):
        data.setdefault(k, v)
    return data


def save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def add(candidates):
    """candidates = [{text, chat, link, date, why}] turned down by the filter but naming a role."""
    if not candidates:
        return
    data = load()
    seen = {c["fp"] for c in data["pending"]} | set(data.get("checked_fp", []))
    for c in candidates:
        fp = tc.fingerprint(c["text"])
        if fp in seen:
            continue
        seen.add(fp)
        data["pending"].append({"fp": fp, "text": c["text"][:1500], "chat": c.get("chat", ""), "link": c.get("link", ""),
                                "date": str(c.get("date") or ""), "why": c.get("why", "")})
    data["pending"] = data["pending"][-PENDING_MAX:]
    save(data)


def prompt_for(posts):
    return "\n\n".join(f"### {n}\n{re.sub(r'\s+', ' ', p['text'])[:1200]}" for n, p in enumerate(posts, 1))


def parse(text, count):
    """{"1": true, ...} -> {index: bool}; tolerant of text around the JSON."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return {}
    return {int(k) - 1: bool(v) for k, v in raw.items() if str(k).isdigit() and 1 <= int(k) <= count and isinstance(v, bool)}


def due(data=None):
    data = data or load()
    return data.get("last_run") != _today() and len(data["pending"]) >= 3


async def run(ai, sample=SAMPLE):
    """Once a day: ask Claude about a random sample of the turned-down posts. -> (checked, lost)."""
    data = load()
    if not due(data):
        return 0, 0
    fresh_after = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    pool = [p for p in data["pending"] if p["date"] >= fresh_after] or data["pending"]
    posts = random.sample(pool, min(sample, len(pool)))
    data["last_run"] = _today()  # one try a day, even if Claude fails: the subscription is shared
    save(data)
    got = parse(await ai.ask(SYSTEM, prompt_for(posts), timeout=TIMEOUT), len(posts))
    data = load()  # re-read: the reader may have added posts meanwhile
    done = {posts[i]["fp"] for i in got}
    data["pending"] = [p for p in data["pending"] if p["fp"] not in done]
    data["checked_fp"] = (data.get("checked_fp", []) + sorted(done))[-2000:]
    lost = [posts[i] for i, yes in got.items() if yes]
    d = data["days"].setdefault(_today(), {"checked": 0, "lost": 0, "why": {}})
    d["checked"] += len(got)
    d["lost"] += len(lost)
    for p in lost:
        d["why"][p["why"]] = d["why"].get(p["why"], 0) + 1
    data["lost"] = ([{"title": tc.guess_title(p["text"]), "chat": p["chat"], "link": p["link"], "why": p["why"],
                      "at": _today()} for p in lost] + data["lost"])[:LOST_KEEP]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date().isoformat()
    data["days"] = {k: v for k, v in data["days"].items() if k >= cutoff}
    save(data)
    return len(got), len(lost)


def report(days=7):
    data = load()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    recent = [v for k, v in data["days"].items() if k >= cutoff]
    why = {}
    for v in recent:
        for k, n in (v.get("why") or {}).items():
            why[k] = why.get(k, 0) + n
    return {"days": days, "checked": sum(v["checked"] for v in recent), "lost": sum(v["lost"] for v in recent),
            "why": why, "waiting": len(data["pending"]), "items": data["lost"][:10], "last_run": data.get("last_run")}
