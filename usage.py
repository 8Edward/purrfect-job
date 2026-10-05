"""📊 Who spent how much of the Claude subscription: every `claude -p` call, by person and day.

Claude.ask (resume_ai.py) writes one line here per call: who asked (the person whose app request
started it, or "system" for the bot's own background work: role labels, the filter check, the
ready-check), what for, and the tokens Claude reported. Only the owner sees it («Ещё → 👥 Пользователи»).

Who is who travels in a context variable: webapp sets it when it recognises the person, and
background tasks started from that request inherit it. The bot's own jobs wrap their calls in
`scope(op, system=True)`.
"""

import contextvars
import json
import os
from contextlib import contextmanager
from datetime import date, timedelta

import tg_collect as tc

PATH = tc.ROOT / "data" / "tg-ai-usage.json"
KEEP_DAYS = 90
SYSTEM = "system"

WHO = contextvars.ContextVar("ai_who", default=None)   # person id (int) or None = the bot itself
OP = contextvars.ContextVar("ai_op", default=None)     # what the call was for: score, improve, plan, roles…


@contextmanager
def scope(op=None, who=None, system=False):
    """Tag the Claude calls inside: `with usage.scope("score"):`; system=True = the bot's own work."""
    tokens = [OP.set(op)] if op else []
    who_token = WHO.set(None) if system else (WHO.set(who) if who is not None else None)
    try:
        yield
    finally:
        for t in tokens:
            OP.reset(t)
        if who_token is not None:
            WHO.reset(who_token)


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PATH)


def record(tokens_in=0, tokens_out=0, ok=True, who=None, op=None, day=None):
    """One Claude call. `who`/`op` default to the context's."""
    who = WHO.get() if who is None else who
    key = str(who) if who is not None else SYSTEM
    op = op or OP.get() or "other"
    day = day or date.today().isoformat()
    data = _load()
    row = data.setdefault(day, {}).setdefault(key, {"n": 0, "in": 0, "out": 0, "fail": 0, "ops": {}})
    row["n"] += 1
    row["in"] += int(tokens_in or 0)
    row["out"] += int(tokens_out or 0)
    row["fail"] += 0 if ok else 1
    row["ops"][op] = row["ops"].get(op, 0) + 1
    cut = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    for d in [d for d in data if d < cut]:
        del data[d]
    _save(data)


def used_today(who):
    """Calls today by this person (failed ones count too: they took a turn of the subscription)."""
    return (_load().get(date.today().isoformat()) or {}).get(str(who), {}).get("n", 0)


def report(days=14):
    """{who: {"days": [{day, n, in, out}…] newest first, "total": {n, in, out}, "today": {...}}} for the last `days`."""
    data, out = _load(), {}
    today = date.today()
    for i in range(days):
        day = (today - timedelta(days=i)).isoformat()
        for who, row in (data.get(day) or {}).items():
            person = out.setdefault(who, {"days": [], "total": {"n": 0, "in": 0, "out": 0},
                                          "today": {"n": 0, "in": 0, "out": 0}, "ops": {}})
            cell = {"day": day, "n": row["n"], "in": row["in"], "out": row["out"]}
            person["days"].append(cell)
            for k in ("n", "in", "out"):
                person["total"][k] += row[k]
                if i == 0:
                    person["today"][k] += row[k]
            for op, n in (row.get("ops") or {}).items():
                person["ops"][op] = person["ops"].get(op, 0) + n
    return out
