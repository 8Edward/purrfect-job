"""«Вечные» и закрытые вакансии.

♾ Evergreen: a listing that hangs forever or is re-posted again and again (a talent pool, a
funnel for résumés). Two independent signals out of four are needed, never one:
  - old: first seen ≥ 90 days ago;
  - reposted: 3+ earlier copies of the same role (company + title) that have already gone;
  - mass-posted: 5+ copies of it open at once (one per city…);
  - evergreen words: «always hiring», «кадровый резерв», «постоянно ищем»…
🪦 Closed: the vacancy vanished from its site's own list while that site answered fine twice
since (a site that failed or was cut off by our cap says nothing: unreachable ≠ closed). Only an
employer's own careers list counts: an aggregator showing its newest N drops open vacancies too.

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
STALE_DAYS = 30          # a site vacancy open this long: «висит больше 30 дней» (archived for the owner)
AUTO_ARCHIVE = ("closed", "evergreen", "stale")  # these go to «Ещё → Архив» for the owner; «reposted» is only a label
REPOST_MIN = 3
MASS_MIN = 5
CONVERGENCE = 2
FORGET_DAYS = 200        # a link not listed for this long leaves the ledger
CLOSED_AFTER_RUNS = 2    # complete, successful site runs without the link
FEED_CAP = 3000          # board-feed.mjs MAX_JOBS_PER_BOARD: a board that hit it is not complete
# Aggregators list only their newest N (Himalayas 300, Jobicy 500, AgileFluent…): an older vacancy scrolls off
# the list while it is still open, so «gone from the list» proves nothing there. Only an employer's own careers
# list can say «closed». Job boards from portals.yml (ids without «co-») are aggregators too.
ROLLING = {"co-agilefluent", "co-jobgether", "co-linkedin", "co-mastodon", "co-bluesky", "co-djinni", "co-himalayas-th",
           "co-getmatch", "co-habr", "co-geekjob", "co-trudvsem", "co-remotecom", "co-finder"}
ROLLING_RATIO = 1.5      # a «co-» board that has shown 1.5× more links than it lists now is a window as well
ROLLING_MIN = 50         # (judged only from 50 links on: a small careers page can close half its jobs in a week)

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
    tmp.write_text(json.dumps({k: v for k, v in data.items() if not k.startswith("_")}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, PATH)


def role_key(company, title):
    title, company = extras.role_title(title), extras._norm(company)
    return f"{company}|{title}" if title and company else ""


def observe_feed(feed, names=None, data=None, at=None):
    """Remember what each site listed this time. feed = board-feed.mjs output; a board that
    failed, came from the stale cache or hit the cap is not a complete run."""
    data = data if data is not None else load()
    data.pop("_rolling", None)
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


def rolling(data):
    """Boards whose list is a moving window (see ROLLING): nothing on them counts as closed. Cached per ledger."""
    cache = data.get("_rolling")
    if cache is None:
        seen, listed = {}, {}
        for e in data["urls"].values():
            seen[e["b"]] = seen.get(e["b"], 0) + 1
            runs = data["boards"].get(e["b"]) or []
            if runs and e["l"] >= runs[-1]:
                listed[e["b"]] = listed.get(e["b"], 0) + 1
        cache = {b for b in seen if not b.startswith("co-") or b in ROLLING
                 or (seen[b] >= ROLLING_MIN and seen[b] > ROLLING_RATIO * max(1, listed.get(b, 0)))}
        data["_rolling"] = cache  # dropped by save()
    return cache


def gone(e, data):
    """This listing left its site's own list (two complete runs without it), on a board where that means closed."""
    if e["b"] in rolling(data):
        return False
    return len([r for r in data["boards"].get(e["b"], []) if r > e["l"]]) >= CLOSED_AFTER_RUNS


def verdict(item, data, roles, at=None):
    """{"kind": "closed" | "evergreen" | "stale" | "reposted", "why": text} or None."""
    at = at or now()
    url = extras.link_key(item.get("link") or "")
    mine = data["urls"].get(url) if url else None
    if mine and item.get("source") == "web" and gone(mine, data):
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
    left = [e for e in copies if e not in open_now and e["b"] not in rolling(data)]  # a window scrolling on is no repost
    reposts = len(left)
    came_back = len([e for e in left if e is not mine])  # earlier copies, not this vacancy itself
    signals = []
    if age >= OLD_DAYS:
        signals.append(f"висит {age} дн.")
    if reposts >= REPOST_MIN:
        signals.append(f"{reposts} перевыкладок")
    if len(open_now) >= MASS_MIN:
        signals.append(f"{len(open_now)} копий сразу")
    if EVERGREEN.search(text):
        signals.append("«всегда ищем»")
    if len(signals) >= CONVERGENCE:
        return {"kind": "evergreen", "why": ", ".join(signals)}
    if age >= STALE_DAYS and item.get("source") == "web":
        return {"kind": "stale", "why": f"висит {age} дн."}
    if came_back >= 1:
        return {"kind": "reposted", "why": f"уже снималась и выкладывалась снова ({came_back} раз)"}
    return None


def apply(member, data, roles):
    """Set / clear item["reality"] in one person's inbox; for the owner, closed / evergreen / stale ones also go to
    the archive (never a starred or responded one). -> how many are marked."""
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
        if (v and v["kind"] in AUTO_ARCHIVE and member.is_owner and not item.get("archived") and not item.get("fav")
                and not (item.get("track") or {}).get("stage")):
            item["archived"] = True
            changed = True
        marked += bool(v)
    if changed:
        member.save_inbox(inbox)
    return marked
