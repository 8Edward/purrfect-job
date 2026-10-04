"""«Вечные» и закрытые вакансии.

♾ Evergreen: a listing that hangs forever or is re-posted again and again (a talent pool, a
funnel for résumés). Two independent signals out of four are needed, never one:
  - old: first seen ≥ 90 days ago;
  - reposted: 3+ earlier copies of the same role (company + title) that have already gone;
  - mass-posted: 5+ copies of it open at once (one per city…);
  - evergreen words: «always hiring», «кадровый резерв», «постоянно ищем»…
🪦 Closed: the vacancy vanished from its site's own list while that site answered fine twice
since (a site that failed or was cut off by our cap says nothing: unreachable ≠ closed).

Ported from freehire (MIT), Copyright (c) 2026 freehire contributors: internal/job/jobreality
(classify.go thresholds and convergence, dictionary.go phrases) and internal/job/liveness
(closed-posting phrases; uncertain ≠ expired). Our part: the ledger of what each site listed.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import extras
import tg_collect as tc

PATH = tc.ROOT / "data" / "tg-job-history.json"
OLD_DAYS = 90
REPOST_MIN = 3
MASS_MIN = 5
CONVERGENCE = 2
FORGET_DAYS = 200        # a link not listed for this long leaves the ledger
CLOSED_AFTER_RUNS = 2    # complete, successful site runs without the link
FEED_CAP = 3000          # board-feed.mjs MAX_JOBS_PER_BOARD: a board that hit it is not complete

EVERGREEN = re.compile("|".join(re.escape(p) for p in (
    "always hiring", "always looking for", "we are always", "we're always", "talent community", "talent pool",
    "talent network", "future opportunities", "future openings", "general application", "open application",
    "ongoing recruitment", "всегда в поиске", "постоянно ищем", "постоянно в поиске", "кадровый резерв",
    "будущие вакансии", "в резерв", "набираем в команду постоянно", "открытая вакансия на постоянной основе",
)), re.I)
CLOSED_TEXT = re.compile(
    r"job (is )?no longer available|position has been filled|this job has expired|job posting has expired|"
    r"no longer accepting applications|this (position|role|job) (is )?no longer|this job (listing |post )?is closed|"
    r"ваканси\S* (в архиве|закрыта|неактивна)|вакансия больше не актуальна|набор (закрыт|завершён|завершен)", re.I)


def now():
    return datetime.now(timezone.utc)


def load():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except ValueError:
        data = {}
    data.setdefault("urls", {})
    data.setdefault("boards", {})
    return data


def save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, PATH)


def role_key(company, title):
    title, company = extras.role_title(title), extras._norm(company)
    return f"{company}|{title}" if title and company else ""


def observe_feed(feed, names=None, data=None, at=None):
    """Remember what each site listed this time. feed = board-feed.mjs output; a board that
    failed, came from the stale cache or hit the cap is not a complete run."""
    data = data if data is not None else load()
    at = (at or now()).isoformat(timespec="seconds")
    for board, r in feed.items():
        jobs = r.get("jobs") or []
        if "error" in r or r.get("stale") or not jobs:
            continue
        runs = data["boards"].setdefault(board, [])
        if len(jobs) < FEED_CAP:
            runs.append(at)
            del runs[:-5]
        name = (names or {}).get(board, board)
        for j in jobs:
            url = extras.link_key(j.get("url") or "")
            if not url:
                continue
            e = data["urls"].get(url)
            if e:
                e["l"] = at
            else:
                data["urls"][url] = {"b": board, "k": role_key(j.get("company") or name, j.get("title")), "f": at, "l": at}
    cutoff = (now() - timedelta(days=FORGET_DAYS)).isoformat()
    data["urls"] = {u: e for u, e in data["urls"].items() if e["l"] >= cutoff}
    return data


def index(data):
    """{role key: [url entries]} for the verdicts."""
    roles = {}
    for url, e in data["urls"].items():
        if e.get("k"):
            roles.setdefault(e["k"], []).append(e)
    return roles


def verdict(item, data, roles, at=None):
    """{"kind": "evergreen" | "closed", "why": text} or None."""
    at = at or now()
    url = extras.link_key(item.get("link") or "")
    mine = data["urls"].get(url) if url else None
    if mine and item.get("source") == "web":
        runs = [r for r in data["boards"].get(mine["b"], []) if r > mine["l"]]
        if len(runs) >= CLOSED_AFTER_RUNS:
            return {"kind": "closed", "why": "пропала с сайта"}
    text = item.get("text") or ""
    if CLOSED_TEXT.search(text):
        return {"kind": "closed", "why": "в тексте: набор закрыт"}
    key = (mine or {}).get("k") or role_key(item.get("company") or item.get("site"), item.get("title"))
    copies = roles.get(key, []) if key else []
    first = min([e["f"] for e in copies] + [str(item.get("first_seen") or item.get("date") or at.isoformat())])
    try:
        age = (at - datetime.fromisoformat(first)).days
    except ValueError:
        age = 0
    open_now = [e for e in copies if not [r for r in data["boards"].get(e["b"], []) if r > e["l"]]]
    gone = len(copies) - len(open_now)
    signals = []
    if age >= OLD_DAYS:
        signals.append(f"висит {age} дн.")
    if gone >= REPOST_MIN:
        signals.append(f"{gone} перевыкладок")
    if len(open_now) >= MASS_MIN:
        signals.append(f"{len(open_now)} копий сразу")
    if EVERGREEN.search(text):
        signals.append("«всегда ищем»")
    if len(signals) >= CONVERGENCE:
        return {"kind": "evergreen", "why": ", ".join(signals)}
    return None


def apply(member, data, roles):
    """Set / clear item["reality"] in one person's inbox. -> how many are marked."""
    inbox = member.load_inbox()
    changed, marked = False, 0
    for item in inbox.values():
        v = verdict(item, data, roles)
        if v != item.get("reality"):
            if v:
                item["reality"] = v
            else:
                item.pop("reality", None)
            changed = True
        marked += bool(v)
    if changed:
        member.save_inbox(inbox)
    return marked
