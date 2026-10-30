"""«Источник замолчал»: a daily counter per source (site, hh.ru, pool channel).

Each check records whether the source answered and how many vacancies / posts it gave.
That tells apart «не смогли проверить» (the source failed: network, captcha, a changed
page) from «правда ноль» (it answered, there was simply nothing) and from a source that
used to give vacancies and has gone quiet for days (likely broken or abandoned).
Idea from freehire's sourcestats / silence (MIT, Copyright (c) 2026 freehire contributors);
the code is ours.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import tg_collect as tc

PATH = tc.DATA_DIR / "tg-source-stats.json"
KEEP_DAYS = 14
QUIET_DAYS = 3  # this many days of zero after earlier activity = «замолчал»


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except ValueError:
        return {}


def save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def record_many(rows):
    """rows = [(source id, name, ok, count, error)]. count = what the source gave on this check
    (a site: all its vacancies; a channel: new posts). Per day: runs ok / failed, the max count
    seen on one check, the sum of counts, the last error."""
    if not rows:
        return
    data, day = load(), _today()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date().isoformat()
    for sid, name, ok, count, error in rows:
        src = data.setdefault(sid, {"name": name, "days": {}})
        src["name"] = name or src.get("name") or sid
        d = src["days"].setdefault(day, {"ok": 0, "fail": 0, "max": 0, "sum": 0})
        if ok:
            d["ok"] += 1
            d["max"] = max(d["max"], int(count or 0))
            d["sum"] += int(count or 0)
        else:
            d["fail"] += 1
            d["err"] = str(error or "")[:160]
        src["last"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        src["days"] = {k: v for k, v in src["days"].items() if k >= cutoff}
    save(data)


def record(sid, name, ok, count=0, error=""):
    record_many([(sid, name, ok, count, error)])


def status(src, today=None):
    """{"state": ok / empty / quiet / fail / unknown, "label": shown text, "today": count}."""
    today = today or _today()
    days = src.get("days") or {}
    order = sorted(days)
    if not order:
        return {"state": "unknown", "label": "ещё не проверялся", "today": 0}
    last = days[order[-1]]
    gave = last["sum"] if last["sum"] else last["max"]
    if last["ok"] == 0:  # every check of its last day failed
        return {"state": "fail", "label": "не смогли проверить: " + (last.get("err") or "ошибка"), "today": 0}
    recent = [d for d in order[-QUIET_DAYS:]]
    earlier = [d for d in order[:-QUIET_DAYS]]
    if len(recent) >= QUIET_DAYS and all(days[d]["ok"] and not days[d]["sum"] and not days[d]["max"] for d in recent) \
            and any(days[d]["sum"] or days[d]["max"] for d in earlier):
        return {"state": "quiet", "label": f"замолчал: {QUIET_DAYS} дн. ничего, раньше было", "today": 0}
    if not gave:
        return {"state": "empty", "label": "проверен, вакансий нет" if last["ok"] else "", "today": 0}
    return {"state": "ok", "label": f"{gave} за последний день проверок", "today": gave}


def report(ids=None):
    """[{id, name, state, label, today, fails}] — problems first, then by name. ids limits it to
    the sources one person uses."""
    rank = {"fail": 0, "quiet": 1, "empty": 2, "unknown": 3, "ok": 4}
    out = []
    for sid, src in load().items():
        if ids is not None and sid not in ids:
            continue
        s = status(src)
        out.append({"id": sid, "name": src.get("name") or sid, **s,
                    "fails": sum(d.get("fail", 0) for d in (src.get("days") or {}).values())})
    out.sort(key=lambda r: (rank[r["state"]], r["name"].casefold()))
    return out
